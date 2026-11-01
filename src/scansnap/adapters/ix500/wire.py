"""The shared framing (``scansnap_wifi.wire``) with the iX500 magic ``VENS``."""

from __future__ import annotations

import socket

from ..scansnap_wifi import wire as _wire
from ..scansnap_wifi.wire import MAX_FRAME, SERVER_HEADER_LEN, ProtocolError, frame_status, trace

__all__ = [
    "MAGIC",
    "MAX_FRAME",
    "SERVER_HEADER_LEN",
    "ProtocolError",
    "client_frame",
    "frame_status",
    "read_frame",
    "server_frame",
    "trace",
]

MAGIC = _wire.VENS


def client_frame(command: int, mac: bytes, body: bytes = b"") -> bytes:
    return _wire.client_frame(command, mac, body, magic=MAGIC)


def server_frame(body: bytes = b"", status: int = 0) -> bytes:
    return _wire.server_frame(body, status, magic=MAGIC)


def read_frame(sock: socket.socket) -> bytes:
    return _wire.read_frame(sock, magic=MAGIC)
