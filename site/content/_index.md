---
title: "ScanSnack"
---

ScanSnack lets you use a **ScanSnap iX1500** document scanner over Wi-Fi without ScanSnap Home. The
project has three parts:

- **[The ScanSnack app](app.md)** – a small desktop app for macOS, Windows and Linux that scans, saves
  every scan as a PDF and lets you tidy up the pages afterwards. [Download it here](app.md#download).
- **[The AirScan bridge](airscan.md)** – makes the scanner show up in *Image Capture* and *Preview* on
  the Mac, so you can scan from the macOS scan dialog without a driver.
- **[The protocol write-up](protocol.md)** – an independent description of the proprietary Wi-Fi
  protocol of the iX1500, reverse-engineered for interoperability: channels, frame layouts, sequences,
  device states and open questions.

{{< screenshot name="main" alt="ScanSnack main window" >}}

Everything in the protocol write-up was observed on the network or tested against a real device.
Anything not confirmed is listed as an open question. Raw captures are not published because they may
contain private data. The source code is on [GitHub](https://github.com/elte0/scansnack) under the MIT
license; see also the [disclaimer](disclaimer.md) on reverse engineering and trademarks.
