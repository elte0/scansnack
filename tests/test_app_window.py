import io
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from scansnap.app import main as app_main  # noqa: E402
from scansnap.app.document import ScanPage, thumbnail  # noqa: E402
from scansnap.app.library import Library  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    app = QApplication.instance() or QApplication([])
    w = app_main.MainWindow(Library(tmp_path / "library.json"))
    w.folder = tmp_path / "out"
    w.open_after.setChecked(False)
    yield w
    w.close()
    app.processEvents()


def jpeg(color, size=(100, 140)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def scan(w, *colors):
    out = []
    for c in colors:
        data = jpeg(c)
        w._add_scanned_page(ScanPage(data, 100), thumbnail(data, 64))
        out.append(data)
    return out


def view_jpegs(w):
    return [w.pages.item(i).data(app_main.PAGE_ROLE).jpeg for i in range(w.pages.count())]


def finish(w):
    w._scan_done("", False)
    return w.current


def test_scan_is_saved_immediately_then_edited_and_renamed(window):
    a, b, c = scan(window, "red", "green", "blue")
    assert window.current.entry is None and window.scan_target is not None      # während des Scans: Entwurf
    doc = finish(window)
    assert doc.entry is not None and doc.entry.file.exists() and not window.drafts
    assert doc.entry.file.parent == window.folder and doc.entry.pages == 3
    assert window.save_button.text() == "Save Changes" and not window.save_button.isEnabled()
    window.pages.move_items([window.pages.item(2)], 0)
    window.pages.clearSelection()
    window.pages.item(1).setSelected(True)
    window._rotate(90)
    window._delete()
    old = doc.entry.file
    window.name.setText("Test")
    window._name_edited("Test")
    window.save()
    assert not old.exists() and (window.folder / "Test.pdf").read_bytes().count(b"/Type /Page ") == 2
    assert window.documents.count() == 1 and window.documents.item(0).text().startswith("Test\n")


def test_each_scan_becomes_its_own_document(window):
    scan(window, "red", "green")
    first = finish(window)
    scan(window, "blue")                          # nächster Scan-Vorgang
    second = finish(window)
    assert first is not second and first.entry.file != second.entry.file
    assert window.documents.count() == 2 and window.current is second and window.pages.count() == 1


def test_empty_scan_leaves_no_document(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window.new_draft()
    window._scan_done("No paper in the feeder.", False)
    assert window.drafts == [] and window.current is None and window.documents.count() == 0


def test_pages_before_paper_jam_are_saved(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    scan(window, "red")
    window._scan_done("Paper jam …", False)
    assert window.current.entry is not None and window.current.entry.pages == 1


def test_failed_autosave_keeps_draft_for_manual_save(window, monkeypatch, tmp_path):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    window.folder = blocker                        # mkdir schlägt fehl
    scan(window, "red")
    finish(window)
    assert len(window.drafts) == 1 and window.save_button.text() == "Save as PDF"
    assert "save failed" in window.documents.item(0).text()
    window.folder = tmp_path / "ok"
    window.save()
    assert window.drafts == [] and window.current.entry.file.parent == tmp_path / "ok"


def test_saved_document_survives_restart_and_can_be_edited(window, tmp_path):
    scan(window, "red", "green")
    finish(window)
    w2 = app_main.MainWindow(Library(tmp_path / "library.json"))
    try:
        assert w2.documents.count() == 1
        w2.documents.setCurrentRow(0)                     # lädt die Seiten aus dem PDF
        assert w2.pages.count() == 2 and not w2.save_button.isEnabled()
        w2.pages.item(0).setSelected(True)
        w2._delete()
        assert w2.save_button.isEnabled() and "•" in w2.documents.item(0).text()
        w2.save()
        assert w2.current.entry.pages == 1 and not w2.current.dirty
    finally:
        w2.close()


def test_forget_keeps_file(window):
    scan(window, "red")
    path = finish(window).entry.file
    window._forget()
    assert path.exists() and window.documents.count() == 0 and window.current is None


def test_move_items_to_end(window):
    a, b, c = scan(window, "red", "green", "blue")
    window.pages.move_items([window.pages.item(0), window.pages.item(1)], 3)
    assert view_jpegs(window) == [c, a, b]


def test_paper_jam_message():
    from scansnap.scanner import FeedError
    from scansnap.scsi import Sense

    worker = app_main.ScanWorker("127.0.0.1")
    messages = []
    worker.done.connect(lambda text, conn: messages.append((text, conn)))
    pages = []
    worker.page.connect(lambda p, t: pages.append(p))

    from PIL import ImageDraw

    from scansnap.scanner import Page

    text_page = Image.new("RGB", (300, 400), "white")
    ImageDraw.Draw(text_page).rectangle((40, 100, 260, 130), fill="black")   # nicht leer
    buf = io.BytesIO()
    text_page.save(buf, "JPEG")

    class JamDevice:
        ip = "127.0.0.1"
        connected = True

        def scan(self, settings, whiten=False):
            yield Page(buf.getvalue(), None, None)
            raise FeedError(Sense(3, 0x80, 0x01, False, False, 0))

    worker.device = JamDevice()
    worker.scan(app_main.PRESETS[0])
    (text, conn), = messages
    assert text.startswith("Paper jam") and "1 page(s) scanned before are kept" in text and not conn
    assert len(pages) == 1


def test_selftest():
    assert app_main._selftest() == 0



@pytest.mark.parametrize("mode", ["light", "dark"])
def test_primer(window, monkeypatch, mode):
    from PySide6.QtGui import QPalette

    from scansnap.app import primer

    monkeypatch.setattr(primer, "scheme", lambda: mode)
    primer.apply()
    app = QApplication.instance()
    assert app.palette().color(QPalette.ColorRole.Base) == primer.color("bgColor-default", mode)
    assert primer.TOKENS[mode]["button-primary-bgColor-rest"] in app.styleSheet()
    assert "rgba(" in app.styleSheet()          # Alpha-Tokens umgerechnet
    app.setStyleSheet("")


def test_panel_button_starts_scan_with_current_settings(window):
    requested = []
    window.request_scan.disconnect()
    window.request_scan.connect(requested.append)
    window.worker.ip = "127.0.0.1"
    window.duplex.setChecked(True)
    window._panel_scan()
    window._panel_scan()                       # zweiter Druck während des Scans: ignoriert
    assert len(requested) == 1 and requested[0].duplex and window.scanning
