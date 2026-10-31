"""ScanSnap iX500 (beta): registration with a pairing key, short sessions, the same SCSI tunnel as the iX1500.

Everything here follows the third-party iX500 write-up summarised in docs/protocol.md, section
"ScanSnap iX500". It has not been tested on a device by us yet, hence the many ``UNVERIFIED`` markers.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import ipaddress
import secrets
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable, Iterator

from ...core.errors import ScanError
from ...core.images import process
from ...core.model import ScanSettings, Sheet
from . import wire
from .control import CONTROL_PORT, HostIdentity
from .discovery import DISCOVERY_PORT, HOST_UDP_PORT, PROBES, build_probe, local_ip_for
from .scanner import Scanner, window_block
from .scsi import DATA_PORT, ScsiChannel

VENS = wire.VENS
STATUS_LEN = 0x20  # C2 reply length on the iX500
ANNOUNCE_PORT = 53220  # the scanner broadcasts its presence here
REGISTER_BODY_LEN = 96
KEY_LEN = 16  # field size; observed keys have 12 ASCII characters
REGISTER_ROUNDS = 4  # ScanSnap Home sends the three UDP probes four times
BUSY = -4

REGISTER_ERRORS = {
    -1: "bad registration packet",
    -2: "scanner serial number mismatch",
    -3: "wrong pairing key",
    -4: "scanner busy (another session is still open)",
    -5: "no serial number in the registration",
    -7: "scanner is paired with another computer",
}

# D4 block for color/300 dpi as ScanSnap Home sends it to an iX500 (third-party write-up).
# Differs from the iX1500 template in bytes 10, 13 and 40.
_MODE_COLOR_300 = bytes.fromhex(
    "00030101d000c1808080908080000000000000000000000000000000000000300010012c012c05810000000028d0000044dc04"
).ljust(80, b"\0")
READ_LEN = 0x300000


class RegistrationError(ScanError, ConnectionError):
    """The scanner refused the 0x11 registration; ``code`` is its signed status."""

    def __init__(self, code: int):
        super().__init__(f"iX500 registration refused: {REGISTER_ERRORS.get(code, 'error')} ({code})")
        self.code = code


def mode_block(settings: ScanSettings) -> bytes:
    """D4 data block (80 B). Always color; grayscale is converted on the host."""
    # UNVERIFIED: only color/300 comes from a (third-party) capture; other dpi values patch bytes 34–37 like
    # on the iX1500. A grayscale block for the iX500 is unknown.
    b = bytearray(_MODE_COLOR_300)
    struct.pack_into(">HH", b, 34, settings.dpi, settings.dpi)
    return bytes(b)


def register_body(identity: HostIdentity, host_ip: str, key: str, now: dt.datetime | None = None) -> bytes:
    """0x11 body (96 B): the iX1500 fields without the 20 bytes before the date, plus the pairing key."""
    raw_key = key.encode("ascii")
    if not raw_key or len(raw_key) > KEY_LEN:
        raise ValueError(f"pairing key must be 1–{KEY_LEN} ASCII characters")
    now = now or dt.datetime.now().astimezone()
    offset = now.utcoffset()
    bias = -int(offset.total_seconds()) if offset else 0
    body = bytearray(REGISTER_BODY_LEN)
    # UNVERIFIED: meaning of 00 06 1e 00 and of the 1 at offset 8; copied from the write-up.
    struct.pack_into(">4sII4sHH", body, 0, b"\x00\x06\x1e\x00", 0, 1, ipaddress.IPv4Address(host_ip).packed, 0, 55265)
    body[20 : 20 + len(raw_key)] = raw_key
    # UNVERIFIED: date, host ID and time zone bias by analogy with the iX1500; the write-up sends fixed values.
    struct.pack_into(
        ">HBBBBBB8si",
        body,
        48,
        now.year,
        now.month,
        now.day,
        now.hour,
        now.minute,
        now.second,
        0,
        identity.host_id_bytes,
        bias,
    )
    return bytes(body)


def register_status(frame: bytes) -> int:
    # UNVERIFIED: the write-up reads the signed status at offset 8 of the 20-byte reply.
    return struct.unpack_from(">i", frame, 8)[0]


def udp_register(
    ip: str, mac: bytes, host_ip: str, rounds: int = REGISTER_ROUNDS, wait: float = 3.0, port: int = DISCOVERY_PORT
) -> bool:
    """Sends the three discovery probes ``rounds`` times from port 55264, as ScanSnap Home does before
    connecting. True if the scanner answered."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("", HOST_UDP_PORT))
        except OSError:
            s.bind(("", 0))
        own_port = s.getsockname()[1]
        for _ in range(rounds):
            for tag, flag in PROBES:
                seq = 1 if tag == b"V2ss" else 0
                s.sendto(build_probe(tag, flag, host_ip, mac, own_port, seq), (ip, port))
        s.settimeout(wait)
        try:
            s.recvfrom(2048)
            return True
        except TimeoutError:
            return False


class Ix500Control:
    """Control channel (TCP 53219) of the iX500: one connection per command, ``VENS`` frames."""

    def __init__(self, ip: str, identity: HostIdentity, port: int = CONTROL_PORT, timeout: float = 5.0):
        self.ip, self.port, self.identity, self.timeout = ip, port, identity, timeout

    def call(self, command: int, body: bytes = b"") -> bytes:
        with socket.create_connection((self.ip, self.port), timeout=self.timeout) as s:
            wire.read_frame(s)  # greeting
            frame = wire.client_frame(command, self.identity.mac, body, magic=VENS)
            wire.trace(f"-> ctl {command:#04x}", frame)
            s.sendall(frame)
            return wire.read_frame(s)

    def register(self, host_ip: str, key: str) -> int:
        return register_status(self.call(0x11, register_body(self.identity, host_ip, key)))

    def device_name(self) -> str:
        return self.call(0x13)[16:80].split(b"\0", 1)[0].decode("ascii", "replace")

    def aux(self, command: int) -> None:
        """0x13/0x30 during the init connection, as ScanSnap Home sends them; the answers are not used."""
        # UNVERIFIED: whether 0x13 and 0x30 are needed at all.
        with contextlib.suppress(OSError, wire.ProtocolError):
            self.call(command)


def release(ip: str, mac: bytes, port: int = DATA_PORT) -> bool:
    """D6 on a fresh scan connection: frees a session the scanner still holds (status -4)."""
    try:
        with ScsiChannel(ip, mac, port, timeout=4.0, magic=VENS, status_len=STATUS_LEN, send_host_ip=False) as ch:
            ch.command(bytes.fromhex("d60000000000"))
        return True
    except (OSError, wire.ProtocolError):
        return False


class Ix500Scanner(Scanner):
    """Scan sequence of the iX500: as on the iX1500, with C2 queries in between, D5 without the profile flag,
    its own D4 block and a page counter in the READ command."""

    def open_job(self) -> None:
        self.ch.command(bytes([0xC2, 0, 0, 0, 0, 0, 0, STATUS_LEN, 0, 0]), in_len=STATUS_LEN)
        # UNVERIFIED: byte 3 = 0 (iX1500: 1); the iX500 has no profile selection before D5.
        self.ch.checked(bytes.fromhex("d50000000808"), out=b"\0" * 8, in_len=8)

    def setup(self, settings: ScanSettings) -> None:
        ch = self.ch
        ch.checked(bytes.fromhex("d80000000000"))
        ch.checked(bytes.fromhex("e9000000000000200000"), out=window_block(settings))
        ch.checked(bytes.fromhex("d40000005000"), out=mode_block(settings))
        ch.command(bytes([0xC2, 0, 0, 0, 0, 0, 0, STATUS_LEN, 0, 0]), in_len=STATUS_LEN)
        ch.request_sense()

    def read_len(self, settings: ScanSettings) -> int:
        return READ_LEN

    def read_cdb(self, sheet: int, side: int, chunk: int, length: int) -> bytes:
        # UNVERIFIED: byte 10 counts pages (front 0, back 1, next front 2 …) instead of naming the side;
        # byte 11 as chunk counter is taken from the iX1500 (the write-up always reads a whole page at once).
        page = (2 * sheet + side) & 0xFF
        return bytes([0x28, 0, 0, 2, 0, 0x80 if side else 0]) + length.to_bytes(3, "big") + bytes([0, page, chunk])

    def chunk_header(self, sheet: int, side: int, chunk: int) -> bytes | None:
        return None  # UNVERIFIED: content of the 2 bytes before the JPEG is not documented for the iX500

    def info_cdb(self, sheet: int, side: int) -> bytes:
        page = (2 * sheet + side) & 0xFF
        return bytes([0x28, 0, 0x80, 0, 0, 0x80 if side else 0, 0, 0, 0x20, 0, page, 0])


def _channel(ip: str, mac: bytes, port: int, timeout: float = 30.0) -> ScsiChannel:
    # UNVERIFIED: the write-up leaves the host IP field of the frame header at 0.
    return ScsiChannel(ip, mac, port, timeout, magic=VENS, status_len=STATUS_LEN, send_host_ip=False)


class Ix500Device:
    """``ports.scanner.ScannerPort`` for the iX500 (beta). Every scan is a session of its own:

    UDP probes → 0x11 with the pairing key (D6 + retry while busy) → init connection on 53218
    (INQUIRY, E7, C2, E6, E6, D5, D6, with 0x13/0x30 alongside) → UDP probes → scan connection → D6.
    There is no scan button support.
    """

    def __init__(
        self,
        ip: str,
        ident: HostIdentity,
        *,
        key: str | None = None,
        rotate: bool = False,
        crop: bool = True,
        data_port: int = DATA_PORT,
        control_port: int = CONTROL_PORT,
        discovery_port: int = DISCOVERY_PORT,
        register_rounds: int = REGISTER_ROUNDS,
        udp_wait: float = 3.0,
        retries: int = 8,
        settle: float = 1.0,
    ):
        # UNVERIFIED: whether iX500 pages arrive rotated like on the iX1500; the write-up does not rotate.
        self.ip, self.ident = ip, ident
        self.key = key if key is not None else ident.pairing_key
        self.rotate, self.crop = rotate, crop
        self.data_port, self.discovery_port, self.udp_wait = data_port, discovery_port, udp_wait
        self.register_rounds, self.retries, self.settle = register_rounds, retries, settle
        self.ctl = Ix500Control(ip, ident, control_port)
        self._ready = False

    @property
    def connected(self) -> bool:
        """The iX500 keeps no session between scans; True once the scanner was reachable."""
        return self._ready

    def connect(self) -> None:
        with socket.create_connection((self.ip, self.ctl.port), timeout=3.0):
            self._ready = True

    def disconnect(self) -> None:
        self._ready = False

    def _host_ip(self) -> str:
        return local_ip_for(self.ip)

    def register(self, host_ip: str) -> None:
        if not self.key:
            raise RegistrationError(-3)
        for attempt in range(self.retries):
            udp_register(self.ip, self.ident.mac, host_ip, self.register_rounds, self.udp_wait, self.discovery_port)
            code = self.ctl.register(host_ip, self.key)
            if code == 0:
                return
            if code != BUSY or attempt == self.retries - 1:
                raise RegistrationError(code)
            print(f"Scanner busy, releasing and retrying ({attempt + 1}/{self.retries})", file=sys.stderr)
            release(self.ip, self.ident.mac, self.data_port)
            time.sleep(self.settle)

    def _init(self) -> None:
        with _channel(self.ip, self.ident.mac, self.data_port) as ch:
            name = threading.Thread(target=self.ctl.aux, args=(0x13,), daemon=True)
            state = threading.Thread(target=self.ctl.aux, args=(0x30,), daemon=True)
            name.start()
            # UNVERIFIED: E6 (read and write 10 1e 00 00) is not used by ScanSnap Home on the iX1500.
            for i, (cdb, out, in_len) in enumerate(
                (
                    (bytes([0x12, 0, 0, 0, 0x60, 0]), b"", 0x60),
                    (bytes.fromhex("e70001000000000c0000"), b"", 0x0C),
                    (bytes([0xC2, 0, 0, 0, 0, 0, 0, STATUS_LEN, 0, 0]), b"", STATUS_LEN),
                    (bytes.fromhex("e600010000000004"), b"", 4),
                    (bytes.fromhex("e600000000040000"), bytes.fromhex("101e0000"), 0),
                    (bytes.fromhex("d50000000808"), b"\0" * 8, 8),
                    (bytes.fromhex("d60000000000"), b"", 0),
                )
            ):
                ch.command(cdb, out, in_len)
                if i == 0:
                    state.start()
            name.join(5)
            state.join(5)

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        """All sheets in the feeder. Grayscale is scanned in color and converted on the host."""
        host_ip = self._host_ip()
        try:
            self.register(host_ip)
            self._init()
            udp_register(self.ip, self.ident.mac, host_ip, 1, min(self.udp_wait, 0.5), self.discovery_port)
            gray = settings.mode == "gray"
            try:
                with _channel(self.ip, self.ident.mac, self.data_port) as ch:
                    for sheet in Ix500Scanner(ch).scan(ScanSettings("color", settings.dpi, settings.duplex)):

                        def fix(jpeg: bytes) -> bytes:
                            return process(
                                jpeg,
                                settings.dpi,
                                rotate=self.rotate,
                                crop=self.crop,
                                whiten_background=whiten,
                                gray=gray,
                            )

                        yield Sheet(fix(sheet.front), sheet.back and fix(sheet.back), sheet.info)
            finally:
                time.sleep(self.settle)
                release(self.ip, self.ident.mac, self.data_port)  # like ScanSnap Home: D6 on a new connection
            self._ready = True
        except (OSError, wire.ProtocolError):
            self._ready = False
            raise


# --- pairing key -------------------------------------------------------------------------------


def device_info(scanner_ip: str, mac: bytes, name: str) -> bytes:
    """132-byte reply to a discovery probe, as an iX500 sends it."""
    b = bytearray(132)
    b[0:4] = VENS
    # UNVERIFIED: bytes 4–15 and 34–39 copied from the write-up's template, meaning unknown.
    b[4:16] = bytes.fromhex("0000000000060030ffffffff")
    b[16:20] = ipaddress.IPv4Address(scanner_ip).packed
    struct.pack_into(">II", b, 20, DATA_PORT, CONTROL_PORT)
    b[28:34] = mac
    b[34:40] = bytes.fromhex("000000800001")
    raw = name.encode("ascii")[:64]
    b[40 : 40 + len(raw)] = raw
    b[104:120] = b"ScanSnap iX500  "
    return bytes(b)


def announcement(scanner_ip: str, mac: bytes) -> bytes:
    """48-byte broadcast on UDP 53220 with which an iX500 makes itself known."""
    b = bytearray(48)
    struct.pack_into(">I4sIIII", b, 0, 48, VENS, 0x21, 0, 1, 0)
    b[24:28] = ipaddress.IPv4Address(scanner_ip).packed
    b[28:34] = mac
    return bytes(b)


def key_from_register(frame: bytes) -> str | None:
    """Pairing key from a 128-byte 0x11 frame that ScanSnap Home sends to a scanner."""
    if len(frame) < 68 or frame[4:8] != VENS or struct.unpack_from(">I", frame, 8)[0] != 0x11:
        return None
    key = frame[52:68].rstrip(b"\0")
    return key.decode("ascii", "replace") if key else None


def capture_key(
    name: str,
    *,
    host_ip: str | None = None,
    timeout: float | None = None,
    cancel: threading.Event | None = None,
    log: Callable[[str], None] = lambda _msg: None,
    discovery_port: int = DISCOVERY_PORT,
    control_port: int = CONTROL_PORT,
    data_port: int = DATA_PORT,
) -> str | None:
    """Poses as an iX500 until ScanSnap Home (on another computer) registers and sends its pairing key.

    The real scanner must be switched off meanwhile. ``name`` is the device name ScanSnap Home knows,
    e.g. ``iX500-A0B1C2D3E4``. Needs the UDP ports 52217/53220 and TCP 53218/53219 of this computer
    (``OSError`` if one is in use). Returns None after ``timeout`` or when ``cancel`` is set.
    """
    my_ip = host_ip or local_ip_for("8.8.8.8")
    mac = bytes.fromhex("008092") + secrets.token_bytes(3)  # Silex OUI like the scanner's Wi-Fi module
    info, announce = device_info(my_ip, mac, name), announcement(my_ip, mac)
    stop = threading.Event()
    found: list[str] = []
    greeting = wire.server_frame(magic=VENS)

    def broadcaster() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not stop.is_set():
                with contextlib.suppress(OSError):
                    s.sendto(announce, ("255.255.255.255", ANNOUNCE_PORT))
                stop.wait(3)

    def responder(s: socket.socket) -> None:
        while not stop.is_set():
            try:
                _data, addr = s.recvfrom(2048)
                s.sendto(info, addr)
            except TimeoutError:
                continue
            except OSError:
                return

    def control(s: socket.socket) -> None:
        while not stop.is_set():
            try:
                conn, _addr = s.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(5)
                    conn.sendall(greeting)
                    frame = wire.read_frame(conn)
                    if key := key_from_register(frame):
                        conn.sendall(struct.pack(">I4sII", 20, VENS, 0, 0) + b"\0" * 4)
                        found.append(key)
                        stop.set()
                    elif struct.unpack_from(">I", frame, 8)[0] == 0x13:
                        conn.sendall(wire.server_frame(name.encode("ascii").ljust(96, b"\0"), magic=VENS))
                    else:
                        conn.sendall(wire.server_frame(b"\0" * 16, magic=VENS))
                except (OSError, wire.ProtocolError):
                    continue

    def data(s: socket.socket) -> None:
        while not stop.is_set():
            try:
                conn, _addr = s.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn, contextlib.suppress(OSError):
                conn.sendall(greeting)

    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp_ctl = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_data = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with udp, tcp_ctl, tcp_data:
        for s in (udp, tcp_ctl, tcp_data):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(0.5)
        udp.bind(("", discovery_port))
        tcp_ctl.bind(("", control_port))
        tcp_ctl.listen(5)
        tcp_data.bind(("", data_port))
        tcp_data.listen(5)
        threads = [
            threading.Thread(target=broadcaster, daemon=True),
            threading.Thread(target=responder, args=(udp,), daemon=True),
            threading.Thread(target=control, args=(tcp_ctl,), daemon=True),
            threading.Thread(target=data, args=(tcp_data,), daemon=True),
        ]
        for t in threads:
            t.start()
        log(f"Posing as {name} at {my_ip}; waiting for ScanSnap Home …")
        deadline = None if timeout is None else time.monotonic() + timeout
        try:
            while not stop.wait(0.2):
                if (cancel is not None and cancel.is_set()) or (deadline is not None and time.monotonic() > deadline):
                    break
        finally:
            stop.set()
            for t in threads:
                t.join(2)
    return found[0] if found else None
