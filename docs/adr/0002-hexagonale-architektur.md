---
status: akzeptiert
date: 2026-10-29
---

# Hexagonale Architektur mit erzwungenen Import-Regeln

## Kontext und Problemstellung

Ein reverse-engineertes Protokoll, mehrere Oberflächen (CLI, eSCL, Qt-Desktop, iOS) und Bildverarbeitung
lagen bisher flach in einem Paket. Oberflächen griffen direkt auf Sockets und Protokollklassen zu; die
Qt-App prüfte die Erreichbarkeit des Scanners sogar im GUI-Thread. Wie halten wir Protokoll, Domäne und
Oberflächen getrennt, sodass jede Seite für sich testbar bleibt?

## Entscheidungstreiber

* Protokollwissen ist unsicher und ändert sich; Änderungen sollen auf einen Adapter beschränkt bleiben.
* Core und Oberflächen ohne echtes Gerät testbar.
* Regeln ausführbar prüfen, nicht nur beschreiben.

## Erwogene Optionen

* Ports & Adapters (hexagonal) mit import-linter
* flaches Paket mit Konventionen

## Entscheidung

Gewählt: **Ports & Adapters**, geprüft mit import-linter (`pyproject.toml`, `[tool.importlinter]`):

```
scansnap/
  core/      Domäne: ScanSettings, Sheet, ScanPage, Entry, Fehler, Bildnachbearbeitung, PDF
  ports/     typing.Protocol: ScannerPort, ScannerLocator, OutputPort
  adapters/
    ix1500/  proprietäres WLAN-Protokoll (Sockets, Framing)
    escl/    eSCL/AirScan-Server + Bonjour
    library/ PDF-Ablage mit JSON-Index (OutputPort)
    qt/      Desktop-Oberfläche
  app/       Composition Root: cli.py, desktop.py
```

Regeln:

* Schichten `app` → `adapters` → `ports` → `core`; nur nach unten importieren.
* `core` und `ports` importieren weder Qt noch `socket`.
* Adapter importieren einander nicht.
* Nur `app/` erzeugt konkrete Adapter. Qt-Typen nur in `adapters/qt`; Protokoll-I/O nie im GUI-Thread.

Abweichungen gegenüber der Vorlage:

* Zusätzlicher Adapter `library` (Dateiablage), da die Ablage ein eigener Ausgang ist (`OutputPort`).
* Die iOS-App (`ios/`, eigenes Paket `scansnap_ios`) liegt außerhalb von `scansnap` und ist ihr eigener
  Composition Root; sie nutzt ix1500- und library-Adapter direkt und die Design-Tokens aus
  `adapters/qt/tokens.py` (Qt-frei). import-linter prüft sie nicht.
* Die CLI liegt in `app/cli.py`: Sie verdrahtet nicht nur, sondern enthält Experimentierbefehle, die
  bewusst auf ix1500-Interna zugreifen (Pairing, Status).

### Konsequenzen

* Gut: Protokollkorrekturen betreffen nur `adapters/ix1500`; eSCL und Qt sehen nur `ScannerPort`.
* Gut: Qt-Tests laufen mit Fake-Locator, eSCL-Tests mit Fake-Scanner, ix1500-Tests gegen Mitschnitte.
* Schlecht: Mehr Dateien und eine Indirektion (Locator), wo vorher direkt `Device(...)` stand.

### Bestätigung

`lint-imports` ist Teil der Checks (`tools/check.sh`, Pre-commit-Hook, CI).
