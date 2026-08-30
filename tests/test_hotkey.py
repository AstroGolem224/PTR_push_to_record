import builtins
import selectors
from types import SimpleNamespace

import evdev

import pc_sound_recorder.hotkey as hotkey_module
from pc_sound_recorder.hotkey import HotkeyThread, invalid_key_names


def test_invalid_key_names_accepts_known_keys():
    assert invalid_key_names(["KEY_F8", "KEY_SCROLLLOCK", "KEY_LEFTMETA"]) == []


def test_invalid_key_names_flags_unknown_keys():
    assert invalid_key_names(["KEY_F8", "KEY_NOPE", "KEY_ALSO_NOPE"]) == [
        "KEY_NOPE",
        "KEY_ALSO_NOPE",
    ]


def test_hotkey_thread_stores_configurable_tts_keys():
    thread = HotkeyThread(
        "KEY_F11",
        ("KEY_LEFTCTRL", "KEY_LEFTALT"),
        tts_trigger="KEY_SCROLLLOCK",
        tts_modifiers=("KEY_LEFTCTRL", "KEY_LEFTSHIFT"),
        read_aloud_enabled=True,
    )
    assert thread.tts_trigger == "KEY_SCROLLLOCK"
    assert thread.tts_modifiers == ("KEY_LEFTCTRL", "KEY_LEFTSHIFT")
    assert thread._invalid_configured_keys() == []


def test_hotkey_thread_reports_invalid_configured_keys():
    thread = HotkeyThread(
        "KEY_F8",
        ("KEY_LEFTMETA",),
        tts_trigger="KEY_NOPE",
        tts_modifiers=("KEY_LEFTMETA",),
        read_aloud_enabled=True,
    )
    assert thread._invalid_configured_keys() == ["KEY_NOPE"]


def test_hotkey_thread_ignores_invalid_tts_keys_when_disabled():
    thread = HotkeyThread(
        "KEY_F8",
        ("KEY_LEFTMETA",),
        tts_trigger="KEY_NOPE",
        read_aloud_enabled=False,
    )
    assert thread._invalid_configured_keys() == []


# --- Ereignisschleife an simulierten Tastaturen ---

FULL = ("KEY_F8", "KEY_SCROLLLOCK", "KEY_LEFTMETA", "KEY_PAUSE")
NO_PAUSE = ("KEY_F8", "KEY_SCROLLLOCK", "KEY_LEFTMETA")


class _FakeDevice:
    """Tastatur ohne evdev: liefert Ereignisse stapelweise, einmal je select().

    Zwei Eigenschaften sind `evdev.InputDevice` nachgebaut, weil ihr Fehlen
    echte Startfehler verdeckt hat:
    * `__hash__ = None` — InputDevice definiert `__eq__`, Python nimmt ihm
      damit den Hash. Das Gerät taugt nicht als dict-Schlüssel.
    * `read()` ist eine Generatorfunktion — der Aufruf führt keinen Code aus,
      Fehler fallen erst beim Iterieren.
    """

    __hash__ = None

    def __init__(self, name: str, keys, batches=()):
        self.name = name
        self._codes = [evdev.ecodes.ecodes[key] for key in keys]
        self._batches = [list(batch) for batch in batches]
        self.closed = False

    def capabilities(self):
        return {evdev.ecodes.EV_KEY: self._codes}

    def pending(self) -> bool:
        return bool(self._batches)

    def read(self):
        yield from self._batches.pop(0)

    def close(self):
        self.closed = True


class _VanishingDevice(_FakeDevice):
    """Gerät, das nach seinen Stapeln verschwindet (USB gezogen, BT getrennt).

    `pending()` bleibt True: genau der heiße Fall — der Deskriptor meldet
    dauerhaft ready. Wird das Gerät nicht abgemeldet, läuft der Test endlos.
    Der OSError fällt wie bei evdev erst beim Iterieren, nicht beim Aufruf.
    """

    def pending(self) -> bool:
        return True

    def read(self):
        if not self._batches:
            raise OSError(19, "No such device")
        yield from self._batches.pop(0)


def _key(name: str, value: int):
    return SimpleNamespace(
        type=evdev.ecodes.EV_KEY, code=evdev.ecodes.ecodes[name], value=value
    )


class _FakeSelector:
    """Gibt jedes Gerät zurück, solange es Stapel hat; danach endet der Lauf."""

    def __init__(self, thread: HotkeyThread):
        self._thread = thread
        self._keys: list[SimpleNamespace] = []

    def register(self, fileobj, events, data=None):
        self._keys.append(SimpleNamespace(fileobj=fileobj, data=data))

    def select(self, timeout=None):
        ready = [(key, selectors.EVENT_READ) for key in self._keys if key.fileobj.pending()]
        if not ready:
            self._thread.stop()
        return ready

    def unregister(self, fileobj):
        self._keys = [key for key in self._keys if key.fileobj is not fileobj]

    def close(self):
        pass


def _run(thread: HotkeyThread, devices, monkeypatch) -> list[str]:
    """Fährt run() an den Fake-Geräten und protokolliert die Signale."""
    paths = [f"/dev/input/event{index}" for index in range(len(devices))]
    monkeypatch.setattr(hotkey_module.evdev, "list_devices", lambda: list(paths))
    monkeypatch.setattr(
        hotkey_module.evdev, "InputDevice", lambda path: devices[paths.index(path)]
    )
    monkeypatch.setattr(
        hotkey_module,
        "selectors",
        SimpleNamespace(
            DefaultSelector=lambda: _FakeSelector(thread),
            EVENT_READ=selectors.EVENT_READ,
        ),
    )
    fired: list[str] = []
    thread.pressed.connect(lambda: fired.append("record"))
    thread.read_aloud_pressed.connect(lambda: fired.append("tts"))
    thread.stt_pressed.connect(lambda: fired.append("stt_pressed"))
    thread.stt_released.connect(lambda: fired.append("stt_released"))
    thread.stop_pressed.connect(lambda: fired.append("stop"))
    thread.unavailable.connect(lambda message: fired.append(f"unavailable:{message}"))
    thread.run()
    return fired


def _stt_thread(**kwargs) -> HotkeyThread:
    return HotkeyThread("KEY_F8", ("KEY_LEFTMETA",), stt_enabled=True, **kwargs)


def test_keyboard_without_stt_key_keeps_record_and_tts(monkeypatch):
    """Der Grund für den ganzen Schritt: KEY_PAUSE fehlt, Meta+F8/F9 tragen."""
    thread = _stt_thread()
    device = _FakeDevice(
        "Tastatur ohne Pause",
        NO_PAUSE,
        [[
            _key("KEY_LEFTMETA", 1),
            _key("KEY_F8", 1), _key("KEY_F8", 0),
            _key("KEY_SCROLLLOCK", 1), _key("KEY_SCROLLLOCK", 0),
            _key("KEY_LEFTMETA", 0),
        ]],
    )
    assert _run(thread, [device], monkeypatch) == ["record", "tts"]


def test_stt_release_on_trigger_up(monkeypatch):
    thread = _stt_thread()
    device = _FakeDevice(
        "Tastatur", FULL,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1), _key("KEY_PAUSE", 0)]],
    )
    assert _run(thread, [device], monkeypatch) == ["stt_pressed", "stt_released"]


def test_stt_release_when_modifier_falls_first(monkeypatch):
    thread = _stt_thread()
    device = _FakeDevice(
        "Tastatur", FULL,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1), _key("KEY_LEFTMETA", 0)]],
    )
    assert _run(thread, [device], monkeypatch) == ["stt_pressed", "stt_released"]


def test_stt_release_needs_a_press_first(monkeypatch):
    """Auslöser ohne Modifier: kein Druck, also auch kein Loslassen."""
    thread = _stt_thread()
    device = _FakeDevice(
        "Tastatur", FULL,
        [[_key("KEY_PAUSE", 1), _key("KEY_PAUSE", 0), _key("KEY_LEFTMETA", 0)]],
    )
    assert _run(thread, [device], monkeypatch) == []


def test_modifier_and_trigger_on_separate_keyboards(monkeypatch):
    """`down` ist gemeinsam: Meta auf Gerät A, Auslöser auf Gerät B."""
    thread = _stt_thread()
    device_a = _FakeDevice("Tastatur A", FULL, [[_key("KEY_LEFTMETA", 1)]])
    device_b = _FakeDevice("Tastatur B", FULL, [[_key("KEY_F8", 1)]])
    assert _run(thread, [device_a, device_b], monkeypatch) == ["record"]


def test_stt_disabled_emits_nothing(monkeypatch):
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",))
    device = _FakeDevice(
        "Tastatur", FULL,
        [[
            _key("KEY_LEFTMETA", 1),
            _key("KEY_PAUSE", 1), _key("KEY_PAUSE", 0),
            _key("KEY_F8", 1), _key("KEY_F8", 0),
            _key("KEY_LEFTMETA", 0),
        ]],
    )
    assert _run(thread, [device], monkeypatch) == ["record"]


def test_keyboards_report_only_the_functions_they_serve(monkeypatch):
    thread = _stt_thread()
    device = _FakeDevice("Tastatur ohne Pause", NO_PAUSE)
    monkeypatch.setattr(hotkey_module.evdev, "list_devices", lambda: ["/dev/input/event0"])
    monkeypatch.setattr(hotkey_module.evdev, "InputDevice", lambda path: device)
    (found, served), = thread._keyboards()
    assert found is device
    assert served == frozenset({"record", "tts"})


def test_partial_failure_is_reported_not_swallowed(monkeypatch):
    """Kein Totalausfall, aber auch nicht still: `degraded` nennt die Lücke."""
    thread = _stt_thread()
    missing: list[list[str]] = []
    thread.degraded.connect(missing.append)
    device = _FakeDevice("Tastatur ohne Pause", NO_PAUSE)
    assert _run(thread, [device], monkeypatch) == []
    assert missing == [["stt"]]


# --- Viertes Kürzel: Abbrechen ---

# KEY_Z ist der Ausloeser: auf deutscher Belegung traegt diese Taste die
# Aufschrift Y. Siehe config.KEY_LABELS.
WITH_Y = FULL + ("KEY_Z",)


def test_stop_shortcut_fires(monkeypatch):
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",), stop_enabled=True)
    device = _FakeDevice(
        "Tastatur", WITH_Y,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_Z", 1), _key("KEY_Z", 0)]],
    )
    assert _run(thread, [device], monkeypatch) == ["stop"]


def test_stop_needs_its_modifier(monkeypatch):
    """Y allein ist ein Buchstabe, kein Abbruch."""
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",), stop_enabled=True)
    device = _FakeDevice("Tastatur", WITH_Y, [[_key("KEY_Z", 1), _key("KEY_Z", 0)]])
    assert _run(thread, [device], monkeypatch) == []


def test_stop_disabled_emits_nothing(monkeypatch):
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",))
    device = _FakeDevice(
        "Tastatur", WITH_Y,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_Z", 1), _key("KEY_Z", 0)]],
    )
    assert _run(thread, [device], monkeypatch) == []


def test_keyboard_without_the_stop_key_is_reported(monkeypatch):
    """Die Funktion muss in `_functions()` stehen, sonst meldet nichts die Lücke."""
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",), stop_enabled=True)
    missing: list[list[str]] = []
    thread.degraded.connect(missing.append)
    device = _FakeDevice("Tastatur ohne Y", NO_PAUSE)
    assert _run(thread, [device], monkeypatch) == []
    assert missing == [["stop"]]
    (found, served), = thread._keyboards()
    assert found is device and served == frozenset({"record", "tts"})


# --- Gerätefilter: Taste vorhanden, Funktion trotzdem nicht bedient ---

# Ein Modifier, den FULL NICHT traegt -- nur so laesst sich pruefen, dass ein
# Geraet ohne ihn die Funktion nicht bedient. KEY_SCROLLLOCK taugt dafuer
# nicht mehr: es ist seit der Umlegung von F9 der Vorlesen-Ausloeser.
WITH_EXTRA = FULL + ("KEY_LEFTCTRL",)


def test_device_without_the_modifier_does_not_trigger_record_or_tts(monkeypatch):
    """Gerät B hat F8 und F9, aber nicht KEY_LEFTCTRL aus der Kombination.

    Der Modifier liegt auf Gerät A und steht damit im gemeinsamen `down` —
    aussortieren kann nur die Prüfung, ob B die Funktion überhaupt bedient.
    """
    thread = HotkeyThread(
        "KEY_F8",
        ("KEY_LEFTMETA", "KEY_LEFTCTRL"),
        tts_modifiers=("KEY_LEFTMETA", "KEY_LEFTCTRL"),
        stt_enabled=True,
    )
    device_a = _FakeDevice(
        "Tastatur A", WITH_EXTRA,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_LEFTCTRL", 1)]],
    )
    device_b = _FakeDevice(
        "Tastatur B", FULL,
        [[_key("KEY_F8", 1), _key("KEY_F8", 0), _key("KEY_SCROLLLOCK", 1), _key("KEY_SCROLLLOCK", 0)]],
    )
    assert _run(thread, [device_a, device_b], monkeypatch) == []


def test_device_without_the_modifier_does_not_start_dictation(monkeypatch):
    """Dasselbe für STT: B hat KEY_PAUSE, aber nicht KEY_LEFTCTRL."""
    thread = HotkeyThread(
        "KEY_F8",
        ("KEY_LEFTMETA",),
        stt_modifiers=("KEY_LEFTMETA", "KEY_LEFTCTRL"),
        stt_enabled=True,
    )
    device_a = _FakeDevice(
        "Tastatur A", WITH_EXTRA,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_LEFTCTRL", 1)]],
    )
    device_b = _FakeDevice("Tastatur B", FULL, [[_key("KEY_PAUSE", 1)]])
    assert _run(thread, [device_a, device_b], monkeypatch) == []


def test_other_keyboard_does_not_end_dictation(monkeypatch):
    """B bedient kein STT: sein Meta-Tipper darf A's Diktat nicht abbrechen.

    Und `down` je Gerät: B's Meta-Loslassen nimmt A das gehaltene Meta nicht
    weg, Meta+F8 auf A löst danach weiter aus.

    KEY_PAUSE bleibt bis zum Schluss gedrückt; das abschließende
    "stt_released" kommt aus dem Netz im `finally` — die Schleife endet mit
    laufendem Diktat.
    """
    thread = _stt_thread()
    device_a = _FakeDevice(
        "Tastatur A", FULL,
        [
            [_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1)],
            [_key("KEY_F8", 1)],
        ],
    )
    device_b = _FakeDevice(
        "Tastatur B ohne Pause", NO_PAUSE,
        [[_key("KEY_LEFTMETA", 1), _key("KEY_LEFTMETA", 0)]],
    )
    assert _run(thread, [device_a, device_b], monkeypatch) == [
        "stt_pressed",
        "record",
        "stt_released",
    ]


def test_second_keyboard_does_not_start_a_second_dictation(monkeypatch):
    """Zwei Geräte mit KEY_PAUSE: ein Start, ein Ende."""
    thread = _stt_thread()
    device_a = _FakeDevice(
        "Tastatur A", FULL, [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1)]]
    )
    device_b = _FakeDevice(
        "Tastatur B", FULL, [[_key("KEY_PAUSE", 1), _key("KEY_PAUSE", 0)]]
    )
    assert _run(thread, [device_a, device_b], monkeypatch) == [
        "stt_pressed",
        "stt_released",
    ]


def test_vanished_device_releases_dictation_and_leaves_the_selector(monkeypatch):
    """Gerät während des Diktats abgezogen: Ende feuert, Deskriptor geht raus.

    Bliebe das Gerät registriert, meldete `pending()` ewig ready und der Test
    liefe nicht zu Ende — genau die heiße Schleife aus dem Befund.
    """
    thread = _stt_thread()
    broken = _VanishingDevice(
        "Tastatur A", FULL, [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1)]]
    )
    healthy = _FakeDevice("Tastatur B", FULL)
    fired = _run(thread, [broken, healthy], monkeypatch)
    assert fired == ["stt_pressed", "stt_released"]
    assert broken.closed is True


def test_lost_device_reports_the_function_it_took_with_it(monkeypatch):
    """A trägt als einzige den Vorlesen-Auslöser. Fällt A weg, ist "tts" tot — und wird das
    zweite Mal gemeldet, obwohl B weiterläuft."""
    thread = _stt_thread()
    missing: list[list[str]] = []
    thread.degraded.connect(missing.append)
    broken = _VanishingDevice("Tastatur A", FULL, [[_key("KEY_LEFTMETA", 1)]])
    healthy = _FakeDevice("Tastatur B ohne F9", ("KEY_F8", "KEY_LEFTMETA", "KEY_PAUSE"))
    assert _run(thread, [broken, healthy], monkeypatch) == []
    assert missing == [["tts"]]


def test_stop_during_dictation_still_releases(monkeypatch):
    """stop() mitten im Diktat (Einstellungen gespeichert): Ende feuert."""
    thread = _stt_thread()
    device = _FakeDevice(
        "Tastatur", FULL, [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1)]]
    )
    # _FakeSelector ruft stop(), sobald kein Stapel mehr ansteht — genau der
    # Fall: Schleife endet, KEY_PAUSE ist noch gedrückt.
    assert _run(thread, [device], monkeypatch) == ["stt_pressed", "stt_released"]


# --- Fehlendes Paket python-evdev ---


def test_module_import_survives_without_evdev(monkeypatch):
    """Ohne evdev muss der Import durchgehen, sonst stirbt PTR vor dem Tray."""
    import importlib
    import sys

    real_import = builtins.__import__

    def no_evdev(name, *args, **kwargs):
        if name == "evdev":
            raise ModuleNotFoundError("No module named 'evdev'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_evdev)
    monkeypatch.delitem(sys.modules, "evdev", raising=False)
    reloaded = importlib.reload(hotkey_module)
    try:
        assert reloaded.evdev is None
        thread = reloaded.HotkeyThread("KEY_F8", ("KEY_LEFTMETA",))
        fired: list[str] = []
        thread.unavailable.connect(fired.append)
        thread.run()
        assert fired == [reloaded.EVDEV_MISSING]
    finally:
        # Ohne Wiederherstellen liefe der Rest der Sitzung gegen ein Modul mit
        # evdev is None.
        monkeypatch.undo()
        importlib.reload(hotkey_module)


def test_missing_package_message_names_package_command_and_all_shortcuts():
    message = hotkey_module.EVDEV_MISSING
    assert "python-evdev" in message
    assert "--asexplicit" in message
    for shortcut in ("Aufnahme", "Vorlesen", "Diktat", "Abbrechen"):
        assert shortcut in message


def test_missing_input_group_keeps_its_own_message(monkeypatch):
    """evdev da, aber keine lesbare Tastatur: der Rechte-Fall, nicht der Paket-Fall."""
    thread = HotkeyThread("KEY_F8", ("KEY_LEFTMETA",))
    fired: list[str] = []
    thread.unavailable.connect(fired.append)
    monkeypatch.setattr(hotkey_module.evdev, "list_devices", list)
    thread.run()
    assert len(fired) == 1
    assert "Gruppe input" in fired[0]
    assert "python-evdev" not in fired[0]


def test_last_device_lost_is_a_real_total_failure(monkeypatch):
    thread = _stt_thread()
    broken = _VanishingDevice(
        "Einzige Tastatur", FULL, [[_key("KEY_LEFTMETA", 1), _key("KEY_PAUSE", 1)]]
    )
    fired = _run(thread, [broken], monkeypatch)
    assert fired[:2] == ["stt_pressed", "stt_released"]
    assert fired[2].startswith("unavailable:Alle Tastaturen wurden getrennt")
