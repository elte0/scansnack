---
status: accepted
date: 2026-10-31
---

# iX500 as a beta model in the existing Wi-Fi adapter, with an explicit model choice

## Context and problem statement

ScanSnack was built and tested for the iX1500 only. A third-party write-up of the iX500 Wi-Fi protocol
(see "Sources and conventions" in `docs/protocol.md`) shows that the iX500 uses the same ports, UDP probes
and SCSI tunnel, but a different frame magic, a shorter registration with a secret pairing key and no
touch panel, users or profiles. We own no iX500, so nothing can be tested on a device. The write-up is
GPL-2.0; ScanSnack is MIT. Users also need to know which models work at all.

## Decision drivers

* Reuse the SCSI layer, which is the same on both models.
* Keep the import rules of ADR 0002: adapters do not import each other.
* Never present untested behavior as verified (`# UNVERIFIED:`, `docs/protocol.md`).
* Keep the MIT license: take facts from the write-up, not code.
* One place that says which model is supported, used by the app and by the docs.

## Considered options

* a separate `adapters/ix500/` package
* iX500 support inside `adapters/ix1500/` (module `ix500.py`), with small model switches in the shared code
* rename `adapters/ix1500/` to a model-neutral name first

## Decision outcome

Chosen: **iX500 inside `adapters/ix1500/`**. A separate package would have to import the framing and SCSI
modules of `ix1500/`, which the independence contract forbids, or copy them. Renaming the package would
touch every import, test and document for no functional gain; the package name stays for now and stands
for "Wi-Fi ScanSnaps".

* `wire`, `scsi`, `discovery` and `scanner` take the model differences as parameters or overridable
  methods (magic, C2 length, host IP field, D5/D4/READ details). `ix500.py` holds what is iX500-only:
  registration with the key, short sessions, the key capture.
* `core/catalog.py` lists all ScanSnap models with a support level (supported, beta, not supported).
  `docs/models.md` shows the same table; a test keeps both in sync. The iX500 is **beta** until someone
  tests it on a device.
* The `ScannerLocator` port takes the model (and the pairing key) in `open`. The desktop app asks for the
  model when connecting and lists all models, with the unsupported ones greyed out. Existing settings
  without a model are treated as iX1500, the only model before.
* The code is written from the write-up's facts only; nothing is copied from its GPL code.

### Consequences

* Good: one SCSI implementation for both models; users see at a glance what works.
* Good: the support matrix cannot drift from the app.
* Bad: the package name `ix1500` is misleading for the iX500 part; a later rename needs its own change.
* Bad: iX500 users must obtain their pairing key with ScanSnap Home once (`scansnap capture-key`).
