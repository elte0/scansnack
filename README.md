# scansnap-bridge

Use the ScanSnap iX1500 over Wi-Fi without ScanSnap Home: an independent client for its proprietary
protocol, an eSCL/AirScan bridge for the macOS scan dialog, a desktop app (PySide6) and an iOS app
(BeeWare).

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
- **eSCL/AirScan bridge**: the scanner shows up in the macOS scan dialog (Image Capture, Preview).
- **Desktop app "ScanSnack"**: choose a profile, scan, review pages, save as PDF, react to the scan
  button on the touch panel.
- **iOS app** built on the same Python core.

## Usage

```sh
uv sync
uv run scansnap --ip <scanner-ip> claim        # create our own user + profile, then select it on the panel
uv run scansnap --ip <scanner-ip> scan --mode color --dpi 300 --out scans/
uv run scansnap --ip <scanner-ip> serve        # eSCL server + Bonjour for the macOS scan dialog
uv run --extra app scansnap-app                # desktop app
```

Broadcast discovery is unreliable, so pass the scanner's IP address with `--ip`.
The host identity (host ID, user ID, MAC) is stored in `~/.config/scansnap-bridge/identity.json`.

## Download

Ready-made apps for macOS (Apple Silicon and Intel), Windows and Linux are on the
[Releases](https://github.com/elte0/scansnack/releases) page. To build one yourself:
`uv run --extra app python tools/build_app.py` (see [Apps](docs/apps.md#executables-and-releases)).

## Documentation

- [Protocol](docs/protocol.md) – the Wi-Fi protocol (single source of truth), including open questions
- [Architecture decisions](docs/adr/README.md) – architecture and protocol decisions (ADRs)
- [Apps](docs/apps.md) – desktop and iOS app, eSCL bridge, packaging, releases
- [Disclaimer](docs/disclaimer.md) – reverse engineering, trademarks, no warranty
- Structure: hexagonal (`core` ← `ports` ← `adapters` ← `app`), see ADR 0002

The website [elte0.github.io/scansnack](https://elte0.github.io/scansnack/) presents the app (screenshots,
features, downloads of the latest release) and publishes the protocol write-up. It is built with Hugo from
`site/` (see [site/README.md](site/README.md)).

## Development

`tools/check.sh` runs all checks (ruff, pyright strict, import-linter, pytest). To run them as a
pre-commit hook: `git config core.hooksPath .githooks`. The tests need no device: a fake scanner
replays the anonymised captures in `tests/fixtures/captures/`.

## License

MIT, see [LICENSE](LICENSE).
