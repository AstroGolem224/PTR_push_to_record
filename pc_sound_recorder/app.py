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
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

from .audio import FORMATS, AudioRecorder, RecorderError
from .config import (
    APP_ID, KEY_LABELS, MODIFIER_OPTIONS, Config, shortcut_label,
)
from .hotkey import HotkeyThread
from .stt import DictationThread, Recording, restore_clipboard, snapshot_clipboard
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

        self.stt_modifier, self.stt_trigger = self._shortcut_widgets(
            config.stt_modifiers, config.stt_trigger_key
        )
        stt_shortcut_row = QHBoxLayout()
        stt_shortcut_row.addWidget(self.stt_modifier)
        stt_shortcut_row.addWidget(QLabel("+"))
        stt_shortcut_row.addWidget(self.stt_trigger)

        self.stop_modifier, self.stop_trigger = self._shortcut_widgets(
            config.stop_modifiers, config.stop_trigger_key
        )
        stop_shortcut_row = QHBoxLayout()
        stop_shortcut_row.addWidget(self.stop_modifier)
        stop_shortcut_row.addWidget(QLabel("+"))
        stop_shortcut_row.addWidget(self.stop_trigger)

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
        self.stop_enabled = QCheckBox(
            f"Vorlesen/Diktat mit {shortcut_label(config, stop=True)} abbrechen"
        )
        self.stop_enabled.setChecked(config.stop_enabled)
        clipboard_warning = QLabel(
            "Achtung: Die Zwischenablage kann vertrauliche Inhalte enthalten "
            "(z. B. aus Passwortmanagern)."
        )
        clipboard_warning.setWordWrap(True)
        clipboard_warning.setStyleSheet("color: #e67700;")

        # --- Diktat ---
        self.stt_enabled = QCheckBox(
            f"Diktat: {shortcut_label(config, stt=True)} halten, sprechen, loslassen"
        )
        self.stt_enabled.setChecked(config.stt_enabled)
        self.stt_model = QComboBox()
        for name in ("large-v3-turbo", "large-v3", "medium", "small"):
            self.stt_model.addItem(name, name)
        if self.stt_model.findData(config.stt_model) < 0:
            self.stt_model.addItem(config.stt_model, config.stt_model)
        self.stt_model.setCurrentIndex(self.stt_model.findData(config.stt_model))
        self.stt_language = QComboBox()
        for label, value in (("Deutsch", "de"), ("Englisch", "en"), ("automatisch", "")):
            self.stt_language.addItem(label, value)
        self.stt_language.setCurrentIndex(
            max(self.stt_language.findData(config.stt_language), 0)
        )
        self.stt_threshold = QDoubleSpinBox()
        self.stt_threshold.setRange(0.0, 0.5)
        self.stt_threshold.setSingleStep(0.005)
        self.stt_threshold.setDecimals(3)
        self.stt_threshold.setValue(config.stt_threshold)
        self.stt_threshold.setToolTip(
            "Lautstärke des lautesten 200-ms-Fensters, unter der eine Aufnahme als "
            "still gilt. Verhindert, dass aus Raumrauschen ein Satz erfunden wird."
        )
        self.stt_clipboard_restore = QCheckBox(
            "Zwischenablage nach dem Diktat wiederherstellen"
        )
        self.stt_clipboard_restore.setChecked(config.stt_clipboard_restore)
        stt_clipboard_hint = QLabel(
            "Das Diktat wird über die Zwischenablage eingefügt und überschreibt sie "
            "dabei kurz. Mit Häkchen liest PTR den bisherigen Inhalt vorher aus, um "
            "ihn danach zurückzulegen; ohne Häkchen wird er nicht gelesen, geht aber "
            "verloren. Schlägt das Einfügen fehl (ydotool nicht bereit), bleibt der "
            "Diktattext in beiden Fällen in der Zwischenablage stehen – sonst wäre "
            "er verloren. Unabhängig vom Häkchen gilt: Diktate werden als "
            "vertraulich markiert und stehen deshalb nicht in der "
            "Klipper-Historie – nur so lässt sich die Zwischenablage danach "
            "überhaupt wieder leeren, denn Klipper böte einen Historieneintrag "
            "sofort wieder an. Mit Häkchen wird auch der zurückgelegte alte "
            "Inhalt vertraulich markiert (er könnte ein Passwort sein) und fällt "
            "damit ebenfalls aus der Historie."
        )
        stt_clipboard_hint.setWordWrap(True)
        stt_clipboard_hint.setStyleSheet("color: #e67700;")

        form = QFormLayout()
        form.addRow("", self.enabled)
        form.addRow("Aufnahme-Hotkey:", shortcut_row)
        form.addRow("Vorlesen-Hotkey:", tts_shortcut_row)
        form.addRow("Diktat-Hotkey:", stt_shortcut_row)
        form.addRow("Abbrechen-Hotkey:", stop_shortcut_row)
        form.addRow("Diktat-Modell:", self.stt_model)
        form.addRow("Diktat-Sprache:", self.stt_language)
        form.addRow("Diktat-Stilleschwelle:", self.stt_threshold)
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

        # Alles außer den Knöpfen in einen Scrollbereich: der Dialog wollte
        # zuletzt 732 px in der Höhe, auf einem 1366×768-Schirm lag „Speichern"
        # damit unterhalb des Bildrands und war nicht erreichbar. Die Knöpfe
        # bleiben außerhalb, damit sie immer sichtbar sind.
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.addLayout(form)
        for widget in (
            self.tts_enabled, self.voice_hint, self.notifications,
            self.silence_warn, self.clipboard_fallback, clipboard_warning,
            self.stt_enabled, self.stt_clipboard_restore, stt_clipboard_hint,
            self.stop_enabled, self.autostart,
        ):
            inner_layout.addWidget(widget)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
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

    def _shortcut(self, modifier: QComboBox, trigger: QComboBox) -> tuple:
        return (tuple(MODIFIER_OPTIONS[modifier.currentText()]), trigger.currentData())

    def accept(self) -> None:
        # Immer ablehnen, unabhängig von den aktiviert-Checkboxen: die
        # Konfiguration selbst soll gar nicht kollidieren können. Über die Liste
        # statt paarweise verdrahtet: vier Funktionen wären sechs feste
        # Vergleiche, fünf wären zehn — so kostet die nächste Funktion eine
        # Zeile und keine Fallunterscheidung.
        combinations = {
            "Aufnahme": self._shortcut(self.modifier, self.trigger),
            "Vorlesen": self._shortcut(self.tts_modifier, self.tts_trigger),
            "Diktat": self._shortcut(self.stt_modifier, self.stt_trigger),
            "Abbrechen": self._shortcut(self.stop_modifier, self.stop_trigger),
        }
        names = list(combinations)
        for index, first in enumerate(names):
            for second in names[index + 1:]:
                if combinations[first] == combinations[second]:
                    QMessageBox.warning(
                        self,
                        "Hotkey-Kollision",
                        f"{first}- und {second}-Hotkey sind identisch. "
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
        config.stt_enabled = self.stt_enabled.isChecked()
        config.stt_modifiers = MODIFIER_OPTIONS[self.stt_modifier.currentText()]
        config.stt_trigger_key = self.stt_trigger.currentData()
        config.stt_model = self.stt_model.currentData()
        config.stt_language = self.stt_language.currentData()
        config.stt_threshold = self.stt_threshold.value()
        config.stt_clipboard_restore = self.stt_clipboard_restore.isChecked()
        config.stop_enabled = self.stop_enabled.isChecked()
        config.stop_modifiers = MODIFIER_OPTIONS[self.stop_modifier.currentText()]
        config.stop_trigger_key = self.stop_trigger.currentData()


class TrayApplication:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.config = Config.load()
        self.config.save()
        self.recorder = AudioRecorder()
        self.hotkey: HotkeyThread | None = None
        self.speech: SpeechThread | None = None
        self.dictation = Recording()
        self.stt: DictationThread | None = None
        self._last_speech_status: str | None = None
        self._last_stt_status: str | None = None
        # Ablagekopie für den Notfall-Rückweg nach einem terminate(), siehe
        # `finish_dictation`.
        self._stt_clipboard: tuple | None = None
        self._hotkey_failed = False
        self._hotkey_missing: set[str] = set()
        self._silence_bridge = _SilenceBridge()
        self._silence_bridge.warned.connect(self._silence_warning)
        themed = QIcon.fromTheme(APP_ID)
        self.icon_idle = themed if not themed.isNull() else tray_icon("#2f9e44")
        self.icon_disabled = tray_icon("#868e96")
        self.icon_recording = tray_icon("#e03131")
        self.icon_speaking = tray_icon("#1971c2")
        self.icon_dictating = tray_icon("#f08c00")
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
        self.stt_enabled_action = QAction(
            f"Diktat-Hotkey {shortcut_label(self.config, stt=True)} aktiv", self.menu
        )
        self.stt_enabled_action.setCheckable(True)
        self.stt_enabled_action.setChecked(self.config.stt_enabled)
        self.stt_enabled_action.toggled.connect(self.set_stt_enabled)
        self.stop_enabled_action = QAction(
            f"Abbrechen-Hotkey {shortcut_label(self.config, stop=True)} aktiv", self.menu
        )
        self.stop_enabled_action.setCheckable(True)
        self.stop_enabled_action.setChecked(self.config.stop_enabled)
        self.stop_enabled_action.toggled.connect(self.set_stop_enabled)
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
        self.menu.addAction(self.stt_enabled_action)
        self.menu.addAction(self.stop_enabled_action)
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
        # Dieselbe Quelle wie die Statuszeile: fest verdrahtet versprach die
        # Blase Kürzel, die gar nicht bedient werden.
        ready = " · ".join(self._ready_shortcuts())
        self._notify(
            "PC-Ton & Vorlesen",
            f"Bereit: {ready}" if ready else "Kein Kürzel aktiv.",
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
        # Zuerst die Aufnahme, dann der Faden: `_restart_hotkey` löst schon ein
        # Linksklick aufs Tray-Symbol aus. Fällt der zwischen Druck und
        # Loslassen, gehört die Loslass-Flanke einem Faden, den wir gleich
        # beenden — pw-record liefe sonst weiter und schriebe ins Leere.
        # Der Faden feuert `stt_released` beim Ende noch nach (hotkey.py,
        # finally); das trifft dann auf eine beendete Aufnahme und tut nichts.
        if self.dictation.is_recording:
            self.dictation.cancel()
            self._notify(
                "Diktat abgebrochen",
                "Die Tastenüberwachung wurde neu gestartet, während das Diktat lief.",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
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
        # Neuer Versuch, neue Chance: die Merker fallen erst, wenn der neue
        # Thread `unavailable` bzw. `degraded` wieder meldet.
        self._hotkey_failed = False
        self._hotkey_missing = set()
        if (
            self.config.enabled
            or self.config.tts_enabled
            or self.config.stt_enabled
            or self.config.stop_enabled
        ):
            self.hotkey = HotkeyThread(
                self.config.trigger_key,
                self.config.modifiers,
                record_enabled=self.config.enabled,
                tts_trigger=self.config.tts_trigger_key,
                tts_modifiers=self.config.tts_modifiers,
                read_aloud_enabled=self.config.tts_enabled,
                stt_trigger=self.config.stt_trigger_key,
                stt_modifiers=self.config.stt_modifiers,
                stt_enabled=self.config.stt_enabled,
                stop_trigger=self.config.stop_trigger_key,
                stop_modifiers=self.config.stop_modifiers,
                stop_enabled=self.config.stop_enabled,
            )
            self.hotkey.pressed.connect(self.toggle_recording)
            self.hotkey.read_aloud_pressed.connect(self.speak_selected_text)
            self.hotkey.stt_pressed.connect(self.start_dictation)
            self.hotkey.stt_released.connect(self.finish_dictation)
            self.hotkey.stop_pressed.connect(self.cancel_playback)
            self.hotkey.unavailable.connect(self.hotkey_error)
            self.hotkey.degraded.connect(self.hotkey_degraded)
            self.hotkey.start()

    def _tick(self) -> None:
        if not self.recorder.is_recording:
            self.duration_timer.stop()
            self._refresh()
            return
        elapsed = time.monotonic() - self.recorder.started_at
        minutes, seconds = divmod(int(elapsed), 60)
        stamp = f"{minutes:02d}:{seconds:02d}"
        # Über `_refresh`, nicht direkt gesetzt: sonst überschriebe der Sekunden-
        # takt jede Diktatmeldung, und die Zusammenführung beider Zustände
        # stünde an zwei Stellen. Auch die Uhr geht durch `_refresh` — ein
        # zweites `setToolTip()` hier hinterher nähme den Diktatteil wieder aus
        # dem Tooltip heraus und kostete je Sekunde ein zusätzliches DBus-Signal.
        self._refresh(f"● Aufnahme läuft … {stamp}", stamp=stamp)
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

    def _ready_shortcuts(self) -> list[str]:
        """Nur die Kürzel, die auch wirklich eine Tastatur bedient.

        Ohne den Abzug von `_hotkey_missing` verspricht das Tray dauerhaft ein
        Kürzel, das auf dieser Hardware nie auslöst.
        """
        shortcuts = []
        if self.config.enabled and "record" not in self._hotkey_missing:
            shortcuts.append(f"{shortcut_label(self.config)} Aufnahme")
        if self.config.tts_enabled and "tts" not in self._hotkey_missing:
            shortcuts.append(f"{shortcut_label(self.config, tts=True)} Vorlesen")
        if self.config.stt_enabled and "stt" not in self._hotkey_missing:
            shortcuts.append(f"{shortcut_label(self.config, stt=True)} Diktat")
        if self.config.stop_enabled and "stop" not in self._hotkey_missing:
            shortcuts.append(f"{shortcut_label(self.config, stop=True)} Abbrechen")
        return shortcuts

    def _dictation_status(self) -> tuple[str, str] | None:
        """(Statuszeile, Tooltip) des Diktats, oder None wenn keins läuft."""
        if self.dictation.is_recording:
            return "● Diktat: hört zu …", "Diktat – hört zu"
        if self.stt and self.stt.isRunning():
            return "✳ Diktat: erkennt …", "Diktat – erkennt"
        return None

    def _refresh(self, status: str | None = None, *, stamp: str | None = None) -> None:
        dictation = self._dictation_status()
        if self.recorder.is_recording:
            # Aufnahme und Diktat schließen einander nicht aus: die Aufnahme
            # hängt am ffmpeg-Monitor, das Diktat an pw-record am Mikrofon —
            # zwei getrennte Wege, und das ist Absicht. Deshalb werden beide
            # Zustände in eine Zeile gelegt, statt der Aufnahme den Vortritt zu
            # geben; sonst wäre das Diktat während einer Aufnahme unsichtbar.
            # Das Symbol bleibt rot: eine laufende Aufnahme darf nie wie etwas
            # anderes aussehen.
            text = status or "● Aufnahme läuft …"
            self.tray.setIcon(self.icon_recording)
            self.tray.setToolTip(
                "PC-Ton aufnehmen – Aufnahme läuft"
                + (f" ({stamp})" if stamp else "")
                + (f" · {dictation[1]}" if dictation else "")
            )
            self.status_action.setText(
                f"{text} · {dictation[0]}" if dictation else text
            )
        elif dictation:
            self.tray.setIcon(self.icon_dictating)
            self.tray.setToolTip(dictation[1])
            self.status_action.setText(status or dictation[0])
        elif self.speech and self.speech.isRunning():
            self.tray.setIcon(self.icon_speaking)
            self.tray.setToolTip(f"Mimic spricht mit {self.config.tts_voice}")
            self.status_action.setText(status or f"▶ Mimic spricht: {self.config.tts_voice}")
        elif self._hotkey_failed:
            # Vor dem Bereit-Zweig: ein toter Hotkey-Thread zeigte sonst nach
            # Ablauf der Meldung wieder Grün, der Ausfall wäre unsichtbar.
            self.tray.setIcon(self.icon_disabled)
            self.tray.setToolTip("PC-Ton & Vorlesen – Hotkey nicht verfügbar")
            self.status_action.setText(status or "Hotkey nicht verfügbar")
        elif (
            self.config.enabled
            or self.config.tts_enabled
            or self.config.stt_enabled
            or self.config.stop_enabled
        ):
            self.tray.setIcon(self.icon_idle)
            ready = " · ".join(self._ready_shortcuts()) or "kein Kürzel bedient"
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

    def set_stt_enabled(self, enabled: bool) -> None:
        if not enabled and self.dictation.is_recording:
            self.dictation.cancel()
        self.config.stt_enabled = enabled
        self.config.save()
        self._restart_hotkey()
        self._refresh()

    def set_stop_enabled(self, enabled: bool) -> None:
        # Kein Aufräumen beim Ausschalten: das Abbrechen hält keinen Zustand.
        self.config.stop_enabled = enabled
        self.config.save()
        self._restart_hotkey()
        self._refresh()

    def cancel_playback(self) -> None:
        """Beendet, was gerade läuft — und startet grundsätzlich nichts Neues.

        Der Unterschied zu einem zweiten Druck auf das Vorlese-Kürzel: das
        stoppt zwar auch, legt aber sofort ein neues Vorlesen nach. Bricht
        nichts ab, weil nichts läuft, ist das kein Fehler und wird nicht
        gemeldet.
        """
        if not self.config.stop_enabled:
            return
        cancelled: list[str] = []
        if self.speech is not None and self.speech.isRunning():
            self.stop_speaking()
            self._last_speech_status = "Vorlesen abgebrochen"
            cancelled.append("Vorlesen abgebrochen")
        # Diktat: entweder läuft noch die Aufnahme oder schon die Erkennung.
        # Nie beides, deshalb elif.
        if self.dictation.is_recording:
            self.dictation.cancel()
            self._last_stt_status = "Diktat abgebrochen"
            cancelled.append("Diktat abgebrochen")
        elif self.stt is not None and self.stt.isRunning():
            self.stt.cancel()
            self._last_stt_status = "Diktat abgebrochen"
            cancelled.append("Diktat abgebrochen")
        if cancelled:
            self._refresh(" · ".join(cancelled))

    # --- Diktat -----------------------------------------------------------

    def start_dictation(self) -> None:
        if not self.config.stt_enabled or self.dictation.is_recording:
            return
        if self.stt is not None and self.stt.isRunning():
            # Das vorige Diktat wird noch erkannt. Ein zweites danebenlegen
            # hieße zwei Modelle gleichzeitig im VRAM und zwei Texte, die um
            # dieselbe Einfügestelle streiten.
            self._notify(
                "Diktat läuft noch",
                "Das vorherige Diktat wird gerade erkannt. Bitte kurz warten.",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            return
        try:
            self.dictation.start()
        except OSError as error:
            self._refresh("Diktat nicht gestartet")
            self._notify(
                "Diktat nicht gestartet",
                f"pw-record ließ sich nicht starten: {error}",
                QSystemTrayIcon.MessageIcon.Critical,
                5000,
            )
            return
        self._refresh()

    def finish_dictation(self) -> None:
        if not self.dictation.is_recording:
            return
        duration = self.dictation.stop()
        if duration < self.config.stt_min_seconds:
            # Ein Tippen auf die Taste ist ein Verklicker, kein Diktat. Ohne
            # diese Sperre erfindet Whisper aus dem Bruchteil einer Sekunde
            # einen Satz.
            self.dictation.path.unlink(missing_ok=True)
            self._refresh("Diktat zu kurz – verworfen")
            # Wie „zu leise": ein verworfenes Diktat ist ein Ergebnis, das der
            # Nutzer erfahren muss. Nur in der Statuszeile blieb es unbemerkt.
            self._notify(
                "Diktat",
                f"Zu kurz – verworfen (unter {self.config.stt_min_seconds} s "
                "gehalten).",
                QSystemTrayIcon.MessageIcon.Warning,
                3000,
            )
            return
        # Die Ablage **hier** sichern, nicht erst im Faden: `shutdown()` bricht
        # eine hängende Erkennung mit `terminate()` ab, und das rollt keine
        # Python-Frames ab — trifft es das 0,3-s-Fenster in `stt.paste()`,
        # bleibt der Diktattext in CLIPBOARD und PRIMARY stehen und der
        # vorherige Inhalt ist weg. Ein try/finally im Faden hilft dagegen
        # nicht, terminate() umgeht es. Also hält der Controller eine eigene
        # Kopie und legt sie nach dem Abbruch im GUI-Faden zurück.
        self._stt_clipboard = (
            snapshot_clipboard() if self.config.stt_clipboard_restore else None
        )
        worker = DictationThread(
            self.dictation.path,
            model=self.config.stt_model,
            language=self.config.stt_language,
            device=self.config.stt_device,
            threshold=self.config.stt_threshold,
            clipboard_restore=self.config.stt_clipboard_restore,
        )
        self.stt = worker
        worker.result.connect(lambda ok, message: self._dictation_result(worker, ok, message))
        worker.finished.connect(lambda: self._dictation_finished(worker))
        worker.start()
        self._refresh()

    def _dictation_result(self, worker: DictationThread, ok: bool, message: str) -> None:
        if worker is not self.stt:
            return
        if getattr(worker, "cancelled", False):
            # Abgebrochen: eine nachgereichte Meldung („Zu leise", „Nichts
            # verstanden") überschriebe sonst das „Diktat abgebrochen" und
            # ließe den Abbruch wie ein Erkennungsproblem aussehen.
            return
        self._last_stt_status = message
        if not ok:
            self._notify(
                "Diktat", message, QSystemTrayIcon.MessageIcon.Warning, 5000
            )
        elif self.recorder.is_recording:
            # Läuft nebenher eine Aufnahme, nimmt ihr Sekundentakt die
            # Statuszeile binnen einer Sekunde zurück – dann ist die
            # Benachrichtigung der einzige Weg, der ankommt.
            self._notify(
                "Diktat", message, QSystemTrayIcon.MessageIcon.Information, 3000
            )

    def _dictation_finished(self, worker: DictationThread) -> None:
        if worker is not self.stt:
            return
        self.stt = None
        self._stt_clipboard = None
        self._refresh(self._last_stt_status)

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
        self._hotkey_failed = True
        self._refresh("Hotkey nicht verfügbar")
        self._notify(
            "Hotkey nicht verfügbar", message,
            QSystemTrayIcon.MessageIcon.Critical, 6000,
        )

    def hotkey_degraded(self, missing: list[str]) -> None:
        """Teilausfall: der Rest läuft weiter, das Fehlende wird benannt.

        Warning statt Critical und kein graues Symbol — eine intakte Aufnahme
        als kaputt auszuweisen wäre schlimmer als die Lücke. Aber still bleibt
        es nicht: der Bereit-Text nennt ab jetzt nur die bedienten Kürzel.
        """
        self._hotkey_missing = set(missing)
        labels = {
            "record": f"{shortcut_label(self.config)} (Aufnahme)",
            "tts": f"{shortcut_label(self.config, tts=True)} (Vorlesen)",
            "stt": f"{shortcut_label(self.config, stt=True)} (Diktat)",
            "stop": f"{shortcut_label(self.config, stop=True)} (Abbrechen)",
        }
        broken = ", ".join(labels.get(name, name) for name in missing)
        self._refresh()
        self._notify(
            "Hotkey teilweise nicht verfügbar",
            f"Keine angeschlossene Tastatur hat die Tasten für {broken}. "
            "Wähle in den Einstellungen eine andere Kombination.",
            QSystemTrayIcon.MessageIcon.Warning, 6000,
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
        self.stt_enabled_action.setText(
            f"Diktat-Hotkey {shortcut_label(self.config, stt=True)} aktiv"
        )
        self.stt_enabled_action.setChecked(self.config.stt_enabled)
        self.stop_enabled_action.setText(
            f"Abbrechen-Hotkey {shortcut_label(self.config, stop=True)} aktiv"
        )
        self.stop_enabled_action.setChecked(self.config.stop_enabled)
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
        # Laufendes Diktat: erst die Aufnahme wegwerfen, dann die Erkennung
        # einsammeln — sonst bleibt pw-record verwaist stehen.
        if self.dictation.is_recording:
            self.dictation.cancel()
        else:
            # Auch ohne laufende Aufnahme: die WAV mit dem Gesprochenen gehört
            # dem Faden, und `terminate()` überspringt dessen `finally`. Ohne
            # dieses unlink überlebt die Sprachaufnahme das Programmende.
            self.dictation.path.unlink(missing_ok=True)
        if self.stt is not None:
            if self.stt.isRunning() and not self.stt.wait(2000):
                # Kein „QThread destroyed while running" beim Prozessende.
                # Die Erkennung ist unterbrechbar: die Aufnahme ist ohnehin weg.
                self.stt.terminate()
                self.stt.wait(500)
                # terminate() kann mitten im Einfügen getroffen haben; dann
                # steht der Diktattext in beiden Ablagen. Die Kopie aus
                # `finish_dictation` zurücklegen, im GUI-Faden.
                #
                # Nur wenn der Faden die Ablage überhaupt angefasst hat: in der
                # Regel trifft terminate() die Erkennung (1,3 s auf der GPU,
                # deutlich mehr auf der CPU) und nicht das 0,3-s-Fenster in
                # `paste()`. Blind zurückschreiben hieße, den unveränderten
                # Inhalt des Nutzers mit `--sensitive` neu zu setzen — und das
                # nimmt ihn aus der Klipper-Historie, obwohl PTR die Ablage nie
                # angerührt hat.
                saved = getattr(self, "_stt_clipboard", None)
                if saved is not None and getattr(self.stt, "clipboard_touched", False):
                    restore_clipboard(saved[0])
                    restore_clipboard(saved[1], primary=True)
            self.stt = None
        self._stt_clipboard = None
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
