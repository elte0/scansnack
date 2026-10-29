---
status: accepted
date: 2026-10-29
---

# iOS app with BeeWare/Toga instead of PySide6

## Context and problem statement

The app from ADR 0003 should also run on the iPhone and use the same Python core.

## Decision drivers

* Reuse the core (protocol, image post-processing with Pillow, PDF) unchanged.
* Build it and get it onto the device with reasonable effort.

## Considered options

* PySide6 on iOS (possible since Qt 6.12)
* BeeWare: Briefcase + Toga

## Decision outcome

Chosen: **BeeWare** (Briefcase 0.4.7, Toga 0.5.7, Python 3.14, Pillow iOS wheel) under `ios/`.
PySide6 on iOS would need a self-compiled Qt/PySide and would run without Pillow.

### Consequences

* Good: core included via the symlink `ios/src/scansnap`, no copy.
* Good: design tokens shared with the desktop app (`adapters/qt/tokens.py`, Qt-free).
* Bad: a second user interface with its own quirks (Toga pitfalls, see [apps](../apps.md)).
* Bad: builds only with Homebrew Python (uv's Python fails because of libpython).
