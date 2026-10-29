"""Minimales PDF aus JPEG-Seiten: jede Seite ein unverändert eingebetteter DCT-Stream."""

from __future__ import annotations

import struct

_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def jpeg_info(data: bytes) -> tuple[int, int, int]:
    """Breite, Höhe und Anzahl Farbkanäle aus dem SOF-Marker."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("kein JPEG")
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            raise ValueError("JPEG-Marker erwartet")
        marker = data[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        length = struct.unpack_from(">H", data, i + 2)[0]
        if marker in _SOF:
            h, w, comps = struct.unpack_from(">HHB", data, i + 5)
            return w, h, comps
        i += 2 + length
    raise ValueError("kein SOF-Marker gefunden")


def jpegs_to_pdf(pages: list[bytes], dpi: int | list[int]) -> bytes:
    """``dpi`` für alle Seiten oder je Seite (bestimmt die Seitengröße im PDF)."""
    dpis = dpi if isinstance(dpi, list) else [dpi] * len(pages)
    objects: list[bytes] = []          # Objekt n steht an Index n-1

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")                 # wird unten gefüllt
    tree = add(b"")
    kids = []
    for jpeg, page_dpi in zip(pages, dpis, strict=True):
        w, h, comps = jpeg_info(jpeg)
        cs = {1: b"/DeviceGray", 3: b"/DeviceRGB", 4: b"/DeviceCMYK"}[comps]
        img = add(b"<< /Type /XObject /Subtype /Image /Width %d /Height %d /ColorSpace %s "
                  b"/BitsPerComponent 8 /Filter /DCTDecode /Length %d >>\nstream\n" % (w, h, cs, len(jpeg))
                  + jpeg + b"\nendstream")
        pw, ph = w * 72 / page_dpi, h * 72 / page_dpi
        content = b"q %.2f 0 0 %.2f 0 0 cm /Im0 Do Q" % (pw, ph)
        cont = add(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
        kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                        b"/Resources << /XObject << /Im0 %d 0 R >> >> /Contents %d 0 R >>"
                        % (tree, pw, ph, img, cont)))
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    objects[tree - 1] = (b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % k for k in kids)
                         + b"] /Count %d >>" % len(kids))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, xref)
    return bytes(out)
