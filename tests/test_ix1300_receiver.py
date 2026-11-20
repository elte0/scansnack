from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from helpers import jpeg
from scansnap.adapters.ix1300.scanner import scan_sheet
from scansnap.adapters.scansnap_wifi.scsi import Response, ScsiChannel
from scansnap.app.ix1300_output import publish_sheet
from scansnap.core.errors import FeedError
from scansnap.core.model import Sheet


def test_publish_duplex_with_blank_back(tmp_path: Path) -> None:
    front, back = jpeg(color="red"), jpeg(color="white")
    path = publish_sheet(tmp_path, Sheet(front, back, None))
    pdf = path.read_bytes()
    assert b"/Count 2" in pdf
    assert pdf.count(b"/Rotate 180") == 2
    assert pdf.index(front) < pdf.index(back)
    assert list(tmp_path.iterdir()) == [path]
    assert publish_sheet(tmp_path, Sheet(front, back, None)) != path


def test_reject_truncated_back(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        publish_sheet(tmp_path, Sheet(jpeg(color="red"), jpeg(color="blue")[:-8], None))
    assert list(tmp_path.iterdir()) == []


def test_failed_rename_cleans_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(source: Path, destination: Path) -> Path:
        assert source.parent == destination.parent == tmp_path
        assert source.suffix != ".pdf"
        assert source.read_bytes().endswith(b"%%EOF\n")
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="rename failed"):
        publish_sheet(tmp_path, Sheet(jpeg(color="red"), jpeg(color="white"), None))
    assert list(tmp_path.iterdir()) == []


class Replay(ScsiChannel):
    def __init__(self, fault: str, release_failure: bool):
        self.fault = fault
        self.release_failure = release_failure
        self.calls: list[list[int]] = []
        self.pending_sense = bytes(18)
        self.fed = False

    @contextmanager
    def open(self, timeout: float) -> Generator[ScsiChannel, None, None]:
        self.calls.append([])
        yield self

    def command(self, cdb: bytes, out: bytes = b"", in_len: int = 0) -> Response:
        op = cdb[0]
        self.calls[-1].append(op)
        if op == 0xD6 and self.release_failure:
            raise OSError("release interrupted")
        if op == 0xD5:
            assert out == bytes(8) and in_len == 8
            if self.fault == "setup":
                raise OSError("setup interrupted")
        if op == 0x03:
            return Response(0, self.pending_sense)
        if op == 0x28 and cdb[2] == 0:
            side = cdb[10]
            assert cdb[5] == side * 128
            if self.fault == ("jam-back" if side else "jam-front"):
                self.pending_sense = bytes.fromhex("f00023002fffd40a00000000800100000000")
                return Response(2, bytes([side, 0]))
            self.pending_sense = bytes.fromhex("f00040000000000a00000000000000000000")
            return Response(2, bytes([side, 0]) + jpeg(color="white" if side else "red"))
        if op == 0xE0:
            if self.fed:
                if self.fault == "before-empty":
                    raise OSError("transfer interrupted")
                self.pending_sense = bytes.fromhex("f00003000000000a00000000800300000000")
                return Response(2, b"")
            self.fed = True
        return Response(0, bytes(in_len))


def test_return_scan_sequence_and_release_warning(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    replay = Replay("", True)
    sheet = scan_sheet(replay.open)
    assert (sheet.front, sheet.back) == (jpeg(color="red"), jpeg(color="white"))
    assert b"/Count 2" in publish_sheet(tmp_path, sheet).read_bytes()
    assert replay.calls == [
        [0xC2, 0xD5, 0xC2],
        [0xD8, 0xE9, 0xD4, 0xC2, 3, 0xE0, 0x28, 3, 0x28, 3, 0x28, 0xC2, 0xE0, 3],
        [0xD6],
    ]
    assert "release interrupted" in caplog.text


@pytest.mark.parametrize("fault", ["setup", "jam-front", "jam-back", "before-empty"])
def test_failed_sheet_released_once(fault: str) -> None:
    replay = Replay(fault, True)
    if fault.startswith("jam"):
        with pytest.raises(FeedError, match="paper jam"):
            scan_sheet(replay.open)
    else:
        with pytest.raises(OSError, match=r"setup interrupted|transfer interrupted"):
            scan_sheet(replay.open)
    assert replay.calls[-1] == [0xD6]
    assert len(replay.calls) == (2 if fault == "setup" else 3)
