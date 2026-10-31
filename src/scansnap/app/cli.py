"""Command line ``scansnap``: protocol experiments, single scans, eSCL server (wires ix1500 + escl)."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Iterable
from pathlib import Path

from ..adapters.ix1500 import wire
from ..adapters.ix1500.button import Notification
from ..adapters.ix1500.control import Control, HostIdentity
from ..adapters.ix1500.device import Device, session
from ..adapters.ix1500.discovery import discover
from ..adapters.ix1500.identity import DEFAULT_PATH, load_or_create
from ..adapters.ix1500.scanner import is_verified
from ..adapters.ix1500.scsi import ScsiChannel
from ..core.errors import NoPaper
from ..core.model import ScanSettings, Sheet


def _resolve_ip(args: argparse.Namespace, ident: HostIdentity) -> str:
    if args.ip:
        return args.ip
    found = discover(ident.mac)
    if not found:
        sys.exit("No scanner found. Specify it with --ip.")
    return found[0].ip


def _serve(args: argparse.Namespace, ip: str, ident: HostIdentity) -> None:
    import contextlib
    import uuid

    from ..adapters.escl.bonjour import Advertisement
    from ..adapters.escl.server import EsclService, make_server
    from ..adapters.ix1500.discovery import local_ip_for

    info = next(iter(discover(ident.mac, target=ip, timeout=2.0)), None)
    model = info.model if info else "ScanSnap iX1500"
    serial = info.serial if info else ""
    name = args.name or model
    # Stable across restarts so that macOS recognizes the scanner again
    escl_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"scansnap-bridge:{serial or ip}:{ident.host_id}"))
    my_ip = local_ip_for(ip)
    device = Device(ip, ident, args.profile_id, crop=not args.no_crop)
    service = EsclService(device, make_and_model=model, serial=serial, uuid=escl_uuid)
    server = make_server(service, port=args.port)
    print(f"eSCL: http://{my_ip}:{args.port}/eSCL/ScannerCapabilities ({name}, Scanner {ip})", file=sys.stderr)
    with contextlib.ExitStack() as stack:
        if not args.no_bonjour:
            stack.enter_context(Advertisement(name, my_ip, args.port, escl_uuid))
            print(f"Bonjour: {name} (_uscan._tcp)", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


def _save(pages: Iterable[Sheet], out: Path) -> int:
    n = 0
    for n, page in enumerate(pages, 1):
        out.mkdir(parents=True, exist_ok=True)
        (out / f"page{n:03d}_front.jpg").write_bytes(page.front)
        if page.back is not None:
            (out / f"page{n:03d}_back.jpg").write_bytes(page.back)
        print(f"Sheet {n}: {page.info}", flush=True)
    return n


def _listen(args: argparse.Namespace, ip: str, ident: HostIdentity) -> None:
    """Keep the session open and scan on every press of "Scan" on the touch panel."""
    import datetime as dt
    import queue

    presses: queue.Queue[Notification] = queue.Queue()
    device = Device(ip, ident, args.profile_id, crop=not args.no_crop, keep_alive=True, on_button=presses.put)
    device.connect()
    settings = ScanSettings(args.mode, args.dpi, args.duplex)
    print('Ready – press "Scan" on the scanner (Ctrl+C quits).', file=sys.stderr)
    try:
        while True:
            try:
                presses.get(timeout=30)
            except queue.Empty:
                device.connect()  # reconnect after a drop, otherwise no notification arrives
                continue
            out = args.out / dt.datetime.now().strftime("%Y-%m-%d_%H.%M.%S")
            try:
                n = _save(device.scan(settings), out)
                print(f"{n} sheet(s) → {out}", file=sys.stderr)
            except NoPaper:
                print("No paper in the feeder.", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 – keep waiting for the next button press
                print(f"Scan failed: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        device.disconnect()


def _diagnose(args: argparse.Namespace) -> None:
    """Diagnostic report for a model that is not supported yet (read-only, anonymized)."""
    from importlib.metadata import PackageNotFoundError, version

    from ..adapters.ix1500.diagnostics import Ix1500Diagnostics
    from ..core.diagnostics import REPORT_EMAIL

    if not args.ip:
        sys.exit("Specify the scanner with --ip (discovery may not work with an unsupported model).")
    try:
        app_version = version("scansnap-bridge")
    except PackageNotFoundError:
        app_version = "unknown"
    report = Ix1500Diagnostics(app_version).run(args.ip, lambda text: print(text, file=sys.stderr))
    report = dataclasses.replace(report, model=args.model, notes=args.notes)
    out: Path = args.out or Path(report.file_name())
    out.write_text(report.to_json() + "\n", encoding="utf-8")
    for key, value in report.summary.items():
        print(f"{key}: {value}", file=sys.stderr)
    print(f"Report: {out} – please check it and send it to {REPORT_EMAIL}.", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="scansnap")
    p.add_argument("--ip", help="IP of the scanner (otherwise discovery)")
    p.add_argument("--identity", type=Path, default=DEFAULT_PATH)
    p.add_argument("--debug", action="store_true", help="all frames as hex on stderr")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    sub.add_parser("pair", help="handshake on the control channel like ScanSnap Home")
    sub.add_parser("status", help="show users/profiles and the selected host")
    sub.add_parser("claim", help="register this host with the scanner and select it")
    dg = sub.add_parser("diagnose", help="diagnostic report for a model that is not supported yet")
    dg.add_argument("--model", default="", help="model name as printed on the scanner, e.g. iX500")
    dg.add_argument("--notes", default="", help="what you tried and what happened")
    dg.add_argument("--out", type=Path, help="report file (default: scansnack-diagnostics-….json)")
    sv = sub.add_parser("serve", help="eSCL/AirScan server with Bonjour advertisement")
    sv.add_argument("--port", type=int, default=8090)
    sv.add_argument("--name", help="name in the scan dialog (default: model reported by the scanner)")
    sv.add_argument("--profile-id", help="profile for 0x0610 (default: own/same IP/first)")
    sv.add_argument("--no-bonjour", action="store_true")
    sv.add_argument("--no-crop", action="store_true", help="whole scan area instead of cropping to the paper")
    ls = sub.add_parser("listen", help='scan on every press of "Scan" on the touch panel')
    sc = sub.add_parser("scan")
    for q in (sc, ls):
        q.add_argument("--mode", choices=["gray", "color"], default="gray")
        q.add_argument("--dpi", type=int, default=150)
        q.add_argument("--duplex", action="store_true")
        q.add_argument("--out", type=Path, default=Path("scans"))
        q.add_argument("--profile-id", help="profile for 0x0610 (default: own/same IP/first)")
        q.add_argument("--no-crop", action="store_true", help="whole scan area instead of cropping to the paper")
    sc.add_argument("--no-pair", action="store_true", help="skip the control channel handshake")
    sc.add_argument("--pair", action="store_true", help="pairing handshake (0x11 flag 5) instead of connecting")
    sc.add_argument("--no-select", action="store_true", help="omit 0x0610 before the scan")
    sc.add_argument("--no-heartbeat", action="store_true", help="omit the UDP heartbeat to 52217")
    args = p.parse_args(argv)

    if args.debug:
        wire.DEBUG = True
    if args.cmd == "diagnose":
        return _diagnose(args)
    ident = load_or_create(args.identity)

    if args.cmd == "discover":
        for s in discover(ident.mac):
            print(f"{s.ip}\t{s.model}\t{s.name}\tserial={s.serial}\tpaired_host={s.paired_host_id}")
        return

    ip = _resolve_ip(args, ident)
    if args.cmd == "status":
        users = Control(ip, ident).get_users()
        print(f"Own host ID: {ident.host_id}")
        print("Users:", [(u["name"], u["host_id"]) for u in users["conn_user"]["users"]])
        print("Selected (JSON):", users["conn_user"].get("select"))
        print("Profiles:", [(p.get("prof_name"), p.get("user_name")) for p in users.get("profiles", [])])
        with ScsiChannel(ip, ident.mac) as ch:
            print("Selected (C2):", ch.hardware_status()[32:40].hex())
        return
    if args.cmd == "serve":
        return _serve(args, ip, ident)
    if args.cmd == "listen":
        return _listen(args, ip, ident)
    ctl = Control(ip, ident)
    if args.cmd == "claim":
        # Writing users/profiles (0x0110) only works in the pairing state (0x11 with flag 5).
        with session(ctl, ip, ident, pair=True) as ch:
            try:
                ctl.claim(ctl.local_ip())
            finally:
                print("Selected (C2):", ch.hardware_status()[32:40].hex())
        print(f"Host {ident.host_id} registered with its own profile.")
        return
    if args.cmd == "pair":
        host_ip = ctl.handshake()
        print(f"Handshake ok (host {host_ip}, ID {ident.host_id})")
        return

    settings = ScanSettings(args.mode, args.dpi, args.duplex)
    if not is_verified(settings):
        print(f"Warning: {args.mode}/{args.dpi} dpi is not backed by captures.", file=sys.stderr)
    pages = Device(ip, ident, args.profile_id, crop=not args.no_crop).scan(
        settings, select=not args.no_select, pair=args.pair, handshake=not args.no_pair, heartbeat=not args.no_heartbeat
    )
    try:
        _save(pages, args.out)
    except NoPaper:
        sys.exit("No paper in the feeder.")


if __name__ == "__main__":
    main()
