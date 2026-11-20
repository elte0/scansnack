# Dedicated iX1300 Return Scan receiver

- Status: accepted

## Context and problem statement

The iX1300 uses VENS framing and discovery. Its front-slot scan uses separate setup, transfer, and release connections. A headless Linux receiver needs to keep listening across multiple scans and deliver complete PDFs to an existing folder.

## Decision

The iX1300 adapter owns registration, session recovery, and the front-slot scan sequence. Shared VENS control and discovery code lives in `scansnap_wifi` under [ADR 0007](0007-shared-scansnap-wifi-package.md).

The application exposes `scansnap ix1300 --ip SCANNER_IP listen --out WATCHED_FOLDER`. It runs in the foreground and publishes completed duplex PDFs with a same-directory rename.

## Consequences

The receiver reuses the scanner's existing enrollment. The CLI can run under a Linux service supervisor with ScanSnap Home closed. The transfer settings cover single-sheet duplex Return Scan.
