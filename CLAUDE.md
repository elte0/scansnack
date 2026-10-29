# scansnap-bridge – context for Claude Code

ScanSnap iX1500 over Wi-Fi without ScanSnap Home: our own client for the proprietary protocol, an
eSCL/AirScan bridge for the macOS scan dialog, a desktop app (PySide6), an iOS app (BeeWare).
Overview: `README.md`, apps: `docs/apps.md`.

## Contents

- [Architecture](#architecture-hexagonal-adr-0002)
- [Reverse engineering](#reverse-engineering)
- [Decisions](#decisions)
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

## Checks

```sh
tools/check.sh    # ruff check . && ruff format --check . && pyright (strict) && lint-imports && pytest
```

A task is only done when all checks are green. Pre-commit hook: `git config core.hooksPath .githooks`.

## Documentation site

Hugo site "ScanSnack" in `site/`; it mounts `docs/protocol.md` and `docs/disclaimer.md` as content, so
the docs stay the only source. The app page `site/content/app.md` lists the app's features: update it
whenever a user-visible feature changes. Its screenshots (`tools/screenshots.py`) and download links (latest
release) are generated in the Pages build. Build locally: `hugo --source site`. Deployment:
`.github/workflows/pages.yml` (push to `main`, after every release, GitHub Pages with source "GitHub Actions").

## Conventions

- Documentation (`*.md`) in English.
- Replies to the user short and in German. App user interfaces in English, code comments in German.
- Live tests: Mac 192.168.178.37, scanner 192.168.178.29, ScanSnap Home quit; broadcast discovery is
  unreliable → use `--ip`.
