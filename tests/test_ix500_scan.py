"""iX500 scan sequence against a simulated scanner that follows the ScanSnap Home captures."""

from __future__ import annotations

import socket
import struct
import threading
from collections.abc import Iterator

import pytest

from helpers import jpeg
from scansnap.adapters.ix500 import wire
from scansnap.adapters.ix500.scanner import Scanner, mode_block, window_block
from scansnap.adapters.ix500.scsi import ScsiChannel
from scansnap.core.errors import FeedError, NoPaper

MAC = bytes.fromhex("020000000001")
INFO = bytes.fromhex("00000a3000001130 00000a3000000d90 012c012c00000a34 00000d9600000000")


def sense(key: int = 0, asc: int = 0, ascq: int = 0, eom: bool = False) -> bytes:
    d = bytearray(18)
    d[0], d[2], d[7], d[12], d[13] = 0xF0, (0x60 if eom else 0) | key, 0x0A, asc, ascq
    return bytes(d)


class MockIx500:
    """Answers like the iX500 in the captures: one read per side, EOM sense after each image."""

    def __init__(self, sheets: int, jam_at: int | None = None, chunk: int | None = None, read_jam: bool = False):
        self.read_jam = read_jam  # the read itself reports a paper jam (no image data)
        self.images = [jpeg(80, 120, "white"), jpeg(80, 120, "gray")]
        self.chunk = chunk  # bytes per read; None: whole image in one read (as in the captures)
        self._pos: dict[int, int] = {}
        self.sheets, self.jam_at = sheets, jam_at
        self.received: list[bytes] = []
        self.blocks: dict[int, bytes] = {}
        self._fed = 0
        self._sense = sense()
        self.srv = socket.create_server(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        conn, _ = self.srv.accept()
        with conn:
            conn.sendall(wire.server_frame())
            while True:
                try:
                    req = wire.read_frame(conn)
                except wire.ProtocolError:
                    return
                cdb_len, in_len, _out_len = struct.unpack_from(">III", req, 32)
                cdb, out = req[48 : 48 + cdb_len], req[64:]
                self.received.append(cdb)
                if out:
                    self.blocks[cdb[0]] = out
                status, data = self._handle(cdb, in_len)
                conn.sendall(wire.server_frame(b"\0" * 24 + data, status))

    def _handle(self, cdb: bytes, in_len: int) -> tuple[int, bytes]:
        op = cdb[0]
        if op == 0x03:
            s, self._sense = self._sense, sense()
            return 0, s
        if op == 0xC2:
            return 0, bytes(0x20)
        if op == 0xD5:
            return 0, bytes(8)
        if op == 0xE0:
            self._fed += 1
            if self._fed == self.jam_at:
                self._sense = sense(3, 0x80, 0x01)
                return 2, b""
            if self._fed > self.sheets:
                self._sense = sense(3, 0x80, 0x03)
                return 2, b""
            return 0, b""
        if op == 0x28 and cdb[2] == 0x80:
            return 0, INFO
        if op == 0x28 and self.read_jam:
            self._sense = sense(3, 0x80, 0x01)
            return 2, b""
        if op == 0x28:
            index = cdb[10]
            image = self.images[index % 2]
            pos = self._pos.get(index, 0)
            size = self.chunk or len(image)
            part, number = image[pos : pos + size], pos // size
            self._pos[index] = pos + len(part)
            if self._pos[index] < len(image):
                return 0, bytes([index, number]) + part
            self._sense = sense(eom=True)
            return 2, bytes([index, number]) + part
        return 0, b""


@pytest.fixture
def make_mock() -> Iterator[type[MockIx500]]:
    mocks: list[MockIx500] = []

    class Tracked(MockIx500):
        def __init__(self, sheets: int, jam_at: int | None = None, chunk: int | None = None, read_jam: bool = False):
            super().__init__(sheets, jam_at, chunk, read_jam)
            mocks.append(self)

    yield Tracked
    for m in mocks:
        m.srv.close()


def test_two_sheets_follow_the_captured_sequence(make_mock: type[MockIx500]) -> None:
    mock = make_mock(sheets=2)
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
        sheets = list(Scanner(ch).scan(300))
    assert len(sheets) == 2
    assert sheets[0].front == mock.images[0] and sheets[0].back == mock.images[1]
    assert sheets[0].info is not None and (sheets[0].info.width, sheets[0].info.height) == (2608, 3472)
    ops = [c[0] for c in mock.received]
    assert ops[:7] == [0xC2, 0xD5, 0xD8, 0xE9, 0xD4, 0xC2, 0x03]
    per_sheet = [0xE0, 0x28, 0x03, 0x28, 0x03, 0x28, 0xC2]
    assert ops[7:21] == per_sheet * 2
    assert ops[21:] == [0xE0, 0x03]
    # image index in byte 10: front 0, back 1, info 1; next sheet 2, 3
    reads = [c for c in mock.received if c[0] == 0x28]
    expected = [(0, 0, 0), (0, 0x80, 1), (0x80, 0x80, 1), (0, 0, 2), (0, 0x80, 3), (0x80, 0x80, 3)]
    assert [(c[2], c[5], c[10]) for c in reads] == expected
    assert mock.blocks[0xE9] == window_block(300) and mock.blocks[0xD4] == mode_block(300)


def test_empty_feeder_raises_no_paper(make_mock: type[MockIx500]) -> None:
    mock = make_mock(sheets=0)
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(NoPaper):
        list(Scanner(ch).scan(300))


def test_jam_raises_feed_error_after_delivered_sheets(make_mock: type[MockIx500]) -> None:
    mock = make_mock(sheets=3, jam_at=2)
    delivered = 0
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(FeedError, match="paper jam"):
        for _ in Scanner(ch).scan(300):
            delivered += 1
    assert delivered == 1


def test_image_in_several_reads_is_joined(make_mock: type[MockIx500]) -> None:
    # UNVERIFIED on hardware: images above one read; the client repeats the read CDB
    mock = make_mock(sheets=1, chunk=200)
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch:
        sheets = list(Scanner(ch).scan(300))
    assert sheets[0].front == mock.images[0] and sheets[0].back == mock.images[1]
    assert sum(1 for c in mock.received if c[0] == 0x28 and c[2] == 0) > 2


def test_jam_during_a_read_reports_the_jam(make_mock: type[MockIx500]) -> None:
    mock = make_mock(sheets=1, read_jam=True)
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(FeedError, match="paper jam"):
        list(Scanner(ch).scan(300))


def test_image_without_an_end_is_cut_off(make_mock: type[MockIx500], monkeypatch: pytest.MonkeyPatch) -> None:
    from scansnap.adapters.ix500 import scanner as scanner_mod
    from scansnap.adapters.ix500.scsi import ScsiError

    monkeypatch.setattr(scanner_mod, "MAX_IMAGE", 100)
    mock = make_mock(sheets=1, chunk=200)
    with ScsiChannel("127.0.0.1", MAC, port=mock.port) as ch, pytest.raises(ScsiError, match="without an end"):
        list(Scanner(ch).scan(300))
