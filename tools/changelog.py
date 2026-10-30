"""CHANGELOG.md (Keep a Changelog): turn the "Unreleased" section into the new version, print release notes.

python3 tools/changelog.py release 1.2.3 [--date 2026-01-31]   # [Unreleased] → [1.2.3] - date, links
python3 tools/changelog.py notes 1.2.3                         # text of the section (for the GitHub release)
python3 tools/changelog.py check                               # check the structure (also in the tests)

``release`` is called by tools/bump_version.py (workflow "Create Release"), ``notes`` by the workflow "Release".
Standard library only: runs in the release job with the runner's Python, without uv.
"""

from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
REPO = "https://github.com/elte0/scansnack"

_HEADING = re.compile(r"^## \[(?P<name>[^\]]+)\](?: - (?P<date>\d{4}-\d{2}-\d{2}))?\s*$", re.MULTILINE)
_UNRELEASED_LINK = re.compile(r"^\[Unreleased\]: \S+/compare/v(?P<prev>\d+\.\d+\.\d+)\.\.\.HEAD\n?", re.MULTILINE)


class ChangelogError(Exception):
    """CHANGELOG.md is not structured the way the release workflows expect."""


def _section(text: str, name: str) -> tuple[int, int, str]:
    """Start of the heading, end of the section and text under the heading (without the link list)."""
    headings = list(_HEADING.finditer(text))
    for i, m in enumerate(headings):
        if m.group("name").lower() == name.lower():
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[m.end() : end]
            # the link list at the end of the file does not belong to the last section
            links = re.search(r"^\[[^\]]+\]: ", body, re.MULTILINE)
            if links:
                end = m.end() + links.start()
                body = body[: links.start()]
            return m.start(), end, body.strip("\n")
    raise ChangelogError(f'no section "## [{name}]" in CHANGELOG.md')


def unreleased(text: str) -> str:
    """Entries under "Unreleased"; error if there are none."""
    body = _section(text, "Unreleased")[2]
    if not re.search(r"^\s*[-*] \S", body, re.MULTILINE):
        raise ChangelogError('"Unreleased" in CHANGELOG.md has no entries')
    return body


def release(text: str, version: str, date: datetime.date) -> str:
    """Turns "Unreleased" into the version's section and adds an empty "Unreleased" above it."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ChangelogError(f"version {version!r} is not major.minor.patch")
    if any(m.group("name") == version for m in _HEADING.finditer(text)):
        raise ChangelogError(f"version {version} is already in CHANGELOG.md")
    body = unreleased(text)
    start, end, _ = _section(text, "Unreleased")
    new_section = f"## [Unreleased]\n\n## [{version}] - {date.isoformat()}\n\n{body}\n\n"
    text = text[:start] + new_section + text[end:].lstrip("\n")

    new_link = f"[Unreleased]: {REPO}/compare/v{version}...HEAD\n"
    link = _UNRELEASED_LINK.search(text)
    if link:
        new_link += f"[{version}]: {REPO}/compare/v{link.group('prev')}...v{version}\n"
        return text[: link.start()] + new_link + text[link.end() :]
    # first release in the changelog: start a new link list
    return text.rstrip("\n") + "\n\n" + new_link + f"[{version}]: {REPO}/releases/tag/v{version}\n"


def notes(text: str, version: str) -> str:
    """Section of a version, headings one level up (### → ##) for the release page."""
    body = _section(text, version)[2].strip()
    if not body:
        raise ChangelogError(f"section {version} in CHANGELOG.md is empty")
    return re.sub(r"^### ", "## ", body, flags=re.MULTILINE)


def check(text: str) -> None:
    """Structure: "Unreleased" first, every version with a link. Entries are only required at release time."""
    names = [m.group("name") for m in _HEADING.finditer(text)]
    if not names or names[0] != "Unreleased":
        raise ChangelogError('"Unreleased" must be the first section')
    for name in names[1:]:
        if f"\n[{name}]: " not in text:
            raise ChangelogError(f'link "[{name}]: …" missing at the end of CHANGELOG.md')


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    rel = sub.add_parser("release")
    rel.add_argument("version")
    rel.add_argument("--date", type=datetime.date.fromisoformat, default=datetime.date.today())
    sub.add_parser("notes").add_argument("version")
    sub.add_parser("check")
    args = parser.parse_args(argv)

    text = CHANGELOG.read_text("utf-8")
    try:
        if args.cmd == "release":
            CHANGELOG.write_text(release(text, args.version, args.date), "utf-8")
        elif args.cmd == "notes":
            print(notes(text, args.version))
        else:
            check(text)
    except ChangelogError as e:
        raise SystemExit(f"CHANGELOG.md: {e}") from None


if __name__ == "__main__":
    main()
