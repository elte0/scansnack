"""iX500 discovery (shared probes ``scansnap_wifi.discovery``): only the ``VENS`` probe with flag 0x0010 is
answered, with a 132 B reply. Layout: docs/protocol-ix500.md, section "Discovery and heartbeat".
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass

from ..scansnap_wifi import discovery as _discovery
from ..scansnap_wifi.discovery import BROADCAST, HOST_UDP_PORT, build_probe, local_ip_for
from .wire import MAGIC

__all__ = ["Heartbeat", "ScannerInfo", "discover", "local_ip_for", "parse_reply"]

PROBE_FLAG = 0x0010
REPLY_LEN = 132


@dataclass(frozen=True)
class ScannerInfo:
    ip: str
    data_port: int
    control_port: int
    mac: str
    name: str
    model: str
    if_version: int
    paired_ip: str
    paired_host_id: str
    serial: str


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("ascii", "replace").strip()


def parse_reply(data: bytes, sender: str | None = None) -> ScannerInfo | None:
    """Parses a discovery reply. With ``sender``, a reply whose embedded IP differs is ignored."""
    if len(data) < REPLY_LEN or data[:4] != MAGIC:
        return None
    ip = str(ipaddress.IPv4Address(data[16:20]))
    if sender is not None and ip != sender:
        return None
    data_port, control_port = struct.unpack_from(">II", data, 20)
    name = _cstr(data[40:104])
    return ScannerInfo(
        ip=ip,
        data_port=data_port,
        control_port=control_port,
        mac=data[28:34].hex(":"),
        name=name,
        model=_cstr(data[104:120]),
        if_version=struct.unpack_from(">H", data, 8)[0],
        paired_ip=str(ipaddress.IPv4Address(data[120:124])),
        paired_host_id=data[124:132].hex(),
        serial=name.rpartition("-")[2],  # the name is "iX500-<serial>"
    )


def discover(host_mac: bytes, target: str = BROADCAST, timeout: float = 3.0) -> list[ScannerInfo]:
    """Sends the ``VENS`` probe and collects replies until ``timeout``."""

    def probes(host_ip: str, port: int) -> list[bytes]:
        return [build_probe(MAGIC, PROBE_FLAG, host_ip, host_mac, port)]

    found: dict[str, ScannerInfo] = {}
    for data, sender in _discovery.replies(probes, target, timeout):
        if info := parse_reply(data, sender):
            found[info.ip] = info
    return list(found.values())


class Heartbeat(_discovery.Heartbeat):
    """Like ScanSnap Home with the iX500: three probes every 30 s."""

    def __init__(self, scanner_ip: str, host_mac: bytes, host_port: int = HOST_UDP_PORT, interval: float = 30.0):
        super().__init__(scanner_ip, host_mac, magic=MAGIC, host_port=host_port, interval=interval, burst=3)
