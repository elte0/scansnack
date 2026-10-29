import io

from PIL import Image, ImageDraw

from helpers import pixel
from scansnap.core.images import is_blank, paper_box, process, rotate, whiten
from scansnap.core.pdf import jpegs_to_pdf


def scan_like(
    w: int = 652,
    h: int = 922,
    box: tuple[int, int, int, int] = (20, 13, 640, 895),
    paper: int = 252,
    background: int = 238,
) -> Image.Image:
    """Synthetischer Scan wie vom iX1500: heller Hintergrund, Schattenkante, Papier mit Text."""
    im = Image.new("L", (w, h), background)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = box
    d.rectangle((x0 - 3, y0 - 3, x1 + 2, y1 + 2), fill=225)  # Schatten
    d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=paper)
    for y in range(y0 + 40, y1 - 40, 24):  # Textzeilen
        d.rectangle((x0 + 60, y, x1 - 60, y + 8), fill=30)
    return im.convert("RGB")


def jpeg_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def test_paper_box_finds_paper_despite_text() -> None:
    box = paper_box(scan_like())
    assert box is not None
    x0, y0, x1, y1 = box
    assert 20 <= x0 <= 28 and 13 <= y0 <= 21 and 632 <= x1 <= 640 and 887 <= y1 <= 895


def test_no_crop_without_contrast() -> None:
    assert paper_box(scan_like(paper=240, background=238)) is None


def test_process_crops_rotates_and_sets_dpi() -> None:
    out = Image.open(io.BytesIO(process(jpeg_bytes(scan_like()), 75)))
    assert 600 <= out.width <= 620 and 860 <= out.height <= 882
    assert out.info["dpi"] == (75, 75)


def test_process_without_crop_keeps_size() -> None:
    out = Image.open(io.BytesIO(process(jpeg_bytes(scan_like()), 75, crop=False)))
    assert out.size == (652, 922)


def test_rotate_90_clockwise() -> None:
    im = Image.new("RGB", (60, 30), "white")
    im.paste((255, 0, 0), (0, 0, 15, 15))  # rot oben links
    out = Image.open(io.BytesIO(rotate(jpeg_bytes(im), 90)))
    assert out.size == (30, 60)
    r, g, _ = pixel(out, (25, 5))  # im Uhrzeigersinn → oben rechts
    assert r > 200 and g < 80


def test_is_blank() -> None:
    empty = Image.new("L", (600, 800), 250)
    assert is_blank(jpeg_bytes(empty))
    text = empty.copy()
    ImageDraw.Draw(text).rectangle((100, 300, 400, 330), fill=20)
    assert not is_blank(jpeg_bytes(text))


def test_pdf_with_per_page_dpi() -> None:
    page = jpeg_bytes(Image.new("L", (300, 300), 255))
    pdf = jpegs_to_pdf([page, page], [300, 150])
    assert b"/MediaBox [0 0 72.00 72.00]" in pdf and b"/MediaBox [0 0 144.00 144.00]" in pdf


def test_whiten_makes_tinted_paper_white_and_keeps_text() -> None:
    im = Image.new("RGB", (400, 560), (246, 240, 222))  # gelbliches Papier
    ImageDraw.Draw(im).rectangle((60, 200, 340, 230), fill=(40, 40, 40))
    out = whiten(im)
    assert all(v >= 254 for v in pixel(out, (20, 20)))
    assert max(pixel(out, (200, 215))) < 60


def test_whiten_leaves_dark_images_alone() -> None:
    im = Image.new("RGB", (200, 200), (90, 120, 60))
    assert whiten(im).getpixel((10, 10)) == (90, 120, 60)
