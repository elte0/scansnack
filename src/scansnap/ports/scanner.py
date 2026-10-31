"""Scanner side: a device that delivers sheets, and the search for devices on the network."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol

from ..core.model import FoundScanner, ScanSettings, Sheet


class ScannerPort(Protocol):
    """A scanner behind a fixed address.

    :meth:`scan` blocks (network I/O) and must not run in the GUI thread. Raises ``core.errors.NoPaper``
    when the feeder is empty, ``core.errors.FeedError`` on paper errors and ``OSError`` on connection problems.
    """

    @property
    def connected(self) -> bool: ...

    def connect(self) -> None:
        """Open a persistent connection (if the device has one); blocks."""
        ...

    def disconnect(self) -> None: ...

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        """All sheets in the feeder; ``whiten`` sets the paper color to white."""
        ...


class ScannerLocator(Protocol):
    """Finds scanners and opens them. All methods block (network I/O)."""

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]: ...

    def reachable(self, ip: str, timeout: float = 1.5) -> bool: ...

    def open(
        self, ip: str, *, model: str, pairing_key: str = "", on_button: Callable[[], None] | None = None
    ) -> ScannerPort:
        """Device of the given model (``core.catalog`` ID). ``pairing_key`` is needed by models that register with
        a secret key (iX500). ``on_button`` runs (in a background thread) on "Scan" at the device, where supported.
        Raises ``ValueError`` for models that are not supported."""
        ...
