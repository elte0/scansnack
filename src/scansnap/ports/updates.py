"""Updates of the desktop app: where new versions come from and how they are installed."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..core.update import Asset, Release


class ReleaseSource(Protocol):
    def latest(self) -> Release | None:
        """Latest published release (no drafts, no pre-releases); None if there is none.
        Network error: ``OSError``, unreadable response: ``ValueError``."""
        ...


class Installer(Protocol):
    """Replaces the running app with a new version and restarts it."""

    def blocker(self) -> str | None:
        """Why a one-click install is not possible here (for the UI), otherwise None."""
        ...

    def prepare(self, asset: Asset, progress: Callable[[int, int], None]) -> Path:
        """Downloads the package, checks the SHA-256 sum and unpacks it next to the installed app.
        ``progress(done, total)`` is called from the calling thread. Result: the unpacked new app.
        Errors: ``OSError``, ``ValueError`` (also on a wrong checksum)."""
        ...

    def restart_into(self, staged: Path) -> None:
        """Starts a helper that waits for this process to end, replaces the app and starts the new one.
        The app must quit itself afterwards."""
        ...
