"""Dauerhafte Sitzung (Device mit keep_alive): eine Verbindung für mehrere Scans, Neuaufbau nach Abriss."""

import time
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

import pytest

from mock_scanner import MockScanner
from scansnap.adapters.ix1500 import device
from scansnap.adapters.ix1500.button import Notification
from scansnap.adapters.ix1500.control import Control, HostIdentity
from scansnap.adapters.ix1500.scsi import ScsiChannel
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
    """Ersetzt den echten Verbindungsablauf: jede Sitzung bekommt einen eigenen Mock-Scanner."""
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
    assert dev.connected  # nach dem Scan läuft das C2-Polling weiter
    time.sleep(0.3)  # Polling-Takt nach dem Scan: 250 ms
    mocks[0].refill()  # nächstes Blatt in den Einzug
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
    channel(opened[0]).sock.shutdown(2)  # Scanner weg: Polling bricht ab
    time.sleep(0.05)
    assert not dev.connected
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
        return real_stop(self, timeout) and len(calls) > 1  # erste Sitzung „hängt“

    monkeypatch.setattr(ScsiChannel, "stop_polling", flaky_stop)
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 2 and opened[0].ch is None  # alte Sitzung geschlossen


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
    dev.connect()  # Sitzung lebt: kein zweiter Listener
    assert len(listeners) == 1
    dev.disconnect()
    assert listeners[0].closed
    dev.connect()  # nach dem Trennen startet ein neuer Listener
    assert len(listeners) == 2
    dev.disconnect()
