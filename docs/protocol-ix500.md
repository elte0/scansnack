# ScanSnap iX500 Wi-Fi Protocol

> **Summary.** The iX500 speaks an older dialect of the iX1500 protocol ([protocol.md](protocol.md)):
> the same ports and TCP framing with the magic `VENS` instead of `ssNR`, a short registration that
> carries an encoded password, no users or profiles, and UDP notifications for the Scan button, power
> off and power on. Pages arrive as color JPEGs; gray and other conversions happen on the host.

Code: `src/scansnap/adapters/ix500/`. Sources: network captures of ScanSnap Home 4.1.0 with an iX500
(firmware 0U00), live tests with this client, and static analysis of ScanSnap Home's own iX500 Wi-Fi code
for the parameter blocks. Captures are private; values below use example addresses. Guesses are marked
`UNVERIFIED` here and `# UNVERIFIED:` in the code.

This is an independent interoperability project, not affiliated with PFU or Fujitsu. See the
[disclaimer](disclaimer.md).

## Overview

| Channel | Transport | Purpose | Module |
|---|---|---|---|
| Discovery / heartbeat | UDP 52217 (scanner) | discovery, keep-alive | `discovery.py` |
| Control | TCP 53219, one connection per command | registration, name, status | `control.py` |
| Scan | TCP 53218 | SCSI tunnel | `scsi.py`, `scanner.py`, `info.py` |
| Button, power off | UDP 55265 (registered host) | notifications | `notify.py` |
| Power on | UDP 53220 (broadcast and registered host) | notification | `notify.py` |

No TLS. Integers are big-endian unless noted.

## TCP framing

As on the iX1500 with `VENS`: client header 32 B (`u32 length | VENS | u32 command | u32 0 | 6 B host MAC
| u16 0 | u32 0 | u32 0`), server header 16 B (`u32 length | VENS | u32 0 | u32 status`), a 16 B greeting on
connect. The extra field that carries the host IP on the iX1500 scan channel is 0 on the iX500.

**One client at a time:** a second connection to 53218 is accepted but gets no greeting until the first
one closes (observed live with the client's C2 poller connected). The client pauses its poller before a
scan or registration.

**The header MAC is the session identity.** The scan channel only answers frames whose MAC is the one that
sent the last registration; other MACs get the connection closed. The client therefore sends the real MAC
of the interface that reaches the scanner, as ScanSnap Home does.

## Discovery and heartbeat (UDP 52217)

The probe is the iX1500 layout with tag `VENS`, seq 0, flag 0x0010. `ssNR` and `V2ss` probes are not
answered. Reply, 132 B:

| Offset | Content |
|---|---|
| 0 | `VENS` |
| 8 | u16 interface version (6), u16 device type (0x30) |
| 12 | `ffffffff` |
| 16 | scanner IP |
| 20, 24 | u32 scan port 53218, u32 control port 53219 |
| 28 | scanner MAC |
| 36 | u32, 4 while a host is registered, 1 right after power on (UNVERIFIED: state) |
| 40 | name, 64 B, e.g. `iX500-A0XX012345` (model and serial) |
| 104 | model, 16 B, `ScanSnap iX500  ` |
| 120 | IP of the registered host |
| 124 | 8 B host ID of the registered host |

The client ignores replies whose embedded IP differs from the sender.

Heartbeat: `VENS`, seq 1, flag 0x1000 from UDP 55264, three probes about 0.5 s apart every ~30 s.
The scanner answers each with 12 B `VENS 8001 0000 0000`.

## Registration (control 0x11)

ScanSnap Home sends it at start-up and after every power-on notice. Frame 0x80 B, body 96 B:

| Offset | Content |
|---|---|
| 0 | u16 interface version (from discovery), u8 0x1e (scanner timeout), u8 0 |
| 4 | u32 reserve mode, 0 |
| 8 | u32 1 if a password follows |
| 12 | host IP |
| 16 | u32 notification port 55265 |
| 20 | 48 B encoded password, ASCII digits |
| 68 | u16 year, u8 month, day, hour, minute, second, 0 (local time) |
| 76 | 8 B host ID |
| 84 | i32 time zone bias in seconds (−UTC offset), only for interface version > 3 |

Reply 20 B: `u32 length | VENS | i32 status`. Statuses seen live:

| Status | When |
|---|---|
| 0 | accepted |
| −2 | registration without the password |
| −7 | host ID that is not in the scanner's host table |

UNVERIFIED: the other negative statuses (ScanSnap Home knows −1 to −7) and the status for a wrong password.
The scanner also accepts the iX1500's 352 B body (header `01 00 1e 00`) once the password field is set;
the client sends the 96 B body ScanSnap Home sends.

Password encoding: for each character `c` at position `i`, `"%03d" % (ord(K[i]) + ord(c) + 11)` with
`K = "pFusCANsNapFiPfu"` (at most 16 characters). The factory password is the last four digits of the
serial number, which the scanner announces in its discovery name.

The registration does not write the scanner's host table; on the iX500 that table is only written over
USB by ScanSnap Home's Wi-Fi setup, and a host ID missing from it is rejected (−7). The client therefore
reuses the host ID the scanner reports as registered (discovery offset 124) on its first run.

Other control commands: 0x13 returns the name (64 B), 0x30 a u32 status (3 observed on AC power).

## Notifications (UDP, 48 B)

`u32 0x30 | VENS | u32 event | u32 0 | u8 counter at 16`, each sent three times about 0.5 s apart.

| Event | Port | Trigger | Extra |
|---|---|---|---|
| 0x01 | 55265, registered host | Scan button | |
| 0x10 | 55265, registered host | lid closed | |
| 0x21 | 53220, broadcast and registered host | lid opened (~10 s later) | scanner IP at 20, MAC at 24 |

The counter restarts at 1 after power on. ScanSnap Home answers a power-on notice with discovery and a
new registration within a second. Port 53220 is configurable on the scanner ("Port number for startup
notice" in ScanSnap Home).

## Scan sequence (TCP 53218)

SCSI framing as on the iX1500, without the host IP in the extra field:

```
C2                  hardware status (0x20 B)
D5 00 00 00 08 08   open session (8 B out, 8 B in)
D8                  start
E9                  window (32 B)
D4                  scan parameters (80 B)
C2, 03
per sheet:
  E0                feed; CHECK CONDITION with sense 3/80/03 = feeder empty, 3/80/01 = jam
  28 00 00 02 00 00 30 00 00 00 <n>   00   front JPEG, CHECK CONDITION + EOM
  03
  28 00 00 02 00 80 30 00 00 00 <n+1> 00   back JPEG
  03
  28 00 80 00 00 80 00 00 20 00 <n+1> 00   image info (width, max height, width, height, dpi x, dpi y)
  C2
D6                  release, on a new connection
```

`n` counts images (0, 2, 4 …). Each read returns the image index and a chunk number, then a whole JPEG
(one read per side at 300 dpi color). The iX500 always reads both sides; simplex drops the back. Pages
arrive rotated by 180° and cover the whole scan area.

## Scan parameters

`D4` data = 32 B parameters + 48 B window descriptor. The color/300/auto block is byte-identical to the
captures; the rest follows ScanSnap Home's iX500 code.

| D4 byte | Value |
|---|---|
| 1 | 3 |
| 2, 3 | 1 for automatic page size (and long page), else 0 |
| 4 | multifeed detection: 0 off, 0xd0 ultrasonic |
| 6 | 0xc1 |
| 7 to 9, 11, 12 | 0x80 |
| 10 | 0x90 at 300 dpi, else 0x80 |
| 13 | 0x80 at 600 dpi, else 0 |
| 31 | 0x30 |
| 32 | `00 10` |
| 34, 36 | u16 dpi x, y: 150, 200, 300 or 600 |
| 38, 39 | 5, 0x81 |
| 40 | JPEG quality: 5 at 150/200, 0 at 300, 0x0a at 600 |
| 42, 46 | u32 width, u32 height in 1/1200 inch |
| 50 | 4 |

`E9` data: `u16 dpi x | u16 dpi y | u32 width | u32 height | 05`, padded to 32 B.

| Page | Width | Height |
|---|---|---|
| auto | 0x28d0 | 0x44dc |
| A4 | 0x26c1 | 0x36d0 |
| Letter | 0x27d8 | 0x3390 |
| Legal | 0x27d8 | 0x41a0 |
| B5 | 0x2196 | 0x2f6e |
| A5 | 0x1b50 | 0x26c1 |
| B6 | 0x179f | 0x2196 |

Color mode is not a scanner parameter: the iX500 returns color JPEGs and ScanSnap Home converts to gray
or black and white on the host, as it also crops, deskews and drops blank pages.

## Device info (read-only)

| Query | CDB | Reply |
|---|---|---|
| Counters | `d9 00 00 00 20 00` | u32 total pages, then two u32 (UNVERIFIED: pick and brake roller) |
| Firmware modules | `e7 00 00 00 00 00 00 50 00 00` | firmware (`0U00`), module versions |
| Serial | `e7 00 01 00 00 00 00 0c 00 00` | u32 number, then the letters around zero padding |
| Auto power-off | `e6 00 01 00 00 00 00 04` | 4 B; `10 1e 00 00` observed. UNVERIFIED: byte 1 in minutes |
| Battery save | `eb 00 01 00 00 00 00 04` | 0 (no battery) |

Not supported on the iX500 (sense 5/20/00): `dd` (read time) and the `dc` host-ID commands of newer models.

## Open questions

1. Negative 0x11 statuses other than −2 and −7, and the control 0x30 status.
2. Images larger than one read (0x300000 B): the client repeats the read, never observed.
3. Labels of the second and third counters.
4. Sense texts other than 3/80/01 and 3/80/03.
5. The E6 power-off block: whether byte 1 is minutes and what byte 0 (0x10) means.
6. The one-byte image index after 128 sheets in one job (the client wraps it at 256).
