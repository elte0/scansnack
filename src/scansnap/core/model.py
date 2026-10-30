"""Basic types of a scan, independent of the transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["gray", "color"]

# Scan area of the iX1500 in 1/1200 inch, as ScanSnap Home sends it in the window block (E9)
SCAN_WIDTH_1200 = 0x28D0
SCAN_HEIGHT_1200 = 0x44DC


@dataclass(frozen=True)
class ScanSettings:
    mode: Mode = "gray"
    dpi: int = 150
    duplex: bool = False


@dataclass(frozen=True)
class ImageInfo:
    """Image info that the scanner reports after each page."""

    width: int
    height_max: int
    height: int
    dpi_x: int
    dpi_y: int


@dataclass
class Sheet:
    """One fed sheet: finished JPEGs; ``back`` is None for simplex."""

    front: bytes
    back: bytes | None
    info: ImageInfo | None


@dataclass(frozen=True)
class FoundScanner:
    """Result of the network search."""

    ip: str
    name: str
    model: str
    serial: str = ""
