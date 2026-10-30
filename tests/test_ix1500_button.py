"""Scan button on the touch panel: UDP notification to host_port (private live capture of 2026-10-29, unpublished)."""

import socket
import threading

from scansnap.adapters.ix1500.button import ButtonListener, Notification, parse_notification

# As in the capture (counter 0x20)
NOTIFY = bytes.fromhex("0000003073734e52000000010000000020000000c0a8b21d020000000002") + b"\0" * 18


def test_parse_notification() -> None:
    note = parse_notification(NOTIFY)
    assert note is not None
    assert note.counter == 0x20 and note.scanner_ip == "192.168.178.29" and note.scanner_mac == "02:00:00:00:00:02"
    assert parse_notification(NOTIFY[:40]) is None
    assert parse_notification(NOTIFY[:8] + b"\0\0\0\2" + NOTIFY[12:]) is None


def test_listener_fires_once_per_press() -> None:
    got: list[int] = []
    ready = threading.Event()

    def on_press(note: Notification) -> None:
        got.append(note.counter)
        ready.set()

    listener = ButtonListener("127.0.0.1", 0, on_press)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            target = ("127.0.0.1", listener.port)
            for _ in range(3):  # the scanner repeats every notification 3×
                s.sendto(NOTIFY, target)
            s.sendto(NOTIFY[:16] + b"\x21" + NOTIFY[17:], target)
            s.sendto(b"junk", target)
            ready.wait(2)
            for _ in range(20):
                if len(got) >= 2:
                    break
                threading.Event().wait(0.05)
    finally:
        listener.close()
    assert got == [0x20, 0x21]
