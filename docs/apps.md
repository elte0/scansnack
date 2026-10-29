# ScanSnack desktop and iOS apps

> **Summary.** "ScanSnack" is the user-facing app on top of the protocol client: a PySide6 desktop app
> (built as a Mac bundle) and an iOS app built with BeeWare. In addition, the eSCL bridge makes the
> scanner available in the macOS scan dialog. This page describes how each of them is built, how they
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
User interface in English, code comments in German.

- Layout: on the left profiles + options (Double-sided, Remove blank pages, Remove background) + Scan;
  in the middle the document list ("New Scan" + saved PDFs; persists across restarts: `adapters/library`,
  index `library.json` + `thumbs/` in the app data directory `…/scansnap-bridge/ScanSnack`; an old
  `ScanSnap` folder is migrated on start); on the right the pages (rotate/delete/reorder), name, folder,
  save. Saved documents can still be edited ("Save Changes" rewrites the PDF, renaming renames the
  file). Pages are read back from our own PDFs.
- Connection: `ScanWorker` in its own `QThread`, device via `ports.scanner.ScannerLocator`; a persistent
  session is opened right at start-up and re-established every 20 s (otherwise the scan button events do
  not arrive). The scan button starts a scan with the settings on the left.
- Start-up: without a saved or reachable IP, "Connect Scanner" appears first (enter an IP or
  "Search Network"; search and check run in the background). Connection errors during a scan open it as
  well.
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

### Mac bundle

Code: `packaging/`. Bundle ID `com.elte0.scansnack`.

- `packaging/build_mac.sh` → `dist/ScanSnack.app` + `dist/ScanSnack-<ver>.dmg`.
- `ScanSnack.spec`: Qt modules excluded, Info.plist including `NSLocalNetworkUsageDescription`; icon from
  `tools/make_icon.py`.
- The build checks the signature and runs `ScanSnack --selftest` (package data, JPEG, PDF).
- arm64, ad-hoc signed (on other Macs: right-click → Open), ~107 MB, DMG ~46 MB. On first launch macOS
  asks for local network access.

## eSCL bridge

Code: `adapters/escl`. Start: `scansnap serve [--port 8090] [--name …]`.

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
