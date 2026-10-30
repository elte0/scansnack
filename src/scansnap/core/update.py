"""Updates of the desktop app: compare versions, pick the matching release package, checksums.

Package names as in ``tools/build_app.py``: ``ScanSnack-<version>-<os>-<arch>.<ext>``.
No network and no platform calls; the adapters (``adapters/github``, ``adapters/selfupdate``) fetch
and install.
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
    url: str  # download URL (browser_download_url)
    size: int
    sha256: str | None  # lower case, hex; None: no checksum known → not installable


@dataclass(frozen=True)
class Release:
    version: Version
    tag: str
    page_url: str  # release page for humans (manual download, notes)
    notes: str  # Markdown
    assets: tuple[Asset, ...]

    @property
    def label(self) -> str:
        return format_version(self.version)


def parse_version(text: str) -> Version | None:
    """``"v1.2.3"``/``"1.2.3"`` → ``(1, 2, 3)``; anything else (including pre-releases like ``1.2.3-rc1``) → None."""
    m = _VERSION.fullmatch(text.strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def format_version(version: Version) -> str:
    return ".".join(map(str, version))


def os_name(sys_platform: str) -> str:
    """``sys.platform`` → name part of the packages."""
    return {"darwin": "macos", "win32": "windows"}.get(sys_platform, "linux")


def arch_name(machine: str) -> str:
    """``platform.machine()`` → name part of the packages."""
    machine = machine.lower()
    return ARCHES.get(machine, machine)


def package_name(version: Version, os: str, arch: str) -> str:
    return f"{APP_NAME}-{format_version(version)}-{os}-{arch}.{EXTENSIONS[os]}"


def pick_asset(release: Release, os: str, arch: str) -> Asset | None:
    """The package for this system from the release, or None if there is none."""
    wanted = package_name(release.version, os, arch)
    return next((a for a in release.assets if a.name == wanted), None)


def is_newer(release: Release, current: Version) -> bool:
    return release.version > current


def parse_sha256sums(text: str) -> dict[str, str]:
    """Content of a ``SHA256SUMS`` file (format of ``sha256sum``) → file name: checksum."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


class ChecksumMismatch(ValueError):
    pass


class Sha256:
    """Compute the checksum while downloading and compare it at the end."""

    def __init__(self, expected: str):
        self.expected = expected.lower()
        self._hash = hashlib.sha256()

    def update(self, chunk: bytes) -> None:
        self._hash.update(chunk)

    def verify(self, name: str) -> None:
        actual = self._hash.hexdigest()
        if actual != self.expected:
            raise ChecksumMismatch(f"{name}: checksum mismatch (expected {self.expected}, got {actual})")
