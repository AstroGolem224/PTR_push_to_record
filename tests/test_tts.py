from pc_sound_recorder.tts import parse_voice_names, split_for_mimic


def test_parse_voice_names_ignores_non_voice_lines():
    output = """forge                     9.3 s
glados                   11.1 s
ungueltig.dot             1.0 s
"""
    assert parse_voice_names(output) == ["forge", "glados"]


def test_split_for_mimic_respects_limit_and_keeps_text():
    text = "Ein erster Satz. " + "wort " * 250 + "Schluss."
    chunks = split_for_mimic(text, limit=100)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_split_for_mimic_handles_single_long_word():
    chunks = split_for_mimic("x" * 205, limit=100)
    assert [len(chunk) for chunk in chunks] == [100, 100, 5]


import types

import pytest

from pc_sound_recorder.tts import SelectionError, read_primary_selection
import pc_sound_recorder.tts as tts_module


def _result(stdout=b"", returncode=0, stderr=b""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_tools(monkeypatch, wl_paste="/usr/bin/wl-paste", xclip="/usr/bin/xclip"):
    tools = {}
    if wl_paste:
        tools["wl-paste"] = wl_paste
    if xclip:
        tools["xclip"] = xclip
    monkeypatch.setattr(tts_module.shutil, "which", lambda name: tools.get(name))


def _patch_run(monkeypatch, handler):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return handler(command)

    monkeypatch.setattr(tts_module.subprocess, "run", fake_run)
    return calls


def test_primary_selection_used_when_present(monkeypatch):
    _patch_tools(monkeypatch)
    calls = _patch_run(monkeypatch, lambda cmd: _result(stdout=b"markierter Text"))
    assert read_primary_selection() == "markierter Text"
    assert len(calls) == 1
    assert "--primary" in calls[0]


def test_empty_primary_raises_without_fallback(monkeypatch):
    _patch_tools(monkeypatch)
    _patch_run(monkeypatch, lambda cmd: _result(stdout=b""))
    with pytest.raises(SelectionError, match="Kein Text markiert"):
        read_primary_selection(clipboard_fallback=False)


def test_clipboard_fallback_reads_wl_paste_clipboard(monkeypatch):
    _patch_tools(monkeypatch)

    def handler(cmd):
        if "--primary" in cmd:
            return _result(stdout=b"")
        return _result(stdout=b"Zwischenablage-Text")

    calls = _patch_run(monkeypatch, handler)
    assert read_primary_selection(clipboard_fallback=True) == "Zwischenablage-Text"
    assert len(calls) == 2
    assert "--primary" not in calls[1]


def test_clipboard_fallback_uses_xclip_under_x11(monkeypatch):
    _patch_tools(monkeypatch)
    monkeypatch.setenv("DISPLAY", ":0")

    def handler(cmd):
        if cmd[0].endswith("wl-paste"):
            return _result(stdout=b"")
        return _result(stdout=b"X11-Text")

    calls = _patch_run(monkeypatch, handler)
    assert read_primary_selection(clipboard_fallback=True) == "X11-Text"
    assert calls[-1][0].endswith("xclip")


def test_clipboard_fallback_without_xclip_reports_missing_tool(monkeypatch):
    _patch_tools(monkeypatch, xclip=None)
    monkeypatch.setenv("DISPLAY", ":0")
    _patch_run(monkeypatch, lambda cmd: _result(stdout=b""))
    with pytest.raises(SelectionError, match="xclip"):
        read_primary_selection(clipboard_fallback=True)


def test_empty_clipboard_raises_without_display(monkeypatch):
    _patch_tools(monkeypatch)
    monkeypatch.delenv("DISPLAY", raising=False)
    _patch_run(monkeypatch, lambda cmd: _result(stdout=b""))
    with pytest.raises(SelectionError, match="Zwischenablage ist leer"):
        read_primary_selection(clipboard_fallback=True)


def test_missing_wl_paste_reports_tool_not_selection(monkeypatch):
    _patch_tools(monkeypatch, wl_paste=None)
    with pytest.raises(SelectionError, match="wl-paste fehlt"):
        read_primary_selection(clipboard_fallback=True)


def test_tool_error_passes_stderr_detail(monkeypatch):
    _patch_tools(monkeypatch)
    _patch_run(
        monkeypatch,
        lambda cmd: _result(returncode=1, stderr=b"Nothing is copied"),
    )
    with pytest.raises(SelectionError, match="Nothing is copied"):
        read_primary_selection()
