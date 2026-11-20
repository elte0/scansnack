import os
import signal
import socket
import struct
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Thread

import pytest
from test_ix1300_receiver import Replay

from scansnap.adapters.ix1300 import receiver
from scansnap.adapters.ix1300.scanner import ChannelOpener
from scansnap.adapters.scansnap_wifi.notify import Listener, Notification
from scansnap.adapters.scansnap_wifi.scsi import Response, ScsiChannel
from scansnap.app import ix1300_output
from scansnap.app.ix1300_output import publish_sheet
from scansnap.core.model import Sheet


@pytest.mark.parametrize("fault", ["jam-front", "jam-back", "before-empty", "release", "publication"])
def test_wake_fault_and_next_scan(
    fault: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    service = receiver.Receiver()
    port: list[int] = []
    delivered: list[int] = []
    offline, recovered, finished = Event(), Event(), Event()
    first = Replay(fault, fault == "release")
    second = Replay("", False)
    current = first

    def listen(ip: str, _: int, callback: Callable[[Notification], None], *, magic: bytes) -> Listener:
        def deliver(note: Notification) -> None:
            callback(note)
            delivered.append(note.counter)

        bound = Listener(ip, 0, deliver, magic=magic)
        port.append(bound.sock.getsockname()[1])
        return bound

    monkeypatch.setattr(receiver, "Listener", listen)
    monkeypatch.setattr(receiver, "_RETRY", 60.0)
    attempts = 0

    @contextmanager
    def session(timeout: float) -> Generator[ChannelOpener, None, None]:
        nonlocal attempts, current
        attempts += 1
        if attempts == 1:
            offline.set()
            raise OSError("sleeping")
        if attempts == 2:
            monkeypatch.setattr(receiver, "_RETRY", 0.01)
        if attempts == 3:
            recovered.set()
        yield lambda timeout: current.open(timeout)

    def publish(folder: Path, sheet: Sheet) -> None:
        if current is first and fault == "publication":
            recovered.set()
            raise OSError("disk unavailable")
        publish_sheet(folder, sheet)
        if current is second:
            finished.set()

    def send(counter: int) -> None:
        packet = struct.pack(">I4sIIB31x", 48, b"VENS", 1, 0, counter)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.sendto(packet, ("127.0.0.1", port[0]))

    monkeypatch.setattr(ix1300_output, "publish_sheet", publish)
    worker = Thread(target=ix1300_output.receive_to_folder, args=(service, "127.0.0.1", bytes(6), session, tmp_path))
    worker.start()
    try:
        assert offline.wait(3)
        send(1)
        send(1)
        assert recovered.wait(4)
        assert len(list(tmp_path.glob("*.pdf"))) == int(fault == "release")
        current = second
        send(2)
        assert finished.wait(3)
    finally:
        service.stop()
        worker.join(4)
    assert not worker.is_alive()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rebound:
        rebound.bind(("", port[0]))
    assert delivered == [1, 2]
    assert first.calls.count([0xD6]) == second.calls.count([0xD6]) == 1
    assert len(list(tmp_path.glob("*.pdf"))) == 1 + int(fault == "release")
    if fault.startswith("jam"):
        assert "paper jam" in caplog.text


@pytest.fixture
def ephemeral_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    def listen(ip: str, _: int, callback: Callable[[Notification], None], *, magic: bytes) -> Listener:
        return Listener(ip, 0, callback, magic=magic)

    monkeypatch.setattr(receiver, "Listener", listen)


class Blocked(Replay):
    def __init__(self, sock: socket.socket, reading: Callable[[], None]) -> None:
        super().__init__("", False)
        self.sock = sock
        self.reading = reading

    def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        if cdb[0] == 0x28:
            self.calls[-1].append(0x28)
            self.reading()
            assert self.sock.recv(1) == b""
            raise OSError("transfer interrupted")
        return super().command(cdb, out, in_len)


@pytest.mark.usefixtures("ephemeral_listener")
def test_stop_interrupts_transfer_and_allows_release() -> None:
    service = receiver.Receiver()
    reading = Event()
    local, peer = socket.socketpair()

    replay = Blocked(local, reading.set)

    @contextmanager
    def session(timeout: float) -> Generator[ChannelOpener, None, None]:
        yield replay.open

    service._notice(Notification(1, 1, "", ""))  # pyright: ignore[reportPrivateUsage]
    outputs: list[Sheet] = []
    with local, peer:
        worker = Thread(target=service.run, args=("127.0.0.1", bytes(6), session, outputs.append))
        worker.start()
        try:
            assert reading.wait(3)
        finally:
            service.stop()
            worker.join(4)
        assert not worker.is_alive()
    assert replay.fed and replay.calls[-1] == [0xD6]
    assert outputs == []


def _foreground_receive(folder: Path, ready: Connection) -> None:
    service = receiver.Receiver()
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    ports: list[int] = []

    def listen(ip: str, _: int, callback: Callable[[Notification], None], *, magic: bytes) -> Listener:
        bound = Listener(ip, 0, callback, magic=magic)
        ports.append(bound.sock.getsockname()[1])
        return bound

    local, peer = socket.socketpair()
    replay = Blocked(local, lambda: ready.send(True))

    @contextmanager
    def session(timeout: float) -> Generator[ChannelOpener, None, None]:
        yield replay.open

    service._notice(Notification(1, 1, "", ""))  # pyright: ignore[reportPrivateUsage]
    with pytest.MonkeyPatch.context() as patch, ready, local, peer:
        patch.setattr(receiver, "Listener", listen)
        ix1300_output.receive_to_folder(service, "127.0.0.1", bytes(6), session, folder)
        assert replay.fed and replay.calls[-1] == [0xD6]
        assert list(folder.iterdir()) == []
        assert {signum: signal.getsignal(signum) for signum in previous} == previous
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as rebound:
            rebound.bind(("", ports[0]))


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_foreground_signal_releases_scan_and_listener(signum: signal.Signals, tmp_path: Path) -> None:
    context = get_context("spawn")
    receiving, sending = context.Pipe(duplex=False)
    with receiving, sending:
        process = context.Process(target=_foreground_receive, args=(tmp_path, sending))
        process.start()
        try:
            sending.close()
            assert receiving.poll(5) and receiving.recv() is True
            assert process.pid is not None
            os.kill(process.pid, signum)
            process.join(5)
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()


@pytest.mark.usefixtures("ephemeral_listener")
def test_latest_fresh_notice_and_inflight_repeats(monkeypatch: pytest.MonkeyPatch) -> None:
    service = receiver.Receiver()
    clock = 0.0
    outputs: list[Sheet] = []
    monkeypatch.setattr(receiver, "monotonic", lambda: clock)

    def notice(counter: int) -> None:
        service._notice(Notification(1, counter, "", ""))  # pyright: ignore[reportPrivateUsage]

    class NoticesDuringScan(Replay):
        def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
            nonlocal clock
            if cdb[0] == 0xC2 and not outputs and clock == 100:
                # The stale pre-registration note must expire before setup.
                assert self.calls == [[]]
                clock += 1
                notice(2)
            if cdb[0] == 0xD6 and not outputs:
                # These have already passed the listener's repeat filter.
                notice(3)
                notice(4)
                clock += 4
                notice(2)
            if cdb[0] == 0xD6 and len(outputs) == 1:
                # The active counter is 4. An earlier counter is eligible again.
                notice(2)
            return super().command(cdb, out, in_len)

    replay = NoticesDuringScan("", False)

    @contextmanager
    def session(timeout: float) -> Generator[ChannelOpener, None, None]:
        nonlocal clock
        clock = 100
        yield replay.open

    def publish(sheet: Sheet) -> None:
        outputs.append(sheet)
        replay.fed = False
        if len(outputs) == 3:
            service.stop()

    notice(1)
    worker = Thread(target=service.run, args=("127.0.0.1", bytes(6), session, publish))
    worker.start()
    try:
        worker.join(3)
        assert not worker.is_alive()
    finally:
        service.stop()
        worker.join(3)
    assert replay.calls[0] == [0xC2]
    assert replay.calls.count([0xD6]) == len(outputs) == 3


@pytest.mark.usefixtures("ephemeral_listener")
def test_status_loss_reconnects_without_wake_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    service = receiver.Receiver()
    monkeypatch.setattr(receiver, "_RETRY", 0)
    replay = Replay("", False)
    attempts = 0

    def offline(timeout: float) -> AbstractContextManager[ScsiChannel]:
        raise OSError("unreachable")

    @contextmanager
    def session(timeout: float) -> Generator[ChannelOpener, None, None]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            yield offline
        else:
            service.stop()
            yield replay.open

    service.run("127.0.0.1", bytes(6), session, lambda sheet: pytest.fail("unexpected scan"))
    assert attempts == 2
