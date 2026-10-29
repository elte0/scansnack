"""Erhöht die Versionsnummer (Major.Minor.Patch) und gibt die neue Version aus.

uv run python tools/bump_version.py patch   # 0.1.0 → 0.1.1   (minor → 0.2.0, major → 1.0.0)

Einzige Quelle der Version ist ``[project] version`` in pyproject.toml (gelesen von PyInstaller-Spec und
uv.lock). Die iOS-App (ios/pyproject.toml, Briefcase) kann sie nicht lesen und wird hier mitgezogen.
Macht außerdem aus „Unreleased“ in CHANGELOG.md den Abschnitt der neuen Version (tools/changelog.py) und
bricht vorher ab, wenn dort nichts steht. Wird vom Workflow „Create Release“ benutzt
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
        raise SystemExit(f"keine Versionszeile in {IOS}")
    IOS.write_text(text, "utf-8")


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in PARTS:
        raise SystemExit(f"usage: bump_version.py {{{'|'.join(PARTS)}}}")
    text = changelog.CHANGELOG.read_text("utf-8")
    try:
        changelog.unreleased(text)  # vor dem Erhöhen: ohne Einträge kein Release
    except changelog.ChangelogError as e:
        raise SystemExit(f"CHANGELOG.md: {e}") from None
    # uv schreibt pyproject.toml und uv.lock; seine Ausgabe nach stderr, stdout trägt nur die neue Version
    subprocess.run(["uv", "version", "--bump", sys.argv[1], "--no-sync"], check=True, cwd=ROOT, stdout=sys.stderr)
    new = version()
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise SystemExit(f"Version {new!r} ist nicht Major.Minor.Patch")
    set_ios_version(new)
    changelog.CHANGELOG.write_text(changelog.release(text, new, datetime.date.today()), "utf-8")
    print(new)


if __name__ == "__main__":
    main()
