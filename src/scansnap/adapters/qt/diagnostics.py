"""Dialog "Report Unsupported Scanner": run the diagnosis, show the report, save it and send it by email."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.diagnostics import DiagnosticReport, mail_url
from ...ports.diagnostics import Diagnostics

INTRO = (
    "Is your ScanSnap model not supported yet? This check asks the scanner the questions the app knows and "
    "records the raw answers, so that support for your model can be developed from them.\n\n"
    "It only asks, it never changes anything: no pairing, no settings, no scan. IP and MAC addresses, the "
    "serial number and the device name are replaced by placeholders. Nothing is sent automatically – you "
    "save the report, can read it below, and send it by email."
)


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))


class DiagnosticsDialog(QDialog):
    progress = Signal(str)
    finished_run = Signal(object)  # DiagnosticReport

    def __init__(self, ip: str, diagnostics: Diagnostics, recipient: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.diagnostics, self.recipient = diagnostics, recipient
        self.report: DiagnosticReport | None = None
        self.saved: Path | None = None
        self.setWindowTitle("Report Unsupported Scanner")
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        self.ip = QLineEdit(ip)
        self.ip.setPlaceholderText("e.g. 192.168.1.20")
        self.model = QLineEdit()
        self.model.setPlaceholderText("as printed on the scanner, e.g. iX500")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional: what you tried and what happened")
        self.notes.setMaximumHeight(70)
        form = QFormLayout()
        form.addRow("Scanner IP address:", self.ip)
        form.addRow("Model:", self.model)
        form.addRow("Notes:", self.notes)
        self.run_button = QPushButton("Run Check")
        self.run_button.clicked.connect(self._run)
        self.status = QLabel("")
        self.spinner = QProgressBar()
        self.spinner.setRange(0, 0)
        self.spinner.setTextVisible(False)
        self.spinner.setMaximumHeight(10)
        self.spinner.hide()
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.view.setPlaceholderText("The report appears here.")
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.save_button = self.buttons.addButton("Save …", QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.clicked.connect(self.save)
        self.mail_button = self.buttons.addButton("Send by Email …", QDialogButtonBox.ButtonRole.ActionRole)
        self.mail_button.clicked.connect(self.send)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(form)
        lay.addWidget(self.run_button)
        lay.addWidget(self.status)
        lay.addWidget(self.spinner)
        lay.addWidget(self.view, 1)
        lay.addWidget(self.buttons)
        self.resize(720, 640)
        self.progress.connect(self.status.setText)
        self.finished_run.connect(self._done)
        self._enable(running=False)

    def _enable(self, *, running: bool) -> None:
        self.spinner.setVisible(running)
        self.run_button.setEnabled(not running)
        self.ip.setEnabled(not running)
        for b in (self.save_button, self.mail_button):
            b.setEnabled(not running and self.report is not None)

    def _run(self) -> None:
        ip = self.ip.text().strip()
        if not ip:
            self.status.setText("Enter the scanner's IP address first.")
            return
        self.report, self.saved = None, None
        self.view.clear()
        self._enable(running=True)
        threading.Thread(
            target=lambda: self.finished_run.emit(self.diagnostics.run(ip, self.progress.emit)), daemon=True
        ).start()

    @Slot(object)
    def _done(self, report: DiagnosticReport) -> None:
        self.report = report
        self.view.setPlainText(report.to_json())
        found = report.summary.get("model") or report.summary.get("inquiry") or "no model reported"
        self.status.setText(f"Done ({found}). Check the report below, then save it or send it by email.")
        self._enable(running=False)

    def current(self) -> DiagnosticReport | None:
        """The report with model and notes as currently entered."""
        if self.report is None:
            return None
        return dataclasses.replace(self.report, model=self.model.text().strip(), notes=self.notes.toPlainText().strip())

    def save(self) -> Path | None:
        report = self.current()
        if report is None:
            return None
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation) or str(Path.home())
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Report", str(Path(folder) / report.file_name()), "JSON (*.json)"
        )
        if not name:
            return None
        path = Path(name)
        try:
            path.write_text(report.to_json() + "\n", encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Save Report", f"Could not save the report: {e}")
            return None
        self.saved = path
        self.status.setText(f"Saved to {path}.")
        return path

    def send(self) -> None:
        """Saves the report (mailto cannot attach files), opens a prepared email and shows the file to attach."""
        report = self.current()
        if report is None:
            return
        path = self.saved
        if path is not None:
            try:
                path.write_text(report.to_json() + "\n", encoding="utf-8")  # model or notes may have changed
            except OSError:
                path = None
        path = path or self.save()
        if path is None:
            return
        QDesktopServices.openUrl(QUrl(mail_url(report, str(path), self.recipient)))
        reveal(path)
        self.status.setText(f"Attach {path.name} to the email and send it to {self.recipient}.")
