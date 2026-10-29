---
status: accepted
date: 2026-10-29
---

# Own small updater on top of GitHub Releases

## Context and problem statement

The desktop app ships as PyInstaller builds for macOS (DMG, arm64 + Intel), Windows (ZIP) and Linux
(tar.gz), attached to GitHub Releases (`release.yml`). Users should be told about a new release when the
app starts and be able to install it with one click; the app then restarts as the new version. "Later"
asks again on the next start. Builds are ad-hoc signed on macOS and unsigned on Windows.

## Decision drivers

* Works on all three platforms with the existing PyInstaller builds and release assets.
* No extra hosting, key management or signing accounts (none exist for this project).
* Integrity: never install a download that does not match a published checksum.
* Fits the hexagonal layout (ADR 0002): no network or platform code in `core`/`ports` or the GUI.

## Considered options

* **tufup** (successor of PyUpdater, based on The Update Framework)
* **Sparkle** (macOS) + **WinSparkle** (Windows)
* **PyUpdater**, **updater4pyi**
* own small updater: GitHub Releases API + SHA-256 + helper script that swaps the app

## Decision outcome

Chosen: **own small updater** (`core/update.py`, `ports/updates.py`, `adapters/github`,
`adapters/selfupdate`, dialog in `adapters/qt/updates.py`).

* tufup is the best existing fit for PyInstaller, but needs its own TUF repository (signed metadata,
  root/targets keys to keep safe and use in CI, its own archive format and hosting) instead of the
  release assets we already publish. Its value, signed metadata, mostly matters once builds are signed;
  for this project it adds key management without removing the Gatekeeper/SmartScreen caveats.
* Sparkle and WinSparkle are native frameworks (Objective-C/C) with an appcast feed and EdDSA keys; there
  is no counterpart for Linux, and binding Sparkle from PySide6 needs PyObjC or ctypes glue. Two systems
  plus a third for Linux.
* PyUpdater is archived (2022); updater4pyi is unmaintained.

How it works: on start (only in the built app; from source only via *Help → Check for Updates …*) the
app asks `GET /repos/elte0/scansnack/releases/latest` (drafts and pre-releases are excluded by GitHub),
compares the tag with its own version (package metadata, `copy_metadata` in the spec) and picks the asset
named like `tools/build_app.py` names it (`core.update.package_name`). The checksum comes from GitHub's
per-asset `digest` (since 2025-06) or the `SHA256SUMS` file the release workflow attaches; without a
checksum the app only offers the release page. The package is downloaded next to the installed app
(`.ScanSnack-update/`), verified, unpacked (`hdiutil` + `ditto` for the DMG, `zipfile`, `tarfile` with
the `data` filter). The app then closes (asking about unsaved changes) and a detached helper (`sh`, or
PowerShell on Windows) waits for it to exit, renames the old app, moves the new one in (rolls back on
failure), removes the leftovers and starts the new app. The same helper on all systems, because Windows
cannot overwrite a running program.

Dialog: *Install and Restart*, *Later* (ask again on the next start), *Skip This Version* (ask again on
the next newer version). *Check for Updates on Start* in the Help menu turns the check off.

### Consequences

* Good: no new dependency, no keys, works with the existing release assets on all three platforms.
* Good: a tampered or truncated download is rejected (SHA-256 from GitHub's API over HTTPS).
* Bad: trust ends at the GitHub account and HTTPS – there is no offline signing key as with TUF or
  Sparkle. Revisit (new ADR) once builds are signed with a Developer ID / Authenticode certificate.
* Bad: one-click install needs write access to the folder holding the app. Not possible (the dialog then
  offers the download page instead) when macOS runs the app translocated (started from Downloads with
  the quarantine flag) or from the DMG, or the folder is not writable (e.g. `/opt` on Linux).
* Neutral (macOS): `urllib` does not set the quarantine attribute, so the updated app opens without a
  second Gatekeeper prompt. Because the build is ad-hoc signed, its signature changes with every release
  and macOS may ask again for local network access.
* Neutral (Windows): SmartScreen can show its prompt again for the new, unsigned executable.
