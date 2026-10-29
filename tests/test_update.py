"""Auto-Update: Versionen, Release-Abfrage (ohne Netz), Installation mit echtem Helfer-Skript (POSIX)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import IO, Any

import pytest

from scansnap.adapters.github.releases import GitHubReleases, parse_release
from scansnap.adapters.selfupdate.installer import SelfInstaller, child_env, windows_script
from scansnap.core.update import (
    Asset,
    ChecksumMismatch,
    Release,
    is_newer,
    package_name,
    parse_sha256sums,
    parse_version,
    pick_asset,
)

ROOT = Path(__file__).resolve().parent.parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release(version: str = "0.3.0", assets: tuple[Asset, ...] = ()) -> Release:
    parsed = parse_version(version)
    assert parsed is not None
    return Release(parsed, f"v{version}", "https://example.invalid/r", "notes", assets)


# --- core ---------------------------------------------------------------------------------
def test_versions() -> None:
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.10.0") == (1, 10, 0)
    assert parse_version("v1.2.3-rc1") is None
    assert parse_version("latest") is None
    assert is_newer(release("0.10.0"), (0, 9, 9))
    assert not is_newer(release("0.3.0"), (0, 3, 0))


def test_pick_asset_matches_build_names() -> None:
    names = [package_name((0, 3, 0), o, a) for o, a in (("macos", "arm64"), ("windows", "x86_64"))]
    assert names == ["ScanSnack-0.3.0-macos-arm64.dmg", "ScanSnack-0.3.0-windows-x86_64.zip"]
    assets = tuple(Asset(n, f"https://x/{n}", 1, None) for n in [*names, "SHA256SUMS"])
    r = release(assets=assets)
    assert pick_asset(r, "windows", "x86_64") == assets[1]
    assert pick_asset(r, "linux", "x86_64") is None


def test_build_tool_uses_package_names() -> None:
    assert "package_name(" in (ROOT / "tools" / "build_app.py").read_text("utf-8")


def test_sha256sums() -> None:
    text = f"{'a' * 64}  ScanSnack-0.3.0-linux-x86_64.tar.gz\n{'B' * 64} *other.zip\nnonsense\n"
    assert parse_sha256sums(text) == {"ScanSnack-0.3.0-linux-x86_64.tar.gz": "a" * 64, "other.zip": "b" * 64}


# --- GitHub ----------------------------------------------------------------------------------
def api_release(assets: list[dict[str, Any]], **extra: object) -> dict[str, Any]:
    return {"tag_name": "v0.3.0", "html_url": "https://github.com/x/y/releases/tag/v0.3.0", "body": "Hi",
            "draft": False, "prerelease": False, "assets": assets, **extra}  # fmt: skip


def api_asset(name: str, digest: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "browser_download_url": f"https://dl/{name}", "size": 10}
    if digest:
        item["digest"] = digest
    return item


class FakeHttp:
    def __init__(self, routes: dict[str, bytes | int]):
        self.routes = routes
        self.requested: list[str] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> IO[bytes]:
        url = request.full_url
        self.requested.append(url)
        answer = self.routes[url]
        if isinstance(answer, int):
            raise urllib.error.HTTPError(url, answer, "error", {}, None)  # type: ignore[arg-type]
        return io.BytesIO(answer)


LATEST = "https://api.github.com/repos/elte0/scansnack/releases/latest"


def test_release_with_digest() -> None:
    data = api_release([api_asset("ScanSnack-0.3.0-linux-x86_64.tar.gz", "sha256:" + "C" * 64)])
    http = FakeHttp({LATEST: json.dumps(data).encode()})
    r = GitHubReleases(opener=http).latest()
    assert r is not None and r.version == (0, 3, 0) and r.notes == "Hi"
    assert r.assets[0].sha256 == "c" * 64 and r.assets[0].url.endswith(".tar.gz")
    assert http.requested == [LATEST]


def test_release_falls_back_to_sha256sums() -> None:
    name = "ScanSnack-0.3.0-windows-x86_64.zip"
    data = api_release([api_asset(name), api_asset("SHA256SUMS")])
    http = FakeHttp({LATEST: json.dumps(data).encode(), "https://dl/SHA256SUMS": f"{'d' * 64}  {name}\n".encode()})
    r = GitHubReleases(opener=http).latest()
    assert r is not None and r.assets[0].sha256 == "d" * 64


def test_no_release_yet_and_prerelease() -> None:
    assert GitHubReleases(opener=FakeHttp({LATEST: 404})).latest() is None
    assert parse_release(api_release([], prerelease=True)) is None
    with pytest.raises(urllib.error.HTTPError):
        GitHubReleases(opener=FakeHttp({LATEST: 500})).latest()


# --- Installation ------------------------------------------------------------------------------
def tarball(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def installed_app(tmp_path: Path, marker: Path) -> Path:
    """Installierte „alte“ App: ein Shell-Skript als ScanSnack-Programm."""
    folder = tmp_path / "apps" / "ScanSnack"
    folder.mkdir(parents=True)
    exe = folder / "ScanSnack"
    exe.write_text(f"#!/bin/sh\necho old > {marker}\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    (folder / "old-only.txt").write_text("stale")
    return exe


def installer(exe: Path, payload: bytes, **kwargs: Any) -> tuple[SelfInstaller, Asset, list[list[str]]]:
    name = "ScanSnack-0.3.0-linux-x86_64.tar.gz"
    asset = Asset(name, f"https://dl/{name}", len(payload), kwargs.pop("digest", sha(payload)))
    spawned: list[list[str]] = []

    def spawn(cmd: list[str], env: Any) -> None:
        spawned.append(cmd)

    inst = SelfInstaller(
        executable=exe, sys_platform="linux", machine="x86_64", frozen=True,
        opener=FakeHttp({asset.url: payload}), spawn=spawn, environ={}, **kwargs,
    )  # fmt: skip
    return inst, asset, spawned


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-Helfer")
def test_install_replaces_app_and_restarts(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    exe = installed_app(tmp_path, marker)
    payload = tarball({"ScanSnack/ScanSnack": f"#!/bin/sh\necho new > {marker}\n", "ScanSnack/lib/x.txt": "x"})
    inst, asset, spawned = installer(exe, payload)
    assert inst.blocker() is None
    progress: list[tuple[int, int]] = []
    staged = inst.prepare(asset, lambda done, total: progress.append((done, total)))
    assert staged == inst.staging / "new" / "ScanSnack" and progress[-1] == (len(payload), len(payload))
    assert not (inst.staging / asset.name).exists()  # Paket nach dem Entpacken gelöscht

    gone = subprocess.Popen(["true"])  # PID eines schon beendeten Prozesses: Helfer wartet nicht
    gone.wait()
    inst.restart_into(staged)
    script = Path(spawned[0][-1])
    text = script.read_text().replace(f"pid={os.getpid()}", f"pid={gone.pid}")
    script.write_text(text)
    subprocess.run(["/bin/sh", str(script)], check=True, timeout=30)
    for _ in range(100):  # neue App läuft im Hintergrund (nohup … &)
        if marker.exists():
            break
        time.sleep(0.05)
    assert marker.read_text().strip() == "new"
    folder = exe.parent
    assert (folder / "lib" / "x.txt").exists() and not (folder / "old-only.txt").exists()
    assert not inst.staging.exists() and not folder.with_name("ScanSnack.old").exists()


def test_wrong_checksum_is_refused(tmp_path: Path) -> None:
    exe = installed_app(tmp_path, tmp_path / "m")
    inst, asset, _ = installer(exe, tarball({"ScanSnack/ScanSnack": "x"}), digest="0" * 64)
    with pytest.raises(ChecksumMismatch):
        inst.prepare(asset, lambda _d, _t: None)
    assert (exe.parent / "old-only.txt").exists()


def test_package_without_app_is_refused(tmp_path: Path) -> None:
    exe = installed_app(tmp_path, tmp_path / "m")
    inst, asset, _ = installer(exe, tarball({"Other/file": "x"}))
    with pytest.raises(ValueError, match="does not contain"):
        inst.prepare(asset, lambda _d, _t: None)


def test_zip_for_windows(tmp_path: Path) -> None:
    folder = tmp_path / "ScanSnack"
    folder.mkdir()
    (folder / "ScanSnack.exe").write_text("old")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("ScanSnack/ScanSnack.exe", "new")
    payload = buf.getvalue()
    asset = Asset("ScanSnack-0.3.0-windows-x86_64.zip", "https://dl/w.zip", len(payload), sha(payload))
    spawned: list[list[str]] = []
    inst = SelfInstaller(
        executable=folder / "ScanSnack.exe", sys_platform="win32", machine="AMD64", frozen=True,
        opener=FakeHttp({asset.url: payload}), spawn=lambda cmd, _env: spawned.append(cmd), environ={},
    )  # fmt: skip
    staged = inst.prepare(asset, lambda _d, _t: None)
    assert (staged / "ScanSnack.exe").read_text() == "new"
    inst.restart_into(staged)
    assert spawned[0][0] == "powershell.exe" and spawned[0][-1].endswith("update.ps1")


def test_windows_script_quotes_paths() -> None:
    text = windows_script(42, Path("C:/Users/O'Brien/ScanSnack"), Path("C:/n"), Path("C:/s"), Path("C:/x.exe"))
    assert "O''Brien" in text and "$target = 'C:" in text and "$appPid = 42" in text


def test_blockers(tmp_path: Path) -> None:
    exe = installed_app(tmp_path, tmp_path / "m")
    assert SelfInstaller(executable=exe, frozen=False).blocker() is not None
    mac = Path("/private/var/folders/x/AppTranslocation/ABC/d/ScanSnack.app/Contents/MacOS/ScanSnack")
    inst = SelfInstaller(executable=mac, sys_platform="darwin", frozen=True)
    assert inst.target.name == "ScanSnack.app"
    assert "Applications" in (inst.blocker() or "")


def test_child_env_resets_pyinstaller() -> None:
    env = child_env({"LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/usr/lib", "DYLD_LIBRARY_PATH": "/b"})
    assert env == {"LD_LIBRARY_PATH": "/usr/lib", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
