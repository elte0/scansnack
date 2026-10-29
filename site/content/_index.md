---
title: "ScanSnap iX1500 Wi-Fi Protocol"
---

An independent write-up of the proprietary Wi-Fi protocol of the **ScanSnap iX1500** document scanner,
reverse-engineered for interoperability. It is the basis of
[scansnap-bridge](https://github.com/elte0/scansnack), an open-source client that scans without
ScanSnap Home and makes the scanner available in the macOS scan dialog via eSCL/AirScan.

- [Protocol write-up](protocol.md) – channels, frame layouts, sequences, device states and open
  questions.
- [Disclaimer](disclaimer.md) – reverse engineering, trademarks, no affiliation, no warranty.

Everything described here was observed on the network or tested against a real device. Anything not
confirmed is listed as an open question. Raw captures are not published because they may contain
private data.
