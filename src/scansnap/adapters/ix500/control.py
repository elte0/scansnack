"""Control channel (TCP 53219): registration (0x11), device name (0x13), Wi-Fi status (0x30).

One TCP connection per command. Layout: docs/protocol-ix500.md, section "Registration (control 0x11)".
"""

from __future__ import annotations

import datetime as dt
import socket
import struct

from ..scansnap_wifi.vens_control import (
    CONTROL_PORT,
    PASSWORD_KEY,
    REGISTER_BODY_LEN,
    SCANNER_TIMEOUT,
    HostIdentity,
    encode_password,
    exchange,
    register_body,
    registration_status,
)

__all__ = [
    "CONTROL_PORT",
    "IN_USE",
    "PASSWORD_KEY",
    "REGISTER_BODY_LEN",
    "REGISTRATION_STATUS",
    "SCANNER_TIMEOUT",
    "Control",
    "HostIdentity",
    "RegistrationError",
    "default_password",
    "encode_password",
    "register_body",
]

IN_USE = -4
# Seen live; UNVERIFIED: the other negative statuses.
REGISTRATION_STATUS = {
    -2: "the scanner wants its Wi-Fi password",
    -3: "wrong Wi-Fi password",
    IN_USE: "the scanner is in use by another computer (over Wi-Fi or USB). "
    "Quit ScanSnap Home there or unplug the scanner's USB cable, then restart the scanner and try again",
    -7: "this host ID is not paired with the scanner (pairing is done over USB)",
}


class RegistrationError(Exception):
    def __init__(self, status: int, holder: str | None = None):
        """``holder``: IP of the host the scanner reports as registered, if known."""
        reason = REGISTRATION_STATUS.get(status, "unknown reason")
        if status == IN_USE and holder:
            reason = reason.replace("another computer", f"another computer ({holder})", 1)
        super().__init__(f"registration rejected, status {status}: {reason}")
        self.status = status
        self.holder = holder


def default_password(serial: str) -> str:
    """Factory Wi-Fi password: the last four digits of the serial number."""
    return serial[-4:]


class Control:
    def __init__(self, ip: str, identity: HostIdentity, port: int = CONTROL_PORT, timeout: float = 10.0):
        self.ip, self.port, self.identity, self.timeout = ip, port, identity, timeout

    def _call(self, command: int, body: bytes = b"") -> bytes:
        return exchange(self.ip, self.port, self.identity.mac, command, body, self.timeout)

    def local_ip(self) -> str:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            return s.getsockname()[0]

    def register(self, host_ip: str, if_version: int, password: str | None, now: dt.datetime | None = None) -> None:
        """0x11: makes this host the target of button notifications. Raises RegistrationError if rejected."""
        frame = self._call(0x11, register_body(self.identity, host_ip, if_version, password, now))
        status = registration_status(frame)
        if status != 0:
            raise RegistrationError(status)

    def device_name(self) -> str:
        frame = self._call(0x13)
        return frame[16:80].split(b"\0", 1)[0].decode("ascii", "replace")

    def wifi_status(self) -> int:
        # UNVERIFIED: 3 observed with the scanner on AC power and connected.
        return struct.unpack_from(">I", self._call(0x30), 16)[0]
