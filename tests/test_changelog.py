"""CHANGELOG.md and tools/changelog.py: Create Release turns "Unreleased" into the new version."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

import changelog
from changelog import ChangelogError

ROOT = Path(__file__).resolve().parent.parent
REPO = changelog.REPO

SAMPLE = f"""# Changelog

Intro.

## [Unreleased]

### Added

- New thing.

### Fixed

- Old bug.

## [0.3.0] - 2026-10-29

### Added

- Updates.

[Unreleased]: {REPO}/compare/v0.3.0...HEAD
[0.3.0]: {REPO}/releases/tag/v0.3.0
"""


def test_repo_changelog_is_well_formed() -> None:
    changelog.check((ROOT / "CHANGELOG.md").read_text("utf-8"))


def test_release_moves_unreleased_into_version() -> None:
    text = changelog.release(SAMPLE, "0.4.0", datetime.date(2026, 11, 2))
    assert "## [Unreleased]\n\n## [0.4.0] - 2026-11-02\n\n### Added\n\n- New thing.\n\n### Fixed" in text
    assert f"[Unreleased]: {REPO}/compare/v0.4.0...HEAD\n[0.4.0]: {REPO}/compare/v0.3.0...v0.4.0\n" in text
    assert text.endswith(f"[0.3.0]: {REPO}/releases/tag/v0.3.0\n")
    changelog.check(text)
    with pytest.raises(ChangelogError, match="no entries"):
        changelog.unreleased(text)  # the next release needs new entries


def test_release_refuses_empty_unreleased_and_known_version() -> None:
    empty = changelog.release(SAMPLE, "0.4.0", datetime.date(2026, 11, 2))
    with pytest.raises(ChangelogError, match="no entries"):
        changelog.release(empty, "0.4.1", datetime.date(2026, 11, 3))
    with pytest.raises(ChangelogError, match="already"):
        changelog.release(SAMPLE, "0.3.0", datetime.date(2026, 11, 3))


def test_first_release_starts_link_list() -> None:
    text = changelog.release("# Changelog\n\n## [Unreleased]\n\n- First.\n", "0.1.0", datetime.date(2026, 1, 1))
    assert text.endswith(f"[Unreleased]: {REPO}/compare/v0.1.0...HEAD\n[0.1.0]: {REPO}/releases/tag/v0.1.0\n")
    changelog.check(text)


def test_notes_for_release_page() -> None:
    assert changelog.notes(SAMPLE, "0.3.0") == "## Added\n\n- Updates."
    with pytest.raises(ChangelogError, match="no section"):
        changelog.notes(SAMPLE, "9.9.9")


def test_check_rejects_missing_link() -> None:
    with pytest.raises(ChangelogError, match=r"\[0.3.0\]"):
        changelog.check(SAMPLE.replace(f"[0.3.0]: {REPO}/releases/tag/v0.3.0\n", ""))
