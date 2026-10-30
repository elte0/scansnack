"""Framing of the ScanSnap Wi-Fi protocol: ``u32 length | b"ssNR"`` + header.

Layout: docs/protocol.md, section "TCP framing".
"""

from __future__ import annotations

import os
import socket
import struct
import sys

MAGIC = b"ssNR"
CLIENT_HEADER_LEN = 32
SERVER_HEADER_LEN = 16


DEBUG = bool(os.environ.get("SCANSNAP_DEBUG"))


class ProtocolError(Exception):
    pass


def trace(direction: str, frame: bytes, limit: int = 160) -> None:
    if DEBUG:
        h = frame.hex()
        print(f"[{direction}] {len(frame):6d} {h[:limit]}{'…' if len(h) > limit else ''}", file=sys.stderr)


def client_frame(command: int, mac: bytes, body: bytes = b"", extra: bytes = b"\0\0\0\0") -> bytes:
    if len(mac) != 6 or len(extra) != 4:
        raise ValueError("mac must be 6 bytes, extra 4 bytes")
    header = struct.pack(">I4sII6sH4sI", CLIENT_HEADER_LEN + len(body), MAGIC, command, 0, mac, 0, extra, 0)
    return header + body


def server_frame(body: bytes = b"", status: int = 0) -> bytes:
    return struct.pack(">I4sII", SERVER_HEADER_LEN + len(body), MAGIC, 0, status) + body


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(n - len(buf), 1 << 20))
        if not chunk:
            raise ProtocolError(f"connection closed after {len(buf)}/{n} bytes")
        buf += chunk
    return bytes(buf)


def read_frame(sock: socket.socket) -> bytes:
    head = recv_exact(sock, 8)
    length, magic = struct.unpack(">I4s", head)
    if magic != MAGIC:
        raise ProtocolError(f"bad magic {magic!r}")
    if length < 8:
        raise ProtocolError(f"bad frame length {length}")
    frame = head + recv_exact(sock, length - 8)
    trace("<-", frame)
    return frame


def frame_status(frame: bytes) -> int:
    return struct.unpack_from(">I", frame, 12)[0]
