import datetime as dt
import io

from PIL import Image

from scansnap.core.document import default_name, thumbnail


def jpeg(w: int = 300, h: int = 600) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


def test_default_name_and_thumbnail() -> None:
    assert default_name(dt.datetime(2026, 10, 29, 9, 5)) == "Scan 2026-10-29 09.05"
    im = Image.open(io.BytesIO(thumbnail(jpeg(), 100)))
    assert im.height == 100 and 49 <= im.width <= 51 and im.format == "PNG"
    assert im.getpixel((0, 0)) == (170, 170, 170)
