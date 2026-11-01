"""TCP framing of the ScanSnap Wi-Fi protocol: ``u32 length | magic`` + header.

Layout: docs/protocol-ix1500.md, section "TCP framing". The iX1500 uses ``ssNR``, the iX500 ``VENS``.
"""

from __future__ import annotations

import os
import socket
import struct
import sys

SSNR = b"ssNR"
VENS = b"VENS"
CONTROL_PORT = 53219
CLIENT_HEADER_LEN = 32
SERVER_HEADER_LEN = 16
# Largest frame the client accepts: one image read (0x300000) plus headers, with headroom.
MAX_FRAME = 0x400000

DEBUG = bool(os.environ.get("SCANSNAP_DEBUG"))


class ProtocolError(Exception):
    pass


def trace(direction: str, frame: bytes, limit: int = 160) -> None:
    if DEBUG:
        h = frame.hex()
        print(f"[{direction}] {len(frame):6d} {h[:limit]}{'…' if len(h) > limit else ''}", file=sys.stderr)


def client_frame(command: int, mac: bytes, body: bytes = b"", extra: bytes = b"\0\0\0\0", *, magic: bytes) -> bytes:
    if len(mac) != 6 or len(extra) != 4:
        raise ValueError("mac must be 6 bytes, extra 4 bytes")
    header = struct.pack(">I4sII6sH4sI", CLIENT_HEADER_LEN + len(body), magic, command, 0, mac, 0, extra, 0)
    return header + body


def server_frame(body: bytes = b"", status: int = 0, *, magic: bytes) -> bytes:
    return struct.pack(">I4sII", SERVER_HEADER_LEN + len(body), magic, 0, status) + body


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ProtocolError(f"connection closed after {len(buf)}/{n} bytes")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket, *, magic: bytes) -> bytes:
    head = recv_exact(sock, 8)
    length, got = struct.unpack(">I4s", head)
    if got != magic:
        raise ProtocolError(f"bad magic {got!r}")
    if not SERVER_HEADER_LEN <= length <= MAX_FRAME:
        raise ProtocolError(f"bad frame length {length}")
    frame = head + recv_exact(sock, length - 8)
    trace("<-", frame)
    return frame


def frame_status(frame: bytes) -> int:
    return struct.unpack_from(">I", frame, 12)[0]


def greeting_magic(ip: str, port: int = CONTROL_PORT, timeout: float = 2.0) -> bytes:
    """Magic of the 16-byte greeting every connection to ``port`` gets; tells the models apart without UDP.

    Returns fewer than 4 bytes if the peer closes early. Raises OSError if nothing answers.
    """
    with socket.create_connection((ip, port), timeout=timeout) as s:
        head = b""
        while len(head) < 8:
            chunk = s.recv(8 - len(head))
            if not chunk:
                break
            head += chunk
    return head[4:8]
