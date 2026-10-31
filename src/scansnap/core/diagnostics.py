"""Diagnostic report for scanner models that are not supported yet.

The report records what a scanner answers to the known protocol (raw bytes per exchange), so that support
for another model can be developed from it. Before it leaves the computer, known identifying values
(IP and MAC addresses, serial number, device name) are replaced by placeholders of the same length,
like ``tools/make_fixtures.py`` does for captures.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field, replace
from urllib.parse import quote

REPORT_EMAIL = "leander.tolksdorf@posteo.de"
FORMAT = "scansnack-diagnostics/1"


@dataclass(frozen=True)
class Exchange:
    """One step of the diagnosis: what was sent, what came back, and how it ended."""

    step: str
    sent: bytes = b""
    received: bytes = b""
    outcome: str = ""


@dataclass(frozen=True)
class Redaction:
    """A value that must not leave the computer, and its replacement of the same length."""

    label: str
    real: bytes
    fake: bytes

    def __post_init__(self) -> None:
        if len(self.real) != len(self.fake):
            raise ValueError(f"replacement for {self.label} must have the same length")


def text_redaction(label: str, value: str) -> Redaction:
    """Placeholder from the label, padded with ``x``: ``serial`` → ``SERIALxxxxxx``."""
    real = value.encode()
    tag = re.sub(r"[^A-Z0-9]", "", label.upper()).encode()
    return Redaction(label, real, (tag + b"x" * len(real))[: len(real)])


@dataclass(frozen=True)
class DiagnosticReport:
    app_version: str
    platform: str
    summary: dict[str, str]
    exchanges: list[Exchange]
    created: str = field(default_factory=lambda: dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    redacted: list[str] = field(default_factory=list[str])
    model: str = ""  # as entered by the user, e.g. "iX500"
    notes: str = ""

    def anonymized(self, redactions: list[Redaction]) -> DiagnosticReport:
        """Replaces every redaction in the raw bytes, outcomes and summary; longest values first, so that a
        value containing another one (device name with serial number) is replaced as a whole."""
        ordered = sorted((r for r in redactions if r.real), key=lambda r: len(r.real), reverse=True)

        def raw(data: bytes) -> bytes:
            for r in ordered:
                data = data.replace(r.real, r.fake)
            return data

        def text(s: str) -> str:
            return raw(s.encode("utf-8", "surrogateescape")).decode("utf-8", "surrogateescape")

        return replace(
            self,
            summary={k: text(v) for k, v in self.summary.items()},
            exchanges=[Exchange(text(e.step), raw(e.sent), raw(e.received), text(e.outcome)) for e in self.exchanges],
            redacted=sorted({*self.redacted, *(r.label for r in ordered)}),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "format": FORMAT,
                "created": self.created,
                "app_version": self.app_version,
                "platform": self.platform,
                "model": self.model,
                "notes": self.notes,
                "summary": self.summary,
                "redacted": self.redacted,
                "exchanges": [
                    {"step": e.step, "outcome": e.outcome, "sent": e.sent.hex(), "received": e.received.hex()}
                    for e in self.exchanges
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

    def file_name(self) -> str:
        model = re.sub(r"[^A-Za-z0-9]+", "-", self.model or self.summary.get("model", "")).strip("-")
        stamp = self.created[:10]
        return f"scansnack-diagnostics-{model or 'scanner'}-{stamp}.json"


def mail_url(report: DiagnosticReport, attachment: str, to: str = REPORT_EMAIL) -> str:
    """``mailto:`` link with subject and a body that asks to attach the saved report (mailto cannot attach)."""
    model = report.model or report.summary.get("model", "") or "unknown model"
    body = (
        f"Hello,\n\nhere is a diagnostic report for my {model}.\n\n"
        f"Please attach the report file before sending: {attachment}\n\n"
        f"App version: {report.app_version}\nSystem: {report.platform}\n"
    )
    if report.notes:
        body += f"\nNotes:\n{report.notes}\n"
    return f"mailto:{to}?subject={quote(f'ScanSnack diagnostics: {model}')}&body={quote(body)}"
