---
status: akzeptiert
date: 2026-10-29
---

# Desktop-App in Python mit PySide6

## Kontext und Problemstellung

Neben dem macOS-Scanfenster (ADR 0001) soll es eine eigene App geben, angelehnt an ScanSnap Home,
reduziert: Profil wählen, scannen, Seiten sichten, als PDF ablegen, Scan-Taste am Gerät.

## Entscheidungstreiber

* Nutzerwunsch: echte Desktop-App in Python – keine Weboberfläche, kein Swift.
* Wiederverwendung des Python-Protokoll-Clients ohne Prozessgrenze.

## Erwogene Optionen

* PySide6 (Qt)
* Weboberfläche
* native App in Swift

## Entscheidung

Gewählt: **PySide6**, als optionales Extra `app`; Mac-Bundle per PyInstaller (`packaging/`).
Oberfläche auf Englisch, Code-Kommentare auf Deutsch. Design: Primer-Tokens (`adapters/qt/tokens.py`).

### Konsequenzen

* Gut: ein Sprach-Stack; Kern und App teilen Code direkt.
* Schlecht: großes Bundle (~107 MB, DMG ~46 MB) trotz ausgeschlossener Qt-Module.
* Schlecht: PySide6 läuft auf iOS nur mit selbst kompiliertem Qt und ohne Pillow → ADR 0004.
