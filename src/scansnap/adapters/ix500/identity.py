"""Persistent host identity for the iX500 (stays the same across restarts)."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import uuid
from pathlib import Path

from .control import HostIdentity

DEFAULT_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "scansnap-bridge" / "ix500.json"
_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.IGNORECASE)
PLACEHOLDER_MAC = bytes.fromhex("020000000000")


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout


def interface_mac(target_ip: str) -> bytes | None:
    """MAC of the interface that routes to ``target_ip`` (macOS and Linux), or None.

    The iX500 only accepts scan-channel commands whose header carries the MAC that registered (0x11),
    so the client sends the real interface MAC like ScanSnap Home does.
    """
    try:
        if sys.platform == "darwin":
            iface = re.search(r"interface:\s*(\S+)", _run("route", "-n", "get", target_ip))
            mac = iface and _MAC.search(_run("networksetup", "-getmacaddress", iface.group(1)))
        elif sys.platform.startswith("linux"):
            dev = re.search(r"\bdev\s+(\S+)", _run("ip", "route", "get", target_ip))
            mac = dev and _MAC.search(Path(f"/sys/class/net/{dev.group(1)}/address").read_text())
        else:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    if not mac:
        return None
    value = bytes.fromhex(mac.group(1).replace(":", ""))
    return value if value != PLACEHOLDER_MAC else None


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
