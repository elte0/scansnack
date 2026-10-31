"""Protocol primitives shared by the ScanSnap Wi-Fi models (ADR 0007): framing, SCSI tunnel, discovery and
heartbeat, UDP notifications. The models differ in the magic (iX1500 ``ssNR``, iX500 ``VENS``) and in what
they send on top; that stays in the model's adapter."""
