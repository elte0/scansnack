# ScanSnap iX1300 Wi-Fi Protocol

A capture of ScanSnap Home receiving one Return Scan over Wi-Fi shows the iX500's `VENS` framing and scan notification, a 48-byte hardware status response, and two JPEGs per sheet. The host was already configured for the scanner.

This is an independent interoperability write-up. See the [disclaimer](disclaimer.md) and the [protocol overview](protocol.md).

## Sources

Two private packet captures of ScanSnap Home on macOS. The first covers idle polling and one successful front-slot scan. Replaying its image reads through ScanSnack's iX500 reader at commit `eb80600` decoded both JPEGs. The second covers reconnecting a configured host and a scan that ended in a paper jam. The captures may contain private network and image data.

## Discovery and registration

On wake, the scanner broadcast 48-byte `VENS` notifications with event `0x21` to UDP port 53220. ScanSnap Home sent the three discovery probe variants documented in the [iX1500 write-up](protocol-ix1500.md#discovery-and-heartbeat-udp-52217). It received a 144-byte `VENS` discovery reply with interface version 16. The iX500 discovery parser decoded its shared fields. The serial at offsets 132–143 matched the suffix of the device name.

The control channel on TCP 53219 saw `0x13 → 0x11 → 0x62 → 0x30`. The scanner accepted the `0x11` registration with status 0. The request used the same host ID as the discovery reply, notification port 55265 and a zero password flag and field. Rebuilding it with the [iX500's 96-byte registration layout](protocol-ix500.md#registration-control-0x11) matched every byte of the captured body. The 20-byte reply began with `00000014 56454e53 00000000 00100000`.

The `0x62` request had a 16-byte body and received a 28-byte reply. Its purpose and whether a client needs it are open.

A Linux host on the same LAN discovered the scanner at interface version 16. Discovery returned a populated host ID and `0.0.0.0` in the notification target field. The Linux host sent `0x11` with its own IP and interface MAC, a distinct host ID, and the zero password flag. The scanner returned status `-7`. The same session command with the already enrolled ID had previously succeeded from Linux using its own IP and MAC.

## Notifications and polling

| Direction | Transport | Observation |
|---|---|---|
| Host → scanner | UDP 52217 | 32-byte `VENS` heartbeat with sequence 1 |
| Scanner → host | UDP 55264 | 12-byte heartbeat reply starting with `VENS` |
| Scanner → host | UDP 55265 | 48-byte `VENS` notification with event `0x01` and a one-byte counter at offset 16 |
| Host ↔ scanner | TCP 53218 | SCSI tunnel with `VENS` framing and a 40-byte response header before command data |

The scan notification arrived three times, about 0.5 seconds apart, with the same counter. ScanSnack's iX500 notification parser decoded all three. See the [iX500 notification layout](protocol-ix500.md#notifications-udp-48-b).

During the observed idle period, ScanSnap Home opened short TCP connections roughly 310 ms apart and requested 48 bytes of hardware status with `C2`. The first notification preceded opening the scan session by about 320 ms.

## Return Scan

The host checked `C2` after the notification. On the next connection it sent `C2 → D5 → C2`. The `D5` CDB was `d50000000808` with eight zero bytes out and eight bytes in. The image transfer used another connection:

```text
D8 → E9 → D4 → C2 → REQUEST SENSE → E0
READ (front) → REQUEST SENSE → READ (back) → REQUEST SENSE
READ (image metadata) → C2 → E0 → REQUEST SENSE
```

Each image read requested `0x300000` bytes and returned a two-byte side/chunk header followed by one complete 2592 × 3472 JPEG. The scanner returned SCSI CHECK CONDITION on both reads. REQUEST SENSE indicated end of image and the residual length matched the requested length minus the bytes delivered, including the header. After both sides, the next `E0` returned CHECK CONDITION with sense `3/80/03` (feeder empty).

With ScanSnap Home closed, the Linux receiver registered after the scanner cover was opened. It received two consecutive front-slot sheets in the same process and produced one readable two-page PDF per sheet. The receiver re-registered after a restart while the scanner was reachable.

Both sides of a test sheet arrived upside down without EXIF orientation. The receiver sets a 180° display rotation on both PDF pages so the JPEG streams can be embedded unchanged.

### Captured 300 dpi settings

These are the command data blocks from the observed scan. The scan window in the `E9` block is `0x2880 × 0x4410` in 1/1200 inch.

```text
E9 (32 bytes):
012c012c00002880000044100500000000000000000000000000000000000000

D4 (80 bytes):
000601010000c180808090808000000000000000000000000000000030000030
0010012c012c05820d0000002880000044100400000000000000000000000100
00000000000000000000000000000000
```

## Feeder status

With the lid open, byte 3 of the 48-byte C2 reply was 0x80 with both slots empty, 0x00 with paper in the top feeder and 0x81 with paper in the front slot. The front-slot value persisted after notification and through scan setup. Simultaneous loading has not been characterized.

## Top feeder

A private capture of a successful 300 dpi top-feeder scan used the same feed command as Return Scan. The E9 and D4 window height was 0x4350. D4 bytes 1 and 28 (zero-based) were 0x03 and 0x00. A nine-sheet duplex transfer with these settings returned a consecutively numbered section in descending order. Reversing the sequence of sides restored ascending page order.

## Paper jam

The jammed scan used the same E9 and D4 settings as the successful scan. It received status 2 (CHECK CONDITION) on its first image read and returned only the two-byte image header. The following REQUEST SENSE returned `f0 00 23 00 2f ff d4 0a 00 00 00 00 80 01 00 00 00 00`. The shared SCSI parser decoded sense key/ASC/ASCQ `3/80/01` with end-of-image clear, the paper jam path. There was no JPEG payload. A later `D6` on a separate connection returned status 0.

After a receiver restart, a live scan reported the same `3/80/01` sense and produced no PDF. The receiver re-registered and delivered one readable two-page PDF when the sheet was re-fed after clearing the jam.

## Open questions

- The one-time setup that authorizes a distinct host ID on the iX1300 remains unverified.
- The notification does not distinguish paper insertion from a physical Scan button press. Those actions need separate observations.
- Which of the observed D4 differences selects the feeder remains open.
- The successful capture ends during status polling after the empty-feeder response. A separate `D6` was observed during reconnect and after the jam, but the successful scan does not establish its release sequence.
