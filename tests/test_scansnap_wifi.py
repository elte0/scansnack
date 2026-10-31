"""Shared ScanSnap Wi-Fi primitives produce the iX1500 bytes with ``ssNR`` (ADR 0007: the iX1500 can move over)."""

from __future__ import annotations

import struct

from scansnap.adapters.ix1500 import button as ix1500_button
from scansnap.adapters.ix1500 import discovery as ix1500_discovery
from scansnap.adapters.ix1500 import wire as ix1500_wire
from scansnap.adapters.scansnap_wifi import discovery, notify, wire

MAC = bytes.fromhex("020000000001")


def test_framing_matches_the_ix1500_adapter() -> None:
    body = bytes(range(40))
    extra = bytes.fromhex("c0000201")
    assert wire.client_frame(0x11, MAC, body, extra, magic=wire.SSNR) == ix1500_wire.client_frame(
        0x11, MAC, body, extra
    )
    assert wire.server_frame(b"abc", 2, magic=wire.SSNR) == ix1500_wire.server_frame(b"abc", 2)


def test_probe_matches_the_ix1500_adapter() -> None:
    for tag, flag in ix1500_discovery.PROBES:
        assert discovery.build_probe(tag, flag, "192.0.2.10", MAC, 55264, seq=1) == ix1500_discovery.build_probe(
            tag, flag, "192.0.2.10", MAC, 55264, seq=1
        )


def test_button_notice_parses_like_the_ix1500_adapter() -> None:
    d = bytearray(48)
    struct.pack_into(">I4sI", d, 0, 48, b"ssNR", 1)
    d[16], d[20:24], d[24:30] = 7, bytes([192, 0, 2, 20]), MAC
    shared = notify.parse_notification(bytes(d), magic=wire.SSNR)
    theirs = ix1500_button.parse_notification(bytes(d))
    assert shared is not None and theirs is not None
    assert (shared.counter, shared.scanner_ip, shared.scanner_mac) == (
        theirs.counter,
        theirs.scanner_ip,
        theirs.scanner_mac,
    )


def _serve_once(reply: bytes) -> tuple[int, list[bytes]]:
    """A one-shot TCP server: sends ``reply`` on connect, records what the client sends until it closes."""
    import socket
    import threading

    srv = socket.create_server(("127.0.0.1", 0))
    received: list[bytes] = []

    def run() -> None:
        conn, _ = srv.accept()
        with conn:
            conn.sendall(reply)
            try:
                while chunk := conn.recv(1024):
                    received.append(chunk)
            except ConnectionResetError:  # closed with unread data: the OS resets instead of a clean EOF
                pass
            received.append(b"<closed>")
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1], received


def test_bad_greeting_closes_the_socket() -> None:
    import time

    import pytest

    from scansnap.adapters.scansnap_wifi.scsi import ScsiChannel

    port, received = _serve_once(wire.server_frame(magic=wire.SSNR))
    with pytest.raises(wire.ProtocolError):
        ScsiChannel("127.0.0.1", MAC, magic=wire.VENS, port=port, timeout=2)
    deadline = time.monotonic() + 2
    while not received and time.monotonic() < deadline:
        time.sleep(0.01)
    assert received == [b"<closed>"]  # the one scan-channel slot is free again


def test_short_scsi_reply_is_rejected() -> None:
    import pytest

    from scansnap.adapters.scansnap_wifi.scsi import ScsiChannel

    port, _ = _serve_once(wire.server_frame(magic=wire.VENS) + wire.server_frame(magic=wire.VENS))
    with ScsiChannel("127.0.0.1", MAC, magic=wire.VENS, port=port, timeout=2) as ch, pytest.raises(wire.ProtocolError):
        ch.checked(bytes.fromhex("d80000000000"))  # 16-byte frame instead of the 40-byte SCSI reply
