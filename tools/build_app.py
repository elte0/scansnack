"""Builds the desktop app for the running operating system and packages it for a release.

uv run --extra app python tools/build_app.py            # app + package
uv run --extra app python tools/build_app.py --no-pack  # app only (dist/ScanSnack.app or dist/ScanSnack/)

Result in dist/ (version from pyproject.toml, architecture of the running Python):
  macOS:   ScanSnack-<ver>-macos-<arch>.dmg      (ad hoc signed, see CODESIGN_IDENTITY)
  Windows: ScanSnack-<ver>-windows-<arch>.zip
  Linux:   ScanSnack-<ver>-linux-<arch>.tar.gz   (incl. .desktop file and icon)

Every build starts the built app with --selftest (package data, JPEG, PDF).
macOS without a Gatekeeper warning: set CODESIGN_IDENTITY="Developer ID Application: …" and then
notarize (xcrun notarytool) – both need an Apple Developer account.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from scansnap.core.update import arch_name, os_name, package_name, parse_version

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging"
NAME = "ScanSnack"

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=ScanSnack
Comment=Scan with a ScanSnap iX1500 over Wi-Fi
Exec=ScanSnack
Icon=scansnack
Categories=Office;Scanning;Graphics;
Terminal=false
"""


def version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"])


def run(*cmd: str | Path) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def build() -> Path:
    """PyInstaller build; returns the executable."""
    if sys.platform == "darwin" and not (PACKAGING / f"{NAME}.icns").exists():
        run(sys.executable, ROOT / "tools" / "make_icon.py")
    run(
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", DIST, "--workpath", ROOT / "build", PACKAGING / f"{NAME}.spec",
    )  # fmt: skip
    if sys.platform == "darwin":
        app = DIST / f"{NAME}.app"
        if identity := os.environ.get("CODESIGN_IDENTITY"):
            run("codesign", "--force", "--deep", "--options", "runtime", "--timestamp", "--sign", identity, app)
        run("codesign", "--verify", "--deep", "--strict", app)
        return app / "Contents" / "MacOS" / NAME
    return DIST / NAME / (f"{NAME}.exe" if sys.platform == "win32" else NAME)


def pack() -> Path:
    # package name from core.update: the app's update check looks for it in the release
    ver = parse_version(version())
    assert ver is not None, version()
    file = DIST / package_name(ver, os_name(sys.platform), arch_name(platform.machine()))
    base = DIST / file.name.removesuffix(".zip").removesuffix(".tar.gz")
    if sys.platform == "darwin":
        dmg = file
        dmg.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory() as stage:
            shutil.copytree(DIST / f"{NAME}.app", Path(stage) / f"{NAME}.app", symlinks=True)
            (Path(stage) / "Applications").symlink_to("/Applications")
            run(
                "hdiutil", "create", "-volname", NAME, "-srcfolder", stage, "-fs", "HFS+", "-format", "UDZO", "-ov", dmg
            )
        return dmg
    if sys.platform == "win32":
        return Path(shutil.make_archive(str(base), "zip", DIST, NAME))
    folder = DIST / NAME
    shutil.copy(PACKAGING / f"{NAME}.png", folder / "scansnack.png")
    (folder / "scansnack.desktop").write_text(DESKTOP_ENTRY, "utf-8")
    return Path(shutil.make_archive(str(base), "gztar", DIST, NAME))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-pack", action="store_true", help="only build the app, no DMG/ZIP/tarball")
    args = parser.parse_args()
    exe = build()
    run(exe, "--selftest")
    print(f"App: {exe}")
    if not args.no_pack:
        print(f"Package: {pack()}")


if __name__ == "__main__":
    main()
