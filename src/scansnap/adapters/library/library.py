"""Document list of the app: index of the saved PDFs (JSON). Pages are read from the PDFs."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from ...core.document import Entry, ScanPage, default_name, thumbnail
from ...core.pdf import jpegs_to_pdf

_IMAGE = re.compile(
    rb"/Subtype /Image /Width (\d+) /Height (\d+) .*?/Filter /DCTDecode /Length (\d+) >>\nstream\n", re.DOTALL
)
_MEDIABOX = re.compile(rb"/Type /Page /Parent \d+ 0 R /MediaBox \[0 0 ([\d.]+) ([\d.]+)\]")


def read_pdf_pages(path: Path) -> list[ScanPage]:
    """Reads the JPEG pages from a PDF written by this app (``jpegs_to_pdf``)."""
    data = path.read_bytes()
    images = [(int(m[1]), m.end(), int(m[3])) for m in _IMAGE.finditer(data)]
    boxes = [float(m[1]) for m in _MEDIABOX.finditer(data)]
    if not images or len(images) != len(boxes):
        raise ValueError(f"{path.name}: not a PDF written by this app")
    return [
        ScanPage(data[start : start + length], round(width * 72 / pw))
        for (width, start, length), pw in zip(images, boxes, strict=True)
    ]


def unique_path(folder: Path, name: str) -> Path:
    name = name.strip().removesuffix(".pdf") or default_name()
    name = name.replace("/", "-").replace(":", "-")
    path = folder / f"{name}.pdf"
    n = 2
    while path.exists():
        path = folder / f"{name} ({n}).pdf"
        n += 1
    return path


THUMB_SIZE = 112  # document list (Retina: twice the display size)


class Library:
    """Implements ``ports.output.OutputPort``."""

    def __init__(self, index: Path):
        self.index = index
        self.entries: list[Entry] = []
        if index.exists():
            raw = json.loads(index.read_text(encoding="utf-8"))
            self.entries = [Entry(**e) for e in raw.get("documents", [])]

    def _store(self) -> None:
        self.index.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"documents": [asdict(e) for e in self.entries]}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(self.index)

    def thumb_path(self, entry: Entry) -> Path:
        """Small preview of the first page so that the list does not have to read PDFs at startup."""
        return self.index.parent / "thumbs" / f"{entry.id}.png"

    def _write_thumb(self, entry: Entry, pages: Sequence[ScanPage]) -> None:
        path = self.thumb_path(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(thumbnail(pages[0].rendered(), THUMB_SIZE))

    def thumbnail(self, entry: Entry) -> bytes | None:
        path = self.thumb_path(entry)
        return path.read_bytes() if path.exists() else None

    def read_pages(self, entry: Entry) -> list[ScanPage]:
        return read_pdf_pages(entry.file)

    def get(self, entry_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def add(self, pages: Sequence[ScanPage], folder: Path, name: str) -> Entry:
        if not pages:
            raise ValueError("no pages")
        folder.mkdir(parents=True, exist_ok=True)
        path = unique_path(folder, name)
        path.write_bytes(_pdf(pages))
        entry = Entry(str(path), path.stem, dt.datetime.now().isoformat(timespec="seconds"), len(pages))
        self.entries.insert(0, entry)
        self._write_thumb(entry, pages)
        self._store()
        return entry

    def update(self, entry: Entry, pages: Sequence[ScanPage], name: str) -> Entry:
        """Rewrites the PDF; a changed name renames the file (without overwriting)."""
        if not pages:
            raise ValueError("no pages")
        target = entry.file
        if name.strip() and name.strip().removesuffix(".pdf") != entry.file.stem:
            target = unique_path(entry.file.parent, name)
        tmp = target.with_suffix(".pdf.tmp")
        tmp.write_bytes(_pdf(pages))
        tmp.replace(target)
        if target != entry.file and entry.file.exists():
            entry.file.unlink()
        entry.path, entry.name, entry.pages = str(target), target.stem, len(pages)
        self._write_thumb(entry, pages)
        self._store()
        return entry

    def remove(self, entry: Entry) -> None:
        """Removes the entry from the list; the file stays."""
        self.entries = [e for e in self.entries if e.id != entry.id]
        self.thumb_path(entry).unlink(missing_ok=True)
        self._store()


def _pdf(pages: Sequence[ScanPage]) -> bytes:
    return jpegs_to_pdf([p.rendered() for p in pages], [p.dpi for p in pages])
