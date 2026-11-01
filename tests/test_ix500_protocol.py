"""iX500 protocol blocks against values from ScanSnap Home captures (private, see docs/protocol-ix500.md)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import struct

import pytest

from scansnap.adapters.ix500 import control, discovery, notify, scanner, wire

# Captured from ScanSnap Home 4.1.0, color/300 dpi, auto page size (no device data in these blocks)
CAPTURED_E9 = bytes.fromhex("012c012c000028d0000044dc05").ljust(32, b"\0")
CAPTURED_D4 = bytes.fromhex(
    "00030101d000c1808080908080000000000000000000000000000000000000300010012c012c05810000000028d0000044dc04"
).ljust(80, b"\0")

IDENT = control.HostIdentity(bytes.fromhex("0102030405060708"), bytes.fromhex("020000000001"))


def test_password_encoding_matches_capture() -> None:
    # Checked against a ScanSnap Home capture; example serial and password here
    assert control.encode_password("2345") == "173132180179"
    assert control.default_password("A0XX012345") == "2345"


def test_password_length_is_limited() -> None:
    with pytest.raises(ValueError):
        control.encode_password("x" * 17)


def test_register_body_layout() -> None:
    now = dt.datetime(2026, 10, 30, 19, 6, 33, tzinfo=dt.timezone(dt.timedelta(hours=-4)))
    body = control.register_body(IDENT, "192.0.2.10", 6, "2345", now)
    assert len(body) == 96
    assert body[:12] == bytes.fromhex("00061e00 00000000 00000001")
    assert body[12:16] == ipaddress.IPv4Address("192.0.2.10").packed
    assert struct.unpack_from(">I", body, 16)[0] == 55265
    assert body[20:32] == b"173132180179" and body[32:68] == b"\0" * 36
    assert body[68:76] == bytes.fromhex("07ea0a1e13062100")
    assert body[76:84] == IDENT.host_id
    assert struct.unpack_from(">i", body, 84)[0] == 14400


def test_register_body_without_password_or_time_zone() -> None:
    body = control.register_body(IDENT, "192.0.2.10", 3, None)
    assert struct.unpack_from(">I", body, 8)[0] == 0 and body[20:68] == b"\0" * 48
    assert body[84:88] == b"\0" * 4


def test_parameter_blocks_match_capture() -> None:
    assert scanner.window_block(300) == CAPTURED_E9
    assert scanner.mode_block(300) == CAPTURED_D4


@pytest.mark.parametrize(("dpi", "byte10", "byte13", "quality"), [(150, 0x80, 0, 5), (600, 0x80, 0x80, 0x0A)])
def test_mode_block_per_dpi(dpi: int, byte10: int, byte13: int, quality: int) -> None:
    b = scanner.mode_block(dpi)
    assert (b[10], b[13], b[40]) == (byte10, byte13, quality)
    assert struct.unpack_from(">HH", b, 34) == (dpi, dpi)


def test_fixed_page_size_turns_off_auto_detection() -> None:
    b = scanner.mode_block(300, "a4", multifeed="off")
    assert (b[2], b[3], b[4]) == (0, 0, 0)
    assert struct.unpack_from(">II", b, 42) == scanner.PAGE_SIZES["a4"]


def test_unsupported_dpi_is_rejected() -> None:
    with pytest.raises(ValueError):
        scanner.window_block(250)


def _reply(ip: str = "192.0.2.20") -> bytes:
    d = bytearray(132)
    d[:4] = b"VENS"
    d[8:12] = bytes.fromhex("00060030")
    d[16:20] = ipaddress.IPv4Address(ip).packed
    struct.pack_into(">II", d, 20, 53218, 53219)
    d[28:34] = bytes.fromhex("008092000001")
    d[40:56] = b"iX500-A0XX012345"
    d[104:120] = b"ScanSnap iX500  "
    d[120:124] = ipaddress.IPv4Address("192.0.2.10").packed
    d[124:132] = IDENT.host_id
    return bytes(d)


def test_parse_discovery_reply() -> None:
    info = discovery.parse_reply(_reply(), "192.0.2.20")
    assert info is not None
    assert (info.model, info.if_version, info.serial) == ("ScanSnap iX500", 6, "A0XX012345")
    assert (info.paired_ip, info.paired_host_id) == ("192.0.2.10", IDENT.host_id.hex())


def test_discovery_reply_from_another_sender_is_ignored() -> None:
    assert discovery.parse_reply(_reply(), "192.0.2.99") is None
    assert discovery.parse_reply(b"ssNR" + bytes(140)) is None


def _notice(event: int, counter: int, ip: str = "0.0.0.0") -> bytes:
    d = bytearray(48)
    struct.pack_into(">I4sI", d, 0, 48, b"VENS", event)
    d[16] = counter
    d[20:24] = ipaddress.IPv4Address(ip).packed
    return bytes(d)


def test_parse_notifications() -> None:
    button = notify.parse_notification(_notice(notify.BUTTON, 3))
    assert button is not None and (button.event, button.counter) == (notify.BUTTON, 3)
    on = notify.parse_notification(_notice(notify.POWER_ON, 1, "192.0.2.20"))
    assert on is not None and (on.event, on.counter, on.scanner_ip) == (notify.POWER_ON, 1, "192.0.2.20")
    assert notify.parse_notification(_notice(notify.POWER_OFF, 6)[:40]) is None


def test_frame_length_is_bounded() -> None:
    assert wire.client_frame(0x13, IDENT.mac)[:12] == bytes.fromhex("00000020 56454e53 00000013")
    with pytest.raises(wire.ProtocolError):

        class FakeSock:
            def recv(self, n: int) -> bytes:
                return struct.pack(">I4s", wire.MAX_FRAME + 1, b"VENS")[:n]

        wire.read_frame(FakeSock())  # type: ignore[arg-type]


def test_read_info_decodes_serial_and_counters() -> None:
    from scansnap.adapters.ix500.info import read_info
    from scansnap.adapters.ix500.scsi import Response, ScsiChannel

    replies = {
        0xE7: bytes.fromhex("00003039 00004130 58580000"),  # A0XX + 12345
        0xD9: struct.pack(">III", 1781, 12, 34).ljust(0x20, b"\0"),
        0xE6: bytes.fromhex("101e0000"),
    }

    class FakeChannel(ScsiChannel):
        def __init__(self) -> None:
            pass

        def inquiry(self) -> tuple[str, str, str]:
            return "FUJITSU", "ScanSnap iX500", "0U00"

        def checked(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
            return Response(0, replies[cdb[0]])

    info = read_info(FakeChannel())
    assert (info.serial, info.total_pages, info.roller_counts) == ("A0XX012345", 1781, (12, 34))
    assert info.power_off_minutes == 30


def test_registration_errors_name_the_known_reasons() -> None:
    assert "password" in str(control.RegistrationError(-2))
    assert "not paired" in str(control.RegistrationError(-7))
    assert "unknown reason" in str(control.RegistrationError(-4))
