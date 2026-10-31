"""Scan channel (TCP 53218): SCSI commands tunnelled over a TCP connection.

Request/response layout: docs/protocol.md, section "Scan channel (TCP 53218, SCSI tunnel)".
"""

from __future__ import annotations

import socket
import struct
import threading
from dataclasses import dataclass
from typing import Self

from . import wire

DATA_PORT = 53218
CHECK_CONDITION = 2
RESPONSE_HEADER_LEN = 40  # server header + 24 B, then the data


class ScsiError(Exception):
    def __init__(self, msg: str, sense: Sense | None = None):
        super().__init__(msg if sense is None else f"{msg}: {sense}")
        self.sense = sense


# Sense 3/80/xx (medium error); confirmed on both models: 01 (paper jam), 03 (feeder empty).
# UNVERIFIED: the other texts follow SANE fujitsu.c.
MEDIUM_ERRORS = {
    0x01: "paper jam",
    0x02: "cover open",
    0x03: "no paper",
    0x07: "double feed",
}


@dataclass(frozen=True)
class Sense:
    key: int
    asc: int
    ascq: int
    eom: bool
    info: int  # for short reads: residual length (requested − delivered)

    @classmethod
    def parse(cls, data: bytes) -> Sense:
        if len(data) < 14:
            raise wire.ProtocolError(f"short sense data {data.hex()}")
        return cls(
            key=data[2] & 0x0F,
            asc=data[12],
            ascq=data[13],
            eom=bool(data[2] & 0x40),
            info=struct.unpack_from(">I", data, 3)[0],
        )

    @property
    def no_paper(self) -> bool:
        return (self.key, self.asc, self.ascq) == (3, 0x80, 0x03)

    @property
    def medium_error(self) -> str | None:
        """Feed/paper error (sense 3/80/xx) as plain text, otherwise None."""
        if self.key == 3 and self.asc == 0x80:
            return MEDIUM_ERRORS.get(self.ascq, f"medium error {self.ascq:#04x}")
        return None

    def __str__(self) -> str:
        return f"sense key={self.key:#x} asc={self.asc:#04x} ascq={self.ascq:#04x} eom={self.eom}"


@dataclass(frozen=True)
class Response:
    status: int
    data: bytes


class ScsiChannel:
    """One connection to 53218. ``extra`` is the 4-byte header field (the iX1500 sends the host IP there)."""

    def __init__(
        self,
        ip: str,
        mac: bytes,
        *,
        magic: bytes,
        port: int = DATA_PORT,
        timeout: float = 60.0,
        extra: bytes = b"\0\0\0\0",
    ):
        self.mac, self.magic, self.extra = mac, magic, extra
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        try:
            greeting = wire.read_frame(self.sock, magic=magic)
            if len(greeting) != wire.SERVER_HEADER_LEN:
                raise wire.ProtocolError("unexpected greeting")
        except BaseException:
            self.sock.close()  # the scanner serves one scan-channel connection at a time
            raise
        self._lock = threading.Lock()

    def close(self) -> None:
        self.sock.close()

    def interrupt(self) -> None:
        """Wakes a request blocked in another thread (it then fails with an OSError)."""
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        if not 6 <= len(cdb) <= 16:
            raise ValueError("CDB must be 6 to 16 bytes")
        body = struct.pack(">IIII", len(cdb), in_len, len(out), 0) + cdb.ljust(16, b"\0") + out
        frame = wire.client_frame(1, self.mac, body, self.extra, magic=self.magic)
        with self._lock:
            wire.trace(f"-> scsi {cdb.hex()}", frame)
            self.sock.sendall(frame)
            reply = wire.read_frame(self.sock, magic=self.magic)
        if len(reply) < RESPONSE_HEADER_LEN:
            raise wire.ProtocolError(f"short SCSI reply {reply.hex()}")
        data = reply[RESPONSE_HEADER_LEN:]
        if len(data) > max(in_len, 0):
            raise wire.ProtocolError(f"{len(data)} bytes for a read of {in_len}")
        return Response(wire.frame_status(reply), data)

    def request_sense(self) -> Sense:
        return Sense.parse(self.command(bytes([0x03, 0, 0, 0, 0x12, 0]), in_len=0x12).data)

    def checked(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        r = self.command(cdb, out, in_len)
        if r.status == CHECK_CONDITION:
            raise ScsiError(f"command {cdb[0]:#04x} failed", self.request_sense())
        if r.status:
            raise ScsiError(f"command {cdb[0]:#04x} status {r.status}")
        return r

    def hardware_status(self, length: int) -> bytes:
        """C2 status (iX1500: 0x30 B, iX500: 0x20 B). Byte 3 bit 7: feeder empty (both models); byte 4 bit 0:
        scan button (iX1500, UNVERIFIED on the iX500)."""
        cdb = bytes([0xC2, 0, 0, 0, 0, 0, 0, 0, length, 0])
        data = self.checked(cdb, in_len=length).data
        if len(data) < 8:
            raise wire.ProtocolError(f"short C2 status {data.hex()}")
        return data

    def inquiry(self) -> tuple[str, str, str]:
        """Vendor, product, firmware revision."""
        d = self.checked(bytes([0x12, 0, 0, 0, 0x60, 0]), in_len=0x60).data
        if len(d) < 36:
            raise wire.ProtocolError(f"short INQUIRY data {d.hex()}")
        return d[8:16].decode().strip(), d[16:32].decode().strip(), d[32:36].decode().strip()
