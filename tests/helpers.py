"""Kleine Hilfen für Tests."""

from __future__ import annotations

import io

from PIL import Image


def jpeg(w: int = 40, h: int = 60, color: str | int | tuple[int, int, int] = "white", mode: str = "RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def pixel(im: Image.Image, xy: tuple[int, int]) -> tuple[int, ...]:
    """Farbwerte eines Pixels als Tupel (RGB-Bilder)."""
    value = im.getpixel(xy)
    assert isinstance(value, tuple)
    return value
