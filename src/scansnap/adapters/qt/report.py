"""Error report window: shows the report (editable), sends it with the user's mail program, copies or saves it."""

from __future__ import annotations

from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QUrl, qVersion
from PySide6.QtGui import QDesktopServices, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.report import REPORT_ADDRESS, mailto

INTRO = (
    "<b>Send an error report</b><br>This report helps to find and fix the problem. Personal data "
    "(IP and MAC addresses, serial numbers, computer and user names, folders, file names, passwords) has "
    "been removed. Please check the text below; you can change or delete anything before sending.<br><br>"
    f"<b>Send by Email</b> opens your mail program with a new message to {REPORT_ADDRESS}. Nothing is "
    "sent until you send that message yourself."
)
WHAT_HAPPENED = "What did you do when the error happened? (optional, please write here)\n\n\n"


def qt_fields() -> dict[str, str]:
    return {"Qt": qVersion(), "PySide6": pyside_version}


class ReportDialog(QDialog):
    def __init__(self, report: str, subject: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Error Report")
        self.subject = subject
        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        self.text = QPlainTextEdit(WHAT_HAPPENED + report)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.send_button = QPushButton("Send by Email …")
        self.send_button.setDefault(True)
        copy = QPushButton("Copy")
        save = QPushButton("Save …")
        buttons.addButton(self.send_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.ActionRole)
        self.send_button.clicked.connect(self.send)
        copy.clicked.connect(self.copy)
        save.clicked.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.status)
        lay.addWidget(buttons)
        self.resize(760, 620)

    def report(self) -> str:
        return self.text.toPlainText()

    def copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(self.report())
        self.status.setText("Report copied to the clipboard.")

    def send(self) -> None:
        mail = mailto(self.report(), self.subject)
        if not mail.complete:
            QGuiApplication.clipboard().setText(self.report())
        if not QDesktopServices.openUrl(QUrl(mail.url, QUrl.ParsingMode.TolerantMode)):
            self.copy()
            self.status.setText(
                f"No mail program opened. The report is on the clipboard: please paste it into an email to "
                f"{REPORT_ADDRESS}, or save it and attach the file."
            )
        elif mail.complete:
            self.status.setText("Your mail program has the message ready. Please send it there.")
        else:
            self.status.setText(
                "Your mail program has the message ready. The report was too long for it: the full report is on "
                "the clipboard, please paste it into the message in place of the shortened text."
            )

    def save(self) -> None:
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Error Report", str(Path.home() / "ScanSnack error report.txt"), "Text (*.txt)"
        )
        if not name:
            return
        try:
            Path(name).write_text(self.report(), encoding="utf-8")
        except OSError as e:
            self.status.setText(f"Saving failed: {e}")
            return
        self.status.setText(f"Saved. You can attach the file to an email to {REPORT_ADDRESS}.")
