from __future__ import annotations

import selectors
import threading

from PySide6.QtCore import QThread, Signal

try:
    import evdev
except ModuleNotFoundError:
    # Kein hartes `import evdev`: app.py zieht dieses Modul beim Start, der
    # Import sitzt also auf Modulebene und schlägt zu, *bevor* die
    # QApplication existiert. Fehlt das Paket, starb PTR damit wortlos im
    # Import — kein Tray, keine Meldung, nichts zu sehen. Stattdessen None,
    # und gemeldet wird erst in `run()`, wo es ein Tray gibt, das die Meldung
    # dauerhaft zeigen kann.
    evdev = None

# Getrennt vom Rechte-Fall ("Gruppe input fehlt"): fehlendes Paket und fehlende
# Rechte brauchen verschiedene Befehle. `--asexplicit`, weil python-evdev sonst
# als verwaiste Abhängigkeit gilt und das nächste `pacman -Rns` es wieder
# mitnimmt — genau so ist es am 2026-08-30 verschwunden.
EVDEV_MISSING = (
    "Das Paket python-evdev fehlt – alle vier Kürzel (Aufnahme, Vorlesen, "
    "Diktat, Abbrechen) lösen nicht aus. Zurückholen mit: "
    "sudo pacman -S --asexplicit python-evdev"
)


def invalid_key_names(names: tuple[str, ...] | list[str]) -> list[str]:
    """Return configured key names that evdev does not know."""
    return [name for name in names if evdev.ecodes.ecodes.get(name) is None]


class HotkeyThread(QThread):
    pressed = Signal()
    read_aloud_pressed = Signal()
    stt_pressed = Signal()
    stt_released = Signal()
    stop_pressed = Signal()
    unavailable = Signal(str)
    # Teilausfall: Liste der Funktionsnamen ("record", "tts", "stt", "stop"), für die
    # keine Tastatur alle Tasten trägt. Getrennt von `unavailable`, weil der
    # Rest weiterläuft — grau und "nicht verfügbar" wäre schlicht falsch.
    degraded = Signal(list)

    def __init__(
        self,
        trigger: str,
        modifiers: tuple[str, ...],
        record_enabled: bool = True,
        tts_trigger: str = "KEY_SCROLLLOCK",
        tts_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",),
        read_aloud_enabled: bool = True,
        stt_trigger: str = "KEY_PAUSE",
        stt_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",),
        stt_enabled: bool = False,
        stop_trigger: str = "KEY_Z",
        stop_modifiers: tuple[str, ...] = ("KEY_LEFTMETA",),
        stop_enabled: bool = False,
    ) -> None:
        super().__init__()
        self.trigger = trigger
        self.modifiers = tuple(modifiers)
        self.record_enabled = record_enabled
        self.tts_trigger = tts_trigger
        self.tts_modifiers = tuple(tts_modifiers)
        self.read_aloud_enabled = read_aloud_enabled
        self.stt_trigger = stt_trigger
        self.stt_modifiers = tuple(stt_modifiers)
        self.stt_enabled = stt_enabled
        self.stop_trigger = stop_trigger
        self.stop_modifiers = tuple(stop_modifiers)
        self.stop_enabled = stop_enabled
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def _functions(self) -> dict[str, tuple[str, ...]]:
        """Tastennamen je aktivierter Funktion, getrennt gehalten.

        Getrennt, weil eine gemeinsame Pflichtmenge jede Tastatur aussperrt,
        der auch nur eine der Tasten fehlt — eine Tastatur ohne KEY_PAUSE legte
        so Aufnahme und Vorlesen gleich mit lahm.
        """
        functions: dict[str, tuple[str, ...]] = {}
        if self.record_enabled:
            functions["record"] = (self.trigger, *self.modifiers)
        if self.read_aloud_enabled:
            functions["tts"] = (self.tts_trigger, *self.tts_modifiers)
        if self.stt_enabled:
            functions["stt"] = (self.stt_trigger, *self.stt_modifiers)
        if self.stop_enabled:
            functions["stop"] = (self.stop_trigger, *self.stop_modifiers)
        return functions

    def _keyboards(self) -> list[tuple[evdev.InputDevice, frozenset[str]]]:
        """Geräte samt der Funktionen, die sie vollständig bedienen können.

        Vereinigungsmenge: ein Gerät kommt in Frage, sobald es die Tasten
        mindestens einer Funktion trägt. Welche das sind, wandert mit, damit
        beim Ereignis geprüft werden kann, ob das Gerät die Funktion überhaupt
        bedienen kann.
        """
        required = {
            name: {evdev.ecodes.ecodes[key] for key in keys}
            for name, keys in self._functions().items()
        }
        devices: list[tuple[evdev.InputDevice, frozenset[str]]] = []
        for path in evdev.list_devices():
            try:
                device = evdev.InputDevice(path)
                keys = set(device.capabilities().get(evdev.ecodes.EV_KEY, []))
                served = frozenset(
                    name for name, codes in required.items() if codes.issubset(keys)
                )
                if served and "ydotool" not in device.name.lower():
                    devices.append((device, served))
                else:
                    device.close()
            except (OSError, PermissionError):
                continue
        return devices

    def _invalid_configured_keys(self) -> list[str]:
        names: list[str] = []
        for keys in self._functions().values():
            names += keys
        return invalid_key_names(names)

    def run(self) -> None:
        if evdev is None:
            self.unavailable.emit(EVDEV_MISSING)
            return
        invalid = self._invalid_configured_keys()
        if invalid:
            self.unavailable.emit(
                "Ungültige Tastennamen in den Einstellungen: " + ", ".join(invalid)
            )
            return
        devices = self._keyboards()
        if not devices:
            self.unavailable.emit(
                "Keine passende Tastatur lesbar (ignoriert werden nur Tastaturen, die "
                "für keine einzige Funktion alle Tasten haben). Prüfe die Mitgliedschaft "
                "in der Gruppe input."
            )
            return
        # Eine Funktion ohne Gerät ist kein Totalausfall: was laufen kann, läuft.
        # Gemeldet wird trotzdem, sonst verspricht das Tray still ein Kürzel,
        # das nie auslöst.
        served = frozenset().union(*(names for _device, names in devices))
        missing = sorted(set(self._functions()) - served)
        if missing:
            self.degraded.emit(missing)
        selector = selectors.DefaultSelector()
        # `down` je Gerät statt einer gemeinsamen Menge: die Vereinigung trägt
        # weiter den Modifier auf Tastatur A zum Auslöser auf Tastatur B, aber
        # ein verschwindendes Gerät nimmt seine hängenden Tasten mit — und der
        # Watchdog unten kann fragen, wo eine Taste noch gehalten wird.
        down: dict[object, set[str]] = {}
        for device, names in devices:
            selector.register(device, selectors.EVENT_READ, names)
            down[id(device)] = set()

        def held(key: str) -> bool:
            return any(key in keys for keys in down.values())

        stt_active = False
        try:
            while not self._stop_event.is_set():
                for selected, _ in selector.select(timeout=0.25):
                    served = selected.data
                    device = selected.fileobj
                    # `try` um die ganze Leseschleife, nicht nur um den Aufruf:
                    # InputDevice.read() ist eine Generatorfunktion, der Aufruf
                    # allein führt keine Zeile aus. Der OSError des
                    # verschwundenen Geräts fällt erst beim ersten next(), also
                    # hier im `for` — säße das `except` nur am Aufruf, wäre es
                    # toter Code und der Thread stürbe mit Traceback.
                    try:
                        for event in device.read():
                            if event.type != evdev.ecodes.EV_KEY or event.value == 2:
                                continue
                            name = evdev.ecodes.KEY.get(event.code)
                            if name is None:
                                continue
                            if not isinstance(name, str):
                                name = name[0]
                            if event.value == 1:
                                down[id(device)].add(name)
                                if (
                                    "record" in served
                                    and name == self.trigger
                                    and all(held(key) for key in self.modifiers)
                                ):
                                    self.pressed.emit()
                                elif (
                                    "tts" in served
                                    and name == self.tts_trigger
                                    and all(held(key) for key in self.tts_modifiers)
                                ):
                                    self.read_aloud_pressed.emit()
                                elif (
                                    "stt" in served
                                    and not stt_active
                                    and name == self.stt_trigger
                                    and all(held(key) for key in self.stt_modifiers)
                                ):
                                    # `not stt_active`: eine zweite Tastatur (oder
                                    # ein zweiter Event-Node desselben Geräts) darf
                                    # kein zweites Diktat starten, das nur einmal
                                    # beendet wird.
                                    stt_active = True
                                    self.stt_pressed.emit()
                                elif (
                                    "stop" in served
                                    and name == self.stop_trigger
                                    and all(held(key) for key in self.stop_modifiers)
                                ):
                                    self.stop_pressed.emit()
                            else:
                                down[id(device)].discard(name)
                                # Loslassen beendet über beide Wege: Auslöser los
                                # oder Modifier fällt — sonst liefe die Aufnahme
                                # weiter, obwohl niemand mehr hält (ptt.py:145-148).
                                # Nur vom bedienenden Gerät: sonst beendet ein
                                # Meta-Tipper auf einer zweiten Tastatur das Diktat
                                # mitten im Satz. Gegen die verschluckte Flanke
                                # steht der Watchdog unten.
                                if (
                                    stt_active
                                    and "stt" in served
                                    and name in (self.stt_trigger, *self.stt_modifiers)
                                ):
                                    stt_active = False
                                    self.stt_released.emit()
                    except BlockingIOError:
                        # Kein Ausfall, nur "gerade nichts zu lesen". Muss vor
                        # dem OSError-Zweig stehen: BlockingIOError ist eine
                        # Unterklasse davon und meldete sonst ein gesundes
                        # Gerät ab.
                        continue
                    except OSError:
                        # Gerät weg (USB gezogen, Bluetooth getrennt). Ohne
                        # unregister bleibt der Deskriptor dauerhaft ready
                        # (EPOLLHUP), select() kehrt sofort zurück und die
                        # Schleife dreht heiß, bis PTR neu startet.
                        selector.unregister(device)
                        # `devices` und `down` in einem Zug: id() als Schlüssel
                        # wird nach dem Freigeben des Objekts wiederverwendet,
                        # ein zurückgelassener Eintrag träfe später ein fremdes
                        # Gerät.
                        devices = [
                            entry for entry in devices if entry[0] is not device
                        ]
                        down.pop(id(device), None)
                        try:
                            device.close()
                        except OSError:
                            pass
                        # Mit dem Gerät kann eine Funktion wegfallen, obwohl
                        # andere Tastaturen bleiben — dann meldet nichts den
                        # Ausfall und das Tray verspricht weiter ein totes
                        # Kürzel. Nur bei Änderung feuern, nicht je Durchlauf.
                        if devices:
                            lost = sorted(
                                set(self._functions())
                                - frozenset().union(
                                    *(names for _device, names in devices)
                                )
                            )
                            if lost != missing:
                                missing = lost
                                self.degraded.emit(missing)
                        continue
                # Watchdog: hält kein STT-Gerät mehr den Auslöser, ist die
                # Loslass-Flanke verschluckt worden (das Gerät ist weg) — sonst
                # hinge das Diktat ohne Rückweg.
                if stt_active and not any(
                    self.stt_trigger in down.get(id(other), ())
                    for other, names in devices
                    if "stt" in names
                ):
                    stt_active = False
                    self.stt_released.emit()
                if not devices:
                    self.unavailable.emit(
                        "Alle Tastaturen wurden getrennt. Nach dem Wiederanstecken "
                        "hilft ein Neustart von PC-Ton & Vorlesen."
                    )
                    return
        finally:
            # Endet die Schleife mit laufendem Diktat — stop() beim Übernehmen
            # neuer Einstellungen, Ausnahme, Totalausfall —, käme sonst nie ein
            # Ende beim Verbraucher an und die Aufnahme hinge.
            if stt_active:
                self.stt_released.emit()
            selector.close()
            for device, _names in devices:
                device.close()
