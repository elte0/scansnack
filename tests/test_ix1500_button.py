"""Scan button on the touch panel: UDP notification to host_port (private live capture of 2026-10-29, unpublished)."""

import socket
import threading
import time
from collections.abc import Callable

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


# Boot notice: the button layout with event 0x21 and counter 1 (private reboot capture, addresses replaced)
BOOT = NOTIFY[:8] + b"\0\0\0\x21" + NOTIFY[12:16] + b"\x01" + NOTIFY[17:]


def test_boot_notice_is_not_a_button_notification() -> None:
    assert parse_notification(BOOT) is None


def test_repeat_window() -> None:
    listener = ButtonListener("127.0.0.1", 0, lambda note: None)
    try:
        accept = listener._accept  # pyright: ignore[reportPrivateUsage]
        assert accept(0x20, 100.0)
        assert not accept(0x20, 100.5) and not accept(0x20, 101.0)  # the scanner's repeats
        assert accept(0x21, 101.5)  # the next press
        assert accept(0x21, 200.0)  # the same counter again after a power cycle
    finally:
        listener.close()


def test_window_restarts_after_a_slow_callback() -> None:
    listener = ButtonListener("127.0.0.1", 0, lambda note: None)
    try:
        assert listener._accept(0x20, 100.0)  # pyright: ignore[reportPrivateUsage]
        listener._handled(0x20, 105.0)  # pyright: ignore[reportPrivateUsage] – the callback took 5 s
        assert not listener._accept(0x20, 105.1)  # pyright: ignore[reportPrivateUsage] – a queued repeat
    finally:
        listener.close()


def wait_until(cond: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Waits for a state of the listener threads instead of fixed pauses (slow CI runners)."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True
