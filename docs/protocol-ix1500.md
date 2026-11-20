# ScanSnap iX1500 Wi-Fi Protocol

> **Summary.** The ScanSnap iX1500 talks to its host software over Wi-Fi using an unencrypted,
> proprietary protocol. It consists of a UDP discovery/heartbeat channel, a TCP control channel that
> carries registration and JSON calls (users, profiles), and a persistent TCP channel that tunnels
> SCSI commands for status polling and scanning. Pages arrive as ready-made JPEGs. A press of the
> "Scan" button on the touch panel is reported to the host by UDP.
> This document describes the frames, the sequences ScanSnap Home uses, the device states, and
> everything that is still unknown.

This is the single source of truth for the iX1500 protocol; the [overview](protocol.md) lists the other
models and what they share. Everything here was observed in network captures
of ScanSnap Home or in live tests against the device. Guesses appear only under
[Open questions](#open-questions) and are marked `# UNVERIFIED:` in the code.

This is an independent interoperability project and is not affiliated with or endorsed by PFU or
Fujitsu. See the [disclaimer](disclaimer.md).

## Contents

- [Sources and conventions](#sources-and-conventions)
- [Overview](#overview)
- [TCP framing](#tcp-framing)
- [Discovery and heartbeat (UDP 52217)](#discovery-and-heartbeat-udp-52217)
- [Control channel (TCP 53219)](#control-channel-tcp-53219)
  - [0x11 Registration](#0x11-registration-352-bytes)
  - [0x41 JSON call](#0x41-json-call)
- [Scan channel (TCP 53218, SCSI tunnel)](#scan-channel-tcp-53218-scsi-tunnel)
  - [Initialising a connection](#initialising-a-new-connection-like-scansnap-home)
  - [C2 hardware status](#c2-hardware-status-0x30-bytes-every-250-ms)
  - [Scan sequence](#scan-sequence)
  - [Double feed or jam: the held batch](#double-feed-or-jam-the-held-batch)
  - [Sense codes](#sense-codes)
- [Scan button on the touch panel](#scan-button-on-the-touch-panel)
- [Sequences](#sequences)
  - [App start / connect](#app-start--connect)
  - [Scan](#scan)
  - [Pairing](#pairing)
  - [Creating our own user and profile](#creating-our-own-user-and-profile-claim)
- [Device states](#device-states)
- [Historical findings](#historical-findings)
- [Open questions](#open-questions)

## Sources and conventions

Code: `src/scansnap/adapters/ix1500/`.

The findings come from the following sources. Raw captures and test logs are **not published**,
because they may contain private data (scanned documents, device and network identifiers). Where a
capture is public, it is an anonymised extract without image data, generated with
`tools/make_fixtures.py` and replayed by the golden tests.

| Source | Content | Public |
|---|---|---|
| Pairing capture | ScanSnap Home pairing with the scanner | anonymised: `tests/fixtures/captures/01_pairing.json` |
| Scan captures | ScanSnap Home scans: gray/simplex, gray/duplex, color/best quality | anonymised, no image data: `tests/fixtures/captures/02_…` to `04_…` |
| Cold-start capture | ScanSnap Home starting up and connecting | no |
| Touch panel captures | two captures of the "Scan" button on the panel | no |
| Live test logs | our own client against the device: keep-alive, scan, `claim` | no |
| User list backup | the scanner's user and profile list before our changes | no |
| Session capture | our own client: double feeds, crumpled sheets, jams (a sheet held back by hand), cover open | no |
| Double-feed capture | ScanSnap Home: Scan pressed on the panel (profile with automatic color mode and resolution), one sheet that double-fed | no |
| Decision captures | ScanSnap Home: double feeds answered with "Continuous scan" and "Quit" in its window, with "Scan" and "Finish" (save the image: no, yes) on the touch panel; a jam continued from the panel | no |

Live tests ran on a home network with ScanSnap Home quit.

Conventions: offsets are byte offsets from the start of the frame or body; integers are big-endian
(BE) unless marked little-endian (LE). Hex values are written `0x..`; status words such as
`fffffffe` are the raw 4 bytes.

## Overview

| Channel | Transport | Purpose | Module |
|---|---|---|---|
| Discovery / heartbeat | UDP 52217 (scanner) | discovery, keep-alive | `discovery.py` |
| Control channel | TCP 53219 | registration, users/profiles (JSON), one connection per command | `control.py` |
| Scan channel | TCP 53218 | SCSI tunnel, persistent connection | `scsi.py`, `scanner.py` |
| Scan button | UDP `host_port` (host; 55265 in our setup) | "Scan" pressed on the touch panel | `button.py` |
| Pairing notifications | UDP 53220 (host) | observed during pairing, **not analysed** | – |

No TLS, no cryptography.

## TCP framing

Every TCP frame starts with `u32 length (BE, including header) | b"ssNR"`.

Client → scanner, 32-byte header:

| Offset | Content |
|---|---|
| 0 | u32 length |
| 4 | `b"ssNR"` |
| 8 | u32 command |
| 12 | u32 0 |
| 16 | 6 B host MAC |
| 22 | u16 0 |
| 24 | 4 B extra field (scan channel: host IP; control channel: usually 0) |
| 28 | u32 0 |

Scanner → client, 16-byte header:

| Offset | Content |
|---|---|
| 0 | u32 length |
| 4 | `b"ssNR"` |
| 8 | u32 0 |
| 12 | u32 status (scan channel: SCSI status, 2 = CHECK CONDITION) |

After the connection is established, the scanner sends a 16-byte greeting frame.

## Discovery and heartbeat (UDP 52217)

Host probe, 32 bytes:

| Offset | Content |
|---|---|
| 0 | 4 B tag (`VENS`, `ssNR`, `V2ss`) |
| 4 | u32 sequence (0; heartbeat: 1) |
| 8 | 4 B host IP |
| 12 | 6 B host MAC |
| 18 | u32 0 |
| 22 | u16 host UDP port (reply target) |
| 24 | u16 flag (`VENS` 0x0010, `ssNR` 0x0100, `V2ss` 0x1000) |
| 26 | 6 B 0 |

Scanner reply, 144 bytes:

| Offset | Content |
|---|---|
| 0 | `b"ssNR"` |
| 16 | 4 B scanner IP |
| 20 | u32 scan channel port (53218) |
| 24 | u32 control channel port (53219) |
| 28 | 6 B scanner MAC |
| 40 | 64 B device name, e.g. `iX1500-TEST000000` |
| 104 | 16 B model, e.g. `ScanSnap iX1500 ` |
| 124 | 8 B ID of the currently paired host |
| 132 | 12 B serial number |

Observed:

- On start-up, ScanSnap Home sends all three probes from port 55264 (port field 55264), three times,
  0.3 s apart.
- While a host holds a session (ScanSnack, ScanSnap Home), the scanner answers no probe, neither broadcast
  nor unicast. After the session is closed it stays silent for a while: no answer after 1 s and 5 s; the
  unicast probe was answered again after 15 s in one test and after 88 s in another. This explains why
  broadcast discovery seemed unreliable; pass the scanner's address with `--ip` in that case. The greeting
  on TCP 53219 still arrives, so the app identifies a connected scanner by its address.
- Unicast probes: only `ssNR`/0x0100/seq 0 and `ssNR`/0x1000/seq 1 are answered.
- ScanSnap Home heartbeat: `ssNR`, seq 1, flag 0x1000 every ~0.5 s, starting only ~5 s after the app
  starts. The scanner answers with 12 bytes `ssNR 8001 0000 0000…` (often several times), but unreliably.

## Control channel (TCP 53219)

One TCP connection per command: read the greeting, send the frame, read the reply.

| Command | Meaning | Body / reply |
|---|---|---|
| 0x11 | register host | 352-byte binary block (see below); reply status at offset 12 |
| 0x12 | meaning unknown | no body, extra field = 1; does not always answer |
| 0x13 | device name | reply from offset 16, 64 B, null-terminated |
| 0x30 | state | reply u32 at offset 16 (meaning unknown) |
| 0x41 | JSON call | see below |

### 0x11 Registration (352 bytes)

| Offset | Content |
|---|---|
| 0 | `01 00 1e 00` |
| 4 | u32 flag: 5 = pair, 0 = connect |
| 8 | u32 0 |
| 12 | 4 B host IP |
| 16 | u16 0 |
| 18 | u16 `host_port` |
| 68 | u16 year, u8 month, day, hour, minute, second, u8 0 (local time) |
| 76 | 8 B host ID |
| 84 | i32 time zone bias in seconds (−UTC offset) |

Byte-identical to the pairing capture (test `test_register_body_matches_capture`).

### 0x41 JSON call

Body:

| Offset | Content |
|---|---|
| 0 | u32 BE length of the rest of the body |
| 4 | u16 BE subcommand |
| 6 | 10 B 0 |
| 16 | u32 LE JSON length |
| 20 | JSON (ScanSnap Home: `indent=2`, separator `" : "`) |

Reply = server header + the same layout from offset 16. Without JSON it is an acknowledgement:
u32 status at offset 24 (0 = ok).

| Sub | Meaning |
|---|---|
| 0x0100 | read users (`conn_user.users`, `conn_user.select`) and profiles |
| 0x0110 | write users/profiles |
| 0x0210 | announce host (`host_address`, `function_level` 2, `current_user_id`, `host_port`, `version` 1) |
| 0x0610 | select profile (`{"version": 1, "prof_id": …}`) |
| 0x0810 | scan settings for the touch panel |

Error frames: `ssNR fffffffe` instead of a reply (e.g. 0x0610 in the wrong state).

Acknowledgement `0xffffffff` for 0x0110, observed with:

- a user name longer than 30 characters (30 is fine, 31 is not),
- an attempt to change `select`,
- a reordered user list,
- the user name `"scansnap-bridge (Mac.fritz.box)"`.

Other statuses of 0x11 flag 0: `fffffffc` (scanner still in pairing state),
`fffffff9` (host is not selected on the touch panel).

## Scan channel (TCP 53218, SCSI tunnel)

Request: command = 1, extra field = host IP. Body:

| Offset | Content |
|---|---|
| 0 | u32 CDB length (6/10/12) |
| 4 | u32 expected data length from the scanner |
| 8 | u32 length of data to the scanner – **required**: without it, the scanner rejects E9/D4 with sense 5/26/00 |
| 12 | 4 B 0 |
| 16 | 16 B CDB (padded with zeros) |
| 32 | data to the scanner (optional) |

Response: server header (status in bytes 12–15), 24 B of zeros, data from byte 40.
Status 2 = CHECK CONDITION → fetch `03` REQUEST SENSE (18 B).

### Initialising a new connection (like ScanSnap Home)

`12` INQUIRY (0x60 B), `12 01 f0` INQUIRY VPD f0 (0x90 B), `E7` (0x0C B), `C2`.

### C2 hardware status (0x30 bytes), every 250 ms

ScanSnap Home keeps 53218 open permanently and polls C2 every 250 ms. During a scan the polling mostly
pauses (in 6 of the batches in the decision captures one more C2 came between D5 and D8). ScanSnap Home
sends C2 once after D4 and once after each sheet's image info (double-feed and decision captures). While a
batch is held after a paper fault the normal 250 ms polling resumes, 0.8–1.1 s after D8 (7 holds); the
touch panel's answer arrives only there (see [Held batch](#double-feed-or-jam-the-held-batch)). Bit
names in parentheses are those of SANE's fujitsu backend (`backend/fujitsu-scsi.h`, `get_GHS_*`).

| Data offset | Content |
|---|---|
| 2, bit 7 | (`top`) set after all three captured jams until the cover was opened; set in 1 of 6 ScanSnap Home double-feed holds (until the sheet after the continue) and for about 55 polls after one of our client's double feeds |
| 3, bit 7 | (`hopper`) feeder empty |
| 3, bit 5 | (`adf_open`) cover open (session capture) |
| 4, bit 0 | (`scan_sw`) scan button pressed (~0.3 s pulse); while a batch is held: "Scan" (continue) on the panel |
| 4, bit 7 | (`sleep`) set in two captures while the scanner was asleep |
| 6, bit 0 | (`double_feed`) double feed; stays set while the batch is held, clear after the next D4 |
| 7 | (`error_code`) 0x55 with a double feed, otherwise 0 in all captures |
| 14, bit 3 | while a batch is held: "Finish" on the panel, then "do not save the image" (decision captures) |
| 14, bit 6 | while a batch is held: "Finish" on the panel, then "save the image" (decision captures) |
| 16 | state byte: 0x80 before 0x11, 0xe0 after it; 0xe1 after 0x11 flag 5 (pairing) |
| 32–39 | currently selected host (host ID) |

### Scan sequence

```
D5 00 00 01 08 08   open session        (8 B out, 8 B in)
D8                  start
E9                  set window          (32 B: dpi x/y, width/height in 1/1200 inch, 0x05)
D4                  set mode            (80 B: color mode, dpi, quality)
C2                  hardware status     (ScanSnap Home; our client only with the fix below)
03                  REQUEST SENSE
per sheet:
  E0                feed sheet          → CHECK CONDITION + sense 3/80/03 = feeder empty
  28 00 00 02 …     JPEG chunks, front/back alternating
                    CHECK CONDITION + sense EOM (info = residual length) = page done
  28 00 80 …        image info (0x20 B: width, max height, width, height, dpi x, dpi y)
  C2                hardware status: double feed? (see "Held batch")
D6                  release session     (also after errors)
```

- Read CDB: `28 00 00 02 00 <80 for back side> <3-byte length> 00 <side> <chunk>`; the first 2 data
  bytes of every chunk are the side and the chunk number.
- Images arrive as complete JPEGs (YCbCr, 3 channels even in gray mode; JFIF dpi = 0).
- The scanner always reads both sides; simplex = discard the back.
- Pages arrive rotated by 180° and cover the whole scan area (width 2608 px at 300 dpi, length with
  ~50/110 px of background). Background ~235–240, paper ~252, shadow edge ~225 (live scans).
- Scan area as sent by ScanSnap Home: width 0x28D0, height 0x44DC (1/1200 inch).
- A sheet that jams while it is read (session capture; 2 jams, both in the back side's read): the front's
  read ends normally; the back's read ends with CHECK CONDITION and 34 bytes that are not an image chunk
  (`00 01`, then what looks like an image info block), and REQUEST SENSE returns 3/80/01. A jam sense that
  is not read stays pending: once the next batch's first REQUEST SENSE returned it.

The E9/D4 parameter blocks for gray/150 and color/600 are byte-identical to the captures (read length
0x300000 and 0x20000 respectively). For profile …0010 (color/300), ScanSnap Home sends a D4 that
differs from our template in byte 10 (0x90), 13 (0x00) and 40 (0x0d); our color/300 block is accepted
anyway. Other combinations are derived (see [Open questions](#open-questions)).

### Double feed or jam: the held batch

**Double feed**: the double-fed sheet's reads end with the normal EOM sense, and C2 after the sheet's image
info shows the fault: byte 6 bit 0 set, error code 0x55 in byte 7. Crumpled single sheets set the same flag
and code (session capture). **Jam**: see "a sheet that jams while it is read" above.

ScanSnap Home does not end the batch. It holds it until the user answers (double-feed and decision
captures; 6 double feeds, 1 jam):

```
double feed:  EE 00 00 00 08 00 (out 00 00 00 55 00 00 00 00), D8     (6 of 6)
jam:          D8, no EE                                              (1 of 1)
held:         C2 every 250 ms (and 0x0210 every 10 s) until the user answers
continue:     D8, E9, D4, C2 (flag clear), 03, E0 …   – no new D5
end:          D6
```

The user answers either in ScanSnap Home's window ("Continuous scan" / "Quit"; no traffic, the host just
continues or ends) or on the touch panel, which ScanSnap Home sees only in C2: "Scan" sets byte 4 bit 0,
"Finish" followed by "do not save the image" byte 14 bit 3 (once), followed by "save the image" byte 14
bit 6 (once). Each time ScanSnap Home sent D8 or D6 right after the answer, so whether the scanner clears
these bits by itself is not known. No UDP notification is sent for these. After the jam the panel offered
"Scan" and "Finish" as well; the user put the jammed sheet back before "Scan", and the next E0 fed it
again. After "continue" the next E0 reports no sense for the double feed. ScanSnap Home's window shows the
double-fed sheet's two sides with an option to keep the image; the images have already been read when the
fault is known. A jam reported by E0 has not been captured with ScanSnap Home.

The double-feed flag stays set while the batch is held and after it has ended, until the next batch's D4
(session capture). Once, with the flag of an earlier batch still set, ScanSnap Home sent EE with
`80 00 ff ff 00 00 00 00` before starting a new batch; the second time the same state came up it sent no EE
(decision captures).

Our client does the same with the opt-in fix `ix1500-hold-paper-faults` (`core.fixes`): it reads C2 after
D4 and after each sheet's image info, holds the batch after a double feed or a jam in a read, takes the
panel's answer from C2 (only after a poll without one) or an answer from the app (`decide`), delivers a
double-fed sheet with `fault="double feed"` only after "Finish" and "save the image", and continues or ends
the batch with FeedError. Without an answer it ends the batch after 5 minutes. A jam that E0 reports ends
the batch at once. Without the fix the scan sequence is unchanged and double feeds are not detected.

### Sense codes

| Sense | Meaning | Source |
|---|---|---|
| EOM bit, info = residual length | end of page | capture |
| 3/80/01 | paper jam / cannot feed | live (crumpled sheet, simulated jam); session capture (sheet held back, on a read) |
| 3/80/02 | cover open | session capture (E0 with the cover open) |
| 3/80/03 | feeder empty | capture, live |
| 3/80/33 | D5 without a preceding 0x0610 ("Wi-Fi control error") | live |
| 5/26/00 | E9/D4 without out length in the SCSI header | live |

For the other 3/80/xx texts in `scsi.MEDIUM_ERRORS` see [Open questions](#open-questions).

## Scan button on the touch panel

Pressing "Scan" → 48-byte UDP datagram to the `host_port` of the selected host (55265 in our setup),
three times, 0.5 s apart (private touch panel captures):

| Offset | Content |
|---|---|
| 0 | u32 length (0x30) |
| 4 | `b"ssNR"` |
| 8 | u32 1 |
| 12 | u32 0 |
| 16 | u8 event counter (+1 per press; repetitions carry the same value) |
| 20 | 4 B scanner IP |
| 24 | 6 B scanner MAC |

No reply is needed: the panel shows "waiting for a response from the device" (observed on a
German-language panel: „Es wird auf eine Antwort vom Gerät gewartet“) until the host runs the
normal 0x0100 → 0x0610 → D5 … sequence. Without a running session (scan channel + C2 polling) nothing
arrives. Confirmed live with `scansnap listen` (2 presses, 1 sheet each).

## Sequences

### App start / connect

From the private cold-start capture:

1. UDP probes (see above).
2. 53218 connection A: INQUIRY, VPD f0, E7, then C2 polling.
3. 53219: 0x13 → 0x0100 → 0x0210 → 0x11 with **flag 0**. No 0x30/0x12.
4. Close connection A, open a **new connection B** on 53218 (C2 polling only, no INQUIRY).
5. UDP heartbeat from ~5 s; 0x0210 repeats roughly every 10 s.

Confirmed live (private keep-alive log): session held for 12 s, then two scans on the same connection;
2.6 s until "feeder empty" instead of 4.9 s with a reconnect.

### Scan

0x0100 (get_users) → 0x0610 (select profile) → D5 … on the scan channel, ~100–150 ms apart.
Stop C2 polling before D5 and resume it after D6.

- D5 without a preceding 0x0610 → sense 3/80/33.
- Live scan works (private scan log): valid JPEG 2608×3688 RGB, color/300.
  What made the difference was the connect sequence above and the out length in the SCSI header.

### Pairing

From the pairing capture (`tests/fixtures/captures/01_pairing.json`):

0x13 → 0x0100 → 0x30 → 0x12 → 0x11 (flag 5) → 0x0210.

### Creating our own user and profile (`claim`)

Confirmed in private live test logs of `claim` and a subsequent scan:

1. Session with the pairing sequence (0x11 flag 5, C2 status 0xe1).
2. 0x0110 with the complete user list (our entry in its place, otherwise `0xffffffff`),
   `select` unchanged, and all profiles plus our own profile (template
   `src/scansnap/adapters/ix1500/profile_template.json`).
3. 0x0210.
4. Finish pairing: 0x30 → 0x12 → 0x11 flag 0. Without this step C2 stays at 0xe1,
   0x11/0 → `fffffffc`, 0x0610 → `fffffffe`.

If step 4 is skipped after an error in steps 2–3, the scanner stays in the pairing state and the touch
panel shows "Processing" until it is switched off (user report, [issue #19](https://github.com/elte0/scansnack/issues/19)).
With the opt-in fix *#19* (app menu *Fixes*) our client runs step 4 after every error.

Then select the user (`scansnap-bridge`) on the touch panel – `select` (= C2 bytes 32–39) can only be
changed on the panel. From then on, scans run under our own identity.

## Device states

| State | Recognisable by | Allowed | Rejected |
|---|---|---|---|
| not registered | C2 byte 16 = 0x80 | discovery, 0x13, 0x0100, 0x30, 0x0210 | – |
| connected (0x11 flag 0) | C2 byte 16 = 0xe0 | 0x0610, scan | 0x0110 (`fffffffe`) |
| pairing (0x11 flag 5) | C2 byte 16 = 0xe1 | 0x0110 | 0x0610 (`fffffffe`), 0x11/0 (`fffffffc`) |
| not selected | C2 bytes 32–39 ≠ own host ID | – | 0x11 flag 0 (`fffffff9`) |

After the user is switched on the panel, 0x0100 returns only the profiles of the selected user
(initially empty).

## Historical findings

- 0x0610 → `fffffffe` even with the exact ScanSnap Home identity and a byte-identical frame ⇒ the
  cause was the state (connect sequence), not the identity.
- Only the scan channel before 0x0610, or only the heartbeat → still `fffffffe`.

## Open questions

Each item corresponds to an `# UNVERIFIED:` marker in the code (location in parentheses).

1. **Parameter blocks other than gray/150 and color/600** (`scanner.mode_block`, `scanner.read_len`):
   derived; read length 0x20000 used for everything without a capture. Accepted live: color/300, gray/150.
2. **Sense texts 3/80/xx** (`scsi.MEDIUM_ERRORS`): written from memory after SANE's `fujitsu.c`
   backend, not looked up. Only 01, 02, 03 and 33 are confirmed.
3. **0x12 and 0x30** (`control.cmd12`, `control.state`): meaning unknown, only reproduced in the
   order ScanSnap Home uses.
4. **E7 and INQUIRY VPD f0** (`scsi.open_session`): content not analysed.
5. **Delays in the connect sequence** (`device.Session.open`: 0.2 s before channel B, 1.0 s after):
   not tested individually whether they are needed.
6. **Is the heartbeat needed?** (`device.Session.open`): replies are unreliable; whether the scanner
   ends the session without a heartbeat has not been tested.
7. **Which of the two fixes was needed** – the connect sequence alone or also the out length?
   Not tested separately (both are implemented).
8. **Profiles disappear** (`device.Device.ensure_profile`): occasionally even without a user switch,
   cause unclear. Hypothesis (user): profiles are stored per user, so the ScanSnap Home profiles still
   exist. Unverified; a private backup of the user list exists.
9. **How long does the panel wait** for the host after "Scan"? (`button.py`)
10. **UDP 53220**: notifications from the scanner during pairing, content not analysed. On the iX500
    the scanner sends a 48-byte power-on notice to this port ([iX500 protocol](protocol-ix500.md#notifications-udp-48-b));
    whether the iX1500's datagrams have the same layout is not checked.
11. **Grayscale** (`escl`): with the gray D4 the image looks gray, but the JPEG has 3 channels.
    Whether the scanner can deliver true 1-channel JPEGs is open.
12. **Feeder capacity of 50 sheets** in the eSCL capabilities (`escl.server.capabilities_xml`):
    origin not documented, not read from the device.
13. **0x0100 reply without `conn_user`** (`control.Control.claim`): a user reported the error
    "conn_user" during `claim` on an iX1500 ([issue #19](https://github.com/elte0/scansnack/issues/19)),
    i.e. the reply lacked `conn_user` (or its `select`). Which reply the scanner sent, and in which state,
    is unknown; the captures always contain it.
14. **C2 error codes other than 0x55** (`scanner.double_feed`): none seen; they are not acted on.
15. **EE data** (`scanner.Scanner.scan`): `00 00 00 55 00 00 00 00` after each double feed; once
    `80 00 ff ff 00 00 00 00` before a new batch while an earlier double-feed flag was still set. The layout
    is not known.
16. **Which command clears the double-feed flag** (`tests/mock_scanner.py`): it stays set while the batch is
    held and after D6, and is clear in C2 after the next D4; D8, E9 and D4 were not told apart.
17. **The panel's answers** (`scanner.panel_decision`): byte 14 bits 3 and 6 were each seen once, after a
    double feed; "Finish" after a jam was not tried. Byte 21 bit 0 changed with 1 of 2 "Scan" answers and
    with "save the image"; its meaning is not known.
18. **A jam reported by E0** (`scanner.Scanner.scan`): every captured jam ended a read; whether ScanSnap
    Home holds the batch after E0's 3/80/01 too is not known, so our client ends it. Live with our client:
    a sheet held back before it was picked up gave 3/80/01 on E0, and the panel showed nothing after D6; a
    sheet held back once it was moving was read normally, the panel showed a jam, and the next E0 reported
    only 3/80/03 (feeder empty), so the host was not told about that jam.
19. **The double-fed image after "Scan" on the panel** (`scanner.panel_decision`): whether ScanSnap Home
    keeps it is not visible in the traffic; our client drops it.
20. **How long a batch can be held** (`scanner.Scanner._hold`): the longest captured hold lasted about 33 s;
    the 5-minute timeout is our choice, and what the scanner does during a longer hold is not known.
