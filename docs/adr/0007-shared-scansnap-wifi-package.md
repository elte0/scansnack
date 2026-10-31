---
status: proposed
date: 2026-10-30
---

# Shared ScanSnap Wi-Fi protocol package

## Context and problem statement

The iX500 speaks an older dialect of the iX1500 Wi-Fi protocol ([protocol-ix500](../protocol-ix500.md)):
the same TCP framing, SCSI tunnel, discovery probes and UDP notifications, with the magic `VENS` instead of
`ssNR`. Adapters must not import each other (ADR 0002), so an iX500 adapter could only reuse the iX1500 code
by copying it. Where does the code both models share live?

## Considered options

* a separate `adapters/ix500` with its own copy of framing, SCSI channel, discovery and notifications
* the iX500 as a second dialect inside `adapters/ix1500`
* a shared package `adapters/scansnap_wifi` with the common primitives, the magic as a parameter

## Decision outcome

Chosen: **shared package `adapters/scansnap_wifi`**. It holds the framing (`wire`), the SCSI tunnel (`scsi`),
discovery probes and heartbeat (`discovery`) and the notification listener (`notify`). Each model adapter
binds it to its magic and adds only what differs: registration, discovery reply, scan parameters and reads.
`scansnap_wifi` imports no adapter (import-linter contract); model adapters stay independent of each other.

### Consequences

* Good: the iX500 adapter contains only iX500 specifics; a third model reuses the same primitives.
* Good: `adapters/ix1500` is unchanged; `tests/test_scansnap_wifi.py` checks that the shared code produces
  the iX1500's bytes with `ssNR`, so moving the iX1500 over is mechanical.
* Bad: until the iX1500 moves over, its own framing, SCSI channel, discovery and button listener exist twice.
