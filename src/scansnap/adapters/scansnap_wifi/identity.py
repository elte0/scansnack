"""Interface identity for VENS scanners."""

import re
import subprocess
import sys
from pathlib import Path

_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.IGNORECASE)
PLACEHOLDER_MAC = bytes.fromhex("020000000000")


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False).stdout


def interface_mac(target_ip: str) -> bytes | None:
    """MAC of the interface that routes to ``target_ip`` (macOS and Linux), or None.

    VENS scan channels use the same interface MAC as the control registration.
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
