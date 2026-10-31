import io
import json
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from helpers import jpeg as make_jpeg
from scansnap.adapters.library.library import Library
from scansnap.adapters.qt import diagnostics as qt_diagnostics
from scansnap.adapters.qt import window as app_main
from scansnap.core.diagnostics import DiagnosticReport
from scansnap.core.document import Entry, Preset, ScanPage, thumbnail
from scansnap.core.errors import FeedError
from scansnap.core.model import FoundScanner, ScanSettings, Sheet
from scansnap.ports.scanner import ScannerPort


class FakeLocator:
    """No network: the search finds nothing, reachability is fixed, ``open`` returns ``device``."""

    def __init__(self, device: ScannerPort | None = None, reachable: bool = True):
        self.device = device
        self.is_reachable = reachable

    def discover(self, timeout: float = 3.0) -> list[FoundScanner]:
        return []

    def reachable(self, ip: str, timeout: float = 1.5) -> bool:
        return self.is_reachable

    def open(self, ip: str, *, on_button: Callable[[], None] | None = None) -> ScannerPort:
        assert self.device is not None
        return self.device


def app() -> QApplication:
    instance = QApplication.instance()
    if isinstance(instance, QApplication):
        return instance
    return QApplication([])


def no_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    def warning(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(QMessageBox, "warning", warning)


@pytest.fixture
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[app_main.MainWindow]:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))

    def question(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", question)
    qapp = app()
    w = app_main.MainWindow(Library(tmp_path / "library.json"), FakeLocator())
    w.folder = tmp_path / "out"
    w.open_after.setChecked(False)
    yield w
    w.close()
    qapp.processEvents()


def jpeg(color: str) -> bytes:
    return make_jpeg(100, 140, color)


def scan(w: app_main.MainWindow, *colors: str) -> list[bytes]:
    out: list[bytes] = []
    for c in colors:
        data = jpeg(c)
        w.add_scanned_page(ScanPage(data, 100), thumbnail(data, 64))
        out.append(data)
    return out


def view_jpegs(w: app_main.MainWindow) -> list[bytes]:
    pages: list[ScanPage] = [w.pages.item(i).data(app_main.PAGE_ROLE) for i in range(w.pages.count())]
    return [p.jpeg for p in pages]


def finish(w: app_main.MainWindow) -> app_main.Document:
    w.scan_done("", False)
    assert w.current is not None
    return w.current


def saved_entry(doc: app_main.Document | None) -> Entry:
    assert doc is not None and doc.entry is not None
    return doc.entry


def test_scan_is_saved_immediately_then_edited_and_renamed(window: app_main.MainWindow) -> None:
    scan(window, "red", "green", "blue")
    assert window.current is not None and window.current.entry is None  # during the scan: draft
    assert window.scan_target is not None
    doc = finish(window)
    entry = saved_entry(doc)
    assert entry.file.exists() and not window.drafts
    assert entry.file.parent == window.folder and entry.pages == 3
    assert window.save_button.text() == "Save Changes" and not window.save_button.isEnabled()
    window.pages.move_items([window.pages.item(2)], 0)
    window.pages.clearSelection()
    window.pages.item(1).setSelected(True)
    window.rotate_selected(90)
    window.delete_selected()
    old = entry.file
    window.name.setText("Test")
    window.name_edited("Test")
    window.save()
    assert not old.exists() and (window.folder / "Test.pdf").read_bytes().count(b"/Type /Page ") == 2
    assert window.documents.count() == 1 and window.documents.item(0).text().startswith("Test\n")


def test_each_scan_becomes_its_own_document(window: app_main.MainWindow) -> None:
    scan(window, "red", "green")
    first = finish(window)
    scan(window, "blue")  # next scan
    second = finish(window)
    assert first is not second and saved_entry(first).file != saved_entry(second).file
    assert window.documents.count() == 2 and window.current is second and window.pages.count() == 1


def test_empty_scan_leaves_no_document(window: app_main.MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    no_dialogs(monkeypatch)
    window.new_draft()
    window.scan_done("No paper in the feeder.", False)
    assert window.drafts == [] and window.current is None and window.documents.count() == 0


def test_pages_before_paper_jam_are_saved(window: app_main.MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    no_dialogs(monkeypatch)
    scan(window, "red")
    window.scan_done("Paper jam …", False)
    assert saved_entry(window.current).pages == 1


def test_failed_autosave_keeps_draft_for_manual_save(
    window: app_main.MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_dialogs(monkeypatch)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    window.folder = blocker  # mkdir fails
    scan(window, "red")
    finish(window)
    assert len(window.drafts) == 1 and window.save_button.text() == "Save as PDF"
    assert "save failed" in window.documents.item(0).text()
    window.folder = tmp_path / "ok"
    window.save()
    assert window.drafts == [] and saved_entry(window.current).file.parent == tmp_path / "ok"


def test_saved_document_survives_restart_and_can_be_edited(window: app_main.MainWindow, tmp_path: Path) -> None:
    scan(window, "red", "green")
    finish(window)
    w2 = app_main.MainWindow(Library(tmp_path / "library.json"), FakeLocator())
    try:
        assert w2.documents.count() == 1
        w2.documents.setCurrentRow(0)  # loads the pages from the PDF
        assert w2.pages.count() == 2 and not w2.save_button.isEnabled()
        w2.pages.item(0).setSelected(True)
        w2.delete_selected()
        assert w2.save_button.isEnabled() and "•" in w2.documents.item(0).text()
        w2.save()
        assert saved_entry(w2.current).pages == 1
        assert w2.current is not None and not w2.current.dirty
    finally:
        w2.close()


def test_forget_keeps_file(window: app_main.MainWindow) -> None:
    scan(window, "red")
    path = saved_entry(finish(window)).file
    window.forget()
    assert path.exists() and window.documents.count() == 0 and window.current is None


def test_move_items_to_end(window: app_main.MainWindow) -> None:
    a, b, c = scan(window, "red", "green", "blue")
    window.pages.move_items([window.pages.item(0), window.pages.item(1)], 3)
    assert view_jpegs(window) == [c, a, b]


class JamDevice:
    """Delivers one sheet with content, then a paper jam."""

    connected = True

    def __init__(self) -> None:
        text_page = Image.new("RGB", (300, 400), "white")
        ImageDraw.Draw(text_page).rectangle((40, 100, 260, 130), fill="black")  # not blank
        buf = io.BytesIO()
        text_page.save(buf, "JPEG")
        self.page = buf.getvalue()

    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def scan(self, settings: ScanSettings, *, whiten: bool = False) -> Iterator[Sheet]:
        yield Sheet(self.page, None, None)
        raise FeedError("paper jam")


def test_paper_jam_message() -> None:
    worker = app_main.ScanWorker("127.0.0.1", FakeLocator(JamDevice()))
    messages: list[tuple[str, bool]] = []
    pages: list[ScanPage] = []

    def on_done(text: str, conn: bool) -> None:
        messages.append((text, conn))

    def on_page(page: ScanPage, _thumb: bytes) -> None:
        pages.append(page)

    worker.done.connect(on_done)
    worker.page.connect(on_page)
    worker.scan(app_main.PRESETS[0])
    ((text, conn),) = messages
    assert text.startswith("Paper jam") and "1 page(s) scanned before are kept" in text and not conn
    assert len(pages) == 1


def test_unreachable_scanner_at_start_opens_dialog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    qapp = app()
    w = app_main.MainWindow(Library(tmp_path / "library.json"), FakeLocator(reachable=False))
    asked: list[str] = []

    def ask(message: str = "") -> bool:
        asked.append(message)
        return False

    monkeypatch.setattr(w, "ask_scanner", ask)
    try:
        w.worker.ip = "192.0.2.1"
        w.check_scanner()  # checks in the background, the result arrives via a signal
        for _ in range(200):
            qapp.processEvents()
            if asked:
                break
            time.sleep(0.01)
        assert asked and "192.0.2.1" in asked[0]
    finally:
        w.close()


def test_data_dir_of_previous_app_name_is_adopted(tmp_path: Path) -> None:
    old = tmp_path / "ScanSnap"
    old.mkdir()
    (old / "library.json").write_text("{}")
    new = tmp_path / "ScanSnack"
    app_main.adopt_previous(new, "ScanSnap")
    assert (new / "library.json").exists() and not old.exists()
    old.mkdir()  # new folder already exists: overwrite nothing
    app_main.adopt_previous(new, "ScanSnap")
    assert old.exists()


def test_selftest() -> None:
    from scansnap.app.desktop import selftest

    assert selftest() == 0


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_primer(window: app_main.MainWindow, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from PySide6.QtGui import QPalette

    from scansnap.adapters.qt import primer

    monkeypatch.setattr(primer, "scheme", lambda: mode)
    primer.apply()
    qapp = app()
    assert qapp.palette().color(QPalette.ColorRole.Base) == primer.color("bgColor-default", mode)
    assert primer.TOKENS[mode]["button-primary-bgColor-rest"] in qapp.styleSheet()
    assert "rgba(" in qapp.styleSheet()  # alpha tokens converted
    qapp.setStyleSheet("")


def test_panel_button_starts_scan_with_current_settings(window: app_main.MainWindow) -> None:
    requested: list[Preset] = []
    window.request_scan.disconnect()
    window.request_scan.connect(requested.append)
    window.worker.ip = "127.0.0.1"
    window.duplex.setChecked(True)
    window.panel_scan()
    window.panel_scan()  # second press during the scan: ignored
    assert len(requested) == 1 and requested[0].duplex and window.scanning


class FakeDiagnostics:
    def __init__(self) -> None:
        self.ips: list[str] = []

    def run(self, ip: str, progress: Callable[[str], None] | None = None) -> DiagnosticReport:
        self.ips.append(ip)
        if progress:
            progress("Sending discovery probes …")
        return DiagnosticReport("1.2.3", "TestOS", {"model": "ScanSnap iX500"}, [], created="2026-10-31T12:00:00Z")


def test_diagnostics_dialog_saves_and_mails_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app()
    target = tmp_path / "report.json"
    opened: list[str] = []
    revealed: list[Path] = []

    def save_name(*_args: object, **_kwargs: object) -> tuple[str, str]:
        return str(target), ""

    monkeypatch.setattr(QFileDialog, "getSaveFileName", save_name)

    def open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    monkeypatch.setattr(qt_diagnostics, "reveal", revealed.append)
    fake = FakeDiagnostics()
    dialog = qt_diagnostics.DiagnosticsDialog("10.0.0.5", fake, "dev@example.org")
    assert not dialog.mail_button.isEnabled()

    dialog.run_button.click()
    deadline = time.monotonic() + 5
    while dialog.report is None and time.monotonic() < deadline:
        QApplication.processEvents()
    assert fake.ips == ["10.0.0.5"]
    assert dialog.mail_button.isEnabled()
    assert "ScanSnap iX500" in dialog.view.toPlainText()

    dialog.model.setText("iX500")
    dialog.notes.setPlainText("Not found by the search.")
    dialog.mail_button.click()
    saved = json.loads(target.read_text())
    assert (saved["model"], saved["notes"]) == ("iX500", "Not found by the search.")
    assert opened and opened[0].startswith("mailto:dev@example.org")
    assert revealed == [target]
