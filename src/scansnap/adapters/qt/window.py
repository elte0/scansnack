"""Main window: pick a preset, scan, review pages (rotate, delete, reorder), save as PDF.

On the left presets and the scan button, next to them the document list ("New Scan" + saved PDFs),
on the right the pages of the selected document.
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
    QStandardItemModel,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
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

from ...core import catalog
from ...core.document import FEED_HELP, PRESETS, Entry, Preset, ScanPage, default_name, thumbnail
from ...core.errors import FeedError, NoPaper
from ...core.images import is_blank
from ...core.model import FoundScanner, ScanSettings
from ...ports.output import OutputPort
from ...ports.scanner import ScannerLocator, ScannerPort
from . import primer
from .updates import UpdateChecker, Updates

APP_NAME = "ScanSnack"
PREVIOUS_APP_NAME = "ScanSnap"  # until 2026-10: data folder under this name
PAGE_ROLE = Qt.ItemDataRole.UserRole
THUMB = 220
DOC_THUMB = 56
RECONNECT_MS = 20_000


def _page_icon(thumb_png: bytes, rotation: int, size: int = THUMB) -> QIcon:
    """Thumbnail bottom-centered on a square, transparent canvas: portrait and landscape (after rotating)
    have the same size, otherwise the old rendering stays visible when rotating."""
    pix = QPixmap.fromImage(QImage.fromData(thumb_png)).transformed(
        QTransform().rotate(rotation), Qt.TransformationMode.SmoothTransformation
    )
    if max(pix.width(), pix.height()) > size:
        pix = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    canvas = QPixmap(size, size)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((size - pix.width()) // 2, size - pix.height(), pix)  # bottom: close to the label
    painter.end()
    icon = QIcon(canvas)
    icon.addPixmap(canvas, QIcon.Mode.Selected)  # otherwise Qt tints selected thumbnails
    return icon


class ScanWorker(QObject):
    """Runs in its own thread; reports every page separately so that the preview grows along."""

    page = Signal(object, bytes)  # ScanPage, PNG preview
    status = Signal(str)
    done = Signal(str, bool)  # error message ("" = ok), connection problem?
    button = Signal()  # "Scan" pressed on the touch panel (from the listener thread)

    def __init__(self, ip: str, locator: ScannerLocator, model: str = catalog.DEFAULT_MODEL, pairing_key: str = ""):
        super().__init__()
        self.ip, self.model, self.pairing_key = ip, model, pairing_key
        self.locator = locator
        self.device: ScannerPort | None = None
        self.device_key: tuple[str, str, str] = ("", "", "")

    def _device(self) -> ScannerPort:
        """Persistently connected device for the current scanner (disconnect the old one when IP, model or
        pairing key change)."""
        key = (self.ip, self.model, self.pairing_key)
        if self.device is None or self.device_key != key:
            self.close_connection()
            self.device = self.locator.open(
                self.ip, model=self.model, pairing_key=self.pairing_key, on_button=self.button.emit
            )
            self.device_key = key
        return self.device

    @Slot()
    def open_connection(self) -> None:
        """Connect before the first scan (and again after a drop, otherwise the scan button press does not
        arrive); errors here are silent, the scan tries again."""
        if not self.ip:
            return
        try:
            device = self._device()
            if device.connected:
                return
            device.connect()
            self.status.emit("Connected to the scanner.")
        except Exception as e:  # noqa: BLE001
            print(f"Connecting at startup failed: {e}", file=sys.stderr)

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
        except Exception as e:  # noqa: BLE001 – errors go to the UI as a message
            traceback.print_exc(file=sys.stderr)
            self.done.emit(f"Scan failed: {e}", False)


class ScannerDialog(QDialog):
    """Choose the scanner model, enter the IP or search the network. Models ScanSnack does not support yet are
    listed but greyed out. Search and check run in the background (with a spinner)."""

    finished_task = Signal(str, object)  # task ("search"/"check"), result

    def __init__(
        self,
        ip: str,
        locator: ScannerLocator,
        message: str = "",
        parent: QWidget | None = None,
        *,
        model: str = "",
        pairing_key: str = "",
    ):
        super().__init__(parent)
        self.locator = locator
        self.setWindowTitle("Connect Scanner")
        self.model = QComboBox()
        self.model.setPlaceholderText("Choose your model")
        items = cast(QStandardItemModel, self.model.model())
        for m in catalog.MODELS:
            label = m.full_name
            if m.support == "beta":
                label += " (beta)"
            elif not m.usable:
                label += " – not supported yet"
            self.model.addItem(label, m.id)
            item = items.item(self.model.count() - 1)
            item.setToolTip(f"{m.connection}. {m.note}".strip())
            item.setEnabled(m.usable)
        self.model.setCurrentIndex(self.model.findData(model) if catalog.by_id(model) else -1)
        self.model.currentIndexChanged.connect(self._model_changed)
        self.ip = QLineEdit(ip)
        self.ip.setPlaceholderText("e.g. 192.168.1.20")
        self.key = QLineEdit(pairing_key)
        self.key.setPlaceholderText("12 characters")
        self.key_label = QLabel("Pairing key (iX500):")
        self.get_key = QPushButton("Get Key …")
        self.get_key.setToolTip("Get the pairing key from ScanSnap Home on another computer")
        self.get_key.clicked.connect(self._capture_key)
        self.found_name = ""  # device name from the last network search, e.g. iX500-A0B1C2D3E4
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.info = QLabel(message or "Choose your ScanSnap model, then enter its IP address or search the network.")
        self.info.setWordWrap(True)
        self.search = QPushButton("Search Network")
        self.search.clicked.connect(self._search)
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)  # indeterminate: animated
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
        lay.addWidget(QLabel("Scanner model:"))
        lay.addWidget(self.model)
        lay.addWidget(self.note)
        lay.addWidget(QLabel("Scanner IP address:"))
        lay.addLayout(row)
        lay.addWidget(self.key_label)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(self.get_key)
        lay.addLayout(key_row)
        lay.addWidget(self.spinner)
        lay.addWidget(self.buttons)
        self.setMinimumWidth(440)
        self.finished_task.connect(self._task_done)
        self._model_changed()

    def selected_model(self) -> catalog.ScannerModel | None:
        data = self.model.currentData()
        return catalog.by_id(data) if isinstance(data, str) else None

    def _model_changed(self) -> None:
        m = self.selected_model()
        needs_key = bool(m and m.needs_pairing_key)
        self.key.setVisible(needs_key)
        self.key_label.setVisible(needs_key)
        self.get_key.setVisible(needs_key)
        self.note.setText(f"Beta: {m.note}" if m and m.support == "beta" else "")
        self.note.setVisible(bool(self.note.text()))
        self.connect_button.setEnabled(m is not None and m.usable)

    def _busy(self, text: str) -> None:
        self.info.setText(text)
        self.spinner.show()
        for w in (self.search, self.connect_button, self.ip, self.model, self.key, self.get_key):
            w.setEnabled(False)

    def _idle(self) -> None:
        self.spinner.hide()
        for w in (self.search, self.ip, self.model, self.key, self.get_key):
            w.setEnabled(True)
        self._model_changed()

    def _run(self, task: str, fn: Callable[[], object]) -> None:
        threading.Thread(target=lambda: self.finished_task.emit(task, fn()), daemon=True).start()

    def _search(self) -> None:
        self._busy("Searching the network …")
        self._run("search", lambda: self.locator.discover(timeout=2.5))

    def _check(self) -> None:
        ip, m = self.value(), self.selected_model()
        if m is None or not m.usable:
            self.info.setText("Choose your scanner model first.")
            return
        if not ip:
            return
        if m.needs_pairing_key and not self.pairing_key():
            self.info.setText(f"The {m.full_name} needs its pairing key. Enter it below.")
            return
        self._busy(f"Connecting to {ip} …")
        self._run("check", lambda: self.locator.reachable(ip))

    @Slot(str, object)
    def _task_done(self, task: str, result: object) -> None:
        self._idle()
        if task == "search":
            found = cast(list[FoundScanner], result)
            if found:
                first = found[0]
                self.found_name = first.name
                self.ip.setText(first.ip)
                m = catalog.match(first.model) or catalog.match(first.name)
                if m is not None:
                    self.model.setCurrentIndex(self.model.findData(m.id))
                text = f"Found {first.name} ({first.model}) at {first.ip}."
                if m is not None and not m.usable:
                    text += f" ScanSnack does not support the {m.full_name} yet."
                self.info.setText(text)
            else:
                self.info.setText("No scanner found. Make sure it is on and in the same network, or enter its IP.")
        elif result:
            self.accept()
        else:
            self.info.setText(f"No scanner responds at {self.value()}.")

    def _capture_key(self) -> None:
        m = self.selected_model()
        if m is None or not m.needs_pairing_key:
            return
        dialog = KeyCaptureDialog(self.locator, m, self.found_name, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.key:
            self.key.setText(dialog.key)
            self.info.setText(f"Pairing key received. Switch the {m.full_name} on again, then connect.")

    def value(self) -> str:
        return self.ip.text().strip()

    def model_id(self) -> str:
        m = self.selected_model()
        return m.id if m else ""

    def pairing_key(self) -> str:
        m = self.selected_model()
        return self.key.text().strip() if m and m.needs_pairing_key else ""


class KeyCaptureDialog(QDialog):
    """Gets a pairing key: the app poses as the scanner until the vendor software on another computer
    sends the key. Runs in the background; Cancel stops it."""

    finished_capture = Signal(object)  # key (str), None (cancelled) or the error (Exception)

    def __init__(
        self, locator: ScannerLocator, model: catalog.ScannerModel, name: str = "", parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.locator, self.scanner = locator, model
        self.key = ""
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.setWindowTitle("Get Pairing Key")
        steps = QLabel(
            f"1. Switch the {model.full_name} off.\n"
            "2. Enter its device name (on the label: model and serial number), then click Start.\n"
            "3. Open ScanSnap Home on another computer in the same network. It finds ScanSnack instead of the "
            "scanner and sends the key.\n"
            "4. Switch the scanner on again."
        )
        steps.setWordWrap(True)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText(f"e.g. {model.name}-A0B1C2D3E4")
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setTextVisible(False)
        self.spinner.setMaximumHeight(10)
        self.spinner.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.start_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.start_button.setText("Start")
        self.buttons.accepted.connect(self._start)
        self.buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(steps)
        lay.addWidget(QLabel("Device name:"))
        lay.addWidget(self.name)
        lay.addWidget(self.info)
        lay.addWidget(self.spinner)
        lay.addWidget(self.buttons)
        self.setMinimumWidth(460)
        self.finished_capture.connect(self._done)

    def _start(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.info.setText("Enter the device name first.")
            return
        self.cancel.clear()
        self.spinner.show()
        self.start_button.setEnabled(False)
        self.name.setEnabled(False)
        self.info.setText("Waiting for ScanSnap Home … (keep the scanner switched off)")

        def run() -> None:
            try:
                result: object = self.locator.capture_pairing_key(self.scanner.id, name, self.cancel)
            except Exception as e:  # noqa: BLE001 – shown in the dialog
                result = e
            self.finished_capture.emit(result)

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    @Slot(object)
    def _done(self, result: object) -> None:
        self.spinner.hide()
        self.start_button.setEnabled(True)
        self.name.setEnabled(True)
        if isinstance(result, str) and result:
            self.key = result
            self.accept()
        elif isinstance(result, OSError):
            self.info.setText(
                f"Could not pose as the scanner ({result}). Another program may use the ports 52217, 53218 "
                "or 53219; quit ScanSnap Home on this computer."
            )
        elif isinstance(result, Exception):
            self.info.setText(f"Getting the key failed: {result}")
        elif not self.cancel.is_set():
            self.info.setText("No key received.")

    def reject(self) -> None:
        self.cancel.set()  # the background capture ends within a moment
        super().reject()


class PageList(QListWidget):
    """Page overview. In icon mode Qt only moves the icons when dragging; here the pages are really reordered."""

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
        event.setDropAction(Qt.DropAction.IgnoreAction)  # otherwise Qt removes the source
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
    """Pages of a document in the app: an unsaved scan (draft) or a saved PDF."""

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
    reachable_checked = Signal(bool)  # result of the check at startup (from a background thread)

    def __init__(self, library: OutputPort, locator: ScannerLocator, updates: Updates | None = None):
        super().__init__()
        self.settings = QSettings("scansnap-bridge", "app")
        self.setWindowTitle(APP_NAME)
        self.resize(1240, 760)
        self.library = library
        self.locator = locator
        self.docs: dict[str, Document] = {}
        self.drafts: list[Document] = []  # unsaved scans, newest first
        self.current: Document | None = None
        self.scan_target: Document | None = None
        self.scanning = False

        # --- left: presets and scan button -----------------------------------------
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

        # --- document list ---------------------------------------------------------
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

        # --- pages -----------------------------------------------------------------
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

        # --- bottom: saving --------------------------------------------------------
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

        # --- toolbar and menu ------------------------------------------------------
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
        self.updates = updates
        self.update_checker: UpdateChecker | None = None
        if updates is not None:
            self.update_checker = checker = UpdateChecker(self, updates, self.settings, self.restart_for_update)
            help_menu = self.menuBar().addMenu("Help")
            check = self._action("Check for Updates …", None, lambda: checker.check(manual=True))
            check.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)  # macOS: into the app menu
            help_menu.addAction(check)
            auto = QAction("Check for Updates on Start", self, checkable=True)
            auto.setChecked(checker.check_on_start)
            auto.toggled.connect(checker.set_check_on_start)
            help_menu.addAction(auto)
        self.pages.itemSelectionChanged.connect(self._update)

        self.statusBar().showMessage("Ready.")
        self._preset_changed(self.presets.currentRow())

        # --- scan thread ---------------------------------------------------------------
        self.scan_thread = QThread(self)
        ip = str(self.settings.value("ip", ""))
        # before the model choice existed, only the iX1500 could be connected
        model = str(self.settings.value("model", catalog.DEFAULT_MODEL if ip else ""))
        self.worker = ScanWorker(ip, locator, model, str(self.settings.value("pairing_key", "")))
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
        # Keep the session open: only then does the scanner report a press of "Scan" on the touch panel
        self.reconnect_timer = QTimer(self, interval=RECONNECT_MS)
        self.reconnect_timer.timeout.connect(self._keep_connected)
        self.reconnect_timer.start()

    # --- helpers ----------------------------------------------------------------------
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
        # Light/dark switched: recolor the entries (only once the window is fully built)
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
        # during a scan only the target is locked; other documents stay editable
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
        """Apply the order and content of the page view to the current document."""
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

    # --- document list -----------------------------------------------------------------
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
        return _page_icon(b"", 0, DOC_THUMB)  # empty canvas keeps the text aligned

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
        """Every scan becomes a document of its own (at the top of the list)."""
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
        m = catalog.by_id(self.worker.model)
        name = f"{m.full_name}{' (beta)' if m.support == 'beta' else ''} at " if m else ""
        self.scanner_label.setText(f"Scanner: {name}{self.worker.ip}" if self.worker.ip else "No scanner connected")

    def ask_scanner(self, message: str = "") -> bool:
        dialog = ScannerDialog(
            self.worker.ip, self.locator, message, self, model=self.worker.model, pairing_key=self.worker.pairing_key
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        self.worker.ip = dialog.value()
        self.worker.model = dialog.model_id()
        self.worker.pairing_key = dialog.pairing_key()
        self.settings.setValue("ip", self.worker.ip)
        self.settings.setValue("model", self.worker.model)
        self.settings.setValue("pairing_key", self.worker.pairing_key)
        self._show_scanner()
        self.statusBar().showMessage(f"Scanner: {self.worker.ip}")
        self.request_connect.emit()
        return True

    def check_scanner(self) -> None:
        """At startup: without a saved or reachable IP, show the scanner dialog first.
        The check runs in the background (never network I/O in the GUI thread)."""
        m = catalog.by_id(self.worker.model)
        if not self.worker.ip or m is None or not m.usable:
            self.ask_scanner(
                "Welcome! Connect your ScanSnap first: choose its model, then enter its IP address "
                "or search the network."
            )
            return
        ip = self.worker.ip
        threading.Thread(target=lambda: self.reachable_checked.emit(self.locator.reachable(ip)), daemon=True).start()

    @Slot(bool)
    def _reachable_checked(self, ok: bool) -> None:
        if ok:
            self.request_connect.emit()  # connect in the background, the first scan then starts right away
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
        """The "Scan" button on the touch panel: like the Scan button in the window, with the settings on the left."""
        if self.scanning:
            return
        self.statusBar().showMessage("Scan started on the scanner …")
        self.start_scan()

    def start_scan(self) -> None:
        if not (self.worker.ip and self.worker.model) and not self.ask_scanner():
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
        if target is not None and not target.pages:  # nothing scanned: remove the draft again
            self._drop_draft(target)
            if self.current is target:
                self._select(self.drafts[0] if self.drafts else None)
        elif target is not None:
            # save right away – including the pages before a paper jam
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

    # --- pages ------------------------------------------------------------------------
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

    # --- saving -----------------------------------------------------------------------
    def _store_draft(self, draft: Document, name: str, *, select: bool, open_after: bool) -> Document | None:
        """Saves a draft as PDF and replaces it in the list with the saved document."""
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
        saved.pages = [ScanPage(p.rendered(), p.dpi) for p in draft.pages]  # rotations are baked into the PDF
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
        if doc.entry is None:  # only if the automatic save after the scan failed
            self._store_draft(doc, self.name.text(), select=True, open_after=False)
            return
        try:
            entry = self.library.update(doc.entry, doc.pages, self.name.text())
            saved, open_after = doc, False
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Save", f"Saving failed: {e}")
            return
        # rotations are now baked into the PDF: take the pages as saved
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

    def check_updates_on_start(self) -> None:
        checker = self.update_checker
        if checker is not None and self.updates is not None and self.updates.check_on_start and checker.check_on_start:
            checker.check()

    def restart_for_update(self, staged: Path) -> None:
        """New version is unpacked: close the app (asks about unsaved changes), start the helper."""
        assert self.updates is not None
        if not self.close():
            self.statusBar().showMessage("Update postponed: ScanSnack was not closed.")
            return
        try:
            self.updates.installer.restart_into(staged)
        except OSError as e:
            QMessageBox.warning(None, "Software Update", f"Could not start the update: {e}")
        QApplication.quit()

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
    """Location for app data (document list, thumbnails); needs a running QApplication."""
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    adopt_previous(path, PREVIOUS_APP_NAME)
    return path


def adopt_previous(path: Path, previous_name: str) -> None:
    """Takes over the app's data folder under its old name as long as there is no new one yet."""
    old = path.with_name(previous_name)
    if not path.exists() and old.is_dir():
        old.rename(path)


def run(
    locator: ScannerLocator,
    make_output: Callable[[Path], OutputPort],
    ip: str | None = None,
    qt_args: list[str] | None = None,
    updates: Updates | None = None,
) -> int:
    """Starts the UI. ``make_output`` gets the app data directory and returns the document library."""
    app = QApplication([sys.argv[0], *(qt_args or [])])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("scansnap-bridge")
    if ip:
        QSettings("scansnap-bridge", "app").setValue("ip", ip)
    primer.apply()
    window = MainWindow(make_output(data_dir()), locator, updates)
    window.show()
    QTimer.singleShot(200, window.check_scanner)
    QTimer.singleShot(1500, window.check_updates_on_start)
    return app.exec()
