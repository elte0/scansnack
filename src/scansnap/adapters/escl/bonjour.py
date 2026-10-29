"""Bonjour-Anzeige als eSCL-Scanner (``_uscan._tcp``), TXT-Schlüssel wie bei AirScan-Geräten."""

from __future__ import annotations

import socket
from typing import Self

from zeroconf import ServiceInfo, Zeroconf

SERVICE_TYPE = "_uscan._tcp.local."


def txt_record(name: str, uuid: str, admin_url: str = "") -> dict[str, str]:
    return {
        "txtvers": "1",
        "vers": "2.63",
        "rs": "eSCL",
        "ty": name,
        "note": "",
        "pdl": "image/jpeg,application/pdf",
        "uuid": uuid,
        "cs": "color,grayscale",
        "is": "adf",
        "duplex": "T",
        "adminurl": admin_url,
    }


class Advertisement:
    def __init__(self, name: str, ip: str, port: int, uuid: str, admin_url: str = ""):
        host = socket.gethostname().split(".")[0]
        self.info = ServiceInfo(
            SERVICE_TYPE,
            f"{name}.{SERVICE_TYPE}",
            port=port,
            properties=txt_record(name, uuid, admin_url),
            server=f"{host}.local.",
            parsed_addresses=[ip],
        )
        self.zc: Zeroconf | None = None

    def __enter__(self) -> Self:
        self.zc = Zeroconf()
        self.zc.register_service(self.info, allow_name_change=True)
        return self

    def __exit__(self, *exc: object) -> None:
        if self.zc:
            self.zc.unregister_service(self.info)
            self.zc.close()
            self.zc = None
