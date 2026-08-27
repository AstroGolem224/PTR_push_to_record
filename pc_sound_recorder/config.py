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
    tts_trigger_key: str = "KEY_F9"
    tts_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",)
    tts_clipboard_fallback: bool = False

    def __post_init__(self) -> None:
        if not self.output_dir:
            self.output_dir = str(default_output_dir())
        self.modifiers = tuple(self.modifiers)
        self.tts_modifiers = tuple(self.tts_modifiers)

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
}

MODIFIER_OPTIONS = {
    "Meta": ("KEY_LEFTMETA",),
    "Strg+Alt": ("KEY_LEFTCTRL", "KEY_LEFTALT"),
    "Strg+Umschalt": ("KEY_LEFTCTRL", "KEY_LEFTSHIFT"),
}


def shortcut_label(config: Config, tts: bool = False) -> str:
    trigger = config.tts_trigger_key if tts else config.trigger_key
    modifiers = config.tts_modifiers if tts else config.modifiers
    modifier = next(
        (label for label, keys in MODIFIER_OPTIONS.items() if tuple(keys) == tuple(modifiers)),
        "+".join(key.removeprefix("KEY_").title() for key in modifiers),
    )
    return f"{modifier}+{KEY_LABELS.get(trigger, trigger)}"
