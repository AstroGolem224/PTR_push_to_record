from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from dataclasses import asdict, dataclass, fields


APP_ID = "pc-sound-recorder"


def default_output_dir() -> pathlib.Path:
    """Return a pleasant, localized default without requiring xdg-user-dirs."""
    if shutil.which("xdg-user-dir"):
        result = subprocess.run(
            ["xdg-user-dir", "MUSIC"], capture_output=True, text=True, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            return pathlib.Path(result.stdout.strip()) / "PC-Aufnahmen"
    return pathlib.Path.home() / "Music" / "PC-Aufnahmen"


def config_path() -> pathlib.Path:
    base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    return base / APP_ID / "config.json"


@dataclass
class Config:
    enabled: bool = True
    trigger_key: str = "KEY_F8"
    modifiers: tuple[str, ...] = ("KEY_LEFTMETA",)
    output_dir: str = ""
    autostart: bool = True
    tts_enabled: bool = True
    tts_voice: str = "forge"
    format: str = "mp3"
    quality: int = 2
    mix_microphone: bool = False
    max_minutes: int = 0
    notifications: bool = True
    silence_warn: bool = True
    # Rollen statt F9: Meta+F9 ist auf Standard-Plasma KWins "Fenster der
    # aktuellen Arbeitsflaeche anzeigen" (Expose). PTR liest eine Ebene tiefer
    # ueber evdev, also feuern beide -- die Uebersicht springt beim Vorlesen auf.
    # Meta+Rollen ist in KDE unbelegt und schreibt kein Zeichen.
    tts_trigger_key: str = "KEY_SCROLLLOCK"
    tts_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",)
    tts_clipboard_fallback: bool = False
    stt_enabled: bool = False
    stt_trigger_key: str = "KEY_PAUSE"
    stt_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",)
    stt_model: str = "large-v3-turbo"
    stt_language: str = "de"
    stt_device: str = "cuda"
    # `int8_float16` statt `float16`: warm sind alle Quantisierungen gleich
    # schnell (0,101/0,110/0,104 s über 20 s Ton, gemessen 2026-08-28), aber
    # `int8_float16` belegt 1,6 GB statt 2,6 GB VRAM. Kalt gewann `float16`,
    # weil das Quantisieren beim Modellaufbau kostet — warm fällt das genau
    # einmal an. Siehe stt.load_model().
    stt_compute_type: str = "int8_float16"
    # Minuten ohne Diktat, nach denen das Modell das VRAM wieder freigibt.
    # 0 = nie freigeben. Warm kostet das Erkennen 0,13 s statt 1,75 s; der
    # Preis sind 1,6 GB VRAM, die neben einem großen LLM fehlen. Zehn Minuten
    # halten eine Diktatsitzung zusammen und geben die Karte frei, sobald
    # wieder programmiert wird.
    # ponytail: beide nur in der Datei, nicht im Einstellungsdialog. Wer die
    # Karte für ein LLM braucht, ändert eine Zahl in config.json; wer nicht,
    # merkt nichts. Nachrüsten, wenn jemand zwischen den Werten hin und her
    # stellen muss, ohne PTR neu zu starten.
    stt_warm_minutes: int = 10
    stt_threshold: float = 0.015
    # Kürzere Drücke sind Verklicker, kein Diktat. Ohne diese Sperre erfindet
    # Whisper aus dem Bruchteil einer Sekunde einen Satz.
    stt_min_seconds: float = 0.3
    # Steuert, ob der bisherige Inhalt der Zwischenablage vor dem Einfügen
    # gelesen und danach zurückgelegt wird. Siehe stt.paste().
    stt_clipboard_restore: bool = True
    # Abbruch-Kürzel. Anders als das Diktat braucht es keine Einrichtung und
    # kann nichts kaputtmachen (es startet nie etwas), deshalb ab Werk an.
    stop_enabled: bool = True
    # KEY_Z, nicht KEY_Y: evdev benennt Tasten nach US-Belegung, und auf einer
    # deutschen Tastatur sitzt an der KEY_Z-Position die Taste mit der Aufschrift
    # Y. Gemessen am 2026-08-28: Meta + Taste "Y" liefert Code 44 (KEY_Z).
    # Dieselbe Vertauschung, an der in stt.py schon `ydotool type` gescheitert
    # ist ("zeigt" -> "yeigt"). Kein Test faengt das: die Attrappen pruefen
    # gegen den Namen, nicht gegen die Taste unter dem Finger.
    stop_trigger_key: str = "KEY_Z"
    stop_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",)

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = str(default_output_dir())
        self.modifiers = tuple(self.modifiers)
        self.tts_modifiers = tuple(self.tts_modifiers)
        self.stt_modifiers = tuple(self.stt_modifiers)
        self.stop_modifiers = tuple(self.stop_modifiers)

    @classmethod
    def load(cls, path: pathlib.Path | None = None) -> "Config":
        path = path or config_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # fields() statt asdict(cls()): vermeidet den xdg-user-dir-Subprocess,
            # solange output_dir aus der Datei kommt.
            known = {field.name: data[field.name] for field in fields(cls) if field.name in data}
            return cls(**known)
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: pathlib.Path | None = None) -> None:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)


KEY_LABELS = {
    "KEY_F8": "F8",
    "KEY_F9": "F9",
    "KEY_F11": "F11",
    "KEY_F12": "F12",
    "KEY_SCROLLLOCK": "Rollen",
    # KEY_PAUSE und nicht etwa KEY_GRAVE: das ^ links der Eins erzeugt ein
    # Zeichen, und zwar ein totes — es blieb schwebend stehen und verband sich
    # mit dem ersten Buchstaben des Diktats zu ê oder â. Die Pausetaste
    # schreibt nichts, also gibt es nichts wegzuräumen.
    "KEY_PAUSE": "Pause",
    # Beschriftet nach deutscher Aufschrift, nicht nach evdev-Name -- sonst
    # waehlt der Nutzer "Y" und bekommt die Taste daneben.
    "KEY_Z": "Y",
    "KEY_Y": "Z",
}

MODIFIER_OPTIONS = {
    "Meta": ("KEY_LEFTMETA",),
    "Strg+Alt": ("KEY_LEFTCTRL", "KEY_LEFTALT"),
    "Strg+Umschalt": ("KEY_LEFTCTRL", "KEY_LEFTSHIFT"),
}


def shortcut_label(
    config: Config, tts: bool = False, stt: bool = False, stop: bool = False
) -> str:
    if stop:
        trigger, modifiers = config.stop_trigger_key, config.stop_modifiers
    elif stt:
        trigger, modifiers = config.stt_trigger_key, config.stt_modifiers
    elif tts:
        trigger, modifiers = config.tts_trigger_key, config.tts_modifiers
    else:
        trigger, modifiers = config.trigger_key, config.modifiers
    modifier = next(
        (label for label, keys in MODIFIER_OPTIONS.items() if tuple(keys) == tuple(modifiers)),
        "+".join(key.removeprefix("KEY_").title() for key in modifiers),
    )
    return f"{modifier}+{KEY_LABELS.get(trigger, trigger)}"
