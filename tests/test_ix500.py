"""iX500 (beta): packet layouts after the third-party write-up and the session flow against a fake scanner.

There are no iX500 captures of our own; the layouts below come from the write-up quoted in docs/protocol.md.
"""

from __future__ import annotations

import datetime as dt
import io
import socket
import struct
import threading

from PIL import Image

from helpers import jpeg
from mock_scanner import sense
from scansnap.adapters.ix1500 import wire
from scansnap.adapters.ix1500.control import HostIdentity
from scansnap.adapters.ix1500.discovery import parse_reply
from scansnap.adapters.ix1500.ix500 import (
    Ix500Device,
    RegistrationError,
    announcement,
    capture_key,
    device_info,
    key_from_register,
    mode_block,
    register_body,
)
from scansnap.core.model import ScanSettings

IDENT = HostIdentity("test", "36c7c7e47a800000", "0" * 32, bytes.fromhex("020000000001"), pairing_key="KEY123456789")
NOW = dt.datetime(2026, 3, 10, 21, 0, 26, tzinfo=dt.timezone(dt.timedelta(hours=7)))

# 0x11 frame of the write-up (pkt offsets 0–127), with its example IP 192.168.1.153 and a dummy key
WRITEUP_REGISTER = bytes.fromhex(
    "0000008056454e530000001100000000"  # length 128, VENS, 0x11
    "02000000000100000000000000000000"  # host MAC
    "00061e000000000000000001c0a80199"  # body 0: 00 06 1e 00, 0, 1, host IP
    "0000d7e1" + b"KEY123456789".hex() + "00000000"  # host port 55265  # pairing key, 16-byte field
    "000000000000000000000000"
    "07ea030a15001a00"  # 2026-03-10 21:00:26
    "36c7c7e47a800000"  # host ID
    "ffff9d90"  # time zone bias (UTC+7)
    "00000000000000000000000000000000000000000000000000000000"
)
WRITEUP_D4_COLOR_300 = bytes.fromhex(
    "00030101d000c1808080908080000000000000000000000000000000000000300010012c012c05810000000028d0000044dc04"
).ljust(80, b"\0")


def test_register_frame_matches_writeup() -> None:
    body = register_body(IDENT, "192.168.1.153", "KEY123456789", NOW)
    frame = wire.client_frame(0x11, IDENT.mac, body, magic=wire.VENS)
    assert len(frame) == 128
    assert frame == WRITEUP_REGISTER
    assert key_from_register(frame) == "KEY123456789"


def test_mode_block_color_300_matches_writeup() -> None:
    assert mode_block(ScanSettings("color", 300)) == WRITEUP_D4_COLOR_300
    assert mode_block(ScanSettings("gray", 150))[34:38] == bytes.fromhex("00960096")


def test_device_info_is_parsed_as_ix500() -> None:
    info = parse_reply(device_info("192.168.1.20", bytes.fromhex("008092000001"), "iX500-A0TEST0000"))
    assert info is not None
    assert (info.ip, info.model, info.name, info.serial) == ("192.168.1.20", "ScanSnap iX500", "iX500-A0TEST0000", "")
    assert (info.data_port, info.control_port) == (53218, 53219)


def test_announcement_layout() -> None:
    b = announcement("192.168.1.20", bytes.fromhex("008092000001"))
    assert len(b) == 48 and b[4:8] == b"VENS" and b[8:12] == bytes.fromhex("00000021")
    assert b[24:28] == bytes([192, 168, 1, 20]) and b[28:34] == bytes.fromhex("008092000001")


class FakeIx500:
    """Control and scan channel of an iX500 as the write-up describes them; one sheet in the feeder."""

    def __init__(self, register_codes: list[int], sheets: int = 1):
        self.register_codes = register_codes
        self.sheets = sheets
        self.commands: list[int] = []  # control channel
        self.cdbs: list[bytes] = []  # scan channel
        self.keys: list[str] = []
        self._fed = 0
        self._sense = sense()
        self.page = jpeg(60, 80, (200, 30, 30))
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(("127.0.0.1", 0))
        self.ctl = socket.create_server(("127.0.0.1", 0))
        self.data = socket.create_server(("127.0.0.1", 0))
        for target, sock in ((self._udp, self.udp), (self._control, self.ctl), (self._scan, self.data)):
            threading.Thread(target=target, args=(sock,), daemon=True).start()

    def device(self) -> Ix500Device:
        return Ix500Device(
            "127.0.0.1",
            IDENT,
            data_port=self.data.getsockname()[1],
            control_port=self.ctl.getsockname()[1],
            discovery_port=self.udp.getsockname()[1],
            udp_wait=0.2,
            settle=0.0,
        )

    def close(self) -> None:
        for s in (self.udp, self.ctl, self.data):
            s.close()

    def _udp(self, s: socket.socket) -> None:
        while True:
            try:
                data, addr = s.recvfrom(2048)
            except OSError:
                return
            if data[:4] == b"VENS":
                s.sendto(device_info("127.0.0.1", bytes(6), "iX500-A0TEST0000"), addr)

    def _accept(self, s: socket.socket) -> socket.socket | None:
        try:
            conn, _ = s.accept()
        except OSError:
            return None
        conn.sendall(wire.server_frame(magic=wire.VENS))
        return conn

    def _control(self, s: socket.socket) -> None:
        while conn := self._accept(s):
            with conn:
                frame = wire.read_frame(conn)
                assert frame[4:8] == b"VENS"
                command = struct.unpack_from(">I", frame, 8)[0]
                self.commands.append(command)
                if command == 0x11:
                    self.keys.append(key_from_register(frame) or "")
                    code = self.register_codes.pop(0) if self.register_codes else 0
                    conn.sendall(struct.pack(">I4si", 20, b"VENS", code) + bytes(8))
                else:
                    conn.sendall(wire.server_frame(b"iX500-A0TEST0000".ljust(96, b"\0"), magic=wire.VENS))

    def _scan(self, s: socket.socket) -> None:
        while conn := self._accept(s):
            threading.Thread(target=self._serve_scan, args=(conn,), daemon=True).start()

    def _serve_scan(self, conn: socket.socket) -> None:
        with conn:
            while True:
                try:
                    frame = wire.read_frame(conn)
                except (OSError, wire.ProtocolError):
                    return
                assert frame[4:8] == b"VENS" and frame[24:28] == bytes(4)
                cdb_len, in_len = struct.unpack_from(">II", frame, 32)
                cdb = frame[48 : 48 + cdb_len]
                self.cdbs.append(cdb)
                status, data = 0, bytes(in_len)
                if cdb[0] == 0x03:
                    data, self._sense = self._sense, sense()
                elif cdb[0] == 0xE0:
                    if self._fed == self.sheets:
                        status, self._sense = 2, sense(3, 0x80, 0x03)
                    self._fed += 1
                elif cdb[:3] == bytes([0x28, 0, 0]):
                    data, status, self._sense = bytes([cdb[10], 0]) + self.page, 2, sense(eom=True)
                elif cdb[:3] == bytes([0x28, 0, 0x80]):
                    data = struct.pack(">IIIIHH", 60, 80, 60, 80, 300, 300).ljust(0x20, b"\0")
                conn.sendall(wire.server_frame(bytes(24) + data, status, magic=wire.VENS))


def test_scan_session_against_fake() -> None:
    fake = FakeIx500(register_codes=[-4, 0])
    try:
        device = fake.device()
        sheets = list(device.scan(ScanSettings("gray", 300, duplex=True)))
    finally:
        fake.close()

    assert fake.keys == ["KEY123456789", "KEY123456789"]  # busy → D6 release → registered again
    assert fake.commands[:2] == [0x11, 0x11] and {0x13, 0x30} <= set(fake.commands)
    ops = [c[0] for c in fake.cdbs]
    assert ops[:3] == [0xD6, 0x12, 0xE7]  # release after "busy", then the init connection
    assert bytes.fromhex("d50000000808") in fake.cdbs
    reads = [c for c in fake.cdbs if c[:3] == bytes([0x28, 0, 0])]
    assert [(c[5], c[10]) for c in reads] == [(0x00, 0), (0x80, 1)]  # front page 0, back page 1
    d4 = fake.cdbs.index(bytes.fromhex("d40000005000"))
    assert ops[d4 + 1] == 0xC2
    assert ops[-1] == 0xD6
    assert len(sheets) == 1 and sheets[0].back is not None
    assert Image.open(io.BytesIO(sheets[0].front)).mode == "L"  # gray is converted on the host
    assert device.connected


def test_wrong_key_is_reported() -> None:
    fake = FakeIx500(register_codes=[-3])
    try:
        device = fake.device()
        try:
            list(device.scan(ScanSettings("color", 300)))
        except RegistrationError as e:
            assert e.code == -3 and "wrong pairing key" in str(e)
        else:
            raise AssertionError("expected RegistrationError")
    finally:
        fake.close()


def test_capture_key_from_scansnap_home() -> None:
    """Plays ScanSnap Home: probe the stand-in, then register with the key."""
    ports: list[int] = []
    for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM, socket.SOCK_STREAM):
        with socket.socket(socket.AF_INET, kind) as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    udp_port, ctl_port, data_port = ports
    result: list[str | None] = []
    cancel = threading.Event()
    t = threading.Thread(
        target=lambda: result.append(
            capture_key(
                "iX500-A0TEST0000",
                host_ip="127.0.0.1",
                timeout=10,
                cancel=cancel,
                discovery_port=udp_port,
                control_port=ctl_port,
                data_port=data_port,
            )
        )
    )
    t.start()
    try:
        reply = b""
        for _ in range(50):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
                u.settimeout(0.2)
                u.sendto(b"VENS" + bytes(28), ("127.0.0.1", udp_port))
                try:
                    reply = u.recv(2048)
                    break
                except (TimeoutError, ConnectionResetError):
                    continue
        info = parse_reply(reply)
        assert info is not None and info.name == "iX500-A0TEST0000"
        body = register_body(IDENT, "127.0.0.1", "KEY123456789", NOW)
        with socket.create_connection(("127.0.0.1", ctl_port), timeout=5) as c:
            wire.read_frame(c)
            c.sendall(wire.client_frame(0x11, IDENT.mac, body, magic=wire.VENS))
            c.recv(64)
    finally:
        t.join(10)
        cancel.set()
    assert result == ["KEY123456789"]


def test_capture_key_can_be_cancelled() -> None:
    ports: list[int] = []
    for kind in (socket.SOCK_DGRAM, socket.SOCK_STREAM, socket.SOCK_STREAM):
        with socket.socket(socket.AF_INET, kind) as s:
            s.bind(("127.0.0.1", 0))
            ports.append(s.getsockname()[1])
    cancel = threading.Event()
    cancel.set()
    key = capture_key(
        "iX500-A0TEST0000",
        host_ip="127.0.0.1",
        cancel=cancel,
        discovery_port=ports[0],
        control_port=ports[1],
        data_port=ports[2],
    )
    assert key is None
