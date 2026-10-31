"""Update dialog: Later / Skip This Version / Install and Restart, with a fake source and installer."""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from scansnap.adapters.qt.updates import SKIP_KEY, UpdateChecker, UpdateDialog, Updates
from scansnap.core.update import Asset, Release

NAME = "ScanSnack-0.3.0-linux-x86_64.tar.gz"


def make_release(sha: str | None = "a" * 64) -> Release:
    return Release((0, 3, 0), "v0.3.0", "https://example.invalid/r", "**New**", (Asset(NAME, "u", 3, sha),))


class FakeSource:
    def __init__(self, release: Release | None):
        self.release = release

    def latest(self) -> Release | None:
        return self.release


class FakeInstaller:
    def __init__(self, blocked: str | None = None):
        self.blocked = blocked
        self.prepared: list[Asset] = []

    def blocker(self) -> str | None:
        return self.blocked

    def prepare(self, asset: Asset, progress: Callable[[int, int], None]) -> Path:
        self.prepared.append(asset)
        progress(3, 3)
        return Path("/staged/ScanSnack")

    def restart_into(self, staged: Path) -> None:
        raise AssertionError("not called by the dialog")


def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[QSettings]:
    app()
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    yield s


def updates(release: Release | None, installer: FakeInstaller | None = None) -> Updates:
    return Updates(FakeSource(release), installer or FakeInstaller(), (0, 2, 0), "linux", "x86_64")


def wait_for(cond: Callable[[], bool]) -> None:
    for _ in range(200):
        app().processEvents()
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("timeout")


def checker(settings: QSettings, u: Updates, restarted: list[Path]) -> UpdateChecker:
    return UpdateChecker(QWidget(), u, settings, restarted.append)


def test_newer_release_offers_install_and_restarts(settings: QSettings) -> None:
    installer = FakeInstaller()
    restarted: list[Path] = []
    c = checker(settings, updates(make_release(), installer), restarted)
    c.check()
    wait_for(lambda: c.dialog is not None)
    dialog = c.dialog
    assert isinstance(dialog, UpdateDialog) and dialog.install_button.text() == "Install and Restart"
    dialog.install_button.click()
    wait_for(lambda: bool(restarted))
    assert restarted == [Path("/staged/ScanSnack")] and installer.prepared[0].name == NAME
    assert c.dialog is None


def test_later_asks_again_skip_does_not(settings: QSettings) -> None:
    restarted: list[Path] = []
    c = checker(settings, updates(make_release()), restarted)
    c.check()
    wait_for(lambda: c.dialog is not None)
    assert c.dialog is not None
    c.dialog.later_button.click()
    assert c.dialog is None and not restarted
    c.check()  # next start: asked again
    wait_for(lambda: c.dialog is not None)
    assert c.dialog is not None
    c.dialog.skip_button.click()
    assert settings.value(SKIP_KEY) == "0.3.0"
    c._checked(make_release(), "", False)  # pyright: ignore[reportPrivateUsage]
    assert c.dialog is None  # skipped
    c._checked(make_release(), "", True)  # pyright: ignore[reportPrivateUsage]
    assert c.dialog is not None  # checked by hand: offer it anyway
    c.dialog.reject()


def test_no_update_and_errors(settings: QSettings, monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[str] = []

    def info(_parent: object, _title: str, text: str) -> None:
        shown.append(text)

    monkeypatch.setattr(QMessageBox, "information", info)
    c = checker(settings, updates(None), [])
    c._checked(None, "offline", False)  # pyright: ignore[reportPrivateUsage]
    c._checked(Release((0, 2, 0), "v0.2.0", "", "", ()), "", False)  # pyright: ignore[reportPrivateUsage]
    assert not shown and c.dialog is None  # silent at startup
    c._checked(None, "offline", True)  # pyright: ignore[reportPrivateUsage]
    c._checked(None, "", True)  # pyright: ignore[reportPrivateUsage]
    assert shown == ["Could not check for updates: offline", "ScanSnack 0.2.0 is the latest version."]


@pytest.mark.parametrize(
    ("release", "blocked", "hint"),
    [
        (make_release(), "Move it first.", "Move it first."),
        (make_release(sha=None), None, "no published checksum"),
        (Release((0, 3, 0), "v0.3.0", "", "", ()), None, "no package for linux"),
    ],
)
def test_blocked_install_offers_download(settings: QSettings, release: Release, blocked: str | None, hint: str) -> None:
    dialog = UpdateDialog(release, updates(release, FakeInstaller(blocked)), settings)
    assert dialog.install_button.text() == "Download …" and hint in dialog.hint.text()


class RecordingInstaller(FakeInstaller):
    def __init__(self) -> None:
        super().__init__()
        self.restarted: list[Path] = []

    def restart_into(self, staged: Path) -> None:
        self.restarted.append(staged)


def test_window_closes_and_hands_over_to_installer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scansnap.adapters.library.library import Library
    from scansnap.adapters.qt import window as app_main

    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    monkeypatch.setattr(QApplication, "quit", lambda: None)
    installer = RecordingInstaller()

    class NoScanner:
        def discover(self, timeout: float = 3.0) -> list[object]:
            return []

        def reachable(self, ip: str, timeout: float = 1.5) -> bool:
            return False

        def open(
            self, ip: str, *, model: str, pairing_key: str = "", on_button: Callable[[], None] | None = None
        ) -> object:
            raise AssertionError

    app()
    w = app_main.MainWindow(Library(tmp_path / "library.json"), NoScanner(), updates(make_release(), installer))  # type: ignore[arg-type]
    titles = [a.text() for a in w.menuBar().actions()]
    assert "Help" in titles and w.update_checker is not None
    w.show()
    w.restart_for_update(Path("/staged/ScanSnack"))
    assert installer.restarted == [Path("/staged/ScanSnack")] and not w.isVisible()
