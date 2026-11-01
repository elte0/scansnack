"""The shared scan channel (``scansnap_wifi.scsi``) for the iX500: magic ``VENS``, extra header field 0, C2 0x20 B."""

from __future__ import annotations

from ..scansnap_wifi import scsi as _scsi
from ..scansnap_wifi.scsi import CHECK_CONDITION, DATA_PORT, MEDIUM_ERRORS, Response, ScsiError, Sense
from .wire import MAGIC

__all__ = ["CHECK_CONDITION", "DATA_PORT", "MEDIUM_ERRORS", "Response", "ScsiChannel", "ScsiError", "Sense"]

C2_LEN = 0x20


class ScsiChannel(_scsi.ScsiChannel):
    def __init__(self, ip: str, mac: bytes, port: int = DATA_PORT, timeout: float = 60.0):
        super().__init__(ip, mac, magic=MAGIC, port=port, timeout=timeout)

    def hardware_status(self, length: int = C2_LEN) -> bytes:
        return super().hardware_status(length)
