"""Nachbearbeitung des Diktattexts (aus Little Dictator übernommen).

Zwei Stufen, beide in den Einstellungen schaltbar (`stt_filler_filter`,
`stt_polish`):
1. strip_fillers: Regex nach Handy-Vorbild (audio_toolkit/text.rs). Nur sprach-
   unabhängig eindeutige Füllwörter; "um" (dt. Präposition), "like", "also",
   "you know" bewusst nicht — die bräuchten eine Sprach-Erkennung.
2. LLMCleaner: lokales Qwen3.5-2B (GGUF, llama-cpp-python, CPU). Entfernt auch
   Selbstkorrekturen und glättet Grammatik. Opt-in, gemessen 0,9–1,2 s je Satz
   auf einem 9900X3D mit 4 Threads. Modell und Wheel legt `PTR_LLM=1
   ./install.sh` an; stt.polish() schiebt die venv in den sys.path.
"""

from __future__ import annotations

import re
from pathlib import Path

_FILLERS = ("äh", "ähm", "ehm", "ahm", "uh", "uhm", "uhh", "umm", "hm", "hmm", "mhm", "mmm")
_FILLER_RE = re.compile(r"\b(?:%s)\b(?:,\s*|\s*(?=[.!?])|\s*)" % "|".join(_FILLERS),
                        re.IGNORECASE)
# ponytail: ab drei identischen Wörtern in Folge auf eines kollabieren; zwei bleiben
# (Handy-Regel, "auf, auf Donnerstag" ist oft gewollt). Selbstkorrekturen macht das LLM.
_STUTTER_RE = re.compile(r"\b(\w+)(?:[,.]?\s+\1\b){2,}", re.IGNORECASE)


def strip_fillers(text: str) -> str:
    cleaned = _FILLER_RE.sub("", text)
    cleaned = _STUTTER_RE.sub(r"\1", cleaned)
    cleaned = re.sub(r",\s*([.!?])", r"\1", cleaned).strip()
    if text[:1].isupper() and cleaned[:1].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


# --- Zahlwörter -> Ziffern -------------------------------------------------------
#
# Parakeet schreibt Zahlen inkonsistent („zwölf Monate", aber „365 Tage"), und
# Qwen3.5-2B darf sie nicht anfassen: aus „neunzehnhundertsechsundzwanzig" machte
# es „1898" (gemessen 2026-09-08). Also deterministisch: Kardinalzahlen DE+EN.
# ponytail: keine Ordinalzahlen („fünften"), keine Brüche, kein „drei Euro
# fünfzig" -> „3,50 Euro" (wird „3 Euro 50"). Artikel ein/eine/a/one bleiben.
_DE_UNITS = {"null": 0, "eins": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5,
             "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
             "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15, "sechzehn": 16,
             "siebzehn": 17, "achtzehn": 18, "neunzehn": 19}
_DE_TENS = {"zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50, "sechzig": 60,
            "siebzig": 70, "achtzig": 80, "neunzig": 90}
_DE_ARTICLES = frozenset(("ein", "eine", "einen", "einem", "einer", "eines"))


def _de_under_100(s: str) -> int | None:
    if s == "":
        return 0
    if s in _DE_UNITS:
        return _DE_UNITS[s]
    if s in _DE_TENS:
        return _DE_TENS[s]
    unit, sep, tens = s.partition("und")
    if sep and tens in _DE_TENS and unit in _DE_UNITS and _DE_UNITS[unit] < 10:
        return _DE_UNITS[unit] + _DE_TENS[tens]
    return None


def _de_under_1000(s: str) -> int | None:
    left, sep, rest = s.partition("hundert")
    if not sep:
        return _de_under_100(s)
    hundreds = 1 if left == "" else _de_under_100(left)   # „neunzehnhundert" = 1900
    tail = _de_under_100(rest)
    if hundreds is None or tail is None or hundreds == 0:
        return None
    return hundreds * 100 + tail


def parse_german_number(word: str) -> int | None:
    word = word.lower()
    if word in _DE_ARTICLES:
        return None
    left, sep, rest = word.partition("tausend")
    if not sep:
        return _de_under_1000(word)
    thousands = 1 if left == "" else _de_under_1000(left)
    tail = _de_under_1000(rest)
    if thousands is None or tail is None or thousands == 0:
        return None
    return thousands * 1000 + tail


_EN_SMALL = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
             "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
             "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
             "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
             "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_EN_WORD = r"(?:%s|hundred|thousand|and)" % "|".join(_EN_SMALL)
_EN_RUN = re.compile(rf"\b{_EN_WORD}(?:[\s-]+{_EN_WORD})*\b", re.IGNORECASE)


def _en_strict(tokens: list[str]) -> int | None:
    """Kardinalzahl aus Wörtern; None bei Unfug wie „five six" oder „ten twenty"."""
    total = current = 0
    previous: int | None = None          # letztes Kleinwort in dieser Gruppe
    for token in tokens:
        if token in _EN_SMALL:
            value = _EN_SMALL[token]
            if previous is not None and not (previous >= 20 and previous % 10 == 0 and 0 < value < 10):
                return None              # nur „twenty three", nicht „nineteen twenty"
            current += value
            previous = value
        elif token == "hundred":
            current = (current or 1) * 100
            previous = None
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
            previous = None
        else:
            return None
    return total + current


def parse_english_number(phrase: str) -> int | None:
    tokens = [t for t in re.split(r"[\s-]+", phrase.lower()) if t and t != "and"]
    if not tokens or tokens == ["one"]:      # „one of them" bleibt
        return None
    value = _en_strict(tokens)
    if value is not None:
        return value
    # Jahreszahl in Zweiergruppen: „nineteen twenty six" -> 1926, „twenty twenty" -> 2020
    if len(tokens) >= 2:
        head, tail = _en_strict(tokens[:1]), _en_strict(tokens[1:])
        if head is not None and tail is not None and 10 <= head <= 99 and tail <= 99:
            return head * 100 + tail
    return None


_DE_WORD_RE = re.compile(r"\b[a-zäöüßA-ZÄÖÜ]+\b")


def words_to_digits(text: str) -> str:
    def de(match: re.Match) -> str:
        value = parse_german_number(match.group(0))
        return match.group(0) if value is None else str(value)

    def en(match: re.Match) -> str:
        value = parse_english_number(match.group(0))
        return match.group(0) if value is None else str(value)

    text = _EN_RUN.sub(en, text)
    return _DE_WORD_RE.sub(de, text)


_SYSTEM = (
    "You clean up dictated speech-to-text output. Rules:\n"
    "1. Keep the language of the input. German stays German, English stays English. Never translate.\n"
    "2. Remove filler words (ähm, äh, hm, um, uh, er, like, you know, I mean when used as filler), "
    "stutters and word repetitions.\n"
    "3. Resolve self-corrections: drop the corrected-away part, keep only the correction "
    "(\"das Ticket, nein ich meine den Bug\" -> \"den Bug\").\n"
    "4. Keep all numbers, names and dates exactly as written. Never change or spell out a number.\n"
    "5. Fix punctuation, capitalization and obvious grammar slips. Keep questions as questions.\n"
    "6. Keep every sentence and statement. Do not paraphrase, summarize, shorten or add anything.\n"
    "Reply with ONLY the cleaned text.\n\n"
    "Example input: also ich äh denke wir sollten das, das Ticket, nein ich meine den Bug erst morgen fixen\n"
    "Example output: Also ich denke, wir sollten den Bug erst morgen fixen.\n\n"
    "Example input: wir treffen uns um 10 Uhr äh das kostet 3,50 Euro kannst du das bitte prüfen\n"
    "Example output: Wir treffen uns um 10 Uhr, das kostet 3,50 Euro. Kannst du das bitte prüfen?\n\n"
    "Example input: um so the the deploy is, like, basically ready, can you check it\n"
    "Example output: So the deploy is basically ready. Can you check it?"
)

# Sprachhinweis für das Modell: ohne ihn übersetzte Qwen3.5-2B einen englischen
# Satz ins Deutsche (gemessen 2026-09-08). Parakeet liefert keine Sprache,
# also Stoppwörter zählen; bei Gleichstand kein Hinweis.
_DE_WORDS = frozenset("der die das und ist nicht ich wir ein eine zu mit auf für dass es sich auch "
                      "bin bitte uns wird kann".split())
_EN_WORDS = frozenset("the and is not are you we to of in it that this with for on can was i "
                      "please at be have".split())


def guess_language(text: str) -> str | None:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(w in _DE_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    if de > en:
        return "German"
    if en > de:
        return "English"
    return None


def _load_llama(model_path: Path, num_threads: int):
    from llama_cpp import Llama  # lazy: optionale Abhängigkeit, Tests faken es

    return Llama(model_path=str(model_path), n_ctx=2048, n_threads=num_threads,
                 n_gpu_layers=0, verbose=False)


class LLMCleaner:
    """Qwen3.5-Chat (ChatML). Leerer <think>-Block im Assistant-Prefix schaltet Thinking ab."""

    def __init__(self, model_path: Path, num_threads: int = 4):
        if not model_path.is_file():
            raise FileNotFoundError(f"LLM-Modell fehlt: {model_path}")
        self._llm = _load_llama(model_path, num_threads)

    def warmup(self) -> None:
        # System-Prompt landet im KV-Cache; llama-cpp-python behält den gemeinsamen Prefix.
        self.clean("Warmup.")

    def clean(self, text: str) -> str:
        language = guess_language(text)
        hint = f"\nThe input is {language}. Reply in {language}." if language else ""
        prompt = (f"<|im_start|>system\n{_SYSTEM}{hint}<|im_end|>\n"
                  f"<|im_start|>user\n{text}<|im_end|>\n"
                  "<|im_start|>assistant\n<think>\n\n</think>\n\n")
        out = self._llm(prompt, max_tokens=len(text) // 2 + 32, temperature=0,
                        stop=["<|im_end|>"])
        reply = out["choices"][0]["text"].strip()
        # Plausibilität: leer, abgeschnitten oder halluziniert → Eingabe unverändert lassen
        if not reply or not (0.3 * len(text) <= len(reply) <= 1.5 * len(text) + 20):
            return text
        return reply
