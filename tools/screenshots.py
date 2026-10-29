"""Screenshots der echten Desktop-App für die Website (site/static/screenshots/).

uv run --extra app python tools/screenshots.py [--out DIR]

Startet das echte ``MainWindow`` (ohne Bildschirm, Qt-Plattform „offscreen“) mit einem Beispiel-Scanner, der
statt Netzwerk-I/O fertige Beispielseiten liefert. Gescannt wird über den echten Weg (Scan-Knopf → ScanWorker
im eigenen Thread → Seiten → automatisches Speichern als PDF), danach werden Dokumente umbenannt. Einstellungen
und Ablage liegen in einem temporären Ordner, die echten App-Daten bleiben unberührt.
Der Pages-Workflow ruft das Skript bei jedem Build auf, damit die Bilder zum aktuellen Stand passen.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")  # scharf auf Retina-Bildschirmen

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import QSettings, QStandardPaths
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from scansnap.adapters.library.library import Library
from scansnap.adapters.qt import primer
from scansnap.adapters.qt import window as qt_window
from scansnap.core.model import FoundScanner, ScanSettings, Sheet

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "site" / "static" / "screenshots"
DPI = 150
A4 = (1240, 1754)  # 210 × 297 mm bei 150 dpi
IP = "192.168.1.20"

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


def font(size: int) -> Font:
    return ImageFont.load_default(size)


def text_lines(d: ImageDraw.ImageDraw, x: int, y: int, widths: list[int], gap: int = 34) -> int:
    """Fließtext als graue Balken (liest sich in der Vorschau wie Text)."""
    for w in widths:
        d.rounded_rectangle((x, y, x + w, y + 12), 6, fill=(150, 150, 150))
        y += gap
    return y


def page_jpeg(draw: Callable[[ImageDraw.ImageDraw], None], paper: tuple[int, int, int] = (255, 255, 255)) -> bytes:
    im = Image.new("RGB", A4, paper)
    draw(ImageDraw.Draw(im))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, dpi=(DPI, DPI))
    return buf.getvalue()


def invoice(d: ImageDraw.ImageDraw) -> None:
    d.rectangle((0, 0, A4[0], 150), fill=(31, 136, 61))
    d.text((110, 45), "Northwind Supplies", font=font(56), fill="white")
    d.text((110, 230), "INVOICE", font=font(72), fill=(36, 41, 47))
    d.text((110, 330), "No. 2026-0417  ·  October 12, 2026", font=font(30), fill=(87, 96, 106))
    text_lines(d, 110, 420, [420, 360, 300])
    y = 620
    d.line((110, y, 1130, y), fill=(36, 41, 47), width=3)
    for i, (item, price) in enumerate((("Paper, A4, 5 reams", "24.90"), ("Toner cartridge", "79.00"),
                                       ("Desk organiser", "18.50"), ("Shipping", "4.90"))):  # fmt: skip
        y += 80
        d.text((110, y), item, font=font(32), fill=(36, 41, 47))
        d.text((960, y), price, font=font(32), fill=(36, 41, 47))
        if i < 3:
            d.line((110, y + 55, 1130, y + 55), fill=(208, 215, 222), width=2)
    y += 110
    d.line((110, y, 1130, y), fill=(36, 41, 47), width=3)
    d.text((760, y + 30), "Total  127.30", font=font(40), fill=(36, 41, 47))
    text_lines(d, 110, 1500, [900, 760])


def letter(d: ImageDraw.ImageDraw) -> None:
    d.ellipse((110, 110, 210, 210), fill=(9, 105, 218))
    d.text((240, 130), "City Library", font=font(48), fill=(36, 41, 47))
    text_lines(d, 110, 330, [330, 280, 250])
    d.text((110, 520), "Your membership renewal", font=font(44), fill=(36, 41, 47))
    y = 620
    for para in ([980, 1010, 960, 990, 620], [1000, 970, 1010, 540], [990, 1000, 950, 980, 1005, 400]):
        y = text_lines(d, 110, y, para, 40) + 50
    d.text((110, y + 40), "Kind regards,", font=font(32), fill=(36, 41, 47))
    d.line((110, y + 170, 420, y + 140), fill=(9, 105, 218), width=5)


def letter_back(d: ImageDraw.ImageDraw) -> None:
    y = 140
    for para in ([990, 1000, 950, 600], [1010, 980, 990, 1000, 720]):
        y = text_lines(d, 110, y, para, 40) + 50


def form(d: ImageDraw.ImageDraw) -> None:
    d.text((110, 120), "Registration Form", font=font(60), fill=(36, 41, 47))
    y = 280
    for label in ("Name", "Street", "Postcode, City", "Phone", "Email"):
        d.text((110, y), label, font=font(28), fill=(87, 96, 106))
        d.rectangle((110, y + 45, 1130, y + 115), outline=(140, 149, 159), width=3)
        y += 190
    for i, label in enumerate(("Newsletter", "Terms accepted")):
        d.rectangle((110, y + i * 80, 160, y + 50 + i * 80), outline=(140, 149, 159), width=3)
        d.text((190, y + 5 + i * 80), label, font=font(30), fill=(36, 41, 47))


def receipt(d: ImageDraw.ImageDraw) -> None:
    d.text((110, 120), "CORNER CAFÉ", font=font(64), fill=(36, 41, 47))
    y = text_lines(d, 110, 240, [520, 440, 480]) + 60
    for item, price in (("Flat white", "3.80"), ("Croissant", "2.90"), ("Bagel, cream cheese", "4.50"),
                        ("Orange juice", "3.40")):  # fmt: skip
        d.text((110, y), item, font=font(40), fill=(36, 41, 47))
        d.text((950, y), price, font=font(40), fill=(36, 41, 47))
        y += 80
    d.line((110, y + 20, 1130, y + 20), fill=(36, 41, 47), width=4)
    d.text((110, y + 60), "TOTAL", font=font(56), fill=(36, 41, 47))
    d.text((900, y + 60), "14.60", font=font(56), fill=(36, 41, 47))
    text_lines(d, 110, y + 220, [700, 620, 760, 540])


DOCUMENTS = (  # (Name, Blätter: (Vorderseite, Rückseite))
    ("Receipt Corner Café", [(page_jpeg(receipt, (238, 233, 220)), None)]),
    ("Registration Form", [(page_jpeg(form), None)]),
    ("Library Renewal", [(page_jpeg(letter), page_jpeg(letter_back))]),
    ("Invoice Northwind", [(page_jpeg(invoice), None), (page_jpeg(letter_back), None)]),
)


class SampleScanner:
    """``ports.scanner.ScannerPort`` ohne Netz: liefert bei jedem Scan das nächste Beispieldokument."""

    def __init__(self) -> None:
        self.next = 0

    @property
    def connected(self) -> bool:
        return True

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        _name, sheets = DOCUMENTS[self.next % len(DOCUMENTS)]
        self.next += 1
        for front, back in sheets:
            yield Sheet(front, back, None)


class SampleLocator:
    def __init__(self) -> None:
        self.device = SampleScanner()

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        return [FoundScanner(IP, "ScanSnap", "iX1500")]

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        return True

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> SampleScanner:
        return self.device


def wait(app: QApplication, done: Callable[[], bool], timeout: float = 20) -> None:
    end = time.monotonic() + timeout
    while not done():
        if time.monotonic() > end:
            raise TimeoutError("Scan im Screenshot-Lauf hängt")
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def grab(widget: QWidget, path: Path) -> None:
    QApplication.processEvents()
    if not widget.grab().save(str(path)):
        raise OSError(f"cannot write {path}")
    print(f"  {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}")


def set_scheme(app: QApplication, mode: str) -> None:
    """Hell/Dunkel erzwingen: die Plattform „offscreen“ kennt kein System-Farbschema."""
    primer.scheme = lambda: mode
    primer.apply()
    app.processEvents()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT, help=f"output folder (default: {OUT.relative_to(ROOT)})")
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        QStandardPaths.setTestModeEnabled(True)
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(home / "settings"))
        app = QApplication([sys.argv[0]])
        app.setApplicationName(qt_window.APP_NAME)
        app.setOrganizationName("scansnap-bridge")
        if "Inter" in QFontDatabase.families():  # ähnelt der macOS-Systemschrift; Pages-Workflow installiert sie
            f = app.font()
            f.setFamily("Inter")
            app.setFont(f)
        QSettings("scansnap-bridge", "app").setValue("ip", IP)
        set_scheme(app, "light")

        locator = SampleLocator()
        w = qt_window.MainWindow(Library(home / "library.json"), locator)
        w.folder = home / "Scans"
        w.open_after.setChecked(False)
        w._show_folder()  # pyright: ignore[reportPrivateUsage]
        w.resize(1120, 700)  # etwas kleiner als der Start, damit die Schrift auf der Website lesbar bleibt
        w.show()
        for name, _sheets in DOCUMENTS:
            w.start_scan()
            wait(app, lambda: not w.scanning)
            w.name.setText(name)
            w.name_edited(name)
            w.save()
        w.duplex.setChecked(True)
        w.skip_blank.setChecked(True)
        w.whiten.setChecked(True)
        w.pages.item(0).setSelected(True)
        # Anzeige wie auf einem Mac statt des temporären Ordners
        shown = "~/Documents/Scans/Invoice Northwind.pdf"
        w.location.setText(shown)
        w.statusBar().showMessage(f"Saved: {shown}")

        print("Screenshots:")
        grab(w, out / "main-light.png")
        set_scheme(app, "dark")
        grab(w, out / "main-dark.png")

        for mode in ("light", "dark"):
            set_scheme(app, mode)
            dialog = qt_window.ScannerDialog(
                "", locator, "Welcome! Connect your ScanSnap first: enter its IP address or search the network.", w
            )
            dialog.ip.setText(IP)
            dialog.info.setText(f"Found ScanSnap (iX1500) at {IP}.")
            dialog.show()
            grab(dialog, out / f"connect-{mode}.png")
            dialog.close()

        w.close()
        app.processEvents()


if __name__ == "__main__":
    main()
