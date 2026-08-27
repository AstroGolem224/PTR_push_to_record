from __future__ import annotations

import selectors
import threading

import evdev
from PySide6.QtCore import QThread, Signal


def invalid_key_names(names: tuple[str, ...] | list[str]) -> list[str]:
    """Return configured key names that evdev does not know."""
    return [name for name in names if evdev.ecodes.ecodes.get(name) is None]


class HotkeyThread(QThread):
    pressed = Signal()
    read_aloud_pressed = Signal()
    unavailable = Signal(str)

    def __init__(
        self,
        trigger: str,
        modifiers: tuple[str, ...],
        record_enabled: bool = True,
        tts_trigger: str = "KEY_F9",
        tts_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",),
        read_aloud_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.trigger = trigger
        self.modifiers = tuple(modifiers)
        self.record_enabled = record_enabled
        self.tts_trigger = tts_trigger
        self.tts_modifiers = tuple(tts_modifiers)
        self.read_aloud_enabled = read_aloud_enabled
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _keyboards(self) -> list[evdev.InputDevice]:
        required: set[int] = set()
        if self.record_enabled:
            required.add(evdev.ecodes.ecodes[self.trigger])
            required.update(evdev.ecodes.ecodes[key] for key in self.modifiers)
        if self.read_aloud_enabled:
            required.add(evdev.ecodes.ecodes[self.tts_trigger])
            required.update(evdev.ecodes.ecodes[key] for key in self.tts_modifiers)
        devices: list[evdev.InputDevice] = []
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
                keys = set(device.capabilities().get(evdev.ecodes.EV_KEY, []))
                if required.issubset(keys) and "ydotool" not in device.name.lower():
                    devices.append(device)
                else:
                    device.close()
            except (OSError, PermissionError):
                continue
        return devices

    def _invalid_configured_keys(self) -> list[str]:
        names: list[str] = []
        if self.record_enabled:
            names += [self.trigger, *self.modifiers]
        if self.read_aloud_enabled:
            names += [self.tts_trigger, *self.tts_modifiers]
        return invalid_key_names(names)

    def run(self) -> None:
        invalid = self._invalid_configured_keys()
        if invalid:
            self.unavailable.emit(
                "Ungültige Tastennamen in den Einstellungen: " + ", ".join(invalid)
            )
            return
        devices = self._keyboards()
        if not devices:
            self.unavailable.emit(
                "Keine passende Tastatur lesbar (Tastaturen ohne alle benötigten Tasten "
                "werden ignoriert). Prüfe die Mitgliedschaft in der Gruppe input."
            )
            return
        selector = selectors.DefaultSelector()
        for device in devices:
            selector.register(device, selectors.EVENT_READ)
        down: set[str] = set()
        try:
            while not self._stop_event.is_set():
                for selected, _ in selector.select(timeout=0.25):
                    try:
                        events = selected.fileobj.read()
                    except OSError:
                        continue
                    for event in events:
                        if event.type != evdev.ecodes.EV_KEY or event.value == 2:
                            continue
                        name = evdev.ecodes.KEY.get(event.code)
                        if name is None:
                            continue
                        if not isinstance(name, str):
                            name = name[0]
                        if event.value == 1:
                            down.add(name)
                            if (
                                self.record_enabled
                                and name == self.trigger
                                and all(key in down for key in self.modifiers)
                            ):
                                self.pressed.emit()
                            elif (
                                self.read_aloud_enabled
                                and name == self.tts_trigger
                                and all(key in down for key in self.tts_modifiers)
                            ):
                                self.read_aloud_pressed.emit()
                        else:
                            down.discard(name)
        finally:
            selector.close()
            for device in devices:
                device.close()
