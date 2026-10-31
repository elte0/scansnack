"""UDP notifications from the scanner (48 B): ``u32 0x30 | magic | u32 event | u32 0 | u8 counter at 16``,
scanner IP at 20 and MAC at 24 where the model fills them in.

Layout: docs/protocol.md, section "Scan button on the touch panel"; docs/protocol-ix500.md, "Notifications".
"""

from __future__ import annotations

import ipaddress
import queue
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from . import wire

NOTIFY_LEN = 48
# The scanner sends every notice 3 times 0.5 s apart. Only repeats within this window are dropped: the iX500
# counts from 1 again after power on, so an older notice with the same counter is a new event.
REPEAT_WINDOW = 3.0


@dataclass(frozen=True)
class Notification:
    event: int
    counter: int
    scanner_ip: str
    scanner_mac: str


def parse_notification(data: bytes, *, magic: bytes) -> Notification | None:
    if len(data) < NOTIFY_LEN or data[4:8] != magic or struct.unpack_from(">I", data, 0)[0] != NOTIFY_LEN:
        return None
    event = struct.unpack_from(">I", data, 8)[0]
    return Notification(event, data[16], str(ipaddress.IPv4Address(data[20:24])), data[24:30].hex(":"))


class Listener:
    """Listens on one UDP port and calls ``callback`` once per notification, on a delivery thread of its own
    (a slow callback does not delay receiving, so repeats are still recognized)."""

    def __init__(self, scanner_ip: str, port: int, callback: Callable[[Notification], None], *, magic: bytes):
        self.scanner_ip, self.callback, self.magic = scanner_ip, callback, magic
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(("", port))
        except OSError:
            self.sock.close()
            raise
        self.sock.settimeout(0.2)
        self.port = port
        self._last: tuple[int, int, float] | None = None
        self._stop = threading.Event()
        self._pending: queue.Queue[Notification | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._worker = threading.Thread(target=self._deliver, daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(2048)
            except TimeoutError:
                continue
            except OSError as e:
                if not self._stop.is_set():
                    print(f"UDP {self.port}: listener stopped: {e}", file=sys.stderr)
                return
            if addr[0] != self.scanner_ip:
                continue
            wire.trace("<- udp notify", data)
            note = parse_notification(data, magic=self.magic)
            if note is None:
                continue
            now = time.monotonic()
            if self._last and self._last[:2] == (note.event, note.counter) and now - self._last[2] < REPEAT_WINDOW:
                continue
            self._last = (note.event, note.counter, now)
            self._pending.put(note)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def _deliver(self) -> None:
        while (note := self._pending.get()) is not None:
            try:
                self.callback(note)
            except Exception as e:  # noqa: BLE001 – the listener must keep running
                print(f"Notification {note.event:#04x}: {e}", file=sys.stderr)

    def close(self) -> None:
        self._stop.set()
        self._pending.put(None)
        self._thread.join(2)
        self._worker.join(2)
        self.sock.close()
