"""Steuerkanal (TCP 53219). Pro Kommando eine eigene TCP-Verbindung.

Kommandos (Header-Feld ``command``):
    0x11  Host registrieren (352-Byte-Binärblock)
    0x12  unbekannt, ohne Body, Zusatzfeld = 1
    0x13  Gerätename abfragen
    0x30  Zustand abfragen (liefert u32)
    0x41  JSON-Aufruf; Body:
              0  u32 BE  Länge des restlichen Bodys
              4  u16 BE  Unterkommando
              6  10 B    0
             16  u32 LE  JSON-Länge
             20  JSON
          Unterkommandos: 0x0100 Hosts/Profile lesen, 0x0110 Profile schreiben,
          0x0210 Host ankündigen, 0x0610 Profil wählen, 0x0810 Scan-Einstellungen
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import socket
import struct
import uuid
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from . import wire

CONTROL_PORT = 53219

SUB_GET_USERS = 0x0100
SUB_SET_PROFILES = 0x0110
SUB_ANNOUNCE = 0x0210
SUB_SELECT_PROFILE = 0x0610
SUB_SCAN_SETTINGS = 0x0810


@dataclass(frozen=True)
class HostIdentity:
    """Wie sich der Host gegenüber dem Scanner ausweist."""

    name: str
    host_id: str          # 16 Hex-Zeichen, erscheint in Discovery und C2-Status
    user_id: str          # 32 Hex-Zeichen (Großbuchstaben)
    mac: bytes            # 6 Byte
    host_port: int = 55265

    @property
    def host_id_bytes(self) -> bytes:
        return bytes.fromhex(self.host_id)


def register_body(identity: HostIdentity, host_ip: str, flag: int = 5, now: dt.datetime | None = None) -> bytes:
    now = now or dt.datetime.now().astimezone()
    bias = -int(now.utcoffset().total_seconds()) if now.utcoffset() else 0
    body = bytearray(352)
    struct.pack_into(">4sII4sHH", body, 0, b"\x01\x00\x1e\x00", flag, 0,
                     ipaddress.IPv4Address(host_ip).packed, 0, identity.host_port)
    struct.pack_into(">HBBBBBB8si", body, 68, now.year, now.month, now.day, now.hour, now.minute,
                     now.second, 0, identity.host_id_bytes, bias)
    return bytes(body)


def _json_body(sub: int, payload: Any | None) -> bytes:
    raw = b"" if payload is None else json.dumps(payload, indent=2, separators=(",", " : ")).encode()
    rest = struct.pack(">H", sub) + b"\0" * 10 + struct.pack("<I", len(raw)) + raw
    return struct.pack(">I", len(rest)) + rest


def _parse_json_response(frame: bytes) -> tuple[int, Any]:
    """Antwort-Layout = Server-Header + JSON-Body-Layout ab Offset 16."""
    if len(frame) < 22:
        raise wire.ProtocolError(f"error frame {frame.hex()}")
    sub = struct.unpack_from(">H", frame, 20)[0]
    if len(frame) < 36:
        return sub, None
    n = struct.unpack_from("<I", frame, 32)[0]
    raw = frame[36:36 + n]
    if not raw.lstrip().startswith((b"{", b"[")):
        return sub, struct.unpack_from(">I", frame, 24)[0]   # Quittung: u32 Status an Offset 24
    return sub, json.loads(raw)


# Längere Benutzernamen lehnt der Scanner bei 0x0110 mit 0xffffffff ab (getestet: 30 ok, 31 nicht).
MAX_NAME_LEN = 30


def _profile_template() -> dict:
    """Profil „ScanSnap Home“ (color/300, Auto-Erkennung) aus einem Scanner-Export, ohne Namen/IDs/IP."""
    return json.loads(files("scansnap").joinpath("profile_template.json").read_text(encoding="utf-8"))

class Control:
    def __init__(self, ip: str, identity: HostIdentity, port: int = CONTROL_PORT, timeout: float = 10.0):
        self.ip, self.port, self.identity, self.timeout = ip, port, identity, timeout

    def _call(self, command: int, body: bytes = b"", extra: bytes = b"\0\0\0\0") -> bytes:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            greeting = wire.read_frame(s)
            if len(greeting) != wire.SERVER_HEADER_LEN:
                raise wire.ProtocolError("unexpected greeting")
            frame = wire.client_frame(command, self.identity.mac, body, extra)
            wire.trace(f"-> ctl {command:#04x}", frame)
            s.sendall(frame)
            return wire.read_frame(s)

    def local_ip(self) -> str:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            return s.getsockname()[0]

    # --- einfache Kommandos -------------------------------------------------

    def device_name(self) -> str:
        frame = self._call(0x13)
        return frame[16:80].split(b"\0", 1)[0].decode("ascii", "replace")

    def state(self) -> int:
        return struct.unpack_from(">I", self._call(0x30), 16)[0]

    def cmd12(self) -> None:
        # Antwortet nicht immer (im Pairing-Capture einmal ohne Antwort) – daher tolerant.
        try:
            with socket.create_connection((self.ip, self.port), timeout=2.0) as s:
                wire.read_frame(s)
                s.sendall(wire.client_frame(0x12, self.identity.mac, extra=struct.pack(">I", 1)))
                wire.read_frame(s)
        except (TimeoutError, wire.ProtocolError):
            pass

    def register(self, host_ip: str, flag: int = 5, now: dt.datetime | None = None) -> int:
        frame = self._call(0x11, register_body(self.identity, host_ip, flag, now))
        return struct.unpack_from(">I", frame, 12)[0]

    # --- JSON-Kommandos -----------------------------------------------------

    def json_call(self, sub: int, payload: Any | None = None) -> Any:
        rsub, result = _parse_json_response(self._call(0x41, _json_body(sub, payload)))
        if rsub != sub:
            raise wire.ProtocolError(f"sub-command mismatch {rsub:#06x} != {sub:#06x}")
        if isinstance(result, int) and result:
            raise wire.ProtocolError(f"sub-command {sub:#06x} rejected, status {result:#010x}")
        return result

    def get_users(self) -> dict:
        return self.json_call(SUB_GET_USERS)

    def announce(self, host_ip: str) -> Any:
        return self.json_call(SUB_ANNOUNCE, {
            "host_address": host_ip,
            "function_level": 2,
            "current_user_id": self.identity.user_id,
            "host_port": self.identity.host_port,
            "version": 1,
        })

    def claim(self, host_ip: str) -> dict:
        """Trägt diesen Host als Benutzer mit eigenem Profil beim Scanner ein.

        Nur im Pairing-Zustand (0x11 mit Flag 5) möglich, sonst antwortet der Scanner
        mit ``fffffffe``. Andere Benutzer und Profile bleiben erhalten; die Auswahl
        (``select``) lässt sich so nicht umstellen (0xffffffff) – das geht nur am
        Touchpanel. Beim Benutzerwechsel am Panel verwirft der Scanner alle Profile;
        danach ``claim`` erneut ausführen. Zum Schluss wird das Pairing wie im
        Pairing-Capture mit 0x30 → 0x12 → 0x11 (Flag 0) abgeschlossen.
        """
        current = self.get_users()
        name = self.identity.name[:MAX_NAME_LEN]
        color_id = 2
        me = {"name": name, "app_ver": 1, "prof_stat": 1, "scloud_user": "",
              "host_id": self.identity.host_id, "icon_id": 9, "color_id": color_id}
        # Eigenen Eintrag an seiner Stelle ersetzen: eine umsortierte Liste lehnt der Scanner ab.
        users = [me if u.get("host_id") == self.identity.host_id else u for u in current["conn_user"]["users"]]
        if me not in users:
            users.append(me)
        profiles = current.get("profiles", [])
        previous = [p for p in profiles if p.get("user_id") == self.identity.user_id]
        others = [p for p in profiles if p.get("user_id") != self.identity.user_id]
        own = dict(_profile_template(),
                   prof_id=previous[0]["prof_id"] if previous else uuid.uuid4().hex.upper(),
                   user_name=name, user_id=self.identity.user_id, color_id=color_id, host_address=host_ip,
                   disp_order=max((p.get("disp_order", 0) for p in others), default=0) + 1)
        payload = {"conn_user": {"users": users, "select": current["conn_user"]["select"]},
                   "version": current.get("version", 3), "profiles": others + [own]}
        try:
            self.json_call(SUB_SET_PROFILES, payload)
            # 0x0610 geht im Pairing-Zustand nicht (fffffffe); das Profil wird vor jedem Scan gewählt.
            self.announce(host_ip)
        finally:
            self.finish_pairing(host_ip)
        return payload

    def finish_pairing(self, host_ip: str) -> int:
        """Abschluss wie im Pairing-Capture. Ohne ihn bleibt der Scanner im Pairing-Zustand
        (C2 e1), 0x11 mit Flag 0 liefert dann fffffffc und 0x0610 fffffffe."""
        self.state()
        self.cmd12()
        return self.register(host_ip, flag=0)

    def select_profile(self, prof_id: str) -> None:
        """Wählt ein Profil; ScanSnap Home sendet das unmittelbar vor jedem Scan (vor D5)."""
        self.json_call(SUB_SELECT_PROFILE, {"version": 1, "prof_id": prof_id})

    def pick_profile(self, host_ip: str) -> dict:
        profiles = self.get_users().get("profiles", [])
        if not profiles:
            raise RuntimeError("Scanner hat keine Profile.")
        own = [p for p in profiles if p.get("user_id") == self.identity.user_id]
        same_ip = [p for p in profiles if p.get("host_address") == host_ip]
        return (own or same_ip or profiles)[0]

    def handshake(self) -> str:
        """Ablauf wie ScanSnap Home beim Koppeln. Liefert die lokale IP."""
        host_ip = self.local_ip()
        self.device_name()
        self.get_users()
        self.state()
        self.cmd12()
        self.register(host_ip)
        self.announce(host_ip)
        return host_ip

    def connect(self) -> str:
        """Ablauf wie ScanSnap Home beim App-Start mit bereits gekoppeltem Scanner (Kalt-Capture).

        Anders als beim Koppeln: kein 0x30/0x12, 0x0210 vor 0x11 und 0x11 mit Flag 0.
        Danach baut ScanSnap Home den Scan-Kanal neu auf (siehe ``cli``).
        """
        host_ip = self.local_ip()
        self.device_name()
        self.get_users()
        self.announce(host_ip)
        self.register(host_ip, flag=0)
        return host_ip
