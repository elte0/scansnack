# ScanSnack

Use the ScanSnap iX1500 or iX500 over Wi-Fi without ScanSnap Home: an independent client for their proprietary
protocol, an AirScan bridge that makes the scanner show up in Image Capture and Preview on the Mac, a
desktop app (PySide6) and an iOS app (BeeWare). The Python package is called `scansnap-bridge` and
provides the commands `scansnap` and `scansnap-app`.

> **Unofficial project.** Not affiliated with or endorsed by PFU or Fujitsu. ScanSnap is a trademark of
> PFU Limited. The protocol was reverse-engineered for interoperability. See the
> [disclaimer](docs/disclaimer.md).

## Contents

- [Features](#features)
- [Usage](#usage)
- [Documentation](#documentation)
- [Development](#development)
- [License](#license)

## Features

- **Protocol client** for discovery, pairing, profiles and duplex scanning over Wi-Fi.
- **AirScan bridge**: `scansnap serve` makes the scanner show up as a network scanner in the macOS scan
  dialog, so you can scan from Image Capture (*Digitale Bilder*), Preview and other apps without a
  driver. Feeder, duplex, color or grayscale, 150–600 dpi, JPEG or PDF. See [AirScan bridge](docs/airscan.md)
  ([website](https://elte0.github.io/scansnack/airscan/)).
- **Desktop app "ScanSnack"**: scans over Wi-Fi and saves every scan as a PDF. Its features are listed
  on the [app page](site/content/app.md) ([website](https://elte0.github.io/scansnack/app/)).
- **iOS app** built on the same Python core.

## Usage

```sh
uv sync
uv run scansnap --ip <scanner-ip> claim        # create our own user + profile, then select it on the panel
uv run scansnap --ip <scanner-ip> scan --mode color --dpi 300 --out scans/
uv run scansnap --ip <scanner-ip> serve        # AirScan bridge for Image Capture and Preview
uv run --extra app scansnap-app                # desktop app
```

Broadcast discovery is unreliable, so pass the scanner's IP address with `--ip`.
The host identity (host ID, user ID, MAC) is stored in `~/.config/scansnap-bridge/identity.json`.

## Download

Ready-made apps for macOS (Apple Silicon and Intel), Windows and Linux are on the
[Releases](https://github.com/elte0/scansnack/releases) page. To build one yourself:
`uv run --extra app python tools/build_app.py` (see [Apps](docs/apps.md#executables-and-releases)).

## Documentation

- [AirScan bridge](docs/airscan.md) – scanning from Image Capture and Preview: start, options, limits,
  troubleshooting
- [Protocol](docs/protocol.md) – the Wi-Fi protocol (single source of truth), including open questions
- [Architecture decisions](docs/adr/README.md) – architecture and protocol decisions (ADRs)
- [Apps](docs/apps.md) – desktop and iOS app, eSCL bridge, packaging, releases
- [Changelog](CHANGELOG.md) – what changed in each version
- [Disclaimer](docs/disclaimer.md) – reverse engineering, trademarks, no warranty
- Structure: hexagonal (`core` ← `ports` ← `adapters` ← `app`), see ADR 0002

The website [elte0.github.io/scansnack](https://elte0.github.io/scansnack/) presents the app (screenshots,
features, downloads of the latest release), explains the AirScan bridge and publishes the protocol
write-up. It is built with Hugo from
`site/` (see [site/README.md](site/README.md)).

## Development

`tools/check.sh` runs all checks (ruff, pyright strict, import-linter, pytest). To run them as a
pre-commit hook: `git config core.hooksPath .githooks`. The tests need no device: a fake scanner
replays the anonymised captures in `tests/fixtures/captures/`.

## License

MIT, see [LICENSE](LICENSE).
