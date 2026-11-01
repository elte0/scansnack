# Supported models

Which ScanSnap models ScanSnack can talk to. ScanSnack only uses the scanner's network connection, so
models without Wi-Fi cannot be supported. How the supported models speak over Wi-Fi is described in the
[protocol overview](protocol.md).

| Model | Connection | ScanSnack | Notes |
|---|---|---|---|
| ScanSnap iX2500 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX1600 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX1500 | Wi-Fi + USB | Supported | Developed and tested on this model; see the [iX1500 protocol](protocol-ix1500.md). |
| ScanSnap iX1400 | USB | Not supported | USB only; ScanSnack talks to scanners over the network. |
| ScanSnap iX1300 | Wi-Fi + USB | Not supported | Wi-Fi protocol not examined yet; captures welcome. |
| ScanSnap iX500 | Wi-Fi + USB | Supported | Tested on firmware 0U00; needs a one-time Wi-Fi setup with ScanSnap Home, see below. See the [iX500 protocol](protocol-ix500.md). |
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

- **Supported:** implemented and tested on the device.
- **Not supported:** not implemented. For Wi-Fi models this usually means nobody has looked at the
  protocol yet.

Mac editions (for example S1500M) are listed under their base model. The list covers the models known to
us; corrections are welcome.

## iX500

The iX500 speaks an older dialect of the iX1500 protocol and has no touch panel. The app and the command
line (`scansnap ix500`) recognize it on their own. Before ScanSnack can connect:

1. Set up the scanner's Wi-Fi once with ScanSnap Home over USB. Only this setup adds a computer to the
   scanner's list of known hosts; ScanSnack then reuses that registration.
2. ScanSnack sends the scanner's Wi-Fi password, by default the last four digits of the serial number. If
   you changed it, pass it with `scansnap ix500 --password`.

Grayscale is scanned in color and converted on the computer. Details are in the
[iX500 protocol](protocol-ix500.md).

## Help with another model

If you own a model that is not supported and can record its network traffic with ScanSnap Home
(Wireshark), please open an issue. Raw captures can contain scanned documents and device identifiers,
so do not attach them publicly; we will agree on a private way.
