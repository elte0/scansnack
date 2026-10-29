"""Dokumentmodell der App ohne Qt: Seiten, Voreinstellungen, Speichern als PDF."""

from __future__ import annotations

import datetime as dt
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from ..images import rotate
from ..pdf import jpegs_to_pdf
from ..scanner import Mode


@dataclass(frozen=True)
class Preset:
    name: str
    mode: Mode
    dpi: int
    duplex: bool = True
    skip_blank: bool = True
    whiten: bool = True             # Papierfarbe auf Weiß setzen

    @property
    def summary(self) -> str:
        return f"{'Color' if self.mode == 'color' else 'Black & white'}, {self.dpi} dpi, " \
               f"{'double-sided' if self.duplex else 'single-sided'}"


PRESETS = (
    Preset("Document – Color", "color", 300),
    Preset("Document – Black & White", "gray", 300),
    Preset("Quick – Black & White", "gray", 150),
    Preset("Photo", "color", 600, duplex=False, skip_blank=False, whiten=False),
)


@dataclass
class ScanPage:
    jpeg: bytes
    dpi: int
    rotation: int = 0          # im Uhrzeigersinn; wird erst beim Speichern angewendet

    def rotate(self, degrees: int) -> None:
        self.rotation = (self.rotation + degrees) % 360

    def rendered(self) -> bytes:
        return rotate(self.jpeg, self.rotation)


def thumbnail(jpeg: bytes, size: int = 320) -> bytes:
    """PNG-Vorschau, längste Kante ``size`` Pixel."""
    with Image.open(io.BytesIO(jpeg)) as im:
        im.draft("RGB", (size, size))
        im = im.convert("RGB")
        im.thumbnail((size - 2, size - 2))
        im = ImageOps.expand(im, border=1, fill=(170, 170, 170))   # weiße Seiten auf weißem Grund
        out = io.BytesIO()
        im.save(out, "PNG")
    return out.getvalue()


def default_name(now: dt.datetime | None = None) -> str:
    return f"Scan {(now or dt.datetime.now()):%Y-%m-%d %H.%M}"


def unique_path(folder: Path, name: str) -> Path:
    name = name.strip().removesuffix(".pdf") or default_name()
    name = name.replace("/", "-").replace(":", "-")
    path = folder / f"{name}.pdf"
    n = 2
    while path.exists():
        path = folder / f"{name} ({n}).pdf"
        n += 1
    return path


def save_pdf(pages: list[ScanPage], folder: Path, name: str) -> Path:
    if not pages:
        raise ValueError("keine Seiten")
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, name)
    path.write_bytes(jpegs_to_pdf([p.rendered() for p in pages], [p.dpi for p in pages]))
    return path
