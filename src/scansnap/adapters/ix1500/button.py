"""Scan-Taste am Touchpanel: UDP-Meldung an ``host_port`` des ausgewählten Hosts.

Layout: docs/protocol.md, Abschnitt „Scan button on the touch panel“. Ausgewertet wird nur die UDP-Meldung
(C2 kann den kurzen Puls verpassen).
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from . import wire
from .wire import MAGIC

NOTIFY_LEN = 48


@dataclass(frozen=True)
class Notification:
    counter: int
    scanner_ip: str
    scanner_mac: str


def parse_notification(data: bytes) -> Notification | None:
    if len(data) < NOTIFY_LEN or data[4:8] != MAGIC or struct.unpack_from(">I", data, 8)[0] != 1:
        return None
    return Notification(data[16], str(ipaddress.IPv4Address(data[20:24])), data[24:30].hex(":"))


# UNVERIFIED: wie lange das Panel nach „Scan“ auf den Host wartet, ist unbekannt.


class ButtonListener:
    """Lauscht auf UDP ``port`` und ruft ``callback`` einmal je Tastendruck auf (im Listener-Thread)."""

    def __init__(self, scanner_ip: str, port: int, callback: Callable[[Notification], None]):
        self.scanner_ip, self.callback = scanner_ip, callback
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", port))
        except OSError:
            self.sock.close()
            raise
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self._last: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError:
                return
            if addr[0] != self.scanner_ip:
                continue
            wire.trace("<- udp notify", data)
            note = parse_notification(data)
            if note is None or note.counter == self._last:  # Wiederholung desselben Drucks
                continue
            self._last = note.counter
            try:
                self.callback(note)
            except Exception as e:  # noqa: BLE001 – Listener soll weiterlaufen
                print(f"Scan-Taste: {e}", file=sys.stderr)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.sock.close()
