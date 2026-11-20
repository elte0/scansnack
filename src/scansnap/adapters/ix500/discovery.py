"""iX500 discovery (shared probes ``scansnap_wifi.discovery``): only the ``VENS`` probe with flag 0x0010 is
answered, with a 132 B reply. Layout: docs/protocol-ix500.md, section "Discovery and heartbeat".
"""

from __future__ import annotations

from ..scansnap_wifi import discovery as _discovery
from ..scansnap_wifi.discovery import BROADCAST, HOST_UDP_PORT, build_probe, local_ip_for
from ..scansnap_wifi.discovery import ScannerInfo as ScannerInfo
from ..scansnap_wifi.discovery import parse_vens_reply as parse_reply
from .wire import MAGIC

__all__ = ["Heartbeat", "ScannerInfo", "discover", "local_ip_for", "parse_reply"]

PROBE_FLAG = 0x0010
REPLY_LEN = 132


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
