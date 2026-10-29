"""Scan-Ablauf auf dem SCSI-Kanal, nachgebaut aus den ScanSnap-Home-Captures.

    D5  Session öffnen          (8 B raus, 8 B rein)
    D8  Start
    E9  Fenster setzen          (32 B: dpi, Breite/Höhe in 1/1200 in)
    D4  Modus setzen            (80 B: Farbmodus, dpi, Qualität)
    03  Request Sense
    pro Blatt:
      E0  Blatt einziehen       → CHECK CONDITION + Sense 3/80/03 = kein Papier
      28 00 00 02 …            JPEG-Chunks, Vorder-/Rückseite abwechselnd,
                                CHECK CONDITION + EOM = Seite fertig
      28 00 80 …               Bildinfo (Breite, Höhe, dpi)
    D6  Session freigeben

Der Scanner liest immer beide Seiten; Simplex verwirft die Rückseite.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from .scsi import CHECK_CONDITION, ScsiChannel, ScsiError, Sense

Mode = Literal["gray", "color"]

# Maße wie von ScanSnap Home gesendet (1/1200 Zoll)
WIDTH_1200 = 0x28D0
HEIGHT_1200 = 0x44DC

# Kombinationen, deren Parameterblöcke 1:1 aus Captures stammen
VERIFIED: dict[tuple[str, int], int] = {("gray", 150): 0x300000, ("color", 600): 0x20000}

_MODE_TEMPLATE = bytes.fromhex(
    "00030101d000c1808080808080000000000000000000000000000000000000300010"
    "009600960581050000"
    "0028d0000044dc04"
).ljust(80, b"\0")


@dataclass(frozen=True)
class Settings:
    mode: Mode = "gray"
    dpi: int = 150
    duplex: bool = False

    @property
    def verified(self) -> bool:
        return (self.mode, self.dpi) in VERIFIED

    @property
    def read_len(self) -> int:
        return VERIFIED.get((self.mode, self.dpi), 0x20000)

    def window_block(self) -> bytes:
        return (struct.pack(">HHII", self.dpi, self.dpi, WIDTH_1200, HEIGHT_1200) + b"\x05").ljust(32, b"\0")

    def mode_block(self) -> bytes:
        b = bytearray(_MODE_TEMPLATE)
        b[13] = 0x80 if self.mode == "color" else 0x00
        struct.pack_into(">HH", b, 34, self.dpi, self.dpi)
        b[40] = 0x0A if self.mode == "color" else 0x05
        return bytes(b)


@dataclass(frozen=True)
class ImageInfo:
    width: int
    height_max: int
    height: int
    dpi_x: int
    dpi_y: int

    @classmethod
    def parse(cls, d: bytes) -> ImageInfo:
        w, hmax, _w2, h, dx, dy = struct.unpack_from(">IIIIHH", d)
        return cls(w, hmax, h, dx, dy)


@dataclass
class Page:
    front: bytes
    back: bytes | None
    info: ImageInfo | None


class NoPaper(ScsiError):
    pass


class FeedError(ScsiError):
    """Papierfehler beim Einzug oder Lesen (Stau, Doppeleinzug, Abdeckung offen …); ``reason`` ist Klartext."""

    def __init__(self, sense: Sense):
        self.reason = sense.medium_error or "medium error"
        super().__init__(self.reason, sense)


def _sense_error(msg: str, sense: Sense) -> ScsiError:
    return FeedError(sense) if sense.medium_error else ScsiError(msg, sense)


def _read_cdb(side: int, chunk: int, length: int) -> bytes:
    return bytes([0x28, 0, 0, 2, 0, 0x80 if side else 0]) + length.to_bytes(3, "big") + bytes([0, side, chunk])


def _info_cdb(side: int) -> bytes:
    return bytes([0x28, 0, 0x80, 0, 0, 0x80 if side else 0, 0, 0, 0x20, 0, side, 0])


class Scanner:
    def __init__(self, channel: ScsiChannel):
        self.ch = channel

    def _read_page(self, read_len: int) -> tuple[list[bytes], int]:
        parts: list[list[bytes]] = [[], []]
        done = [False, False]
        chunk = [0, 0]
        last = 0
        while not all(done):
            for side in (0, 1):
                if done[side]:
                    continue
                r = self.ch.command(_read_cdb(side, chunk[side], read_len), in_len=read_len)
                if r.data[:2] != bytes([side, chunk[side]]):
                    raise ScsiError(f"unexpected chunk header {r.data[:2].hex()} for side {side}")
                parts[side].append(r.data[2:])
                chunk[side] += 1
                if r.status == CHECK_CONDITION:
                    sense = self.ch.request_sense()
                    if not sense.eom:
                        raise _sense_error("read failed", sense)
                    done[side], last = True, side
                elif r.status:
                    raise ScsiError(f"read status {r.status}")
        return [b"".join(p) for p in parts], last

    def scan(self, settings: Settings) -> Iterator[Page]:
        """Scannt alle Blätter im Einzug. Wirft NoPaper, wenn der Einzug leer ist."""
        ch = self.ch
        ch.checked(bytes.fromhex("d50000010808"), out=b"\0" * 8, in_len=8)
        try:
            ch.checked(bytes.fromhex("d80000000000"))
            ch.checked(bytes.fromhex("e9000000000000200000"), out=settings.window_block())
            ch.checked(bytes.fromhex("d40000005000"), out=settings.mode_block())
            ch.request_sense()
            pages = 0
            while True:
                r = ch.command(bytes.fromhex("e00000000000"))
                if r.status == CHECK_CONDITION:
                    sense = ch.request_sense()
                    if sense.no_paper:
                        if pages == 0:
                            raise NoPaper("no paper in feeder", sense)
                        return
                    raise _sense_error("feed failed", sense)
                (front, back), last = self._read_page(settings.read_len)
                info = ImageInfo.parse(ch.checked(_info_cdb(last), in_len=0x20).data)
                pages += 1
                yield Page(front, back if settings.duplex else None, info)
        finally:
            ch.command(bytes.fromhex("d60000000000"))
