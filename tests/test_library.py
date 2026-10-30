import io
from pathlib import Path

import pytest
from PIL import Image

from scansnap.adapters.library.library import Library, read_pdf_pages, unique_path
from scansnap.core.document import ScanPage


def jpeg(w: int, h: int, color: str = "white") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def test_add_reads_back_pages_and_persists(tmp_path: Path) -> None:
    lib = Library(tmp_path / "lib.json")
    a, b = jpeg(300, 600, "red"), jpeg(200, 100, "blue")
    entry = lib.add([ScanPage(a, 300), ScanPage(b, 150)], tmp_path / "docs", "Letter")
    pages = read_pdf_pages(entry.file)
    assert [p.jpeg for p in pages] == [a, b] and [p.dpi for p in pages] == [300, 150]
    again = Library(tmp_path / "lib.json")
    assert [(e.name, e.pages) for e in again.entries] == [("Letter", 2)]


def test_update_rewrites_and_renames(tmp_path: Path) -> None:
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(300, 600), 300)], tmp_path, "Alt")
    old = entry.file
    page = read_pdf_pages(old)[0]
    page.rotate(90)
    lib.update(entry, [page, ScanPage(jpeg(100, 100), 100)], "New")
    assert not old.exists() and entry.file.name == "New.pdf" and entry.pages == 2
    first = Image.open(io.BytesIO(read_pdf_pages(entry.file)[0].jpeg))
    assert first.size == (600, 300)  # the rotation made it into the PDF


def test_remove_keeps_file_and_foreign_pdf_is_rejected(tmp_path: Path) -> None:
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(10, 10), 72)], tmp_path, "X")
    lib.remove(entry)
    assert entry.file.exists() and Library(tmp_path / "lib.json").entries == []
    foreign = tmp_path / "foreign.pdf"
    foreign.write_bytes(b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF")
    with pytest.raises(ValueError):
        read_pdf_pages(foreign)


def test_thumbnail_written_and_removed(tmp_path: Path) -> None:
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(300, 600), 300)], tmp_path, "T")
    thumb = lib.thumb_path(entry)
    assert Image.open(thumb).height == 112
    lib.remove(entry)
    assert not thumb.exists()


def test_rotation_applied_on_save(tmp_path: Path) -> None:
    page = ScanPage(jpeg(300, 600), 300)
    page.rotate(90)
    page.rotate(-180)
    assert page.rotation == 270
    path = Library(tmp_path / "lib.json").add([page, ScanPage(jpeg(300, 600), 150)], tmp_path, "Letter").file
    data = path.read_bytes()
    assert path.name == "Letter.pdf"
    assert b"/MediaBox [0 0 144.00 72.00]" in data  # rotated: landscape
    assert b"/MediaBox [0 0 144.00 288.00]" in data  # 150 dpi: double size


def test_unique_path_does_not_overwrite(tmp_path: Path) -> None:
    (tmp_path / "Scan.pdf").write_bytes(b"x")
    assert unique_path(tmp_path, "Scan").name == "Scan (2).pdf"
    assert unique_path(tmp_path, "a/b: c.pdf").name == "a-b- c.pdf"
