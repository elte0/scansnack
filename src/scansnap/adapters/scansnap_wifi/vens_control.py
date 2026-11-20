"""VENS registration and control framing shared by the iX500 and iX1300."""

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


def exchange(ip: str, port: int, mac: bytes, command: int, body: bytes, timeout: float) -> bytes:
    """Use a separate TCP connection for each VENS control command."""
    with socket.create_connection((ip, port), timeout=timeout) as s:
        greeting = wire.read_frame(s, magic=wire.VENS)
        if len(greeting) != wire.SERVER_HEADER_LEN:
            raise wire.ProtocolError("unexpected greeting")
        frame = wire.client_frame(command, mac, body, magic=wire.VENS)
        wire.trace(f"-> ctl {command:#04x}", frame)
        s.sendall(frame)
        return wire.read_frame(s, magic=wire.VENS)


def registration_status(frame: bytes) -> int:
    if len(frame) < 20:
        raise wire.ProtocolError(f"short registration reply {frame.hex()}")
    return struct.unpack_from(">i", frame, 8)[0]
