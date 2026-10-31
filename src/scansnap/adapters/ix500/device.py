"""The iX500 as a ``ports.scanner.ScannerPort``: register like ScanSnap Home, keep the session, scan."""

from __future__ import annotations

import io
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

from PIL import Image

from ...core.images import process
from ...core.model import ScanSettings, Sheet
from . import wire
from .control import Control, HostIdentity, RegistrationError, default_password
from .discovery import Heartbeat, ScannerInfo, discover, local_ip_for
from .identity import DEFAULT_PATH, load_or_create, local_mac
from .notify import BUTTON, POWER_OFF, POWER_ON, STARTUP_PORT, Listener, Notification
from .scanner import DPIS, Multifeed, Scanner, release
from .scsi import ScsiChannel, ScsiError

DISCOVERY_ATTEMPTS = 3
PAPER_POLL = 0.25  # the iX1500's interval (docs/protocol.md); ScanSnap Home does not poll the iX500
POLL_RETRY = 1.0  # after a failed C2
POLL_FAILURES = 4  # failed C2 in a row that end the session (scanner gone without a power-off notice)
FEEDER_EMPTY = 0x80  # C2 byte 3: 0x80 with an empty feeder, 0 with paper (live 2026-10-30)


def nearest_dpi(dpi: int) -> int:
    return min(DPIS, key=lambda d: abs(d - dpi))


def to_gray(jpeg: bytes) -> bytes:
    """The iX500 always scans in color; gray is converted on the host like ScanSnap Home does."""
    im = Image.open(io.BytesIO(jpeg))
    dpi = im.info.get("dpi")
    buf = io.BytesIO()
    im.convert("L").save(buf, "JPEG", quality=90, **({"dpi": dpi} if dpi else {}))
    return buf.getvalue()


class Device:
    """iX500 behind a fixed IP.

    :meth:`connect` registers this host (0x11), starts the heartbeat and listens for notifications:
    the Scan button calls ``on_button``, power off ends the session, power on registers again.
    """

    def __init__(
        self,
        ip: str,
        identity_path: Path = DEFAULT_PATH,
        *,
        password: str | None = None,
        page: str = "auto",
        multifeed: Multifeed = "ultrasonic",
        rotate: bool = True,
        crop: bool = True,
        on_button: Callable[[], None] | None = None,
    ):
        self.ip, self.identity_path, self.password = ip, identity_path, password
        self.page, self.rotate, self.crop = page, rotate, crop
        self.multifeed: Multifeed = multifeed
        self.on_button = on_button
        self.info: ScannerInfo | None = None
        self.ident: HostIdentity | None = None
        self._lock = threading.Lock()
        self._registered = False
        self._heartbeat: Heartbeat | None = None
        self._listeners: list[Listener] = []
        self._paper: bool | None = None
        self._scanning = threading.Event()
        self._closed = threading.Event()  # set by disconnect(): late notifications do nothing
        self._poll_lock = threading.Lock()  # held while a C2 request of the poller is in flight
        self._poll_stop: threading.Event | None = None  # one event per poller thread
        self._poller: threading.Thread | None = None
        self._scan_ch: ScsiChannel | None = None  # open while a scan runs; disconnect() interrupts it

    @property
    def connected(self) -> bool:
        return self._registered

    def paper_loaded(self) -> bool | None:
        """``ports.scanner.PaperSensor``: feeder state from the C2 poller."""
        return self._paper

    def _poll(self, stop: threading.Event) -> None:
        """C2 every 250 ms on a connection of its own, closed while a scan runs: the scanner serves one
        scan-channel connection at a time."""
        ch: ScsiChannel | None = None
        failures = 0
        try:
            while not stop.is_set():
                wait = PAPER_POLL
                with self._poll_lock:
                    if self._scanning.is_set() or not self._registered or self.ident is None:
                        if ch is not None:
                            ch.close()
                            ch = None
                        self._paper = None
                        failures = 0
                    else:
                        try:
                            if ch is None:
                                ch = ScsiChannel(self.ip, self.ident.mac, timeout=3.0)
                            self._paper = not ch.hardware_status()[3] & FEEDER_EMPTY
                            failures = 0
                        except (OSError, ScsiError, wire.ProtocolError):
                            self._paper = None
                            if ch is not None:
                                ch.close()
                                ch = None
                            wait = POLL_RETRY
                            failures += 1
                            if failures >= POLL_FAILURES:
                                self._registered = False  # the next connect() registers again
                stop.wait(wait)
        finally:
            if ch is not None:
                ch.close()
            self._paper = None

    def _discover(self, mac: bytes) -> ScannerInfo:
        for _ in range(DISCOVERY_ATTEMPTS):  # UDP: a single probe or reply can get lost
            if found := discover(mac, target=self.ip, timeout=1.0):
                return found[0]
        raise ConnectionError(f"no iX500 answers at {self.ip}")

    def _register(self) -> None:
        if self.ident is None:
            raise ConnectionError("not connected")
        self.info = self._discover(self.ident.mac)
        # an explicit "" means the scanner has no password; None falls back to the factory default
        password = next((p for p in (self.password, self.ident.password) if p is not None), None)
        if password is None:
            password = default_password(self.info.serial)
        ctl = Control(self.ip, self.ident)
        ctl.register(local_ip_for(self.ip), self.info.if_version, password)
        with ScsiChannel(self.ip, self.ident.mac, timeout=10.0) as ch:
            ch.inquiry()
            ch.hardware_status()
        self._registered = True

    def connect(self) -> None:
        """Registers if needed and (re)starts whatever of heartbeat, listeners and poller is missing."""
        with self._lock:
            self._closed.clear()
            if self.ident is None:
                # The first run takes over the host ID the scanner already knows (see identity.load_or_create).
                mac = local_mac(self.ip)
                paired = None if self.identity_path.exists() else self._discover(mac).paired_host_id
                self.ident = load_or_create(self.identity_path, paired, mac)
            if not self._registered:
                self._register()
            if self._heartbeat is not None and not self._heartbeat.alive:  # its thread ended on a network error
                self._heartbeat.close()
                self._heartbeat = None
            if self._heartbeat is None:
                self._heartbeat = Heartbeat(self.ip, self.ident.mac)
                self._heartbeat.start()
            for dead in [listener for listener in self._listeners if not listener.alive]:
                dead.close()  # its thread ended on a network error; started again below
                self._listeners.remove(dead)
            listening = {listener.port for listener in self._listeners}
            for port in (self.ident.host_port, STARTUP_PORT):
                if port in listening:
                    continue
                try:
                    self._listeners.append(Listener(self.ip, port, self._notified))
                except OSError as e:  # port in use, e.g. by ScanSnap Home; the next connect() tries again
                    print(f"UDP {port} not available: {e}", file=sys.stderr)
            if self._poller is None or not self._poller.is_alive():
                self._poll_stop = threading.Event()
                self._poller = threading.Thread(target=self._poll, args=(self._poll_stop,), daemon=True)
                self._poller.start()

    def _notified(self, note: Notification) -> None:
        if self._closed.is_set():
            return
        if note.event == BUTTON and self.on_button:
            self.on_button()
        elif note.event == POWER_OFF:
            self._registered = False
        elif note.event == POWER_ON:
            with self._lock:
                if self._closed.is_set():
                    return
                self._registered = False
                try:
                    self._register()
                except (OSError, wire.ProtocolError, RegistrationError) as e:
                    print(f"Registering after power on failed: {e}", file=sys.stderr)  # connect() retries

    def disconnect(self) -> None:
        self._closed.set()
        with self._lock:
            listeners, self._listeners = self._listeners, []
            heartbeat, self._heartbeat = self._heartbeat, None
            poller, stop = self._poller, self._poll_stop
            self._poller = self._poll_stop = None
            self._registered = False
            scan_ch = self._scan_ch
        if scan_ch is not None:
            scan_ch.interrupt()  # a scan blocked in a read ends with an OSError
        # outside the lock: a notification callback waiting for it can return
        if stop is not None:
            stop.set()
        for listener in listeners:
            listener.close()
        if heartbeat is not None:
            heartbeat.close()
        if poller is not None:
            poller.join(5)

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        """All sheets in the feeder (``ports.scanner.ScannerPort``). Raises NoPaper if the feeder is empty."""
        self.connect()
        assert self.ident is not None
        dpi = nearest_dpi(settings.dpi)

        def fix(jpeg: bytes) -> bytes:
            if self.rotate or self.crop or whiten:
                jpeg = process(jpeg, dpi, rotate=self.rotate, crop=self.crop, whiten_background=whiten)
            return to_gray(jpeg) if settings.mode == "gray" else jpeg

        self._scanning.set()
        with self._poll_lock:  # wait for an in-flight C2 of the poller
            pass
        failed = False
        try:
            with ScsiChannel(self.ip, self.ident.mac) as ch:
                self._scan_ch = ch
                for sheet in Scanner(ch).scan(dpi, self.page, self.multifeed):
                    yield Sheet(
                        fix(sheet.front), fix(sheet.back) if settings.duplex and sheet.back else None, sheet.info
                    )
        except BaseException:
            failed = True
            raise
        finally:
            self._scan_ch = None
            try:
                release(self.ip, self.ident.mac)
            except (OSError, ScsiError, wire.ProtocolError) as e:
                if not failed:
                    raise
                # the scan's own error (e.g. a paper jam) is the one the caller needs
                print(f"Releasing the scanner failed: {e}", file=sys.stderr)
            finally:
                self._scanning.clear()
