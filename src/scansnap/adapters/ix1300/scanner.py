"""One front-slot sheet using channels supplied by an enrolled session."""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

from ...core.errors import NoPaper
from ...core.model import Sheet
from ..scansnap_wifi.images import info_cdb, parse_image_info, read_image, sense_error
from ..scansnap_wifi.scsi import CHECK_CONDITION, ScsiChannel, ScsiError
from ..scansnap_wifi.wire import ProtocolError

_WINDOW = bytes.fromhex("012c012c00002880000044100500000000000000000000000000000000000000")
_MODE = bytes.fromhex(
    "000601010000c180808090808000000000000000000000000000000030000030"
    "0010012c012c05820d0000002880000044100400000000000000000000000100"
    "00000000000000000000000000000000"
)


_TOP_WINDOW = bytes.fromhex("012c012c00002880000043500500000000000000000000000000000000000000")
_TOP_MODE = bytes.fromhex(
    "000301010000c180808090808000000000000000000000000000000000000030"
    "0010012c012c05820d0000002880000043500400000000000000000000000100"
    "00000000000000000000000000000000"
)


def _feed(channel: ScsiChannel) -> None:
    result = channel.command(bytes.fromhex("e00000000000"))
    if result.status == CHECK_CONDITION:
        sense = channel.request_sense()
        if sense.no_paper:
            raise NoPaper("feeder empty")
        raise sense_error("feed failed", sense)
    if result.status:
        raise ScsiError(f"feed status {result.status}")


ChannelOpener = Callable[[float], AbstractContextManager[ScsiChannel]]


def scan_sheet(open_channel: ChannelOpener, *, open_release: ChannelOpener | None = None) -> Sheet:
    return scan_job(open_channel, open_release=open_release, top_feeder=False)[1][0]


def scan_job(
    open_channel: ChannelOpener, *, open_release: ChannelOpener | None = None, top_feeder: bool | None
) -> tuple[bool, list[Sheet]]:
    """Consume one scan attempt, releasing even when setup or transfer fails."""
    reserved = False
    try:
        with open_channel(10) as setup:
            status = setup.hardware_status(48)
            if top_feeder is None:
                state = status[3]
                if state == 0x80:
                    raise NoPaper("both feeders empty")
                if state not in (0x00, 0x81):
                    raise ScsiError(f"unknown feeder status {state:#04x}")
                top_feeder = state == 0x00
            logging.getLogger(__name__).info("Selected %s", "top feeder" if top_feeder else "front slot")
            reserved = True
            setup.checked(bytes.fromhex("d50000000808"), out=bytes(8), in_len=8)
            setup.hardware_status(48)
        with open_channel(45) as transfer:
            transfer.checked(bytes.fromhex("d80000000000"))
            transfer.checked(bytes.fromhex("e9000000000000200000"), out=_TOP_WINDOW if top_feeder else _WINDOW)
            transfer.checked(bytes.fromhex("d40000005000"), out=_TOP_MODE if top_feeder else _MODE)
            transfer.hardware_status(48)
            transfer.request_sense()
            sheets: list[Sheet] = []
            while True:
                try:
                    _feed(transfer)
                except NoPaper:
                    if sheets:
                        return top_feeder, sheets
                    raise
                if sheets and not top_feeder:
                    raise ScsiError("expected empty feeder after one Return Scan sheet")
                index = len(sheets) * 2
                front = read_image(transfer, False, index)
                back = read_image(transfer, True, index + 1)
                info = parse_image_info(transfer.checked(info_cdb(index + 1), in_len=32).data)
                transfer.hardware_status(48)
                sheets.append(Sheet(front, back, info))
    finally:
        if reserved:
            try:
                with (open_release or open_channel)(5) as release:
                    release.checked(bytes.fromhex("d60000000000"))
            except (OSError, ScsiError, ProtocolError) as exc:
                logging.getLogger(__name__).warning("Scanner release failed: %s", exc)
