"""Ein Scan-Auftrag als Ganzes: Verbinden wie ScanSnap Home, Profil wählen, Blätter liefern."""

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

ANNOUNCE_EVERY = 10.0  # ScanSnap Home wiederholt 0x0210 etwa alle 10 s (Kalt-Capture)


class Session:
    """Verbindung wie ScanSnap Home beim App-Start (Kalt-Capture), offen bis :meth:`close`.

    Scan-Kanal A mit Init-Sequenz, Steuerkanal 0x13 → 0x0100 → 0x0210 → 0x11 (Flag 0),
    dann Scan-Kanal B neu aufbauen (nur C2-Polling) und UDP-Heartbeat. Mit ``announce_every``
    zusätzlich 0x0210 in diesem Abstand; ``lock`` hält das an (während Profilwahl und Scan).
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
                print(f"Verbunden (Host {host_ip}, ID {self.ident.host_id})", file=sys.stderr)
        finally:
            ch.close()
        # UNVERIFIED: ob die Pausen (0,2 s vor Kanal B, 1,0 s danach) nötig sind, ist nicht einzeln getestet.
        time.sleep(0.2)
        try:
            self.ch = ScsiChannel(self.ip, self.ident.mac)
            self.ch.hardware_status()
            self.ch.start_polling()
            # UNVERIFIED: ob der Scanner ohne Heartbeat die Sitzung beendet, ist nicht getestet.
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
                    except Exception as e:  # noqa: BLE001 – Ausfall zeigt das C2-Polling
                        print(f"0x0210 fehlgeschlagen: {e}", file=sys.stderr)

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
    """Sitzung für einen einzelnen Auftrag (siehe :class:`Session`). Liefert den offenen Scan-Kanal."""
    s = Session(ctl, ip, ident, pair=pair, handshake=handshake, heartbeat=heartbeat)
    try:
        yield s.open()
    finally:
        s.close()


class Device:
    """Scanner hinter einer festen IP.

    Ohne ``keep_alive`` ist jeder Aufruf von :meth:`scan` eine eigene Sitzung. Mit ``keep_alive`` bleibt die
    Sitzung zwischen den Scans offen (C2-Polling, Heartbeat, 0x0210 wie ScanSnap Home), der Scan beginnt dann
    ohne Verbindungsaufbau. Ist die Verbindung abgerissen, baut :meth:`scan` sie einmal neu auf.

    ``on_button`` wird (im Listener-Thread) aufgerufen, wenn am Touchpanel „Scan“ gedrückt wird; das geht nur,
    solange die dauerhafte Sitzung steht. Der Aufrufer startet dann wie gewohnt :meth:`scan`.
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
        # Der Scanner liefert die Seiten um 180° gedreht (Live-Test 2026-10-29, Vorder- und Rückseite)
        # und mit dem ganzen Scanfeld (Hintergrund um das Papier).
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
        """Dauerhafte Sitzung aufbauen, falls keine lebendige besteht."""
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
            except OSError as e:  # Port belegt, z. B. durch ScanSnap Home
                print(f"Scan-Taste nicht verfügbar (UDP {self.ident.host_port}: {e})", file=sys.stderr)
        return s

    def disconnect(self) -> None:
        if self._button is not None:
            self._button.close()
            self._button = None
        if self._session is not None:
            self._session.close()
            self._session = None

    def ensure_profile(self) -> None:
        """Legt Benutzer + eigenes Profil an, wenn der Scanner keins liefert.

        Der Scanner verliert die Profile gelegentlich (z. B. nach Benutzerwechsel am Panel).
        """
        # UNVERIFIED: Ursache für verschwundene Profile ohne Benutzerwechsel unbekannt.
        profiles = self.ctl.get_users().get("profiles", [])
        if any(p.get("user_id") == self.ident.user_id for p in profiles):
            return
        print("Kein eigenes Profil am Scanner – führe claim aus", file=sys.stderr)
        self.disconnect()  # Koppeln braucht einen eigenen Verbindungsablauf
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
        """Scannt alle Blätter im Einzug (``ports.scanner.ScannerPort``). Wirft NoPaper, wenn der Einzug leer ist.

        ``whiten`` setzt die Papierfarbe auf Weiß (Hintergrund entfernen). ``select``, ``pair``, ``handshake``
        und ``heartbeat`` sind Schalter zum Experimentieren (CLI) und gelten nur ohne ``keep_alive``.
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
                    raise ConnectionError("Sitzung ohne Scan-Kanal")
                with s.lock:
                    for sheet in self._scan(s.ch, settings, select, whiten, resume_polling=True):
                        delivered = True
                        yield sheet
                return
            except (OSError, wire.ProtocolError):
                self.disconnect()
                if delivered or attempt:
                    raise
                print("Verbindung zum Scanner verloren – baue sie neu auf", file=sys.stderr)

    def _scan(
        self, ch: ScsiChannel, settings: ScanSettings, select: bool, whiten: bool, *, resume_polling: bool = False
    ) -> Iterator[Sheet]:
        if select:
            prof_id = self.profile_id
            if prof_id:
                self.ctl.get_users()  # wie ScanSnap Home: 0x0100 direkt vor 0x0610
            else:
                prof = self.ctl.pick_profile(self.ctl.local_ip())
                prof_id = prof["prof_id"]
                print(f"Profil: {prof.get('prof_name')} ({prof.get('user_name')}, {prof_id})", file=sys.stderr)
            self.ctl.select_profile(prof_id)
        if not ch.stop_polling():
            raise ConnectionError("Scanner antwortet nicht auf C2")
        try:
            for sheet in Scanner(ch).scan(settings):
                if self.rotate or self.crop or whiten:

                    def fix(jpeg: bytes) -> bytes:
                        return process(jpeg, settings.dpi, rotate=self.rotate, crop=self.crop, whiten_background=whiten)

                    sheet = Sheet(fix(sheet.front), sheet.back and fix(sheet.back), sheet.info)
                yield sheet
        finally:
            if resume_polling:
                ch.start_polling()  # wie ScanSnap Home: nach dem Scan weiter C2
