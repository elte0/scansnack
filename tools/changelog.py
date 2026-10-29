"""CHANGELOG.md (Keep a Changelog): Abschnitt „Unreleased“ zur neuen Version machen, Release-Notes ausgeben.

python3 tools/changelog.py release 1.2.3 [--date 2026-01-31]   # [Unreleased] → [1.2.3] - Datum, Links
python3 tools/changelog.py notes 1.2.3                         # Text des Abschnitts (für das GitHub-Release)
python3 tools/changelog.py check                               # Aufbau prüfen (auch in den Tests)

``release`` ruft tools/bump_version.py auf (Workflow „Create Release“), ``notes`` der Workflow „Release“.
Nur Standardbibliothek: läuft im Release-Job mit dem Python des Runners, ohne uv.
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
    """CHANGELOG.md ist nicht so aufgebaut, wie die Release-Workflows es erwarten."""


def _section(text: str, name: str) -> tuple[int, int, str]:
    """Start der Überschrift, Ende des Abschnitts und Text unter der Überschrift (ohne Linkliste)."""
    headings = list(_HEADING.finditer(text))
    for i, m in enumerate(headings):
        if m.group("name").lower() == name.lower():
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            body = text[m.end() : end]
            # Linkliste am Dateiende gehört nicht zum letzten Abschnitt
            links = re.search(r"^\[[^\]]+\]: ", body, re.MULTILINE)
            if links:
                end = m.end() + links.start()
                body = body[: links.start()]
            return m.start(), end, body.strip("\n")
    raise ChangelogError(f"kein Abschnitt „## [{name}]“ in CHANGELOG.md")


def unreleased(text: str) -> str:
    """Einträge unter „Unreleased“; Fehler, wenn dort nichts steht."""
    body = _section(text, "Unreleased")[2]
    if not re.search(r"^\s*[-*] \S", body, re.MULTILINE):
        raise ChangelogError("„Unreleased“ in CHANGELOG.md hat keine Einträge")
    return body


def release(text: str, version: str, date: datetime.date) -> str:
    """Macht aus „Unreleased“ den Abschnitt der Version und legt ein leeres „Unreleased“ darüber an."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ChangelogError(f"Version {version!r} ist nicht Major.Minor.Patch")
    if any(m.group("name") == version for m in _HEADING.finditer(text)):
        raise ChangelogError(f"Version {version} steht schon in CHANGELOG.md")
    body = unreleased(text)
    start, end, _ = _section(text, "Unreleased")
    new_section = f"## [Unreleased]\n\n## [{version}] - {date.isoformat()}\n\n{body}\n\n"
    text = text[:start] + new_section + text[end:].lstrip("\n")

    new_link = f"[Unreleased]: {REPO}/compare/v{version}...HEAD\n"
    link = _UNRELEASED_LINK.search(text)
    if link:
        new_link += f"[{version}]: {REPO}/compare/v{link.group('prev')}...v{version}\n"
        return text[: link.start()] + new_link + text[link.end() :]
    # erstes Release im Changelog: Linkliste neu anfangen
    return text.rstrip("\n") + "\n\n" + new_link + f"[{version}]: {REPO}/releases/tag/v{version}\n"


def notes(text: str, version: str) -> str:
    """Abschnitt einer Version, Überschriften eine Ebene höher (### → ##) für die Release-Seite."""
    body = _section(text, version)[2].strip()
    if not body:
        raise ChangelogError(f"Abschnitt {version} in CHANGELOG.md ist leer")
    return re.sub(r"^### ", "## ", body, flags=re.MULTILINE)


def check(text: str) -> None:
    """Aufbau: „Unreleased“ zuerst, jede Version mit Link. Einträge erst beim Release Pflicht."""
    names = [m.group("name") for m in _HEADING.finditer(text)]
    if not names or names[0] != "Unreleased":
        raise ChangelogError("„Unreleased“ muss der erste Abschnitt sein")
    for name in names[1:]:
        if f"\n[{name}]: " not in text:
            raise ChangelogError(f"Link „[{name}]: …“ fehlt am Ende von CHANGELOG.md")


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
