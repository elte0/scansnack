"""Dokumentmodell der Apps (Desktop und iOS): Seiten, Voreinstellungen, Einträge der Dokumentliste."""

from __future__ import annotations

import datetime as dt
import io
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps

from .images import rotate
from .model import Mode


@dataclass(frozen=True)
class Preset:
    name: str
    mode: Mode
    dpi: int
    duplex: bool = True
    skip_blank: bool = True
    whiten: bool = True  # Papierfarbe auf Weiß setzen

    @property
    def summary(self) -> str:
        return (
            f"{'Color' if self.mode == 'color' else 'Black & white'}, {self.dpi} dpi, "
            f"{'double-sided' if self.duplex else 'single-sided'}"
        )


PRESETS = (
    Preset("Document – Color", "color", 300),
    Preset("Document – Black & White", "gray", 300),
    Preset("Quick – Black & White", "gray", 150),
    Preset("Photo", "color", 600, duplex=False, skip_blank=False, whiten=False),
)


# Hinweise zu errors.FeedError.reason (Desktop- und iOS-App)
FEED_HELP = {
    "paper jam": "Paper jam, or the sheet could not be fed. Open the scanner, remove the paper, "
    "smooth it out and try again.",
    "double feed": "Several sheets were fed at once. Fan the stack and try again.",
    "cover open": "The scanner cover is open. Close it and try again.",
    "unusual paper": "This paper cannot be fed. Try a different sheet or a carrier sheet.",
    "not enough paper": "Not enough paper in the feeder.",
    "stop button pressed": "The scan was stopped on the scanner.",
}


@dataclass
class ScanPage:
    jpeg: bytes
    dpi: int
    rotation: int = 0  # im Uhrzeigersinn; wird erst beim Speichern angewendet

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
        im = ImageOps.expand(im, border=1, fill=(170, 170, 170))  # weiße Seiten auf weißem Grund
        out = io.BytesIO()
        im.save(out, "PNG")
    return out.getvalue()


def default_name(now: dt.datetime | None = None) -> str:
    return f"Scan {(now or dt.datetime.now()):%Y-%m-%d %H.%M}"


@dataclass
class Entry:
    """Gespeichertes Dokument in der Dokumentliste."""

    path: str
    name: str
    created: str
    pages: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def file(self) -> Path:
        return Path(self.path)

    @property
    def exists(self) -> bool:
        return self.file.exists()
