"""UDP discovery and heartbeat (the scanner listens on 52217).

Probe layout: docs/protocol.md, section "Discovery and heartbeat (UDP 52217)". The replies differ per model and
are parsed in the model's adapter.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Self

from . import wire

DISCOVERY_PORT = 52217
HEARTBEAT_FLAG = 0x1000
HOST_UDP_PORT = 55264  # source port of ScanSnap Home for probes and heartbeat
BROADCAST = "255.255.255.255"


def build_probe(tag: bytes, flag: int, host_ip: str, host_mac: bytes, host_port: int, *, seq: int = 0) -> bytes:
    return (
        struct.pack(">4sI4s6sIHH", tag, seq, ipaddress.IPv4Address(host_ip).packed, host_mac, 0, host_port, flag)
        + b"\0" * 6
    )


def local_ip_for(target: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target if target != BROADCAST else "8.8.8.8", 9))
        return s.getsockname()[0]


def replies(
    probes: Callable[[str, int], Sequence[bytes]], target: str = BROADCAST, timeout: float = 3.0
) -> Iterator[tuple[bytes, str]]:
    """Sends ``probes(host_ip, reply_port)`` to ``target`` and yields ``(data, sender_ip)`` until ``timeout``."""
    host_ip = local_ip_for(target)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        for probe in probes(host_ip, s.getsockname()[1]):
            s.sendto(probe, (target, DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            s.settimeout(remaining)
            try:
                data, addr = s.recvfrom(2048)
            except TimeoutError:
                return
            yield data, addr[0]


class Heartbeat:
    """UDP keepalive like ScanSnap Home: probe with seq 1 and flag 0x1000 to 52217, ``burst`` probes every
    ``interval`` seconds. The scanner answers with 12 bytes ``<magic> 8001 0000 0000``."""

    def __init__(
        self,
        scanner_ip: str,
        host_mac: bytes,
        *,
        magic: bytes,
        host_port: int = HOST_UDP_PORT,
        interval: float = 0.5,
        burst: int = 1,
        spacing: float = 0.5,
    ):
        self.target = (scanner_ip, DISCOVERY_PORT)
        self.magic, self.interval, self.burst, self.spacing = magic, interval, burst, spacing
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", host_port))
        except OSError:
            self.sock.bind(("", 0))
        self.sock.settimeout(0.3)
        self.probe = build_probe(
            magic, HEARTBEAT_FLAG, local_ip_for(scanner_ip), host_mac, self.sock.getsockname()[1], seq=1
        )
        self.replies = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def beat(self) -> None:
        for n in range(self.burst):
            if n and self._stop.wait(self.spacing):  # probes of a burst are ~0.5 s apart
                return
            wire.trace("-> udp hb", self.probe)
            self.sock.sendto(self.probe, self.target)
            try:
                data, addr = self.sock.recvfrom(2048)
            except TimeoutError:
                continue
            if addr[0] == self.target[0] and data[:4] == self.magic:
                self.replies += 1

    def start(self) -> None:
        self._stop.clear()

        def run() -> None:
            while True:
                try:
                    self.beat()
                except OSError:
                    return
                if self._stop.wait(self.interval):
                    return

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(2)
            self._thread = None
        self.sock.close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
