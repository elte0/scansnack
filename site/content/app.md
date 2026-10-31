---
title: "ScanSnack app"
description: "A small desktop app that scans with a ScanSnap iX1500 over Wi-Fi, without ScanSnap Home."
layout: app
# Download buttons: the files of the latest release are found by the end of their name
# (names from tools/build_app.py: ScanSnack-<version>-<os>-<arch>.<ext>).
platforms:
  - name: macOS
    detail: Apple Silicon (M1 or newer)
    suffix: -macos-arm64.dmg
  - name: macOS
    detail: Intel
    suffix: -macos-x86_64.dmg
  - name: Windows
    detail: Windows 10/11, 64-bit
    suffix: -windows-x86_64.zip
  - name: Linux
    detail: x86_64, tar.gz
    suffix: -linux-x86_64.tar.gz
---

ScanSnack scans with a **ScanSnap iX1500** over Wi-Fi and saves every scan as a PDF, without
ScanSnap Home, an account or a cloud service. It talks to the scanner directly, using the protocol
described in the [protocol write-up](protocol.md).

## Features

- **Scan over Wi-Fi** – connect by IP address or let the app search the network. The connection is kept
  open, so the *Scan* button on the scanner's touch panel starts a scan in the app.
- **Profiles** – color or black & white documents at 300 dpi, a quick 150 dpi mode and photos at
  600 dpi.
- **Double-sided**, **remove blank pages** and **remove background** (turns gray or yellowish paper into
  pure white).
- **Cropped to the paper** – pages come out in the size of the sheet, the right way up.
- **Saved right away** – every scan becomes its own PDF in the folder you choose, named after date and
  time. Pages scanned before a paper jam are kept.
- **Document list** – your scans stay in the list across restarts. Open a document to rotate, delete or
  reorder pages (drag and drop) and rename it; *Save Changes* rewrites the PDF.
- **Clear help for paper problems** – paper jam, double feed, open cover and similar errors come with a
  hint on what to do.
- **Help for other models** – *Help → Report Unsupported Scanner* checks a ScanSnap model that is not
  supported yet, without changing anything on it, and prepares an anonymized report you can read and send
  by email, so that support for the model can be added.
- **Light and dark mode** – follows the system setting.
- **Updates with one click** – on start the app checks GitHub for a new release and offers to install it;
  it then restarts as the new version. *Later* asks again next time, *Skip This Version* waits for the
  next one, and the check can be turned off in the *Help* menu.
- **Scan from Image Capture and Preview** – the [AirScan bridge](airscan.md) makes the scanner show up in
  the macOS scan dialog (*Image Capture*, German *Digitale Bilder*, and *Preview*), without a driver. It
  is a separate command (`scansnap serve`) that runs from the source code, not part of the app download.
- **Open source** – MIT license, no telemetry; the only request besides the scanner is the update check
  on GitHub. The source code is on
  [GitHub](https://github.com/elte0/scansnack).

What changed in each version is listed in the [changelog](changelog.md).

> ScanSnack is an early version. It is developed and tested on macOS; the Windows and Linux builds are
> made automatically and have not been tested with a scanner yet. Only the iX1500 is supported; for
> another model, please send a report via *Help → Report Unsupported Scanner*.

## Getting started

1. Download the file for your system above.
2. Install it:
   - **macOS:** open the DMG and drag ScanSnack to *Applications*. The app is not signed by Apple, so
     macOS blocks the first launch: choose *Open Anyway* under *System Settings → Privacy & Security*.
     When asked, allow access to the local network.
   - **Windows:** unzip the file and start `ScanSnack\ScanSnack.exe`. At the SmartScreen prompt choose
     *More info → Run anyway*.
   - **Linux:** unpack the archive and start `ScanSnack/ScanSnack`. It needs the usual Qt system
     libraries (for example `libxcb-cursor0` and `libegl1`); `scansnack.desktop` and `scansnack.png` are
     included for a menu entry.
3. Switch the scanner on, quit ScanSnap Home if it is running, and connect: enter the scanner's IP
   address or choose *Search Network*.

4. Put paper in the feeder and press *Scan*, in the app or on the scanner.

{{< screenshot name="connect" class="small" alt="The Connect Scanner dialog with the scanner's IP address and the buttons Search Network, Connect and Cancel" caption="Connecting to the scanner on first launch" >}}


ScanSnack is an independent project and not affiliated with PFU or Fujitsu; see the
[disclaimer](disclaimer.md).
