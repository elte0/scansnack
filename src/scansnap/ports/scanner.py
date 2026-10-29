"""Scanner-Seite: ein Gerät, das Blätter liefert, und die Suche nach Geräten im Netz."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol

from ..core.model import FoundScanner, ScanSettings, Sheet


class ScannerPort(Protocol):
    """Ein Scanner hinter einer festen Adresse.

    :meth:`scan` blockiert (Netzwerk-I/O) und darf nicht im GUI-Thread laufen. Wirft ``core.errors.NoPaper``
    bei leerem Einzug, ``core.errors.FeedError`` bei Papierfehlern und ``OSError`` bei Verbindungsproblemen.
    """

    @property
    def connected(self) -> bool: ...

    def connect(self) -> None:
        """Dauerhafte Verbindung aufbauen (falls das Gerät eine hat); blockiert."""
        ...

    def disconnect(self) -> None: ...

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        """Alle Blätter im Einzug; ``whiten`` setzt die Papierfarbe auf Weiß."""
        ...


class ScannerLocator(Protocol):
    """Findet Scanner und öffnet sie. Alle Methoden blockieren (Netzwerk-I/O)."""

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]: ...

    def reachable(self, ip: str, timeout: float = 1.5) -> bool: ...

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> ScannerPort:
        """Gerät mit dauerhafter Sitzung; ``on_button`` läuft (in einem Hintergrund-Thread) bei „Scan“ am Gerät."""
        ...
