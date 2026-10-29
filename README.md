# scansnap-bridge

ScanSnap iX1500 per WLAN ohne ScanSnap Home: eigener Client für das proprietäre Protokoll,
eSCL/AirScan-Bridge fürs macOS-Scanfenster, Desktop-App (PySide6) und iOS-App (BeeWare).

## Nutzung

```
uv sync
uv run scansnap --ip 192.168.178.29 claim        # eigenen Benutzer + Profil eintragen, dann am Panel wählen
uv run scansnap --ip 192.168.178.29 scan --mode color --dpi 300 --out scans/
uv run scansnap --ip 192.168.178.29 serve        # eSCL-Server + Bonjour fürs macOS-Scanfenster
uv run --extra app scansnap-app                  # Desktop-App
```

Die Host-Identität (Host-ID, User-ID, MAC) liegt in `~/.config/scansnap-bridge/identity.json`.

## Download

Fertige Apps für macOS (Apple Silicon/Intel), Windows und Linux unter
[Releases](https://github.com/elte0/scansnack/releases). Selbst bauen: `uv run --extra app python tools/build_app.py`.

## Dokumentation

- `docs/protocol.md` – das Protokoll (Single Source of Truth), inkl. offener Fragen
- `docs/adr/` – Architektur- und Protokollentscheidungen
- `docs/apps.md` – Desktop- und iOS-App, Packaging, Releases
- Aufbau: hexagonal (`core` ← `ports` ← `adapters` ← `app`), siehe ADR 0002

## Checks

`tools/check.sh` (ruff, pyright strict, import-linter, pytest). Als Pre-commit-Hook:
`git config core.hooksPath .githooks`. Tests brauchen kein Gerät: ein Fake-Scanner spielt die
Mitschnitte unter `tests/fixtures/captures/` ab.
