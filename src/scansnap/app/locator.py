"""Scanner search across models: the iX1500 (``ssNR``) and the iX500 (``VENS``) adapters behind one locator."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from ..adapters.ix500.locator import Ix500Locator
from ..adapters.ix1500.locator import Ix1500Locator
from ..adapters.scansnap_wifi.wire import SSNR, VENS, greeting_magic
from ..core.fixes import Fixes
from ..core.model import FoundScanner
from ..ports.scanner import ScannerPort

Model = Literal["ix500", "ix1500"]
MAGICS: dict[bytes, Model] = {VENS: "ix500", SSNR: "ix1500"}
NAMES: dict[Model, str] = {"ix500": "ScanSnap iX500", "ix1500": "ScanSnap iX1500"}
ATTEMPTS = 3


class AutoLocator:
    """Finds both models; :meth:`open` picks the adapter from the greeting magic of the given IP."""

    def __init__(self, fixes: Fixes | None = None) -> None:
        self.ix1500 = Ix1500Locator(fixes=fixes)
        self.ix500 = Ix500Locator()
        self.models: dict[str, Model] = {}

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        found = {s.ip: s for s in self.ix1500.discover(timeout)}
        found.update({s.ip: s for s in self.ix500.discover(timeout)})
        return list(found.values())

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        return self.ix1500.reachable(ip, timeout)

    def identify(self, ip: str, timeout: float = 1.5) -> FoundScanner | None:
        """One greeting instead of asking both adapters (no UDP: see ``ports.scanner.ScannerLocator``)."""
        try:
            model = MAGICS.get(greeting_magic(ip, timeout=timeout))
        except OSError:
            return None
        if model is None:
            return None
        self.models[ip] = model
        return FoundScanner(ip, NAMES[model], NAMES[model])

    def model(self, ip: str) -> Model:
        """Model at ``ip`` (remembered per IP). Raises ConnectionError if no ScanSnap answers."""
        if ip in self.models:
            return self.models[ip]
        error: Exception | None = None
        for _ in range(ATTEMPTS):
            try:
                magic = greeting_magic(ip)
            except OSError as e:
                error = e
                continue
            if model := MAGICS.get(magic):
                self.models[ip] = model
                return model
            error = ConnectionError(f"unknown greeting {magic!r}")
        raise ConnectionError(f"no ScanSnap answers at {ip}: {error}")

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> ScannerPort:
        if self.model(ip) == "ix500":
            return self.ix500.open(ip, on_button=on_button)
        return self.ix1500.open(ip, on_button=on_button)
