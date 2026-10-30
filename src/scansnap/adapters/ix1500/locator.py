"""``ports.scanner.ScannerLocator`` for the iX1500: discovery via UDP, reachability via TCP 53219."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from ...core.model import FoundScanner
from .button import Notification
from .control import CONTROL_PORT, HostIdentity
from .device import Device
from .discovery import discover
from .identity import DEFAULT_PATH, load_or_create


class Ix1500Locator:
    """The host identity is only loaded or created when needed."""

    def __init__(self, identity_path: Path = DEFAULT_PATH, name: str | None = None):
        self.identity_path, self.name = identity_path, name
        self._ident: HostIdentity | None = None

    @property
    def identity(self) -> HostIdentity:
        if self._ident is None:
            self._ident = load_or_create(self.identity_path, self.name)
        return self._ident

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        return [FoundScanner(s.ip, s.name, s.model, s.serial) for s in discover(self.identity.mac, timeout=timeout)]

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((ip, CONTROL_PORT), timeout=timeout):
                return True
        except OSError:
            return False

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> Device:
        def callback(_note: Notification) -> None:
            if on_button is not None:
                on_button()

        return Device(ip, self.identity, keep_alive=True, on_button=callback if on_button else None)
