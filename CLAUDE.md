# scansnap-bridge – Kontext für Claude Code

ScanSnap iX1500 per WLAN ohne ScanSnap Home: eigener Client fürs proprietäre Protokoll, eSCL/AirScan-Bridge
fürs macOS-Scanfenster, Desktop-App (PySide6), iOS-App (BeeWare). Überblick: `README.md`, Apps: `docs/apps.md`.

## Architektur (hexagonal, ADR 0002)

```
src/scansnap/
  core/      Domäne: ScanSettings, Sheet, ScanPage, Entry, Fehler, Bildnachbearbeitung, PDF
  ports/     typing.Protocol: ScannerPort, ScannerLocator, OutputPort
  adapters/  ix1500/ (WLAN-Protokoll), escl/ (eSCL + Bonjour), library/ (PDF-Ablage), qt/ (GUI)
  app/       Composition Root: cli.py, desktop.py – einzige Stelle, die konkrete Adapter erzeugt
```

- `core` und `ports` kennen weder Qt noch Sockets noch Adapter. Adapter importieren einander nicht.
- Qt-Typen (`QImage`, Signals, `QObject`) nur in `adapters/qt`; der Core arbeitet mit `bytes`/PIL.
- Protokoll-I/O nie im GUI-Thread: Worker-Thread, Ergebnisse per Signal in die GUI.
- Durchgesetzt per import-linter (`[tool.importlinter]` in `pyproject.toml`).

## Reverse Engineering

- `docs/protocol.md` ist die Single Source of Truth fürs Protokoll (Pakete, Sequenzen, Zustände,
  offene Fragen). Bei jeder neuen Erkenntnis aktualisieren. Nichts erfinden.
- Beobachtetes von Vermutetem trennen: Unbestätigtes im Code mit `# UNVERIFIED:` markieren und in
  `protocol.md` unter „Offene Fragen“ führen.
- Golden Tests: aus pcap extrahierte Mitschnitte in `tests/fixtures/captures/` (`tools/make_fixtures.py`),
  abgespielt vom Fake-Scanner `tests/mock_scanner.py`. Tests setzen kein echtes Gerät voraus.
  Mitschnitte nie „passend machen“, damit ein Test grün wird.
- Rohcaptures (`captures/`) und `ssh-identity.json` nie committen (Gerätedaten bzw. gescannte Dokumente).

## Entscheidungen

ADRs im MADR-Format unter `docs/adr/NNNN-titel.md`. Bestehende ADRs nicht stillschweigend unterlaufen;
bei Änderungsbedarf einen neuen ADR anlegen, der den alten ersetzt.

## Checks

```sh
tools/check.sh    # ruff check . && ruff format --check . && pyright (strict) && lint-imports && pytest
```

Eine Aufgabe ist erst erledigt, wenn alle Checks grün sind. Pre-commit-Hook: `git config core.hooksPath .githooks`.

## Konventionen

- Antworten an den Nutzer kurz und auf Deutsch. App-Oberflächen auf Englisch, Code-Kommentare auf Deutsch.
- Live-Tests: Mac 192.168.178.37, Scanner 192.168.178.29, ScanSnap Home beendet; Discovery per Broadcast
  ist unzuverlässig → `--ip` verwenden.
