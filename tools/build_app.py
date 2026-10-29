"""Baut die Desktop-App für das laufende Betriebssystem und packt sie für ein Release.

uv run --extra app python tools/build_app.py            # App + Paket
uv run --extra app python tools/build_app.py --no-pack  # nur App (dist/ScanSnack.app bzw. dist/ScanSnack/)

Ergebnis in dist/ (Version aus pyproject.toml, Architektur des laufenden Pythons):
  macOS:   ScanSnack-<ver>-macos-<arch>.dmg      (ad hoc signiert, siehe CODESIGN_IDENTITY)
  Windows: ScanSnack-<ver>-windows-<arch>.zip
  Linux:   ScanSnack-<ver>-linux-<arch>.tar.gz   (inkl. .desktop-Datei und Icon)

Jeder Build startet die gebaute App mit --selftest (Paketdaten, JPEG, PDF).
macOS ohne Gatekeeper-Warnung: CODESIGN_IDENTITY="Developer ID Application: …" setzen und danach
notarisieren (xcrun notarytool) – beides braucht ein Apple-Developer-Konto.
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

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PACKAGING = ROOT / "packaging"
NAME = "ScanSnack"

ARCHES = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}

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


def os_name() -> str:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")


def arch() -> str:
    machine = platform.machine().lower()
    return ARCHES.get(machine, machine)


def run(*cmd: str | Path) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def build() -> Path:
    """PyInstaller-Build; gibt die ausführbare Datei zurück."""
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
    base = DIST / f"{NAME}-{version()}-{os_name()}-{arch()}"
    if sys.platform == "darwin":
        dmg = base.parent / f"{base.name}.dmg"  # nicht with_suffix: die Version enthält Punkte
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
