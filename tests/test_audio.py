import pytest

from pc_sound_recorder.audio import (
    FORMATS, RecorderError, build_command, parse_mean_volume,
)


def test_build_command_mp3_defaults():
    command = build_command("alsa.monitor", None, "/tmp/a.mp3", "mp3", 2)
    assert command[:2] == ["ffmpeg", "-nostdin"]
    assert ["-f", "pulse", "-i", "alsa.monitor"] == command[6:10]
    assert "-c:a" in command and "libmp3lame" in command
    quality_index = command.index("-q:a")
    assert command[quality_index + 1] == "2"
    assert command[-1] == "/tmp/a.mp3"


def test_build_command_flac_uses_compression_level():
    command = build_command("mon", None, "/tmp/a.flac", "flac", 8)
    assert "flac" in command
    level_index = command.index("-compression_level")
    assert command[level_index + 1] == "8"


def test_build_command_ogg_allows_negative_quality():
    command = build_command("mon", None, "/tmp/a.ogg", "ogg", -1)
    quality_index = command.index("-q:a")
    assert command[quality_index + 1] == "-1"
    assert "libvorbis" in command


def test_build_command_wav_ignores_quality():
    command = build_command("mon", None, "/tmp/a.wav", "wav", 5)
    assert "pcm_s16le" in command
    assert "-q:a" not in command and "-compression_level" not in command


def test_build_command_clamps_quality_to_format_range():
    command = build_command("mon", None, "/tmp/a.mp3", "mp3", 99)
    assert command[command.index("-q:a") + 1] == "9"


def test_build_command_unknown_format_raises():
    with pytest.raises(RecorderError):
        build_command("mon", None, "/tmp/a.xyz", "xyz", 2)


def test_build_command_mic_mix_uses_amix_without_normalize():
    command = build_command("mon", "mic.source", "/tmp/a.mp3", "mp3", 2)
    assert command.count("-i") == 2
    assert "mic.source" in command
    filter_index = command.index("-filter_complex")
    filtergraph = command[filter_index + 1]
    assert "amix=inputs=2" in filtergraph
    assert "duration=longest" in filtergraph
    assert "normalize=0" in filtergraph
    map_index = command.index("-map")
    assert command[map_index + 1] == "[a]"
    # Die Filter-Kette steht vor den Codec-Argumenten.
    assert filter_index < command.index("-c:a")


def test_build_command_without_mic_has_no_filter():
    command = build_command("mon", None, "/tmp/a.mp3", "mp3", 2)
    assert "-filter_complex" not in command
    assert command.count("-i") == 1


def test_parse_mean_volume_reads_ffmpeg_stderr():
    stderr = (
        "[Parsed_volumedetect_0 @ 0x55] n_samples: 12345\n"
        "[Parsed_volumedetect_0 @ 0x55] mean_volume: -27.4 dB\n"
        "[Parsed_volumedetect_0 @ 0x55] max_volume: -3.1 dB\n"
    )
    assert parse_mean_volume(stderr) == pytest.approx(-27.4)


def test_parse_mean_volume_returns_none_on_garbage():
    assert parse_mean_volume("") is None
    assert parse_mean_volume("ffmpeg version n9.0.1") is None
    assert parse_mean_volume("mean_volume: dB") is None


def test_build_command_invalid_quality_raises_recorder_error():
    with pytest.raises(RecorderError, match="Qualitätswert"):
        build_command("mon", None, "/tmp/a.mp3", "mp3", "laut")


class _FakeProcess:
    def poll(self):
        return None


def _fake_popen(commands):
    def popen(command, **kwargs):
        commands.append(command)
        return _FakeProcess()

    return popen


def test_start_without_microphone_source_warns_and_records_monitor_only(
    tmp_path, monkeypatch
):
    import pc_sound_recorder.audio as audio_module

    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/tool")
    monkeypatch.setattr(audio_module, "default_monitor", lambda: "monitor.quelle")
    monkeypatch.setattr(audio_module, "default_source", lambda: None)
    commands = []
    monkeypatch.setattr(audio_module.subprocess, "Popen", _fake_popen(commands))

    recorder = audio_module.AudioRecorder()
    recorder.start(tmp_path, fmt="mp3", quality=2, mix_microphone=True)

    assert recorder.last_warning is not None
    assert "-filter_complex" not in commands[0]
    assert commands[0].count("-i") == 1
    recorder.process = None


def test_start_with_microphone_source_mixes_without_warning(tmp_path, monkeypatch):
    import pc_sound_recorder.audio as audio_module

    monkeypatch.setattr(audio_module.shutil, "which", lambda name: "/usr/bin/tool")
    monkeypatch.setattr(audio_module, "default_monitor", lambda: "monitor.quelle")
    monkeypatch.setattr(audio_module, "default_source", lambda: "mikro.quelle")
    commands = []
    monkeypatch.setattr(audio_module.subprocess, "Popen", _fake_popen(commands))

    recorder = audio_module.AudioRecorder()
    recorder.start(tmp_path, fmt="mp3", quality=2, mix_microphone=True)

    assert recorder.last_warning is None
    assert "normalize=0" in commands[0][commands[0].index("-filter_complex") + 1]
    assert commands[0].count("-i") == 2
    recorder.process = None
