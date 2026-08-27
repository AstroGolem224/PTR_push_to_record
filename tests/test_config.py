from pc_sound_recorder.config import Config, shortcut_label


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    original = Config(
        enabled=False,
        trigger_key="KEY_F11",
        modifiers=("KEY_LEFTCTRL", "KEY_LEFTALT"),
        output_dir=str(tmp_path / "audio"),
        autostart=False,
        tts_enabled=False,
        tts_voice="glados",
    )
    original.save(path)
    assert Config.load(path) == original


def test_shortcut_label():
    assert shortcut_label(Config(output_dir="/tmp")) == "Meta+F8"


def test_old_config_receives_tts_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"enabled": false, "output_dir": "/tmp"}', encoding="utf-8")
    config = Config.load(path)
    assert config.tts_enabled is True
    assert config.tts_voice == "forge"


def test_config_round_trip_with_new_fields(tmp_path):
    path = tmp_path / "config.json"
    original = Config(
        output_dir=str(tmp_path / "audio"),
        format="flac",
        quality=8,
        mix_microphone=True,
        max_minutes=30,
        notifications=False,
        silence_warn=False,
        tts_trigger_key="KEY_SCROLLLOCK",
        tts_modifiers=("KEY_LEFTCTRL", "KEY_LEFTALT"),
        tts_clipboard_fallback=True,
    )
    original.save(path)
    assert Config.load(path) == original


def test_load_does_not_spawn_xdg_user_dir_when_output_dir_set(tmp_path, monkeypatch):
    import pc_sound_recorder.config as config_module

    def forbidden_run(*args, **kwargs):
        raise AssertionError("xdg-user-dir darf hier nicht gestartet werden")

    monkeypatch.setattr(config_module.shutil, "which", lambda name: "/usr/bin/xdg-user-dir")
    monkeypatch.setattr(config_module.subprocess, "run", forbidden_run)
    path = tmp_path / "config.json"
    path.write_text('{"output_dir": "/tmp/aufnahmen"}', encoding="utf-8")
    assert Config.load(path).output_dir == "/tmp/aufnahmen"


def test_old_config_receives_recording_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"output_dir": "/tmp"}', encoding="utf-8")
    config = Config.load(path)
    assert config.format == "mp3"
    assert config.quality == 2
    assert config.mix_microphone is False
    assert config.max_minutes == 0
    assert config.notifications is True
    assert config.silence_warn is True
    assert config.tts_trigger_key == "KEY_F9"
    assert config.tts_modifiers == ("KEY_LEFTMETA",)
    assert config.tts_clipboard_fallback is False


def test_shortcut_label_tts():
    assert shortcut_label(Config(output_dir="/tmp"), tts=True) == "Meta+F9"
