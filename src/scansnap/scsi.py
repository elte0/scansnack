"""Scan-Kanal (TCP 53218): SCSI-Kommandos, getunnelt über eine dauerhafte Verbindung.

Request (command = 1, Zusatzfeld = Host-IP), Body:
    0  u32  CDB-Länge (6/10/12)
    4  u32  erwartete Datenlänge vom Scanner
    8  u32  Länge der Daten zum Scanner (fehlt sie, lehnt der Scanner E9/D4 mit 5/26/00 ab)
   12  4 B  0
   16  16 B CDB (mit Nullen aufgefüllt)
   32  …    Daten zum Scanner (optional)

Response: Server-Header (Status in Byte 12–15), 24 B Null, ab Byte 40 die Daten.
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import threading
from dataclasses import dataclass

from . import wire

DATA_PORT = 53218
CHECK_CONDITION = 2


class ScsiError(Exception):
    def __init__(self, msg: str, sense: Sense | None = None):
        super().__init__(msg if sense is None else f"{msg}: {sense}")
        self.sense = sense


# Sense 3/80/xx (Medium Error) der Fujitsu/PFU-Scanner. Quelle: SANE-Treiber fujitsu.c (nicht nachgeschlagen,
# aus dem Gedächtnis); live bestätigt: 01 (Stau/zerknülltes Blatt), 03 (Einzug leer), 33 (D5 ohne 0x0610).
MEDIUM_ERRORS = {
    0x01: "paper jam",
    0x02: "cover open",
    0x03: "no paper",
    0x04: "unusual paper",
    0x07: "double feed",
    0x08: "ADF setup error",
    0x09: "carrier sheet",
    0x20: "stop button pressed",
    0x22: "scanning halted",
    0x30: "not enough paper",
    0x31: "scanning disabled",
    0x32: "scanning paused",
    0x33: "WiFi control error",
}


@dataclass(frozen=True)
class Sense:
    key: int
    asc: int
    ascq: int
    eom: bool
    ili: bool
    info: int          # bei kurzen Reads: Restlänge (angefordert − geliefert)

    @classmethod
    def parse(cls, data: bytes) -> Sense:
        if len(data) < 14:
            return cls(0, 0, 0, False, False, 0)
        return cls(
            key=data[2] & 0x0F,
            asc=data[12],
            ascq=data[13],
            eom=bool(data[2] & 0x40),
            ili=bool(data[2] & 0x20),
            info=struct.unpack_from(">I", data, 3)[0],
        )

    @property
    def no_paper(self) -> bool:
        return (self.key, self.asc, self.ascq) == (3, 0x80, 0x03)

    @property
    def medium_error(self) -> str | None:
        """Einzugs-/Papierfehler (Sense 3/80/xx) als Klartext, sonst None."""
        if self.key == 3 and self.asc == 0x80:
            return MEDIUM_ERRORS.get(self.ascq, f"medium error {self.ascq:#04x}")
        return None

    def __str__(self) -> str:
        return f"sense key={self.key:#x} asc={self.asc:#04x} ascq={self.ascq:#04x} eom={self.eom}"


@dataclass(frozen=True)
class Response:
    status: int
    data: bytes


class ScsiChannel:
    def __init__(self, ip: str, mac: bytes, port: int = DATA_PORT, timeout: float = 60.0):
        self.mac = mac
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.host_ip = ipaddress.IPv4Address(self.sock.getsockname()[0]).packed
        greeting = wire.read_frame(self.sock)
        if len(greeting) != wire.SERVER_HEADER_LEN:
            raise wire.ProtocolError("unexpected greeting")
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None

    def open_session(self) -> None:
        """Erste Kommandos von ScanSnap Home auf einer neuen Verbindung."""
        self.inquiry()
        self.checked(bytes.fromhex("1201f0009000"), in_len=0x90)
        self.checked(bytes.fromhex("e70001000000000c0000"), in_len=0x0C)
        self.hardware_status()

    def start_polling(self, interval: float = 0.25) -> None:
        """C2-Abfrage im Hintergrund, wie ScanSnap Home sie dauerhaft betreibt. Bricht beim ersten Fehler ab."""
        self._stop.clear()

        def run() -> None:
            while not self._stop.wait(interval):
                try:
                    self.hardware_status()
                except Exception:
                    return

        self._poller = threading.Thread(target=run, daemon=True)
        self._poller.start()

    @property
    def polling(self) -> bool:
        """C2-Polling läuft noch (also hat der Scanner zuletzt geantwortet)."""
        return self._poller is not None and self._poller.is_alive()

    def stop_polling(self, timeout: float = 3.0) -> bool:
        """Polling anhalten. False, wenn es in einer Abfrage hängt (Scanner antwortet nicht mehr)."""
        self._stop.set()
        if self._poller:
            self._poller.join(timeout)
            if self._poller.is_alive():
                return False
            self._poller = None
        return True

    def close(self) -> None:
        self._stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)      # weckt eine hängende C2-Abfrage auf
        except OSError:
            pass
        self.stop_polling()
        self.sock.close()

    def __enter__(self) -> ScsiChannel:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        if len(cdb) not in (6, 10, 12):
            raise ValueError("CDB must be 6, 10 or 12 bytes")
        body = struct.pack(">III", len(cdb), in_len, len(out)) + b"\0" * 4 + cdb.ljust(16, b"\0") + out
        frame = wire.client_frame(1, self.mac, body, self.host_ip)
        with self._lock:
            wire.trace(f"-> scsi {cdb.hex()}", frame)
            self.sock.sendall(frame)
            frame = wire.read_frame(self.sock)
        return Response(wire.frame_status(frame), frame[40:])

    def request_sense(self) -> Sense:
        return Sense.parse(self.command(bytes([0x03, 0, 0, 0, 0x12, 0]), in_len=0x12).data)

    def checked(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        r = self.command(cdb, out, in_len)
        if r.status == CHECK_CONDITION:
            raise ScsiError(f"command {cdb[0]:#04x} failed", self.request_sense())
        if r.status:
            raise ScsiError(f"command {cdb[0]:#04x} status {r.status}")
        return r

    def hardware_status(self) -> bytes:
        """C2-Status, wie ScanSnap Home ihn alle 250 ms abfragt. Byte 32–39: ausgewählter Host."""
        return self.checked(bytes.fromhex("c2000000000000003000"), in_len=0x30).data

    def inquiry(self) -> tuple[str, str]:
        d = self.checked(bytes([0x12, 0, 0, 0, 0x60, 0]), in_len=0x60).data
        return d[8:16].decode().strip(), d[16:32].decode().strip()
