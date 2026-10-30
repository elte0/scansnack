---
status: accepted
date: 2026-10-29
---

# Desktop app in Python with PySide6

## Context and problem statement

Besides the macOS scan dialog (ADR 0001) there should be a dedicated app, modelled on ScanSnap Home
but reduced: choose a profile, scan, review pages, save as PDF, use the scan button on the device.

## Decision drivers

* User requirement: a real desktop app in Python – no web interface, no Swift.
* Reuse the Python protocol client without a process boundary.

## Considered options

* PySide6 (Qt)
* web interface
* native app in Swift

## Decision outcome

Chosen: **PySide6**, as the optional extra `app`; Mac bundle via PyInstaller (`packaging/`).
User interface in English, code comments in German (the comment language is superseded by
[ADR 0006](0006-english-as-repository-language.md): everything in English). Design: Primer tokens (`adapters/qt/tokens.py`).

### Consequences

* Good: one language stack; core and app share code directly.
* Bad: large bundle (~107 MB, DMG ~46 MB) despite excluded Qt modules.
* Bad: PySide6 only runs on iOS with a self-compiled Qt and without Pillow → ADR 0004.
