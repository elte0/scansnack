"""Scan sequence D5, D8, E9, D4, 03, (E0, 28 reads, 28 info)*, D6 on the scan channel.

Flow and parameter blocks: docs/protocol.md, section "Scan sequence".
"""

from __future__ import annotations

import struct
from collections.abc import Iterator

from ...core.errors import FeedError, NoPaper
from ...core.model import SCAN_HEIGHT_1200, SCAN_WIDTH_1200, ImageInfo, ScanSettings, Sheet
from .scsi import CHECK_CONDITION, ScsiChannel, ScsiError, Sense

# Combinations whose parameter blocks come 1:1 from captures, with the read length from the capture
VERIFIED: dict[tuple[str, int], int] = {("gray", 150): 0x300000, ("color", 600): 0x20000}

_MODE_TEMPLATE = bytes.fromhex(
    "00030101d000c18080808080800000000000000000000000000000000000003000100096009605810500000028d0000044dc04"
).ljust(80, b"\0")


def is_verified(settings: ScanSettings) -> bool:
    return (settings.mode, settings.dpi) in VERIFIED


def read_len(settings: ScanSettings) -> int:
    # UNVERIFIED: 0x20000 for combinations without a capture (taken from color/600)
    return VERIFIED.get((settings.mode, settings.dpi), 0x20000)


def window_block(settings: ScanSettings) -> bytes:
    """E9 data block (32 B)."""
    dpi = settings.dpi
    return (struct.pack(">HHII", dpi, dpi, SCAN_WIDTH_1200, SCAN_HEIGHT_1200) + b"\x05").ljust(32, b"\0")


def mode_block(settings: ScanSettings) -> bytes:
    """D4 data block (80 B)."""
    # UNVERIFIED: derived except for gray/150 and color/600 (byte 13 color, 34–37 dpi, 40 mode);
    # color/300 is accepted live, although ScanSnap Home sends different bytes 10/13/40 there.
    b = bytearray(_MODE_TEMPLATE)
    b[13] = 0x80 if settings.mode == "color" else 0x00
    struct.pack_into(">HH", b, 34, settings.dpi, settings.dpi)
    b[40] = 0x0A if settings.mode == "color" else 0x05
    return bytes(b)


def parse_image_info(d: bytes) -> ImageInfo:
    w, hmax, _w2, h, dx, dy = struct.unpack_from(">IIIIHH", d)
    return ImageInfo(w, hmax, h, dx, dy)


def _sense_error(msg: str, sense: Sense) -> Exception:
    reason = sense.medium_error
    return FeedError(reason, str(sense)) if reason else ScsiError(msg, sense)


def _read_cdb(side: int, chunk: int, length: int) -> bytes:
    return bytes([0x28, 0, 0, 2, 0, 0x80 if side else 0]) + length.to_bytes(3, "big") + bytes([0, side, chunk])


def _info_cdb(side: int) -> bytes:
    return bytes([0x28, 0, 0x80, 0, 0, 0x80 if side else 0, 0, 0, 0x20, 0, side, 0])


class Scanner:
    """Scan sequence on an open scan channel (iX1500). Subclasses adapt single commands to other models."""

    def __init__(self, channel: ScsiChannel):
        self.ch = channel

    # --- model-specific parts ------------------------------------------------------------------
    def open_job(self) -> None:
        self.ch.checked(bytes.fromhex("d50000010808"), out=b"\0" * 8, in_len=8)

    def setup(self, settings: ScanSettings) -> None:
        ch = self.ch
        ch.checked(bytes.fromhex("d80000000000"))
        ch.checked(bytes.fromhex("e9000000000000200000"), out=window_block(settings))
        ch.checked(bytes.fromhex("d40000005000"), out=mode_block(settings))
        ch.request_sense()

    def read_len(self, settings: ScanSettings) -> int:
        return read_len(settings)

    def read_cdb(self, sheet: int, side: int, chunk: int, length: int) -> bytes:
        return _read_cdb(side, chunk, length)

    def chunk_header(self, sheet: int, side: int, chunk: int) -> bytes | None:
        """Expected first two data bytes of a chunk; None = do not check."""
        return bytes([side, chunk])

    def info_cdb(self, sheet: int, side: int) -> bytes:
        return _info_cdb(side)

    # --- sequence ------------------------------------------------------------------------------
    def _read_page(self, sheet: int, read_len: int) -> tuple[list[bytes], int]:
        parts: list[list[bytes]] = [[], []]
        done = [False, False]
        chunk = [0, 0]
        last = 0
        while not all(done):
            for side in (0, 1):
                if done[side]:
                    continue
                r = self.ch.command(self.read_cdb(sheet, side, chunk[side], read_len), in_len=read_len)
                header = self.chunk_header(sheet, side, chunk[side])
                if header is not None and r.data[:2] != header:
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

    def scan(self, settings: ScanSettings) -> Iterator[Sheet]:
        """Scans all sheets in the feeder. Raises NoPaper if the feeder is empty."""
        ch = self.ch
        self.open_job()
        try:
            self.setup(settings)
            pages = 0
            while True:
                r = ch.command(bytes.fromhex("e00000000000"))
                if r.status == CHECK_CONDITION:
                    sense = ch.request_sense()
                    if sense.no_paper:
                        if pages == 0:
                            raise NoPaper("no paper in feeder")
                        return
                    raise _sense_error("feed failed", sense)
                (front, back), last = self._read_page(pages, self.read_len(settings))
                info = parse_image_info(ch.checked(self.info_cdb(pages, last), in_len=0x20).data)
                pages += 1
                yield Sheet(front, back if settings.duplex else None, info)
        finally:
            ch.command(bytes.fromhex("d60000000000"))
