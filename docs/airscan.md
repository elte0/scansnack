# AirScan bridge

> **Summary.** The AirScan bridge makes the ScanSnap iX1500 show up as a network scanner on your Mac,
> so you can scan straight from *Image Capture* (German: *Digitale Bilder*), *Preview* and other apps
> that use the macOS scan dialog. It needs no driver and no ScanSnap Home: a small server on your Mac
> speaks the standard eSCL/AirScan protocol to macOS and the iX1500's own Wi-Fi protocol to the scanner.
> It is started from the command line with `scansnap serve` and is not part of the ScanSnack app.

## Contents

- [How it works](#how-it-works)
- [Start the bridge](#start-the-bridge)
- [Scan from Image Capture or Preview](#scan-from-image-capture-or-preview)
- [Supported options](#supported-options)
- [Command-line options](#command-line-options)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

## How it works

macOS finds network scanners that speak eSCL (also called AirScan) on its own, via Bonjour, and needs no
driver for them. The iX1500 does not speak eSCL over Wi-Fi, only a proprietary protocol (see the
[protocol write-up](protocol.md)). The bridge sits in between:

1. It announces itself on the local network as an eSCL scanner (Bonjour service `_uscan._tcp`).
2. When you click *Scan* in the macOS scan dialog, macOS sends the job to the bridge.
3. The bridge connects to the scanner over Wi-Fi, selects its own profile on the scanner, scans every
   sheet in the feeder and hands the pages back to macOS as JPEG images or one PDF.

Pages are cropped to the size of the sheet and turned the right way up, just like in the app. Why a
bridge and not a native macOS driver is explained in [ADR 0001](https://github.com/elte0/scansnack/blob/main/docs/adr/0001-escl-bridge-instead-of-macos-driver.md).

## Start the bridge

The bridge is a command of the Python package and runs from the source code; the ready-made ScanSnack
downloads do not contain it. You need [uv](https://docs.astral.sh/uv/) and a copy of the repository:

```sh
git clone https://github.com/elte0/scansnack.git
cd scansnack
uv sync
uv run scansnap --ip <scanner-ip> serve
```

Use the scanner's IP address (your router's list of devices shows it).
Broadcast discovery is unreliable, so `--ip` is recommended. Before the first start, quit ScanSnap Home
if it is running.

When the bridge is ready it prints two lines like these and keeps running:

```text
eSCL: http://192.168.1.20:8090/eSCL/ScannerCapabilities (ScanSnap iX1500, Scanner 192.168.1.30)
Bonjour: ScanSnap iX1500 (_uscan._tcp)
```

Leave the terminal open while you scan; <kbd>Ctrl</kbd>+<kbd>C</kbd> stops the bridge and the scanner
disappears from the scan dialog. There is no autostart yet.

On the first scan the bridge registers this computer with the scanner (user and profile, the same as
`scansnap claim`) if it is not registered yet. You do not need to choose anything on the touch panel:
the bridge selects its own profile before every scan.

## Scan from Image Capture or Preview

- **Image Capture** (*Digitale Bilder*): open the app; the scanner appears in the sidebar under its
  model name (or the name given with `--name`). Select it, choose the settings on the right and click
  *Scan*. If you do not see the settings, click *Show Details*.
- **Preview** (*Vorschau*): *File → Import from* the scanner's name. The same scan dialog opens.
- Other apps that offer *Import from Scanner* or use the macOS scan dialog work the same way.

Put the paper in the feeder before you click *Scan*; the scanner pulls in all sheets in one go. JPEG
pages arrive in the app one by one while the scanner is still running; a PDF arrives when the last sheet
is through.

## Supported options

| Setting in the scan dialog | What the bridge does |
|---|---|
| Source | Document feeder only (the iX1500 has no flatbed) |
| Duplex | Supported: both sides of every sheet |
| Kind | *Color* scans in color; *Black & White* scans in grayscale (the JPEG still has three color channels, see [open questions](protocol.md#open-questions)) |
| Resolution | 150, 300 or 600 dpi; other values are rounded to the nearest of these |
| Format | JPEG (one image per page) or PDF (all pages in one file); other formats are created by the macOS app from the JPEGs |
| Size / scan area | Ignored: every page is cropped to the paper. With `--no-crop` you get the whole scan area instead |

## Command-line options

```text
scansnap [--ip IP] [--identity FILE] [--debug] serve [--port PORT] [--name NAME] [--profile-id ID] [--no-bonjour] [--no-crop]
```

| Option | Meaning |
|---|---|
| `--ip IP` | IP address of the scanner (otherwise the bridge searches the network) |
| `--identity FILE` | identity of this computer (default `~/.config/scansnap-bridge/identity.json`, shared with the other `scansnap` commands) |
| `--debug` | print every frame of the scanner protocol as hex |
| `--port PORT` | HTTP port of the bridge (default `8090`) |
| `--name NAME` | name shown in the scan dialog (default: the model name reported by the scanner) |
| `--profile-id ID` | use this scanner profile instead of the bridge's own one |
| `--no-bonjour` | do not announce the scanner; clients must be given the URL by hand |
| `--no-crop` | deliver the whole scan area instead of cropping to the paper |

## Limitations

- **Runs from source only**, in a terminal, and has to be running whenever someone wants to scan. It is
  not included in the app downloads and does not start automatically.
- **One scan at a time.** A second job while the scanner is busy is refused.
- **No app extras:** removing blank pages and removing the background are only available in the
  ScanSnack app.
- **The *Scan* button on the touch panel does nothing** for the bridge; start scans from the Mac.
- **Grayscale only, no 1-bit black and white**, and grayscale images are stored as three-channel JPEGs.
- **Tested on macOS** with Image Capture and the scan dialog (color and black and white, one- and
  two-sided, JPEG and PDF). Other eSCL clients, other computers on the network using the bridge of one
  Mac, and running the bridge and the ScanSnack app at the same time have not been tested.

## Troubleshooting

| Problem | What to try |
|---|---|
| The bridge reports that no scanner was found | Start with `--ip <scanner-ip>`. Broadcast discovery often finds nothing. |
| The scanner does not appear in Image Capture | Check that the bridge is still running and printed the `Bonjour:` line. Mac and scanner must be on the same network. If macOS asks whether to accept incoming network connections, allow them. Quit and reopen Image Capture. Open the `eSCL:` URL in a browser: it should show an XML description of the scanner. |
| The port is already in use | Start with another port, for example `--port 8091`. |
| The scan fails right away or says there is no paper | Put the paper in the feeder and make sure the scanner is awake, then click *Scan* again. |
| A scan is refused while another one runs | The bridge handles one scan at a time; wait until the first one is finished. |
| A paper jam or double feed | The job is cancelled. Clear the paper path, close the cover and scan the remaining sheets again. |
| Scans fail while ScanSnap Home or the ScanSnack app is open | Quit ScanSnap Home; if the app is open, quit it too and try again. |
| Pages are cropped wrongly | Use `--no-crop`. Very dark paper is never cropped. |
| You want to see what happens | Every request and every page is logged with `[escl]` in the terminal. `--debug` also prints the scanner protocol. |

How the scanner protocol behaves in detail, and what is still unknown, is in the
[protocol write-up](protocol.md).
