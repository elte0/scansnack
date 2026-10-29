import datetime as dt
import io
import json
import struct
from typing import Any

import pytest
from PIL import Image

from mock_scanner import FIXTURES, MockScanner
from scansnap.adapters.ix1500 import discovery
from scansnap.adapters.ix1500.control import (
    SUB_ANNOUNCE,
    SUB_SET_PROFILES,
    Control,
    HostIdentity,
    json_body,
    parse_json_response,
    register_body,
)
from scansnap.adapters.ix1500.discovery import ScannerInfo, parse_reply
from scansnap.adapters.ix1500.scanner import Scanner, mode_block, window_block
from scansnap.adapters.ix1500.scsi import ScsiChannel
from scansnap.core.errors import FeedError, NoPaper
from scansnap.core.model import ScanSettings as Settings
from scansnap.core.model import Sheet

PAIRING: dict[str, Any] = json.loads((FIXTURES / "01_pairing.json").read_text())
CASES = {
    "02_grayscale_simplex_normalquality": Settings("gray", 150, duplex=False),
    "03_grayscale_duplex_normalquality": Settings("gray", 150, duplex=True),
    "04_color_simplex_bestquality": Settings("color", 600, duplex=False),
}
MAC = bytes.fromhex("020000000001")


def recorded(fixture: str, op: int) -> dict[str, Any]:
    fx = json.loads((FIXTURES / f"{fixture}.json").read_text())
    return next(e for e in fx["exchanges"] if bytes.fromhex(e["cdb"])[0] == op)


@pytest.mark.parametrize("fixture,settings", CASES.items())
def test_parameter_blocks_match_capture(fixture: str, settings: Settings) -> None:
    assert window_block(settings).hex() == recorded(fixture, 0xE9)["out"]
    assert mode_block(settings).hex() == recorded(fixture, 0xD4)["out"]


@pytest.mark.parametrize("fixture,settings", CASES.items())
def test_scan_against_mock(fixture: str, settings: Settings) -> None:
    mock = MockScanner(fixture)
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
            pages = list(Scanner(ch).scan(settings))
    finally:
        mock.close()

    assert len(pages) == 1
    page = pages[0]
    for side, jpeg in ((0, page.front), (1, page.back)):
        if jpeg is None:
            assert not settings.duplex and side == 1
            continue
        expected = mock.sheets[0][side]
        assert jpeg == expected
        assert Image.open(io.BytesIO(jpeg)).size == Image.open(io.BytesIO(expected)).size

    ops = [cdb[0] for cdb, _ in mock.received if cdb[0] != 0x28]
    assert ops[:5] == [0xD5, 0xD8, 0xE9, 0xD4, 0x03]
    assert ops[-1] == 0xD6
    sent = {cdb[0]: out for cdb, out in mock.received}
    assert sent[0xE9] == window_block(settings)
    assert sent[0xD4] == mode_block(settings)


def test_multiple_sheets_and_empty_feeder() -> None:
    mock = MockScanner("04_color_simplex_bestquality", sheets=3)
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
            assert len(list(Scanner(ch).scan(Settings("color", 600, duplex=True)))) == 3
    finally:
        mock.close()

    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=0)
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(NoPaper):
            list(Scanner(ch).scan(Settings()))
        assert mock.received[-1][0][0] == 0xD6  # Session wird trotzdem freigegeben
    finally:
        mock.close()


def recorded_reply() -> ScannerInfo:
    info = parse_reply(bytes.fromhex(PAIRING["discovery_reply"]))
    assert info is not None
    return info


def test_discovery_reply() -> None:
    info = recorded_reply()
    assert info.model == "ScanSnap iX1500"
    assert (info.data_port, info.control_port) == (53218, 53219)
    assert info.name.startswith("iX1500-")


def _recorded_call(command: int, sub: int | None = None) -> dict[str, Any]:
    for c in PAIRING["calls"]:
        body = bytes.fromhex(c["request_body"])
        if c["command"] == command and (sub is None or body[4:6] == struct.pack(">H", sub)):
            return c
    raise LookupError


def test_register_body_matches_capture() -> None:
    rec = bytes.fromhex(_recorded_call(0x11)["request_body"])
    ident = HostIdentity("x", rec[76:84].hex(), "0" * 32, MAC)
    tz = dt.timezone(dt.timedelta(seconds=-struct.unpack_from(">i", rec, 84)[0]))
    y, mo, d, h, mi, s = struct.unpack_from(">HBBBBB", rec, 68)
    body = register_body(ident, "192.168.178.37", flag=rec[7], now=dt.datetime(y, mo, d, h, mi, s, tzinfo=tz))
    assert body == rec


def test_announce_body_matches_capture() -> None:
    rec = bytes.fromhex(_recorded_call(0x41, SUB_ANNOUNCE)["request_body"])
    payload = json.loads(rec[20:])
    assert json_body(SUB_ANNOUNCE, payload) == rec


def test_parse_json_responses() -> None:
    resp = bytes.fromhex(_recorded_call(0x41, 0x0100)["response"])
    sub, data = parse_json_response(resp)
    assert sub == 0x0100 and "conn_user" in data
    ack = bytes.fromhex(_recorded_call(0x41, SUB_ANNOUNCE)["response"])
    assert parse_json_response(ack) == (SUB_ANNOUNCE, 0)


def test_discovery_model_has_no_trailing_bytes() -> None:
    assert recorded_reply().model == "ScanSnap iX1500"


def test_session_open_and_polling_against_mock() -> None:
    import time

    mock = MockScanner("02_grayscale_simplex_normalquality")
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
            ch.open_session()
            ch.start_polling(0.01)
            time.sleep(0.1)
            ch.stop_polling()
            assert len(list(Scanner(ch).scan(Settings()))) == 1
    finally:
        mock.close()
    ops = [cdb[0] for cdb, _ in mock.received]
    assert ops[:4] == [0x12, 0x12, 0xE7, 0xC2] and ops.count(0xC2) > 2
    assert 0xC2 not in ops[ops.index(0xD5) :]


def test_heartbeat_probe_and_reply_counting() -> None:
    import socket

    scanner = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    scanner.bind(("127.0.0.1", 0))
    mac = bytes.fromhex("020000000001")
    hb = discovery.Heartbeat("127.0.0.1", mac, host_ip="127.0.0.1")
    hb.target = scanner.getsockname()
    try:
        assert struct.unpack_from(">4sI", hb.probe) == (b"ssNR", 1)
        assert hb.probe[12:18] == mac
        assert struct.unpack_from(">HH", hb.probe, 22) == (hb.sock.getsockname()[1], 0x1000)
        hb.start()
        scanner.settimeout(2)
        probe, addr = scanner.recvfrom(64)
        assert probe == hb.probe
        scanner.sendto(b"ssNR" + b"\0" * 140, addr)
        hb.stop()
        assert hb.replies == 1
    finally:
        hb.close()
        scanner.close()


class RecordingControl(Control):
    """Control ohne Netz: protokolliert die Kommandos in ``calls``."""

    def __init__(self, ident: HostIdentity, users: dict[str, Any] | None = None):
        super().__init__("127.0.0.1", ident)
        self.users = users or {}
        self.calls: list[str] = []
        self.payloads: list[tuple[int, Any]] = []

    def local_ip(self) -> str:
        return "192.168.178.37"

    def device_name(self) -> str:
        self.calls.append("0x13")
        return "iX1500"

    def get_users(self) -> dict[str, Any]:
        self.calls.append("0x0100")
        return self.users

    def json_call(self, sub: int, payload: Any | None = None) -> Any:
        self.payloads.append((sub, payload))
        return None

    def announce(self, host_ip: str) -> Any:
        self.calls.append("0x0210")

    def state(self) -> int:
        self.calls.append("0x30")
        return 0

    def cmd12(self) -> None:
        self.calls.append("0x12")

    def register(self, host_ip: str, flag: int = 5, now: dt.datetime | None = None) -> int:
        self.calls.append(f"0x11/{flag}")
        return 0


def test_connect_order_matches_cold_start_capture() -> None:
    ctl = RecordingControl(HostIdentity("t", "0f1e2d3c4b5a6978", "U", bytes(6)))
    calls = ctl.calls
    ctl.connect()
    assert calls == ["0x13", "0x0100", "0x0210", "0x11/0"]


def test_json_ack_status_is_read_from_offset_24() -> None:
    rejected = bytes.fromhex("0000002473734e5200000000000000000000001001100000ffffffff0000000000000000")
    ok = bytes.fromhex("0000002473734e5200000000000000000000001006100000000000000000000000000000")
    assert parse_json_response(rejected) == (0x0110, 0xFFFFFFFF)
    assert parse_json_response(ok) == (0x0610, 0)


def test_claim_builds_own_profile_and_finishes_pairing() -> None:
    ident = HostIdentity("x" * 40, "8ff7d20fae1b79f6", "OWNUSER", bytes(6))
    other = {"host_id": "0f1e2d3c4b5a6978", "name": "MacBook"}
    current: dict[str, Any] = {
        "conn_user": {"users": [other], "select": {"id": ident.host_id, "kind": 1}},
        "profiles": [],
        "version": 3,
    }
    ctl = RecordingControl(ident, current)
    payload = ctl.claim("192.168.178.37")
    assert [sub for sub, _ in ctl.payloads] == [SUB_SET_PROFILES]
    assert ctl.calls == ["0x0100", "0x0210", "0x30", "0x12", "0x11/0"]
    assert payload["conn_user"]["select"] == current["conn_user"]["select"]
    own = payload["conn_user"]["users"][-1]
    assert own["host_id"] == ident.host_id and len(own["name"]) == 30
    (prof,) = payload["profiles"]
    assert prof["user_id"] == "OWNUSER" and prof["host_address"] == "192.168.178.37" and prof["scan_info"]


def test_claim_keeps_user_order_when_already_registered() -> None:
    ident = HostIdentity("scansnap-bridge", "8ff7d20fae1b79f6", "OWNUSER", bytes(6))
    me: dict[str, Any] = {
        "app_ver": 1,
        "color_id": 2,
        "host_id": ident.host_id,
        "icon_id": 9,
        "name": "alt",
        "prof_stat": 1,
        "scloud_user": "",
    }
    other = {"host_id": "0f1e2d3c4b5a6978", "name": "MacBook"}
    current: dict[str, Any] = {
        "conn_user": {"users": [me, other], "select": {"id": ident.host_id, "kind": 1}},
        "profiles": [{"user_id": "OWNUSER", "prof_id": "KEEP"}],
        "version": 3,
    }
    ctl = RecordingControl(ident, current)
    payload = ctl.claim("192.168.178.37")
    assert [u["host_id"] for u in payload["conn_user"]["users"]] == [ident.host_id, other["host_id"]]
    assert payload["conn_user"]["users"][0]["name"] == "scansnap-bridge"
    assert [p["prof_id"] for p in payload["profiles"]] == ["KEEP"]


def test_paper_jam_keeps_earlier_pages_and_releases_session() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.jam_at = 1
    pages: list[Sheet] = []
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(FeedError) as err:
            pages.extend(Scanner(ch).scan(Settings()))
        assert err.value.reason == "paper jam" and len(pages) == 1
        assert mock.received[-1][0][0] == 0xD6
    finally:
        mock.close()
