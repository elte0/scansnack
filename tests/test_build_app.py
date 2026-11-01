"""Release packages must match the Python architecture, even under Windows emulation."""

from __future__ import annotations

import zipfile
from pathlib import Path

import build_app
import pytest


@pytest.mark.parametrize(("python_platform", "arch"), [("win-arm64", "arm64"), ("win-amd64", "x86_64")])
def test_windows_package_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    python_platform: str,
    arch: str,
) -> None:
    monkeypatch.setattr(build_app.sys, "platform", "win32")
    monkeypatch.setattr(build_app.platform, "machine", lambda: "ARM64")
    monkeypatch.setattr(build_app.sysconfig, "get_platform", lambda: python_platform)
    monkeypatch.setattr(build_app, "DIST", tmp_path)
    monkeypatch.setattr(build_app, "version", lambda: "0.4.0")
    app = tmp_path / "ScanSnack"
    app.mkdir()
    (app / "ScanSnack.exe").write_bytes(b"executable")
    package = build_app.pack()
    assert package.name == f"ScanSnack-0.4.0-windows-{arch}.zip"
    with zipfile.ZipFile(package) as archive:
        assert archive.read("ScanSnack/ScanSnack.exe") == b"executable"


def test_wrong_target_fails_before_building(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(build_app.sys, "platform", "win32")
    monkeypatch.setattr(build_app.sys, "argv", ["build_app.py", "--target", "windows-arm64"])
    monkeypatch.setattr(build_app.sysconfig, "get_platform", lambda: "win-amd64")

    def unexpected_build() -> Path:
        pytest.fail("must reject the wrong interpreter before starting PyInstaller")

    monkeypatch.setattr(build_app, "build", unexpected_build)
    with pytest.raises(SystemExit) as exc:
        build_app.main()
    assert exc.value.code == 2
    assert "this Python builds windows-x86_64" in capsys.readouterr().err
