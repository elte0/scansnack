"""Updates der Desktop-App: Versionen vergleichen, das passende Release-Paket wählen, Prüfsummen.

Paketnamen wie in ``tools/build_app.py``: ``ScanSnack-<version>-<os>-<arch>.<ext>``.
Ohne Netz und ohne Plattform-Aufrufe; die Adapter (``adapters/github``, ``adapters/selfupdate``) holen
und installieren.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

Version = tuple[int, int, int]

APP_NAME = "ScanSnack"
ARCHES = {"amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}
EXTENSIONS = {"macos": "dmg", "windows": "zip", "linux": "tar.gz"}
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class Asset:
    name: str
    url: str  # Download-URL (browser_download_url)
    size: int
    sha256: str | None  # klein geschrieben, hex; None: keine Prüfsumme bekannt → nicht installierbar


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    page_url: str  # Release-Seite für Menschen (Download von Hand, Notizen)
    notes: str  # Markdown
    assets: tuple[Asset, ...]

    @property
    def label(self) -> str:
        return format_version(self.version)


def parse_version(text: str) -> Version | None:
    """``"v1.2.3"``/``"1.2.3"`` → ``(1, 2, 3)``; anderes (auch Vorabversionen wie ``1.2.3-rc1``) → None."""
    m = _VERSION.fullmatch(text.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def format_version(version: Version) -> str:
    return ".".join(map(str, version))


def os_name(sys_platform: str) -> str:
    """``sys.platform`` → Namensteil der Pakete."""
    return {"darwin": "macos", "win32": "windows"}.get(sys_platform, "linux")


def arch_name(machine: str) -> str:
    """``platform.machine()`` → Namensteil der Pakete."""
    machine = machine.lower()
    return ARCHES.get(machine, machine)


def package_name(version: Version, os: str, arch: str) -> str:
    return f"{APP_NAME}-{format_version(version)}-{os}-{arch}.{EXTENSIONS[os]}"


def pick_asset(release: Release, os: str, arch: str) -> Asset | None:
    """Das Paket dieses Systems aus dem Release, oder None, wenn es keins gibt."""
    wanted = package_name(release.version, os, arch)
    return next((a for a in release.assets if a.name == wanted), None)


def is_newer(release: Release, current: Version) -> bool:
    return release.version > current


def parse_sha256sums(text: str) -> dict[str, str]:
    """Inhalt einer ``SHA256SUMS``-Datei (Format von ``sha256sum``) → Dateiname: Prüfsumme."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


class ChecksumMismatch(ValueError):
    pass


class Sha256:
    """Prüfsumme beim Herunterladen mitrechnen und am Ende vergleichen."""

    def __init__(self, expected: str):
        self.expected = expected.lower()
        self._hash = hashlib.sha256()

    def update(self, chunk: bytes) -> None:
        self._hash.update(chunk)

    def verify(self, name: str) -> None:
        actual = self._hash.hexdigest()
        if actual != self.expected:
            raise ChecksumMismatch(f"{name}: checksum mismatch (expected {self.expected}, got {actual})")
