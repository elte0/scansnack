"""Scanner search across models: the iX1500 (``ssNR``) and the iX500 (``VENS``) adapters behind one locator."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Literal

from ..adapters.ix500.locator import Ix500Locator
from ..adapters.ix1500.locator import Ix1500Locator
from ..core.model import FoundScanner
from ..ports.scanner import ScannerPort

Model = Literal["ix500", "ix1500"]
CONTROL_PORT = 53219
MAGICS: dict[bytes, Model] = {b"VENS": "ix500", b"ssNR": "ix1500"}
ATTEMPTS = 3


def greeting_magic(ip: str, timeout: float = 2.0) -> bytes:
    """Magic of the 16-byte greeting every connection to the control port gets (TCP, no UDP needed)."""
    with socket.create_connection((ip, CONTROL_PORT), timeout=timeout) as s:
        head = b""
        while len(head) < 8:
            chunk = s.recv(8 - len(head))
            if not chunk:
                break
            head += chunk
    return head[4:8]


class AutoLocator:
    """Finds both models; :meth:`open` picks the adapter from the greeting magic of the given IP."""

    def __init__(self) -> None:
        self.ix1500 = Ix1500Locator()
        self.ix500 = Ix500Locator()
        self.models: dict[str, Model] = {}

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        found = {s.ip: s for s in self.ix1500.discover(timeout)}
        found.update({s.ip: s for s in self.ix500.discover(timeout)})
        return list(found.values())

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        return self.ix1500.reachable(ip, timeout)

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
