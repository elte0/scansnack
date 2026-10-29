---
status: accepted
date: 2026-10-29
---

# eSCL bridge instead of a native macOS driver

## Context and problem statement

The ScanSnap iX1500 should be usable over Wi-Fi from the macOS scan dialog (Image Capture, Preview,
print dialog) without ScanSnap Home. The scanner only speaks a proprietary protocol
([protocol](../protocol.md)). How do we get it into the scan dialog?

## Decision drivers

* No changes to the system: no driver installation, no kernel or system extension, no driver signing.
* Later run as a Docker container with host networking on a server (one service for every computer on
  the network).
* Protocol client in Python, testable against captures.

## Considered options

* eSCL/AirScan server (HTTP + Bonjour `_uscan._tcp`) that forwards jobs to the scanner
* native macOS scanner driver (Image Capture driver)

## Decision outcome

Chosen: **eSCL/AirScan bridge**. macOS discovers eSCL scanners via Bonjour by itself and needs no
driver for them; the same bridge can run on a server and then serve every Mac (and other eSCL
clients) on the network.

### Consequences

* Good: runs without any installation on the clients; bridge and protocol client stay in Python.
* Good: the feature set is limited by the eSCL standard and therefore clear (feeder only, JPEG/PDF).
* Bad: features that eSCL does not cover (touch panel button, profiles) need their own user interface
  (desktop app, see ADR 0003).
* Bad: an additional service that has to be running whenever someone wants to scan.

### Confirmation

Scanning from the macOS scan dialog confirmed live (2026-10-29): capabilities → POST ScanJobs 201 →
NextDocument 200 → 404 → DELETE; color simplex/duplex, duplex PDF, black and white.
Tests: `tests/test_escl.py`.
