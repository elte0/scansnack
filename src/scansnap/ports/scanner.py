"""Scanner side: a device that delivers sheets, and the search for devices on the network."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Protocol, runtime_checkable

from ..core.model import Decide, FoundScanner, ScanSettings, Sheet
from ..core.report import DeviceDiagnostics


@runtime_checkable
class PaperSensor(Protocol):
    """Optional for a ``ScannerPort``: whether paper is in the feeder, from a status the device polls itself."""

    def paper_loaded(self) -> bool | None:
        """True/False from the last status, None if unknown (not connected, scanning, no answer). Never blocks."""
        ...


@runtime_checkable
class Diagnosable(Protocol):
    """Optional for a ``ScannerPort``: device and connection state for an error report (``core.report``)."""

    def diagnostics(self) -> DeviceDiagnostics:
        """Only what is known already (model, firmware, session state, last step). Never blocks."""
        ...


@runtime_checkable
class PasswordProtected(Protocol):
    """Optional for a ``ScannerPort``: a scanner that wants a password (``core.errors.PasswordRejected``)."""

    def set_password(self, password: str) -> None:
        """Remember ``password`` on this computer; the next :meth:`ScannerPort.connect` uses it. No network I/O."""
        ...


@runtime_checkable
class HoldsBatch(Protocol):
    """Optional for a ``ScannerPort``: after a double feed or a jam the scanner holds the batch until the user
    answers ("Scan" continues, "Finish" ends it) on its touch panel or through ``decide``."""

    def set_fault_handler(self, decide: Decide | None) -> None:
        """``decide`` runs in the scan thread about every 250 ms while a batch is held: it returns an answer, or
        None to keep waiting. Without an answer the batch ends after a few minutes. No network I/O."""
        ...


class ScannerPort(Protocol):
    """A scanner behind a fixed address.

    :meth:`scan` blocks (network I/O) and must not run in the GUI thread. Raises ``core.errors.NoPaper``
    when the feeder is empty, ``core.errors.FeedError`` on paper errors,
    ``core.errors.PasswordRejected`` if the scanner wants a (different) password and ``OSError`` on connection problems.
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

    def identify(self, ip: str, timeout: float = 1.5) -> FoundScanner | None:
        """The ScanSnap at ``ip`` without the network search, None if none answers. Needed because a scanner
        does not answer the search while a host (this app, the vendor software) is connected to it."""
        ...

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> ScannerPort:
        """Device with a persistent session; ``on_button`` runs (in a background thread) on "Scan" at the device."""
        ...
