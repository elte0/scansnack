# ScanSnap iX1300 Wi-Fi Protocol

A capture of ScanSnap Home receiving one Return Scan over Wi-Fi shows the iX500's `VENS` framing and scan notification, a 48-byte hardware status response, and two JPEGs per sheet. The host was already configured for the scanner.

This is an independent interoperability write-up. See the [disclaimer](disclaimer.md) and the [protocol overview](protocol.md).

## Source

One private packet capture of a single test sheet fed through the iX1300's front Return Scan slot. The capture covers idle polling, a scan notification and the resulting scan. The host ran ScanSnap Home on macOS. Replaying the image reads through ScanSnack's iX500 reader at commit `eb80600` decoded both JPEGs. The raw capture contains private network and image data.

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

## Open questions

- The capture contains no traffic on TCP 53219. The registration request and what the scanner requires to notify a new host need a separate capture.
- The notification does not distinguish paper insertion from a physical Scan button press. Those actions need separate observations.
- The capture covers one front-slot profile. A top-feeder scan with the same profile would help distinguish feeder selection from other model-specific settings.
- The capture ends during status polling after the empty-feeder response. It does not establish whether ScanSnap Home sends a separate release command later.
