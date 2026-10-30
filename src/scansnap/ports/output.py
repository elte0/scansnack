"""Storage of scanned documents."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..core.document import Entry, ScanPage


class OutputPort(Protocol):
    """Document list with saved PDFs. Write errors: ``OSError``/``ValueError``."""

    @property
    def entries(self) -> Sequence[Entry]:
        """Newest first."""
        ...

    def get(self, entry_id: str) -> Entry | None: ...

    def add(self, pages: Sequence[ScanPage], folder: Path, name: str) -> Entry: ...

    def update(self, entry: Entry, pages: Sequence[ScanPage], name: str) -> Entry: ...

    def remove(self, entry: Entry) -> None:
        """Remove from the list only; the file stays."""
        ...

    def read_pages(self, entry: Entry) -> list[ScanPage]: ...

    def thumbnail(self, entry: Entry) -> bytes | None:
        """PNG preview of the first page, without reading the PDF; None if there is none."""
        ...
