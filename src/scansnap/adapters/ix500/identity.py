"""Persistent host identity for the iX500 (stays the same across restarts)."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from pathlib import Path

from ..scansnap_wifi.identity import PLACEHOLDER_MAC
from ..scansnap_wifi.identity import interface_mac as interface_mac
from .control import HostIdentity

DEFAULT_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "scansnap-bridge" / "ix500.json"


def local_mac(target_ip: str | None = None) -> bytes:
    """Interface MAC towards ``target_ip`` if it can be found, else this machine's MAC, else a random one."""
    if target_ip and (mac := interface_mac(target_ip)):
        return mac
    node = uuid.getnode().to_bytes(6, "big")
    if not node[0] & 1 and node != PLACEHOLDER_MAC:  # a real MAC, not uuid's random fallback
        return node
    b = bytearray(secrets.token_bytes(6))
    b[0] = (b[0] | 0x02) & 0xFE  # locally administered, unicast
    return bytes(b)


def load_or_create(path: Path = DEFAULT_PATH, host_id: str | None = None, mac: bytes | None = None) -> HostIdentity:
    """Loads the identity, or creates one with ``host_id`` (hex) or a random host ID.

    The iX500 keeps a table of paired host IDs that is only written over USB. Reusing the host ID the scanner
    reports as paired (discovery) lets this client take over from ScanSnap Home on the same scanner.
    A changed scanner password can be added to the file as ``"password"``.
    """
    if path.exists():
        d = json.loads(path.read_text())
        return HostIdentity(
            bytes.fromhex(d["host_id"]), bytes.fromhex(d["mac"]), d.get("host_port", 55265), d.get("password")
        )
    ident = HostIdentity(bytes.fromhex(host_id) if host_id else secrets.token_bytes(8), mac or local_mac())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"host_id": ident.host_id.hex(), "mac": ident.mac.hex(), "host_port": ident.host_port}, f, indent=2)
    return ident
