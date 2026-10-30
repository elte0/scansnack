"""Bumps the version number (major.minor.patch) and prints the new version.

uv run python tools/bump_version.py patch   # 0.1.0 → 0.1.1   (minor → 0.2.0, major → 1.0.0)

The only source of the version is ``[project] version`` in pyproject.toml (read by the PyInstaller spec and
uv.lock). The iOS app (ios/pyproject.toml, Briefcase) cannot read it and is updated here as well.
Also turns "Unreleased" in CHANGELOG.md into the section of the new version (tools/changelog.py) and
stops beforehand if it has no entries. Used by the workflow "Create Release"
(.github/workflows/create-release.yml).
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import changelog

ROOT = Path(__file__).resolve().parent.parent
IOS = ROOT / "ios" / "pyproject.toml"
PARTS = ("major", "minor", "patch")


def version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"])


def set_ios_version(new: str) -> None:
    text = IOS.read_text("utf-8")
    text, n = re.subn(r'(?m)^version = "[^"]*"', f'version = "{new}"', text, count=1)
    if n != 1:
        raise SystemExit(f"no version line in {IOS}")
    IOS.write_text(text, "utf-8")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PARTS:
        raise SystemExit(f"usage: bump_version.py {{{'|'.join(PARTS)}}}")
    text = changelog.CHANGELOG.read_text("utf-8")
    try:
        changelog.unreleased(text)  # before bumping: no entries, no release
    except changelog.ChangelogError as e:
        raise SystemExit(f"CHANGELOG.md: {e}") from None
    # uv writes pyproject.toml and uv.lock; its output goes to stderr, stdout carries only the new version
    subprocess.run(["uv", "version", "--bump", sys.argv[1], "--no-sync"], check=True, cwd=ROOT, stdout=sys.stderr)
    new = version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise SystemExit(f"version {new!r} is not major.minor.patch")
    set_ios_version(new)
    changelog.CHANGELOG.write_text(changelog.release(text, new, datetime.date.today()), "utf-8")
    print(new)


if __name__ == "__main__":
    main()
