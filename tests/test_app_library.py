import io

import pytest
from PIL import Image

from scansnap.app.document import ScanPage
from scansnap.app.library import Library, read_pdf_pages


def jpeg(w, h, color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def test_add_reads_back_pages_and_persists(tmp_path):
    lib = Library(tmp_path / "lib.json")
    a, b = jpeg(300, 600, "red"), jpeg(200, 100, "blue")
    entry = lib.add([ScanPage(a, 300), ScanPage(b, 150)], tmp_path / "docs", "Brief")
    pages = read_pdf_pages(entry.file)
    assert [p.jpeg for p in pages] == [a, b] and [p.dpi for p in pages] == [300, 150]
    again = Library(tmp_path / "lib.json")
    assert [(e.name, e.pages) for e in again.entries] == [("Brief", 2)]


def test_update_rewrites_and_renames(tmp_path):
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(300, 600), 300)], tmp_path, "Alt")
    old = entry.file
    page = read_pdf_pages(old)[0]
    page.rotate(90)
    lib.update(entry, [page, ScanPage(jpeg(100, 100), 100)], "Neu")
    assert not old.exists() and entry.file.name == "Neu.pdf" and entry.pages == 2
    first = Image.open(io.BytesIO(read_pdf_pages(entry.file)[0].jpeg))
    assert first.size == (600, 300)                     # Drehung ist im PDF angekommen


def test_remove_keeps_file_and_foreign_pdf_is_rejected(tmp_path):
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(10, 10), 72)], tmp_path, "X")
    lib.remove(entry)
    assert entry.file.exists() and Library(tmp_path / "lib.json").entries == []
    foreign = tmp_path / "foreign.pdf"
    foreign.write_bytes(b"%PDF-1.7\n1 0 obj << >> endobj\n%%EOF")
    with pytest.raises(ValueError):
        read_pdf_pages(foreign)


def test_thumbnail_written_and_removed(tmp_path):
    lib = Library(tmp_path / "lib.json")
    entry = lib.add([ScanPage(jpeg(300, 600), 300)], tmp_path, "T")
    thumb = lib.thumb_path(entry)
    assert Image.open(thumb).height == 112
    lib.remove(entry)
    assert not thumb.exists()
