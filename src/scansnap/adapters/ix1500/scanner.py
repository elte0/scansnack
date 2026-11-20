"""Scan sequence D5, D8, E9, D4, C2, 03, (E0, 28 reads, 28 info, C2)*, D6 on the scan channel. After a double
feed (EE + D8) or a jam (D8) the batch is held until the user decides on the touch panel or in the app; it
continues with D8, E9, D4, C2, 03, E0 or ends with D6.

Flow and parameter blocks: docs/protocol-ix1500.md, section "Scan sequence".
"""

from __future__ import annotations

import contextlib
import struct
import time
from collections.abc import Callable, Iterator

from ...core.errors import FeedError, NoPaper
from ...core.model import (
    SCAN_HEIGHT_1200,
    SCAN_WIDTH_1200,
    Decide,
    Fault,
    FaultDecision,
    ImageInfo,
    ScanSettings,
    Sheet,
)
from . import wire
from .scsi import CHECK_CONDITION, ScsiChannel, ScsiError, Sense

# Combinations whose parameter blocks come 1:1 from captures, with the read length from the capture
VERIFIED: dict[tuple[str, int], int] = {("gray", 150): 0x300000, ("color", 600): 0x20000}

_MODE_TEMPLATE = bytes.fromhex(
    "00030101d000c18080808080800000000000000000000000000000000000003000100096009605810500000028d0000044dc04"
).ljust(80, b"\0")


def is_verified(settings: ScanSettings) -> bool:
    return (settings.mode, settings.dpi) in VERIFIED


def read_len(settings: ScanSettings) -> int:
    # UNVERIFIED: 0x20000 for combinations without a capture (taken from color/600)
    return VERIFIED.get((settings.mode, settings.dpi), 0x20000)


def window_block(settings: ScanSettings) -> bytes:
    """E9 data block (32 B)."""
    dpi = settings.dpi
    return (struct.pack(">HHII", dpi, dpi, SCAN_WIDTH_1200, SCAN_HEIGHT_1200) + b"\x05").ljust(32, b"\0")


def mode_block(settings: ScanSettings) -> bytes:
    """D4 data block (80 B)."""
    # UNVERIFIED: derived except for gray/150 and color/600 (byte 13 color, 34–37 dpi, 40 mode);
    # color/300 is accepted live, although ScanSnap Home sends different bytes 10/13/40 there.
    b = bytearray(_MODE_TEMPLATE)
    b[13] = 0x80 if settings.mode == "color" else 0x00
    struct.pack_into(">HH", b, 34, settings.dpi, settings.dpi)
    b[40] = 0x0A if settings.mode == "color" else 0x05
    return bytes(b)


def parse_image_info(d: bytes) -> ImageInfo:
    w, hmax, _w2, h, dx, dy = struct.unpack_from(">IIIIHH", d)
    return ImageInfo(w, hmax, h, dx, dy)


def double_feed(status: bytes) -> int | None:
    """The error code (byte 7) if a C2 status block reports a double feed (byte 6 bit 0), else None.

    With a double feed the sheet's reads end with the normal EOM sense, and C2 afterwards shows the fault.
    Crumpled single sheets set the same flag (session capture).
    """
    # UNVERIFIED: byte 7 has only been seen as 0 or 0x55 (double feed); other codes are not acted on.
    if len(status) < 8 or not status[6] & 0x01:
        return None
    return status[7]


def panel_decision(status: bytes) -> FaultDecision | None:
    """The user's answer on the touch panel to a held batch, read from C2, or None while there is none.

    ScanSnap Home captures: "Scan" (continue) sets byte 4 bit 0 (SANE ``get_GHS_scan_sw``); "Finish" and then
    "do not save the image" sets byte 14 bit 3, "save the image" byte 14 bit 6. "Continue" does not say
    whether to keep a double-fed sheet's image; it is not kept.
    """
    # UNVERIFIED: byte 14 bits 3 and 6 were each seen once, after a double feed; for a jam "Finish" was not tried.
    if len(status) < 15:
        return None
    if status[14] & 0x40:
        return FaultDecision(resume=False, keep=True)
    if status[14] & 0x08:
        return FaultDecision(resume=False, keep=False)
    if status[4] & 0x01:
        # UNVERIFIED: whether ScanSnap Home keeps the double-fed image after "Scan" on the panel.
        return FaultDecision(resume=True, keep=False)
    return None


def _ack_cdb() -> bytes:
    return bytes.fromhex("ee0000000800")


def _sense_error(msg: str, sense: Sense) -> Exception:
    reason = sense.medium_error
    return FeedError(reason, str(sense)) if reason else ScsiError(msg, sense)


def _read_cdb(side: int, chunk: int, length: int) -> bytes:
    return bytes([0x28, 0, 0, 2, 0, 0x80 if side else 0]) + length.to_bytes(3, "big") + bytes([0, side, chunk])


def _info_cdb(side: int) -> bytes:
    return bytes([0x28, 0, 0x80, 0, 0, 0x80 if side else 0, 0, 0, 0x20, 0, side, 0])


class Scanner:
    """Scan sequence on an open scan channel.

    While a batch is held after a paper fault, C2 is polled every ``poll_interval`` seconds for the panel's
    answer (ScanSnap Home keeps its normal 250 ms polling). ``on_hold`` is called once when a hold starts, and
    ``on_hold_tick`` (e.g. 0x0210, as ScanSnap Home sends it while waiting) every ``tick_every`` seconds. After
    ``hold_timeout`` seconds without an answer (None: never) the batch ends as with "Finish" and "do not save".
    """

    def __init__(
        self,
        channel: ScsiChannel,
        *,
        poll_interval: float = 0.25,
        on_hold: Callable[[Fault], None] | None = None,
        on_hold_tick: Callable[[], None] | None = None,
        tick_every: float = 10.0,
        hold_timeout: float | None = 300.0,
    ):
        self.ch = channel
        self.poll_interval, self.on_hold, self.on_hold_tick = poll_interval, on_hold, on_hold_tick
        self.tick_every, self.hold_timeout = tick_every, hold_timeout

    def _hold(self, fault: Fault, decide: Decide | None) -> FaultDecision:
        """Wait for the user's answer, from the touch panel (C2) or from ``decide`` (None means "not yet")."""
        if self.on_hold:
            self.on_hold(fault)
        # UNVERIFIED: the timeout is our choice; the longest captured hold lasted about 33 s, and what the
        # scanner does during a longer one is not known.
        started = ticked = time.monotonic()
        # A panel answer counts only after a poll without one, so that a bit still set from an earlier answer
        # cannot answer this fault. (Whether the scanner clears the bits by itself is not known: in the captures
        # ScanSnap Home sent D8 or D6 right after each answer.)
        armed = False
        while True:
            on_panel = panel_decision(self.ch.hardware_status())
            answer = on_panel if armed else None
            armed = armed or on_panel is None
            if answer is None and decide is not None:
                answer = decide(fault)
            now = time.monotonic()
            if answer is None and self.hold_timeout is not None and now - started >= self.hold_timeout:
                answer = FaultDecision(resume=False, keep=False)
            if answer is not None:
                return answer
            if self.on_hold_tick and now - ticked >= self.tick_every:
                ticked = now
                with contextlib.suppress(Exception):  # like the background 0x0210: a failure does not end the scan
                    self.on_hold_tick()
            time.sleep(self.poll_interval)

    def _start(self, settings: ScanSettings, hold: bool) -> None:
        """D8, E9, D4, (C2,) 03: at the start of a batch and, as ScanSnap Home does, to continue a held one."""
        ch = self.ch
        ch.checked(bytes.fromhex("d80000000000"))
        ch.checked(bytes.fromhex("e9000000000000200000"), out=window_block(settings))
        ch.checked(bytes.fromhex("d40000005000"), out=mode_block(settings))
        if hold:
            # Like ScanSnap Home: C2 once after D4. A double-feed flag from an earlier batch is clear again here.
            ch.hardware_status()
        ch.request_sense()

    def _read_page(self, read_len: int) -> tuple[list[bytes], int]:
        parts: list[list[bytes]] = [[], []]
        done = [False, False]
        chunk = [0, 0]
        last = 0
        while not all(done):
            for side in (0, 1):
                if done[side]:
                    continue
                r = self.ch.command(_read_cdb(side, chunk[side], read_len), in_len=read_len)
                # The sense comes first: when a sheet jams, the read ends with CHECK CONDITION and data that
                # is not an image chunk (private session capture), so the chunk header says nothing.
                sense = self.ch.request_sense() if r.status == CHECK_CONDITION else None
                if sense is not None and not sense.eom:
                    raise _sense_error("read failed", sense)
                if r.status and sense is None:
                    raise ScsiError(f"read status {r.status}")
                if r.data[:2] != bytes([side, chunk[side]]):
                    raise ScsiError(f"unexpected chunk header {r.data[:2].hex()} for side {side}")
                parts[side].append(r.data[2:])
                chunk[side] += 1
                if sense is not None:
                    done[side], last = True, side
        return [b"".join(p) for p in parts], last

    def scan(self, settings: ScanSettings, *, hold: bool = False, decide: Decide | None = None) -> Iterator[Sheet]:
        """Scans all sheets in the feeder. Raises NoPaper if the feeder is empty.

        With ``hold`` (opt-in fix ``core.fixes.IX1500_HOLD_PAPER_FAULTS``), double feeds are detected, and after
        a double feed or a jam in a read the batch is held, as ScanSnap Home does, until the user answers on
        the touch panel or ``decide`` returns an answer. "Continue" goes on with the next sheet: the user puts
        the jammed or double-fed sheets back first. A double-fed sheet's images are delivered with
        ``fault="double feed"`` when the answer says ``keep``. Ending the batch raises FeedError, after the
        double-fed sheet if it is kept.
        """
        ch = self.ch
        ch.checked(bytes.fromhex("d50000010808"), out=b"\0" * 8, in_len=8)
        released = False
        try:
            self._start(settings, hold)
            pages = 0
            while True:
                r = ch.command(bytes.fromhex("e00000000000"))
                if r.status == CHECK_CONDITION:
                    sense = ch.request_sense()
                    if sense.no_paper:
                        if pages == 0:
                            raise NoPaper("no paper in feeder")
                        return
                    # UNVERIFIED: whether ScanSnap Home holds the batch after a jam that E0 reports (not
                    # captured); here it ends, as before.
                    raise _sense_error("feed failed", sense)
                try:
                    (front, back), last = self._read_page(read_len(settings))
                except FeedError as e:
                    if e.reason != "paper jam" or not hold:
                        raise
                    # ScanSnap Home: D8 (no EE), the panel offers "Scan" (continue) and "Finish" (jam capture).
                    try:
                        ch.checked(bytes.fromhex("d80000000000"))
                    except (OSError, wire.ProtocolError, ScsiError):
                        raise e from None  # cannot hold the batch: report the jam
                    if not self._hold(Fault("paper jam", pages + 1), decide).resume:
                        raise
                    self._start(settings, hold)
                    continue
                info = parse_image_info(ch.checked(_info_cdb(last), in_len=0x20).data)
                # Like ScanSnap Home: C2 after the sheet's image info, where a double feed shows up.
                code = double_feed(ch.hardware_status()) if hold else None
                if code is None:
                    pages += 1
                    yield Sheet(front, back if settings.duplex else None, info)
                    continue
                fault = FeedError("double feed", f"sheet {pages + 1}, error code {code:#04x}")
                # ScanSnap Home: EE with 8 bytes (00 00 00 55 00 00 00 00), then D8; the panel asks the user.
                # UNVERIFIED: the EE data layout; also seen: 80 00 ff ff 00 00 00 00 before a new batch.
                try:
                    ch.checked(_ack_cdb(), out=struct.pack(">II", code, 0))
                    ch.checked(bytes.fromhex("d80000000000"))
                except (OSError, wire.ProtocolError, ScsiError):
                    raise fault from None  # cannot hold the batch: report the double feed
                answer = self._hold(Fault("double feed", pages + 1), decide)
                if answer.keep:
                    pages += 1
                    yield Sheet(front, back if settings.duplex else None, info, fault="double feed")
                if not answer.resume:
                    raise fault
                self._start(settings, hold)
        except BaseException:
            # Release the scanner, but report the error that stopped the scan, not a failing D6.
            released = True
            with contextlib.suppress(OSError, wire.ProtocolError):
                ch.command(bytes.fromhex("d60000000000"))
            raise
        finally:
            if not released:
                ch.command(bytes.fromhex("d60000000000"))
