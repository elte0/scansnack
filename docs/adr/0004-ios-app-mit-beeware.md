---
status: akzeptiert
date: 2026-10-29
---

# iOS-App mit BeeWare/Toga statt PySide6

## Kontext und Problemstellung

Die App aus ADR 0003 soll auch auf dem iPhone laufen und denselben Python-Kern nutzen.

## Entscheidungstreiber

* Kern (Protokoll, Bildnachbearbeitung mit Pillow, PDF) unverändert wiederverwenden.
* Mit vertretbarem Aufwand bauen und aufs Gerät bringen.

## Erwogene Optionen

* PySide6 auf iOS (seit Qt 6.12 möglich)
* BeeWare: Briefcase + Toga

## Entscheidung

Gewählt: **BeeWare** (Briefcase 0.4.7, Toga 0.5.7, Python 3.14, Pillow-iOS-Wheel) unter `ios/`.
PySide6 auf iOS bräuchte ein selbst kompiliertes Qt/PySide und liefe ohne Pillow.

### Konsequenzen

* Gut: Kern per Symlink `ios/src/scansnap` eingebunden, keine Kopie.
* Gut: Design-Tokens geteilt mit der Desktop-App (`adapters/qt/tokens.py`, Qt-frei).
* Schlecht: zweite Oberfläche mit eigenen Eigenheiten (Toga-Fallen, siehe docs/apps.md).
* Schlecht: Bauen nur mit Homebrew-Python (uv-Python scheitert an libpython).
