"""Desktop app ``scansnap-app``: wires the scanners (iX1500, iX500), PDF library and Qt UI."""

from __future__ import annotations

import argparse
import io
import platform
import sys
from importlib.metadata import version

from PIL import Image

from ..adapters.github.releases import GitHubReleases
from ..adapters.ix1500.control import profile_template
from ..adapters.library.library import Library
from ..adapters.selfupdate.installer import SelfInstaller
from ..core.document import ScanPage
from ..core.images import process
from ..core.pdf import jpegs_to_pdf
from ..core.update import Version, arch_name, os_name, parse_version
from .locator import AutoLocator

DIST_NAME = "scansnap-bridge"


def app_version() -> Version:
    """Version from the package metadata (in the bundle via ``copy_metadata`` in ``ScanSnack.spec``)."""
    parsed = parse_version(version(DIST_NAME))
    if parsed is None:
        raise ValueError(f"unexpected version {version(DIST_NAME)!r}")
    return parsed


def selftest() -> int:
    """Checks in the built bundle what a plain start does not show: package data, Pillow JPEG, PDF."""
    assert profile_template()["scan_info"], "profile_template.json missing"
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (240, 235, 220)).save(buf, "JPEG")
    page = ScanPage(process(buf.getvalue(), 300, whiten_background=True), 300)
    page.rotate(90)
    assert jpegs_to_pdf([page.rendered()], 300).startswith(b"%PDF")
    print(f"version {'.'.join(map(str, app_version()))}")
    print("selftest ok")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="scansnap-app")
    parser.add_argument("--ip", help="scanner IP address (remembered)")
    parser.add_argument("--selftest", action="store_true", help="check bundled data and image functions (build)")
    args, qt_args = parser.parse_known_args()
    if args.selftest:
        sys.exit(selftest())
    from ..adapters.qt import window  # only here: --selftest and tests without importing Qt
    from ..adapters.qt.updates import Updates

    installer = SelfInstaller()
    updates = Updates(
        GitHubReleases(),
        installer,
        app_version(),
        os_name(sys.platform),
        arch_name(platform.machine()),
        check_on_start=installer.frozen,  # started from source: only via the menu
    )
    sys.exit(window.run(AutoLocator(), lambda data: Library(data / "library.json"), args.ip, qt_args, updates))


if __name__ == "__main__":
    main()
