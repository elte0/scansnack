"""Error reports: collect what is needed to understand a failure, strip personal data, prepare an email.

Nothing here sends anything. The app shows the report, the user reads (and may edit) it and then sends it
with their own mail program (``mailto:``), see docs/adr/0008-error-reports-by-email.md.
"""

from __future__ import annotations

import collections
import datetime as dt
import getpass
import os
import platform
import re
import sys
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

REPORT_ADDRESS = "scansnack@posteo.de"
LOG_LINES = 400
# Longest mailto: URL handed to the mail program. Windows passes URLs through ShellExecute, which cuts them
# at about 2,000 characters; macOS and Linux mail programs take much longer ones.
MAILTO_LIMIT_WINDOWS = 1_900
MAILTO_LIMIT = 60_000


class LogBuffer:
    """The last ``lines`` lines written to it (thread-safe); the app's stderr goes here as well."""

    def __init__(self, lines: int = LOG_LINES):
        self._lines: collections.deque[str] = collections.deque(maxlen=lines)
        self._partial = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            parts = (self._partial + text).split("\n")
            self._partial = parts.pop()
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            self._lines.extend(f"{stamp} {line}" for line in parts)

    def text(self) -> str:
        with self._lock:
            lines = list(self._lines)
            if self._partial:
                lines.append(self._partial)
        return "\n".join(lines)


class Tee:
    """A text stream that writes to ``original`` (may be None, as in windowed builds) and to ``log``."""

    def __init__(self, original: TextIO | None, log: LogBuffer):
        self.original, self.log = original, log

    def write(self, text: str) -> int:
        self.log.write(text)
        if self.original is not None:
            try:
                self.original.write(text)
            except (OSError, ValueError):  # closed or no console
                pass
        return len(text)

    def flush(self) -> None:
        if self.original is not None:
            try:
                self.original.flush()
            except (OSError, ValueError):
                pass

    def isatty(self) -> bool:
        return False


@dataclass(frozen=True)
class DeviceDiagnostics:
    """What a scanner adapter knows about the device and its connection, without network I/O.

    ``fields`` go into the report as they are. ``private`` lists values that identify the device or the
    host (IP, MAC, serial, host ID, password …); they are removed from the whole report, logs included.
    """

    fields: Mapping[str, str]
    private: tuple[str, ...] = ()


@dataclass
class ReportInput:
    app_version: str
    error: str = ""
    details: str = ""  # traceback
    fields: dict[str, str] = field(default_factory=dict[str, str])  # e.g. Qt version, last scan settings
    device: DeviceDiagnostics | None = None
    log: str = ""
    private: list[str] = field(default_factory=list[str])  # e.g. scanner IP, folder, document names


def system_fields() -> dict[str, str]:
    frozen = bool(getattr(sys, "frozen", False))
    return {
        "OS": f"{platform.system()} {platform.release()} ({platform.version()})",
        "Platform": platform.platform(),
        "Architecture": platform.machine(),
        "Python": platform.python_version(),
        "Build": "app bundle" if frozen else "from source",
    }


def host_private() -> list[str]:
    """Names that identify this computer and its user."""
    values = [platform.node(), Path.home().name]
    try:
        values.append(getpass.getuser())
    except (OSError, KeyError, ImportError):  # no user name in the environment
        pass
    values += [os.environ.get(k, "") for k in ("USER", "USERNAME", "LOGNAME", "COMPUTERNAME", "HOSTNAME")]
    return values


def build(report: ReportInput) -> str:
    """The report as plain text, personal data already removed."""
    lines = ["ScanSnack error report", "", f"App version: {report.app_version}"]
    lines += [f"{k}: {v}" for k, v in system_fields().items()]
    lines += [f"{k}: {v}" for k, v in report.fields.items()]
    lines += ["", "## Scanner"]
    if report.device is None:
        lines.append("not connected in this session")
    else:
        lines += [f"{k}: {v}" for k, v in report.device.fields.items()]
    lines += ["", "## Error", report.error or "(none – report sent from the Help menu)"]
    if report.details:
        lines += ["", report.details.rstrip()]
    lines += ["", "## Log (last lines)", report.log.rstrip() or "(empty)"]
    private = [*report.private, *host_private(), *(report.device.private if report.device else ())]
    return scrub("\n".join(lines) + "\n", private)


# --- removing personal data ----------------------------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_MAC = re.compile(r"\b[0-9A-Fa-f]{2}(?:([:-])[0-9A-Fa-f]{2})(?:\1[0-9A-Fa-f]{2}){4}\b")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# IPv6 only with "::" or all eight groups, so that times (16:01:32) and version strings stay
_IPV6 = re.compile(
    r"(?<![\w:])(?:(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,5})?"
    r"|::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}))(?:%\w+)?(?![\w:])"
)
_UUID = re.compile(r"\b[0-9A-Fa-f]{8}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{4}-?[0-9A-Fa-f]{12}\b")
# Long hex strings: IDs, keys, raw frames that may carry serials or passwords
_LONG_HEX = re.compile(r"\b[0-9A-Fa-f]{16,}\b")
# User folders: /Users/name, /home/name, C:\Users\name
_USER_DIR = re.compile(r"(?i)(/Users/|/home/|[A-Z]:\\Users\\|[A-Z]:/Users/)[^/\\\s\"']+")
# File names of documents and images (often personal); program files (.py, .dll …) stay
_DOC_FILE = re.compile(r"[^/\\\s\"'(]*[^/\\\s\"'(.]\.(?i:pdf|jpe?g|png|tiff?|heic)\b")
_PASSWORD = re.compile(r"(?i)\b(password|passwd|pwd|passphrase|token|secret|api[_-]?key)\b(\s*[=:]\s*)\S+")


def _variants(value: str) -> set[str]:
    """``value`` and its other spellings (hex upper/lower, MAC with and without separators)."""
    out = {value, value.lower(), value.upper()}
    plain = re.sub(r"[:\-]", "", value)
    if re.fullmatch(r"[0-9A-Fa-f]{12}", plain):
        for sep in ("", ":", "-"):
            mac = sep.join(plain[i : i + 2] for i in range(0, 12, 2))
            out |= {mac.lower(), mac.upper()}
    return out


def scrub(text: str, private: Iterable[str] = ()) -> str:
    """Removes personal data: the given ``private`` values (in any case), then email addresses, IP and MAC
    addresses, IDs, long hex strings, user folder names, document file names and ``password=…`` values."""
    values = {v for raw in private if len(p := str(raw).strip()) >= 4 for v in _variants(p)}
    for value in sorted(values, key=len, reverse=True):  # whole words only, longest first
        text = re.sub(rf"(?<!\w){re.escape(value)}(?!\w)", "<private>", text, flags=re.IGNORECASE)
    text = _EMAIL.sub("<email>", text)
    text = _MAC.sub("<mac>", text)
    text = _UUID.sub("<id>", text)
    text = _IPV6.sub("<ip>", text)
    text = _IPV4.sub("<ip>", text)
    text = _LONG_HEX.sub(lambda m: f"<hex {len(m.group()) // 2} bytes>", text)
    text = _USER_DIR.sub(r"\1<user>", text)
    text = _DOC_FILE.sub(lambda m: "<file>" + m.group()[m.group().rfind(".") :], text)
    return _PASSWORD.sub(r"\1\2<private>", text)


# --- email ---------------------------------------------------------------------------------------------

SHORTENED = (
    "\n[The report is too long for an email link. The full report is on your clipboard: "
    "please replace this text with it (paste).]\n"
)


@dataclass(frozen=True)
class Mail:
    url: str
    complete: bool  # False: the body was shortened, the full report must be pasted from the clipboard


def mailto(report: str, subject: str, *, limit: int | None = None, address: str = REPORT_ADDRESS) -> Mail:
    """``mailto:`` URL with the report as the body, shortened (whole lines) to stay within ``limit``."""
    if limit is None:
        limit = MAILTO_LIMIT_WINDOWS if sys.platform == "win32" else MAILTO_LIMIT

    def url(body: str) -> str:
        return f"mailto:{address}?subject={quote(subject)}&body={quote(body.replace(chr(10), chr(13) + chr(10)))}"

    full = url(report)
    if len(full) <= limit:
        return Mail(full, True)
    lines = report.splitlines()
    lo, hi = 0, len(lines)
    while lo < hi:  # most leading lines that still fit together with the note
        mid = (lo + hi + 1) // 2
        if len(url("\n".join(lines[:mid]) + SHORTENED)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return Mail(url("\n".join(lines[:lo]) + SHORTENED), False)
