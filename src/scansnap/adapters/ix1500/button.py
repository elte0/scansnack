"""Scan button on the touch panel: UDP notification to ``host_port`` of the selected host.

Layout: docs/protocol-ix1500.md, section "Scan button on the touch panel". Only the UDP notification is evaluated
(C2 can miss the short pulse).
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..scansnap_wifi.notify import REPEAT_WINDOW
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


# UNVERIFIED: how long the panel waits for the host after "Scan"; about 32 s and about 10 s were seen live with
# no session.


class ButtonListener:
    """Listens on UDP ``port`` and calls ``callback`` once per button press (in the listener thread)."""

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
        self._last: tuple[int, float] | None = None
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
            if note is None:
                continue
            if not self._accept(note.counter, time.monotonic()):
                continue
            try:
                self.callback(note)
            except Exception as e:  # noqa: BLE001 – the listener must keep running
                print(f"Scan button: {e}", file=sys.stderr)
            self._handled(note.counter, time.monotonic())

    def _accept(self, counter: int, now: float) -> bool:
        """False for a repeat of the last press: same counter within ``REPEAT_WINDOW``. The counter restarts
        after a power cycle, so an older notification with the same counter is a new press."""
        if self._last and self._last[0] == counter and now - self._last[1] < REPEAT_WINDOW:
            return False
        self._last = (counter, now)
        return True

    def _handled(self, counter: int, now: float) -> None:
        """Start the window again when the callback returns: repeats that queued up during a slow callback are
        read only now."""
        self._last = (counter, now)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.sock.close()
