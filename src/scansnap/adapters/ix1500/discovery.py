"""UDP-Discovery und Heartbeat (Scanner lauscht auf 52217).

Layout von Probe und Antwort: docs/protocol.md, Abschnitt „Discovery und Heartbeat“.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import threading
import time
from dataclasses import dataclass
from typing import Self

from . import wire
from .wire import MAGIC

DISCOVERY_PORT = 52217
PROBES = ((b"VENS", 0x0010), (MAGIC, 0x0100), (b"V2ss", 0x1000))
HEARTBEAT_FLAG = 0x1000
HOST_UDP_PORT = 55264  # Quellport von ScanSnap Home für Probes und Heartbeat


@dataclass(frozen=True)
class ScannerInfo:
    ip: str
    data_port: int
    control_port: int
    mac: str
    name: str
    model: str
    paired_host_id: str
    serial: str


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def build_probe(tag: bytes, flag: int, host_ip: str, host_mac: bytes, host_port: int, seq: int = 0) -> bytes:
    return (
        struct.pack(">4sI4s6sIHH", tag, seq, ipaddress.IPv4Address(host_ip).packed, host_mac, 0, host_port, flag)
        + b"\0" * 6
    )


def parse_reply(data: bytes) -> ScannerInfo | None:
    if len(data) < 144 or data[:4] != MAGIC:
        return None
    ip = str(ipaddress.IPv4Address(data[16:20]))
    data_port, control_port = struct.unpack_from(">II", data, 20)
    return ScannerInfo(
        ip=ip,
        data_port=data_port,
        control_port=control_port,
        mac=data[28:34].hex(":"),
        name=_cstr(data[40:104]),
        model=_cstr(data[104:120]),
        paired_host_id=data[124:132].hex(),
        serial=_cstr(data[132:144]),
    )


def local_ip_for(target: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((target, 9))
        return s.getsockname()[0]


def discover(
    host_mac: bytes, target: str = "255.255.255.255", timeout: float = 3.0, host_ip: str | None = None
) -> list[ScannerInfo]:
    """Sendet die drei Probe-Varianten und sammelt Antworten bis ``timeout``."""
    host_ip = host_ip or local_ip_for(target if target != "255.255.255.255" else "8.8.8.8")
    found: dict[str, ScannerInfo] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.bind(("", 0))
        port = s.getsockname()[1]
        for tag, flag in PROBES:
            s.sendto(build_probe(tag, flag, host_ip, host_mac, port), (target, DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            s.settimeout(remaining)
            try:
                data, _ = s.recvfrom(2048)
            except TimeoutError:
                break
            if info := parse_reply(data):
                found[info.ip] = info
    return list(found.values())


class Heartbeat:
    """UDP-Keepalive wie ScanSnap Home: ``ssNR`` seq=1, Flag 0x1000 an 52217 alle ~0,5 s.

    Der Scanner antwortet (unzuverlässig) mit 12 Byte ``ssNR 8001 0000 0000…``.
    """

    def __init__(
        self, scanner_ip: str, host_mac: bytes, host_port: int = 0, interval: float = 0.5, host_ip: str | None = None
    ):
        self.target = (scanner_ip, DISCOVERY_PORT)
        self.interval = interval
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", host_port))
        except OSError:
            self.sock.bind(("", 0))
        self.sock.settimeout(0.1)
        self.probe = build_probe(
            MAGIC, HEARTBEAT_FLAG, host_ip or local_ip_for(scanner_ip), host_mac, self.sock.getsockname()[1], seq=1
        )
        self.replies = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self) -> None:
        wire.trace("-> udp hb", self.probe)
        self.sock.sendto(self.probe, self.target)
        deadline = time.monotonic() + min(self.interval, 0.3)
        while time.monotonic() < deadline:
            try:
                data, addr = self.sock.recvfrom(2048)
            except TimeoutError:
                continue
            if addr[0] == self.target[0]:
                wire.trace("<- udp", data)
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

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
            self._thread = None

    def close(self) -> None:
        self.stop()
        self.sock.close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
