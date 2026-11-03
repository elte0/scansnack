"""Error reports: personal data is removed, the email link stays within its limit."""

from urllib.parse import unquote

import pytest

from scansnap.core import report
from scansnap.core.report import DeviceDiagnostics, LogBuffer, ReportInput, Tee, build, mailto, scrub


def test_scrub_removes_addresses_ids_and_user_folders() -> None:
    text = (
        "Connected (host 192.168.178.37, ID 0123456789abcdef)\n"
        "mac 3c:22:fb:12:34:56, v6 fe80::1c2d:3e4f%en0, mail jane.doe@example.org\n"
        'File "/Users/jane/Library/x.py", line 3; C:\\Users\\jane\\AppData\\y.py\n'
        "user AB12CD34EF56AB12CD34EF56AB12CD34 saved Steuer_2025.pdf, password=hunter22\n"
    )
    out = scrub(text)
    for secret in ("192.168", "0123456789abcdef", "3c:22", "fe80", "jane", "AB12CD34", "Steuer", "hunter22"):
        assert secret not in out
    assert "/Users/<user>/Library/x.py" in out
    assert "<file>.pdf" in out
    assert "password=<private>" in out


def test_scrub_keeps_what_is_needed_to_debug() -> None:
    text = (
        "16:01:32 Scan failed: command 0xd4 failed: sense key=0x5 asc=0x26 ascq=0x00 eom=False\n"
        "registration rejected, status -3: unknown reason\nKeyError: 'conn_user'\nPython 3.12.7, Qt 6.8.1\n"
        'File "scanner.py", line 42, in scan'
    )
    assert scrub(text) == text


def test_scrub_private_values_in_every_spelling() -> None:
    out = scrub(
        "serial A0XX012345, mac 0011223344AA, host aabbccdd. a0xx012345 Scanner", ["A0XX012345", "0011223344aa"]
    )
    assert "012345" not in out and "0011" not in out and "00:11" not in out
    assert out.endswith("Scanner")


def test_scrub_ignores_short_values_and_matches_whole_words() -> None:
    assert scrub("Scan failed in Scanner", ["Sca", "Scan"]) == "<private> failed in Scanner"


def test_build_contains_sections_and_strips_device_private_values() -> None:
    device = DeviceDiagnostics(
        {"Model": "iX500", "Firmware": "0M00", "Last step": "registering (0x11)"}, ("A0XX012345",)
    )
    text = build(
        ReportInput(
            "0.5.0",
            "Scan failed: registration rejected, status -3",
            "Traceback (most recent call last):\n  ...\nRegistrationError: status -3",
            {"Qt": "6.8.1"},
            device,
            "Connected to A0XX012345 at 10.0.0.7",
            ["10.0.0.7"],
        )
    )
    assert "App version: 0.5.0" in text
    assert "OS: " in text and "Qt: 6.8.1" in text
    assert "Model: iX500" in text and "Firmware: 0M00" in text and "Last step: registering (0x11)" in text
    assert "registration rejected, status -3" in text and "RegistrationError" in text
    assert "A0XX012345" not in text and "10.0.0.7" not in text


def test_build_strips_host_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(report, "host_private", lambda: ["janes-macbook"])
    text = build(ReportInput("0.5.0", log="started on janes-macbook.local"))
    assert "janes-macbook" not in text
    assert "not connected in this session" in text


def test_mailto_short_report_is_complete() -> None:
    mail = mailto("line 1\nline 2 & more", "ScanSnack error", limit=10_000)
    assert mail.complete
    assert mail.url.startswith("mailto:scansnack@posteo.de?subject=ScanSnack%20error&body=")
    assert unquote(mail.url.split("body=")[1]) == "line 1\r\nline 2 & more"


def test_mailto_long_report_is_shortened_to_the_limit() -> None:
    body = "\n".join(f"line {i} " + "x" * 40 for i in range(500))
    mail = mailto(body, "s", limit=1_900)
    assert not mail.complete
    assert len(mail.url) <= 1_900
    decoded = unquote(mail.url)
    assert "line 0 " in decoded and "full report is on your clipboard" in decoded


def test_log_buffer_keeps_the_last_lines_and_tee_writes_through() -> None:
    log = LogBuffer(lines=3)
    tee = Tee(None, log)
    tee.write("a\nb\n")
    tee.write("c\nd")
    tee.write("\ne")
    lines = log.text().splitlines()
    assert [line.split(" ", 1)[1] for line in lines[:3]] == ["b", "c", "d"]
    assert lines[3] == "e"
