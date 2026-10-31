"""Post-processing of the JPEGs from the scanner: crop to the paper and rotate by 180° in one pass."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from PIL import Image, JpegImagePlugin

# Measurements (300 dpi, live scans 2026-10-29): background ~235–240, paper ~252,
# a shadow stripe ~225 at the paper edge. About 80 px of background left/right.
SCALE = 4  # analysis on a downscaled image
PERCENTILE = 0.75  # "mostly" bright: text on the paper does not interfere
MIN_CONTRAST = 6  # the paper must stand out this much from the background, otherwise no crop
MIN_FRACTION = 0.3  # smaller results count as misdetection


def _percentile(hist: list[int], q: float) -> int:
    target = q * sum(hist)
    acc = 0
    for value, n in enumerate(hist):
        acc += n
        if acc >= target:
            return value
    return 255


def _profile(gray: Image.Image, axis: str) -> list[int]:
    w, h = gray.size
    if axis == "x":
        return [_percentile(gray.crop((x, 0, x + 1, h)).histogram(), PERCENTILE) for x in range(w)]
    return [_percentile(gray.crop((0, y, w, y + 1)).histogram(), PERCENTILE) for y in range(h)]


def _span(profile: list[int]) -> tuple[int, int] | None:
    n = len(profile)
    edge = max(2, n // 100)
    background = min(profile[:edge] + profile[-edge:])
    middle = sorted(profile[n // 4 : 3 * n // 4])
    paper = middle[int(len(middle) * 0.9)]  # brightest rows/columns; dense text pulls the median down
    if paper - background < MIN_CONTRAST:
        return None
    threshold = (paper + background) / 2
    inside = [i for i, v in enumerate(profile) if v > threshold]
    if not inside:
        return None
    lo, hi = inside[0], inside[-1] + 1
    if hi - lo < MIN_FRACTION * n:
        return None
    return lo, hi


def paper_box(im: Image.Image) -> tuple[int, int, int, int] | None:
    """Paper area (left, top, right, bottom) in pixels, or None if it cannot be detected reliably."""
    w, h = im.size
    gray = im.convert("L").resize((max(w // SCALE, 1), max(h // SCALE, 1)), Image.Resampling.BOX)
    xs, ys = _span(_profile(gray, "x")), _span(_profile(gray, "y"))
    if xs is None and ys is None:
        return None
    (x0, x1), (y0, y1) = xs or (0, gray.width), ys or (0, gray.height)
    # one analysis line inwards so that no shadow remains
    box = (
        (x0 + 1) * SCALE if xs else 0,
        (y0 + 1) * SCALE if ys else 0,
        min((x1 - 1) * SCALE, w) if xs else w,
        min((y1 - 1) * SCALE, h) if ys else h,
    )
    return box if box[2] > box[0] and box[3] > box[1] else None


def _reencode(jpeg: bytes, transform: Callable[[Image.Image], Image.Image], dpi: int | None = None) -> bytes:
    """Applies ``transform`` and re-encodes with the quantization tables and the
    subsampling of the original so that hardly any quality is lost."""
    with Image.open(io.BytesIO(jpeg)) as src:
        if not isinstance(src, JpegImagePlugin.JpegImageFile):
            raise ValueError("not a JPEG")
        qtables, sampling = src.quantization, JpegImagePlugin.get_sampling(src)
        density: tuple[int, int] | None = (dpi, dpi) if dpi else src.info.get("dpi")
        im = transform(src.copy())
    opts: dict[str, Any] = {"qtables": qtables, "subsampling": sampling if sampling != -1 else 0}
    if density and all(density):
        opts["dpi"] = density
    out = io.BytesIO()
    im.save(out, "JPEG", **opts)
    return out.getvalue()


# Remove the background: estimate the paper color per channel and pull it to white
WHITEN_PERCENTILE = 0.85  # documents are mostly paper
WHITEN_KNEE = 10  # this far below the paper color already becomes pure white (noise, texture)
WHITEN_MIN_PAPER = 150  # darker "paper" (photo, dark card) stays unchanged


def paper_color(im: Image.Image) -> tuple[int, ...] | None:
    """Paper color per channel (inner area, downscaled), or None if no bright paper can be detected."""
    w, h = im.size
    inner = im.crop((w // 20, h // 20, w - w // 20, h - h // 20))
    small = inner.resize((max(inner.width // 8, 1), max(inner.height // 8, 1)), Image.Resampling.BOX)
    bands = small.split()
    color = tuple(_percentile(b.histogram(), WHITEN_PERCENTILE) for b in bands)
    return color if min(color) >= WHITEN_MIN_PAPER else None


def whiten(im: Image.Image) -> Image.Image:
    """Sets the paper color to white (levels correction per channel); dark content stays dark."""
    color = paper_color(im)
    if color is None:
        return im
    luts: list[int] = []
    for c in color:
        white = max(c - WHITEN_KNEE, 1)
        luts.extend(min(255, round(v * 255 / white)) for v in range(256))
    return im.point(luts)


def process(
    jpeg: bytes,
    dpi: int | None = None,
    *,
    rotate: bool = True,
    crop: bool = True,
    whiten_background: bool = False,
    gray: bool = False,
) -> bytes:
    """Crops to the paper, rotates by 180°, optionally sets the paper color to white and converts to
    grayscale – in one pass. ``dpi`` is written into the JFIF header (the scanner writes 0)."""
    if not (rotate or crop or whiten_background or gray):
        return jpeg

    def transform(im: Image.Image) -> Image.Image:
        if crop and (box := paper_box(im)):
            im = im.crop(box)
        if whiten_background:
            im = whiten(im)
        if gray:
            im = im.convert("L")
        return im.transpose(Image.Transpose.ROTATE_180) if rotate else im

    return _reencode(jpeg, transform, dpi)


def rotate_180(jpeg: bytes, dpi: int | None = None) -> bytes:
    return process(jpeg, dpi, rotate=True, crop=False)


_TRANSPOSE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def rotate(jpeg: bytes, degrees: int) -> bytes:
    """Rotates clockwise by 90, 180 or 270 degrees."""
    degrees %= 360
    if degrees == 0:
        return jpeg
    method = _TRANSPOSE[degrees]
    return _reencode(jpeg, lambda im: im.transpose(method))


# Blank pages: share of "dark" pixels (clearly darker than the paper) in the inner area
BLANK_MARGIN = 0.05  # ignore the margin (shadow edge, punch holes at the edge)
BLANK_DARKER = 40  # this much darker than the paper counts as content
BLANK_MAX_INK = 0.002  # at most 0.2 % content → blank


def is_blank(jpeg: bytes) -> bool:
    with Image.open(io.BytesIO(jpeg)) as src:
        src.draft("L", (src.width // 8, src.height // 8))  # decodes directly at reduced size
        im = src.convert("L")
        im = im.resize((max(im.width // 4, 1), max(im.height // 4, 1)), Image.Resampling.BOX)
    w, h = im.size
    mx, my = int(w * BLANK_MARGIN), int(h * BLANK_MARGIN)
    hist = im.crop((mx, my, w - mx, h - my)).histogram()
    total = sum(hist)
    paper = _percentile(hist, 0.5)
    ink = sum(hist[: max(paper - BLANK_DARKER, 0)])
    return total > 0 and ink / total <= BLANK_MAX_INK
