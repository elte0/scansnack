"""All ScanSnap models and how far ScanSnack supports them.

``docs/models.md`` shows the same list as a table; a test keeps both in sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Support = Literal["supported", "beta", "unsupported"]

SUPPORT_LABELS: dict[Support, str] = {"supported": "Supported", "beta": "Beta", "unsupported": "Not supported"}


@dataclass(frozen=True)
class ScannerModel:
    id: str  # stable key, e.g. for settings and the command line
    name: str  # as the scanner reports it (without "ScanSnap")
    connection: str  # "Wi-Fi + USB", "USB", "Ethernet"
    support: Support
    note: str = ""
    needs_pairing_key: bool = False  # registration needs a secret key (iX500)

    @property
    def usable(self) -> bool:
        return self.support != "unsupported"

    @property
    def full_name(self) -> str:
        return f"ScanSnap {self.name}"

    @property
    def wifi(self) -> bool:
        return "Wi-Fi" in self.connection


_WIFI_UNKNOWN = "Wi-Fi protocol not examined yet; captures welcome."
_USB_ONLY = "USB only; ScanSnack talks to scanners over the network."

# Newest generation first.
MODELS: tuple[ScannerModel, ...] = (
    ScannerModel("ix2500", "iX2500", "Wi-Fi + USB", "unsupported", _WIFI_UNKNOWN),
    ScannerModel("ix1600", "iX1600", "Wi-Fi + USB", "unsupported", _WIFI_UNKNOWN),
    ScannerModel("ix1500", "iX1500", "Wi-Fi + USB", "supported", "Developed and tested on this model."),
    ScannerModel("ix1400", "iX1400", "USB", "unsupported", _USB_ONLY),
    ScannerModel("ix1300", "iX1300", "Wi-Fi + USB", "unsupported", _WIFI_UNKNOWN),
    ScannerModel(
        "ix500",
        "iX500",
        "Wi-Fi + USB",
        "beta",
        "Based on a third-party protocol write-up, not yet tested by us. Needs the pairing key.",
        needs_pairing_key=True,
    ),
    ScannerModel("ix100", "iX100", "Wi-Fi + USB", "unsupported", _WIFI_UNKNOWN),
    ScannerModel("sv600", "SV600", "USB", "unsupported", _USB_ONLY),
    ScannerModel("n1800", "N1800", "Ethernet", "unsupported", "Network protocol not examined yet."),
    ScannerModel("s1500", "S1500", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s1300i", "S1300i", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s1300", "S1300", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s1100i", "S1100i", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s1100", "S1100", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s510", "S510", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s500", "S500", "USB", "unsupported", _USB_ONLY),
    ScannerModel("s300", "S300", "USB", "unsupported", _USB_ONLY),
    ScannerModel("fi-5110eox", "fi-5110EOX", "USB", "unsupported", _USB_ONLY),
    ScannerModel("fi-4110eox", "fi-4110EOX", "USB", "unsupported", _USB_ONLY),
)

DEFAULT_MODEL = "ix1500"  # the only model before the model choice existed


def by_id(model_id: str) -> ScannerModel | None:
    return next((m for m in MODELS if m.id == model_id), None)


def match(reported: str) -> ScannerModel | None:
    """Model for the name a scanner reports in discovery, e.g. ``"ScanSnap iX1500 "`` or ``"iX500-A0B1C2"``."""
    text = reported.strip().lower().removeprefix("scansnap").strip()
    for candidate in (text, text.split("-", 1)[0]):  # device names carry the serial: "iX500-A0B1C2"
        if found := next((m for m in MODELS if m.name.lower() == candidate), None):
            return found
    return None
