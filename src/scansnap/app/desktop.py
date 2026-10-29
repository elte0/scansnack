"""Desktop-App ``scansnap-app``: verdrahtet ix1500-Scanner, PDF-Ablage und Qt-Oberfläche."""

from __future__ import annotations

import argparse
import io
import sys

from PIL import Image

from ..adapters.ix1500.control import profile_template
from ..adapters.ix1500.locator import Ix1500Locator
from ..adapters.library.library import Library
from ..core.document import ScanPage
from ..core.images import process
from ..core.pdf import jpegs_to_pdf


def selftest() -> int:
    """Prüft im gebauten Bundle, was ein bloßer Start nicht zeigt: Paketdaten, Pillow-JPEG, PDF."""
    assert profile_template()["scan_info"], "profile_template.json fehlt"
    buf = io.BytesIO()
    Image.new("RGB", (64, 96), (240, 235, 220)).save(buf, "JPEG")
    page = ScanPage(process(buf.getvalue(), 300, whiten_background=True), 300)
    page.rotate(90)
    assert jpegs_to_pdf([page.rendered()], 300).startswith(b"%PDF")
    print("selftest ok")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="scansnap-app")
    parser.add_argument("--ip", help="scanner IP address (remembered)")
    parser.add_argument("--selftest", action="store_true", help="check bundled data and image functions (build)")
    args, qt_args = parser.parse_known_args()
    if args.selftest:
        sys.exit(selftest())
    from ..adapters.qt import window  # erst hier: --selftest und Tests ohne Qt-Import

    sys.exit(window.run(Ix1500Locator(), lambda data: Library(data / "library.json"), args.ip, qt_args))


if __name__ == "__main__":
    main()
