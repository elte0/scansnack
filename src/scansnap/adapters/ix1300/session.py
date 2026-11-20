"""Use the scanner's existing enrollment for Return Scan notifications."""

import logging
from collections.abc import Sequence
from functools import partial

from ..scansnap_wifi.discovery import build_probe, local_ip_for, parse_vens_reply, replies
from ..scansnap_wifi.scsi import ScsiChannel
from ..scansnap_wifi.vens_control import CONTROL_PORT, HostIdentity, exchange, register_body, registration_status
from ..scansnap_wifi.wire import VENS
from .scanner import ChannelOpener

_LOG = logging.getLogger(__name__)


def open_session(ip: str, mac: bytes, timeout: float) -> ChannelOpener:
    """Register at the current route using the ID reported by the owner-configured scanner."""

    def probes(host_ip: str, port: int) -> Sequence[bytes]:
        return [
            build_probe(magic, flags, host_ip, mac, port)
            for magic, flags in ((VENS, 0x0010), (b"ssNR", 0x0100), (b"V2ss", 0x1000))
        ]

    for data, sender in replies(probes, ip, timeout):
        if len(data) != 144 or sender != ip:
            continue
        info = parse_vens_reply(data, sender)
        if info and info.model == "ScanSnap iX1300":
            break
    else:
        raise TimeoutError("No iX1300 discovery response")

    host_ip = local_ip_for(ip)
    if info.paired_ip not in ("0.0.0.0", host_ip):
        raise OSError("The scanner is registered to another active receiver")
    host_id = bytes.fromhex(info.paired_host_id)
    if not any(host_id):
        raise OSError("The scanner reports no enrolled host ID")
    body = register_body(HostIdentity(host_id, mac), host_ip, info.if_version, None)
    status = registration_status(exchange(ip, CONTROL_PORT, mac, 0x11, body, timeout))
    if status:
        raise OSError(f"iX1300 registration rejected, status {status}")

    # Check the data channel before prompting for paper.
    channel = partial(ScsiChannel, ip, mac, magic=VENS)
    with channel(timeout=timeout) as status_channel:
        status_channel.hardware_status(48)
    _LOG.info("Listening for iX1300 front-slot scans")
    return lambda io_timeout: channel(timeout=io_timeout)
