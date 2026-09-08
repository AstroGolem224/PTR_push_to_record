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


_SYSTEM = (
    "You clean up dictated speech-to-text output. Remove filler words (ähm, äh, hm, um, uh, "
    "er, like, you know, I mean when used as filler), stutters/word repetitions, and "
    "self-corrections (keep only the corrected version). Fix punctuation, capitalization "
    "and obvious grammar slips. NEVER translate: keep the exact language of the input "
    "(German stays German, English stays English). Keep wording and meaning; do not "
    "paraphrase or summarize. Reply with ONLY the cleaned text.\n\n"
    "Example input: also ich äh denke wir sollten das, das Ticket, nein ich meine den Bug "
    "erst morgen fixen\n"
    "Example output: Also ich denke, wir sollten den Bug erst morgen fixen.\n\n"
    "Example input: um so the the deploy is, like, basically ready\n"
    "Example output: So the deploy is basically ready."
)


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
        prompt = (f"<|im_start|>system\n{_SYSTEM}<|im_end|>\n"
                  f"<|im_start|>user\n{text}<|im_end|>\n"
                  "<|im_start|>assistant\n<think>\n\n</think>\n\n")
        out = self._llm(prompt, max_tokens=len(text) // 2 + 32, temperature=0,
                        stop=["<|im_end|>"])
        reply = out["choices"][0]["text"].strip()
        # Plausibilität: leer, abgeschnitten oder halluziniert → Eingabe unverändert lassen
        if not reply or not (0.3 * len(text) <= len(reply) <= 1.5 * len(text) + 20):
            return text
        return reply
