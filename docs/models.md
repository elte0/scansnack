# Supported models

Which ScanSnap models ScanSnack can talk to. ScanSnack only uses the scanner's network connection, so
models without Wi-Fi cannot be supported. The desktop app lists the same models when you connect a
scanner and greys out the ones that are not supported yet.

| Model | Connection | ScanSnack | Notes |
|---|---|---|---|
| ScanSnap iX2500 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX1600 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX1500 | Wi-Fi + USB | Supported | Developed and tested on this model. |
| ScanSnap iX1400 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap iX1300 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX500 | Wi-Fi + USB | Beta | Based on a third-party protocol write-up, not yet tested by us. Needs the pairing key. |
| ScanSnap iX100 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap SV600 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap N1800 | Ethernet | Not supported | Network protocol not examined yet. |
| ScanSnap S1500 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S1300i | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S1300 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S1100i | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S1100 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S510 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S500 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap S300 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap fi-5110EOX | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap fi-4110EOX | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |

- **Supported:** developed and tested on the device.
- **Beta:** implemented from protocol knowledge, but not yet tested on a device by us. Reports are
  welcome, see below.
- **Not supported:** not implemented. For Wi-Fi models this usually means nobody has looked at the
  protocol yet.

Mac editions (for example S1500M) are listed under their base model. The list covers the models known to
us; corrections are welcome.

## iX500 (beta)

The iX500 speaks the same protocol family as the iX1500, but registers with a secret pairing key and has
no touch panel. ScanSnack's support is based on a third-party write-up (see "Sources and conventions"
in the [protocol description](protocol.md)) and has not been tested on a device by us. Before the first
scan you need the scanner's pairing key:

1. Switch the iX500 off.
2. In the app, choose *ScanSnap iX500 (beta)* when connecting and click *Get Key …*, enter the device
   name `iX500-<serial>` (the serial is on the label on the bottom of the scanner) and click *Start*.
   On the command line, run `scansnap capture-key --name iX500-<serial>` instead. ScanSnack then poses
   as the scanner and needs the ports 52217, 53218 and 53219 of this computer.
3. Start ScanSnap Home on another computer in the same network. It connects to the stand-in and sends
   the key, which ScanSnack fills in (app) or prints and stores (command line).
4. Switch the iX500 on again and connect; on the command line, use `scansnap --model ix500 scan`.

Not available on the iX500: starting a scan with the button on the device (in the app and with
`scansnap listen`). Grayscale is scanned in color and converted on the computer.

## Help with another model

If you own a model that is not supported and can record its network traffic with ScanSnap Home
(Wireshark), please open an issue. Raw captures can contain scanned documents and device identifiers,
so do not attach them publicly; we will agree on a private way.

This list is kept in sync with the app by a test (`src/scansnap/core/catalog.py`).
