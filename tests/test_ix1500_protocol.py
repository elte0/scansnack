import datetime as dt
import io
import json
import struct
from typing import Any

import pytest
from PIL import Image

from mock_scanner import FIXTURES, MockScanner
from scansnap.adapters.ix1500 import discovery, wire
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
from scansnap.adapters.ix1500.scanner import Scanner, double_feed, mode_block, panel_decision, window_block
from scansnap.adapters.ix1500.scsi import ScsiChannel
from scansnap.core.errors import FeedError, NoPaper
from scansnap.core.fixes import IX1500_PAIRING_CLEANUP, Fixes
from scansnap.core.model import Fault, FaultDecision, Sheet
from scansnap.core.model import ScanSettings as Settings

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
        assert mock.received[-1][0][0] == 0xD6  # the session is released anyway
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
    """Control without a network: logs the commands in ``calls``."""

    def __init__(self, ident: HostIdentity, users: dict[str, Any] | None = None, fixes: Fixes | None = None):
        super().__init__("127.0.0.1", ident, fixes=fixes)
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


NO_CONN_USER: list[dict[str, Any]] = [{"profiles": [], "version": 3}, {"conn_user": {"users": []}}]
FIX_19 = Fixes([IX1500_PAIRING_CLEANUP.id])


@pytest.mark.parametrize("users", NO_CONN_USER)
def test_claim_without_conn_user_keeps_previous_behaviour_by_default(users: dict[str, Any]) -> None:
    ctl = RecordingControl(HostIdentity("scansnap-bridge", "8ff7d20fae1b79f6", "OWNUSER", bytes(6)), users)
    with pytest.raises(KeyError):
        ctl.claim("192.168.178.37")
    assert ctl.calls == ["0x0100"]  # pairing is not finished: the panel hangs on "Processing" (#19)


@pytest.mark.parametrize("users", NO_CONN_USER)
def test_fix_19_claim_without_conn_user_fails_clearly_and_finishes_pairing(users: dict[str, Any]) -> None:
    ident = HostIdentity("scansnap-bridge", "8ff7d20fae1b79f6", "OWNUSER", bytes(6))
    ctl = RecordingControl(ident, users, FIX_19)
    with pytest.raises(wire.ProtocolError, match="conn_user"):
        ctl.claim("192.168.178.37")
    assert ctl.payloads == []
    assert ctl.calls == ["0x0100", "0x30", "0x12", "0x11/0"]


def test_fix_19_claim_succeeds_like_before() -> None:
    ident = HostIdentity("scansnap-bridge", "8ff7d20fae1b79f6", "OWNUSER", bytes(6))
    current: dict[str, Any] = {
        "conn_user": {"users": [], "select": {"id": "0f1e2d3c4b5a6978", "kind": 1}},
        "profiles": [],
        "version": 3,
    }
    before, after = RecordingControl(ident, current), RecordingControl(ident, current, FIX_19)
    old, new = before.claim("192.168.178.37"), after.claim("192.168.178.37")
    assert before.calls == after.calls == ["0x0100", "0x0210", "0x30", "0x12", "0x11/0"]
    for p in (old, new):
        p["profiles"][0].pop("prof_id")  # random for a new profile
    assert old == new


class FailingAnnounce(RecordingControl):
    def announce(self, host_ip: str) -> Any:
        super().announce(host_ip)
        raise wire.ProtocolError("rejected")


@pytest.mark.parametrize(
    ("fixes", "tail"), [(None, ["0x11/5", "0x0210"]), (FIX_19, ["0x0210", "0x30", "0x12", "0x11/0"])]
)
def test_pairing_handshake_finishes_pairing_on_error_only_with_fix_19(fixes: Fixes | None, tail: list[str]) -> None:
    ctl = FailingAnnounce(HostIdentity("scansnap-bridge", "8ff7d20fae1b79f6", "OWNUSER", bytes(6)), fixes=fixes)
    with pytest.raises(wire.ProtocolError):
        ctl.handshake()
    assert ctl.calls[-len(tail) :] == tail


def test_fixes_ignore_unknown_ids_and_switch() -> None:
    fixes = Fixes(["gone", IX1500_PAIRING_CLEANUP.id])
    assert fixes.ids == [IX1500_PAIRING_CLEANUP.id]
    fixes.set(IX1500_PAIRING_CLEANUP, False)
    assert not fixes.enabled(IX1500_PAIRING_CLEANUP) and fixes.ids == []
    label = IX1500_PAIRING_CLEANUP.label
    assert label.startswith("#19: ") and len(label.split(": ", 1)[1].split()) <= 5


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


def test_next_batch_after_a_double_feed_scans_normally() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=2)
    mock.double_feed_at = 0
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
            with pytest.raises(FeedError):
                list(Scanner(ch, poll_interval=0.01).scan(Settings(), hold=True))  # the panel answers "finish"
            mock.refill()
            assert len(list(Scanner(ch).scan(Settings(), hold=True))) == 2
    finally:
        mock.close()


def test_double_feed_is_reported_when_the_connection_drops_on_ee() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=2)
    mock.double_feed_at = 0
    mock.drop_on = 0xEE  # EE and the D6 after it both fail
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(FeedError) as err:
            list(Scanner(ch).scan(Settings(), hold=True))
        assert err.value.reason == "double feed"
    finally:
        mock.close()


def test_double_feed_status() -> None:
    # C2 blocks from the private double-feed capture (host ID bytes left out)
    idle = bytes.fromhex("00000000000000000000000004090000e000000000180000")
    double = bytes.fromhex("00000080000001550000000004090000e000000000180000")
    assert double_feed(idle) is None
    assert double_feed(double) == 0x55
    assert double_feed(idle[:7] + b"\x42" + idle[8:]) is None  # an unknown code alone is not acted on
    assert double_feed(bytes(4)) is None


def run_scan(
    mock: MockScanner, settings: Settings, scanner: dict[str, Any] | None = None, **kwargs: Any
) -> tuple[list[Sheet], FeedError | None]:
    sheets: list[Sheet] = []
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
            sheets.extend(Scanner(ch, poll_interval=0.01, **(scanner or {})).scan(settings, hold=True, **kwargs))
    except FeedError as e:
        return sheets, e
    return sheets, None


def ops(mock: MockScanner) -> list[int]:
    return [cdb[0] for cdb, _ in mock.received if cdb[0] not in (0x28, 0x03)]


def test_double_feed_holds_the_batch_and_finish_on_the_panel_ends_it() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, "finish"
    try:
        sheets, err = run_scan(mock, Settings())
        assert err is not None and err.reason == "double feed" and "0x55" in str(err)
        assert len(sheets) == 1  # "finish" without saving: the double-fed sheet is not delivered
        seq = ops(mock)
        ee = seq.index(0xEE)
        assert seq[ee - 1 : ee + 2] == [0xC2, 0xEE, 0xD8] and seq[-1] == 0xD6
        assert seq[ee + 2 :].count(0xC2) >= 3  # held: C2 polled until the panel answers
        ee_out = next(out for cdb, out in mock.received if cdb[0] == 0xEE)
        assert ee_out == bytes.fromhex("0000005500000000")  # as ScanSnap Home sends it
    finally:
        mock.close()


def test_double_feed_continue_on_the_panel_scans_the_rest() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, "continue"
    try:
        sheets, err = run_scan(mock, Settings())
        assert err is None and [s.fault for s in sheets] == [None, None]  # not kept by default
        seq = ops(mock)
        held = seq[seq.index(0xEE) + 2 :]
        cont = held.index(0xD8)  # ScanSnap Home's continue: D8, E9, D4, C2, then E0
        assert held[cont : cont + 5] == [0xD8, 0xE9, 0xD4, 0xC2, 0xE0] and seq[-1] == 0xD6
        assert 0xD5 not in held
    finally:
        mock.close()


def test_double_feed_finish_with_image_delivers_the_sheet() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, "finish_keep"
    try:
        sheets, err = run_scan(mock, Settings())
        assert err is not None and err.reason == "double feed"
        assert [s.fault for s in sheets] == [None, "double feed"]
    finally:
        mock.close()


def test_the_app_can_decide_instead_of_the_panel() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, None
    asked: list[Fault] = []

    def decide(fault: Fault) -> FaultDecision | None:
        asked.append(fault)
        return FaultDecision(resume=True, keep=False) if len(asked) > 2 else None

    try:
        sheets, err = run_scan(mock, Settings(), decide=decide)
        assert err is None and len(sheets) == 2
        assert asked[0] == Fault("double feed", 2)
    finally:
        mock.close()


def test_jam_during_a_read_holds_and_continue_rescans_the_sheet() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=3)
    mock.jam_in_read_at, mock.panel_choice = 1, "continue"
    try:
        sheets, err = run_scan(mock, Settings(duplex=True))
        assert err is None and len(sheets) == 3  # the user put the jammed sheet back
        seq = ops(mock)
        assert 0xEE not in seq  # ScanSnap Home: D8 only after a jam
        jam = seq.index(0xD8, seq.index(0xE0))
        assert seq[jam + 1] == 0xC2  # held
        cont = seq.index(0xD8, jam + 1)
        assert seq[cont : cont + 5] == [0xD8, 0xE9, 0xD4, 0xC2, 0xE0]
    finally:
        mock.close()


def test_jam_finish_on_the_panel_reports_the_jam() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=3)
    mock.jam_in_read_at, mock.panel_choice = 1, "finish"
    try:
        sheets, err = run_scan(mock, Settings(duplex=True))
        assert err is not None and err.reason == "paper jam" and len(sheets) == 1
        assert ops(mock)[-1] == 0xD6
    finally:
        mock.close()


def test_jam_at_feed_ends_the_batch() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=2)
    mock.jam_at, mock.panel_choice = 1, "continue"
    try:
        sheets, err = run_scan(mock, Settings())
        assert err is not None and err.reason == "paper jam" and len(sheets) == 1
        assert ops(mock)[-2:] == [0xE0, 0xD6]  # not held: E0's jam was never captured with ScanSnap Home
    finally:
        mock.close()


def test_held_batch_ends_after_the_timeout_without_an_answer() -> None:
    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, None
    held: list[Fault] = []
    try:
        sheets, err = run_scan(mock, Settings(), {"hold_timeout": 0.2, "on_hold": held.append})
        assert err is not None and err.reason == "double feed" and len(sheets) == 1
        assert held == [Fault("double feed", 2)]
        seq = ops(mock)
        assert seq[-1] == 0xD6 and 0xE0 not in seq[seq.index(0xEE) :]
    finally:
        mock.close()


def test_the_app_can_end_the_batch_at_once() -> None:
    def finish(_fault: Fault) -> FaultDecision:
        return FaultDecision(resume=False, keep=False)

    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice = 1, None
    try:
        sheets, err = run_scan(mock, Settings(), decide=finish)
        assert err is not None and err.reason == "double feed" and len(sheets) == 1
        seq = ops(mock)
        assert seq[seq.index(0xEE) :] == [0xEE, 0xD8, 0xC2, 0xD6]
    finally:
        mock.close()


def test_two_faults_in_one_batch_are_answered_separately() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=4)
    mock.double_feed_at, mock.jam_in_read_at, mock.panel_choice = 0, 2, "continue"
    held: list[Fault] = []
    try:
        sheets, err = run_scan(mock, Settings(duplex=True), {"on_hold": held.append})
        assert err is None and len(sheets) == 3  # the double-fed sheet is not kept
        assert held == [Fault("double feed", 1), Fault("paper jam", 2)]  # counted in the delivered sheets
    finally:
        mock.close()


def test_a_panel_bit_left_set_does_not_answer_the_next_fault() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=4)
    mock.double_feed_at, mock.jam_in_read_at, mock.panel_choice, mock.panel_stuck = 0, 2, "continue", True
    try:
        sheets, err = run_scan(mock, Settings(duplex=True), {"hold_timeout": 0.3})
        assert err is not None and err.reason == "paper jam" and len(sheets) == 1  # waited, then timed out
    finally:
        mock.close()


def test_jam_is_reported_when_d8_fails() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=3)
    mock.jam_in_read_at, mock.drop_on, mock.drop_nth = 1, 0xD8, 2  # the D8 after the jam
    try:
        sheets, err = run_scan(mock, Settings(duplex=True))
        assert err is not None and err.reason == "paper jam" and len(sheets) == 1
    finally:
        mock.close()


def test_a_failing_tick_does_not_end_the_hold() -> None:
    def tick() -> None:
        raise json.JSONDecodeError("bad reply", "", 0)

    mock = MockScanner("02_grayscale_simplex_normalquality", sheets=3)
    mock.double_feed_at, mock.panel_choice, mock.panel_after = 1, "continue", 5
    try:
        sheets, err = run_scan(mock, Settings(), {"on_hold_tick": tick, "tick_every": 0})
        assert err is None and len(sheets) == 2
    finally:
        mock.close()


def test_without_the_fix_the_scan_sequence_is_unchanged() -> None:
    mock = MockScanner("03_grayscale_duplex_normalquality", sheets=3)
    mock.double_feed_at, mock.jam_in_read_at = 0, 1
    pages: list[Sheet] = []
    try:
        with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(FeedError) as err:
            pages.extend(Scanner(ch).scan(Settings(duplex=True)))
        assert err.value.reason == "paper jam" and len(pages) == 1  # the double feed goes unnoticed
        seq = ops(mock)
        assert 0xC2 not in seq and 0xEE not in seq and seq[-1] == 0xD6  # ends at the jam, not held
    finally:
        mock.close()


def test_panel_decision() -> None:
    # C2 blocks from the private ScanSnap Home decision captures (host ID bytes left out)
    held = bytes.fromhex("00008000000001550000000004090000e000000000180000")
    cont = bytes.fromhex("00008000010001550000000004090000e000000000190000")
    finish = bytes.fromhex("00000080000001550000000004090800e000000000180000")
    finish_keep = bytes.fromhex("00000080000001550000000004094000e000000000190000")
    assert panel_decision(held) is None
    assert panel_decision(cont) == FaultDecision(resume=True, keep=False)
    assert panel_decision(finish) == FaultDecision(resume=False, keep=False)
    assert panel_decision(finish_keep) == FaultDecision(resume=False, keep=True)
    assert panel_decision(bytes(8)) is None
