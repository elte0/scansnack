"""Persistent session (Device with keep_alive): one connection for several scans, reconnect after a drop."""

import time
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest

from mock_scanner import MockScanner
from scansnap.adapters.ix1500 import device, wire
from scansnap.adapters.ix1500.button import Notification
from scansnap.adapters.ix1500.control import Control, HostIdentity
from scansnap.adapters.ix1500.scsi import ScsiChannel
from scansnap.core.errors import FeedError
from scansnap.core.fixes import IX1500_HOLD_PAPER_FAULTS
from scansnap.core.model import Fault, FaultDecision
from scansnap.core.model import ScanSettings as Settings

IDENT = HostIdentity("test", "00" * 8, "AB" * 16, bytes.fromhex("020000000001"))
FIXTURE = "02_grayscale_simplex_normalquality"


class FakeControl(Control):
    def __init__(self) -> None:
        super().__init__("127.0.0.1", IDENT)
        self.calls: list[str] = []

    def get_users(self) -> dict[str, Any]:
        self.calls.append("get_users")
        return {"profiles": [{"user_id": IDENT.user_id, "prof_id": "P1"}]}

    def local_ip(self) -> str:
        return "127.0.0.1"

    def pick_profile(self, host_ip: str) -> dict[str, Any]:
        self.calls.append("get_users")
        return {"prof_id": "P1"}

    def select_profile(self, prof_id: str) -> None:
        self.calls.append(f"select {prof_id}")


Sessions = tuple[list[MockScanner], list[device.Session]]


@pytest.fixture
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[Sessions]:
    """Replaces the real connection sequence: every session gets a mock scanner of its own."""
    mocks: list[MockScanner] = []
    opened: list[device.Session] = []

    class FakeSession(device.Session):
        def open(self) -> ScsiChannel:
            mock = MockScanner(FIXTURE)
            mocks.append(mock)
            self.ch = ScsiChannel("127.0.0.1", IDENT.mac, port=mock.port)
            self.ch.start_polling(0.01)
            opened.append(self)
            return self.ch

    monkeypatch.setattr(device, "Session", FakeSession)
    yield mocks, opened
    for m in mocks:
        m.close()


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Waits for a state of the polling threads instead of fixed pauses (slow CI runners)."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def make_device() -> tuple[device.Device, FakeControl]:
    dev = device.Device("127.0.0.1", IDENT, rotate=False, crop=False, keep_alive=True)
    ctl = dev.ctl = FakeControl()
    return dev, ctl


def channel(s: device.Session) -> ScsiChannel:
    assert s.ch is not None
    return s.ch


def test_keep_alive_reuses_session_and_polls_between_scans(fake_sessions: Sessions) -> None:
    mocks, opened = fake_sessions
    dev, ctl = make_device()
    dev.connect()
    assert len(list(dev.scan(Settings()))) == 1
    assert dev.connected  # C2 polling continues after the scan

    # polling interval after the scan: 250 ms; wait until a C2 arrived after the end of the scan (D6)
    def polled_after_scan() -> bool:
        ops = [cdb[0] for cdb, _ in mocks[0].received]
        return 0xC2 in ops[ops.index(0xD6) :]

    assert wait_until(polled_after_scan)
    mocks[0].refill()  # next sheet into the feeder
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 1
    ops = [cdb[0] for cdb, _ in mocks[0].received]
    first_end = ops.index(0xD6)
    assert 0xC2 in ops[first_end : ops.index(0xD5, first_end)]
    assert ctl.calls.count("select P1") == 2
    dev.disconnect()
    assert not dev.connected


def test_dead_connection_is_rebuilt_before_scan(fake_sessions: Sessions) -> None:
    mocks, opened = fake_sessions
    dev, _ = make_device()
    dev.connect()
    mocks[0].close()
    channel(opened[0]).sock.shutdown(2)  # scanner gone: polling stops
    assert wait_until(lambda: not dev.connected)
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 2


def test_connection_lost_during_scan_start_retries_once(
    fake_sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, opened = fake_sessions
    dev, _ = make_device()
    dev.connect()
    real_stop = ScsiChannel.stop_polling
    calls: list[ScsiChannel] = []

    def flaky_stop(self: ScsiChannel, timeout: float = 3.0) -> bool:
        calls.append(self)
        return real_stop(self, timeout) and len(calls) > 1  # first session "hangs"

    monkeypatch.setattr(ScsiChannel, "stop_polling", flaky_stop)
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 2 and opened[0].ch is None  # old session closed


class FakeListener:
    created: ClassVar[list["FakeListener"]] = []

    def __init__(self, ip: str, port: int, callback: Callable[[Notification], None]):
        self.args = (ip, port, callback)
        self.closed = False
        self.created.append(self)

    def close(self) -> None:
        self.closed = True


def test_button_listener_follows_session(fake_sessions: Sessions, monkeypatch: pytest.MonkeyPatch) -> None:
    listeners: list[FakeListener] = []
    monkeypatch.setattr(FakeListener, "created", listeners)
    monkeypatch.setattr(device, "ButtonListener", FakeListener)
    presses: list[Notification] = []
    dev, _ = make_device()
    dev.on_button = presses.append
    dev.connect()
    assert [ls.args for ls in listeners] == [("127.0.0.1", IDENT.host_port, presses.append)]
    dev.connect()  # session alive: no second listener
    assert len(listeners) == 1
    dev.disconnect()
    assert listeners[0].closed
    dev.connect()  # after disconnecting, a new listener starts
    assert len(listeners) == 2
    dev.disconnect()


def test_held_batch_uses_the_fault_handler_and_is_not_retried(fake_sessions: Sessions) -> None:
    mocks, opened = fake_sessions
    dev, _ = make_device()
    dev.connect()
    mocks[0].double_feed_at, mocks[0].panel_choice = 0, None
    dev.fixes.set(IX1500_HOLD_PAPER_FAULTS, True)
    asked: list[tuple[Fault, str]] = []

    def decide(fault: Fault) -> FaultDecision | None:
        asked.append((fault, dev.stage))
        channel(opened[0]).sock.shutdown(2)  # the connection drops while the batch waits
        return None

    dev.set_fault_handler(decide)
    with pytest.raises((OSError, wire.ProtocolError)):
        list(dev.scan(Settings()))
    assert asked == [(Fault("double feed", 1), "waiting for the user (double feed on sheet 1)")]
    assert len(opened) == 1  # no second session: it would feed the next sheet without an answer


def test_a_held_batch_does_not_stop_the_next_scan_from_retrying(
    fake_sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    mocks, _ = fake_sessions
    dev, _ = make_device()
    dev.connect()
    mocks[0].double_feed_at, mocks[0].panel_choice = 0, "finish"
    dev.fixes.set(IX1500_HOLD_PAPER_FAULTS, True)
    with pytest.raises(FeedError):
        list(dev.scan(Settings()))
    real_live = dev._live_session  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    def flaky_live() -> device.Session:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("connection reset")  # fails before the scan starts
        return real_live()

    monkeypatch.setattr(dev, "_live_session", flaky_live)
    assert len(list(dev.scan(Settings()))) == 1  # retried
    assert len(calls) == 2
