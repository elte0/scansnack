"""Persistent host identity (stays the same across restarts so that the scanner recognizes us)."""

from __future__ import annotations

import json
import os
import secrets
import uuid
from pathlib import Path

from .control import HostIdentity

DEFAULT_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "scansnap-bridge" / "identity.json"


def _random_mac() -> bytes:
    b = bytearray(secrets.token_bytes(6))
    b[0] = (b[0] | 0x02) & 0xFE  # locally administered, unicast
    return bytes(b)


def load_or_create(path: Path = DEFAULT_PATH, name: str | None = None) -> HostIdentity:
    if path.exists():
        d = json.loads(path.read_text())
        return HostIdentity(d["name"], d["host_id"], d["user_id"], bytes.fromhex(d["mac"]), d.get("host_port", 55265))
    node = uuid.getnode()
    mac = node.to_bytes(6, "big") if not (node >> 40) & 1 else _random_mac()
    ident = HostIdentity(
        # the scanner rejects "scansnap-bridge (Mac.fritz.box)" in 0x0110 with 0xffffffff
        name=name or "scansnap-bridge",
        host_id=secrets.token_hex(8),
        user_id=uuid.uuid4().hex.upper(),
        mac=mac,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": ident.name,
                "host_id": ident.host_id,
                "user_id": ident.user_id,
                "mac": ident.mac.hex(),
                "host_port": ident.host_port,
            },
            indent=2,
        )
    )
    return ident
