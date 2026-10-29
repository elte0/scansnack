"""Erzeugt die App-Icons (packaging/ScanSnack.icns/.ico/.png): Blatt, das in einen Scanner läuft.

.icns nur auf macOS (iconutil), .ico (Windows) und .png (Linux) überall.

uv run python tools/make_icon.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging"
S = 1024


def draw() -> Image.Image:
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Hintergrund: abgerundetes Quadrat im macOS-Raster (824 px Inhalt auf 1024), Verlauf blau
    bg = Image.new("RGBA", (S, S))
    top, bottom = (64, 140, 255), (20, 70, 200)
    for y in range(S):
        t = y / (S - 1)
        ImageDraw.Draw(bg).line(
            [(0, y), (S, y)], fill=(*(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)), 255)
        )
    mask = Image.new("L", (S, S))
    ImageDraw.Draw(mask).rounded_rectangle((100, 100, 924, 924), radius=185, fill=255)
    shadow = Image.new("RGBA", (S, S))
    ImageDraw.Draw(shadow).rounded_rectangle((100, 118, 924, 942), radius=185, fill=(0, 0, 0, 90))
    im.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    im.paste(bg, (0, 0), mask)

    d = ImageDraw.Draw(im)
    # Blatt mit Textzeilen
    d.rounded_rectangle((322, 190, 702, 640), radius=22, fill=(255, 255, 255, 255))
    for i, y in enumerate(range(262, 560, 52)):
        d.rounded_rectangle((372, y, 652 - (90 if i % 3 == 2 else 0), y + 20), radius=10, fill=(150, 170, 200, 255))
    # Scanner-Körper
    d.rounded_rectangle((210, 580, 814, 800), radius=60, fill=(36, 40, 52, 255))
    d.rounded_rectangle((250, 610, 774, 650), radius=20, fill=(14, 16, 22, 255))  # Einzugsschlitz
    d.rounded_rectangle((640, 712, 760, 744), radius=16, fill=(90, 210, 140, 255))  # Statuslicht
    return im


def main() -> None:
    icon = draw()
    iconset = OUT / "ScanSnack.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            icon.resize((px, px), Image.Resampling.LANCZOS).save(iconset / name)
    icon.save(OUT / "ScanSnack.png")
    icon.save(OUT / "ScanSnack.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "ScanSnack.icns")], check=True)
        print(OUT / "ScanSnack.icns")


if __name__ == "__main__":
    main()
