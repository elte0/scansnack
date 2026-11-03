"""Opt-in fixes: new behaviour that users can switch on (menu "Fixes") and test before it becomes the default.

The previous behaviour stays the default until a fix is confirmed; then the fix becomes the only behaviour
and is removed from :data:`AVAILABLE`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Fix:
    id: str  # stable, stored in the settings
    issue: int  # GitHub issue in elte0/scansnack
    summary: str  # the bug in at most five words

    @property
    def label(self) -> str:
        return f"#{self.issue}: {self.summary}"


IX1500_PAIRING_CLEANUP = Fix("ix1500-pairing-cleanup", 19, "Scan hangs on “Processing”")

AVAILABLE: tuple[Fix, ...] = (IX1500_PAIRING_CLEANUP,)


class Fixes:
    """Which fixes are switched on. Shared between the UI and the scanner adapters; switching takes effect
    at the next use, without reconnecting. Unknown IDs (from older settings) are ignored."""

    def __init__(self, enabled: Iterable[str] = ()):
        known = {f.id for f in AVAILABLE}
        self._on = frozenset(i for i in enabled if i in known)

    def enabled(self, fix: Fix) -> bool:
        return fix.id in self._on

    def set(self, fix: Fix, on: bool) -> None:
        # One assignment of a new frozenset: safe to read from the scan thread meanwhile.
        self._on = self._on | {fix.id} if on else self._on - {fix.id}

    @property
    def ids(self) -> list[str]:
        return sorted(self._on)
