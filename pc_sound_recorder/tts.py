from __future__ import annotations

import os
import pathlib
import re
import shutil
import signal
import subprocess
import threading

from PySide6.QtCore import QThread, Signal


MIMIC_TEXT_LIMIT = 1000
VOICE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class SelectionError(RuntimeError):
    pass


def find_mimic() -> str | None:
    found = shutil.which("mimic")
    if found:
        return found
    local = pathlib.Path.home() / ".local" / "bin" / "mimic"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    return None


def _paste_text(command: list[str], empty_message: str) -> str:
    """Run a paste tool; distinguish tool failure from an empty selection."""
    try:
        result = subprocess.run(command, capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SelectionError(f"Die Auswahl konnte nicht gelesen werden: {error}") from error
    if result.returncode != 0:
        stderr = result.stderr
        detail = stderr.decode(errors="replace").strip() if isinstance(stderr, bytes) else str(stderr).strip()
        raise SelectionError(detail or "Die Auswahl konnte nicht gelesen werden.")
    stdout = result.stdout
    raw = stdout if isinstance(stdout, bytes) else str(stdout).encode()
    text = raw.decode("utf-8", errors="replace").replace("\x00", "").strip()
    if not text:
        raise SelectionError(empty_message)
    return text


def read_primary_selection(clipboard_fallback: bool = False) -> str:
    """Read the primary selection; optionally fall back to the clipboard.

    The clipboard fallback is opt-in only: clipboard managers and password
    managers may hold sensitive data there.
    """
    wl_paste = shutil.which("wl-paste")
    if not wl_paste:
        raise SelectionError("wl-paste fehlt. Bitte installiere das Paket wl-clipboard.")
    try:
        return _paste_text([wl_paste, "--primary", "--no-newline"], "Kein Text markiert.")
    except SelectionError:
        if not clipboard_fallback:
            raise
    try:
        return _paste_text([wl_paste, "--no-newline"], "Die Zwischenablage ist leer.")
    except SelectionError as clipboard_error:
        if os.environ.get("DISPLAY"):
            xclip = shutil.which("xclip")
            if xclip:
                return _paste_text(
                    [xclip, "-o", "-selection", "clipboard"],
                    "Die Zwischenablage ist leer.",
                )
            raise SelectionError(
                f"{clipboard_error} Zusätzlich fehlt xclip für den X11-Zugriff."
            ) from clipboard_error
        raise


def split_for_mimic(text: str, limit: int = MIMIC_TEXT_LIMIT) -> list[str]:
    """Split long selections at natural boundaries accepted by Mimic."""
    remaining = text.strip()
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        window = remaining[: limit + 1]
        boundaries = [
            window.rfind(mark) + len(mark)
            for mark in ("\n", ". ", "! ", "? ", "; ", ", ", " ")
        ]
        cut = max(boundaries)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [chunk for chunk in chunks if chunk]


def parse_voice_names(output: str) -> list[str]:
    voices: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if parts and VOICE_NAME.fullmatch(parts[0]) and parts[0] not in voices:
            voices.append(parts[0])
    return voices


def available_mimic_voices() -> tuple[list[str], str | None]:
    mimic = find_mimic()
    if not mimic:
        return [], "Mimic wurde nicht gefunden."
    try:
        result = subprocess.run(
            [mimic, "voices"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return [], f"Mimic-Stimmen konnten nicht geladen werden: {error}"
    if result.returncode != 0:
        return [], result.stderr.strip() or "Mimic-Stimmen konnten nicht geladen werden."
    return parse_voice_names(result.stdout), None


class VoicesThread(QThread):
    """Load the mimic voice list off the GUI thread."""

    loaded = Signal(list, object)  # (voices, error message or None)

    def run(self) -> None:
        voices, error = available_mimic_voices()
        self.loaded.emit(voices, error)


class SpeechThread(QThread):
    playback_started = Signal()
    result = Signal(bool, str)

    def __init__(self, voice: str, clipboard_fallback: bool = False) -> None:
        super().__init__()
        self.voice = voice
        self.clipboard_fallback = clipboard_fallback
        self._stop_event = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._process_lock = threading.Lock()

    def stop(self) -> None:
        self._stop_event.set()
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run(self) -> None:
        try:
            text = read_primary_selection(self.clipboard_fallback)
            if self._stop_event.is_set():
                return
            mimic = find_mimic()
            if not mimic:
                raise SelectionError("Mimic wurde nicht gefunden. Bitte installiere oder repariere Mimic.")
            if not VOICE_NAME.fullmatch(self.voice):
                raise SelectionError("Die eingestellte Mimic-Stimme ist ungültig.")
            chunks = split_for_mimic(text)
            self.playback_started.emit()
            for chunk in chunks:
                if self._stop_event.is_set():
                    return
                process = subprocess.Popen(
                    [mimic, "say", "--voice", self.voice, chunk],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                with self._process_lock:
                    self._process = process
                    stopped = self._stop_event.is_set()
                if stopped:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                _, stderr = process.communicate()
                with self._process_lock:
                    self._process = None
                if self._stop_event.is_set():
                    return
                if process.returncode != 0:
                    detail = stderr.decode(errors="replace").strip()
                    raise SelectionError(
                        detail or f"Mimic wurde mit Code {process.returncode} beendet."
                    )
            self.result.emit(True, "Vorlesen beendet")
        except SelectionError as error:
            self.result.emit(False, str(error))
        except OSError as error:
            self.result.emit(False, f"Mimic konnte nicht gestartet werden: {error}")
        finally:
            with self._process_lock:
                self._process = None
