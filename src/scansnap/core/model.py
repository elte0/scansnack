"""Grundtypen eines Scans, unabhängig vom Übertragungsweg."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["gray", "color"]

# Scanfeld des iX1500 in 1/1200 Zoll, wie ScanSnap Home es im Fenster-Block (E9) sendet
SCAN_WIDTH_1200 = 0x28D0
SCAN_HEIGHT_1200 = 0x44DC


@dataclass(frozen=True)
class ScanSettings:
    mode: Mode = "gray"
    dpi: int = 150
    duplex: bool = False


@dataclass(frozen=True)
class ImageInfo:
    """Bildinfo, die der Scanner nach jeder Seite meldet."""

    width: int
    height_max: int
    height: int
    dpi_x: int
    dpi_y: int


@dataclass
class Sheet:
    """Ein eingezogenes Blatt: fertige JPEGs; ``back`` ist None bei Simplex."""

    front: bytes
    back: bytes | None
    info: ImageInfo | None


@dataclass(frozen=True)
class FoundScanner:
    """Ergebnis der Suche im Netz."""

    ip: str
    name: str
    model: str
    serial: str = ""
