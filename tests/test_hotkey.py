from pc_sound_recorder.hotkey import HotkeyThread, invalid_key_names


def test_invalid_key_names_accepts_known_keys():
    assert invalid_key_names(["KEY_F8", "KEY_F9", "KEY_LEFTMETA"]) == []


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
