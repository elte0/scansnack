"""Hauptfenster: Voreinstellung wählen, scannen, Seiten sichten (drehen, löschen, sortieren), als PDF ablegen.

Links Voreinstellungen und Scan-Knopf, daneben die Dokumentliste („New Scan“ + gespeicherte PDFs),
rechts die Seiten des gewählten Dokuments. Oberfläche auf Englisch, Code-Kommentare auf Deutsch.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QEvent, QObject, QSettings, QSize, QStandardPaths, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDropEvent,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPalette,
    QPixmap,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ...core.document import FEED_HELP, PRESETS, Entry, Preset, ScanPage, default_name, thumbnail
from ...core.errors import FeedError, NoPaper
from ...core.images import is_blank
from ...core.model import FoundScanner, ScanSettings
from ...ports.output import OutputPort
from ...ports.scanner import ScannerLocator, ScannerPort
from . import primer

PAGE_ROLE = Qt.ItemDataRole.UserRole
THUMB = 220
DOC_THUMB = 56
RECONNECT_MS = 20_000


def _page_icon(thumb_png: bytes, rotation: int, size: int = THUMB) -> QIcon:
    """Miniatur unten mittig auf quadratischer, transparenter Fläche: Hoch- und Querformat (nach dem Drehen)
    haben dieselbe Größe, sonst bleibt beim Drehen die alte Darstellung stehen."""
    pix = QPixmap.fromImage(QImage.fromData(thumb_png)).transformed(
        QTransform().rotate(rotation), Qt.TransformationMode.SmoothTransformation
    )
    if max(pix.width(), pix.height()) > size:
        pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((size - pix.width()) // 2, size - pix.height(), pix)  # unten: nah an der Beschriftung
    painter.end()
    icon = QIcon(canvas)
    icon.addPixmap(canvas, QIcon.Mode.Selected)  # sonst färbt Qt ausgewählte Miniaturen ein
    return icon


class ScanWorker(QObject):
    """Läuft im eigenen Thread; meldet jede Seite einzeln, damit die Vorschau mitwächst."""

    page = Signal(object, bytes)  # ScanPage, PNG-Vorschau
    status = Signal(str)
    done = Signal(str, bool)  # Fehlermeldung ("" = ok), Verbindungsproblem?
    button = Signal()  # „Scan“ am Touchpanel gedrückt (aus dem Listener-Thread)

    def __init__(self, ip: str, locator: ScannerLocator):
        super().__init__()
        self.ip = ip
        self.locator = locator
        self.device: ScannerPort | None = None
        self.device_ip = ""

    def _device(self) -> ScannerPort:
        """Dauerhaft verbundenes Gerät zur aktuellen IP (bei IP-Wechsel alte Verbindung trennen)."""
        if self.device is None or self.device_ip != self.ip:
            self.close_connection()
            self.device = self.locator.open(self.ip, on_button=self.button.emit)
            self.device_ip = self.ip
        return self.device

    @Slot()
    def open_connection(self) -> None:
        """Verbindung schon vor dem ersten Scan aufbauen (und nach Abriss wieder, sonst kommt die Scan-Taste
        nicht an); Fehler hier sind still, der Scan versucht es erneut."""
        if not self.ip:
            return
        try:
            device = self._device()
            if device.connected:
                return
            device.connect()
            self.status.emit("Connected to the scanner.")
        except Exception as e:  # noqa: BLE001
            print(f"Verbindung beim Start fehlgeschlagen: {e}", file=sys.stderr)

    def close_connection(self) -> None:
        if self.device is not None:
            self.device.disconnect()

    @Slot(object)
    def scan(self, preset: Preset) -> None:
        count = skipped = 0
        try:
            device = self._device()
            self.status.emit("Scanning …" if device.connected else "Connecting to the scanner …")
            settings = ScanSettings(preset.mode, preset.dpi, preset.duplex)
            for sheet in device.scan(settings, whiten=preset.whiten):
                for jpeg in (sheet.front, sheet.back):
                    if jpeg is None:
                        continue
                    if preset.skip_blank and is_blank(jpeg):
                        skipped += 1
                        continue
                    count += 1
                    self.page.emit(ScanPage(jpeg, preset.dpi), thumbnail(jpeg, THUMB))
                    self.status.emit(f"{count} page(s) scanned …")
            note = f", {skipped} blank page(s) removed" if skipped else ""
            self.status.emit(f"Done: {count} page(s){note}.")
            self.done.emit("", False)
        except NoPaper:
            self.done.emit("No paper in the feeder.", False)
        except FeedError as e:
            text = FEED_HELP.get(e.reason, f"The scanner reported a paper problem ({e.reason}).")
            if count:
                text += f"\n\nThe {count} page(s) scanned before are kept."
            self.done.emit(text, False)
        except OSError as e:
            traceback.print_exc(file=sys.stderr)
            self.done.emit(f"Cannot reach the scanner at {self.ip}: {e}", True)
        except Exception as e:  # noqa: BLE001 – Fehler gehen als Meldung an die Oberfläche
            traceback.print_exc(file=sys.stderr)
            self.done.emit(f"Scan failed: {e}", False)


class ScannerDialog(QDialog):
    """Scanner-IP eingeben oder im Netz suchen. Suche und Prüfung laufen im Hintergrund (mit Spinner)."""

    finished_task = Signal(str, object)  # Aufgabe ("search"/"check"), Ergebnis

    def __init__(self, ip: str, locator: ScannerLocator, message: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.locator = locator
        self.setWindowTitle("Connect Scanner")
        self.ip = QLineEdit(ip)
        self.ip.setPlaceholderText("e.g. 192.168.1.20")
        self.info = QLabel(message or "Enter the IP address of your ScanSnap or search the network.")
        self.info.setWordWrap(True)
        self.search = QPushButton("Search Network")
        self.search.clicked.connect(self._search)
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)  # unbestimmt: animiert
        self.spinner.setTextVisible(False)
        self.spinner.setMaximumHeight(10)
        self.spinner.hide()
        row = QHBoxLayout()
        row.addWidget(self.ip, 1)
        row.addWidget(self.search)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.connect_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.connect_button.setText("Connect")
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.info)
        lay.addWidget(QLabel("Scanner IP address:"))
        lay.addLayout(row)
        lay.addWidget(self.spinner)
        lay.addWidget(self.buttons)
        self.setMinimumWidth(440)
        self.finished_task.connect(self._task_done)

    def _busy(self, text: str) -> None:
        self.info.setText(text)
        self.spinner.show()
        for w in (self.search, self.connect_button, self.ip):
            w.setEnabled(False)

    def _idle(self) -> None:
        self.spinner.hide()
        for w in (self.search, self.connect_button, self.ip):
            w.setEnabled(True)

    def _run(self, task: str, fn: Callable[[], object]) -> None:
        threading.Thread(target=lambda: self.finished_task.emit(task, fn()), daemon=True).start()

    def _search(self) -> None:
        self._busy("Searching the network …")
        self._run("search", lambda: self.locator.discover(timeout=2.5))

    def _check(self) -> None:
        ip = self.value()
        if not ip:
            return
        self._busy(f"Connecting to {ip} …")
        self._run("check", lambda: self.locator.reachable(ip))

    @Slot(str, object)
    def _task_done(self, task: str, result: object) -> None:
        self._idle()
        if task == "search":
            found = cast(list[FoundScanner], result)
            if found:
                self.ip.setText(found[0].ip)
                self.info.setText(f"Found {found[0].name} ({found[0].model}) at {found[0].ip}.")
            else:
                self.info.setText("No scanner found. Make sure it is on and in the same network, or enter its IP.")
        elif result:
            self.accept()
        else:
            self.info.setText(f"No scanner responds at {self.value()}.")

    def value(self) -> str:
        return self.ip.text().strip()


class PageList(QListWidget):
    """Seitenübersicht. Im Icon-Modus verschiebt Qt beim Ziehen nur die Icons; hier wird wirklich umsortiert."""

    reordered = Signal()

    def move_items(self, items: list[QListWidgetItem], target_row: int) -> None:
        rows = sorted(self.row(i) for i in items)
        target_row -= sum(1 for r in rows if r < target_row)
        taken = [self.takeItem(r) for r in reversed(rows)][::-1]
        for offset, item in enumerate(taken):
            self.insertItem(target_row + offset, item)
            item.setSelected(True)
        self.reordered.emit()

    def dropEvent(self, event: QDropEvent) -> None:
        if event.source() is not self:
            return super().dropEvent(event)
        target = self.indexAt(event.position().toPoint())
        row = self.count()
        if target.isValid():
            rect = self.visualRect(target)
            row = target.row() + (1 if event.position().x() > rect.center().x() else 0)
        self.move_items(self.selectedItems(), row)
        event.setDropAction(Qt.DropAction.IgnoreAction)  # sonst entfernt Qt die Quelle
        event.accept()


class PreviewDialog(QDialog):
    def __init__(self, page: ScanPage, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Preview")
        pix = QPixmap.fromImage(QImage.fromData(page.jpeg)).transformed(QTransform().rotate(page.rotation))
        screen = self.screen().availableGeometry()
        label = QLabel()
        label.setPixmap(
            pix.scaled(
                int(screen.width() * 0.7),
                int(screen.height() * 0.85),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        lay = QVBoxLayout(self)
        lay.addWidget(scroll)
        self.resize(label.pixmap().width() + 40, label.pixmap().height() + 40)


class Document:
    """Seiten eines Dokuments in der App: ein ungespeicherter Scan (Entwurf) oder ein gespeichertes PDF."""

    def __init__(self, entry: Entry | None = None):
        self.entry = entry
        self.pages: list[ScanPage] = []
        self.thumbs: list[bytes] = []
        self.loaded = entry is None
        self.dirty = False
        self.name = entry.name if entry else default_name()
        self.key = entry.id if entry else f"draft-{uuid.uuid4().hex}"

    def load(self, library: OutputPort) -> None:
        if not self.loaded and self.entry is not None:
            self.pages = library.read_pages(self.entry)
            self.thumbs = [thumbnail(p.jpeg, THUMB) for p in self.pages]
            self.loaded = True


class MainWindow(QMainWindow):
    request_scan = Signal(object)
    request_connect = Signal()
    reachable_checked = Signal(bool)  # Ergebnis der Prüfung beim Start (aus einem Hintergrund-Thread)

    def __init__(self, library: OutputPort, locator: ScannerLocator):
        super().__init__()
        self.settings = QSettings("scansnap-bridge", "app")
        self.setWindowTitle("ScanSnap")
        self.resize(1240, 760)
        self.library = library
        self.locator = locator
        self.docs: dict[str, Document] = {}
        self.drafts: list[Document] = []  # ungespeicherte Scans, neueste zuerst
        self.current: Document | None = None
        self.scan_target: Document | None = None
        self.scanning = False

        # --- links: Voreinstellungen und Scan-Knopf --------------------------------
        self.presets = QListWidget()
        self.presets.setSpacing(3)
        self.presets.setWordWrap(True)
        self.presets.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.presets.setStyleSheet("QListWidget::item { padding: 6px; }")
        for p in PRESETS:
            item = QListWidgetItem(f"{p.name}\n{p.summary}")
            item.setData(PAGE_ROLE, p)
            self.presets.addItem(item)
        self.presets.setCurrentRow(min(int(str(self.settings.value("preset", 0))), len(PRESETS) - 1))
        self.presets.currentRowChanged.connect(self._preset_changed)
        self.duplex = QCheckBox("Double-sided")
        self.skip_blank = QCheckBox("Remove blank pages")
        self.whiten = QCheckBox("Remove background")
        self.whiten.setToolTip("Turn the paper color (gray, yellowish) into pure white.")
        self.scan_button = QPushButton("Scan")
        self.scan_button.setMinimumHeight(56)
        font = self.scan_button.font()
        font.setPointSize(font.pointSize() + 4)
        font.setBold(True)
        self.scan_button.setFont(font)
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self.start_scan)
        self.scanner_label = QLabel()
        self.scanner_label.setProperty("muted", True)
        self.folder = Path(str(self.settings.value("folder", str(Path.home() / "Documents" / "Scans"))))
        self.folder_button = QPushButton()
        self.folder_button.setToolTip("Folder for new scans")
        self.folder_button.clicked.connect(self._choose_folder)
        self.open_after = QCheckBox("Open PDF after scanning")
        self.open_after.setChecked(self.settings.value("open_after", "false") == "true")
        self.open_after.toggled.connect(self._open_after_toggled)
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Profile</b>"))
        left.addWidget(self.presets, 1)
        left.addWidget(self.duplex)
        left.addWidget(self.skip_blank)
        left.addWidget(self.whiten)
        left.addSpacing(8)
        save_to = QHBoxLayout()
        save_to.addWidget(QLabel("Save to:"))
        save_to.addWidget(self.folder_button, 1)
        left.addLayout(save_to)
        left.addWidget(self.open_after)
        left.addSpacing(8)
        left.addWidget(self.scan_button)
        left.addWidget(self.scanner_label)
        left_box = QWidget()
        left_box.setLayout(left)
        left_box.setFixedWidth(270)

        # --- Dokumentliste ---------------------------------------------------------
        self.documents = QListWidget()
        self.documents.setIconSize(QSize(DOC_THUMB, DOC_THUMB))
        self.documents.setSpacing(2)
        self.documents.setWordWrap(True)
        self.documents.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.documents.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.documents.currentItemChanged.connect(self._document_selected)
        self.documents.itemDoubleClicked.connect(self._open_file)
        doc_col = QVBoxLayout()
        doc_col.addWidget(QLabel("<b>Documents</b>"))
        doc_col.addWidget(self.documents, 1)
        doc_box = QWidget()
        doc_box.setLayout(doc_col)
        doc_box.setFixedWidth(270)

        # --- Seiten ----------------------------------------------------------------
        self.pages = PageList()
        self.pages.setViewMode(QListWidget.ViewMode.IconMode)
        self.pages.setIconSize(QSize(THUMB, THUMB))
        self.pages.setGridSize(QSize(THUMB + 24, THUMB + 40))
        self.pages.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.pages.setMovement(QListWidget.Movement.Snap)
        self.pages.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.pages.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.pages.setSpacing(8)
        self.pages.itemDoubleClicked.connect(self._preview)
        self.pages.reordered.connect(self._pages_changed)
        self.empty_hint = QLabel()
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setProperty("muted", True)
        self.empty_hint.setStyleSheet("font-size: 15px;")

        # --- unten: Ablage ---------------------------------------------------------
        self.name = QLineEdit()
        self.name.setMinimumWidth(200)
        self.name.textEdited.connect(self.name_edited)
        self.location = QLabel()
        self.location.setProperty("muted", True)
        self.location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.save_button = QPushButton("Save as PDF")
        self.save_button.clicked.connect(self.save)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        name_row.addWidget(self.name, 1)
        save_row = QHBoxLayout()
        save_row.addWidget(self.location, 1)
        save_row.addWidget(self.save_button)
        center = QVBoxLayout()
        center.addWidget(self.empty_hint, 1)
        center.addWidget(self.pages, 1)
        center.addLayout(name_row)
        center.addLayout(save_row)
        center_box = QWidget()
        center_box.setLayout(center)

        root = QHBoxLayout()
        root.addWidget(left_box)
        root.addWidget(doc_box)
        root.addWidget(center_box, 1)
        main = QWidget()
        main.setLayout(root)
        self.setCentralWidget(main)

        # --- Werkzeugleiste und Menü -----------------------------------------------
        bar = QToolBar("Pages")
        bar.setMovable(False)
        self.addToolBar(bar)
        self.act_left = self._action("Rotate Left", "Ctrl+L", lambda: self.rotate_selected(-90), bar)
        self.act_right = self._action("Rotate Right", "Ctrl+R", lambda: self.rotate_selected(90), bar)
        self.act_delete = self._action("Delete", None, self.delete_selected, bar)
        self.act_delete.setShortcuts([QKeySequence(Qt.Key.Key_Backspace), QKeySequence(Qt.Key.Key_Delete)])
        bar.addSeparator()
        self.act_discard = self._action("Discard Scan", None, self._discard, bar)
        bar.addSeparator()
        self.act_open = self._action("Open", "Ctrl+O", self._open_file, bar)
        self.act_reveal = self._action("Show in Finder", "Ctrl+Shift+R", self._reveal, bar)
        self.act_forget = self._action("Remove from List", None, self.forget, bar)
        menu = self.menuBar().addMenu("Scanner")
        menu.addAction(self._action("Connect Scanner …", "Ctrl+K", lambda: self.ask_scanner()))
        self.pages.itemSelectionChanged.connect(self._update)

        self.statusBar().showMessage("Ready.")
        self._preset_changed(self.presets.currentRow())

        # --- Scan-Thread ---------------------------------------------------------------
        self.scan_thread = QThread(self)
        self.worker = ScanWorker(str(self.settings.value("ip", "")), locator)
        self.worker.moveToThread(self.scan_thread)
        self.request_scan.connect(self.worker.scan)
        self.request_connect.connect(self.worker.open_connection)
        self.worker.page.connect(self.add_scanned_page)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.done.connect(self.scan_done)
        self.worker.button.connect(self.panel_scan)
        self.reachable_checked.connect(self._reachable_checked)
        self.scan_thread.finished.connect(self.worker.deleteLater)
        self.scan_thread.start()

        self._fill_documents()
        self._show_document(self.current)
        self._show_scanner()
        # Sitzung halten: nur dann meldet der Scanner den Druck auf „Scan“ am Touchpanel
        self.reconnect_timer = QTimer(self, interval=RECONNECT_MS)
        self.reconnect_timer.timeout.connect(self._keep_connected)
        self.reconnect_timer.start()

    # --- Hilfen -----------------------------------------------------------------------
    def _action(
        self, text: str, shortcut: str | None, slot: Callable[[], object], bar: QToolBar | None = None
    ) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        if bar is not None:
            bar.addAction(act)
        return act

    def _open_after_toggled(self, on: bool) -> None:
        self.settings.setValue("open_after", "true" if on else "false")

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        # Hell/Dunkel gewechselt: Farben der Einträge neu (erst wenn das Fenster fertig aufgebaut ist)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "worker"):
            self._fill_documents_keep(self.current)

    def _preset_changed(self, row: int) -> None:
        if row < 0:
            return
        p: Preset = self.presets.item(row).data(PAGE_ROLE)
        self.duplex.setChecked(p.duplex)
        self.skip_blank.setChecked(p.skip_blank)
        self.whiten.setChecked(p.whiten)
        self.settings.setValue("preset", row)

    def _show_folder(self) -> None:
        self.folder_button.setText(self.folder.name or str(self.folder))
        self.folder_button.setToolTip(f"Folder for new scans: {self.folder}")
        entry = self.current.entry if self.current else None
        self.location.setText(str(entry.file) if entry else "")
        self.location.setToolTip(str(entry.file) if entry else "")

    def _choose_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose Folder", str(self.folder))
        if chosen:
            self.folder = Path(chosen)
            self.settings.setValue("folder", chosen)
            self._show_folder()

    def _selected(self) -> list[QListWidgetItem]:
        return self.pages.selectedItems()

    def _update(self) -> None:
        doc = self.current
        has_pages = self.pages.count() > 0
        is_draft = doc is not None and doc.entry is None
        missing = doc is not None and doc.entry is not None and not doc.entry.exists
        scanning_here = doc is not None and doc is self.scan_target and self.scanning
        self.pages.setVisible(has_pages)
        self.empty_hint.setVisible(not has_pages)
        if missing and doc is not None and doc.entry is not None:
            self.empty_hint.setText(f"The file was moved or deleted:\n{doc.entry.path}")
        elif doc is None or is_draft:
            self.empty_hint.setText(
                "Scanning …" if scanning_here else "Load the document into the scanner and press “Scan”."
            )
        else:
            self.empty_hint.setText("This document has no pages.")
        # während eines Scans ist nur das Ziel gesperrt; andere Dokumente bleiben bearbeitbar
        editable = doc is not None and not missing and not scanning_here
        selected = bool(self._selected())
        for act in (self.act_left, self.act_right, self.act_delete):
            act.setEnabled(selected and editable)
        self.act_discard.setEnabled(is_draft and not scanning_here)
        saved = doc is not None and doc.entry is not None
        self.act_open.setEnabled(saved and not missing)
        self.act_reveal.setEnabled(saved and not missing)
        self.act_forget.setEnabled(saved)
        self.save_button.setText("Save Changes" if saved else "Save as PDF")
        self.save_button.setEnabled(has_pages and editable and (is_draft or (doc is not None and doc.dirty)))
        self.name.setEnabled(doc is not None and not missing)
        self.folder_button.setEnabled(not self.scanning)
        self.scan_button.setEnabled(not self.scanning)
        self.presets.setEnabled(not self.scanning)

    def _renumber(self) -> None:
        for i in range(self.pages.count()):
            self.pages.item(i).setText(f"Page {i + 1}")

    def _sync_from_view(self) -> None:
        """Reihenfolge und Inhalt der Seitenansicht ins aktuelle Dokument übernehmen."""
        doc = self.current
        if doc is None:
            return
        doc.pages = [self.pages.item(i).data(PAGE_ROLE) for i in range(self.pages.count())]
        doc.thumbs = [self.pages.item(i).data(PAGE_ROLE + 1) for i in range(self.pages.count())]

    def _pages_changed(self) -> None:
        self._renumber()
        self._sync_from_view()
        if self.current is None:
            return
        self.current.dirty = True
        self._refresh_document_item(self.current)
        self._update()

    # --- Dokumentliste -----------------------------------------------------------------
    def _document_text(self, doc: Document) -> str:
        if doc.entry is None:
            n = len(doc.pages)
            if doc is self.scan_target and self.scanning:
                return f"{doc.name}\nscanning … {n} page(s)"
            return f"{doc.name} •\n{n} page(s), not saved – save failed"
        e = doc.entry
        when = dt.datetime.fromisoformat(e.created).strftime("%b %d, %Y %H:%M")
        state = " (missing)" if not e.exists else (" •" if doc.dirty else "")
        count = len(doc.pages) if doc.loaded else e.pages
        return f"{doc.name if doc.dirty else e.name}{state}\n{when} · {count} page(s)"

    def _document_icon(self, doc: Document) -> QIcon:
        if doc.thumbs:
            return _page_icon(doc.thumbs[0], doc.pages[0].rotation, DOC_THUMB)
        if doc.entry is not None and (thumb := self.library.thumbnail(doc.entry)) is not None:
            return _page_icon(thumb, 0, DOC_THUMB)
        return _page_icon(b"", 0, DOC_THUMB)  # leere Fläche hält den Text bündig

    def _fill_documents(self) -> None:
        self.documents.blockSignals(True)
        self.documents.clear()
        for entry in self.library.entries:
            self.docs.setdefault(entry.id, Document(entry))
        for doc in self.drafts + [self.docs[e.id] for e in self.library.entries]:
            item = QListWidgetItem(self._document_icon(doc), self._document_text(doc))
            item.setData(PAGE_ROLE, doc.key)
            if doc.entry is not None and not doc.entry.exists:
                item.setForeground(self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
            self.documents.addItem(item)
            if doc is self.current:
                self.documents.setCurrentItem(item)
        self.documents.blockSignals(False)

    def _item_for(self, doc: Document) -> QListWidgetItem | None:
        for i in range(self.documents.count()):
            if self.documents.item(i).data(PAGE_ROLE) == doc.key:
                return self.documents.item(i)
        return None

    def _refresh_document_item(self, doc: Document) -> None:
        if item := self._item_for(doc):
            item.setText(self._document_text(doc))
            item.setIcon(self._document_icon(doc))

    def _document_selected(self, item: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if item is None:
            return
        doc = self.docs[item.data(PAGE_ROLE)]
        if doc.entry is not None and doc.entry.exists and not doc.loaded:
            try:
                doc.load(self.library)
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "Open Document", f"Cannot read {doc.entry.file.name}: {e}")
            self._refresh_document_item(doc)
        self._show_document(doc)

    def _show_document(self, doc: Document | None) -> None:
        self.current = doc
        self.pages.clear()
        if doc is not None:
            for page, thumb in zip(doc.pages, doc.thumbs, strict=True):
                self._append_item(page, thumb)
        self._renumber()
        self.name.setText(doc.name if doc else "")
        self._show_folder()
        self._update()

    def _append_item(self, page: ScanPage, thumb_png: bytes) -> QListWidgetItem:
        item = QListWidgetItem(_page_icon(thumb_png, page.rotation), "")
        item.setData(PAGE_ROLE, page)
        item.setData(PAGE_ROLE + 1, thumb_png)
        self.pages.addItem(item)
        return item

    def _select(self, doc: Document | None) -> None:
        self._fill_documents_keep(doc)
        self._show_document(doc)

    def _fill_documents_keep(self, doc: Document | None) -> None:
        self.current = doc
        self._fill_documents()
        if doc is None:
            self.documents.setCurrentRow(-1)

    def new_draft(self) -> Document:
        """Jeder Scan-Vorgang wird ein eigenes Dokument (oben in der Liste)."""
        doc = Document()
        self.docs[doc.key] = doc
        self.drafts.insert(0, doc)
        self.scan_target = doc
        self._select(doc)
        return doc

    def _drop_draft(self, doc: Document) -> None:
        self.drafts = [d for d in self.drafts if d is not doc]
        self.docs.pop(doc.key, None)
        if self.scan_target is doc:
            self.scan_target = None

    # --- Scanner ----------------------------------------------------------------------
    def _show_scanner(self) -> None:
        self.scanner_label.setText(f"Scanner: {self.worker.ip}" if self.worker.ip else "No scanner connected")

    def ask_scanner(self, message: str = "") -> bool:
        dialog = ScannerDialog(self.worker.ip, self.locator, message, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.worker.ip = dialog.value()
        self.settings.setValue("ip", self.worker.ip)
        self._show_scanner()
        self.statusBar().showMessage(f"Scanner: {self.worker.ip}")
        self.request_connect.emit()
        return True

    def check_scanner(self) -> None:
        """Beim Start: ohne gespeicherte oder erreichbare IP zuerst den Scanner-Dialog zeigen.
        Die Prüfung läuft im Hintergrund (Netzwerk-I/O nie im GUI-Thread)."""
        if not self.worker.ip:
            self.ask_scanner("Welcome! Connect your ScanSnap first: enter its IP address or search the network.")
            return
        ip = self.worker.ip
        threading.Thread(target=lambda: self.reachable_checked.emit(self.locator.reachable(ip)), daemon=True).start()

    @Slot(bool)
    def _reachable_checked(self, ok: bool) -> None:
        if ok:
            self.request_connect.emit()  # im Hintergrund verbinden, der erste Scan startet dann sofort
        else:
            self.ask_scanner(
                f"No scanner responds at {self.worker.ip}. Is it switched on? "
                "Enter a different IP address or search the network."
            )

    def _keep_connected(self) -> None:
        if self.worker.ip and not self.scanning:
            self.request_connect.emit()

    @Slot()
    def panel_scan(self) -> None:
        """„Scan“ am Touchpanel: wie der Scan-Knopf, mit den Einstellungen links."""
        if self.scanning:
            return
        self.statusBar().showMessage("Scan started on the scanner …")
        self.start_scan()

    def start_scan(self) -> None:
        if not self.worker.ip and not self.ask_scanner():
            return
        base: Preset = self.presets.currentItem().data(PAGE_ROLE)
        preset = Preset(
            base.name,
            base.mode,
            base.dpi,
            self.duplex.isChecked(),
            self.skip_blank.isChecked(),
            self.whiten.isChecked(),
        )
        self.scanning = True
        self.new_draft()
        self.request_scan.emit(preset)

    @Slot(object, bytes)
    def add_scanned_page(self, page: ScanPage, thumb_png: bytes) -> None:
        new = self.scan_target or self.new_draft()
        new.pages.append(page)
        new.thumbs.append(thumb_png)
        if self.current is new:
            item = self._append_item(page, thumb_png)
            self._renumber()
            self.pages.scrollToItem(item)
        self._refresh_document_item(new)
        self._update()

    @Slot(str, bool)
    def scan_done(self, error: str, connection_problem: bool) -> None:
        self.scanning = False
        target, self.scan_target = self.scan_target, None
        if target is not None and not target.pages:  # nichts gescannt: Entwurf wieder entfernen
            self._drop_draft(target)
            if self.current is target:
                self._select(self.drafts[0] if self.drafts else None)
        elif target is not None:
            # sofort speichern – auch die Seiten vor einem Papierstau
            self._store_draft(
                target, target.name, select=self.current is target, open_after=self.open_after.isChecked()
            )
        self._update()
        if not error:
            return
        self.statusBar().showMessage(error)
        if connection_problem:
            self.ask_scanner(error)
        else:
            QMessageBox.warning(self, "Scan", error)

    # --- Seiten -----------------------------------------------------------------------
    def rotate_selected(self, degrees: int) -> None:
        for item in self._selected():
            page: ScanPage = item.data(PAGE_ROLE)
            page.rotate(degrees)
            item.setIcon(_page_icon(item.data(PAGE_ROLE + 1), page.rotation))
        self.pages.viewport().update()
        if self.current is None:
            return
        self.current.dirty = True
        self._refresh_document_item(self.current)
        self._update()

    def delete_selected(self) -> None:
        for item in self._selected():
            self.pages.takeItem(self.pages.row(item))
        self._pages_changed()

    def _discard(self) -> None:
        doc = self.current
        if doc is None or doc.entry is not None:
            return
        if QMessageBox.question(self, "Discard Scan", f"Discard “{doc.name}”?") == QMessageBox.StandardButton.Yes:
            self._drop_draft(doc)
            self._select(self.drafts[0] if self.drafts else None)

    def _preview(self, item: QListWidgetItem) -> None:
        PreviewDialog(item.data(PAGE_ROLE), self).exec()

    def name_edited(self, text: str) -> None:
        if self.current is None:
            return
        self.current.name = text
        self._refresh_document_item(self.current)
        if self.current.entry is not None:
            self.current.dirty = self.current.dirty or text.strip() != self.current.entry.name
            self._refresh_document_item(self.current)
            self._update()

    # --- Ablage -----------------------------------------------------------------------
    def _store_draft(self, draft: Document, name: str, *, select: bool, open_after: bool) -> Document | None:
        """Speichert einen Entwurf als PDF und ersetzt ihn in der Liste durch das gespeicherte Dokument."""
        try:
            entry = self.library.add(draft.pages, self.folder, name)
        except (OSError, ValueError) as e:
            QMessageBox.warning(
                self,
                "Save",
                f"Saving “{draft.name}” failed: {e}\n\n"
                "The scan is kept in the list; choose a different folder and save it manually.",
            )
            return None
        saved = Document(entry)
        saved.pages = [ScanPage(p.rendered(), p.dpi) for p in draft.pages]  # Drehungen stecken im PDF
        saved.thumbs = [thumbnail(p.jpeg, THUMB) for p in saved.pages]
        saved.loaded = True
        self.docs[entry.id] = saved
        self._drop_draft(draft)
        self.statusBar().showMessage(f"Saved: {entry.path}")
        if select or self.current is draft:
            self._select(saved)
        else:
            self._fill_documents()
        if open_after:
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry.path))
        return saved

    def save(self) -> None:
        doc = self.current
        if doc is None:
            return
        self._sync_from_view()
        if doc.entry is None:  # nur wenn das automatische Speichern nach dem Scan fehlschlug
            self._store_draft(doc, self.name.text(), select=True, open_after=False)
            return
        try:
            entry = self.library.update(doc.entry, doc.pages, self.name.text())
            saved, open_after = doc, False
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Save", f"Saving failed: {e}")
            return
        # Drehungen stecken jetzt im PDF: Seiten wie gespeichert übernehmen
        saved.pages = [ScanPage(p.rendered(), p.dpi) for p in doc.pages]
        saved.thumbs = [thumbnail(p.jpeg, THUMB) for p in saved.pages]
        saved.loaded, saved.dirty, saved.name = True, False, entry.name
        self.current = saved
        self.statusBar().showMessage(f"Saved: {entry.path}")
        self._fill_documents()
        self._show_document(saved)
        if open_after:
            QDesktopServices.openUrl(QUrl.fromLocalFile(entry.path))

    def _open_file(self, _item: QListWidgetItem | None = None) -> None:
        if self.current and self.current.entry is not None and self.current.entry.exists:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.current.entry.path))

    def _reveal(self) -> None:
        entry = self.current.entry if self.current else None
        if entry is None or not entry.exists:
            return
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", entry.path], check=False)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(entry.file.parent)))

    def forget(self) -> None:
        doc = self.current
        if doc is None or doc.entry is None:
            return
        if (
            QMessageBox.question(
                self, "Remove from List", f"Remove “{doc.entry.name}” from the list? The PDF file is kept."
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self.library.remove(doc.entry)
        self.docs.pop(doc.key, None)
        self._select(self.drafts[0] if self.drafts else None)

    def unsaved(self) -> list[str]:
        return [d.name for d in self.drafts if d.pages] + [
            d.name for d in self.docs.values() if d.entry is not None and d.dirty
        ]

    def closeEvent(self, event: QCloseEvent) -> None:
        unsaved = self.unsaved()
        if (
            unsaved
            and QMessageBox.question(self, "Quit", f"Unsaved changes in: {', '.join(unsaved)}. Quit anyway?")
            != QMessageBox.StandardButton.Yes
        ):
            event.ignore()
            return
        self.reconnect_timer.stop()
        self.scan_thread.quit()
        self.scan_thread.wait(2000)
        self.worker.close_connection()
        super().closeEvent(event)


def data_dir() -> Path:
    """Ablage für App-Daten (Dokumentliste, Vorschaubilder); braucht eine laufende QApplication."""
    return Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))


def run(
    locator: ScannerLocator,
    make_output: Callable[[Path], OutputPort],
    ip: str | None = None,
    qt_args: list[str] | None = None,
) -> int:
    """Startet die Oberfläche. ``make_output`` bekommt das App-Datenverzeichnis und liefert die Ablage."""
    app = QApplication([sys.argv[0], *(qt_args or [])])
    app.setApplicationName("ScanSnap")
    app.setOrganizationName("scansnap-bridge")
    if ip:
        QSettings("scansnap-bridge", "app").setValue("ip", ip)
    primer.apply()
    window = MainWindow(make_output(data_dir()), locator)
    window.show()
    QTimer.singleShot(200, window.check_scanner)
    return app.exec()
