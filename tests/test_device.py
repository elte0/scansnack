"""Dauerhafte Sitzung (Device mit keep_alive): eine Verbindung für mehrere Scans, Neuaufbau nach Abriss."""

import time

import pytest

from mock_scanner import MockScanner
from scansnap import Settings, device
from scansnap.control import HostIdentity
from scansnap.scsi import ScsiChannel

IDENT = HostIdentity("test", "00" * 8, "AB" * 16, bytes.fromhex("020000000001"))
FIXTURE = "02_grayscale_simplex_normalquality"


class FakeControl:
    def __init__(self):
        self.calls = []

    def get_users(self):
        self.calls.append("get_users")
        return {"profiles": [{"user_id": IDENT.user_id, "prof_id": "P1"}]}

    def local_ip(self):
        return "127.0.0.1"

    def pick_profile(self, host_ip):
        self.calls.append("get_users")
        return {"prof_id": "P1"}

    def select_profile(self, prof_id):
        self.calls.append(f"select {prof_id}")


@pytest.fixture
def fake_sessions(monkeypatch):
    """Ersetzt den echten Verbindungsablauf: jede Sitzung bekommt einen eigenen Mock-Scanner."""
    mocks, opened = [], []

    class FakeSession(device.Session):
        def open(self):
            mock = MockScanner(FIXTURE)
            mocks.append(mock)
            self.ch = ScsiChannel("127.0.0.1", IDENT.mac, port=mock.port)
            self.ch.start_polling(0.01)
            opened.append(self)
            return self.ch

    monkeypatch.setattr(device, "Session", FakeSession)
    yield mocks, opened
    for m in mocks:
        m.close()


def make_device():
    dev = device.Device("127.0.0.1", IDENT, rotate=False, crop=False, keep_alive=True)
    dev.ctl = FakeControl()
    return dev


def test_keep_alive_reuses_session_and_polls_between_scans(fake_sessions):
    mocks, opened = fake_sessions
    dev = make_device()
    dev.connect()
    assert len(list(dev.scan(Settings()))) == 1
    assert dev.connected                       # nach dem Scan läuft das C2-Polling weiter
    time.sleep(0.3)                            # Polling-Takt nach dem Scan: 250 ms
    mocks[0]._sheet = -1                       # nächstes Blatt in den Einzug
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 1
    ops = [cdb[0] for cdb, _ in mocks[0].received]
    first_end = ops.index(0xD6)
    assert 0xC2 in ops[first_end:ops.index(0xD5, first_end)]
    assert dev.ctl.calls.count("select P1") == 2
    dev.disconnect()
    assert not dev.connected


def test_dead_connection_is_rebuilt_before_scan(fake_sessions):
    mocks, opened = fake_sessions
    dev = make_device()
    dev.connect()
    mocks[0].close()
    opened[0].ch.sock.shutdown(2)              # Scanner weg: Polling bricht ab
    time.sleep(0.05)
    assert not dev.connected
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 2


def test_connection_lost_during_scan_start_retries_once(fake_sessions, monkeypatch):
    mocks, opened = fake_sessions
    dev = make_device()
    dev.connect()
    real_stop = ScsiChannel.stop_polling
    calls = []

    def flaky_stop(self, timeout=3.0):
        calls.append(self)
        return real_stop(self, timeout) and len(calls) > 1      # erste Sitzung „hängt“

    monkeypatch.setattr(ScsiChannel, "stop_polling", flaky_stop)
    assert len(list(dev.scan(Settings()))) == 1
    assert len(opened) == 2 and opened[0].ch is None             # alte Sitzung geschlossen


def test_button_listener_follows_session(fake_sessions, monkeypatch):
    started = []

    class FakeListener:
        def __init__(self, ip, port, callback):
            started.append((ip, port, callback))
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(device, "ButtonListener", FakeListener)
    presses = []
    dev = make_device()
    dev.on_button = presses.append
    dev.connect()
    assert started == [("127.0.0.1", IDENT.host_port, presses.append)]
    listener = dev._button
    dev.connect()                              # Sitzung lebt: kein zweiter Listener
    assert len(started) == 1
    dev.disconnect()
    assert listener.closed and dev._button is None
