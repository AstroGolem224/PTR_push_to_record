"""Nachbearbeitung: Regex-Füllwortfilter und LLM-Prompt/Ausgabe (llama_cpp gefaked)."""

import pytest

from pc_sound_recorder import postprocess
from pc_sound_recorder.postprocess import LLMCleaner, strip_fillers


@pytest.mark.parametrize("raw, expected", [
    # Parakeet-Rohausgaben, gemessen mit espeak-Testsätzen (2026-09-08)
    ("Also, ich glaube, äh, das ist, ähm, ganz gut so.",
     "Also, ich glaube, das ist, ganz gut so."),
    ("Um, so I was uh thinking that we could move the the meeting to Thursday.",
     "Um, so I was thinking that we could move the the meeting to Thursday."),
    ("Ähm, ich wollte das sagen.", "Ich wollte das sagen."),
    ("Hm, hm, ja.", "Ja."),
    # Stottern: ab drei identischen Wörtern auf eines, zwei bleiben (Handy-Regel)
    ("das das das Meeting", "das Meeting"),
    ("das das Meeting", "das das Meeting"),
])
def test_strip_fillers_removes_universal_fillers_and_stutters(raw, expected):
    assert strip_fillers(raw) == expected


@pytest.mark.parametrize("raw", [
    "Wir treffen uns um 10 Uhr.",           # "um" = Präposition, nie filtern
    "I like this plan, you know.",          # mehrdeutig, bewusst nicht in der Liste
    "Ehrlich, das ähnelt dem alten Plan.",  # Teilwort-Treffer verboten
    "",
])
def test_strip_fillers_leaves_ambiguous_words_alone(raw):
    assert strip_fillers(raw) == raw


class FakeLlama:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def __call__(self, prompt, **kwargs):
        self.prompts.append((prompt, kwargs))
        return {"choices": [{"text": self.reply}]}


def _cleaner(tmp_path, monkeypatch, reply="Hallo Welt."):
    fake = FakeLlama(reply)
    model = tmp_path / "x.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(postprocess, "_load_llama", lambda path, threads: fake)
    return LLMCleaner(model, num_threads=2), fake


def test_prompt_uses_chatml_with_empty_think_block(tmp_path, monkeypatch):
    cleaner, fake = _cleaner(tmp_path, monkeypatch)
    cleaner.clean("also äh hallo welt")
    prompt, kwargs = fake.prompts[0]
    assert prompt.endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert "<|im_start|>user\nalso äh hallo welt<|im_end|>" in prompt
    assert "<|im_end|>" in kwargs["stop"]
    assert kwargs["temperature"] == 0


def test_clean_returns_stripped_reply(tmp_path, monkeypatch):
    cleaner, _ = _cleaner(tmp_path, monkeypatch, "  Hallo Welt. \n")
    assert cleaner.clean("hallo welt") == "Hallo Welt."


@pytest.mark.parametrize("reply", ["", "x" * 500])
def test_clean_falls_back_to_input_when_reply_is_implausible(tmp_path, monkeypatch, reply):
    cleaner, _ = _cleaner(tmp_path, monkeypatch, reply)
    assert cleaner.clean("kurzer satz") == "kurzer satz"


def test_missing_model_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LLMCleaner(tmp_path / "none.gguf", num_threads=2)
