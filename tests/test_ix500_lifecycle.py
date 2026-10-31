"""iX500 device lifecycle: notifications, poller, disconnect, model detection, error reporting."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Self

import pytest

from scansnap.adapters.ix500 import device as device_mod
from scansnap.adapters.ix500 import notify, wire
from scansnap.adapters.ix500.control import HostIdentity
from scansnap.adapters.ix500.device import Device
from scansnap.adapters.ix500.scsi import Response, ScsiChannel
from scansnap.adapters.scansnap_wifi import notify as shared_notify
from scansnap.app import locator as locator_mod
from scansnap.core.errors import FeedError
from scansnap.core.model import ScanSettings, Sheet

IDENT = HostIdentity(bytes(8), bytes.fromhex("020000000001"))


def _no_reply(*_args: object, **_kwargs: object) -> list[object]:
    return []


def _zero_mac(_ip: str) -> bytes:
    return bytes(6)


def _notice(event: int, counter: int) -> bytes:
    d = bytearray(48)
    struct.pack_into(">I4sI", d, 0, 48, b"VENS", event)
    d[16] = counter
    return bytes(d)


def test_power_on_counter_restarts_and_is_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_notify, "REPEAT_WINDOW", 0.3)
    got: list[notify.Notification] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    listener = notify.Listener("127.0.0.1", port, got.append)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _ in range(3):  # one notice, sent three times
                s.sendto(_notice(notify.POWER_ON, 1), ("127.0.0.1", port))
            time.sleep(0.5)  # next power cycle: the counter is 1 again
            s.sendto(_notice(notify.POWER_ON, 1), ("127.0.0.1", port))
        deadline = time.monotonic() + 2
        while len(got) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        listener.close()
    assert [n.event for n in got] == [notify.POWER_ON, notify.POWER_ON]


def test_notifications_after_disconnect_do_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = Device("127.0.0.1", tmp_path / "id.json")

    def must_not_register() -> None:
        raise AssertionError("registered after disconnect")

    monkeypatch.setattr(d, "_register", must_not_register)
    d.disconnect()
    d._notified(notify.Notification(notify.POWER_ON, 1, "127.0.0.1", ""))  # pyright: ignore[reportPrivateUsage]
    assert not d.connected


def test_failed_registration_after_power_on_is_not_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    d = Device("127.0.0.1", tmp_path / "id.json")

    def unreachable() -> None:
        raise ConnectionError("scanner still booting")

    monkeypatch.setattr(d, "_register", unreachable)
    d._notified(notify.Notification(notify.POWER_ON, 1, "127.0.0.1", ""))  # pyright: ignore[reportPrivateUsage]
    assert not d.connected  # the next connect() registers again


def test_lost_scanner_ends_the_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Gone:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise ConnectionRefusedError("scanner gone")

    monkeypatch.setattr(device_mod, "ScsiChannel", Gone)
    monkeypatch.setattr(device_mod, "POLL_RETRY", 0.01)
    d = Device("127.0.0.1", tmp_path / "id.json")
    d.ident = IDENT
    d._registered = True  # pyright: ignore[reportPrivateUsage]
    stop = threading.Event()
    poller = threading.Thread(target=d._poll, args=(stop,), daemon=True)  # pyright: ignore[reportPrivateUsage]
    poller.start()
    deadline = time.monotonic() + 2
    while d.connected and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    poller.join(2)
    assert not d.connected and d.paper_loaded() is None


def test_short_status_is_a_protocol_error() -> None:
    class Short(ScsiChannel):
        def __init__(self) -> None:
            pass

        def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
            return Response(0, b"\0\0")

    with pytest.raises(wire.ProtocolError):
        Short().hardware_status()


def test_release_failure_keeps_the_scan_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Channel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

    class Jams:
        def __init__(self, _ch: object) -> None:
            pass

        def scan(self, *_args: object) -> Iterator[Sheet]:
            raise FeedError("paper jam")
            yield  # pragma: no cover

    def release_fails(*_args: object) -> None:
        raise ConnectionResetError("release failed")

    monkeypatch.setattr(device_mod, "ScsiChannel", Channel)
    monkeypatch.setattr(device_mod, "Scanner", Jams)
    monkeypatch.setattr(device_mod, "release", release_fails)
    d = Device("127.0.0.1", tmp_path / "id.json")
    monkeypatch.setattr(d, "connect", _no_reply)
    d.ident = IDENT
    with pytest.raises(FeedError, match="paper jam"):
        list(d.scan(ScanSettings("color", 300, True)))


def test_model_from_the_greeting_retries_and_is_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: Iterator[bytes] = iter([b"", b"VENS"])
    calls: list[str] = []

    def greeting(ip: str, timeout: float = 2.0) -> bytes:
        calls.append(ip)
        magic = next(answers)
        if not magic:
            raise ConnectionRefusedError("lost")
        return magic

    monkeypatch.setattr(locator_mod, "greeting_magic", greeting)
    loc = locator_mod.AutoLocator()
    assert loc.model("192.0.2.20") == "ix500"
    assert loc.model("192.0.2.20") == "ix500" and len(calls) == 2  # remembered


def test_ix1500_is_found_over_tcp_without_udp(monkeypatch: pytest.MonkeyPatch) -> None:
    def greeting(_ip: str, timeout: float = 2.0) -> bytes:
        return b"ssNR"

    monkeypatch.setattr(locator_mod, "greeting_magic", greeting)
    assert locator_mod.AutoLocator().model("192.0.2.22") == "ix1500"


def test_no_answer_is_an_error_not_an_iX1500(monkeypatch: pytest.MonkeyPatch) -> None:
    def refused(_ip: str, timeout: float = 2.0) -> bytes:
        raise ConnectionRefusedError("nothing there")

    monkeypatch.setattr(locator_mod, "greeting_magic", refused)
    with pytest.raises(ConnectionError):
        locator_mod.AutoLocator().model("192.0.2.21")


def test_empty_password_means_no_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scansnap.adapters.ix500.discovery import ScannerInfo

    sent: list[str | None] = []

    class Ctl:
        def __init__(self, *_args: object) -> None:
            pass

        def register(self, _ip: str, _ver: int, password: str | None) -> None:
            sent.append(password)

    info = ScannerInfo("127.0.0.1", 53218, 53219, "", "iX500-A0XX012345", "ScanSnap iX500", 6, "", "", "A0XX012345")
    monkeypatch.setattr(device_mod, "Control", Ctl)
    monkeypatch.setattr(device_mod, "local_ip_for", _zero_mac)
    monkeypatch.setattr(device_mod, "ScsiChannel", _NoChannel)
    for password, expected in (("", ""), (None, "2345"), ("custom", "custom")):
        d = Device("127.0.0.1", tmp_path / "id.json", password=password)
        d.ident = IDENT

        def known(_mac: bytes, info: ScannerInfo = info) -> ScannerInfo:
            return info

        monkeypatch.setattr(d, "_discover", known)
        d._register()  # pyright: ignore[reportPrivateUsage]
        assert sent[-1] == expected


class _NoChannel:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        pass

    def inquiry(self) -> tuple[str, str, str]:
        return "FUJITSU", "ScanSnap iX500", "0U00"

    def hardware_status(self) -> bytes:
        return bytes(0x20)


def test_connect_retries_parts_that_failed_to_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    starts: list[int] = []

    class FlakyListener:
        alive = True

        def __init__(self, _ip: str, port: int, _cb: object) -> None:
            starts.append(port)
            if len(starts) == 1:
                raise OSError("port busy")
            self.port = port

        def close(self) -> None:
            pass

    class QuietHeartbeat:
        alive = True

        def __init__(self, *_args: object) -> None:
            pass

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(device_mod, "Listener", FlakyListener)
    monkeypatch.setattr(device_mod, "Heartbeat", QuietHeartbeat)
    monkeypatch.setattr(device_mod, "ScsiChannel", _NoChannel)
    d = Device("127.0.0.1", tmp_path / "id.json")
    d.ident = IDENT
    monkeypatch.setattr(d, "_register", _no_reply)
    d.connect()
    d.connect()  # the listener that failed is started now
    ports = [listener.port for listener in d._listeners]  # pyright: ignore[reportPrivateUsage]
    d.disconnect()
    assert sorted(ports) == sorted({IDENT.host_port, notify.STARTUP_PORT})


def test_release_failure_after_a_good_scan_is_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Channel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc: object) -> None:
            pass

    class OneSheet:
        def __init__(self, _ch: object) -> None:
            pass

        def scan(self, *_args: object) -> Iterator[Sheet]:
            yield Sheet(b"front", b"back", None)

    def release_fails(*_args: object) -> None:
        raise ConnectionResetError("release failed")

    monkeypatch.setattr(device_mod, "ScsiChannel", Channel)
    monkeypatch.setattr(device_mod, "Scanner", OneSheet)
    monkeypatch.setattr(device_mod, "release", release_fails)
    d = Device("127.0.0.1", tmp_path / "id.json", rotate=False, crop=False)
    monkeypatch.setattr(d, "connect", _no_reply)
    d.ident = IDENT
    with pytest.raises(ConnectionResetError):
        list(d.scan(ScanSettings("color", 300, True)))


def test_disconnect_interrupts_a_running_scan(tmp_path: Path) -> None:
    interrupted: list[bool] = []

    class Blocked:
        def interrupt(self) -> None:
            interrupted.append(True)

    d = Device("127.0.0.1", tmp_path / "id.json")
    d._scan_ch = Blocked()  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    d.disconnect()
    assert interrupted == [True]


def test_slow_callback_does_not_turn_repeats_into_new_notices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_notify, "REPEAT_WINDOW", 0.3)
    got: list[notify.Notification] = []

    def slow(note: notify.Notification) -> None:
        got.append(note)
        time.sleep(0.6)  # e.g. registering after power on

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    listener = notify.Listener("127.0.0.1", port, slow)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            for _ in range(3):
                s.sendto(_notice(notify.POWER_ON, 1), ("127.0.0.1", port))
                time.sleep(0.05)
        time.sleep(1.5)
    finally:
        listener.close()
    assert len(got) == 1


def test_poller_scan_and_release_share_the_single_scan_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The iX500 serves one 53218 connection at a time: a second one gets no greeting until the first closes."""
    from test_ix500_scan import MockIx500

    from scansnap.adapters.ix500 import scanner as scanner_mod

    class OneAtATime(MockIx500):
        def _serve(self) -> None:
            while True:  # one connection after the other, like the scanner
                try:
                    super()._serve()
                except OSError:
                    return

    mock = OneAtATime(sheets=1)

    class LocalChannel(ScsiChannel):
        def __init__(self, ip: str, mac: bytes, port: int = 0, timeout: float = 60.0):
            super().__init__(ip, mac, port=mock.port, timeout=5.0)

    monkeypatch.setattr(device_mod, "ScsiChannel", LocalChannel)
    monkeypatch.setattr(scanner_mod, "ScsiChannel", LocalChannel)
    d = Device("127.0.0.1", tmp_path / "id.json", rotate=False, crop=False)
    d.ident = IDENT
    d._registered = True  # pyright: ignore[reportPrivateUsage]
    stop = threading.Event()
    poller = threading.Thread(target=d._poll, args=(stop,), daemon=True)  # pyright: ignore[reportPrivateUsage]
    poller.start()
    try:
        deadline = time.monotonic() + 3
        while d.paper_loaded() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert d.paper_loaded() is True  # poller holds the channel
        sheets = list(d.scan(ScanSettings("color", 300, True)))
        assert len(sheets) == 1
        deadline = time.monotonic() + 3
        while d.paper_loaded() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert d.paper_loaded() is not None  # poller is back after scan and release
    finally:
        stop.set()
        poller.join(3)
        mock.srv.close()
    assert any(cdb[0] == 0xD6 for cdb in mock.received)  # released on its own connection


def test_dead_listener_is_started_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[object] = []

    class FakeListener:
        def __init__(self, _ip: str, port: int, _cb: object) -> None:
            self.port, self.alive = port, True
            created.append(self)

        def close(self) -> None:
            pass

    class QuietHeartbeat:
        alive = True

        def __init__(self, *_args: object) -> None:
            pass

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(device_mod, "Listener", FakeListener)
    monkeypatch.setattr(device_mod, "Heartbeat", QuietHeartbeat)
    d = Device("127.0.0.1", tmp_path / "id.json")
    d.ident = IDENT
    monkeypatch.setattr(d, "_register", _no_reply)
    d.connect()
    first = d._listeners[0]  # pyright: ignore[reportPrivateUsage]
    first.alive = False  # type: ignore[attr-defined]  # pyright: ignore[reportAttributeAccessIssue]
    d.connect()
    assert len(created) == 3 and first not in d._listeners  # pyright: ignore[reportPrivateUsage]
    d.disconnect()
