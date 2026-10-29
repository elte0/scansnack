import datetime as dt
import io

from PIL import Image

from scansnap.app.document import ScanPage, default_name, save_pdf, thumbnail, unique_path


def jpeg(w=300, h=600):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


def test_rotation_applied_on_save(tmp_path):
    page = ScanPage(jpeg(), 300)
    page.rotate(90)
    page.rotate(-180)
    assert page.rotation == 270
    path = save_pdf([page, ScanPage(jpeg(), 150)], tmp_path, "Brief")
    data = path.read_bytes()
    assert path.name == "Brief.pdf"
    assert b"/MediaBox [0 0 144.00 72.00]" in data          # gedreht: quer
    assert b"/MediaBox [0 0 144.00 288.00]" in data         # 150 dpi: doppelte Größe


def test_unique_path_does_not_overwrite(tmp_path):
    (tmp_path / "Scan.pdf").write_bytes(b"x")
    assert unique_path(tmp_path, "Scan").name == "Scan (2).pdf"
    assert unique_path(tmp_path, "a/b: c.pdf").name == "a-b- c.pdf"


def test_default_name_and_thumbnail():
    assert default_name(dt.datetime(2026, 10, 29, 9, 5)) == "Scan 2026-10-29 09.05"
    im = Image.open(io.BytesIO(thumbnail(jpeg(), 100)))
    assert im.height == 100 and 49 <= im.width <= 51 and im.format == "PNG"
    assert im.getpixel((0, 0)) == (170, 170, 170)
