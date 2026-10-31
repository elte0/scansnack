"""``ports.diagnostics.Diagnostics``: what an unknown ScanSnap answers to the iX1500 protocol.

Only questions that do not change the scanner: the UDP discovery probes, a TCP port check, the device name
(0x13) and user list (0x0100) on the control channel, and INQUIRY, VPD f0, E7, C2 and REQUEST SENSE on the
scan channel – the same requests the app sends on every connect. No pairing (0x11), no announcement
(0x0210), no profiles, no scan. Every answer is recorded raw, because another model may frame it
differently. Protocol layout: docs/protocol.md.
"""

from __future__ import annotations

import ipaddress
import json
import platform
import secrets
import socket
import struct
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ...core.diagnostics import DiagnosticReport, Exchange, Redaction, text_redaction
from .control import CONTROL_PORT, SUB_GET_USERS, json_body, parse_json_response
from .discovery import DISCOVERY_PORT, PROBES, build_probe, local_ip_for, parse_reply
from .scsi import DATA_PORT
from .wire import MAGIC, SERVER_HEADER_LEN, client_frame

# Known ports of the iX1500 and their neighbourhood, plus common printer/scanner ports.
TCP_PORTS = (80, 443, 515, 631, 8080, 9100, *range(52200, 52240), *range(53200, 53240))

SCSI_QUERIES = (
    ("INQUIRY", "120000006000", 0x60),
    ("INQUIRY VPD f0", "1201f0009000", 0x90),
    ("E7", "e70001000000000c0000", 0x0C),
    ("hardware status C2", "c2000000000000003000", 0x30),
    ("REQUEST SENSE", "030000001200", 0x12),
)

# Placeholders from documentation ranges (RFC 5737, RFC 7042)
FAKE_SCANNER_IP = bytes([192, 0, 2, 1])
FAKE_HOST_IP = bytes([192, 0, 2, 2])
FAKE_SCANNER_MAC = bytes.fromhex("00005e005301")


def _random_mac() -> bytes:
    """Locally administered: the diagnosis never sends the computer's real MAC address."""
    b = bytearray(secrets.token_bytes(6))
    b[0] = (b[0] | 0x02) & 0xFE
    return bytes(b)


def _receive(sock: socket.socket, timeout: float, limit: int = 1 << 16) -> bytes:
    """Everything that arrives within ``timeout``; stops early once a complete ``ssNR`` frame is in."""
    buf = bytearray()
    deadline = time.monotonic() + timeout
    while len(buf) < limit and (remaining := deadline - time.monotonic()) > 0:
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(limit - len(buf))
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        if len(buf) >= 8 and bytes(buf[4:8]) == MAGIC and len(buf) >= struct.unpack_from(">I", buf)[0]:
            break
    return bytes(buf)


def _shape(value: Any) -> Any:
    """Structure of a JSON value without its content (user names, addresses and IDs stay private)."""
    if isinstance(value, dict):
        return {str(k): _shape(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, list):
        return [_shape(value[0])] if value else []  # pyright: ignore[reportUnknownArgumentType]
    return type(value).__name__


def _ip_redactions(label: str, ip: str, fake: bytes) -> list[Redaction]:
    out = [text_redaction(label, ip)]
    try:
        out.append(Redaction(label, ipaddress.IPv4Address(ip).packed, fake))
    except ValueError:
        pass
    return out


def _meaningful(value: bytes) -> bool:
    """Skip values that would also match unrelated bytes (empty, all zero, all 0xff)."""
    return bool(value.strip(b"\0")) and bool(value.strip(b"\xff"))


class Ix1500Diagnostics:
    def __init__(
        self,
        app_version: str,
        *,
        udp_port: int = DISCOVERY_PORT,
        control_port: int = CONTROL_PORT,
        data_port: int = DATA_PORT,
        tcp_ports: Iterable[int] = TCP_PORTS,
        broadcast: bool = True,
        timeout: float = 2.0,
    ):
        self.app_version = app_version
        self.udp_port, self.control_port, self.data_port = udp_port, control_port, data_port
        self.tcp_ports = sorted({*tcp_ports, control_port, data_port})
        self.broadcast, self.timeout = broadcast, timeout
        self._mac = b""
        self._exchanges: list[Exchange] = []
        self._summary: dict[str, str] = {}
        self._redactions: list[Redaction] = []
        self._open: list[int] = []

    def run(self, ip: str, progress: Callable[[str], None] | None = None) -> DiagnosticReport:
        say: Callable[[str], None] = progress or (lambda _text: None)
        self._mac = _random_mac()
        self._exchanges, self._summary, self._open = [], {}, []
        self._redactions = _ip_redactions("scanner IP", ip, FAKE_SCANNER_IP)
        sections: list[tuple[str, Callable[[str], None]]] = [
            ("Sending discovery probes …", self._discovery),
            ("Checking network ports …", self._ports),
            ("Asking the control channel …", self._control),
            ("Asking the scan channel …", self._scan_channel),
        ]
        for text, section in sections:
            say(text)
            try:
                section(ip)
            except Exception as e:  # noqa: BLE001 – every failure belongs into the report
                self._add(section.__name__.strip("_"), outcome=f"error: {type(e).__name__}: {e}")
        report = DiagnosticReport(self.app_version, platform.platform(), self._summary, self._exchanges)
        return report.anonymized(self._redactions)

    def _add(self, step: str, sent: bytes = b"", received: bytes = b"", outcome: str = "") -> None:
        self._exchanges.append(Exchange(step, sent, received, outcome))

    # --- UDP discovery ------------------------------------------------------------------
    def _discovery(self, ip: str) -> None:
        host_ip = local_ip_for(ip)
        self._redactions += _ip_redactions("host IP", host_ip, FAKE_HOST_IP)
        targets = [(ip, "unicast")] + ([("255.255.255.255", "broadcast")] if self.broadcast else [])
        replies: list[bytes] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("", 0))
            port = s.getsockname()[1]
            for target, kind in targets:
                for tag, flag in PROBES:
                    step = f"udp {self.udp_port} {kind} probe {tag.decode()} flag {flag:#06x}"
                    probe = build_probe(tag, flag, host_ip, self._mac, port)
                    try:
                        s.sendto(probe, (target, self.udp_port))
                    except OSError as e:
                        self._add(step, probe, outcome=f"error: {e}")
                        continue
                    got = self._collect(s, ip)
                    if not got:
                        self._add(step, probe, outcome="no reply")
                    for n, (data, sport) in enumerate(got):
                        label = step if n == 0 else f"{step} (reply {n + 1})"
                        self._add(label, probe if n == 0 else b"", data, f"{len(data)} bytes from port {sport}")
                    replies += [data for data, _ in got]
        info = next((i for i in map(parse_reply, replies) if i), None)
        if info:
            self._summary["discovery"] = "answered in the iX1500 format"
            self._summary["model"] = info.model
            for label, value in (("serial", info.serial), ("device name", info.name)):
                if len(value) >= 4:
                    self._redactions.append(text_redaction(label, value))
            mac, host_id = bytes.fromhex(info.mac.replace(":", "")), bytes.fromhex(info.paired_host_id)
            if _meaningful(mac):
                self._redactions.append(Redaction("scanner MAC", mac, FAKE_SCANNER_MAC))
            if _meaningful(host_id):
                self._redactions.append(Redaction("paired host ID", host_id, bytes(range(1, 9))))
        else:
            self._summary["discovery"] = "answered in an unknown format" if replies else "no reply"

    def _collect(self, s: socket.socket, ip: str, window: float = 1.0) -> list[tuple[bytes, int]]:
        """Replies from the scanner within ``window``; other devices' replies are ignored (their data is private)."""
        got: list[tuple[bytes, int]] = []
        deadline = time.monotonic() + window
        while (remaining := deadline - time.monotonic()) > 0:
            s.settimeout(remaining)
            try:
                data, (addr, sport) = s.recvfrom(4096)
            except TimeoutError:
                break
            if addr == ip:
                got.append((data, sport))
        return got

    # --- TCP ports ----------------------------------------------------------------------
    def _probe_port(self, ip: str, port: int) -> tuple[int, str, bytes]:
        try:
            with socket.create_connection((ip, port), timeout=1.0) as s:
                return port, "open", _receive(s, 1.0, limit=4096)
        except ConnectionRefusedError:
            return port, "closed", b""
        except OSError:
            return port, "no answer", b""

    def _ports(self, ip: str) -> None:
        with ThreadPoolExecutor(16) as pool:
            results = list(pool.map(self._probe_port, [ip] * len(self.tcp_ports), self.tcp_ports))
        self._open = [p for p, state, _ in results if state == "open"]
        for port, state, greeting in results:
            if state == "open":
                self._add(f"tcp {port} connect", received=greeting, outcome=f"open, {len(greeting)} bytes greeting")
        closed = sum(state == "closed" for _, state, _ in results)
        silent = len(results) - closed - len(self._open)
        self._add(
            f"tcp port check ({len(results)} ports)",
            outcome=f"open: {self._open or 'none'}, closed: {closed}, no answer: {silent}",
        )
        self._summary["open_tcp_ports"] = ", ".join(map(str, self._open)) or "none"

    # --- control channel ----------------------------------------------------------------
    def _control_call(self, ip: str, command: int, body: bytes = b"") -> tuple[bytes, bytes, bytes]:
        frame = client_frame(command, self._mac, body)
        with socket.create_connection((ip, self.control_port), timeout=self.timeout) as s:
            greeting = _receive(s, self.timeout)
            s.sendall(frame)
            return frame, greeting, _receive(s, self.timeout)

    def _control(self, ip: str) -> None:
        if self.control_port not in self._open:
            self._summary["control_channel"] = f"port {self.control_port} not open"
            return
        step = f"tcp {self.control_port} device name (0x13)"
        frame, greeting, reply = self._control_call(ip, 0x13)
        self._add(step, frame, reply, f"greeting {greeting.hex() or 'none'}, {len(reply)} bytes")
        if len(reply) > SERVER_HEADER_LEN and reply[4:8] == MAGIC:
            name = reply[16:80].split(b"\0", 1)[0].decode("ascii", "replace")
            if len(name) >= 4:
                self._redactions.append(text_redaction("device name", name))

        step = f"tcp {self.control_port} users and profiles (0x41/0x0100)"
        frame, greeting, reply = self._control_call(ip, 0x41, json_body(SUB_GET_USERS, None))
        try:
            _sub, users = parse_json_response(reply)
            outcome = f"{len(reply)} bytes, JSON structure: {json.dumps(_shape(users))}"
        except Exception as e:  # noqa: BLE001 – an unknown format is a finding, not a failure
            outcome = f"{len(reply)} bytes, not the iX1500 JSON format ({type(e).__name__})"
        # Only the header: the JSON body holds user names, IP addresses and IDs
        self._add(step, frame, reply[:36], outcome)
        self._summary["control_channel"] = "answered" if reply else "no reply"

    # --- scan channel -------------------------------------------------------------------
    def _scan_channel(self, ip: str) -> None:
        if self.data_port not in self._open:
            self._summary["scan_channel"] = f"port {self.data_port} not open"
            return
        with socket.create_connection((ip, self.data_port), timeout=self.timeout) as s:
            host_ip = ipaddress.IPv4Address(s.getsockname()[0]).packed
            greeting = _receive(s, self.timeout)
            self._add(f"tcp {self.data_port} greeting", received=greeting, outcome=f"{len(greeting)} bytes")
            for name, cdb_hex, in_len in SCSI_QUERIES:
                cdb = bytes.fromhex(cdb_hex)
                body = struct.pack(">III", len(cdb), in_len, 0) + b"\0" * 4 + cdb.ljust(16, b"\0")
                frame = client_frame(1, self._mac, body, host_ip)
                s.sendall(frame)
                reply = _receive(s, self.timeout)
                status = struct.unpack_from(">I", reply, 12)[0] if len(reply) >= 16 else None
                self._add(
                    f"tcp {self.data_port} {name}",
                    frame,
                    reply,
                    f"{len(reply)} bytes, status {status}" if reply else "no reply",
                )
                if not reply:
                    self._summary["scan_channel"] = f"no reply to {name}"
                    return
                if name == "INQUIRY" and len(reply) >= 40 + 36:
                    data = reply[40:]
                    vendor, product, rev = (
                        data[a:b].decode("ascii", "replace").strip() for a, b in ((8, 16), (16, 32), (32, 36))
                    )
                    self._summary["inquiry"] = f"{vendor} {product} rev {rev}"
        self._summary["scan_channel"] = "answered"
