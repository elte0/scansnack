"""Scan sequence on the scan channel: D5, D8, E9, D4, (E0, 28 reads, 28 info)*, D6.

Parameter blocks follow ScanSnap Home's iX500 Wi-Fi code (SshResident ``SSDevCtliX500Wifi::SetWindow``,
``MakeWindowDescriptor``); see docs/protocol-ix500.md, section "Scan parameters".
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Literal

from ...core.errors import NoPaper
from ...core.model import Sheet
from ..scansnap_wifi.images import MAX_IMAGE, parse_image_info, read_image
from ..scansnap_wifi.images import READ_LEN as READ_LEN
from ..scansnap_wifi.images import info_cdb as _info_cdb
from ..scansnap_wifi.images import sense_error as _sense_error
from .scsi import CHECK_CONDITION, ScsiChannel, ScsiError

DPIS = (150, 200, 300, 600)
Multifeed = Literal["off", "ultrasonic"]

# Page sizes in 1/1200 inch (width, height); "auto" is what ScanSnap Home sends by default.
PAGE_SIZES: dict[str, tuple[int, int]] = {
    "auto": (0x28D0, 0x44DC),
    "a4": (0x26C1, 0x36D0),
    "letter": (0x27D8, 0x3390),
    "legal": (0x27D8, 0x41A0),
    "b5": (0x2196, 0x2F6E),
    "a5": (0x1B50, 0x26C1),
    "b6": (0x179F, 0x2196),
}
AUTO_SIZES = ("auto",)


def check_dpi(dpi: int) -> int:
    if dpi not in DPIS:
        raise ValueError(f"dpi must be one of {DPIS}")
    return dpi


def window_block(dpi: int, page: str = "auto") -> bytes:
    """E9 data block (32 B)."""
    width, height = PAGE_SIZES[page]
    return (struct.pack(">HHII", check_dpi(dpi), dpi, width, height) + b"\x05").ljust(32, b"\0")


def mode_block(dpi: int, page: str = "auto", multifeed: Multifeed = "ultrasonic") -> bytes:
    """D4 data block (80 B): 32 B scan parameters + 48 B window descriptor."""
    check_dpi(dpi)
    width, height = PAGE_SIZES[page]
    auto = page in AUTO_SIZES
    params = bytearray(32)
    params[1] = 3
    params[2] = params[3] = 1 if auto else 0
    params[4] = 0xD0 if multifeed == "ultrasonic" else 0x00
    params[6] = 0xC1
    params[7:10] = b"\x80\x80\x80"
    params[10] = 0x90 if dpi == 300 else 0x80
    params[11:13] = b"\x80\x80"
    params[13] = 0x80 if dpi == 600 else 0x00
    params[31] = 0x30
    quality = {150: 5, 200: 5, 300: 0, 600: 0x0A}[dpi]
    descriptor = struct.pack(">2sHHBBBxII", b"\x00\x10", dpi, dpi, 5, 0x81, quality, width, height) + b"\x04"
    return bytes(params) + descriptor.ljust(48, b"\0")


class Scanner:
    """Scan sequence on an open scan channel. The iX500 always scans both sides in color."""

    def __init__(self, channel: ScsiChannel):
        self.ch = channel

    def _read_image(self, back: bool, index: int) -> bytes:
        return read_image(self.ch, back, index, MAX_IMAGE)

    def scan(self, dpi: int = 300, page: str = "auto", multifeed: Multifeed = "ultrasonic") -> Iterator[Sheet]:
        """Scans all sheets in the feeder (front and back). Raises NoPaper if the feeder is empty."""
        ch = self.ch
        ch.hardware_status()
        ch.checked(bytes.fromhex("d50000000808"), out=b"\0" * 8, in_len=8)
        ch.checked(bytes.fromhex("d80000000000"))
        ch.checked(bytes.fromhex("e9000000000000200000"), out=window_block(dpi, page))
        ch.checked(bytes.fromhex("d40000005000"), out=mode_block(dpi, page, multifeed))
        ch.hardware_status()
        ch.request_sense()
        index = sheets = 0
        while True:
            r = ch.command(bytes.fromhex("e00000000000"))
            if r.status == CHECK_CONDITION:
                sense = ch.request_sense()
                if sense.no_paper:
                    if sheets == 0:
                        raise NoPaper("no paper in feeder")
                    return
                raise _sense_error("feed failed", sense)
            if r.status:
                raise ScsiError(f"feed status {r.status}")
            front = self._read_image(False, index)
            back = self._read_image(True, index + 1)
            info = parse_image_info(ch.checked(_info_cdb(index + 1), in_len=0x20).data)
            ch.hardware_status()
            index += 2
            sheets += 1
            yield Sheet(front, back, info)


def release(ip: str, mac: bytes) -> None:
    """D6 on a connection of its own, as ScanSnap Home ends every scan."""
    with ScsiChannel(ip, mac, timeout=10.0) as ch:
        ch.checked(bytes.fromhex("d60000000000"))
