"""``ports.scanner.ScannerLocator`` for the iX500: discovery via UDP (``VENS``), reachability via TCP 53219."""

from __future__ import annotations

import socket
from collections.abc import Callable
from pathlib import Path

from ...core.model import FoundScanner
from .control import CONTROL_PORT
from .device import Device
from .discovery import discover
from .identity import DEFAULT_PATH, local_mac


class Ix500Locator:
    def __init__(self, identity_path: Path = DEFAULT_PATH):
        self.identity_path = identity_path

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        return [FoundScanner(s.ip, s.name, s.model, s.serial) for s in discover(local_mac(), timeout=timeout)]

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        try:
            with socket.create_connection((ip, CONTROL_PORT), timeout=timeout):
                return True
        except OSError:
            return False

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> Device:
        return Device(ip, self.identity_path, on_button=on_button)
