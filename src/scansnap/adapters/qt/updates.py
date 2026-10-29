"""Update-Prüfung beim Start und „Check for Updates …“: Dialog mit Install and Restart / Later / Skip.

Abfrage und Download laufen in Hintergrund-Threads; die Ergebnisse kommen per Signal in den GUI-Thread.
"Later" fragt beim nächsten Start wieder, "Skip This Version" erst bei der nächsten neueren Version.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.update import Asset, Release, Version, format_version, is_newer, pick_asset
from ...ports.updates import Installer, ReleaseSource

SKIP_KEY = "update/skip"
AUTO_KEY = "update/check_on_start"


@dataclass(frozen=True)
class Updates:
    """Was die App zum Aktualisieren braucht (verdrahtet in ``app/desktop.py``)."""

    source: ReleaseSource
    installer: Installer
    current: Version
    os: str
    arch: str
    check_on_start: bool = True  # False: nur über das Menü (z. B. beim Start aus dem Quellcode)


class UpdateDialog(QDialog):
    """Neue Version anbieten; installiert auf Wunsch mit Fortschrittsbalken."""

    progress = Signal(int, int)
    prepared = Signal(object, str)  # entpackte App (Path) oder None, Fehlermeldung

    def __init__(self, release: Release, updates: Updates, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self.release = release
        self.updates = updates
        self.settings = settings
        self.asset: Asset | None = pick_asset(release, updates.os, updates.arch)
        self.staged: Path | None = None
        self.setWindowTitle("Software Update")
        self.setMinimumWidth(480)
        title = QLabel(f"<b>ScanSnack {release.label} is available</b>")
        info = QLabel(f"You have version {format_version(updates.current)}.")
        info.setProperty("muted", True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(release.notes or "No release notes.")
        self.notes.setMaximumHeight(220)
        self.blocker = self._blocker()
        self.hint = QLabel(self.blocker or "")
        self.hint.setWordWrap(True)
        self.hint.setVisible(bool(self.blocker))
        self.bar = QProgressBar()
        self.bar.hide()
        self.buttons = QDialogButtonBox()
        self.skip_button = QPushButton("Skip This Version")
        self.later_button = QPushButton("Later")
        self.install_button = QPushButton("Download …" if self.blocker else "Install and Restart")
        self.buttons.addButton(self.skip_button, QDialogButtonBox.ButtonRole.DestructiveRole)
        self.buttons.addButton(self.later_button, QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.addButton(self.install_button, QDialogButtonBox.ButtonRole.AcceptRole)
        self.install_button.setDefault(True)
        self.skip_button.clicked.connect(self.skip)
        self.later_button.clicked.connect(self.reject)
        self.install_button.clicked.connect(self.install)
        lay = QVBoxLayout(self)
        lay.addWidget(title)
        lay.addWidget(info)
        lay.addWidget(self.notes)
        lay.addWidget(self.hint)
        lay.addWidget(self.bar)
        lay.addWidget(self.buttons)
        self.progress.connect(self._progress)
        self.prepared.connect(self._prepared)

    def _blocker(self) -> str | None:
        if self.asset is None:
            return f"This release has no package for {self.updates.os} ({self.updates.arch})."
        if not self.asset.sha256:
            return "This release has no published checksum."
        return self.updates.installer.blocker()

    @Slot()
    def skip(self) -> None:
        self.settings.setValue(SKIP_KEY, self.release.label)
        self.reject()

    @Slot()
    def install(self) -> None:
        if self.blocker or self.asset is None:
            QDesktopServices.openUrl(QUrl(self.release.page_url))
            self.reject()
            return
        for b in (self.skip_button, self.later_button, self.install_button):
            b.setEnabled(False)
        self.hint.setText(f"Downloading {self.asset.name} …")
        self.hint.show()
        self.bar.setRange(0, 0)
        self.bar.show()
        asset = self.asset
        installer = self.updates.installer

        def work() -> None:
            try:
                self.prepared.emit(installer.prepare(asset, self.progress.emit), "")
            except Exception as e:  # noqa: BLE001 – Fehler gehen als Meldung an die Oberfläche
                traceback.print_exc()
                self.prepared.emit(None, str(e))

        threading.Thread(target=work, daemon=True).start()

    @Slot(int, int)
    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))

    @Slot(object, str)
    def _prepared(self, staged: Path | None, error: str) -> None:
        if staged is None:
            self.bar.hide()
            self.hint.setText(f"The update failed: {error}")
            for b in (self.later_button, self.install_button):
                b.setEnabled(True)
            self.install_button.setText("Try Again")
            return
        self.staged = staged
        self.hint.setText("Installing and restarting …")
        self.accept()


class UpdateChecker(QObject):
    """Fragt die Release-Quelle im Hintergrund ab und zeigt bei Bedarf den Dialog."""

    checked = Signal(object, str, bool)  # Release | None, Fehlermeldung, von Hand gestartet?

    def __init__(self, window: QWidget, updates: Updates, settings: QSettings, restart: Callable[[Path], None]):
        super().__init__(window)
        self.window = window
        self.updates = updates
        self.settings = settings
        self.restart = restart
        self.dialog: UpdateDialog | None = None
        self.checked.connect(self._checked)

    @property
    def check_on_start(self) -> bool:
        return self.settings.value(AUTO_KEY, "true") == "true"

    def set_check_on_start(self, on: bool) -> None:
        self.settings.setValue(AUTO_KEY, "true" if on else "false")

    def check(self, *, manual: bool = False) -> None:
        source = self.updates.source

        def work() -> None:
            try:
                self.checked.emit(source.latest(), "", manual)
            except Exception as e:  # noqa: BLE001 – offline o. Ä.: beim Start still
                self.checked.emit(None, str(e), manual)

        threading.Thread(target=work, daemon=True).start()

    @Slot(object, str, bool)
    def _checked(self, release: Release | None, error: str, manual: bool) -> None:
        if self.dialog is not None:
            return
        current = self.updates.current
        if error or release is None or not is_newer(release, current):
            if manual:
                text = (
                    f"Could not check for updates: {error}"
                    if error
                    else f"ScanSnack {format_version(current)} is the latest version."
                )
                QMessageBox.information(self.window, "Software Update", text)
            return
        if not manual and self.settings.value(SKIP_KEY, "") == release.label:
            return
        self.show_dialog(release)

    def show_dialog(self, release: Release) -> UpdateDialog:
        dialog = UpdateDialog(release, self.updates, self.settings, self.window)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)

        def closed(_result: int) -> None:
            self._closed(dialog)

        dialog.finished.connect(closed)
        self.dialog = dialog
        dialog.open()
        return dialog

    def _closed(self, dialog: UpdateDialog) -> None:
        self.dialog = None
        if dialog.staged is not None:
            self.restart(dialog.staged)
