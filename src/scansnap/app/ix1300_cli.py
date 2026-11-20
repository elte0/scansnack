"""Run the iX1300 Return Scan receiver from the ScanSnack CLI."""

import argparse
import logging
from contextlib import contextmanager
from ipaddress import IPv4Address
from pathlib import Path

from ..adapters.ix1300.receiver import Receiver
from ..adapters.ix1300.session import open_session
from ..adapters.scansnap_wifi.identity import interface_mac
from .ix1300_output import receive_to_folder


def main(argv: list[str]) -> None:
    """Listen until stopped so a service supervisor can manage the receiver."""
    parser = argparse.ArgumentParser(prog="scansnap ix1300")
    parser.add_argument("--ip", type=IPv4Address, required=True, help="IPv4 address of the enrolled scanner")
    subcommands = parser.add_subparsers(dest="command", required=True)
    listen = subcommands.add_parser("listen", help="receive front-slot PDFs")
    listen.add_argument("--out", type=Path, required=True, help="existing folder for received PDFs")
    listen.add_argument("--top-feeder", action="store_true", help="receive duplex stacks in physical page order")
    args = parser.parse_args(argv)
    ip = str(args.ip)
    folder = args.out
    if not folder.is_dir():
        parser.error("--out must be an existing directory")
    if (mac := interface_mac(ip)) is None:
        parser.error("Cannot determine scanner-facing interface MAC")

    @contextmanager
    def session(timeout: float):
        yield open_session(ip, mac, timeout)

    logging.basicConfig(level=logging.INFO)
    receive_to_folder(Receiver(), ip, mac, session, folder, top_feeder=args.top_feeder)
