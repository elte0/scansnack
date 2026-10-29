"""Updates der Desktop-App: wo neue Versionen herkommen und wie sie installiert werden."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..core.update import Asset, Release


class ReleaseSource(Protocol):
    def latest(self) -> Release | None:
        """Neuestes veröffentlichtes Release (keine Entwürfe, keine Vorabversionen); None, wenn es keins gibt.
        Netzfehler: ``OSError``, unlesbare Antwort: ``ValueError``."""
        ...


class Installer(Protocol):
    """Ersetzt die laufende App durch eine neue Version und startet sie neu."""

    def blocker(self) -> str | None:
        """Warum hier nicht mit einem Klick installiert werden kann (für die Oberfläche), sonst None."""
        ...

    def prepare(self, asset: Asset, progress: Callable[[int, int], None]) -> Path:
        """Lädt das Paket, prüft die SHA-256-Summe und entpackt es neben die installierte App.
        ``progress(geladen, gesamt)`` kommt aus dem aufrufenden Thread. Ergebnis: die entpackte neue App.
        Fehler: ``OSError``, ``ValueError`` (auch bei falscher Prüfsumme)."""
        ...

    def restart_into(self, staged: Path) -> None:
        """Startet einen Helfer, der auf das Ende dieses Prozesses wartet, die App austauscht und die neue
        startet. Danach muss sich die App selbst beenden."""
        ...
