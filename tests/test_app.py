from PySide6.QtWidgets import QSystemTrayIcon

from pc_sound_recorder.app import TrayApplication
from pc_sound_recorder.config import Config
INFO = QSystemTrayIcon.MessageIcon.Information
WARNING = QSystemTrayIcon.MessageIcon.Warning
CRITICAL = QSystemTrayIcon.MessageIcon.Critical


class FakeTray:
    def __init__(self):
        self.calls = []

    def showMessage(self, title, message, icon, msecs):
        self.calls.append((title, message, icon, msecs))


def _app(notifications: bool) -> TrayApplication:
    app = TrayApplication.__new__(TrayApplication)
    app.config = Config(output_dir="/tmp", notifications=notifications)
    app.tray = FakeTray()
    return app


def test_notify_suppresses_only_information_when_disabled():
    app = _app(notifications=False)
    app._notify("Info", "text", INFO)
    assert app.tray.calls == []
    app._notify("Warnung", "text", WARNING)
    app._notify("Fehler", "text", CRITICAL)
    assert [call[0] for call in app.tray.calls] == ["Warnung", "Fehler"]


def test_notify_shows_everything_when_enabled():
    app = _app(notifications=True)
    app._notify("Info", "text", INFO)
    app._notify("Warnung", "text", WARNING)
    assert [call[0] for call in app.tray.calls] == ["Info", "Warnung"]


# --- Hotkey-Kollision im Einstellungsdialog ---

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

import pc_sound_recorder.app as app_module
from pc_sound_recorder.app import SettingsDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class _FakeVoicesThread(QObject):
    """Steht in Tests für VoicesThread: kein Subprocess, keine Netzwerkstelle."""

    loaded = Signal(list, object)
    finished = Signal()

    def start(self):
        pass

    def deleteLater(self):
        pass


def _dialog(qapp, monkeypatch) -> SettingsDialog:
    monkeypatch.setattr(app_module, "VoicesThread", _FakeVoicesThread)
    return SettingsDialog(Config(output_dir="/tmp"))


def test_settings_rejects_identical_hotkeys(qapp, monkeypatch):
    dialog = _dialog(qapp, monkeypatch)
    # Vorlese-Hotkey auf dieselbe Kombination wie die Aufnahme stellen.
    dialog.tts_trigger.setCurrentIndex(dialog.trigger.currentIndex())
    dialog.tts_modifier.setCurrentText(dialog.modifier.currentText())
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args: warnings.append(args))
    )
    dialog.accept()
    assert warnings, "Kollision muss eine Warnung auslösen"
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_settings_accepts_distinct_hotkeys(qapp, monkeypatch):
    dialog = _dialog(qapp, monkeypatch)
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args: pytest.fail("keine Kollision erwartet")),
    )
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
