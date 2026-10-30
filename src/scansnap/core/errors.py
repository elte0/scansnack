"""Errors of a scan job, as the user interfaces and eSCL see them."""

from __future__ import annotations


class ScanError(Exception):
    pass


class NoPaper(ScanError):
    """Feeder empty before the first sheet was read."""


class FeedError(ScanError):
    """Paper error while feeding or reading (jam, multifeed, cover open …); ``reason`` is plain text."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
