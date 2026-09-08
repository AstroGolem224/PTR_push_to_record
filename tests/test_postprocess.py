"""Nachbearbeitung: Regex-Füllwortfilter und LLM-Prompt/Ausgabe (llama_cpp gefaked)."""

import pytest

from pc_sound_recorder import postprocess
from pc_sound_recorder.postprocess import LLMCleaner, guess_language, strip_fillers, words_to_digits


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


@pytest.mark.parametrize("text, expected", [
    ("Wir treffen uns um 10 Uhr und das ist gut.", "German"),
    ("I was born in 1926 and the meeting is at 10.", "English"),
    ("1926", None),
])
def test_guess_language_by_stopwords(text, expected):
    assert guess_language(text) == expected


def test_prompt_carries_the_language_hint(tmp_path, monkeypatch):
    cleaner, fake = _cleaner(tmp_path, monkeypatch)
    cleaner.clean("I think we should not do this.")
    assert "The input is English. Reply in English." in fake.prompts[0][0]


@pytest.mark.parametrize("raw, expected", [
    ("Ich bin neunzehnhundertsechsundzwanzig geboren.", "Ich bin 1926 geboren."),
    ("Wir treffen uns um zehn Uhr.", "Wir treffen uns um 10 Uhr."),
    ("Im Jahr 1926 gab es zwölf Monate.", "Im Jahr 1926 gab es 12 Monate."),
    ("Das kostet drei Euro fünfzig.", "Das kostet 3 Euro 50."),
    ("zweitausenddreiundzwanzig, einhundertfünf, tausend, eins", "2023, 105, 1000, 1"),
    ("Zwölf Monate hat das Jahr.", "12 Monate hat das Jahr."),
    # Artikel und Nicht-Zahlen bleiben
    ("ich hab ein Auto und eine Katze, einen Hund", "ich hab ein Auto und eine Katze, einen Hund"),
    ("Die Zeichnung ist einfach.", "Die Zeichnung ist einfach."),
    ("neunundneunzig Luftballons", "99 Luftballons"),
    # Englisch
    ("twenty three people, one hundred and five days, two thousand", "23 people, 105 days, 2000"),
    ("one of them is here", "one of them is here"),
    ("nineteen twenty six, twenty twenty", "1926, 2020"),
    ("five six seven", "five six seven"),      # kein Zahlwort-Muster, bleibt
])
def test_words_to_digits(raw, expected):
    assert words_to_digits(raw) == expected
