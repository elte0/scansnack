# scansnap-bridge – context for Claude Code

ScanSnap iX1500 over Wi-Fi without ScanSnap Home: our own client for the proprietary protocol, an
eSCL/AirScan bridge for the macOS scan dialog, a desktop app (PySide6), an iOS app (BeeWare).
Overview: `README.md`, apps: `docs/apps.md`.

## Contents

- [Architecture](#architecture-hexagonal-adr-0002)
- [Reverse engineering](#reverse-engineering)
- [Decisions](#decisions)
- [Feature list and changelog](#feature-list-and-changelog)
- [Checks](#checks)
- [Documentation site](#documentation-site)
- [Conventions](#conventions)

## Architecture (hexagonal, ADR 0002)

```
src/scansnap/
  core/      domain: ScanSettings, Sheet, ScanPage, Entry, errors, image post-processing, PDF
  ports/     typing.Protocol: ScannerPort, ScannerLocator, OutputPort, ReleaseSource, Installer
  adapters/  ix1500/ (Wi-Fi protocol), escl/ (eSCL + Bonjour), library/ (PDF storage), qt/ (GUI),
             github/ (release check), selfupdate/ (install update + restart, ADR 0005)
  app/       composition root: cli.py, desktop.py – the only place that creates concrete adapters
```

- `core` and `ports` know nothing about Qt, sockets or adapters. Adapters do not import each other.
- Qt types (`QImage`, signals, `QObject`) only in `adapters/qt`; the core works with `bytes`/PIL.
- Protocol I/O never in the GUI thread: worker thread, results to the GUI via signals.
- Enforced by import-linter (`[tool.importlinter]` in `pyproject.toml`).

## Reverse engineering

- `docs/protocol.md` is the single source of truth for the protocol (packets, sequences, states, open
  questions). Update it with every new finding. Never invent anything.
- Keep observations apart from assumptions: mark anything unconfirmed in the code with `# UNVERIFIED:`
  and list it in `protocol.md` under "Open questions".
- Golden tests: captures extracted from pcap files in `tests/fixtures/captures/` (`tools/make_fixtures.py`,
  anonymised), replayed by the fake scanner `tests/mock_scanner.py`. Tests never require a real device.
  Never "adjust" captures to make a test pass.
- Never commit raw captures (`captures/`), test logs or `ssh-identity.json` (device data or scanned
  documents). In the docs, refer to them only as private sources (see "Sources and conventions" in
  `protocol.md`), never by file name.
- Keep `docs/disclaimer.md` (reverse engineering, trademarks) in place and linked from the README.

## Decisions

ADRs in MADR format under `docs/adr/NNNN-title.md`. Never silently override an existing ADR; if a
decision needs to change, write a new ADR that supersedes the old one.

## Feature list and changelog

Part of every change, in the same commit – not a follow-up and not only when asked:

- **Changelog:** every change a user, a packager or a reader of the protocol docs would notice gets an
  entry under `## [Unreleased]` in `CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
  groups `### Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`; create the group if it is
  missing). One line per change, in English, written for users: what changed, not which file. Pure
  refactoring, tests and CI tweaks need no entry; such a PR gets the label `no changelog`.
- **Never edit released sections or write a version heading by hand.** *Create Release* turns
  *Unreleased* into `## [X.Y.Z] - date` (`tools/bump_version.py` → `tools/changelog.py release`) and
  uses the section as the release notes; it refuses to release an empty *Unreleased*.
- **Feature list:** the only list of app features is the "Features" section of `site/content/app.md`
  (the README only links to it). Whenever a change adds, changes or removes a user-visible feature
  (an `Added`/`Changed`/`Removed` changelog entry about the app), update that list in the same commit:
  add a bullet, reword it or delete it, in the style of the existing bullets. Do not copy the list
  anywhere else.
- Before finishing, check the diff: app code changed → changelog entry (or a reason for `no changelog`);
  user-visible feature changed → feature list updated. CI (`.github/workflows/changelog.yml`) fails a PR
  that changes `src/`, `packaging/` or `ios/src/` without touching `CHANGELOG.md`.

## Checks

```sh
tools/check.sh    # ruff check . && ruff format --check . && pyright (strict) && lint-imports && pytest
```

A task is only done when all checks are green and the changelog and feature list are up to date
(see above). Pre-commit hook: `git config core.hooksPath .githooks`.

## Documentation site

Hugo site "ScanSnack" in `site/`; it mounts `docs/protocol.md`, `docs/disclaimer.md` and `CHANGELOG.md`
(page "Changelog") as content, so those files stay the only source. The app page `site/content/app.md`
holds the feature list (see [Feature list and changelog](#feature-list-and-changelog)). Its screenshots
(`tools/screenshots.py`) and download links (latest release) are generated in the Pages build. Build
locally: `hugo --source site`. Deployment: `.github/workflows/pages.yml` (push to `main`, after every
release, GitHub Pages with source "GitHub Actions"; pull requests only build).

## Conventions

- Documentation (`*.md`) in English.
- Repository language is English (ADR 0006): code comments, docstrings, CLI/log/error messages, UI texts,
  tests, scripts, workflows, docs and commit messages. Keep quotations of device output verbatim.
- Replies to the user short and in German.
- Live tests: Mac 192.168.178.37, scanner 192.168.178.29, ScanSnap Home quit; broadcast discovery is
  unreliable → use `--ip`.
