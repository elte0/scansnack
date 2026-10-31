"""Read-only device information over the scan channel (docs/protocol-ix500.md, "Device info queries")."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .scsi import ScsiChannel
from .wire import ProtocolError


def _block(ch: ScsiChannel, cdb: str, length: int, need: int) -> bytes:
    data = ch.checked(bytes.fromhex(cdb), in_len=length).data
    if len(data) < need:
        raise ProtocolError(f"short reply to {cdb[:2]}: {data.hex()}")
    return data


@dataclass(frozen=True)
class DeviceInfo:
    vendor: str
    product: str
    firmware: str
    serial: str
    total_pages: int
    # UNVERIFIED: the second and third counters are probably the pick and brake roller counts.
    roller_counts: tuple[int, int]
    power_off_minutes: int  # UNVERIFIED: byte 1 of the E6 power-off block (0x1e = 30 observed)


def read_info(ch: ScsiChannel) -> DeviceInfo:
    vendor, product, firmware = ch.inquiry()
    serial_block = _block(ch, "e70001000000000c0000", 0x0C, 12)
    number = struct.unpack_from(">I", serial_block, 0)[0]
    # 00003039 00004130 58580000 → "A0" "XX" around zero padding, then the number: A0XX012345
    prefix = serial_block[4:12].replace(b"\0", b"").decode("ascii", "replace")
    serial = f"{prefix}{number:06d}"
    counters = _block(ch, "d90000002000", 0x20, 12)
    total, pick, brake = struct.unpack_from(">III", counters, 0)
    power = _block(ch, "e600010000000004", 4, 2)
    return DeviceInfo(vendor, product, firmware, serial, total, (pick, brake), power[1])
