"""Foreground Return Scan lifecycle for an enrolled iX1300."""

import logging
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from threading import Condition
from time import monotonic

from ...core.errors import ScanError
from ...core.model import Sheet
from ..scansnap_wifi.discovery import Heartbeat
from ..scansnap_wifi.notify import Listener, Notification
from ..scansnap_wifi.scsi import ScsiChannel, ScsiError
from ..scansnap_wifi.wire import ProtocolError
from .scanner import ChannelOpener, scan_job

_LOG = logging.getLogger(__name__)
_RETRY = 2.0
_FRESH = 10.0
_STATUS_INTERVAL = 0.31
_OPEN_TIMEOUT = 2.0
_ERRORS = (OSError, ProtocolError, ScsiError, ScanError)


AuthorizedSession = Callable[[float], AbstractContextManager[ChannelOpener]]


class Receiver:
    """One foreground run. A supervisor may call stop from another thread."""

    def __init__(self) -> None:
        self._changed = Condition()
        self._stopped = False
        self._pending: tuple[int, float] | None = None
        self._active: int | None = None
        self._channel: ScsiChannel | None = None

    def stop(self) -> None:
        with self._changed:
            self._stopped = True
            if self._channel is not None:
                self._channel.interrupt()
            self._changed.notify_all()

    def _notice(self, note: Notification) -> None:
        with self._changed:
            if self._stopped or note.event != 1 or note.counter == self._active:
                return
            self._pending = note.counter, monotonic()
            self._changed.notify_all()

    @contextmanager
    def _interruptible(self, open_channel: ChannelOpener, timeout: float) -> Generator[ScsiChannel, None, None]:
        with open_channel(timeout) as channel:
            try:
                with self._changed:
                    self._channel = channel
                    if self._stopped:
                        raise InterruptedError("receiver stopped")
                yield channel
            finally:
                with self._changed:
                    self._channel = None

    def run(
        self,
        scanner_ip: str,
        host_mac: bytes,
        authorized_session: AuthorizedSession,
        publish: Callable[[Sheet], object],
    ) -> None:
        self.run_job(
            scanner_ip, host_mac, authorized_session, lambda _top, sheets: publish(sheets[0]), top_feeder=False
        )

    def run_job(
        self,
        scanner_ip: str,
        host_mac: bytes,
        authorized_session: AuthorizedSession,
        publish: Callable[[bool, list[Sheet]], object],
        *,
        top_feeder: bool | None,
    ) -> None:
        """Bind before opening a session so wake-time notifications can queue."""
        listener = Listener(scanner_ip, 55265, self._notice, magic=b"VENS")
        try:
            while True:
                with self._changed:
                    if self._stopped:
                        return
                    previous = self._pending
                try:
                    with (
                        authorized_session(_OPEN_TIMEOUT) as open_channel,
                        Heartbeat(scanner_ip, host_mac, magic=b"VENS", interval=30, burst=3),
                    ):
                        self._serve(open_channel, publish, top_feeder)
                except _ERRORS as exc:
                    _LOG.warning("Scanner session interrupted: %s", exc)
                    with self._changed:
                        self._changed.wait_for(
                            lambda previous=previous: self._stopped or self._pending != previous, _RETRY
                        )
        finally:
            listener.close()

    def _serve(
        self, open_channel: ChannelOpener, publish: Callable[[bool, list[Sheet]], object], top_feeder: bool | None
    ) -> None:
        def interruptible(timeout: float) -> AbstractContextManager[ScsiChannel]:
            return self._interruptible(open_channel, timeout)

        next_status = monotonic()
        while True:
            with self._changed:
                self._changed.wait_for(
                    lambda: self._stopped or self._pending is not None, max(0, next_status - monotonic())
                )
                if self._stopped:
                    return
                pending, self._pending = self._pending, None
                if pending and monotonic() - pending[1] <= _FRESH:
                    self._active = pending[0]
            if self._active is None:
                with interruptible(_OPEN_TIMEOUT) as status:
                    status.hardware_status(48)
                next_status = monotonic() + _STATUS_INTERVAL
                continue

            release_failed = False

            @contextmanager
            def release(timeout: float) -> Generator[ScsiChannel, None, None]:
                nonlocal release_failed
                try:
                    with open_channel(min(timeout, _OPEN_TIMEOUT)) as channel:
                        yield channel
                except _ERRORS:
                    release_failed = True
                    raise

            try:
                selected_top, sheets = scan_job(interruptible, open_release=release, top_feeder=top_feeder)
                with self._changed:
                    if self._stopped:
                        return
                try:
                    publish(selected_top, sheets)
                except (OSError, ValueError) as exc:
                    _LOG.warning("Scan publication failed: %s", exc)
            finally:
                with self._changed:
                    self._active = None
            if release_failed:
                raise ScsiError("reconnecting after failed release")
