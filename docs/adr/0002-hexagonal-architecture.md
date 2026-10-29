---
status: accepted
date: 2026-10-29
---

# Hexagonal architecture with enforced import rules

## Context and problem statement

A reverse-engineered protocol, several user interfaces (CLI, eSCL, Qt desktop, iOS) and image
processing used to live flat in one package. User interfaces accessed sockets and protocol classes
directly; the Qt app even checked whether the scanner was reachable in the GUI thread. How do we keep
protocol, domain and user interfaces apart so that each side stays testable on its own?

## Decision drivers

* Protocol knowledge is uncertain and changes; changes should stay confined to one adapter.
* Core and user interfaces testable without a real device.
* Rules checked automatically, not just described.

## Considered options

* Ports & adapters (hexagonal) with import-linter
* flat package with conventions

## Decision outcome

Chosen: **ports & adapters**, checked with import-linter (`pyproject.toml`, `[tool.importlinter]`):

```
scansnap/
  core/      domain: ScanSettings, Sheet, ScanPage, Entry, errors, image post-processing, PDF
  ports/     typing.Protocol: ScannerPort, ScannerLocator, OutputPort
  adapters/
    ix1500/  proprietary Wi-Fi protocol (sockets, framing)
    escl/    eSCL/AirScan server + Bonjour
    library/ PDF storage with a JSON index (OutputPort)
    qt/      desktop user interface
  app/       composition root: cli.py, desktop.py
```

Rules:

* Layers `app` → `adapters` → `ports` → `core`; imports only go downwards.
* `core` and `ports` import neither Qt nor `socket`.
* Adapters do not import each other.
* Only `app/` creates concrete adapters. Qt types only in `adapters/qt`; protocol I/O never in the GUI
  thread.

Deviations from the template:

* Additional adapter `library` (file storage), because storage is an output of its own (`OutputPort`).
* The iOS app (`ios/`, its own package `scansnack`) lives outside `scansnap` and is its own
  composition root; it uses the ix1500 and library adapters directly and the design tokens from
  `adapters/qt/tokens.py` (Qt-free). import-linter does not check it.
* The CLI lives in `app/cli.py`: it does not only wire things up but also contains experimental
  commands that deliberately access ix1500 internals (pairing, status).

### Consequences

* Good: protocol fixes only touch `adapters/ix1500`; eSCL and Qt only see `ScannerPort`.
* Good: Qt tests run with a fake locator, eSCL tests with a fake scanner, ix1500 tests against captures.
* Bad: more files and one indirection (locator) where `Device(...)` used to be called directly.

### Confirmation

`lint-imports` is part of the checks (`tools/check.sh`, pre-commit hook, CI).
