from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import time

from PySide6.QtCore import (
    QLockFile, QObject, QProcess, QStandardPaths, Qt, QTimer, Signal,
)
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QSpinBox, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .audio import FORMATS, AudioRecorder, RecorderError
from .config import (
    APP_ID, KEY_LABELS, MODIFIER_OPTIONS, Config, shortcut_label,
)
from .hotkey import HotkeyThread
from .tts import SpeechThread, VoicesThread, find_mimic


# Strong refs so a voice-loading QThread is never garbage-collected while it
# is still running (the dialog may be closed before mimic answers).
_voice_threads: set[VoicesThread] = set()


def tray_icon(color: str) -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(7, 7, 50, 50)
    painter.setBrush(QColor("white"))
    painter.drawEllipse(23, 23, 18, 18)
    painter.end()
    return QIcon(pixmap)


def autostart_path() -> pathlib.Path:
    base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return base / "autostart" / f"{APP_ID}.desktop"


def desktop_entry() -> str:
    installed_launcher = shutil.which(APP_ID)
    executable = pathlib.Path(sys.argv[0]).resolve()
    if installed_launcher:
        command = f'"{installed_launcher}"'
    elif executable.name == "__main__.py":
        project = pathlib.Path(__file__).resolve().parent.parent
        command = f'env PYTHONPATH="{project}" "{sys.executable}" -m pc_sound_recorder'
    else:
        command = f'"{executable}"'
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=PC-Ton & Vorlesen",
        "Comment=PC-Audio aufnehmen und markierten Text mit Mimic vorlesen",
        f"Exec={command}", f"Icon={APP_ID}", "Terminal=false",
        "Categories=AudioVideo;Audio;", "X-KDE-autostart-after=panel", "",
    ])


def set_autostart(enabled: bool) -> None:
    path = autostart_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_entry(), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


class _SilenceBridge(QObject):
    """Delivers the background volumedetect result onto the GUI thread."""

    warned = Signal(float)


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PC-Ton & Vorlesen – Einstellungen")

        self.enabled = QCheckBox("Aufnahme-Hotkey aktiv")
        self.enabled.setChecked(config.enabled)

        self.modifier, self.trigger = self._shortcut_widgets(
            config.modifiers, config.trigger_key
        )
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(self.modifier)
        shortcut_row.addWidget(QLabel("+"))
        shortcut_row.addWidget(self.trigger)

        self.tts_modifier, self.tts_trigger = self._shortcut_widgets(
            config.tts_modifiers, config.tts_trigger_key
        )
        tts_shortcut_row = QHBoxLayout()
        tts_shortcut_row.addWidget(self.tts_modifier)
        tts_shortcut_row.addWidget(QLabel("+"))
        tts_shortcut_row.addWidget(self.tts_trigger)

        self.format = QComboBox()
        for key in FORMATS:
            self.format.addItem(key.upper(), key)
        index = self.format.findData(config.format)
        self.format.setCurrentIndex(max(index, 0))
        self.quality = QSpinBox()
        self.format.currentIndexChanged.connect(self._update_quality_range)
        self._update_quality_range()
        self.quality.setValue(config.quality)
        format_row = QHBoxLayout()
        format_row.addWidget(self.format)
        format_row.addWidget(QLabel("Qualität:"))
        format_row.addWidget(self.quality)

        self.mix_microphone = QCheckBox("Mikrofon zusätzlich zum PC-Ton aufnehmen")
        self.mix_microphone.setChecked(config.mix_microphone)

        self.max_minutes = QSpinBox()
        self.max_minutes.setRange(0, 480)
        self.max_minutes.setSpecialValueText("aus")
        self.max_minutes.setSuffix(" min")
        self.max_minutes.setValue(config.max_minutes)

        self.folder = QLineEdit(config.output_dir)
        choose = QPushButton("Auswählen …")
        choose.clicked.connect(self._choose_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(choose)
        self.autostart = QCheckBox("Automatisch bei der Anmeldung starten")
        self.autostart.setChecked(config.autostart)

        self.tts_enabled = QCheckBox(
            f"Markierten Text mit {shortcut_label(config, tts=True)} vorlesen"
        )
        self.tts_enabled.setChecked(config.tts_enabled)
        self._requested_voice = config.tts_voice
        self._voices_ready = False
        self.tts_voice = QComboBox()
        self.tts_voice.addItem("Stimmen werden geladen …")
        self.tts_voice.setEnabled(False)
        self.tts_enabled.toggled.connect(
            lambda enabled: self.tts_voice.setEnabled(enabled and self._voices_ready)
        )
        self.preview_button = QPushButton("Probehören")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self._preview_voice)
        self._preview_process: QProcess | None = None
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.tts_voice, 1)
        voice_row.addWidget(self.preview_button)
        self.voice_hint = QLabel("Mimic-Stimmen werden im Hintergrund geladen …")
        self.voice_hint.setWordWrap(True)
        self._load_voices()

        self.notifications = QCheckBox("Benachrichtigungen anzeigen (Warnungen kommen immer)")
        self.notifications.setChecked(config.notifications)
        self.silence_warn = QCheckBox("Warnen, wenn eine Aufnahme nahezu stumm ist")
        self.silence_warn.setChecked(config.silence_warn)
        self.clipboard_fallback = QCheckBox(
            "Zwischenablage als Fallback nutzen, wenn kein Text markiert ist"
        )
        self.clipboard_fallback.setChecked(config.tts_clipboard_fallback)
        clipboard_warning = QLabel(
            "Achtung: Die Zwischenablage kann vertrauliche Inhalte enthalten "
            "(z. B. aus Passwortmanagern)."
        )
        clipboard_warning.setWordWrap(True)
        clipboard_warning.setStyleSheet("color: #e67700;")

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("Aufnahme-Hotkey:", shortcut_row)
        form.addRow("Vorlesen-Hotkey:", tts_shortcut_row)
        form.addRow("Format:", format_row)
        form.addRow("", self.mix_microphone)
        form.addRow("Maximale Dauer:", self.max_minutes)
        form.addRow("Zielordner:", folder_row)
        form.addRow("Mimic-Stimme:", voice_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.tts_enabled)
        layout.addWidget(self.voice_hint)
        layout.addWidget(self.notifications)
        layout.addWidget(self.silence_warn)
        layout.addWidget(self.clipboard_fallback)
        layout.addWidget(clipboard_warning)
        layout.addWidget(self.autostart)
        layout.addWidget(buttons)

    def _shortcut_widgets(
        self, modifiers: tuple[str, ...], trigger_key: str
    ) -> tuple[QComboBox, QComboBox]:
        modifier = QComboBox()
        modifier.addItems(MODIFIER_OPTIONS)
        current_modifier = next(
            (label for label, keys in MODIFIER_OPTIONS.items() if tuple(keys) == tuple(modifiers)),
            "Meta",
        )
        modifier.setCurrentText(current_modifier)
        trigger = QComboBox()
        for key, label in KEY_LABELS.items():
            trigger.addItem(label, key)
        index = trigger.findData(trigger_key)
        trigger.setCurrentIndex(max(index, 0))
        return modifier, trigger

    def _update_quality_range(self) -> None:
        _, option, low, high = FORMATS[self.format.currentData()]
        self.quality.setEnabled(option is not None)
        if option is not None:
            self.quality.setRange(low, high)

    def _preview_voice(self) -> None:
        if self._preview_process is not None and self._preview_process.state() != QProcess.ProcessState.NotRunning:
            return
        mimic = find_mimic()
        if not mimic:
            self.voice_hint.setText("Mimic wurde nicht gefunden.")
            return
        self._preview_process = QProcess(self)
        self._preview_process.start(
            mimic, ["say", "--voice", self.tts_voice.currentText(), "Dies ist eine Hörprobe."]
        )

    def _load_voices(self) -> None:
        thread = VoicesThread()
        _voice_threads.add(thread)
        thread.loaded.connect(self._voices_loaded)
        thread.finished.connect(lambda: _voice_threads.discard(thread))
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _voices_loaded(self, voices: list[str], error: object) -> None:
        if self._requested_voice and self._requested_voice not in voices:
            voices.insert(0, self._requested_voice)
        self.tts_voice.clear()
        self.tts_voice.addItems(voices)
        if self._requested_voice:
            self.tts_voice.setCurrentText(self._requested_voice)
        self._voices_ready = True
        self.tts_voice.setEnabled(self.tts_enabled.isChecked() and bool(voices))
        self.preview_button.setEnabled(bool(voices))
        self.voice_hint.setText(
            str(error) if error else f"{len(voices)} lokale Mimic-Stimmen gefunden."
        )

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Zielordner", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def accept(self) -> None:
        # Immer ablehnen, unabhängig von den aktiviert-Checkboxen: die
        # Konfiguration selbst soll gar nicht kollidieren können.
        if (
            self.trigger.currentData() == self.tts_trigger.currentData()
            and MODIFIER_OPTIONS[self.modifier.currentText()]
                == MODIFIER_OPTIONS[self.tts_modifier.currentText()]
        ):
            QMessageBox.warning(
                self,
                "Hotkey-Kollision",
                "Aufnahme- und Vorlesen-Hotkey sind identisch. "
                "Bitte wähle unterschiedliche Tastenkombinationen.",
            )
            return
        super().accept()

    def apply(self, config: Config) -> None:
        config.enabled = self.enabled.isChecked()
        config.modifiers = MODIFIER_OPTIONS[self.modifier.currentText()]
        config.trigger_key = self.trigger.currentData()
        config.tts_modifiers = MODIFIER_OPTIONS[self.tts_modifier.currentText()]
        config.tts_trigger_key = self.tts_trigger.currentData()
        config.format = self.format.currentData()
        config.quality = self.quality.value()
        config.mix_microphone = self.mix_microphone.isChecked()
        config.max_minutes = self.max_minutes.value()
        config.output_dir = self.folder.text().strip()
        config.autostart = self.autostart.isChecked()
        config.tts_enabled = self.tts_enabled.isChecked()
        if self._voices_ready and self.tts_voice.currentText():
            config.tts_voice = self.tts_voice.currentText()
        config.notifications = self.notifications.isChecked()
        config.silence_warn = self.silence_warn.isChecked()
        config.tts_clipboard_fallback = self.clipboard_fallback.isChecked()


class TrayApplication:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.config = Config.load()
        self.config.save()
        self.recorder = AudioRecorder()
        self.hotkey: HotkeyThread | None = None
        self.speech: SpeechThread | None = None
        self._last_speech_status: str | None = None
        self._silence_bridge = _SilenceBridge()
        self._silence_bridge.warned.connect(self._silence_warning)
        themed = QIcon.fromTheme(APP_ID)
        self.icon_idle = themed if not themed.isNull() else tray_icon("#2f9e44")
        self.icon_disabled = tray_icon("#868e96")
        self.icon_recording = tray_icon("#e03131")
        self.icon_speaking = tray_icon("#1971c2")
        self.app.setWindowIcon(self.icon_idle)
        self.duration_timer = QTimer()
        self.duration_timer.setInterval(1000)
        self.duration_timer.timeout.connect(self._tick)
        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        self.enabled_action = QAction("Aufnahme-Hotkey aktiv", self.menu)
        self.enabled_action.setCheckable(True)
        self.enabled_action.setChecked(self.config.enabled)
        self.enabled_action.toggled.connect(self.set_enabled)
        self.tts_enabled_action = QAction(
            f"Vorlesen-Hotkey {shortcut_label(self.config, tts=True)} aktiv", self.menu
        )
        self.tts_enabled_action.setCheckable(True)
        self.tts_enabled_action.setChecked(self.config.tts_enabled)
        self.tts_enabled_action.toggled.connect(self.set_tts_enabled)
        self.status_action = QAction("Bereit", self.menu)
        self.status_action.setEnabled(False)
        self.recent_menu = QMenu("Letzte Aufnahmen", self.menu)
        self.recent_menu.aboutToShow.connect(self._populate_recent)
        self.open_action = QAction("Aufnahmeordner öffnen", self.menu)
        self.open_action.triggered.connect(self.open_folder)
        self.settings_action = QAction("Einstellungen …", self.menu)
        self.settings_action.triggered.connect(self.show_settings)
        self.quit_action = QAction("Beenden", self.menu)
        self.quit_action.triggered.connect(self.quit)
        self.menu.addAction(self.enabled_action)
        self.menu.addAction(self.tts_enabled_action)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        self.menu.addMenu(self.recent_menu)
        self.menu.addAction(self.open_action)
        self.menu.addAction(self.settings_action)
        self.menu.addSeparator()
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        set_autostart(self.config.autostart)
        self._restart_hotkey()
        self._refresh()
        self._notify(
            "PC-Ton & Vorlesen",
            f"Bereit: {shortcut_label(self.config)} nimmt auf, "
            f"{shortcut_label(self.config, tts=True)} liest markierten Text vor.",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )

    def _notify(
        self,
        title: str,
        message: str,
        icon: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
        msecs: int = 3000,
    ) -> None:
        # notifications=False suppresses Information only; Warning/Critical always show.
        if icon == QSystemTrayIcon.MessageIcon.Information and not self.config.notifications:
            return
        self.tray.showMessage(title, message, icon, msecs)

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.enabled_action.toggle()

    def _restart_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
            if not self.hotkey.wait(1500):
                # Letzte Rettung: nie ein laufendes QThread-Objekt fallen lassen.
                # terminate() umgeht das finally in hotkey.py — die evdev-FDs
                # bleiben bis zum GC offen. Bewusst akzeptiert: nur erreichbar,
                # wenn der 0,25-s-select-Loop trotz Stop-Event hängt.
                self.hotkey.terminate()
                self.hotkey.wait(500)
            self.hotkey = None
        if self.config.enabled or self.config.tts_enabled:
            self.hotkey = HotkeyThread(
                self.config.trigger_key,
                self.config.modifiers,
                record_enabled=self.config.enabled,
                tts_trigger=self.config.tts_trigger_key,
                tts_modifiers=self.config.tts_modifiers,
                read_aloud_enabled=self.config.tts_enabled,
            )
            self.hotkey.pressed.connect(self.toggle_recording)
            self.hotkey.read_aloud_pressed.connect(self.speak_selected_text)
            self.hotkey.unavailable.connect(self.hotkey_error)
            self.hotkey.start()

    def _tick(self) -> None:
        if not self.recorder.is_recording:
            self.duration_timer.stop()
            self._refresh()
            return
        elapsed = time.monotonic() - self.recorder.started_at
        minutes, seconds = divmod(int(elapsed), 60)
        stamp = f"{minutes:02d}:{seconds:02d}"
        self.tray.setToolTip(f"PC-Ton aufnehmen – Aufnahme läuft ({stamp})")
        self.status_action.setText(f"● Aufnahme läuft … {stamp}")
        limit = self.config.max_minutes * 60
        if limit > 0 and elapsed >= limit:
            max_minutes = self.config.max_minutes
            self.stop_recording()
            self._notify(
                "Maximale Aufnahmedauer erreicht",
                f"Die Aufnahme wurde nach {max_minutes} min automatisch gestoppt und gespeichert.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def _refresh(self, status: str | None = None) -> None:
        if self.recorder.is_recording:
            self.tray.setIcon(self.icon_recording)
            self.tray.setToolTip("PC-Ton aufnehmen – Aufnahme läuft")
            self.status_action.setText(status or "● Aufnahme läuft …")
        elif self.speech and self.speech.isRunning():
            self.tray.setIcon(self.icon_speaking)
            self.tray.setToolTip(f"Mimic spricht mit {self.config.tts_voice}")
            self.status_action.setText(status or f"▶ Mimic spricht: {self.config.tts_voice}")
        elif self.config.enabled or self.config.tts_enabled:
            label = shortcut_label(self.config)
            self.tray.setIcon(self.icon_idle)
            shortcuts = []
            if self.config.enabled:
                shortcuts.append(f"{label} Aufnahme")
            if self.config.tts_enabled:
                shortcuts.append(f"{shortcut_label(self.config, tts=True)} Vorlesen")
            ready = " · ".join(shortcuts)
            self.tray.setToolTip(f"PC-Ton & Vorlesen – bereit ({ready})")
            self.status_action.setText(status or f"Bereit: {ready}")
        else:
            self.tray.setIcon(self.icon_disabled)
            self.tray.setToolTip("PC-Ton & Vorlesen – deaktiviert")
            self.status_action.setText(status or "Deaktiviert")

    def _populate_recent(self) -> None:
        self.recent_menu.clear()
        folder = pathlib.Path(self.config.output_dir).expanduser()
        endings = {f".{fmt}" for fmt in FORMATS}
        try:
            files = [
                path for path in folder.iterdir()
                if path.suffix in endings and ".unfertig." not in path.name
            ]
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        for path in files[:5]:
            action = self.recent_menu.addAction(path.name)
            action.triggered.connect(
                lambda checked=False, target=path: self._open_recording(target)
            )
        if not files[:5]:
            empty = self.recent_menu.addAction("Keine Aufnahmen")
            empty.setEnabled(False)

    def _open_recording(self, path: pathlib.Path) -> None:
        subprocess.Popen(
            ["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def set_enabled(self, enabled: bool) -> None:
        if not enabled and self.recorder.is_recording:
            self.stop_recording()
        self.config.enabled = enabled
        self.config.save()
        self._restart_hotkey()
        self._refresh()

    def set_tts_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.stop_speaking()
        self.config.tts_enabled = enabled
        self.config.save()
        self._restart_hotkey()
        self._refresh()

    def start_recording(self) -> None:
        if not self.config.enabled or self.recorder.is_recording:
            return
        try:
            self.recorder.start(
                pathlib.Path(self.config.output_dir).expanduser(),
                fmt=self.config.format,
                quality=self.config.quality,
                mix_microphone=self.config.mix_microphone,
            )
            if self.recorder.last_warning:
                self._notify(
                    "Mikrofon-Mix ohne Mikrofon",
                    self.recorder.last_warning,
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000,
                )
            self.duration_timer.start()
            self._refresh()
        except RecorderError as error:
            self._refresh("Fehler beim Start")
            self._notify(
                "Aufnahme nicht gestartet", str(error),
                QSystemTrayIcon.MessageIcon.Critical, 5000,
            )

    def toggle_recording(self) -> None:
        if self.recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def stop_recording(self) -> None:
        self.duration_timer.stop()
        try:
            finished = self.recorder.stop(
                silence_warn=self.config.silence_warn,
                on_silence=self._silence_bridge.warned.emit,
            )
            if finished:
                self._refresh(f"Gespeichert: {finished.name}")
                self._notify(
                    "PC-Ton gespeichert", finished.name,
                    QSystemTrayIcon.MessageIcon.Information, 2500,
                )
            else:
                self._refresh("Zu kurz – verworfen")
        except (OSError, subprocess.SubprocessError) as error:
            self._refresh("Fehler beim Speichern")
            self._notify(
                "Aufnahmefehler", str(error), QSystemTrayIcon.MessageIcon.Critical, 5000
            )

    def _silence_warning(self, mean_db: float) -> None:
        self._notify(
            "Aufnahme wirkt stumm",
            f"Mittlere Lautstärke nur {mean_db:.1f} dB. Bitte Quelle und Pegel prüfen.",
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )

    def speak_selected_text(self) -> None:
        if not self.config.tts_enabled:
            return
        if self.speech and self.speech.isRunning():
            self.speech.stop()
            if not self.speech.wait(300):
                self._notify(
                    "Mimic ist noch beschäftigt",
                    "Die laufende Wiedergabe konnte noch nicht beendet werden.",
                    QSystemTrayIcon.MessageIcon.Warning,
                    3000,
                )
                return
        worker = SpeechThread(
            self.config.tts_voice,
            clipboard_fallback=self.config.tts_clipboard_fallback,
        )
        self.speech = worker
        self._last_speech_status = "Markierten Text lesen …"
        worker.playback_started.connect(lambda: self._speech_started(worker))
        worker.result.connect(lambda ok, message: self._speech_result(worker, ok, message))
        worker.finished.connect(lambda: self._speech_finished(worker))
        worker.start()
        self._refresh(self._last_speech_status)

    def _speech_started(self, worker: SpeechThread) -> None:
        if worker is not self.speech:
            return
        self._last_speech_status = f"▶ Mimic spricht: {self.config.tts_voice}"
        self._refresh(self._last_speech_status)

    def _speech_result(self, worker: SpeechThread, ok: bool, message: str) -> None:
        if worker is not self.speech:
            return
        self._last_speech_status = message
        if not ok:
            self._notify(
                "Vorlesen nicht möglich", message,
                QSystemTrayIcon.MessageIcon.Warning, 5000,
            )

    def _speech_finished(self, worker: SpeechThread) -> None:
        if worker is not self.speech:
            return
        self.speech = None
        self._refresh(self._last_speech_status)

    def stop_speaking(self) -> None:
        if self.speech and self.speech.isRunning():
            self.speech.stop()
            if not self.speech.wait(300):
                return
        self.speech = None

    def hotkey_error(self, message: str) -> None:
        self._refresh("Hotkey nicht verfügbar")
        self._notify(
            "Hotkey nicht verfügbar", message,
            QSystemTrayIcon.MessageIcon.Critical, 6000,
        )

    def open_folder(self) -> None:
        folder = pathlib.Path(self.config.output_dir).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["xdg-open", str(folder)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.config)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        dialog.apply(self.config)
        if not self.config.output_dir:
            QMessageBox.warning(None, "Ungültiger Ordner", "Bitte wähle einen Zielordner aus.")
            return
        self.config.save()
        set_autostart(self.config.autostart)
        self.enabled_action.setChecked(self.config.enabled)
        self.tts_enabled_action.setText(
            f"Vorlesen-Hotkey {shortcut_label(self.config, tts=True)} aktiv"
        )
        self.tts_enabled_action.setChecked(self.config.tts_enabled)
        self._restart_hotkey()
        self._refresh()

    def quit(self) -> None:
        self.shutdown()
        self.tray.hide()
        self.app.quit()

    def shutdown(self) -> None:
        self.duration_timer.stop()
        if self.hotkey:
            self.hotkey.stop()
            if not self.hotkey.wait(1500):
                # Wie in _restart_hotkey: terminate() umgeht das finally in
                # hotkey.py, evdev-FDs bleiben bis zum GC offen (akzeptiert).
                self.hotkey.terminate()
                self.hotkey.wait(500)
            self.hotkey = None
        self.stop_speaking()
        if self.speech is not None and self.speech.isRunning():
            # Letzte Rettung beim Beenden: kein „QThread destroyed while running".
            self.speech.terminate()
            self.speech.wait(500)
        self.speech = None
        # Noch laufende Stimmen-Lade-Threads einsammeln, sonst droht beim
        # Prozessende „QThread destroyed while running".
        for thread in list(_voice_threads):
            if thread.isRunning() and not thread.wait(500):
                thread.terminate()
                thread.wait(200)
        self.recorder.cancel()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PC-Ton & Vorlesen")
    app.setDesktopFileName(APP_ID)
    app.setQuitOnLastWindowClosed(False)
    runtime = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.RuntimeLocation)
    lock = QLockFile(str(pathlib.Path(runtime) / f"{APP_ID}.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "PC-Ton & Vorlesen", "Die App läuft bereits im Systembereich.")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "PC-Ton & Vorlesen", "Kein Systembereich verfügbar.")
        return 1
    controller = TrayApplication(app)
    app.aboutToQuit.connect(controller.shutdown)
    return app.exec()
