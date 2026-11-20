"""Watched-folder delivery for iX1300 front-slot scans."""

import signal
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import current_thread, main_thread
from types import FrameType
from typing import Literal
from uuid import uuid4

from PIL import Image

from scansnap.adapters.ix1300.receiver import AuthorizedSession, Receiver
from scansnap.core.model import Sheet
from scansnap.core.pdf import jpegs_to_pdf


def publish_sheet(folder: Path, sheet: Sheet) -> Path:
    """Publish both sides of a 300 dpi Return Scan in front-to-back order."""
    if sheet.back is None:
        raise ValueError("Return Scan requires both sides")
    pages = [sheet.front, sheet.back]
    return _publish(folder, pages, (sheet.info.dpi_y if sheet.info else 0) or 300, 180)


def publish_stack(folder: Path, sheets: list[Sheet]) -> Path:
    pages: list[bytes] = []
    for sheet in reversed(sheets):
        if sheet.back is None:
            raise ValueError("Top feeder requires both sides")
        pages.extend((sheet.back, sheet.front))
    return _publish(folder, pages, 300, 0)


def _publish(folder: Path, pages: list[bytes], dpi: int, rotation: Literal[0, 180]) -> Path:
    for page in pages:
        with Image.open(BytesIO(page), formats=["JPEG"]) as image:
            image.load()
    pdf = jpegs_to_pdf(pages, dpi, page_rotation=rotation)
    destination = folder / f"scan-{uuid4().hex}.pdf"
    staging = NamedTemporaryFile(dir=folder, prefix=".scan-", suffix=".tmp", delete=False)
    staged = Path(staging.name)
    try:
        with staging:
            staging.write(pdf)
        staged.replace(destination)
    finally:
        staged.unlink(missing_ok=True)
    return destination


def receive_to_folder(
    receiver: Receiver,
    scanner_ip: str,
    host_mac: bytes,
    authorized_session: AuthorizedSession,
    folder: Path,
    *,
    top_feeder: bool = False,
) -> None:
    """Receive scans until stopped, handling process signals on the main thread."""

    def publish(sheets: list[Sheet]) -> Path:
        return publish_stack(folder, sheets) if top_feeder else publish_sheet(folder, sheets[0])

    if current_thread() is not main_thread():
        receiver.run_job(scanner_ip, host_mac, authorized_session, publish, top_feeder=top_feeder)
        return

    def stop(signum: int, frame: FrameType | None) -> None:
        receiver.stop()

    previous: dict[signal.Signals, Callable[[int, FrameType | None], object] | int] = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            handler = signal.getsignal(signum)
            if handler is not None:
                previous[signum] = handler
                signal.signal(signum, stop)
        receiver.run_job(scanner_ip, host_mac, authorized_session, publish, top_feeder=top_feeder)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
