import math
import os
import pathlib
import stat
import subprocess
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from pc_sound_recorder import stt


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _wav(path, seconds=1.0, amplitude=0, loud_from=None, loud_to=None, rate=16000):
    """Schreibt eine 16-kHz-Mono-Datei; optional nur ein Abschnitt laut."""
    frames = bytearray()
    total = int(rate * seconds)
    for index in range(total):
        second = index / rate
        loud = loud_from is None or (loud_from <= second < (loud_to or seconds))
        value = int(amplitude * math.sin(2 * math.pi * 220 * second)) if loud else 0
        frames += int(value).to_bytes(2, "little", signed=True)
    with wave.open(str(path), "wb") as datei:
        datei.setnchannels(1)
        datei.setsampwidth(2)
        datei.setframerate(rate)
        datei.writeframes(bytes(frames))
    return path


# --- Pegelprüfung -----------------------------------------------------------


def test_silence_is_rejected(tmp_path):
    path = _wav(tmp_path / "still.wav", seconds=2.0, amplitude=0)
    quiet, level = stt.too_quiet(path)
    assert quiet is True
    assert level == 0.0


def test_speech_passes(tmp_path):
    path = _wav(tmp_path / "laut.wav", seconds=2.0, amplitude=8000)
    quiet, level = stt.too_quiet(path)
    assert quiet is False
    assert level > 0.015


def test_loudest_window_beats_the_mean(tmp_path):
    """20 s Stille mit einer lauten Sekunde: der Mittelwert verwürfe das."""
    path = _wav(tmp_path / "kurz_laut.wav", seconds=20.0, amplitude=8000,
                loud_from=5.0, loud_to=6.0)
    quiet, level = stt.too_quiet(path)
    assert quiet is False
    assert level > 0.1


def test_loudest_window_measures_the_last_window(tmp_path):
    """Das letzte Wort liegt beim Push-to-Talk am Dateiende.

    0,4 s, laut erst ab der Hälfte: wer nur bis `len(values) - window` läuft,
    misst hier 0,0000 und verwirft ein echtes Diktat als „zu leise".
    """
    path = _wav(tmp_path / "spaet_laut.wav", seconds=0.4, amplitude=8000,
                loud_from=0.2)
    quiet, level = stt.too_quiet(path)
    assert quiet is False
    assert level > 0.15         # RMS des Sinus: 8000/√2/32768 = 0,1726


def test_loudest_window_divides_by_the_partial_block(tmp_path):
    """Befund E: 0,35 s sind kein Vielfaches des 200-ms-Fensters.

    Alle anderen Pegeltests treffen den Fensterraster genau (0,4 / 2,0 / 20,0 s),
    ein Restblock entsteht dort nie – und ohne ihn läuft `/ window` statt
    `/ len(block)` unbemerkt durch. Real ist der Restblock der Normalfall
    (1,37 s Diktat = 21 920 Samples), und der falsche Divisor dämpft genau das
    letzte Wort: hier 0,1495 statt 0,1726, also −13 %.
    """
    path = _wav(tmp_path / "restblock.wav", seconds=0.35, amplitude=8000,
                loud_from=0.2)
    quiet, level = stt.too_quiet(path)
    assert quiet is False
    assert level == pytest.approx(0.1726, abs=0.002)


def test_empty_recording_is_quiet(tmp_path):
    path = _wav(tmp_path / "leer.wav", seconds=0.0)
    assert stt.too_quiet(path)[0] is True


# --- Zwischenablage ---------------------------------------------------------


class FakeClipboard:
    """wl-paste/wl-copy/ydotool als Attrappe, mit echtem Ablagezustand."""

    def __init__(self, ydotool_returncode=0, wl_copy_returncode=0):
        self.clipboard = b"geheimes-passwort"
        self.primary = b"markierter-text"
        self.ydotool_returncode = ydotool_returncode
        # `wl-copy` scheitert z. B. in einer X11-Sitzung ohne WAYLAND_DISPLAY:
        # rc=1, „Failed to connect to a Wayland server". Die Ablage bleibt dann
        # unverändert – und ohne diesen Fall kann keine Attrappe zeigen, dass
        # ydotool danach den *alten* Inhalt einfügt.
        self.wl_copy_returncode = wl_copy_returncode
        self.pasted = False
        # Leere Ablage: `wl-paste` liefert dann rc=1, nicht etwa leeren Inhalt.
        self.clipboard_empty = False
        self.primary_empty = False
        # Angebotene Typen je Auswahl. `wl-paste` ohne `-t` gibt nur Text
        # heraus; auf einer Bild-Ablage meldet es rc=1 – dieselbe Antwort wie
        # auf einer leeren. Erst `--list-types` trennt die beiden Fälle.
        self.clipboard_types = ["text/plain"]
        self.primary_types = ["text/plain"]
        self.calls = []

    def run(self, arguments, **kwargs):
        self.calls.append(list(arguments))
        command = arguments[0]
        primary = "--primary" in arguments
        name = "primary" if primary else "clipboard"
        wanted = arguments[arguments.index("-t") + 1] if "-t" in arguments else None
        if command == "wl-paste":
            if getattr(self, f"{name}_empty"):
                return subprocess.CompletedProcess(arguments, 1, stdout=b"", stderr=b"")
            types = getattr(self, f"{name}_types")
            if "--list-types" in arguments:
                text = "\n".join(types) + "\n"
                return subprocess.CompletedProcess(
                    arguments, 0, stdout=text.encode(), stderr=b""
                )
            if (wanted or "text/plain") not in types:
                return subprocess.CompletedProcess(arguments, 1, stdout=b"", stderr=b"")
            return subprocess.CompletedProcess(
                arguments, 0, stdout=getattr(self, name), stderr=b""
            )
        if command == "wl-copy":
            if self.wl_copy_returncode != 0:
                # Ohne capture_output (wl-copy forkt, siehe `_clipboard_write`):
                # stdout/stderr sind bei einem echten Lauf None, nur rc trägt.
                return subprocess.CompletedProcess(
                    arguments, self.wl_copy_returncode
                )
            clear = "--clear" in arguments
            if clear:
                value = b""
            else:
                value = kwargs.get("input", b"")
                if isinstance(value, str):
                    value = value.encode()
            setattr(self, name, value)
            setattr(self, f"{name}_empty", clear)
            setattr(self, f"{name}_types", [] if clear else [wanted or "text/plain"])
            return subprocess.CompletedProcess(arguments, 0)
        if command == "ydotool":
            self.pasted = self.ydotool_returncode == 0
            return subprocess.CompletedProcess(
                arguments, self.ydotool_returncode, stdout="",
                stderr="" if self.ydotool_returncode == 0 else "ydotoold nicht erreichbar",
            )
        raise AssertionError(f"unerwarteter Aufruf: {arguments}")


@pytest.fixture
def clipboard(monkeypatch):
    fake = FakeClipboard()
    monkeypatch.setattr(stt.subprocess, "run", fake.run)
    monkeypatch.setattr(stt.time, "sleep", lambda _seconds: None)
    return fake


def test_paste_restores_clipboard_afterwards(clipboard):
    ok, message = stt.paste("Diktattext", restore=True)
    assert ok is True and clipboard.pasted
    assert clipboard.clipboard == b"geheimes-passwort"
    assert clipboard.primary == b"markierter-text"
    assert "eingefügt" in message


def test_paste_clears_a_clipboard_that_was_empty(clipboard):
    """War die Ablage vorher leer, muss sie danach wieder leer sein.

    `wl-paste` meldet auf einer leeren Ablage rc=1. Wer daraus „Lesefehler,
    also nichts tun" macht, lässt den Diktattext für immer stehen.
    """
    clipboard.clipboard_empty = True
    ok, _message = stt.paste("Diktattext", restore=True)
    assert ok is True
    assert clipboard.clipboard == b""
    assert ["wl-copy", "--clear"] in clipboard.calls


def test_paste_restores_the_clipboard_as_sensitive(clipboard):
    """Der zurückgelegte Inhalt kann ein Passwort sein – Markierung neu setzen."""
    stt.paste("Diktattext", restore=True)
    restore = [call for call in clipboard.calls if call[0] == "wl-copy"][-2:]
    assert ["wl-copy", "--sensitive"] in restore
    assert ["wl-copy", "--primary", "--sensitive"] in restore


def test_paste_writes_the_dictation_as_sensitive(clipboard):
    """Befund A: sonst hält Klipper das Diktat fest, `--clear` bleibt wirkungslos.

    Plasma hat „leere Zwischenablage verhindern" als Vorgabe: nach dem `--clear`
    bietet Klipper sofort den letzten Historieneintrag wieder an – den gerade
    geschriebenen Diktattext. Nur was nie in die Historie kam, ist danach weg.
    """
    stt.paste("Diktattext", restore=True)
    writes = [call for call in clipboard.calls if call[0] == "wl-copy"][:2]
    assert writes == [
        ["wl-copy", "--sensitive"],
        ["wl-copy", "--primary", "--sensitive"],
    ]


def test_paste_aborts_when_wl_copy_fails(clipboard):
    """Befund B: ydotool fügt sonst den alten Inhalt ein und meldet Erfolg."""
    clipboard.wl_copy_returncode = 1
    ok, message = stt.paste("Diktattext", restore=True)
    assert ok is False
    assert not any(call[0] == "ydotool" for call in clipboard.calls)
    assert clipboard.clipboard == b"geheimes-passwort"
    assert "Zwischenablage" in message and "Wayland" in message


def test_paste_does_not_clear_a_clipboard_holding_an_image(clipboard):
    """Befund C: `rc != 0` heißt nicht „war leer" – ein Bild darf nicht sterben."""
    clipboard.clipboard = b"\x89PNG\r\n\x1a\n"
    clipboard.clipboard_types = ["image/png"]
    ok, _message = stt.paste("Diktattext", restore=True)
    assert ok is True
    assert ["wl-copy", "--clear"] not in clipboard.calls
    assert clipboard.clipboard == b"\x89PNG\r\n\x1a\n"
    assert clipboard.clipboard_types == ["image/png"]


def test_paste_without_restore_leaves_the_dictation(clipboard):
    ok, _message = stt.paste("Diktattext", restore=False)
    assert ok is True
    assert clipboard.clipboard == b"Diktattext"


def test_paste_keeps_text_when_ydotool_fails(clipboard):
    """Bewusst: der Text bleibt in der Ablage, sonst wäre das Diktat verloren."""
    clipboard.ydotool_returncode = 1
    ok, message = stt.paste("Diktattext", restore=True)
    assert ok is False
    assert clipboard.clipboard == b"Diktattext"
    # Die Primärauswahl wird trotzdem zurückgelegt.
    assert clipboard.primary == b"markierter-text"
    assert "Zwischenablage" in message and "ydotoold" in message


def test_paste_survives_missing_ydotool(clipboard, monkeypatch):
    original = clipboard.run

    def run(arguments, **kwargs):
        if arguments[0] == "ydotool":
            raise OSError("No such file or directory")
        return original(arguments, **kwargs)

    monkeypatch.setattr(stt.subprocess, "run", run)
    ok, message = stt.paste("Diktattext", restore=True)
    assert ok is False and "ydotool" in message
    assert clipboard.clipboard == b"Diktattext"
    assert clipboard.primary == b"markierter-text"


# --- Faden-Lebenszyklus -----------------------------------------------------


def _run_thread(qapp, thread, timeout_ms=5000):
    """Startet einen echten QThread und dreht die Ereignisschleife bis zum Ende.

    Absichtlich kein QObject-Ersatz für den Faden: Signale über Fadengrenzen
    sind sonst ungetestet, und genau dort liegen die Fehler.
    """
    results = []
    loop = QEventLoop()
    thread.result.connect(lambda ok, message: results.append((ok, message)))
    thread.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, loop.quit)
    thread.start()
    loop.exec()
    assert thread.wait(2000), "Faden ist nicht beendet"
    return results


def test_thread_rejects_quiet_recording_without_loading_a_model(qapp, tmp_path, monkeypatch):
    path = _wav(tmp_path / "still.wav", seconds=2.0, amplitude=0)
    monkeypatch.setattr(
        stt, "load_model",
        lambda *args, **kwargs: pytest.fail("Modell darf bei Stille nicht laden"),
    )
    results = _run_thread(qapp, stt.DictationThread(path))
    assert results and results[0][0] is False
    assert "leise" in results[0][1]
    # Die Aufnahme wird in jedem Fall weggeräumt.
    assert not path.exists()


def test_thread_transcribes_and_pastes(qapp, tmp_path, monkeypatch):
    path = _wav(tmp_path / "laut.wav", seconds=2.0, amplitude=8000)
    monkeypatch.setattr(stt, "load_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(stt, "transcribe", lambda model, wav, language: "Hallo Welt")
    pasted = []
    monkeypatch.setattr(
        stt, "paste",
        lambda text, restore=True: (pasted.append((text, restore)), (True, "Diktat eingefügt"))[1],
    )
    results = _run_thread(qapp, stt.DictationThread(path, clipboard_restore=False))
    assert results == [(True, "Diktat eingefügt")]
    assert pasted == [("Hallo Welt", False)]
    assert not path.exists()


def test_cancelled_thread_does_not_paste(qapp, tmp_path, monkeypatch):
    """Abbruch während der Erkennung: der Text darf nicht mehr irgendwo landen.

    `cancel()` fällt im Ernstfall mitten in `recognize()` — hier gesetzt, sobald
    die Erkennung aufgerufen wird.
    """
    path = _wav(tmp_path / "laut.wav", seconds=2.0, amplitude=8000)
    monkeypatch.setattr(stt, "load_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(
        stt, "paste",
        lambda text, restore=True: pytest.fail("Abgebrochenes Diktat darf nicht einfügen"),
    )
    thread = stt.DictationThread(path)
    monkeypatch.setattr(
        stt, "transcribe",
        lambda model, wav, language: (thread.cancel(), "Hallo Welt")[1],
    )
    assert _run_thread(qapp, thread) == []
    assert thread.clipboard_touched is False
    # Die Sprachaufnahme wird auch beim Abbruch weggeräumt.
    assert not path.exists()


def test_recognize_falls_back_to_cpu_when_cuda_fails_late(monkeypatch, tmp_path):
    """ctranslate2 baut verzögert: die fehlende libcublas fliegt erst hier auf."""
    devices = []
    monkeypatch.setattr(
        stt, "load_model",
        lambda model, device, compute_type: devices.append(device),
    )

    def transcribe(model, path, language):
        if devices[-1] == "cuda":
            raise RuntimeError("Library libcublas.so.12 is not found or cannot be loaded")
        return "auf der CPU erkannt"

    monkeypatch.setattr(stt, "transcribe", transcribe)
    assert stt.recognize(tmp_path / "x.wav") == "auf der CPU erkannt"
    assert devices == ["cuda", "cpu"]


def test_recognize_does_not_loop_on_cpu(monkeypatch, tmp_path):
    """Auf der CPU gibt es keinen Rückfall mehr – der Fehler muss durch.

    Gezählt wird `load_model`: dass eine RuntimeError herauskommt, beweist nichts,
    die wirft der zweite Versuch genauso. Nur die Zahl der Ladeversuche zeigt,
    ob der Rückfall übersprungen wurde.
    """
    calls = []
    monkeypatch.setattr(
        stt, "load_model", lambda model, device, compute_type: calls.append(device)
    )

    def transcribe(model, path, language):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(stt, "transcribe", transcribe)
    with pytest.raises(RuntimeError):
        stt.recognize(tmp_path / "x.wav", device="cpu")
    assert calls == ["cpu"]


# --- Warm halten und freigeben ----------------------------------------------


def test_second_recognition_reuses_the_warm_model(monkeypatch, tmp_path):
    """Warm heißt warm: das zweite Diktat lädt nicht noch einmal."""
    ladungen = []
    monkeypatch.setattr(
        stt, "load_model",
        lambda model, device, compute_type: (ladungen.append(device), object())[1],
    )
    monkeypatch.setattr(stt, "transcribe", lambda model, wav, language: "Hallo")
    assert stt.recognize(tmp_path / "x.wav") == "Hallo"
    assert stt.recognize(tmp_path / "x.wav") == "Hallo"
    assert ladungen == ["cuda"]
    assert stt.release_model() is True
    # Und nach der Freigabe wieder von vorn.
    assert stt.recognize(tmp_path / "x.wav") == "Hallo"
    assert ladungen == ["cuda", "cuda"]


def test_changed_settings_do_not_reuse_the_old_model(monkeypatch, tmp_path):
    geladen = []
    monkeypatch.setattr(
        stt, "load_model",
        lambda model, device, compute_type: (
            geladen.append((model, device, compute_type)), object()
        )[1],
    )
    monkeypatch.setattr(stt, "transcribe", lambda model, wav, language: "Hallo")
    stt.recognize(tmp_path / "x.wav")
    stt.recognize(tmp_path / "x.wav", compute_type="float16")
    assert geladen == [
        ("large-v3-turbo", "cuda", "int8_float16"),
        ("large-v3-turbo", "cuda", "float16"),
    ]


def test_release_during_recognition_does_not_break_the_dictation(qapp, tmp_path, monkeypatch):
    """Die Frist läuft mitten in der Erkennung ab — das darf nichts kosten.

    Freigeben, während ctranslate2 auf dem Modell rechnet, wäre ein Absturz.
    `release_model()` versucht den Riegel deshalb ohne zu warten und tut hier
    nichts; das Diktat behält sein Ergebnis, und der GUI-Faden (dieser Test)
    hängt nicht an der Erkennung fest.
    """
    path = _wav(tmp_path / "laut.wav", seconds=2.0, amplitude=8000)
    monkeypatch.setattr(stt, "load_model", lambda *args, **kwargs: object())
    freigaben = []

    def transcribe(model, wav, language):
        # Aus dem Erkennungsfaden heraus aufgerufen: der Riegel ist belegt.
        freigaben.append(stt.release_model())
        return "Hallo Welt"

    monkeypatch.setattr(stt, "transcribe", transcribe)
    monkeypatch.setattr(
        stt, "paste", lambda text, restore=True: (True, f"eingefügt: {text}")
    )
    results = _run_thread(qapp, stt.DictationThread(path))
    assert results == [(True, "eingefügt: Hallo Welt")]
    assert freigaben == [False]         # abgelehnt, nicht abgestürzt
    # Danach ist der Riegel frei und die Freigabe greift.
    assert stt.release_model() is True


def test_release_without_a_model_reports_nothing_to_free(monkeypatch):
    assert stt.release_model() is False


def test_model_is_built_from_the_cache_first(monkeypatch):
    """Ohne `local_files_only` fragt huggingface_hub bei jedem Laden online nach."""
    aufrufe = []

    class FakeWhisper:
        def __init__(self, model, **kwargs):
            aufrufe.append(kwargs.get("local_files_only", False))

    assert isinstance(stt._build_model(FakeWhisper, "large-v3-turbo"), FakeWhisper)
    assert aufrufe == [True]


def test_missing_cache_falls_back_to_one_download(monkeypatch):
    """Erstes Diktat auf frischer Installation: einmal online, danach nie wieder."""

    class LocalEntryNotFoundError(OSError):
        pass

    aufrufe = []

    class FakeWhisper:
        def __init__(self, model, **kwargs):
            offline = kwargs.get("local_files_only", False)
            aufrufe.append(offline)
            if offline:
                raise LocalEntryNotFoundError("nichts im Cache")

    assert isinstance(stt._build_model(FakeWhisper, "large-v3-turbo"), FakeWhisper)
    assert aufrufe == [True, False]


def test_other_load_errors_do_not_trigger_a_download(monkeypatch):
    """Eine kaputte GPU ist kein Grund, auf ein Netz-Timeout zu warten."""
    aufrufe = []

    class FakeWhisper:
        def __init__(self, model, **kwargs):
            aufrufe.append(kwargs.get("local_files_only", False))
            raise RuntimeError("libcublas.so.12 is not found")

    with pytest.raises(RuntimeError):
        stt._build_model(FakeWhisper, "large-v3-turbo")
    assert aufrufe == [True]


def test_thread_reports_a_missing_environment(qapp, tmp_path, monkeypatch):
    path = _wav(tmp_path / "laut.wav", seconds=2.0, amplitude=8000)

    def missing(*args, **kwargs):
        raise stt.SttError("Die Diktat-Umgebung fehlt.")

    monkeypatch.setattr(stt, "load_model", missing)
    results = _run_thread(qapp, stt.DictationThread(path))
    assert results == [(False, "Die Diktat-Umgebung fehlt.")]


def test_thread_rejects_a_tiny_file(qapp, tmp_path):
    path = tmp_path / "winzig.wav"
    path.write_bytes(b"RIFF")
    results = _run_thread(qapp, stt.DictationThread(path))
    assert results and results[0][0] is False and "kurz" in results[0][1]


# --- venv-Einschub ----------------------------------------------------------


def test_missing_venv_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(stt, "venv_site_packages", lambda venv=None: None)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)
    with pytest.raises(stt.SttError, match="install.sh"):
        stt._whisper_model()


def test_recording_falls_back_to_a_private_directory(monkeypatch):
    """Ohne XDG_RUNTIME_DIR darf die Sprachaufnahme nicht offen in /tmp liegen."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    stt._fallback_dir.cache_clear()
    try:
        path = stt.recording_path()
        assert path.parent != pathlib.Path("/tmp")
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        # Zweiter Aufruf legt kein weiteres Verzeichnis an.
        assert stt.recording_path() == path
    finally:
        path.parent.rmdir()
        stt._fallback_dir.cache_clear()


def test_the_fallback_directory_is_removed_at_exit():
    """Befund J: sonst blieb je PTR-Start ein leeres Verzeichnis in /tmp liegen.

    `Recording()` entsteht schon in `TrayApplication.__init__`, also auch bei
    ausgeschaltetem Diktat. Geprüft in einem echten Kindprozess – `atexit` läuft
    nur bei einem echten Prozessende.
    """
    import sys

    umgebung = {name: wert for name, wert in os.environ.items()
                if name != "XDG_RUNTIME_DIR"}
    fertig = subprocess.run(
        [sys.executable, "-c",
         "from pc_sound_recorder import stt; print(stt.recording_path().parent)"],
        capture_output=True, text=True, env=umgebung,
        cwd=pathlib.Path(__file__).resolve().parent.parent,
    )
    assert fertig.returncode == 0, fertig.stderr
    verzeichnis = pathlib.Path(fertig.stdout.strip())
    assert verzeichnis.name.startswith("pc-sound-recorder-")
    assert not verzeichnis.exists()


def test_model_download_gets_its_own_message():
    """Der englische huggingface-Fehler sagt nicht, dass ein Modell fehlt."""
    error = type("LocalEntryNotFoundError", (OSError,), {})("An error happened …")
    assert "1,5 GB" in stt._fehlertext(error)
    assert stt._fehlertext(OSError("Platte voll")) == "Diktat fehlgeschlagen: Platte voll"


def test_venv_site_packages_finds_the_version_folder(tmp_path):
    site = tmp_path / "venv" / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True)
    assert stt.venv_site_packages(tmp_path / "venv") == site
    assert stt.venv_site_packages(tmp_path / "fehlt") is None
