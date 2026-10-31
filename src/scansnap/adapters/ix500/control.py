"""Control channel (TCP 53219): registration (0x11), device name (0x13), Wi-Fi status (0x30).

One TCP connection per command. Layout: docs/protocol-ix500.md, section "Startup / registration".
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import socket
import struct
from dataclasses import dataclass

from . import wire

CONTROL_PORT = 53219
REGISTER_BODY_LEN = 96
SCANNER_TIMEOUT = 0x1E  # sent by ScanSnap Home in every registration
# Key of ScanSnap Home's password encoding (SsSvcPCWIFITelReserveScanner codePFUCipher:)
PASSWORD_KEY = "pFusCANsNapFiPfu"


# Seen live; UNVERIFIED: the other negative statuses and the one for a wrong password.
REGISTRATION_STATUS = {
    -2: "the scanner wants its Wi-Fi password",
    -7: "this host ID is not paired with the scanner (pairing is done over USB)",
}


class RegistrationError(Exception):
    def __init__(self, status: int):
        reason = REGISTRATION_STATUS.get(status, "unknown reason")
        super().__init__(f"registration rejected, status {status}: {reason}")
        self.status = status


@dataclass(frozen=True)
class HostIdentity:
    """How the host identifies itself to the scanner."""

    host_id: bytes  # 8 bytes, the entry in the scanner's host table
    mac: bytes  # 6 bytes, sent in every frame header
    host_port: int = 55265  # button and power-off notifications
    password: str | None = None  # scanner Wi-Fi password if it is not the factory default


def encode_password(password: str) -> str:
    """ScanSnap Home's password encoding: three decimal digits per character."""
    if not 0 < len(password) <= len(PASSWORD_KEY) or not password.isascii():
        raise ValueError(f"password must be 1 to {len(PASSWORD_KEY)} ASCII characters")
    return "".join(f"{ord(k) + ord(c) + 11:03d}" for k, c in zip(PASSWORD_KEY, password, strict=False))


def default_password(serial: str) -> str:
    """Factory Wi-Fi password: the last four digits of the serial number."""
    return serial[-4:]


def register_body(
    identity: HostIdentity,
    host_ip: str,
    if_version: int,
    password: str | None,
    now: dt.datetime | None = None,
) -> bytes:
    now = now or dt.datetime.now().astimezone()
    offset = now.utcoffset()
    bias = -int(offset.total_seconds()) if offset else 0
    body = bytearray(REGISTER_BODY_LEN)
    struct.pack_into(">HBxII", body, 0, if_version, SCANNER_TIMEOUT, 0, 1 if password else 0)
    body[12:16] = ipaddress.IPv4Address(host_ip).packed
    struct.pack_into(">I", body, 16, identity.host_port)
    if password:
        code = encode_password(password).encode()
        body[20 : 20 + len(code)] = code
    struct.pack_into(">HBBBBB", body, 68, now.year, now.month, now.day, now.hour, now.minute, now.second)
    body[76:84] = identity.host_id
    if if_version > 3:  # ScanSnap Home only sends the time zone from interface version 4 on
        struct.pack_into(">i", body, 84, bias)
    return bytes(body)


class Control:
    def __init__(self, ip: str, identity: HostIdentity, port: int = CONTROL_PORT, timeout: float = 10.0):
        self.ip, self.port, self.identity, self.timeout = ip, port, identity, timeout

    def _call(self, command: int, body: bytes = b"") -> bytes:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            greeting = wire.read_frame(s)
            if len(greeting) != wire.SERVER_HEADER_LEN:
                raise wire.ProtocolError("unexpected greeting")
            frame = wire.client_frame(command, self.identity.mac, body)
            wire.trace(f"-> ctl {command:#04x}", frame)
            s.sendall(frame)
            return wire.read_frame(s)

    def local_ip(self) -> str:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            return s.getsockname()[0]

    def register(self, host_ip: str, if_version: int, password: str | None, now: dt.datetime | None = None) -> None:
        """0x11: makes this host the target of button notifications. Raises RegistrationError if rejected."""
        frame = self._call(0x11, register_body(self.identity, host_ip, if_version, password, now))
        if len(frame) < 20:
            raise wire.ProtocolError(f"short registration reply {frame.hex()}")
        status = struct.unpack_from(">i", frame, 8)[0]
        if status != 0:
            raise RegistrationError(status)

    def device_name(self) -> str:
        frame = self._call(0x13)
        return frame[16:80].split(b"\0", 1)[0].decode("ascii", "replace")

    def wifi_status(self) -> int:
        # UNVERIFIED: 3 observed with the scanner on AC power and connected.
        return struct.unpack_from(">I", self._call(0x30), 16)[0]
