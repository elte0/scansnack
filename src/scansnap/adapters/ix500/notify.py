"""iX500 notifications (shared listener ``scansnap_wifi.notify``): Scan button and lid closed to the registered
host on 55265, power on as broadcast and to the registered host on 53220.

Layout: docs/protocol-ix500.md, section "Notifications (UDP, 48 B)".
"""

from __future__ import annotations

from collections.abc import Callable

from ..scansnap_wifi import notify as _notify
from ..scansnap_wifi.notify import Notification
from .wire import MAGIC

__all__ = ["BUTTON", "POWER_OFF", "POWER_ON", "STARTUP_PORT", "Listener", "Notification", "parse_notification"]

STARTUP_PORT = 53220  # "Port number for startup notice" in ScanSnap Home

BUTTON = 0x01
POWER_OFF = 0x10
POWER_ON = 0x21


def parse_notification(data: bytes) -> Notification | None:
    return _notify.parse_notification(data, magic=MAGIC)


class Listener(_notify.Listener):
    def __init__(self, scanner_ip: str, port: int, callback: Callable[[Notification], None]):
        super().__init__(scanner_ip, port, callback, magic=MAGIC)
