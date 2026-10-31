"""Diagnostic report: anonymization, mail link, and the read-only diagnosis against fake servers."""

from __future__ import annotations

import dataclasses
import json
import socket
import struct
import threading
from collections.abc import Callable, Iterator
from urllib.parse import parse_qs, urlsplit

import pytest

from scansnap.adapters.ix1500 import wire
from scansnap.adapters.ix1500.control import SUB_GET_USERS, json_body
from scansnap.adapters.ix1500.diagnostics import Ix1500Diagnostics
from scansnap.core.diagnostics import DiagnosticReport, Exchange, Redaction, mail_url, text_redaction

SERIAL = "AB12CD34EF56"
NAME = f"iX1500-{SERIAL}"
MAC = bytes.fromhex("a1b2c3d4e5f6")
USERS = {"conn_user": {"users": [{"name": "Alice's MacBook", "host_id": "0123456789abcdef"}], "select": 0}}


def report(*exchanges: Exchange, summary: dict[str, str] | None = None) -> DiagnosticReport:
    return DiagnosticReport("1.2.3", "TestOS", summary or {}, list(exchanges), created="2026-10-31T12:00:00Z")


def test_redactions_keep_length_and_replace_longest_first() -> None:
    raw = b"name " + NAME.encode() + b" serial " + SERIAL.encode()
    r = report(Exchange("probe", b"", raw, f"from {NAME}"), summary={"model": "ScanSnap iX9", "who": NAME})
    out = r.anonymized([text_redaction("serial", SERIAL), text_redaction("device name", NAME)])
    received = out.exchanges[0].received
    assert len(received) == len(raw)
    assert SERIAL.encode() not in received
    assert b"DEVICENAMExxxxxxxxx" in received  # the whole name, not only the serial inside it
    assert out.summary == {"model": "ScanSnap iX9", "who": "DEVICENAMExxxxxxxxx"}
    assert out.redacted == ["device name", "serial"]


def test_redaction_must_keep_length() -> None:
    with pytest.raises(ValueError):
        Redaction("x", b"abc", b"ab")


def test_json_and_file_name() -> None:
    r = report(Exchange("tcp 1 greeting", b"\x01", b"\xff", "ok"), summary={"model": "ScanSnap iX500"})
    data = json.loads(r.to_json())
    assert data["format"] == "scansnack-diagnostics/1"
    assert data["exchanges"] == [{"step": "tcp 1 greeting", "outcome": "ok", "sent": "01", "received": "ff"}]
    assert r.file_name() == "scansnack-diagnostics-ScanSnap-iX500-2026-10-31.json"


def test_mail_url() -> None:
    r = dataclasses.replace(report(), model="iX500", notes="Discovery finds nothing.")
    url = urlsplit(mail_url(r, "/Users/me/Desktop/report.json", to="dev@example.org"))
    query = parse_qs(url.query)
    assert (url.scheme, url.path) == ("mailto", "dev@example.org")
    assert query["subject"] == ["ScanSnack diagnostics: iX500"]
    assert "/Users/me/Desktop/report.json" in query["body"][0]
    assert "Discovery finds nothing." in query["body"][0]


# --- diagnosis against fake servers ----------------------------------------------------------


def discovery_reply() -> bytes:
    d = bytearray(144)
    d[:4] = wire.MAGIC
    d[16:20] = bytes([127, 0, 0, 1])
    struct.pack_into(">II", d, 20, 53218, 53219)
    d[28:34] = MAC
    d[40 : 40 + len(NAME)] = NAME.encode()
    d[104:120] = b"ScanSnap iX1500 "
    d[124:132] = bytes.fromhex("0123456789abcdef")
    d[132:144] = SERIAL.encode()
    return bytes(d)


def udp_responder() -> tuple[socket.socket, list[bytes]]:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    probes: list[bytes] = []

    def run() -> None:
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except OSError:
                return
            probes.append(data)
            if data[:4] == wire.MAGIC:
                s.sendto(discovery_reply(), addr)

    threading.Thread(target=run, daemon=True).start()
    return s, probes


def tcp_server(handle: Callable[[bytes], bytes]) -> socket.socket:
    """Greets every connection like the scanner and answers each frame with ``handle``."""
    srv = socket.create_server(("127.0.0.1", 0))

    def serve(conn: socket.socket) -> None:
        with conn:
            try:
                conn.sendall(wire.server_frame())
                while True:
                    conn.sendall(handle(wire.read_frame(conn)))
            except (OSError, wire.ProtocolError):
                return

    def accept() -> None:
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    return srv


def control(frame: bytes) -> bytes:
    command = struct.unpack_from(">I", frame, 8)[0]
    if command == 0x13:
        return wire.server_frame(NAME.encode().ljust(64, b"\0"))
    return wire.server_frame(json_body(SUB_GET_USERS, USERS))


def scan_channel(frame: bytes) -> bytes:
    cdb = frame[48:]
    data = b""
    if cdb[:2] == b"\x12\x00":
        data = (b"\x06\x00\x92\x02\x5b\x00\x00\x10" + b"FUJITSU ScanSnap iX1500 0M00").ljust(0x60, b"\0")
    elif cdb[0] == 0xC2:
        data = bytes(0x30)
    return wire.server_frame(b"\0" * 24 + data)


@pytest.fixture
def servers() -> Iterator[tuple[int, int, int, list[bytes]]]:
    udp, probes = udp_responder()
    ctl, scsi = tcp_server(control), tcp_server(scan_channel)
    yield udp.getsockname()[1], ctl.getsockname()[1], scsi.getsockname()[1], probes
    for s in (udp, ctl, scsi):
        s.close()


def test_diagnosis_records_answers_and_anonymizes(servers: tuple[int, int, int, list[bytes]]) -> None:
    udp_port, control_port, data_port, probes = servers
    steps: list[str] = []
    diagnostics = Ix1500Diagnostics(
        "1.2.3", udp_port=udp_port, control_port=control_port, data_port=data_port, tcp_ports=(), broadcast=False
    )
    r = diagnostics.run("127.0.0.1", steps.append)

    assert len(steps) == 4
    assert len(probes) == 3  # VENS, ssNR, V2ss – unicast only
    assert r.summary["model"] == "ScanSnap iX1500"
    assert r.summary["inquiry"] == "FUJITSU ScanSnap iX1500 rev 0M00"
    assert r.summary["open_tcp_ports"] == ", ".join(map(str, sorted((control_port, data_port))))
    assert r.summary["control_channel"] == r.summary["scan_channel"] == "answered"
    names = [e.step for e in r.exchanges]
    assert f"tcp {control_port} device name (0x13)" in names
    assert f"tcp {data_port} REQUEST SENSE" in names

    text = r.to_json()
    for secret in (SERIAL, SERIAL.encode().hex(), MAC.hex(), "127.0.0.1", "7f000001", "0123456789abcdef"):
        assert secret not in text
    assert "Alice" not in text and b"Alice".hex() not in text  # only the JSON structure of 0x0100
    users = next(e for e in r.exchanges if "users and profiles" in e.step)
    assert '"users": [{"name": "str", "host_id": "str"}]' in users.outcome
    assert {"serial", "device name", "scanner MAC", "scanner IP"} <= set(r.redacted)


def test_diagnosis_of_silent_device() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", 0))  # receives the probes, never answers
        closed = socket.create_server(("127.0.0.1", 0))
        port = closed.getsockname()[1]
        closed.close()
        diagnostics = Ix1500Diagnostics(
            "1.2.3", udp_port=udp.getsockname()[1], control_port=port, data_port=port, tcp_ports=(), broadcast=False
        )
        r = diagnostics.run("127.0.0.1")
    assert r.summary["discovery"] == "no reply"
    assert r.summary["open_tcp_ports"] == "none"
    assert r.summary["control_channel"] == f"port {port} not open"
