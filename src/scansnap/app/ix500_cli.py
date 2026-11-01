"""``scansnap ix500 …``: discovery, device info, single scans and button scans for the iX500 (wires ix500)."""

from __future__ import annotations

import argparse
import datetime as dt
import queue
import sys
from collections.abc import Iterable
from pathlib import Path

from ..adapters.ix500 import wire
from ..adapters.ix500.control import RegistrationError
from ..adapters.ix500.device import Device
from ..adapters.ix500.discovery import discover
from ..adapters.ix500.identity import DEFAULT_PATH, local_mac
from ..adapters.ix500.info import read_info
from ..adapters.ix500.scanner import DPIS, PAGE_SIZES
from ..adapters.ix500.scsi import ScsiChannel
from ..adapters.scansnap_wifi import wire as shared_wire
from ..core.errors import NoPaper
from ..core.model import ScanSettings, Sheet


def _resolve_ip(args: argparse.Namespace) -> str:
    if args.ip:
        return args.ip
    found = discover(local_mac())
    if not found:
        sys.exit("No iX500 found. Specify it with --ip.")
    return found[0].ip


def _save(sheets: Iterable[Sheet], out: Path) -> int:
    n = 0
    for n, sheet in enumerate(sheets, 1):
        out.mkdir(parents=True, exist_ok=True)
        (out / f"page{n:03d}_front.jpg").write_bytes(sheet.front)
        if sheet.back is not None:
            (out / f"page{n:03d}_back.jpg").write_bytes(sheet.back)
        print(f"Sheet {n}: {sheet.info}", flush=True)
    return n


def _scan_dir(base: Path) -> Path:
    return base / dt.datetime.now().strftime("%Y-%m-%d_%H.%M.%S")


def main(argv: list[str]) -> None:
    p = argparse.ArgumentParser(prog="scansnap ix500")
    p.add_argument("--ip", help="IP of the scanner (otherwise discovery)")
    p.add_argument("--identity", type=Path, default=DEFAULT_PATH)
    p.add_argument("--password", help="scanner Wi-Fi password (default: last four digits of the serial)")
    p.add_argument("--debug", action="store_true", help="all frames as hex on stderr")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    inf = sub.add_parser("info", help="firmware, serial, page and roller counters")
    inf.add_argument(
        "--register", action="store_true", help="register first (takes the Scan button from ScanSnap Home)"
    )
    sc = sub.add_parser("scan", help="scan the sheets in the feeder")
    ls = sub.add_parser("listen", help="scan on every press of the scanner's Scan button")
    for q in (sc, ls):
        q.add_argument("--mode", choices=["gray", "color"], default="color")
        q.add_argument("--dpi", type=int, choices=DPIS, default=300)
        q.add_argument("--page", choices=sorted(PAGE_SIZES), default="auto")
        q.add_argument("--simplex", action="store_true", help="front sides only")
        q.add_argument("--out", type=Path, default=Path("scans"))
        q.add_argument("--no-crop", action="store_true", help="whole scan area instead of cropping to the paper")
    args = p.parse_args(argv)
    if args.debug:
        shared_wire.DEBUG = True

    if args.cmd == "discover":
        for s in discover(local_mac(), target=args.ip or "255.255.255.255"):
            print(f"{s.ip}\t{s.model}\t{s.name}\tpaired_host={s.paired_host_id} ({s.paired_ip})")
        return

    ip = _resolve_ip(args)
    if args.cmd == "info":
        mac = local_mac(ip)
        if args.register:
            device = Device(ip, args.identity, password=args.password)
            device.connect()
            assert device.ident is not None
            mac = device.ident.mac
            device.disconnect()
        try:
            with ScsiChannel(ip, mac, timeout=5.0) as ch:
                info = read_info(ch)
        except TimeoutError:
            sys.exit("Scan channel busy: another program (ScanSnack, ScanSnap Home) is connected to the scanner.")
        except wire.ProtocolError:
            sys.exit("The scanner only answers the host that registered last. Run again with info --register.")
        print(f"{info.vendor} {info.product}, firmware {info.firmware}, serial {info.serial}")
        print(f"Total pages: {info.total_pages}, roller counters: {info.roller_counts[0]}, {info.roller_counts[1]}")
        print(f"Auto power-off: {info.power_off_minutes} min")
        return

    settings = ScanSettings(args.mode, args.dpi, not args.simplex)
    presses: queue.Queue[None] = queue.Queue()
    device = Device(
        ip,
        args.identity,
        password=args.password,
        page=args.page,
        crop=not args.no_crop,
        on_button=lambda: presses.put(None),
    )
    try:
        device.connect()
        if args.cmd == "scan":
            out = _scan_dir(args.out)
            print(f"{_save(device.scan(settings), out)} sheet(s) → {out}", file=sys.stderr)
            return
        print('Ready: press "Scan" on the scanner (Ctrl+C quits).', file=sys.stderr)
        while True:
            try:
                presses.get(timeout=30)
            except queue.Empty:
                try:
                    device.connect()  # re-register if a power-on notice was missed
                except (OSError, wire.ProtocolError, RegistrationError) as e:
                    print(f"Reconnecting failed: {e}", file=sys.stderr)
                continue
            out = _scan_dir(args.out)
            try:
                print(f"{_save(device.scan(settings), out)} sheet(s) → {out}", file=sys.stderr)
            except NoPaper:
                print("No paper in the feeder.", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 – keep waiting for the next button press
                print(f"Scan failed: {e}", file=sys.stderr)
    except NoPaper:
        sys.exit("No paper in the feeder.")
    except KeyboardInterrupt:
        pass
    finally:
        device.disconnect()
