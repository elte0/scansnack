"""Nachbearbeitung der JPEGs vom Scanner: Zuschnitt aufs Papier und 180°-Drehung in einem Durchgang."""

from __future__ import annotations

import io

from PIL import Image, JpegImagePlugin

# Messwerte (300 dpi, Live-Scans 2026-10-29): Hintergrund ~235–240, Papier ~252,
# am Papierrand ein Schattenstreifen ~225. Rund 80 px Hintergrund links/rechts.
SCALE = 4                 # Analyse auf verkleinertem Bild
PERCENTILE = 0.75         # „überwiegend“ hell: Text auf dem Papier stört nicht
MIN_CONTRAST = 6          # Papier muss sich so weit vom Hintergrund abheben, sonst kein Zuschnitt
MIN_FRACTION = 0.3        # kleinere Ergebnisse gelten als Fehlerkennung


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
    middle = sorted(profile[n // 4: 3 * n // 4])
    paper = middle[int(len(middle) * 0.9)]      # hellste Zeilen/Spalten; dichter Text drückt den Median
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
    """Papierbereich (links, oben, rechts, unten) in Pixeln oder None, wenn nicht sicher erkennbar."""
    w, h = im.size
    gray = im.convert("L").resize((max(w // SCALE, 1), max(h // SCALE, 1)), Image.Resampling.BOX)
    xs, ys = _span(_profile(gray, "x")), _span(_profile(gray, "y"))
    if xs is None and ys is None:
        return None
    (x0, x1), (y0, y1) = xs or (0, gray.width), ys or (0, gray.height)
    # eine Analysezeile nach innen, damit kein Schattenrest bleibt
    box = ((x0 + 1) * SCALE if xs else 0, (y0 + 1) * SCALE if ys else 0,
           min((x1 - 1) * SCALE, w) if xs else w, min((y1 - 1) * SCALE, h) if ys else h)
    return box if box[2] > box[0] and box[3] > box[1] else None


def _reencode(jpeg: bytes, transform, dpi: int | None = None) -> bytes:
    """Wendet ``transform`` (Image → Image) an und kodiert mit den Quantisierungstabellen und dem
    Subsampling des Originals neu, damit kaum Qualität verloren geht."""
    with Image.open(io.BytesIO(jpeg)) as src:
        qtables, sampling = src.quantization, JpegImagePlugin.get_sampling(src)
        density = (dpi, dpi) if dpi else src.info.get("dpi")
        im = transform(src.copy())
    opts = {"qtables": qtables, "subsampling": sampling if sampling != -1 else 0}
    if density and all(density):
        opts["dpi"] = density
    out = io.BytesIO()
    im.save(out, "JPEG", **opts)
    return out.getvalue()


# Hintergrund entfernen: Papierfarbe je Kanal schätzen und auf Weiß ziehen
WHITEN_PERCENTILE = 0.85  # Dokumente sind überwiegend Papier
WHITEN_KNEE = 10          # so viel unter der Papierfarbe wird schon reines Weiß (Rauschen, Struktur)
WHITEN_MIN_PAPER = 150    # dunkleres „Papier“ (Foto, dunkler Karton) bleibt unverändert


def paper_color(im: Image.Image) -> tuple[int, ...] | None:
    """Papierfarbe je Kanal (Innenbereich, verkleinert) oder None, wenn kein helles Papier erkennbar ist."""
    w, h = im.size
    inner = im.crop((w // 20, h // 20, w - w // 20, h - h // 20))
    small = inner.resize((max(inner.width // 8, 1), max(inner.height // 8, 1)), Image.Resampling.BOX)
    bands = small.split()
    color = tuple(_percentile(b.histogram(), WHITEN_PERCENTILE) for b in bands)
    return color if min(color) >= WHITEN_MIN_PAPER else None


def whiten(im: Image.Image) -> Image.Image:
    """Setzt die Papierfarbe auf Weiß (Tonwertkorrektur je Kanal); dunkle Inhalte bleiben dunkel."""
    color = paper_color(im)
    if color is None:
        return im
    luts = []
    for c in color:
        white = max(c - WHITEN_KNEE, 1)
        luts.extend(min(255, round(v * 255 / white)) for v in range(256))
    return im.point(luts)


def process(jpeg: bytes, dpi: int | None = None, *, rotate: bool = True, crop: bool = True,
            whiten_background: bool = False) -> bytes:
    """Schneidet aufs Papier zu, dreht um 180° und setzt auf Wunsch die Papierfarbe auf Weiß – in einem
    Durchgang. ``dpi`` wird ins JFIF geschrieben (der Scanner trägt 0 ein)."""
    if not (rotate or crop or whiten_background):
        return jpeg

    def transform(im: Image.Image) -> Image.Image:
        if crop and (box := paper_box(im)):
            im = im.crop(box)
        if whiten_background:
            im = whiten(im)
        return im.transpose(Image.Transpose.ROTATE_180) if rotate else im

    return _reencode(jpeg, transform, dpi)


def rotate_180(jpeg: bytes, dpi: int | None = None) -> bytes:
    return process(jpeg, dpi, rotate=True, crop=False)


_TRANSPOSE = {90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_90}


def rotate(jpeg: bytes, degrees: int) -> bytes:
    """Dreht im Uhrzeigersinn um 90, 180 oder 270 Grad."""
    degrees %= 360
    if degrees == 0:
        return jpeg
    return _reencode(jpeg, lambda im: im.transpose(_TRANSPOSE[degrees]))


# Leerseiten: Anteil „dunkler“ Pixel (deutlich dunkler als das Papier) im Innenbereich
BLANK_MARGIN = 0.05       # Rand ignorieren (Schattenkante, Lochung am Rand)
BLANK_DARKER = 40         # so viel dunkler als das Papier zählt als Inhalt
BLANK_MAX_INK = 0.002     # höchstens 0,2 % Inhalt → leer


def is_blank(jpeg: bytes) -> bool:
    with Image.open(io.BytesIO(jpeg)) as src:
        src.draft("L", (src.width // 8, src.height // 8))      # dekodiert direkt verkleinert
        im = src.convert("L")
        im = im.resize((max(im.width // 4, 1), max(im.height // 4, 1)), Image.Resampling.BOX)
    w, h = im.size
    mx, my = int(w * BLANK_MARGIN), int(h * BLANK_MARGIN)
    hist = im.crop((mx, my, w - mx, h - my)).histogram()
    total = sum(hist)
    paper = _percentile(hist, 0.5)
    ink = sum(hist[: max(paper - BLANK_DARKER, 0)])
    return total > 0 and ink / total <= BLANK_MAX_INK
