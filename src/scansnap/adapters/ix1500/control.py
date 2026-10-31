"""Control channel (TCP 53219). One TCP connection per command.

Commands, JSON layout and status values: docs/protocol.md, section "Control channel (TCP 53219)".
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
    """How the host identifies itself to the scanner."""

    name: str
    host_id: str  # 16 hex characters, appears in discovery and C2 status
    user_id: str  # 32 hex characters (upper case)
    mac: bytes  # 6 bytes
    host_port: int = 55265
    pairing_key: str = ""  # iX500 only: key from ``scansnap capture-key``

    @property
    def host_id_bytes(self) -> bytes:
        return bytes.fromhex(self.host_id)


def register_body(identity: HostIdentity, host_ip: str, flag: int = 5, now: dt.datetime | None = None) -> bytes:
    now = now or dt.datetime.now().astimezone()
    offset = now.utcoffset()
    bias = -int(offset.total_seconds()) if offset else 0
    body = bytearray(352)
    struct.pack_into(
        ">4sII4sHH", body, 0, b"\x01\x00\x1e\x00", flag, 0, ipaddress.IPv4Address(host_ip).packed, 0, identity.host_port
    )
    struct.pack_into(
        ">HBBBBBB8si",
        body,
        68,
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        0,
        identity.host_id_bytes,
        bias,
    )
    return bytes(body)


def json_body(sub: int, payload: Any | None) -> bytes:
    raw = b"" if payload is None else json.dumps(payload, indent=2, separators=(",", " : ")).encode()
    rest = struct.pack(">H", sub) + b"\0" * 10 + struct.pack("<I", len(raw)) + raw
    return struct.pack(">I", len(rest)) + rest


def parse_json_response(frame: bytes) -> tuple[int, Any]:
    """Response layout = server header + JSON body layout from offset 16."""
    if len(frame) < 22:
        raise wire.ProtocolError(f"error frame {frame.hex()}")
    sub = struct.unpack_from(">H", frame, 20)[0]
    if len(frame) < 36:
        return sub, None
    n = struct.unpack_from("<I", frame, 32)[0]
    raw = frame[36 : 36 + n]
    if not raw.lstrip().startswith((b"{", b"[")):
        return sub, struct.unpack_from(">I", frame, 24)[0]  # acknowledgement: u32 status at offset 24
    return sub, json.loads(raw)


# The scanner rejects longer user names in 0x0110 with 0xffffffff (tested: 30 ok, 31 not).
MAX_NAME_LEN = 30


def profile_template() -> dict[str, Any]:
    """Profile "ScanSnap Home" (color/300, auto detection) from a scanner export, without names/IDs/IP."""
    data = files("scansnap.adapters.ix1500").joinpath("profile_template.json").read_text(encoding="utf-8")
    return json.loads(data)


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

    # --- simple commands ----------------------------------------------------

    def device_name(self) -> str:
        frame = self._call(0x13)
        return frame[16:80].split(b"\0", 1)[0].decode("ascii", "replace")

    def state(self) -> int:
        # UNVERIFIED: meaning of the value unknown.
        return struct.unpack_from(">I", self._call(0x30), 16)[0]

    def cmd12(self) -> None:
        # UNVERIFIED: meaning of 0x12 unknown, only sent as in the pairing capture.
        # Does not always answer (once without an answer in the pairing capture) – hence tolerant.
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

    # --- JSON commands ------------------------------------------------------

    def json_call(self, sub: int, payload: Any | None = None) -> Any:
        rsub, result = parse_json_response(self._call(0x41, json_body(sub, payload)))
        if rsub != sub:
            raise wire.ProtocolError(f"sub-command mismatch {rsub:#06x} != {sub:#06x}")
        if isinstance(result, int) and result:
            raise wire.ProtocolError(f"sub-command {sub:#06x} rejected, status {result:#010x}")
        return result

    def get_users(self) -> dict[str, Any]:
        return self.json_call(SUB_GET_USERS)

    def announce(self, host_ip: str) -> Any:
        return self.json_call(
            SUB_ANNOUNCE,
            {
                "host_address": host_ip,
                "function_level": 2,
                "current_user_id": self.identity.user_id,
                "host_port": self.identity.host_port,
                "version": 1,
            },
        )

    def claim(self, host_ip: str) -> dict[str, Any]:
        """Registers this host as a user with its own profile on the scanner.

        Only possible in the pairing state (0x11 with flag 5), otherwise the scanner replies
        with ``fffffffe``. Other users and profiles are kept; the selection
        (``select``) cannot be changed this way (0xffffffff) – that only works on the
        touch panel. After a user switch on the panel, 0x0100 only returns the profiles of the
        selected user; then run ``claim`` again. Finally, pairing is completed as in the
        pairing capture with 0x30 → 0x12 → 0x11 (flag 0).
        """
        current = self.get_users()
        name = self.identity.name[:MAX_NAME_LEN]
        color_id = 2
        me = {
            "name": name,
            "app_ver": 1,
            "prof_stat": 1,
            "scloud_user": "",
            "host_id": self.identity.host_id,
            "icon_id": 9,
            "color_id": color_id,
        }
        # Replace our own entry in place: the scanner rejects a reordered list.
        users = [me if u.get("host_id") == self.identity.host_id else u for u in current["conn_user"]["users"]]
        if me not in users:
            users.append(me)
        profiles = current.get("profiles", [])
        previous = [p for p in profiles if p.get("user_id") == self.identity.user_id]
        others = [p for p in profiles if p.get("user_id") != self.identity.user_id]
        own = dict(
            profile_template(),
            prof_id=previous[0]["prof_id"] if previous else uuid.uuid4().hex.upper(),
            user_name=name,
            user_id=self.identity.user_id,
            color_id=color_id,
            host_address=host_ip,
            disp_order=max((p.get("disp_order", 0) for p in others), default=0) + 1,
        )
        payload = {
            "conn_user": {"users": users, "select": current["conn_user"]["select"]},
            "version": current.get("version", 3),
            "profiles": [*others, own],
        }
        try:
            self.json_call(SUB_SET_PROFILES, payload)
            # 0x0610 does not work in the pairing state (fffffffe); the profile is selected before every scan.
            self.announce(host_ip)
        finally:
            self.finish_pairing(host_ip)
        return payload

    def finish_pairing(self, host_ip: str) -> int:
        """Completion as in the pairing capture. Without it the scanner stays in the pairing state
        (C2 e1), 0x11 with flag 0 then returns fffffffc and 0x0610 fffffffe."""
        self.state()
        self.cmd12()
        return self.register(host_ip, flag=0)

    def select_profile(self, prof_id: str) -> None:
        """Selects a profile; ScanSnap Home sends this immediately before every scan (before D5)."""
        self.json_call(SUB_SELECT_PROFILE, {"version": 1, "prof_id": prof_id})

    def pick_profile(self, host_ip: str) -> dict[str, Any]:
        profiles = self.get_users().get("profiles", [])
        if not profiles:
            raise RuntimeError("Scanner has no profiles.")
        own = [p for p in profiles if p.get("user_id") == self.identity.user_id]
        same_ip = [p for p in profiles if p.get("host_address") == host_ip]
        return (own or same_ip or profiles)[0]

    def handshake(self) -> str:
        """Sequence like ScanSnap Home when pairing. Returns the local IP."""
        host_ip = self.local_ip()
        self.device_name()
        self.get_users()
        self.state()
        self.cmd12()
        self.register(host_ip)
        self.announce(host_ip)
        return host_ip

    def connect(self) -> str:
        """Sequence like ScanSnap Home at app start with an already paired scanner (cold capture).

        Unlike pairing: no 0x30/0x12, 0x0210 before 0x11, and 0x11 with flag 0.
        Afterwards ScanSnap Home opens the scan channel anew (see ``cli``).
        """
        host_ip = self.local_ip()
        self.device_name()
        self.get_users()
        self.announce(host_ip)
        self.register(host_ip, flag=0)
        return host_ip
