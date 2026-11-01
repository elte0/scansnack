# ScanSnap Wi-Fi Protocols

> **Summary.** ScanSnap scanners talk to their host software over Wi-Fi using an unencrypted,
> proprietary protocol: UDP for discovery and notifications, a TCP control channel for registration,
> and a TCP channel that tunnels SCSI commands for status and scanning. The models share this skeleton
> but differ in the frame magic, the registration and what else the control channel carries. Each model
> has its own write-up; this page lists them and what they have in common.

This is an independent interoperability project and is not affiliated with or endorsed by PFU or
Fujitsu. See the [disclaimer](disclaimer.md).

## Contents

- [Write-ups per model](#write-ups-per-model)
- [What the models share](#what-the-models-share)
- [Where they differ](#where-they-differ)
- [Sources and conventions](#sources-and-conventions)

## Write-ups per model

| Model | Write-up | Code | Status |
|---|---|---|---|
| ScanSnap iX1500 | [iX1500 protocol](protocol-ix1500.md) | `src/scansnap/adapters/ix1500/` | tested live |
| ScanSnap iX500 | [iX500 protocol](protocol-ix500.md) | `src/scansnap/adapters/ix500/` | tested live (firmware 0U00) |

Each write-up is the single source of truth for its model, including its own list of open questions.
Which other ScanSnap models exist and why they are not supported is in the [model list](models.md).
Code shared by both models (framing, SCSI tunnel, discovery probes, UDP notifications) lives in
`src/scansnap/adapters/scansnap_wifi/` ([ADR 0007](adr/0007-shared-scansnap-wifi-package.md)).

## What the models share

- **Ports:** UDP 52217 on the scanner (discovery, heartbeat), TCP 53219 (control), TCP 53218 (scan),
  UDP 55264/55265 on the host, UDP 53220 for scanner notices.
- **TCP framing:** a 32-byte client header and a 16-byte server header, both starting with
  `u32 length | magic`; every connection starts with a 16-byte greeting from the scanner.
- **Discovery probe:** the same 32-byte layout; the reply carries IP, ports, MAC, name, model and the
  ID of the registered host at the same offsets.
- **Registration (control 0x11):** host IP, notification port, date and time, an 8-byte host ID and the
  time zone bias, at the same body offsets (68, 76, 84).
- **SCSI tunnel:** the same request and response layout and the same command set for scanning
  (C2, D5, D8, E9, D4, REQUEST SENSE, E0, READ, D6). Pages arrive as ready-made JPEGs.

## Where they differ

| | iX1500 | iX500 |
|---|---|---|
| Frame magic | `ssNR` | `VENS` |
| Discovery reply | 144 B, serial at 132 | 132 B, serial only in the name |
| Registration body | 352 B, no secret | 96 B, with the encoded Wi-Fi password |
| Pairing | on the touch panel | over USB with ScanSnap Home; the client reuses the paired host ID |
| JSON calls (0x41), users, profiles | yes | no |
| Scan button | UDP datagram from the touch panel | UDP notification 0x01 to the registered host |
| C2 status length | 0x30 B | 0x20 B |
| Gray scans | gray D4 block (JPEG still has three channels) | always color, converted on the host |

Details and evidence are in the write-ups.

## Sources and conventions

Every write-up lists its own sources. Raw captures and test logs are **not published**, because they may
contain private data (scanned documents, device and network identifiers); the write-ups refer to them only
as private sources, never by file name.

- **iX1500:** captures of ScanSnap Home and live tests against the device.
- **iX500:** captures of ScanSnap Home 4.1.0, live tests with this client, and static analysis of
  ScanSnap Home's iX500 Wi-Fi code for the parameter blocks and the password encoding (for
  interoperability, see the [disclaimer](disclaimer.md#how-the-information-was-obtained)). The independent
  write-up [bramheerink/scansnap](https://github.com/bramheerink/scansnap) (GPL-2.0, commit `5706688`)
  agrees on ports, `VENS` framing, the discovery reply and the scan sequence; no code was taken from it.

Conventions in all write-ups: offsets are byte offsets from the start of the frame or body; integers are
big-endian unless marked little-endian. Anything not confirmed by observation is marked `UNVERIFIED` in the
write-up and `# UNVERIFIED:` in the code.
