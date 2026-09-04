from types import SimpleNamespace

from PySide6.QtWidgets import QSystemTrayIcon

from pc_sound_recorder import app as app_modul
from pc_sound_recorder.app import TrayApplication
from pc_sound_recorder.config import Config
from pc_sound_recorder.hotkey import EVDEV_MISSING
INFO = QSystemTrayIcon.MessageIcon.Information
WARNING = QSystemTrayIcon.MessageIcon.Warning
CRITICAL = QSystemTrayIcon.MessageIcon.Critical


class FakeTray:
    def __init__(self):
        self.calls = []
        self.icon = None
        self.tooltip = None

    def showMessage(self, title, message, icon, msecs):
        self.calls.append((title, message, icon, msecs))

    def setIcon(self, icon):
        self.icon = icon

    def setToolTip(self, tooltip):
        self.tooltip = tooltip


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


class FakeRecording:
    """Steht für stt.Recording: kein pw-record, aber derselbe Zustand.

    Kein Faden — Recording ist auch im Ernstfall keiner, es hält nur den
    Kindprozess. Die Erkennung dagegen läuft in einem echten QThread und wird
    in test_stt.py auch als solcher geprüft.
    """

    def __init__(self, path="/tmp/diktat-test.wav"):
        import pathlib
        self.path = pathlib.Path(path)
        self.is_recording = False
        self.duration = 1.0
        self.cancelled = 0
        self.started = 0

    def start(self):
        self.is_recording = True
        self.started += 1

    def stop(self):
        self.is_recording = False
        return self.duration

    def cancel(self):
        self.is_recording = False
        self.cancelled += 1


class FakeAction:
    def __init__(self):
        self.text = None

    def setText(self, text):
        self.text = text


class FakeTimer:
    """Steht für den QTimer der Diktat-Leerlauffrist. Merkt sich, was er täte."""

    def __init__(self):
        self.interval = None
        self.stops = 0

    def stop(self):
        self.interval = None
        self.stops += 1

    def start(self, interval):
        self.interval = interval


def _tray_app_without_gui() -> TrayApplication:
    """TrayApplication ohne Qt-Aufbau, nur so weit wie `_refresh` es braucht."""
    app = _app(notifications=True)
    app.recorder = SimpleNamespace(is_recording=False)
    app.speech = None
    app.stt = None
    app.dictation = FakeRecording()
    app._last_stt_status = None
    app.hotkey = None
    app._hotkey_failed = False
    app._hotkey_missing = set()
    app.icon_idle = "gruen"
    app.icon_disabled = "grau"
    app.icon_dictating = "orange"
    app.icon_recording = "rot"
    app.icon_speaking = "blau"
    app.status_action = FakeAction()
    app._stt_release_timer = FakeTimer()
    return app


def test_hotkey_error_greys_icon_and_stays_grey():
    app = _tray_app_without_gui()
    app._refresh()
    assert app.tray.icon == "gruen"
    app.hotkey_error("Keine passende Tastatur lesbar")
    assert app.tray.icon == "grau"
    assert app.status_action.text == "Hotkey nicht verfügbar: Keine passende Tastatur lesbar"
    # Der nächste Auffrischer darf nicht wieder Bereit melden — und den Grund
    # nicht verlieren, sonst bleibt nach der Blase nur "nicht verfügbar".
    app._refresh()
    assert app.tray.icon == "grau"
    assert app.status_action.text.endswith("Keine passende Tastatur lesbar")


def test_missing_evdev_package_stays_readable_in_the_tray():
    """Die Blase vergeht; Statuszeile und Tooltip müssen den Befehl behalten."""
    app = _tray_app_without_gui()
    app.hotkey_error(EVDEV_MISSING)
    app._refresh()
    assert app.tray.icon == "grau"
    assert "sudo pacman -S --asexplicit python-evdev" in app.status_action.text
    assert "python-evdev" in app.tray.tooltip


def test_restart_hotkey_clears_the_failure_flag():
    app = _tray_app_without_gui()
    app.config.enabled = False
    app.config.tts_enabled = False
    # Auch das Abbrechen aus: sonst startet `_restart_hotkey` einen echten
    # evdev-Faden, und der Test prüft nur die Merker.
    app.config.stop_enabled = False
    app._hotkey_failed = True
    app._hotkey_missing = {"tts"}
    app._restart_hotkey()
    assert app._hotkey_failed is False
    assert app._hotkey_missing == set()


def test_partial_hotkey_failure_warns_and_names_only_working_shortcuts():
    app = _tray_app_without_gui()
    app._refresh()
    assert app.status_action.text == (
        "Bereit: Meta+F8 Aufnahme · Meta+Rollen Vorlesen · Meta+Y Abbrechen"
    )
    app.hotkey_degraded(["tts"])
    # Grün bleibt grün: die Aufnahme ist intakt, nur das Vorlesen fällt weg.
    assert app.tray.icon == "gruen"
    assert app.status_action.text == "Bereit: Meta+F8 Aufnahme · Meta+Y Abbrechen"
    title, message, icon, _msecs = app.tray.calls[-1]
    assert icon == WARNING
    assert "Meta+Rollen (Vorlesen)" in message
    # Auch nach dem nächsten Auffrischer bleibt das tote Kürzel draußen.
    app._refresh()
    assert app.status_action.text == "Bereit: Meta+F8 Aufnahme · Meta+Y Abbrechen"


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


def test_settings_dialog_shows_the_figure(qapp, monkeypatch):
    # GIVEN ein frischer Einstellungsdialog (Repo enthält packaging/<APP_ID>.png):
    dialog = _dialog(qapp, monkeypatch)

    # WHEN wir das Figuren-Label betrachten,
    # THEN trägt es ein geladenes Bild — Theme-Icon oder Repo-PNG als Rückfall:
    assert dialog.figure.pixmap() is not None
    assert not dialog.figure.pixmap().isNull()


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


def test_settings_rejects_dictation_colliding_with_read_aloud(qapp, monkeypatch):
    """Drei Funktionen heißen drei Paarvergleiche, nicht einer."""
    dialog = _dialog(qapp, monkeypatch)
    dialog.stt_trigger.setCurrentIndex(dialog.tts_trigger.currentIndex())
    dialog.stt_modifier.setCurrentText(dialog.tts_modifier.currentText())
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args: warnings.append(args))
    )
    dialog.accept()
    assert warnings and "Vorlesen- und Diktat" in warnings[0][2]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_settings_rejects_dictation_colliding_with_recording(qapp, monkeypatch):
    dialog = _dialog(qapp, monkeypatch)
    dialog.stt_trigger.setCurrentIndex(dialog.trigger.currentIndex())
    dialog.stt_modifier.setCurrentText(dialog.modifier.currentText())
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args: warnings.append(args))
    )
    dialog.accept()
    assert warnings and "Aufnahme- und Diktat" in warnings[0][2]


def test_settings_rejects_cancel_colliding_with_read_aloud(qapp, monkeypatch):
    """Das vierte Kürzel geht durch dieselbe Prüfung wie die drei anderen."""
    dialog = _dialog(qapp, monkeypatch)
    dialog.stop_trigger.setCurrentIndex(dialog.tts_trigger.currentIndex())
    dialog.stop_modifier.setCurrentText(dialog.tts_modifier.currentText())
    warnings = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *args: warnings.append(args))
    )
    dialog.accept()
    assert warnings and "Vorlesen- und Abbrechen" in warnings[0][2]
    assert dialog.result() != QDialog.DialogCode.Accepted


def test_settings_catches_every_pair(qapp, monkeypatch):
    """Jedes Paar, nicht nur die mit der Aufnahme: alle sechs werden geprüft."""
    pairs = [
        (("modifier", "trigger"), ("tts_modifier", "tts_trigger")),
        (("modifier", "trigger"), ("stt_modifier", "stt_trigger")),
        (("modifier", "trigger"), ("stop_modifier", "stop_trigger")),
        (("tts_modifier", "tts_trigger"), ("stt_modifier", "stt_trigger")),
        (("tts_modifier", "tts_trigger"), ("stop_modifier", "stop_trigger")),
        (("stt_modifier", "stt_trigger"), ("stop_modifier", "stop_trigger")),
    ]
    for (mod_a, trig_a), (mod_b, trig_b) in pairs:
        dialog = _dialog(qapp, monkeypatch)
        getattr(dialog, trig_b).setCurrentIndex(getattr(dialog, trig_a).currentIndex())
        getattr(dialog, mod_b).setCurrentText(getattr(dialog, mod_a).currentText())
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning", staticmethod(lambda *args: warnings.append(args))
        )
        dialog.accept()
        assert warnings, f"{trig_a} == {trig_b} blieb unbemerkt"
        assert dialog.result() != QDialog.DialogCode.Accepted


def test_settings_apply_writes_the_cancel_fields(qapp, monkeypatch):
    dialog = _dialog(qapp, monkeypatch)
    dialog.stop_enabled.setChecked(False)
    dialog.stop_trigger.setCurrentIndex(dialog.stop_trigger.findData("KEY_F11"))
    dialog.stop_modifier.setCurrentText("Strg+Alt")
    config = Config(output_dir="/tmp")
    dialog.apply(config)
    assert config.stop_enabled is False
    assert config.stop_trigger_key == "KEY_F11"
    assert config.stop_modifiers == ("KEY_LEFTCTRL", "KEY_LEFTALT")


def test_settings_apply_writes_the_dictation_fields(qapp, monkeypatch):
    dialog = _dialog(qapp, monkeypatch)
    dialog.stt_enabled.setChecked(True)
    dialog.stt_language.setCurrentIndex(dialog.stt_language.findData(""))
    dialog.stt_threshold.setValue(0.030)
    dialog.stt_clipboard_restore.setChecked(False)
    config = Config(output_dir="/tmp")
    dialog.apply(config)
    assert config.stt_enabled is True
    assert config.stt_trigger_key == "KEY_PAUSE"
    assert config.stt_model == "large-v3-turbo"
    assert config.stt_language == ""
    assert config.stt_threshold == pytest.approx(0.030)
    assert config.stt_clipboard_restore is False


# --- Diktat: Lebenszyklus im Tray ---

from PySide6.QtCore import QEventLoop, QTimer

import pc_sound_recorder.stt as stt_module


def _dictation_app(tmp_path) -> TrayApplication:
    app = _tray_app_without_gui()
    app.config.stt_enabled = True
    # Ohne Wiederherstellung: sonst griffe `finish_dictation` über
    # snapshot_clipboard() auf die echte Zwischenablage des Rechners zu.
    app.config.stt_clipboard_restore = False
    app._stt_clipboard = None
    recording = FakeRecording(tmp_path / "diktat.wav")
    recording.path.write_bytes(b"\0" * 5000)
    app.dictation = recording
    return app


def test_short_press_is_discarded_without_recognition(tmp_path, monkeypatch):
    app = _dictation_app(tmp_path)
    app.dictation.duration = 0.1        # unter stt_min_seconds (0,3)
    monkeypatch.setattr(
        stt_module, "load_model",
        lambda *args, **kwargs: pytest.fail("Verklicker darf kein Modell laden"),
    )
    app.start_dictation()
    assert app.dictation.is_recording and app.tray.icon == "orange"
    app.finish_dictation()
    assert app.stt is None
    assert app.status_action.text == "Diktat zu kurz – verworfen"
    assert not app.dictation.path.exists()


def test_dictation_runs_in_a_real_thread_and_is_collected(qapp, tmp_path, monkeypatch):
    app = _dictation_app(tmp_path)
    monkeypatch.setattr(stt_module, "too_quiet", lambda path, threshold: (False, 0.2))
    monkeypatch.setattr(stt_module, "load_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(stt_module, "transcribe", lambda model, path, language: "Guten Tag")
    monkeypatch.setattr(
        stt_module, "paste", lambda text, restore=True: (True, "Diktat eingefügt")
    )
    app.start_dictation()
    app.finish_dictation()
    worker = app.stt
    assert isinstance(worker, stt_module.DictationThread)
    # Echter QThread, echte Signale über die Fadengrenze: die Ereignisschleife
    # muss laufen, sonst kommt nichts an.
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    assert worker.wait(2000)
    qapp.processEvents()
    assert app.stt is None
    assert app.status_action.text == "Diktat eingefügt"
    assert app.tray.icon == "gruen"


def test_restart_hotkey_cancels_a_running_dictation(tmp_path):
    """Ein Tray-Linksklick zwischen Druck und Loslassen darf pw-record nicht erben."""
    app = _dictation_app(tmp_path)
    app.config.enabled = False
    app.config.tts_enabled = False
    app.config.stt_enabled = False
    app.config.stop_enabled = False
    app.dictation.start()
    app._restart_hotkey()
    assert app.dictation.cancelled == 1
    assert app.dictation.is_recording is False
    assert app.tray.calls[-1][0] == "Diktat abgebrochen"


def test_release_after_restart_does_nothing(tmp_path):
    """Die nachgereichte Loslass-Flanke des alten Fadens trifft ins Leere."""
    app = _dictation_app(tmp_path)
    app.config.enabled = False
    app.config.tts_enabled = False
    app.config.stt_enabled = False
    app.config.stop_enabled = False
    app.dictation.start()
    app._restart_hotkey()
    app.finish_dictation()
    assert app.stt is None


def test_ready_state_counts_the_dictation_alone():
    """Nur Diktat aktiv: das Tray meldete „Deaktiviert", obwohl Pause geht.

    Grau ist nicht nur falsch beschriftet – ein Linksklick aufs graue Symbol
    schaltet die Aufnahme ein, die der Nutzer gar nicht wollte.
    """
    app = _tray_app_without_gui()
    app.config.enabled = False
    app.config.tts_enabled = False
    app.config.stt_enabled = True
    app.config.stop_enabled = False
    app._refresh()
    assert app.tray.icon == "gruen"
    assert app.status_action.text == "Bereit: Pause Diktat"


def test_dictation_stays_visible_during_a_recording(tmp_path):
    """Aufnahme und Diktat laufen gleichzeitig – beide gehören in die Zeile."""
    app = _dictation_app(tmp_path)
    app.recorder = SimpleNamespace(is_recording=True)
    app.dictation.start()
    app._refresh()
    # Rot bleibt rot: die laufende Aufnahme darf nie wie etwas anderes aussehen.
    assert app.tray.icon == "rot"
    assert app.status_action.text == "● Aufnahme läuft … · ● Diktat: hört zu …"
    assert "hört zu" in app.tray.tooltip
    # Und weiter durch: nach dem Loslassen läuft die Erkennung. Der Zweig war
    # ungedeckt – gestrichen blieben alle Tests grün (Befund K).
    app.dictation.is_recording = False
    app.stt = SimpleNamespace(isRunning=lambda: True)
    app._refresh()
    assert app.status_action.text == "● Aufnahme läuft … · ✳ Diktat: erkennt …"
    assert "erkennt" in app.tray.tooltip


def test_second_dictation_is_refused_while_the_first_is_recognized(tmp_path):
    """Zwei Erkennungen nebeneinander hieße zwei Modelle im VRAM."""
    app = _dictation_app(tmp_path)
    app.stt = SimpleNamespace(isRunning=lambda: True)
    app.start_dictation()
    assert app.dictation.started == 0
    assert app.dictation.is_recording is False
    assert app.tray.calls[-1][0] == "Diktat läuft noch"


def test_signals_of_a_replaced_worker_are_ignored(tmp_path):
    """Der alte Faden meldet sich nach; das darf den neuen nicht überschreiben."""
    app = _dictation_app(tmp_path)
    current = SimpleNamespace(isRunning=lambda: True)
    app.stt = current
    app._last_stt_status = "läuft"
    stale = SimpleNamespace(isRunning=lambda: False)
    app._dictation_result(stale, False, "Zu leise – nichts erkannt")
    assert app._last_stt_status == "läuft"
    assert app.tray.calls == []
    app._dictation_finished(stale)
    assert app.stt is current


def test_short_press_warns_instead_of_only_updating_the_status(tmp_path):
    app = _dictation_app(tmp_path)
    app.dictation.duration = 0.1
    app.start_dictation()
    app.finish_dictation()
    title, message, icon, _msecs = app.tray.calls[-1]
    assert (title, icon) == ("Diktat", WARNING)
    assert "Zu kurz" in message


def _terminated_shutdown(app, monkeypatch, *, clipboard_touched):
    """shutdown() gegen einen hängenden Erkennungsfaden. Rückgabe: Rückwege."""
    import pc_sound_recorder.app as app_module

    # `_voice_threads` ist Modulzustand und trägt Attrappen aus anderen Tests
    # herüber; shutdown() räumt ihn mit ab.
    app_module._voice_threads.clear()
    restored = []
    monkeypatch.setattr(
        app_module, "restore_clipboard",
        lambda saved, primary=False: restored.append((saved, primary)),
    )
    terminated = []
    app.stt = SimpleNamespace(
        isRunning=lambda: True,
        wait=lambda msecs: False,
        terminate=lambda: terminated.append(True),
        clipboard_touched=clipboard_touched,
    )
    app._stt_clipboard = ("ablage", "primaer")
    app.duration_timer = SimpleNamespace(stop=lambda: None)
    app.recorder = SimpleNamespace(is_recording=False, cancel=lambda: None)
    app.shutdown()
    assert terminated == [True]
    return restored


def test_shutdown_puts_the_clipboard_back_after_terminate(tmp_path, monkeypatch):
    """terminate() umgeht das finally im Faden – der Controller räumt auf."""
    app = _dictation_app(tmp_path)
    restored = _terminated_shutdown(app, monkeypatch, clipboard_touched=True)
    assert restored == [("ablage", False), ("primaer", True)]
    # Und die Sprachaufnahme überlebt das Programmende nicht.
    assert not app.dictation.path.exists()


def test_shutdown_leaves_an_untouched_clipboard_alone(tmp_path, monkeypatch):
    """Befund D: terminate() trifft fast immer die Erkennung, nicht das Einfügen.

    Blind zurückschreiben setzt den unveränderten Inhalt des Nutzers mit
    `--sensitive` neu – gemessen verschwindet er dadurch aus der
    Klipper-Historie, ohne dass PTR die Ablage je angefasst hätte.
    """
    app = _dictation_app(tmp_path)
    assert _terminated_shutdown(app, monkeypatch, clipboard_touched=False) == []


def test_idle_timer_starts_after_a_dictation_and_pauses_during_one(tmp_path, monkeypatch):
    """Die Frist zählt Leerlauf, nicht Diktat."""
    app = _dictation_app(tmp_path)
    app.config.stt_warm_minutes = 10
    monkeypatch.setattr(stt_module, "too_quiet", lambda path, threshold: (False, 0.2))
    monkeypatch.setattr(stt_module, "load_model", lambda *args, **kwargs: object())
    app._stt_release_timer.start(600_000)
    app.start_dictation()
    assert app._stt_release_timer.interval is None
    app.finish_dictation()
    worker = app.stt
    assert app._stt_release_timer.interval is None
    app._dictation_finished(worker)
    assert app._stt_release_timer.interval == 600_000
    worker.wait(5000)


def test_warm_minutes_zero_never_releases(tmp_path):
    app = _dictation_app(tmp_path)
    app.config.stt_warm_minutes = 0
    app._arm_stt_release()
    assert app._stt_release_timer.interval is None


def test_shutdown_frees_the_warm_model(tmp_path, monkeypatch):
    """Sonst bliebe das Modell bis zum Prozessende im VRAM – auch beim Beenden."""
    import pc_sound_recorder.app as app_module

    app = _dictation_app(tmp_path)
    app_module._voice_threads.clear()
    freigegeben = []
    monkeypatch.setattr(app_module, "release_model", lambda: freigegeben.append(True))
    app.duration_timer = SimpleNamespace(stop=lambda: None)
    app.recorder = SimpleNamespace(is_recording=False, cancel=lambda: None)
    app.shutdown()
    assert freigegeben == [True]
    assert app._stt_release_timer.stops


def test_dictation_shortcut_is_named_and_can_degrade():
    app = _tray_app_without_gui()
    app.config.stt_enabled = True
    app.config.stop_enabled = False
    app._refresh()
    assert app.status_action.text == (
        "Bereit: Meta+F8 Aufnahme · Meta+Rollen Vorlesen · Pause Diktat"
    )
    app.hotkey_degraded(["stt"])
    assert app.status_action.text == "Bereit: Meta+F8 Aufnahme · Meta+Rollen Vorlesen"
    assert "Pause (Diktat)" in app.tray.calls[-1][1]


# --- Abbrechen (viertes Kürzel) ---------------------------------------------


class _FakeSpeech:
    """Steht für SpeechThread: derselbe Zustand, ohne Mimic."""

    def __init__(self):
        self.running = True
        self.stopped = 0

    def isRunning(self):
        return self.running

    def stop(self):
        self.stopped += 1
        self.running = False

    def wait(self, msecs):
        return True


class _FakeRecognition:
    """Steht für DictationThread, so weit `cancel_playback` ihn anfasst."""

    def __init__(self):
        self.cancelled = False

    def isRunning(self):
        return True

    def cancel(self):
        self.cancelled = True


def _no_new_speech(monkeypatch):
    monkeypatch.setattr(
        app_module, "SpeechThread",
        lambda *args, **kwargs: pytest.fail("Abbrechen darf nichts Neues starten"),
    )


def test_cancel_ends_the_playback_without_starting_a_new_one(monkeypatch):
    """Der Unterschied zum zweiten Druck auf das Vorlesen-Kürzel: kein neues Vorlesen."""
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    speech = _FakeSpeech()
    app.speech = speech
    app.cancel_playback()
    assert speech.stopped == 1
    assert app.speech is None
    assert app.status_action.text == "Vorlesen abgebrochen"


class _FakeSpeechThread(QObject):
    """Steht für einen frisch gestarteten SpeechThread, samt Signalen."""

    playback_started = Signal()
    result = Signal(bool, str)
    finished = Signal()

    def isRunning(self):
        return True

    def start(self):
        pass


def test_speak_shortcut_still_restarts(qapp, monkeypatch):
    """Gegenprobe: das Vorlesen-Kürzel stoppt und legt sofort nach – das ist der Bestand."""
    app = _tray_app_without_gui()
    started = []
    monkeypatch.setattr(
        app_module, "SpeechThread",
        lambda *args, **kwargs: started.append(True) or _FakeSpeechThread(),
    )
    speech = _FakeSpeech()
    app.speech = speech
    app.speak_selected_text()
    assert speech.stopped == 1 and started == [True]


def test_cancel_into_the_void_does_nothing(monkeypatch):
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    app.cancel_playback()
    assert app.tray.calls == []
    assert app.status_action.text is None
    assert app.speech is None


def test_cancel_ends_a_running_dictation_recording(monkeypatch):
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    app.dictation.start()
    app.cancel_playback()
    assert app.dictation.cancelled == 1
    assert app.status_action.text == "Diktat abgebrochen"


def test_cancel_drops_a_running_recognition(monkeypatch):
    """Und die nachgereichte Meldung des Fadens überschreibt den Abbruch nicht."""
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    worker = _FakeRecognition()
    app.stt = worker
    app.cancel_playback()
    assert worker.cancelled is True
    assert app._last_stt_status == "Diktat abgebrochen"
    app._dictation_result(worker, False, "Nichts verstanden")
    assert app._last_stt_status == "Diktat abgebrochen"
    assert app.tray.calls == []


def test_cancel_takes_both_at_once(monkeypatch):
    """Vorlesen und Diktat können gleichzeitig laufen – beides fällt."""
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    app.speech = _FakeSpeech()
    app.dictation.start()
    app.cancel_playback()
    assert app.status_action.text == "Vorlesen abgebrochen · Diktat abgebrochen"


def test_cancel_is_silent_when_the_shortcut_is_off(monkeypatch):
    app = _tray_app_without_gui()
    _no_new_speech(monkeypatch)
    app.config.stop_enabled = False
    speech = _FakeSpeech()
    app.speech = speech
    app.cancel_playback()
    assert speech.stopped == 0 and app.speech is speech


# --- Sekundentakt und Menüs -------------------------------------------------

import time


class FakeMenu:
    """QMenu, so weit `_populate_recent` es braucht."""

    def __init__(self):
        self.items = []

    def clear(self):
        self.items = []

    def addAction(self, text):
        self.items.append(text)
        return SimpleNamespace(
            setEnabled=lambda enabled: None,
            triggered=SimpleNamespace(connect=lambda slot: None),
        )


def _recording_app(seconds_ago: float, max_minutes: int) -> TrayApplication:
    app = _tray_app_without_gui()
    app.config.max_minutes = max_minutes
    app.duration_timer = SimpleNamespace(stop=lambda: None)
    app.recorder = SimpleNamespace(
        is_recording=True, started_at=time.monotonic() - seconds_ago
    )
    return app


def test_tick_stops_the_recording_at_the_limit():
    """Befund H: der Auto-Stopp bei `max_minutes` hing an keinem Test."""
    app = _recording_app(seconds_ago=61, max_minutes=1)
    stopped = []
    app.stop_recording = lambda: stopped.append(True)
    app._tick()
    assert stopped == [True]
    title, message, icon, _msecs = app.tray.calls[-1]
    assert (title, icon) == ("Maximale Aufnahmedauer erreicht", INFO)
    assert "1 min" in message


def test_tick_keeps_recording_below_the_limit():
    app = _recording_app(seconds_ago=59, max_minutes=1)
    app.stop_recording = lambda: pytest.fail("unter dem Limit darf nichts stoppen")
    app._tick()
    assert app.tray.calls == []
    assert app.status_action.text == "● Aufnahme läuft … 00:59"


def test_tick_without_a_limit_runs_on():
    """`max_minutes = 0` heißt unbegrenzt – nicht „sofort abbrechen"."""
    app = _recording_app(seconds_ago=3600, max_minutes=0)
    app.stop_recording = lambda: pytest.fail("ohne Limit darf nichts stoppen")
    app._tick()
    assert app.status_action.text == "● Aufnahme läuft … 60:00"


def test_the_clock_keeps_the_dictation_in_the_tooltip(tmp_path):
    """Befund I: `_tick` überschrieb den Tooltip ohne den Diktatteil.

    Der Sekundentakt nahm damit im Tooltip genau das zurück, was in der
    Statuszeile zusammengeführt wird – und kostete je Sekunde ein DBus-Signal.
    """
    app = _dictation_app(tmp_path)
    app.config.max_minutes = 0
    app.duration_timer = SimpleNamespace(stop=lambda: None)
    app.recorder = SimpleNamespace(is_recording=True, started_at=time.monotonic() - 63)
    app.dictation.start()
    app._tick()
    assert app.tray.tooltip == (
        "PC-Ton aufnehmen – Aufnahme läuft (01:03) · Diktat – hört zu"
    )
    assert app.status_action.text == "● Aufnahme läuft … 01:03 · ● Diktat: hört zu …"


def test_recent_menu_says_so_when_there_is_nothing(tmp_path):
    """Befund K: der leere Zweig lief ungedeckt durch."""
    app = _tray_app_without_gui()
    app.config.output_dir = str(tmp_path)
    app.recent_menu = FakeMenu()
    app._populate_recent()
    assert app.recent_menu.items == ["Keine Aufnahmen"]


def test_recent_menu_lists_at_most_five_recordings(tmp_path):
    app = _tray_app_without_gui()
    app.config.output_dir = str(tmp_path)
    app.recent_menu = FakeMenu()
    for index in range(6):
        datei = tmp_path / f"aufnahme-{index}.mp3"
        datei.write_bytes(b"x")
        os.utime(datei, (index, index))
    (tmp_path / "notiz.txt").write_bytes(b"x")
    (tmp_path / "aufnahme.unfertig.mp3").write_bytes(b"x")
    app._populate_recent()
    assert app.recent_menu.items == [f"aufnahme-{index}.mp3" for index in (5, 4, 3, 2, 1)]


def test_autostart_schaltet_die_unit_und_raeumt_den_xdg_eintrag_weg(tmp_path, monkeypatch):
    """Liegt die Unit, entscheidet systemctl – und der XDG-Eintrag muss weg.

    Beides nebeneinander startet PTR zweimal; die zweite Instanz läuft in die
    QLockFile.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    unit = tmp_path / "systemd" / "user" / "pc-sound-recorder.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\n")
    xdg = tmp_path / "autostart" / "pc-sound-recorder.desktop"
    xdg.parent.mkdir(parents=True)
    xdg.write_text("[Desktop Entry]\n")

    aufrufe = []
    monkeypatch.setattr(app_modul.shutil, "which", lambda name: "/usr/bin/systemctl")
    monkeypatch.setattr(
        app_modul.subprocess, "run", lambda cmd, **kwargs: aufrufe.append(cmd)
    )

    app_modul.set_autostart(True)
    assert aufrufe == [["/usr/bin/systemctl", "--user", "enable", "pc-sound-recorder.service"]]
    assert not xdg.exists()

    app_modul.set_autostart(False)
    assert aufrufe[-1][2] == "disable"
    assert not xdg.exists()


def test_autostart_faellt_ohne_unit_auf_den_xdg_eintrag_zurueck(tmp_path, monkeypatch):
    """Aus einem Quell-Checkout heraus gibt es keine Unit – dann der alte Weg."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    xdg = tmp_path / "autostart" / "pc-sound-recorder.desktop"

    app_modul.set_autostart(True)
    assert "[Desktop Entry]" in xdg.read_text()

    app_modul.set_autostart(False)
    assert not xdg.exists()
