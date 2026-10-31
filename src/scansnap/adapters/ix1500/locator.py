"""``ports.scanner.ScannerLocator`` for the Wi-Fi ScanSnaps (iX1500, iX500 beta): discovery via UDP,
reachability via TCP 53219."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from ...core import catalog
from ...core.model import FoundScanner
from ...ports.scanner import ScannerPort
from .button import Notification
from .control import CONTROL_PORT, HostIdentity
from .device import Device
from .discovery import discover
from .identity import DEFAULT_PATH, load_or_create
from .ix500 import Ix500Device


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

    def open(
        self, ip: str, *, model: str, pairing_key: str = "", on_button: Callable[[], None] | None = None
    ) -> ScannerPort:
        if model == "ix500":
            return Ix500Device(ip, self.identity, key=pairing_key or None)
        if model != "ix1500":
            known = catalog.by_id(model)
            raise ValueError(f"{known.full_name if known else model} is not supported")

        def callback(_note: Notification) -> None:
            if on_button is not None:
                on_button()

        return Device(ip, self.identity, keep_alive=True, on_button=callback if on_button else None)
