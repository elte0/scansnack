"""VENS image reads and per-sheet metadata."""

import struct

from ...core.errors import FeedError
from ...core.model import ImageInfo
from .scsi import CHECK_CONDITION, ScsiChannel, ScsiError, Sense
from .wire import ProtocolError

READ_LEN = 0x300000  # as ScanSnap Home sends it
MAX_IMAGE = 64 << 20  # one side at 600 dpi color is a few MB; more means the scanner never ended the image


def parse_image_info(d: bytes) -> ImageInfo:
    if len(d) < 20:
        raise ProtocolError(f"short image info {d.hex()}")
    w, hmax, _w2, h, dx, dy = struct.unpack_from(">IIIIHH", d)
    return ImageInfo(w, hmax, h, dx, dy)


def sense_error(msg: str, sense: Sense) -> Exception:
    reason = sense.medium_error
    return FeedError(reason, str(sense)) if reason else ScsiError(msg, sense)


def _read_cdb(back: bool, index: int) -> bytes:
    return bytes([0x28, 0, 0, 2, 0, 0x80 if back else 0]) + READ_LEN.to_bytes(3, "big") + bytes([0, index & 0xFF, 0])


def info_cdb(index: int) -> bytes:
    return bytes([0x28, 0, 0x80, 0, 0, 0x80, 0, 0, 0x20, 0, index & 0xFF, 0])


def read_image(channel: ScsiChannel, back: bool, index: int, max_size: int = MAX_IMAGE) -> bytes:
    parts: list[bytes] = []
    for chunk in range(256):
        r = channel.command(_read_cdb(back, index), in_len=READ_LEN)
        sense = channel.request_sense() if r.status == CHECK_CONDITION else None
        if sense is not None and not sense.eom:
            raise sense_error("read failed", sense)  # the paper error, before any header check
        if r.status not in (0, CHECK_CONDITION):
            raise ScsiError(f"read status {r.status}")
        # UNVERIFIED: the one-byte image index wraps after 128 sheets in one job (never observed)
        if r.data[:2] != bytes([index & 0xFF, chunk]):
            raise ScsiError(f"unexpected image header {r.data[:2].hex()} for image {index}")
        parts.append(r.data[2:])
        if sum(map(len, parts)) > max_size:
            raise ScsiError(f"image {index} larger than {max_size >> 20} MB without an end")
        if sense is not None:  # end of image
            return b"".join(parts)
        # UNVERIFIED: images above READ_LEN continue with the same CDB (never seen on the iX500).
    raise ScsiError(f"image {index} has more than 256 chunks")
