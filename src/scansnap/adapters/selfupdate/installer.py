"""Replaces the built app (PyInstaller) with a new version and restarts it.

Flow: download the package next to the installed app (``.<App>-update/``), check SHA-256, unpack (DMG via
``hdiutil``, ZIP, tar.gz). Then a helper script (``sh`` or PowerShell) as a separate process: it waits
until the app has quit, renames the old app, moves the new one into its place (back on error),
cleans up and starts the new app. A running program cannot overwrite itself on Windows,
hence the same route via the helper everywhere.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import IO, cast

from ...core.update import APP_NAME, Asset, Sha256, arch_name, os_name

CHUNK = 256 * 1024
WAIT_SECONDS = 120  # how long the helper waits for the old app to quit
CREATE_NEW_PROCESS_GROUP = 0x00000200  # Windows constants (subprocess only has them on Windows)
CREATE_NO_WINDOW = 0x08000000

Opener = Callable[[urllib.request.Request, float], IO[bytes]]
Spawn = Callable[[list[str], Mapping[str, str]], None]


def _open(request: urllib.request.Request, timeout: float) -> IO[bytes]:
    return cast(IO[bytes], urllib.request.urlopen(request, timeout=timeout))


def _spawn(cmd: list[str], env: Mapping[str, str]) -> None:
    """Start the helper detached: it must outlive the app."""
    if sys.platform == "win32":
        flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        subprocess.Popen(cmd, env=dict(env), creationflags=flags, close_fds=True)
    else:
        subprocess.Popen(
            cmd, env=dict(env), start_new_session=True, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )  # fmt: skip


def child_env(environ: Mapping[str, str]) -> dict[str, str]:
    """Environment for the helper and the new app: without the variables the PyInstaller bootloader sets
    (otherwise the new app takes itself for a subprocess of the old one)."""
    env = dict(environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if f"{var}_ORIG" in env:
            env[var] = env.pop(f"{var}_ORIG")
        else:
            env.pop(var, None)
    return env


def _ps(value: str | Path) -> str:
    """PowerShell string in single quotes."""
    return "'" + str(value).replace("'", "''") + "'"


def posix_script(pid: int, target: Path, staged: Path, staging: Path, relaunch: str) -> str:
    q = shlex.quote
    return f"""#!/bin/sh
# ScanSnack update: waits for the old app to quit, replaces it and starts the new one.
pid={pid}
target={q(str(target))}
new={q(str(staged))}
staging={q(str(staging))}
old="$target.old"
i=0
while kill -0 "$pid" 2>/dev/null; do
  i=$((i + 1))
  [ "$i" -gt {WAIT_SECONDS * 5} ] && exit 1
  sleep 0.2
done
rm -rf "$old"
if mv "$target" "$old"; then
  if mv "$new" "$target"; then rm -rf "$old"; else mv "$old" "$target"; fi
fi
rm -rf "$staging"
{relaunch}
"""


def windows_script(pid: int, target: Path, staged: Path, staging: Path, exe: Path) -> str:
    return f"""# ScanSnack update: waits for the old app to quit, replaces it and starts the new one.
$appPid = {pid}
$target = {_ps(target)}
$new = {_ps(staged)}
$staging = {_ps(staging)}
$old = "$target.old"
Wait-Process -Id $appPid -Timeout {WAIT_SECONDS} -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }}
$moved = $false
for ($i = 0; $i -lt 40 -and -not $moved; $i++) {{
  try {{ Move-Item -LiteralPath $target -Destination $old -ErrorAction Stop; $moved = $true }}
  catch {{ Start-Sleep -Milliseconds 500 }}
}}
if ($moved) {{
  try {{
    Move-Item -LiteralPath $new -Destination $target -ErrorAction Stop
    Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue
  }} catch {{ Move-Item -LiteralPath $old -Destination $target }}
}}
Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
Start-Process -FilePath {_ps(exe)}
"""


class SelfInstaller:
    """``Installer`` for the PyInstaller app: on macOS ``ScanSnack.app``, otherwise the folder ``ScanSnack``."""

    def __init__(
        self,
        *,
        executable: Path | None = None,
        sys_platform: str = sys.platform,
        machine: str | None = None,
        frozen: bool | None = None,
        opener: Opener = _open,
        spawn: Spawn = _spawn,
        environ: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ):
        self.executable = (executable or Path(sys.executable)).resolve()
        self.sys_platform = sys_platform
        self.os = os_name(sys_platform)
        self.arch = arch_name(machine or platform.machine())
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        self.opener = opener
        self.spawn = spawn
        self.environ = os.environ if environ is None else environ
        self.timeout = timeout

    @property
    def target(self) -> Path:
        """What gets replaced: the ``.app`` bundle or the app folder."""
        if self.os == "macos" and len(self.executable.parents) > 2 and self.executable.parents[2].suffix == ".app":
            return self.executable.parents[2]
        return self.executable.parent

    @property
    def staging(self) -> Path:
        return self.target.parent / f".{self.target.name}-update"

    def blocker(self) -> str | None:
        if not self.frozen:
            return "ScanSnack runs from source code; update it with git pull."
        target = self.target.as_posix()
        if self.os == "macos" and "/AppTranslocation/" in target:
            return "macOS runs ScanSnack from a temporary location. Move it to the Applications folder first."
        if self.os == "macos" and target.startswith("/Volumes/"):
            return "ScanSnack runs from the disk image. Drag it to the Applications folder first."
        if not (os.access(self.target, os.W_OK) and os.access(self.target.parent, os.W_OK)):
            return f"No permission to replace {self.target}."
        return None

    # --- Download and unpack ------------------------------------------------------------
    def prepare(self, asset: Asset, progress: Callable[[int, int], None]) -> Path:
        if not asset.sha256:
            raise ValueError(f"{asset.name}: no checksum published, refusing to install")
        shutil.rmtree(self.staging, ignore_errors=True)
        self.staging.mkdir(parents=True)
        archive = self.staging / asset.name
        self._download(asset, archive, progress)
        new = self.staging / "new"
        new.mkdir()
        self._unpack(archive, new)
        archive.unlink()
        staged = new / (f"{APP_NAME}.app" if self.os == "macos" else APP_NAME)  # name in the package, not the local one
        if not (staged / self.executable.relative_to(self.target)).is_file():
            raise ValueError(f"{asset.name}: does not contain {self.executable.relative_to(self.target)}")
        return staged

    def _download(self, asset: Asset, path: Path, progress: Callable[[int, int], None]) -> None:
        assert asset.sha256 is not None
        check = Sha256(asset.sha256)
        request = urllib.request.Request(asset.url, headers={"User-Agent": "ScanSnack"})
        done = 0
        with self.opener(request, self.timeout) as response, path.open("wb") as out:
            while chunk := response.read(CHUNK):
                out.write(chunk)
                check.update(chunk)
                done += len(chunk)
                progress(done, asset.size)
        check.verify(asset.name)

    def _unpack(self, archive: Path, dest: Path) -> None:
        name = archive.name
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(dest)  # zipfile strips absolute paths and ".."
        elif name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(archive, "r:gz") as t:
                t.extractall(dest, filter="data")  # nothing outside dest, no device files
        elif name.endswith(".dmg"):
            self._unpack_dmg(archive, dest)
        else:
            raise ValueError(f"{name}: unknown package format")

    @staticmethod
    def _unpack_dmg(dmg: Path, dest: Path) -> None:
        mount = Path(tempfile.mkdtemp(prefix="scansnack-dmg-"))
        subprocess.run(
            ["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(dmg)],
            check=True, capture_output=True,
        )  # fmt: skip
        try:
            for app in mount.glob("*.app"):
                subprocess.run(["ditto", str(app), str(dest / app.name)], check=True, capture_output=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], check=False, capture_output=True)
            mount.rmdir()

    # --- Replace and restart ---------------------------------------------------------------
    def restart_into(self, staged: Path) -> None:
        pid = os.getpid()
        env = child_env(self.environ)
        if self.os == "windows":
            script = self.staging / "update.ps1"
            exe = self.target / self.executable.relative_to(self.target)
            script.write_text(windows_script(pid, self.target, staged, self.staging, exe), "utf-8-sig")
            cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden"]
            self.spawn([*cmd, "-File", str(script)], env)
            return
        if self.os == "macos":
            # No quarantine attribute (urllib sets none), remove it anyway to be safe
            relaunch = 'xattr -dr com.apple.quarantine "$target" 2>/dev/null\nopen "$target"'
        else:
            exe = shlex.quote(self.executable.relative_to(self.target).as_posix())
            relaunch = f'nohup "$target"/{exe} >/dev/null 2>&1 &'
        script = self.staging / "update.sh"
        script.write_text(posix_script(pid, self.target, staged, self.staging, relaunch), "utf-8")
        self.spawn(["/bin/sh", str(script)], env)
