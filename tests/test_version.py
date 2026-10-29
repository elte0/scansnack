"""Version: Major.Minor.Patch, eine Quelle (pyproject.toml), iOS-App zieht mit (tools/bump_version.py)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _version(path: Path) -> str:
    data = tomllib.loads(path.read_text("utf-8"))
    return str(data["project"]["version"] if "project" in data else data["tool"]["briefcase"]["version"])


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version(ROOT / "pyproject.toml"))


def test_ios_version_matches() -> None:
    assert _version(ROOT / "ios" / "pyproject.toml") == _version(ROOT / "pyproject.toml")
