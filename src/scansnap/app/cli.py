"""Kommandozeile ``scansnap``: Experimente am Protokoll, Einzelscans, eSCL-Server (verdrahtet ix1500 + escl)."""

from __future__ import annotations

import argparse
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
        sys.exit("Kein Scanner gefunden. Mit --ip angeben.")
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
    # Stabil über Neustarts, damit macOS den Scanner wiedererkennt
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
        print(f"Blatt {n}: {page.info}", flush=True)
    return n


def _listen(args: argparse.Namespace, ip: str, ident: HostIdentity) -> None:
    """Sitzung halten und bei jedem Druck auf „Scan“ am Touchpanel scannen."""
    import datetime as dt
    import queue

    presses: queue.Queue[Notification] = queue.Queue()
    device = Device(ip, ident, args.profile_id, crop=not args.no_crop, keep_alive=True, on_button=presses.put)
    device.connect()
    settings = ScanSettings(args.mode, args.dpi, args.duplex)
    print("Bereit – am Scanner auf „Scan“ drücken (Strg+C beendet).", file=sys.stderr)
    try:
        while True:
            try:
                presses.get(timeout=30)
            except queue.Empty:
                device.connect()  # Sitzung nach Abriss wieder aufbauen, sonst kommt keine Meldung
                continue
            out = args.out / dt.datetime.now().strftime("%Y-%m-%d_%H.%M.%S")
            try:
                n = _save(device.scan(settings), out)
                print(f"{n} Blatt/Blätter → {out}", file=sys.stderr)
            except NoPaper:
                print("Kein Papier im Einzug.", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 – weiter auf den nächsten Tastendruck warten
                print(f"Scan fehlgeschlagen: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        pass
    finally:
        device.disconnect()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="scansnap")
    p.add_argument("--ip", help="IP des Scanners (sonst Discovery)")
    p.add_argument("--identity", type=Path, default=DEFAULT_PATH)
    p.add_argument("--debug", action="store_true", help="alle Frames als Hex auf stderr")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover")
    sub.add_parser("pair", help="Handshake auf dem Steuerkanal wie ScanSnap Home")
    sub.add_parser("status", help="Benutzer/Profile und ausgewählten Host anzeigen")
    sub.add_parser("claim", help="diesen Host beim Scanner eintragen und auswählen")
    sv = sub.add_parser("serve", help="eSCL/AirScan-Server mit Bonjour-Anzeige")
    sv.add_argument("--port", type=int, default=8090)
    sv.add_argument("--name", help="Name im Scanfenster (Default: Modell vom Scanner)")
    sv.add_argument("--profile-id", help="Profil für 0x0610 (Default: eigenes/gleiche IP/erstes)")
    sv.add_argument("--no-bonjour", action="store_true")
    sv.add_argument("--no-crop", action="store_true", help="ganzes Scanfeld statt Zuschnitt aufs Papier")
    ls = sub.add_parser("listen", help="bei jedem Druck auf „Scan“ am Touchpanel scannen")
    sc = sub.add_parser("scan")
    for q in (sc, ls):
        q.add_argument("--mode", choices=["gray", "color"], default="gray")
        q.add_argument("--dpi", type=int, default=150)
        q.add_argument("--duplex", action="store_true")
        q.add_argument("--out", type=Path, default=Path("scans"))
        q.add_argument("--profile-id", help="Profil für 0x0610 (Default: eigenes/gleiche IP/erstes)")
        q.add_argument("--no-crop", action="store_true", help="ganzes Scanfeld statt Zuschnitt aufs Papier")
    sc.add_argument("--no-pair", action="store_true", help="Steuerkanal-Handshake überspringen")
    sc.add_argument("--pair", action="store_true", help="Pairing-Handshake (0x11 Flag 5) statt Verbinden")
    sc.add_argument("--no-select", action="store_true", help="0x0610 vor dem Scan weglassen")
    sc.add_argument("--no-heartbeat", action="store_true", help="UDP-Heartbeat an 52217 weglassen")
    args = p.parse_args(argv)

    if args.debug:
        wire.DEBUG = True
    ident = load_or_create(args.identity)

    if args.cmd == "discover":
        for s in discover(ident.mac):
            print(f"{s.ip}\t{s.model}\t{s.name}\tserial={s.serial}\tpaired_host={s.paired_host_id}")
        return

    ip = _resolve_ip(args, ident)
    if args.cmd == "status":
        users = Control(ip, ident).get_users()
        print(f"Eigene Host-ID: {ident.host_id}")
        print("Benutzer:", [(u["name"], u["host_id"]) for u in users["conn_user"]["users"]])
        print("Ausgewählt (JSON):", users["conn_user"].get("select"))
        print("Profile:", [(p.get("prof_name"), p.get("user_name")) for p in users.get("profiles", [])])
        with ScsiChannel(ip, ident.mac) as ch:
            print("Ausgewählt (C2):", ch.hardware_status()[32:40].hex())
        return
    if args.cmd == "serve":
        return _serve(args, ip, ident)
    if args.cmd == "listen":
        return _listen(args, ip, ident)
    ctl = Control(ip, ident)
    if args.cmd == "claim":
        # Benutzer/Profile schreiben (0x0110) geht nur im Pairing-Zustand (0x11 mit Flag 5).
        with session(ctl, ip, ident, pair=True) as ch:
            try:
                ctl.claim(ctl.local_ip())
            finally:
                print("Ausgewählt (C2):", ch.hardware_status()[32:40].hex())
        print(f"Host {ident.host_id} mit eigenem Profil eingetragen.")
        return
    if args.cmd == "pair":
        host_ip = ctl.handshake()
        print(f"Handshake ok (Host {host_ip}, ID {ident.host_id})")
        return

    settings = ScanSettings(args.mode, args.dpi, args.duplex)
    if not is_verified(settings):
        print(f"Warnung: {args.mode}/{args.dpi} dpi ist nicht durch Captures belegt.", file=sys.stderr)
    pages = Device(ip, ident, args.profile_id, crop=not args.no_crop).scan(
        settings, select=not args.no_select, pair=args.pair, handshake=not args.no_pair, heartbeat=not args.no_heartbeat
    )
    try:
        _save(pages, args.out)
    except NoPaper:
        sys.exit("Kein Papier im Einzug.")


if __name__ == "__main__":
    main()
