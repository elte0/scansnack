"""Diagnosis of a scanner that is not supported yet."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ..core.diagnostics import DiagnosticReport


class Diagnostics(Protocol):
    def run(self, ip: str, progress: Callable[[str], None] | None = None) -> DiagnosticReport:
        """Asks the scanner at ``ip`` read-only questions of the known protocol and records the raw answers.

        Never changes the scanner (no pairing, no profiles, no scan). Blocks (network I/O) and must not run
        in the GUI thread; ``progress`` gets a short text per step. Connection problems end up in the report,
        not as exceptions. The report is already anonymized.
        """
        ...
