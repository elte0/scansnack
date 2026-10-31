# ScanSnack desktop and iOS apps

> **Summary.** "ScanSnack" is the user-facing app on top of the protocol client: a PySide6 desktop app
> (built as a Mac bundle) and an iOS app built with BeeWare. In addition, the eSCL bridge makes the
> scanner available in the macOS scan dialog (user guide: [AirScan bridge](airscan.md)). This page describes how each of them is built, how they
> behave, and what has been tested so far.

Decisions: ADR 0003 (PySide6), ADR 0004 (BeeWare). Protocol: [protocol](protocol.md).

## Contents

- [Desktop app](#desktop-app)
  - [Mac bundle](#mac-bundle)
- [eSCL bridge](#escl-bridge)
- [iOS app](#ios-app)
- [Status and next steps](#status-and-next-steps)

## Desktop app

Code: `adapters/qt`, entry point `app/desktop.py`. Start: `uv run --extra app scansnap-app [--ip …]`.
User interface and code comments in English (ADR 0006).

- Layout: on the left profiles + options (Double-sided, Remove blank pages, Remove background) + Scan;
  in the middle the document list ("New Scan" + saved PDFs; persists across restarts: `adapters/library`,
  index `library.json` + `thumbs/` in the app data directory `…/scansnap-bridge/ScanSnack`; an old
  `ScanSnap` folder is migrated on start); on the right the pages (rotate/delete/reorder), name, folder,
  save. Saved documents can still be edited ("Save Changes" rewrites the PDF, renaming renames the
  file). Pages are read back from our own PDFs.
- Connection: `ScanWorker` in its own `QThread`, device via `ports.scanner.ScannerLocator`; a persistent
  session is opened right at start-up and re-established every 20 s (otherwise the scan button events do
  not arrive). The scan button starts a scan with the settings on the left.
- Start-up: without a saved model and IP, or when the scanner does not respond, "Connect Scanner" appears
  first: choose the model (all of `core.catalog`, unsupported ones greyed out), enter an IP or
  "Search Network" (selects the model it finds), and for the iX500 the pairing key ("Get Key …" opens
  `KeyCaptureDialog`, which runs `ScannerLocator.capture_pairing_key` in a background thread). Search and check run
  in the background. Connection errors during a scan open it as well. Settings without a model (from
  before the model choice) count as iX1500.
- iX500 (beta, `adapters/ix1500/ix500.py`): no persistent session and no scan button; every scan
  registers anew (ADR 0007).
- Every scan run is a document of its own and is **saved immediately after the scan** (user
  requirement), named "Scan YYYY-MM-DD HH.MM", into the "Save to" folder, optionally opened – including
  the pages scanned before a paper jam. If saving fails, the draft stays available for "Save as PDF".
  A scan without pages disappears.
- "Remove background": `core.images.whiten` – paper colour per channel (85th percentile) → white; dark
  paper (< 150) is left unchanged. Runs in the same re-encode as cropping/rotation.
- Paper errors: `core.errors.FeedError(reason)`, help texts in `core.document.FEED_HELP`.
- Design: Primer (GitHub), tokens from @primer/primitives 11.10.0 in `adapters/qt/tokens.py` (Qt-free,
  also used on iOS); `primer.py` sets QPalette + stylesheet, Fusion style, light/dark follows the system
  live. Default button = primary (green), muted text via the `muted` property.
- Pitfall: a QListWidget in IconMode does not reorder on drag → `PageList.dropEvent` reorders itself.

### Executables and releases

Code: `packaging/`, `tools/build_app.py`.

- Build for the running system: `uv run --extra app python tools/build_app.py` (`--no-pack`: app only;
  `packaging/build_mac.sh` does the same). `ScanSnack.spec` is cross-platform (Qt modules excluded).
- Every build runs the app with `--selftest` (package data, JPEG, PDF). Icons come from
  `tools/make_icon.py` (`.icns` can only be generated on macOS, `.ico`/`.png` anywhere).

| System | Result in `dist/` | Notes |
|---|---|---|
| macOS arm64 / x86_64 | `ScanSnack.app`, `ScanSnack-<ver>-macos-<arch>.dmg` | bundle ID `com.elte0.scansnack`, Info.plist including `NSLocalNetworkUsageDescription`, ad-hoc signed, checked with `codesign --verify` |
| Windows x86_64 | `ScanSnack/ScanSnack.exe`, `ScanSnack-<ver>-windows-x86_64.zip` | no console window, icon `ScanSnack.ico` |
| Linux x86_64 | `ScanSnack/ScanSnack`, `ScanSnack-<ver>-linux-x86_64.tar.gz` | ships `scansnack.desktop` + `scansnack.png`; needs the Qt system libraries (`libxcb-cursor0`, `libegl1` …) |

- The architecture is that of the running Python (no universal2: Pillow ships separate wheels).
- Mac arm64 ~107 MB, DMG ~46 MB. On first launch macOS asks for local network access.
- Tested: Mac locally; all four targets build and pass `--selftest` in GitHub Actions; the Linux build
  also starts offscreen. Windows, Intel Mac and Linux have not been tried with the scanner.

**Auto-update** (ADR 0005): `core/update.py` (versions, package names, checksums), `ports/updates.py`
(`ReleaseSource`, `Installer`), `adapters/github` (GitHub Releases API), `adapters/selfupdate`
(download, verify, unpack, helper script that swaps the app and restarts it), `adapters/qt/updates.py`
(dialog). About 1.5 s after start the built app checks the latest release in the background; offline it
stays silent. Dialog: *Install and Restart* / *Later* (ask again next start) / *Skip This Version*.
*Help → Check for Updates …* checks by hand (also from source, where installing is replaced by a link
to the release page); *Check for Updates on Start* switches the check off.

- The release workflow attaches `SHA256SUMS`; the app prefers GitHub's per-asset `digest`. No checksum,
  no one-click install.
- One-click install needs write access to the folder that holds the app. Otherwise (macOS App
  Translocation, running from the DMG, read-only folder) the dialog offers the release page instead.
- Tested: unit tests (including the real `sh` helper swapping a fake app) and an update of a real Linux
  build; the macOS DMG path and the Windows PowerShell helper are not yet tried on a real system.

**Version:** the only source is `[project] version` in `pyproject.toml` (major.minor.patch). The spec and
`uv.lock` read it; `tools/bump_version.py` also updates `ios/pyproject.toml` (`tests/test_version.py`
checks that both match).

**Releases (GitHub Actions):**

- `Create Release` (`.github/workflows/create-release.yml`, started manually on `main`, choose
  patch/minor/major): `tools/check.sh` → `tools/bump_version.py` → commit "Version X.Y.Z" + tag `vX.Y.Z`
  → push → calls `Release`. The bump also turns the *Unreleased* section of `CHANGELOG.md` into
  `## [X.Y.Z] - date` (`tools/changelog.py release`) and stops if *Unreleased* has no entries.
- `Release` (`.github/workflows/release.yml`): for every tag `v*.*.*` (also one pushed by hand) builds on
  macos-latest (arm64), macos-15-intel, windows-latest and ubuntu-22.04, then creates a GitHub release
  with all packages. Checks that the tag matches the version. The release notes are the version's
  section of `CHANGELOG.md` (`tools/changelog.py notes`) plus the download table; a tag without a
  changelog section falls back to GitHub's generated notes. Tags pushed by a workflow with
  `GITHUB_TOKEN` do not trigger workflows, so `Create Release` calls the release workflow directly
  (`workflow_call`).
- `Changelog` (`.github/workflows/changelog.yml`): a pull request that changes `src/`, `packaging/` or
  `ios/src/` fails unless it also changes `CHANGELOG.md`; the label `no changelog` skips the check (for
  refactoring or tests).
- If `main` is protected, `github-actions[bot]` must be allowed to push (or use a PAT as a secret).

**Signing (optional, needs secrets):** macOS: import a Developer ID certificate (p12 + password) into a
temporary keychain, set `CODESIGN_IDENTITY`, then `xcrun notarytool submit --wait` + `xcrun stapler
staple` (Apple ID, app-specific password, team ID). Windows: a code signing certificate or Azure Trusted
Signing + `signtool`. Unsigned: macOS "Open Anyway", Windows SmartScreen "Run anyway".

## eSCL bridge

Code: `adapters/escl`. Start: `scansnap serve [--port 8090] [--name …]`. User guide (start, options,
limitations, troubleshooting): [AirScan bridge](airscan.md). The desktop app does not start the bridge.

- Feeder (ADF) only, JPEG/PDF; Bonjour `_uscan._tcp` via zeroconf. Runs `claim` automatically if our
  own profile is missing.
- macOS "Black & White" = `ColorMode=Grayscale8` → gray D4 (the JPEG still has 3 channels, see
  [protocol](protocol.md#open-questions)).
- Cropping + rotation in `Device.scan` (`core.images.process`, one re-encode with the same quantisation
  tables); A4 test 210×298 mm confirmed in the macOS scan dialog. `--no-crop` turns it off; dark paper →
  no cropping.
- Paper errors → job Aborted / `MediaJam`.

## iOS app

Code: `ios/`. Bundle ID `com.elte0.scansnack` (Briefcase: `bundle` + app name; the app name is also the
module name, hence the package `ios/src/scansnack`).

BeeWare: Briefcase 0.4.7, Toga 0.5.7, Python 3.14, Pillow iOS wheel. Core included via the symlink
`ios/src/scansnap`. The iOS app is its own composition root (uses the ix1500 and library adapters
directly).

- `ios/src/scansnack/app.py`: tabs "Scan" (profile list, options, scan; persistent session, scan
  button) and "Documents", page view (Delete/Share, UIActivityViewController via rubicon), scanner IP.
  After a scan, the page view opens. `theme.py` takes its tokens from `scansnap.adapters.qt.tokens`.
  Every screen is rebuilt from state via `render()`, also when switching light/dark.
- Identity name `scansnap-iphone` → select it as the user on the touch panel. PDFs go to `Documents`
  (visible in the Files app), everything else to `Library/Application Support/ScanSnack`.
- Toga pitfalls: `toga.App` already has `documents` and other attributes (don't use those names for
  your own attributes); labels do not wrap; colours with alpha only as an `rgb(...)` object; `margin` is
  the outer spacing; set a fixed width in hidden tabs.
- Build and install on the iPhone: `ios/build_ios.sh` (Briefcase needs Homebrew Python; uv's Python
  fails because of libpython). Simulator: `cd ios && .venv/bin/briefcase run iOS -d <simulator UDID>`.
- Installed and launched on an iPhone 16e (2026-10-29, team 2BCANYGFHU). Not yet tested with the scanner.

## Status and next steps

- Desktop app: only checked by tests (offscreen), not yet live with the scanner (neither from source nor
  as .app).
- iOS: after the restructuring into `core/ports/adapters` only the imports were adjusted; not rebuilt.

Next steps:

1. Test the app live, incorporate feedback.
2. Test the scan button in the app live.
3. Grayscale as 1-channel JPEG; test cropping with receipts, business cards and coloured paper.

Not a priority: Docker/server, profiles.
