# Desktop- und iOS-App „ScanSnack“

Entscheidungen: ADR 0003 (PySide6), ADR 0004 (BeeWare). Protokoll: `docs/protocol.md`.

## Desktop-App (`adapters/qt`, Start: `app/desktop.py`)

Start: `uv run --extra app scansnap-app [--ip …]`. Oberfläche auf Englisch, Code-Kommentare auf Deutsch.

- Aufbau: links Profile + Optionen (Double-sided, Remove blank pages, Remove background) + Scan;
  Mitte Dokumentliste („New Scan“ + gespeicherte PDFs, bleibt über Neustarts: `adapters/library`,
  Index `library.json` + `thumbs/` im App-Datenverzeichnis `…/scansnap-bridge/ScanSnack`, ein alter Ordner `ScanSnap` wird beim Start übernommen); rechts Seiten (drehen/löschen/sortieren),
  Name, Ordner, Speichern. Gespeicherte Dokumente lassen sich weiter bearbeiten („Save Changes“ schreibt
  das PDF neu, Umbenennen benennt die Datei um). Seiten werden aus den eigenen PDFs zurückgelesen.
- Verbindung: `ScanWorker` im eigenen `QThread`, Gerät über `ports.scanner.ScannerLocator`; dauerhafte
  Sitzung schon beim Start, Neuaufbau alle 20 s (sonst kommt die Scan-Taste nicht an). Die Scan-Taste
  startet einen Scan mit den Einstellungen links.
- Start: ohne gespeicherte oder erreichbare IP erscheint zuerst „Connect Scanner“ (IP eingeben oder
  „Search Network“; Suche und Prüfung im Hintergrund). Verbindungsfehler beim Scan öffnen ihn ebenfalls.
- Jeder Scan-Vorgang ist ein eigenes Dokument und wird **sofort nach dem Scan gespeichert** (Nutzerwunsch),
  Name „Scan JJJJ-MM-TT HH.MM“, Ordner „Save to“, optional öffnen – auch die Seiten vor einem Papierstau.
  Scheitert das Speichern, bleibt der Entwurf für „Save as PDF“ stehen. Scan ohne Seiten verschwindet.
- „Remove background“: `core.images.whiten` – Papierfarbe je Kanal (85.-Perzentil) → Weiß; dunkles Papier
  (< 150) bleibt unverändert. Läuft im selben Re-Encode wie Zuschnitt/Drehung.
- Papierfehler: `core.errors.FeedError(reason)`, Hinweistexte in `core.document.FEED_HELP`.
- Design: Primer (GitHub), Tokens aus @primer/primitives 11.10.0 in `adapters/qt/tokens.py` (Qt-frei,
  auch für iOS), `primer.py` setzt QPalette + Stylesheet, Fusion, Hell/Dunkel folgt dem System live.
  Default-Knopf = Primary (grün), gedämpfter Text per `muted`-Property.
- Falle: QListWidget im IconMode sortiert beim Ziehen nicht um → `PageList.dropEvent` sortiert selbst.

### Executables und Releases (`packaging/`, `tools/build_app.py`)

Build fürs laufende System: `uv run --extra app python tools/build_app.py` (`--no-pack`: nur App;
`packaging/build_mac.sh` ruft dasselbe auf). `ScanSnack.spec` ist plattformübergreifend (Qt-Module
ausgeschlossen); jeder Build startet die App mit `--selftest` (Paketdaten, JPEG, PDF). Icons aus
`tools/make_icon.py` (`.icns` nur auf macOS erzeugbar, `.ico`/`.png` überall).

| System | Ergebnis in `dist/` | Hinweise |
|---|---|---|
| macOS arm64 / x86_64 | `ScanSnack.app`, `ScanSnack-<ver>-macos-<arch>.dmg` | Bundle-ID `com.elte0.scansnack`, Info.plist inkl. `NSLocalNetworkUsageDescription`, ad hoc signiert, Prüfung per `codesign --verify` |
| Windows x86_64 | `ScanSnack/ScanSnack.exe`, `ScanSnack-<ver>-windows-x86_64.zip` | ohne Konsole, Icon `ScanSnack.ico` |
| Linux x86_64 | `ScanSnack/ScanSnack`, `ScanSnack-<ver>-linux-x86_64.tar.gz` | mit `scansnack.desktop` + `scansnack.png`; braucht Qt-Systembibliotheken (`libxcb-cursor0`, `libegl1` …) |

Die Architektur ist die des laufenden Pythons (kein universal2: Pillow liefert getrennte Wheels).
Mac arm64 ~107 MB, DMG ~46 MB; beim ersten Start fragt macOS nach Zugriff aufs lokale Netz.
Getestet: Mac (lokal), Linux-Build + Selftest + Start offscreen; Windows und Intel-Mac nur in CI.

**Version:** einzige Quelle `[project] version` in `pyproject.toml` (Major.Minor.Patch). Spec und
`uv.lock` lesen sie; `ios/pyproject.toml` wird von `tools/bump_version.py` mitgezogen (Test
`tests/test_version.py` prüft Gleichstand).

**Releases (GitHub Actions):**

- `Create Release` (`.github/workflows/create-release.yml`, manuell auf `main`, Auswahl patch/minor/major):
  `tools/check.sh` → `tools/bump_version.py` → Commit „Version X.Y.Z“ + Tag `vX.Y.Z` → Push → ruft `Release` auf.
- `Release` (`.github/workflows/release.yml`): bei jedem Tag `v*.*.*` (auch von Hand gepusht) Builds auf
  macos-latest (arm64), macos-15-intel, windows-latest, ubuntu-22.04 → GitHub-Release mit allen Paketen.
  Prüft, dass Tag und Version übereinstimmen. Tags, die ein Workflow mit `GITHUB_TOKEN` pusht, lösen keine
  Workflows aus – deshalb ruft `Create Release` den Release-Workflow direkt auf (`workflow_call`).
- Ist `main` per Branch-Schutz gesperrt, muss `github-actions[bot]` pushen dürfen (oder ein PAT als Secret).

**Signatur (optional, braucht Secrets):** macOS: Developer-ID-Zertifikat (p12 + Passwort) in einen
temporären Keychain importieren, `CODESIGN_IDENTITY` setzen, danach `xcrun notarytool submit --wait` +
`xcrun stapler staple` (Apple-ID, App-spezifisches Passwort, Team-ID). Windows: Code-Signing-Zertifikat
bzw. Azure Trusted Signing + `signtool`. Ohne Signatur: macOS „Dennoch öffnen“, Windows SmartScreen.

## eSCL-Bridge (`adapters/escl`, Start: `scansnap serve [--port 8090] [--name …]`)

- Nur Einzug (ADF), JPEG/PDF; Bonjour `_uscan._tcp` via zeroconf. Auto-`claim`, wenn das eigene Profil fehlt.
- macOS „Schwarzweiß“ = `ColorMode=Grayscale8` → gray-D4 (JPEG trotzdem 3 Kanäle, siehe protocol.md).
- Zuschnitt + Drehung in `Device.scan` (`core.images.process`, ein Re-Encode mit gleichen qtables);
  A4-Test 210×298 mm im macOS-Scanfenster bestätigt. `--no-crop` schaltet ab; dunkles Papier → kein Zuschnitt.
- Papierfehler → Job Aborted / `MediaJam`.

## iOS-App (`ios/`)

Bundle-ID `com.elte0.scansnack` (Briefcase: `bundle` + App-Name; der App-Name ist zugleich der Modulname,
daher das Paket `ios/src/scansnack`).

BeeWare: Briefcase 0.4.7, Toga 0.5.7, Python 3.14, Pillow-iOS-Wheel. Kern per Symlink `ios/src/scansnap`.
Die iOS-App ist ihr eigener Composition Root (nutzt ix1500- und library-Adapter direkt).

- `ios/src/scansnack/app.py`: Tabs „Scan“ (Profilliste, Optionen, Scan; dauerhafte Sitzung, Scan-Taste)
  und „Documents“, Seitenansicht (Delete/Share, UIActivityViewController via rubicon), Scanner-IP. Nach dem
  Scan öffnet die Seitenansicht. `theme.py` nimmt die Tokens aus `scansnap.adapters.qt.tokens`.
  Jeder Bildschirm wird per `render()` aus dem Zustand gebaut, auch beim Hell/Dunkel-Wechsel.
- Identität-Name `scansnap-iphone` → am Touchpanel als Benutzer wählen. PDFs in `Documents` (in „Dateien“
  sichtbar), Rest in `Library/Application Support/ScanSnack`.
- Toga-Fallen: `toga.App` hat schon `documents` u. a. (eigene Attribute nicht so nennen); Labels brechen
  nicht um; Farben mit Alpha nur als `rgb(...)`-Objekt; `margin` ist Außenabstand; in verdeckten Tabs feste
  Breite setzen.
- Bauen + aufs iPhone: `ios/build_ios.sh` (Briefcase braucht Homebrew-Python, uv-Python scheitert an
  libpython). Simulator: `cd ios && .venv/bin/briefcase run iOS -d <Sim-UDID>`.
- Auf dem iPhone 16e installiert und gestartet (2026-10-29, Team 2BCANYGFHU). Mit dem Scanner noch nicht getestet.

## Stand und nächste Schritte

- Desktop-App: nur mit Tests (offscreen) geprüft, noch nicht live mit dem Scanner (weder aus Quellen noch als .app).
- iOS: nach dem Umbau auf `core/ports/adapters` nur Importe angepasst, nicht neu gebaut.

1. App live testen, Feedback einarbeiten.
2. Scan-Taste in der App live testen.
3. Graustufen als 1-Kanal-JPEG; Zuschnitt mit Quittung/Visitenkarte/farbigem Papier testen.

Keine Priorität: Docker/Server, Profile.
