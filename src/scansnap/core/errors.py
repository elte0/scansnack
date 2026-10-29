"""Fehler eines Scan-Auftrags, wie sie Oberflächen und eSCL sehen."""

from __future__ import annotations


class ScanError(Exception):
    pass


class NoPaper(ScanError):
    """Einzug leer, bevor das erste Blatt gelesen wurde."""


class FeedError(ScanError):
    """Papierfehler beim Einzug oder Lesen (Stau, Doppeleinzug, Abdeckung offen …); ``reason`` ist Klartext."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
