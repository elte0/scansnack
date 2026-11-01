# Disclaimer: reverse engineering and trademarks

> **Summary.** This is an independent, unofficial project. It documents the Wi-Fi protocol of the
> ScanSnap iX1500 and iX500 so that a scanner its owner has lawfully acquired can be used with other software. It is not
> affiliated with, endorsed by or supported by PFU or Fujitsu. Use it at your own risk.

## Contents

- [No affiliation](#no-affiliation)
- [Trademarks](#trademarks)
- [How the information was obtained](#how-the-information-was-obtained)
- [Purpose: interoperability](#purpose-interoperability)
- [Captures and personal data](#captures-and-personal-data)
- [No warranty](#no-warranty)

## No affiliation

This project is not affiliated with, authorised, sponsored or endorsed by PFU Limited, Ricoh, Fujitsu
Limited or any of their subsidiaries. It is not an official ScanSnap product and receives no support
from the manufacturer. Please do not contact the manufacturer's support about problems with this
software.

## Trademarks

ScanSnap and ScanSnap Home are trademarks or registered trademarks of PFU Limited. Fujitsu is a
trademark of Fujitsu Limited. Apple, macOS, iPhone and AirScan are trademarks of Apple Inc. All other
product names and trademarks are the property of their respective owners.

These names are used only to describe which device and which software this project works with
(nominative use). Their use does not imply any relationship with, or endorsement by, the trademark
owners. The project's own names ("scansnap-bridge", "ScanSnack") are descriptive working names and
are not meant to suggest an official product.

## How the information was obtained

The protocol description was obtained by observing the network traffic between the original host
software (ScanSnap Home) and a scanner owned by the author, and by sending requests to that scanner
and observing its responses. Some error texts were cross-checked against the open-source SANE
`fujitsu` backend (see the [open questions](protocol-ix1500.md#open-questions)).

For the iX500, the scan parameter blocks, the page sizes and the encoding of the scanner's Wi-Fi password
in the registration were taken from a static analysis of ScanSnap Home's iX500 Wi-Fi code, done only to
make the scanner work with this independent client (see [Purpose: interoperability](#purpose-interoperability)).
Only facts and constants needed to talk to the scanner were written down; no code of ScanSnap Home was
copied. The independent write-up [bramheerink/scansnap](https://github.com/bramheerink/scansnap) was used
for comparison only; none of its code (GPL-2.0) is part of this project. Sources per model are listed in
the [iX1500](protocol-ix1500.md#sources-and-conventions) and [iX500](protocol-ix500.md#sources) write-ups.

This repository contains no code, firmware or binaries of the manufacturer. The test fixtures and the
profile template (`profile_template.json`) contain only protocol data as observed on the network.
Statements that have not been confirmed by observation are marked as unverified.

## Purpose: interoperability

The sole purpose of this project is interoperability: to let owners use their own scanner with other
operating systems and programs (for example the macOS scan dialog via eSCL/AirScan, or a custom app)
without the manufacturer's software. Many jurisdictions explicitly allow analysing a product for this
purpose, for example Article 6 of the EU Software Directive (2009/24/EC). This note is not legal
advice.

## Captures and personal data

Raw network captures and test logs are not published, because they may contain scanned documents and
device or network identifiers. The test fixtures in `tests/fixtures/captures/` are anonymised extracts
without image data.

## No warranty

The software and documentation are provided "as is", without warranty of any kind (see `LICENSE`).
Using an unofficial client may put the scanner into an unexpected state, change its stored users and
profiles, or affect the manufacturer's warranty or support. Use it at your own risk.
