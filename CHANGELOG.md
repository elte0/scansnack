# Changelog

All notable changes to ScanSnack are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

New entries go under *Unreleased*. When a release is made, the *Create Release* workflow turns
*Unreleased* into the new version's section and uses it as the release notes.

## [Unreleased]

### Added

- ScanSnap iX500 support in the app and on the command line (`scansnap ix500`): the app recognizes the
  model on its own; discovery, device info with page and roller counters, scans at 150 to 600 dpi with page
  size and multifeed detection, and scanning on the scanner's Scan button.
- With an iX500, the Scan button shows "Load paper" and is dimmed while the feeder is empty.
- Protocol write-up for the iX500 Wi-Fi dialect in `docs/protocol-ix500.md`.

## [0.3.2] - 2026-10-30

### Fixed

- The Windows download is built again; the release build failed while reading the project version.

## [0.3.1] - 2026-10-30

### Added

- A changelog: this page, also on the website, and in the release notes of every new version.
- A guide to the AirScan bridge, on the website and in `docs/airscan.md`: how to start it, how to scan
  from Image Capture (*Digitale Bilder*) and Preview, supported options, limitations and
  troubleshooting. The bridge is also listed among the features.

### Changed

- The README and the website call the project ScanSnack throughout.
- The command line, log output and error messages are now in English throughout, as are the code
  comments and developer docs.

## [0.3.0] - 2026-10-29

### Added

- **Updates with one click**: on start the app checks GitHub for a new release and offers to install
  it, then restarts as the new version. *Later* asks again next time, *Skip This Version* waits for the
  next one, and the check can be turned off in the *Help* menu.
- Releases carry a `SHA256SUMS` file; the app checks every download against it.
- The website has an app page with screenshots, the feature list and download buttons for the latest
  release.

## [0.2.0] - 2026-10-29

First release with ready-made apps.

### Added

- Desktop app "ScanSnack" for macOS (Apple Silicon and Intel), Windows and Linux: scan over Wi-Fi,
  profiles, double-sided, remove blank pages and background, every scan saved right away as a PDF,
  document list with page editing, help for paper problems, light and dark mode.
- The *Scan* button on the scanner's touch panel starts a scan in the app.
- Protocol client and command line tool `scansnap` (claim, scan, eSCL/AirScan bridge for the macOS
  scan dialog).
- Protocol write-up, disclaimer and website on GitHub Pages.

[Unreleased]: https://github.com/elte0/scansnack/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/elte0/scansnack/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/elte0/scansnack/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/elte0/scansnack/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/elte0/scansnack/releases/tag/v0.2.0
