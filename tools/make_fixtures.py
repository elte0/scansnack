"""Erzeugt bereinigte Test-Fixtures aus Wireshark-Captures.

Übernommen wird ausschließlich ScanSnap-Protokollverkehr (Scanner-IP, Ports
52217/53218/53219). Capture-Metadaten, fremder Netzverkehr und die gescannten
Bildinhalte landen nicht in den Fixtures; von den Bildern bleiben nur Maße und
dpi, die Tests erzeugen daraus synthetische JPEGs.

    python tools/make_fixtures.py captures/ tests/fixtures/captures/ --scanner-ip 192.168.178.29

Mitschnitte nie von Hand „passend machen“, damit ein Test grün wird – neu erzeugen.

Benötigt tshark und Pillow.
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import struct
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image


def tcp_streams(pcap: Path, port: int, scanner_ip: str) -> dict[int, dict[str, bytes]]:
    out = subprocess.run(
        [
            "tshark",
            "-r",
            str(pcap),
            "-Y",
            f"ip.addr=={scanner_ip} && tcp.port=={port} && tcp.len>0",
            "-T",
            "fields",
            "-e",
            "tcp.stream",
            "-e",
            "ip.src",
            "-e",
            "tcp.seq",
            "-e",
            "tcp.payload",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    segs: dict[int, dict[str, dict[int, bytes]]] = collections.defaultdict(lambda: {"C": {}, "S": {}})
    for line in filter(None, out.split("\n")):
        st, src, seq, payload = line.split("\t")
        segs[int(st)]["S" if src == scanner_ip else "C"][int(seq)] = bytes.fromhex(payload)
    return {st: {d: b"".join(v[d][k] for k in sorted(v[d])) for d in "CS"} for st, v in segs.items()}


def frames(buf: bytes) -> list[bytes]:
    res: list[bytes] = []
    i = 0
    while i + 8 <= len(buf):
        n = struct.unpack_from(">I", buf, i)[0]
        assert buf[i + 4 : i + 8] == b"ssNR"
        res.append(buf[i : i + n])
        i += n
    return res


def pairs(stream: dict[str, bytes]) -> list[tuple[bytes, bytes]]:
    c, s = frames(stream["C"]), frames(stream["S"])
    if s and len(s[0]) == 16 and len(s) > len(c):
        s = s[1:]
    return list(zip(c, s, strict=False))  # Antworten können fehlen (0x12)


def scan_fixture(pcap: Path, scanner_ip: str) -> dict[str, Any]:
    exchanges: list[dict[str, Any]] = []
    images: dict[int, list[bytes]] = collections.defaultdict(list)
    c2: str | None = None
    for stream in tcp_streams(pcap, 53218, scanner_ip).values():
        for req, resp in pairs(stream):
            cdb_len, in_len = struct.unpack_from(">II", req, 32)
            cdb, out = req[48 : 48 + cdb_len], req[64:]
            status, data = struct.unpack_from(">I", resp, 12)[0], resp[40:]
            if cdb[0] == 0xC2:
                c2 = c2 or data.hex()
                continue
            if cdb[0] == 0x28 and cdb[2] == 0:  # Bilddaten: nur Maße behalten
                images[data[0]].append(data[2:])
                continue
            exchanges.append(
                {"cdb": cdb.hex(), "out": out.hex(), "in_len": in_len, "status": status, "data": data.hex()}
            )
    sides: list[dict[str, Any]] = []
    for side in sorted(images):
        im = Image.open(io.BytesIO(b"".join(images[side])))
        sides.append({"side": side, "width": im.width, "height": im.height, "dpi": im.info.get("dpi", (0, 0))[1]})
    return {"exchanges": exchanges, "c2_response": c2, "pages": [{"sides": sides}]}


def control_fixture(pcap: Path, scanner_ip: str) -> dict[str, Any]:
    calls: list[dict[str, Any]] = []
    for stream in tcp_streams(pcap, 53219, scanner_ip).values():
        for req, resp in pairs(stream):
            calls.append(
                {"command": struct.unpack_from(">I", req, 8)[0], "request_body": req[32:].hex(), "response": resp.hex()}
            )
    out = subprocess.run(
        [
            "tshark",
            "-r",
            str(pcap),
            "-Y",
            f"udp.port==52217 && ip.src=={scanner_ip} && udp.length>100",
            "-T",
            "fields",
            "-e",
            "udp.payload",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {"calls": calls, "discovery_reply": out.split("\n")[0]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("captures", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--scanner-ip", required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for pcap in sorted(a.captures.glob("*.pcap*")):
        fx = control_fixture(pcap, a.scanner_ip) if "pairing" in pcap.stem else scan_fixture(pcap, a.scanner_ip)
        (a.out / f"{pcap.stem.split('.')[0]}.json").write_text(json.dumps(fx, indent=1))
        print(pcap.name, "->", pcap.stem + ".json")


if __name__ == "__main__":
    main()
