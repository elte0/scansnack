"""Simulated iX1500 based on the fixtures.

Control commands (D5, E9, D4 …) are answered with the recorded replies and logged
so that tests can check the parameter blocks that were sent.
The mock simulates image data: synthetic JPEGs in the recorded dimensions,
split into chunks of the length the client requests, with an EOM sense at the end of the page.
"""

from __future__ import annotations

import io
import json
import socket
import struct
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from scansnap.adapters.ix1500 import wire

FIXTURES = Path(__file__).parent / "fixtures" / "captures"


def synthetic_jpeg(width: int, height: int, dpi: int, label: str) -> bytes:
    im = Image.new("RGB", (width, height), "white")
    ImageDraw.Draw(im).text((20, 20), label, fill="black")
    buf = io.BytesIO()
    im.save(buf, "JPEG", dpi=(dpi, dpi), quality=50)
    return buf.getvalue()


def sense(key: int = 0, asc: int = 0, ascq: int = 0, eom: bool = False, info: int = 0) -> bytes:
    d = bytearray(18)
    d[0], d[2], d[7], d[12], d[13] = 0xF0, (0x40 | 0x20 if eom else 0) | key, 0x0A, asc, ascq
    struct.pack_into(">I", d, 3, info)
    return bytes(d)


class MockScanner:
    def __init__(self, fixture: str, sheets: int = 1):
        fx = json.loads((FIXTURES / f"{fixture}.json").read_text())
        self.recorded = {bytes.fromhex(e["cdb"])[0]: e for e in fx["exchanges"] if e["cdb"][:2] != "28"}
        self.info = next(bytes.fromhex(e["data"]) for e in fx["exchanges"] if e["cdb"][:6] == "280080")
        dims = {s["side"]: s for s in fx["pages"][0]["sides"]}
        self.sheets = [
            {
                side: synthetic_jpeg(d["width"], d["height"], d["dpi"], f"sheet {n} side {side}")
                for side, d in dims.items()
            }
            for n in range(sheets)
        ]
        self.received: list[tuple[bytes, bytes]] = []
        self._sheet = -1
        self._pos = {0: 0, 1: 0}
        self._sense = sense()
        self.jam_at: int | None = None  # sheet number at which E0 reports a paper jam (3/80/01)
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def close(self) -> None:
        self.srv.close()

    def refill(self) -> None:
        """Put the same sheets back into the feeder."""
        self._sheet = -1

    def _serve(self) -> None:
        try:
            conn, _ = self.srv.accept()
        except OSError:
            return
        with conn:
            conn.sendall(wire.server_frame())
            while True:
                try:
                    req = wire.read_frame(conn)
                except wire.ProtocolError:
                    return
                cdb_len, in_len, out_len = struct.unpack_from(">III", req, 32)
                cdb, out = req[48 : 48 + cdb_len], req[64:]
                if out_len != len(out):
                    raise AssertionError(f"out length field {out_len} != {len(out)} for {cdb.hex()}")
                self.received.append((cdb, out))
                status, data = self._handle(cdb, in_len)
                conn.sendall(wire.server_frame(b"\0" * 24 + data, status))

    def _handle(self, cdb: bytes, in_len: int) -> tuple[int, bytes]:
        op = cdb[0]
        if op == 0x03:
            s, self._sense = self._sense, sense()
            return 0, s
        if op == 0xE0:
            self._sheet += 1
            self._pos = {0: 0, 1: 0}
            if self._sheet == self.jam_at:
                self._sense = sense(3, 0x80, 0x01)
                return 2, b""
            if self._sheet >= len(self.sheets):
                self._sense = sense(3, 0x80, 0x03)
                return 2, b""
            return 0, b""
        if op == 0x28 and cdb[2] == 0x80:
            return 0, self.info
        if op == 0x28:
            side, chunk = cdb[10], cdb[11]
            img = self.sheets[self._sheet][side]
            room = in_len - 2
            part = img[self._pos[side] : self._pos[side] + room]
            self._pos[side] += len(part)
            payload = bytes([side, chunk]) + part
            if self._pos[side] >= len(img):
                self._sense = sense(eom=True, info=in_len - len(payload))
                return 2, payload
            return 0, payload
        rec = self.recorded.get(op)
        return (rec["status"], bytes.fromhex(rec["data"])) if rec else (0, b"")
