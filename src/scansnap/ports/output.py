"""Ablage gescannter Dokumente."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from ..core.document import Entry, ScanPage


class OutputPort(Protocol):
    """Dokumentliste mit gespeicherten PDFs. Fehler beim Schreiben: ``OSError``/``ValueError``."""

    @property
    def entries(self) -> Sequence[Entry]:
        """Neueste zuerst."""
        ...

    def get(self, entry_id: str) -> Entry | None: ...

    def add(self, pages: Sequence[ScanPage], folder: Path, name: str) -> Entry: ...

    def update(self, entry: Entry, pages: Sequence[ScanPage], name: str) -> Entry: ...

    def remove(self, entry: Entry) -> None:
        """Nur aus der Liste entfernen; die Datei bleibt."""
        ...

    def read_pages(self, entry: Entry) -> list[ScanPage]: ...

    def thumbnail(self, entry: Entry) -> bytes | None:
        """PNG-Vorschau der ersten Seite, ohne das PDF zu lesen; None, wenn keine vorliegt."""
        ...
