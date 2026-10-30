"""A scan job as a whole: connect like ScanSnap Home, select a profile, deliver sheets."""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from collections.abc import Callable, Generator, Iterator

from ...core.images import process
from ...core.model import ScanSettings, Sheet
from . import wire
from .button import ButtonListener, Notification
from .control import Control, HostIdentity
from .discovery import HOST_UDP_PORT, Heartbeat
from .scanner import Scanner
from .scsi import ScsiChannel

ANNOUNCE_EVERY = 10.0  # ScanSnap Home repeats 0x0210 about every 10 s (cold capture)


class Session:
    """Connection like ScanSnap Home at app start (cold capture), open until :meth:`close`.

    Scan channel A with the init sequence, control channel 0x13 → 0x0100 → 0x0210 → 0x11 (flag 0),
    then open scan channel B anew (C2 polling only) and the UDP heartbeat. With ``announce_every``,
    additionally 0x0210 at that interval; ``lock`` pauses it (during profile selection and scan).
    """

    def __init__(
        self,
        ctl: Control,
        ip: str,
        ident: HostIdentity,
        *,
        pair: bool = False,
        handshake: bool = True,
        heartbeat: bool = True,
        announce_every: float | None = None,
    ):
        self.ctl, self.ip, self.ident = ctl, ip, ident
        self.pair, self.handshake, self.heartbeat = pair, handshake, heartbeat
        self.announce_every = announce_every
        self.lock = threading.Lock()
        self.ch: ScsiChannel | None = None
        self._hb: Heartbeat | None = None
        self._stop = threading.Event()
        self._announcer: threading.Thread | None = None

    def open(self) -> ScsiChannel:
        ch = ScsiChannel(self.ip, self.ident.mac)
        try:
            ch.open_session()
            ch.start_polling()
            host_ip = None
            if self.handshake:
                host_ip = self.ctl.handshake() if self.pair else self.ctl.connect()
                print(f"Connected (host {host_ip}, ID {self.ident.host_id})", file=sys.stderr)
        finally:
            ch.close()
        # UNVERIFIED: whether the pauses (0.2 s before channel B, 1.0 s after) are needed is not tested individually.
        time.sleep(0.2)
        try:
            self.ch = ScsiChannel(self.ip, self.ident.mac)
            self.ch.hardware_status()
            self.ch.start_polling()
            # UNVERIFIED: whether the scanner ends the session without a heartbeat is not tested.
            if self.heartbeat:
                self._hb = Heartbeat(self.ip, self.ident.mac, HOST_UDP_PORT)
                self._hb.start()
            if self.announce_every and host_ip:
                self._start_announcer(host_ip)
        except BaseException:
            self.close()
            raise
        time.sleep(1.0)
        return self.ch

    def _start_announcer(self, host_ip: str) -> None:
        def run() -> None:
            while not self._stop.wait(self.announce_every):
                with self.lock:
                    try:
                        self.ctl.announce(host_ip)
                    except Exception as e:  # noqa: BLE001 – C2 polling shows a failure
                        print(f"0x0210 failed: {e}", file=sys.stderr)

        self._announcer = threading.Thread(target=run, daemon=True)
        self._announcer.start()

    @property
    def alive(self) -> bool:
        return self.ch is not None and self.ch.polling

    def close(self) -> None:
        self._stop.set()
        if self._announcer:
            self._announcer.join(15)
            self._announcer = None
        if self._hb:
            self._hb.close()
            self._hb = None
        if self.ch:
            self.ch.close()
            self.ch = None


@contextlib.contextmanager
def session(
    ctl: Control, ip: str, ident: HostIdentity, *, pair: bool = False, handshake: bool = True, heartbeat: bool = True
) -> Generator[ScsiChannel]:
    """Session for a single job (see :class:`Session`). Yields the open scan channel."""
    s = Session(ctl, ip, ident, pair=pair, handshake=handshake, heartbeat=heartbeat)
    try:
        yield s.open()
    finally:
        s.close()


class Device:
    """Scanner behind a fixed IP.

    Without ``keep_alive`` every call of :meth:`scan` is a session of its own. With ``keep_alive`` the session
    stays open between scans (C2 polling, heartbeat, 0x0210 like ScanSnap Home), and a scan then starts
    without connecting first. If the connection has dropped, :meth:`scan` re-establishes it once.

    ``on_button`` is called (in the listener thread) when "Scan" is pressed on the touch panel; this only works
    while the persistent session is up. The caller then starts :meth:`scan` as usual.
    """

    def __init__(
        self,
        ip: str,
        ident: HostIdentity,
        profile_id: str | None = None,
        *,
        rotate: bool = True,
        crop: bool = True,
        keep_alive: bool = False,
        on_button: Callable[[Notification], None] | None = None,
    ):
        # The scanner delivers the pages rotated by 180° (live test 2026-10-29, front and back)
        # and with the whole scan area (background around the paper).
        self.ip, self.ident, self.profile_id = ip, ident, profile_id
        self.rotate, self.crop, self.keep_alive = rotate, crop, keep_alive
        self.ctl = Control(ip, ident)
        self.on_button = on_button
        self._session: Session | None = None
        self._button: ButtonListener | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None and self._session.alive

    def connect(self) -> None:
        """Open a persistent session unless a live one exists."""
        self._live_session()

    def _live_session(self) -> Session:
        if self._session is not None and self._session.alive:
            return self._session
        self.disconnect()
        s = Session(self.ctl, self.ip, self.ident, announce_every=ANNOUNCE_EVERY)
        s.open()
        self._session = s
        if self.on_button and self._button is None:
            try:
                self._button = ButtonListener(self.ip, self.ident.host_port, self.on_button)
            except OSError as e:  # port in use, e.g. by ScanSnap Home
                print(f"Scan button not available (UDP {self.ident.host_port}: {e})", file=sys.stderr)
        return s

    def disconnect(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None
        if self._session is not None:
            self._session.close()
            self._session = None

    def ensure_profile(self) -> None:
        """Creates a user + our own profile if the scanner does not return one.

        The scanner occasionally loses its profiles (e.g. after switching users on the panel).
        """
        # UNVERIFIED: the cause of profiles disappearing without a user switch is unknown.
        profiles = self.ctl.get_users().get("profiles", [])
        if any(p.get("user_id") == self.ident.user_id for p in profiles):
            return
        print("No profile of our own on the scanner – running claim", file=sys.stderr)
        self.disconnect()  # pairing needs a connection sequence of its own
        with session(self.ctl, self.ip, self.ident, pair=True):
            self.ctl.claim(self.ctl.local_ip())

    def scan(
        self,
        settings: ScanSettings,
        *,
        whiten: bool = False,
        select: bool = True,
        pair: bool = False,
        handshake: bool = True,
        heartbeat: bool = True,
    ) -> Iterator[Sheet]:
        """Scans all sheets in the feeder (``ports.scanner.ScannerPort``). Raises NoPaper if the feeder is empty.

        ``whiten`` sets the paper color to white (removes the background). ``select``, ``pair``, ``handshake``
        and ``heartbeat`` are switches for experiments (CLI) and only apply without ``keep_alive``.
        """
        if not self.keep_alive:
            if select and not self.profile_id:
                self.ensure_profile()
            with session(self.ctl, self.ip, self.ident, pair=pair, handshake=handshake, heartbeat=heartbeat) as ch:
                yield from self._scan(ch, settings, select, whiten)
            return
        for attempt in range(2):
            delivered = False
            try:
                if select and not self.profile_id:
                    self.ensure_profile()
                s = self._live_session()
                if s.ch is None:
                    raise ConnectionError("session without a scan channel")
                with s.lock:
                    for sheet in self._scan(s.ch, settings, select, whiten, resume_polling=True):
                        delivered = True
                        yield sheet
                return
            except (OSError, wire.ProtocolError):
                self.disconnect()
                if delivered or attempt:
                    raise
                print("Connection to the scanner lost – reconnecting", file=sys.stderr)

    def _scan(
        self, ch: ScsiChannel, settings: ScanSettings, select: bool, whiten: bool, *, resume_polling: bool = False
    ) -> Iterator[Sheet]:
        if select:
            prof_id = self.profile_id
            if prof_id:
                self.ctl.get_users()  # like ScanSnap Home: 0x0100 right before 0x0610
            else:
                prof = self.ctl.pick_profile(self.ctl.local_ip())
                prof_id = prof["prof_id"]
                print(f"Profile: {prof.get('prof_name')} ({prof.get('user_name')}, {prof_id})", file=sys.stderr)
            self.ctl.select_profile(prof_id)
        if not ch.stop_polling():
            raise ConnectionError("scanner does not respond to C2")
        try:
            for sheet in Scanner(ch).scan(settings):
                if self.rotate or self.crop or whiten:

                    def fix(jpeg: bytes) -> bytes:
                        return process(jpeg, settings.dpi, rotate=self.rotate, crop=self.crop, whiten_background=whiten)

                    sheet = Sheet(fix(sheet.front), sheet.back and fix(sheet.back), sheet.info)
                yield sheet
        finally:
            if resume_polling:
                ch.start_polling()  # like ScanSnap Home: continue C2 after the scan
