"""Diktat: Hotkey halten, sprechen, loslassen — der Text landet im Fokus.

Portiert aus `diktat.py`/`ptt.py` (eigenes Repo, bleibt als Rückbauweg stehen).
Die Kommentare dort begründen mit Messwerten, welche Wege gescheitert sind;
sie sind hier mitgenommen, nicht weggelassen.

## Warum ydotool und nicht xdotool

Unter Wayland kann kein Programm einem fremden Fenster Tastenanschläge
schicken — das Protokoll sieht es nicht vor, und genau das ist der Punkt.
Der einzige Weg führt eine Ebene tiefer: `ydotool` legt über `/dev/uinput`
eine virtuelle Tastatur an, und die sieht der Compositor wie echte Hardware.
xdotool erreicht nur XWayland-Fenster.

## Warum das Modell je Diktat neu lädt (kalter Betrieb)

Ein warmer Dienst wäre nach dem ersten Diktat schneller, belegt dafür dauerhaft
1,6–2,6 GB VRAM neben Mimic und ComfyUI. Bewusst entschieden am 2026-08-28:
kalt. Gemessen kostet der kalte Pfad rund 1,3 s bis zum Text.
# ponytail: Modell lädt je Diktat. Wird ein warm gehaltener Faden, wenn die
# Sekunde stört — dann `load_model()` einmal aufrufen und das Ergebnis halten.

**Kalt heißt nicht spurlos.** Das Modellgewicht ist nach dem Diktat wieder frei,
der CUDA-Kontext samt cuBLAS-/cuDNN-Griffen nicht: ctranslate2 hält ihn bis zum
Prozessende. Gemessen am 2026-08-28 über fünf kalte Läufe an der eigenen PID:
vorher 0 MiB, danach konstant 500 MiB — kein wachsendes Leck, aber dauerhaft.
Wer die 500 MiB wirklich zurückhaben will, braucht die Erkennung in einem
Kindprozess statt in einem Faden; das ist eine Architekturentscheidung und
steht hier bewusst aus.

## Bekannte Grenze: der Fokus kann wechseln

Zwischen dem Loslassen der Taste und dem Einfügen liegen rund 1,3 s. Wechselt in
dieser Zeit das aktive Fenster, tippt `ydotool` in das neue — der Diktattext
landet im falschen Programm. Unter Wayland ist das nicht abstellbar: aus
demselben Grund, aus dem xdotool hier nicht arbeiten kann, darf auch niemand ein
bestimmtes Fenster als Ziel benennen. `ydotool` schickt seine Anschläge an das,
was der Compositor gerade fokussiert hat.

## Eigene Laufzeitumgebung

`faster-whisper` hängt an `ctranslate2`, und das braucht `libcublas.so.12`;
systemweit liegt hier `.so.13`. Deshalb eine eigene venv unter
`~/.local/share/pc-sound-recorder/venv` (Python 3.14, wie das System-Python
von PTR — nur deshalb trägt der `sys.path`-Einschub). `install.sh` legt sie an.
`LD_LIBRARY_PATH` auf die Nvidia-Ordner muss **vor** dem Prozessstart stehen
und kommt deshalb aus dem Starter, nicht von hier.
"""

from __future__ import annotations

import array
import atexit
import functools
import math
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from typing import NamedTuple

from PySide6.QtCore import QThread, Signal


# 16 kHz mono ist, was Whisper intern ohnehin daraus macht — gleich so
# aufnehmen spart das Umrechnen.
RATE = "16000"
# Gemessen am 2026-08-14 an 11,9 s Ton: ct2 wählt von selbst schlecht (2,24 s),
# acht Fäden 1,70 s (−24 %). Zwölf 1,77 s, vierundzwanzig 1,82 s — darüber
# kostet die Verteilung mehr als sie bringt. Gilt nur für den CPU-Rückfall.
THREADS = 8
# Umschalt+Einfg ist unter Linux der anwendungsübergreifende Einfügeweg:
# Qt-/GTK-Felder und Chromium verstehen ihn ebenso wie Konsole und Alacritty.
# Manche Programme nehmen dabei CLIPBOARD, andere PRIMARY — `einfuegen()`
# belegt deshalb für den kurzen Einfügevorgang beide mit demselben Text.
PASTE_KEYS = ("42:1", "110:1", "110:0", "42:0")
# Unter dieser Dateigröße kann nichts Gesprochenes drinstehen (WAV-Kopf + Rest).
MIN_BYTES = 4096


class SttError(RuntimeError):
    pass


def venv_dir() -> pathlib.Path:
    base = pathlib.Path(
        os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share")
    )
    return base / "pc-sound-recorder" / "venv"


def venv_site_packages(venv: pathlib.Path | None = None) -> pathlib.Path | None:
    """site-packages der STT-venv, oder None wenn es sie nicht gibt.

    Kein fester Versionsordner: die venv wird von `install.sh` mit dem jeweils
    vorhandenen Python 3.14.x angelegt.
    """
    venv = venv or venv_dir()
    for candidate in sorted((venv / "lib").glob("python3.*/site-packages")):
        if candidate.is_dir():
            return candidate
    return None


@functools.lru_cache(maxsize=1)
def _fallback_dir() -> pathlib.Path:
    """Ein eigenes Verzeichnis mit 0700, einmal je Prozess.

    Nicht `/tmp/pc-sound-recorder-diktat.wav`: `pw-record` legt die Datei mit
    0644 an, und in einem für alle schreibbaren Verzeichnis liest dann jeder
    lokale Nutzer mit, was gesprochen wurde. Der feste Name ist zusätzlich ein
    Symlink-Ziel — zwischen `unlink()` und dem Anlegen kann ein anderer Nutzer
    dort eine Verknüpfung hinterlegen und die Aufnahme umleiten. `mkdtemp()`
    nimmt beides weg: unvorhersagbarer Name, nur für uns betretbar.

    Und räumt sich beim Prozessende weg: `Recording()` entsteht schon in
    `TrayApplication.__init__`, also auch bei ausgeschaltetem Diktat, und ohne
    `XDG_RUNTIME_DIR` bliebe je PTR-Start ein leeres Verzeichnis in /tmp liegen.
    """
    verzeichnis = pathlib.Path(tempfile.mkdtemp(prefix="pc-sound-recorder-"))
    atexit.register(shutil.rmtree, verzeichnis, ignore_errors=True)
    return verzeichnis


def recording_path() -> pathlib.Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = pathlib.Path(runtime) if runtime else _fallback_dir()
    return base / "pc-sound-recorder-diktat.wav"


class Recording:
    """Die laufende `pw-record`-Aufnahme. Start im GUI-Faden, das ist ein Popen."""

    def __init__(self, path: pathlib.Path | None = None) -> None:
        self.path = path or recording_path()
        self.process: subprocess.Popen[bytes] | None = None
        self.started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self.process is not None

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        self.process = subprocess.Popen(
            ["pw-record", "--rate", RATE, "--channels", "1", "--format", "s16",
             str(self.path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.started_at = time.monotonic()

    def _end(self) -> float:
        """Beendet und **erntet** den Kindprozess. Rückgabe: Dauer in Sekunden.

        Ernten ist nicht optional: ungeerntet bleibt pw-record als Zombie
        stehen, und ein Warten per os.kill(pid, 0) hielte ihn 5 s lang für
        lebendig — in `ptt.py` kostete genau das jedes Diktat fünf Sekunden.
        Popen.wait() kehrt in einer Millisekunde zurück.
        """
        process, self.process = self.process, None
        if process is None:
            return 0.0
        duration = time.monotonic() - self.started_at
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
            process.wait(timeout=1)
        return duration

    def stop(self) -> float:
        return self._end()

    def cancel(self) -> None:
        """Aufnahme wegwerfen — Abbruch, kein Diktat."""
        self._end()
        self.path.unlink(missing_ok=True)


def load_model(model: str = "large-v3-turbo", device: str = "cuda"):
    """Lädt das Erkennungsmodell. GPU, wenn sie erreichbar ist, sonst CPU.

    `float16` und nicht `int8_float16`: warm sind alle Quantisierungen gleich
    schnell (0,101/0,110/0,104 s, gemessen 2026-08-28), aber PTR fährt kalt —
    und da gewinnt `float16` deutlich (1,257 s gegen 1,727 s Gesamtzeit), weil
    Quantisierung beim Modellaufbau kostet und der bei jedem Diktat anfällt.
    Der Preis ist rund 1 GB mehr VRAM, aber nur für die Dauer des Diktats.

    Der Rückfall auf CPU ist kein Zierrat: ohne LD_LIBRARY_PATH auf die
    Nvidia-lib-Ordner findet ctranslate2 libcublas nicht, und dann soll das
    Diktat langsamer laufen statt gar nicht.
    """
    WhisperModel = _whisper_model()
    if device == "cuda":
        try:
            return WhisperModel(model, device="cuda", compute_type="float16")
        except Exception as error:      # ct2 wirft RuntimeError, aber nicht nur
            print(f"GPU nicht nutzbar ({error}) – CPU", file=sys.stderr)
    return WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=THREADS)


def _whisper_model():
    """`faster_whisper.WhisperModel`, notfalls über die eigene venv.

    Erst beim ersten Diktat, nicht beim PTR-Start: der Import zieht ctranslate2
    und numpy nach und kostet rund eine halbe Sekunde. Die zahlt niemand beim
    Anmelden für eine Funktion, die er vielleicht nicht benutzt.
    """
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError:
        pass
    site = venv_site_packages()
    if site is None:
        raise SttError(
            "Die Diktat-Umgebung fehlt. `./install.sh` legt sie unter "
            f"{venv_dir()} an (rund 2,7 GB)."
        )
    if str(site) not in sys.path:
        # Hinten anhängen, nicht vorn: die venv soll den Suchpfad des laufenden
        # PTR ergänzen, nicht verdrängen. Vorn stehend könnte jedes dort
        # liegende Paket (numpy, PySide6 …) das systemweite überdecken.
        sys.path.append(str(site))
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise SttError(
            f"faster-whisper ist in {site} nicht importierbar: {error}. "
            "`./install.sh` erneut ausführen – es erkennt eine unvollständige "
            "Umgebung und baut sie neu."
        ) from error
    return WhisperModel


def loudest_window(path: pathlib.Path) -> float:
    """Lautstärke des **lautesten 200-ms-Fensters**, 0.0 bis 1.0.

    Nicht der Mittelwert über die ganze Datei. Der erste Versuch nahm den und
    lag daneben: in einer 20-Sekunden-Aufnahme mit ein paar Sekunden Sprache
    zieht die Stille den Schnitt nach unten. Gemessen am 2026-08-12 lag echte
    Rede bei 0,0264 im Schnitt gegen 0,0209 Rauschen — zu dicht beieinander,
    und eine verstandene Äußerung wurde als „zu leise" verworfen. Im lautesten
    Fenster trennt es sauber: dieselbe Rede 0,1276, dasselbe Rauschen 0,0366.
    """
    with wave.open(str(path)) as datei:
        rate = datei.getframerate()
        raw = datei.readframes(datei.getnframes())
    if not raw:
        return 0.0
    values = array.array("h")
    values.frombytes(raw[: len(raw) - len(raw) % values.itemsize])
    window = max(int(rate * 0.2), 1)
    loudest = 0.0
    # Bis `len(values)`, nicht bis `len(values) - window`: sonst bleibt der
    # letzte, unvollständige Block ungemessen — und genau dort liegt beim
    # Push-to-Talk das letzte Wort. Bei einer 0,3-s-Aufnahme (der kürzesten, die
    # `stt_min_seconds` durchlässt) wurde so nur das erste Fenster geprüft und
    # ein kurzes, lautes Diktat als „zu leise" verworfen. Der Restblock trägt
    # sich selbst: die Division geht durch `len(block)`, nicht durch `window`.
    for start in range(0, len(values), window):
        block = values[start:start + window]
        if not block:
            break
        loudest = max(loudest, math.sqrt(sum(x * x for x in block) / len(block)) / 32768)
    return loudest


def too_quiet(path: pathlib.Path, threshold: float = 0.015) -> tuple[bool, float]:
    """(zu leise?, gemessener Pegel). Stillewächter, keine Qualitätsprüfung.

    Whisper erfindet aus Raumrauschen ganze Sätze: eine Aufnahme ohne ein
    gesprochenes Wort ergab „Einsammeldatei. Reich die Sitzung durch." Weder der
    VAD-Filter noch `no_speech_prob` haben das gefangen — letzteres stand bei
    0,000, das Modell war sich also sicher. Also wird vor der Erkennung gemessen
    statt danach gefiltert.

    Die Schwelle ist bewusst niedrig (0,015). 0,07 war zu hoch: eine echte
    Äußerung kam mit 0,0500 an und wurde verworfen. Zwischen Rede und Rauschen
    liegt an manchem Mikrofon zu wenig Abstand, um daraus einen Wächter zu
    bauen — beim Halten einer Taste nimmt niemand versehentlich ein leeres
    Zimmer auf. Die Sperre schützt nur davor, dass aus dem Nichts ein Satz
    erfunden wird. Geeicht an einem Zimmer und einem Mikrofon; ein anderer Raum
    braucht eine andere Zahl, deshalb steht sie in den Einstellungen.
    """
    level = loudest_window(path)
    return level < threshold, level


def transcribe(model, path: pathlib.Path, language: str = "de") -> str:
    """Erkennt und gibt den zusammengesetzten Text zurück.

    `language="de"` ist keine Vorgabe, sondern eine Ansage: das Modell setzt das
    Sprachmerkmal und schreibt deutsch, was es hört — englische Rede kam dann
    übersetzt an. Wer zwischen Sprachen wechselt, stellt „auto" ein; Whisper
    bestimmt die Sprache dann je Aufnahme selbst, zum Preis eines zusätzlichen
    Durchlaufs über das erste Fenster.
    """
    parts, _info = model.transcribe(
        str(path), language=language or None, vad_filter=True
    )
    return " ".join(part.text.strip() for part in parts).strip()


def recognize(
    path: pathlib.Path,
    model: str = "large-v3-turbo",
    language: str = "de",
    device: str = "cuda",
) -> str:
    """Modell laden, erkennen, wieder freigeben — der ganze kalte Weg.

    Der CPU-Rückfall steckt hier und nicht nur in `load_model()`, weil
    ctranslate2 das Modell verzögert baut: ohne `LD_LIBRARY_PATH` auf die
    Nvidia-Ordner geht `WhisperModel(device="cuda")` **durch** und erst der
    erste `encode()` wirft `RuntimeError: Library libcublas.so.12 is not found`.
    Gemessen 2026-08-28. Säße der Rückfall nur am Laden, wäre er genau in dem
    Fall wirkungslos, für den er gebaut wurde.
    """
    whisper = load_model(model, device)
    try:
        return transcribe(whisper, path, language)
    except RuntimeError as error:
        if device != "cuda":
            raise
        print(f"GPU nicht nutzbar ({error}) – CPU", file=sys.stderr)
    del whisper
    return transcribe(load_model(model, "cpu"), path, language)


# --- Zwischenablage ---------------------------------------------------------
#
# PTR führt Zwischenablagezugriff sonst als opt-in mit Warnung. Hier ist der
# Griff unvermeidbar (siehe `paste()`), aber er ist sichtbar gemacht: der
# Einstellungsdialog erklärt ihn, und `restore` steuert, ob der alte Inhalt
# überhaupt gelesen wird.

class Clip(NamedTuple):
    """Ein gesicherter Ablageinhalt. `data is None` heißt: die Ablage war leer."""

    data: bytes | None
    mime: str | None = None


def _clipboard_read(*, primary: bool = False) -> Clip:
    """Sichert eine Auswahl – als Text, sonst in ihrem eigenen Typ.

    `wl-paste` meldet rc != 0 nicht nur auf einer leeren Ablage, sondern auch
    dann, wenn Inhalt da ist, **nur nicht als Text** — ein mit Spectacle
    kopiertes Bild antwortet genauso wie gar nichts. Wer daraus „war leer"
    macht, ruft beim Zurücklegen `wl-copy --clear` und das Bild ist weg,
    während die Meldung „Diktat eingefügt" lautet. `--list-types` trennt die
    beiden Fälle: rc=0 mit nichtleerer Ausgabe heißt „da ist etwas".
    """
    arguments = ["wl-paste"]
    if primary:
        arguments.append("--primary")
    result = subprocess.run(arguments + ["--no-newline"], capture_output=True)
    if result.returncode == 0:
        return Clip(result.stdout)
    types = subprocess.run(arguments + ["--list-types"], capture_output=True)
    if types.returncode != 0 or not types.stdout.strip():
        return Clip(None)                       # wirklich leer
    # ponytail: nur der erste angebotene Typ. Wayland-Ablagen bieten denselben
    # Inhalt in mehreren Kodierungen an; der erste ist der bevorzugte. Wer
    # wirklich alle zurücklegen will, braucht einen eigenen wl-copy je Typ —
    # nachrüsten, wenn ein Programm den zurückgelegten Inhalt nicht mehr nimmt.
    mime = types.stdout.split()[0].decode(errors="replace")
    content = subprocess.run(
        arguments + ["-t", mime, "--no-newline"], capture_output=True
    )
    return Clip(content.stdout, mime) if content.returncode == 0 else Clip(None)


def _clipboard_write(
    text: str | bytes,
    *,
    primary: bool = False,
    sensitive: bool = False,
    mime: str | None = None,
) -> subprocess.CompletedProcess:
    """Schreibt in eine Auswahl. **Der Rückgabewert gehört geprüft.**

    `check=False` und niemand sieht hin, hieße: in einer X11-Sitzung ohne
    `WAYLAND_DISPLAY` scheitert `wl-copy` mit rc=1 („Failed to connect to a
    Wayland server"), der Diktattext steht nie in der Ablage — und ydotool
    schickt trotzdem Umschalt+Einfg und fügt den *alten* Inhalt ein.

    Bewusst **ohne** `capture_output`: `wl-copy` forkt sich in den Hintergrund,
    um die Auswahl zu halten, und der Kindprozess erbt die Ausgabekanäle. Über
    eine Pipe wartete `subprocess.run()` dann auf ein EOF, das erst mit der
    nächsten Ablageänderung käme — das Diktat hinge. Der Rückgabewert kommt vom
    Elternprozess und ist auch ohne Pipe da; die Fehlerzeile von wl-copy geht
    dafür wie bisher an PTRs eigene Fehlerausgabe.
    """
    arguments = ["wl-copy"]
    if primary:
        arguments.append("--primary")
    if sensitive:
        arguments.append("--sensitive")
    if mime:
        arguments += ["-t", mime]
    return subprocess.run(
        arguments, input=text, text=isinstance(text, str), check=False
    )


def _clipboard_clear(*, primary: bool = False) -> None:
    arguments = ["wl-copy"]
    if primary:
        arguments.append("--primary")
    arguments.append("--clear")
    subprocess.run(arguments, check=False)


def snapshot_clipboard() -> tuple[Clip, Clip]:
    """(CLIPBOARD, PRIMARY) sichern — für den Rückweg von außerhalb `paste()`."""
    return _clipboard_read(), _clipboard_read(primary=True)


def paste(text: str, restore: bool = True) -> tuple[bool, str]:
    """Text über die Zwischenablage einfügen. (Erfolg, Meldung).

    Der Weg über `ydotool type` ist an der Tastaturbelegung gescheitert, und
    zwar grundsätzlich: ydotool schickt Tastencodes nach amerikanischer
    Belegung, KDE legt die deutsche darüber. Gemessen am 2026-08-12 an einem
    Diktat — „zeigt" kam als „yeigt" an (z und y vertauscht), „tatsächlich" als
    „tatschlich" (für das ä gibt es in der US-Belegung keine Taste, der
    Buchstabe fiel weg). Eine langsamere Anschlagrate half nicht und konnte
    nicht helfen; es war nie ein Zeitproblem. Die Zwischenablage kennt keine
    Belegung — ein Kürzel statt hundert einzelner Tastencodes.

    `restore=True` legt den alten Inhalt zurück; dafür muss er vorher gelesen
    werden. `restore=False` liest nichts und überschreibt endgültig.

    **Schlägt ydotool fehl, bleibt der Diktattext in der Zwischenablage**, auch
    bei `restore=True` — sonst wäre das Diktat verloren und der Nutzer hätte
    keinen Weg mehr an seinen Text. Die Primärauswahl wird in beiden Fällen
    zurückgelegt.

    **Der Diktattext geht als `--sensitive` heraus.** Das ist kein Zierrat,
    sondern der einzige Weg, der unter KDE trägt: Klipper hat „leere
    Zwischenablage verhindern" als Vorgabe, nimmt jede Änderung in seine
    Historie auf und bietet nach `wl-copy --clear` sofort den letzten Eintrag
    wieder an — den Diktattext. Gemessen am 2026-08-28: leere Ablage, Diktat,
    Rückweg, und nach 2 s stand das Diktat weiter in CLIPBOARD und PRIMARY. Was
    Klipper nie aufgenommen hat, kann es auch nicht zurückgeben. Der Preis ist
    Komfort: das Diktat steht danach nicht in der Klipper-Historie.
    """
    before = _clipboard_read() if restore else None
    primary_before = _clipboard_read(primary=True) if restore else None
    written = _clipboard_write(text, sensitive=True)
    if written.returncode != 0:
        # Vor ydotool abbrechen: die Ablage ist unverändert, und ein
        # Umschalt+Einfg fügte jetzt den alten Inhalt ins fremde Fenster.
        # ponytail: nur CLIPBOARD geprüft. Fällt PRIMARY allein aus, ist der
        # Text über CLIPBOARD trotzdem da — nachrüsten, wenn ein Programm
        # auftaucht, das bei Umschalt+Einfg nur PRIMARY liest.
        return False, (
            f"Zwischenablage nicht beschreibbar (wl-copy rc={written.returncode}) "
            "– nichts eingefügt. Läuft PTR in einer Wayland-Sitzung?"
        )
    _clipboard_write(text, primary=True, sensitive=True)
    try:
        result = subprocess.run(
            ["ydotool", "key", *PASTE_KEYS], capture_output=True, text=True
        )
    except OSError as error:
        restore_clipboard(primary_before, primary=True)
        return False, (
            f"ydotool nicht ausführbar ({error}) – der Text liegt in der Zwischenablage."
        )
    time.sleep(0.3)                 # dem Fenster Zeit lassen, die Ablage zu lesen
    if result.returncode != 0:
        detail = (
            result.stderr.strip().splitlines()[-1]
            if result.stderr and result.stderr.strip()
            else "unbekannt"
        )
        # Der Diktattext steht seit dem Schreiben oben in CLIPBOARD und wird
        # hier absichtlich **nicht** zurückgenommen — sonst wäre er verloren.
        restore_clipboard(primary_before, primary=True)
        return False, f"Einfügen fehlgeschlagen ({detail}) – der Text liegt in der Zwischenablage."
    restore_clipboard(before)
    restore_clipboard(primary_before, primary=True)
    return True, "Diktat eingefügt"


def restore_clipboard(saved: Clip | None, *, primary: bool = False) -> None:
    """Legt einen mit `_clipboard_read()` gesicherten Ablageinhalt zurück.

    `data is None` heißt „da war nichts", und dann muss **geleert** werden. Das
    ist der Unterschied, an dem CLIPBOARD lange vorbeilief: wer vor dem Diktat
    eine leere Ablage hatte, bekam den Diktattext dauerhaft hineingeschrieben,
    obwohl der Haken „wiederherstellen" gesetzt war und die Meldung „Diktat
    eingefügt" lautete. Leer ist ein Zustand, kein Fehler. Ein Bild ist
    allerdings *kein* leerer Zustand — das trennt `_clipboard_read()`.

    `--sensitive` beim Zurücklegen, immer: ob die Ablage vom Passwortmanager als
    vertraulich markiert war, ist nach dem `wl-paste` nicht mehr erkennbar — die
    Markierung hängt am Angebot, nicht am Inhalt. Ohne sie käme ein Passwort als
    gewöhnlicher Text zurück, Klipper nähme es in die Historie auf und der
    Aufräumtimer des Managers fände nichts mehr. Die Vorsorge kostet Komfort
    (kein Eintrag in der Historie) und nie Sicherheit; andersherum wäre es
    umgekehrt.
    """
    if saved is None:
        return
    if saved.data is None:
        _clipboard_clear(primary=primary)
    else:
        _clipboard_write(
            saved.data, primary=primary, sensitive=True, mime=saved.mime
        )


def _fehlertext(error: BaseException) -> str:
    """Fehlertext fürs Tray. Der fehlende Modelldownload bekommt einen eigenen.

    Ohne Netz wirft huggingface_hub beim ersten Diktat einen 300 Zeichen langen
    englischen `LocalEntryNotFoundError`, in dem nirgends steht, dass gerade ein
    Modell geladen werden sollte — der Nutzer sieht eine Fehlermauer statt eines
    Hinweises. Die Klasse wird über den Namen erkannt, weil huggingface_hub hier
    nicht importiert wird (es liegt in der venv, nicht im PTR-Prozess).

    Bewusst hier abgefangen statt in `install.sh` vorgeladen: welches der vier
    wählbaren Modelle gebraucht wird, entscheidet erst die Einstellung, und die
    Installation um 1,5 GB auf Verdacht zu vergrößern ist der schlechtere Tausch.
    """
    if "LocalEntryNotFound" in type(error).__name__:
        return (
            "Diktat-Modell wird geladen, rund 1,5 GB – dafür braucht der erste "
            "Lauf eine Netzverbindung. Danach arbeitet das Diktat offline."
        )
    return f"Diktat fehlgeschlagen: {error}"


class DictationThread(QThread):
    """Pegelprüfung, Erkennung und Einfügen — alles außerhalb des GUI-Fadens.

    `loudest_window()` iteriert in reinem Python über jedes Sample: bei 20 s
    Aufnahme 320 000 Runden, danach die Erkennung. Im Signalempfänger fröre die
    Tray-App genau so lange ein.
    """

    result = Signal(bool, str)      # (Erfolg, Meldung fürs Tray)

    def __init__(
        self,
        path: pathlib.Path,
        model: str = "large-v3-turbo",
        language: str = "de",
        device: str = "cuda",
        threshold: float = 0.015,
        clipboard_restore: bool = True,
    ) -> None:
        super().__init__()
        self.path = path
        self.model = model
        self.language = language
        self.device = device
        self.threshold = threshold
        self.clipboard_restore = clipboard_restore
        # Wird kurz vor dem ersten `wl-copy` gesetzt und vom GUI-Faden gelesen.
        # Bewusst ein einfaches Attribut und kein Signal: ein Signal über die
        # Fadengrenze wird in die Ereignisschlange gelegt, und genau beim
        # Beenden dreht die niemand mehr — die Nachricht käme im Ernstfall nie
        # an, und dann bliebe der Diktattext in der Ablage stehen. Ein Lesen
        # eines bool ist unter dem GIL unteilbar und kann nicht verloren gehen.
        self.clipboard_touched = False
        # Wie `clipboard_touched` ein einfaches Attribut über die Fadengrenze:
        # gesetzt vom GUI-Faden, gelesen hier. Siehe `cancel()`.
        self.cancelled = False

    def cancel(self) -> None:
        """Ergebnis verwerfen: nichts wird mehr eingefügt.

        ponytail: bricht die laufende Erkennung nicht wirklich ab — sie rechnet
        auf der GPU zu Ende, nur ihr Ergebnis fällt weg. Ein hartes
        `terminate()` mitten im Lauf ließe den CUDA-Kontext von ctranslate2 in
        unbekanntem Zustand zurück und damit womöglich jedes weitere Diktat
        scheitern; beim Programmende (`shutdown()`) ist das egal, mitten im
        Betrieb nicht. Decke: ein Abbruch gibt das Diktat erst frei, wenn die
        Erkennung durch ist (1–3 s auf der GPU). Ausbaupfad, falls das stört:
        die Erkennung in einen eigenen Prozess legen, den man abschießen darf.
        """
        self.cancelled = True

    def run(self) -> None:
        try:
            if not self.path.is_file() or self.path.stat().st_size < MIN_BYTES:
                self.result.emit(False, "Zu kurz – nichts erkannt")
                return
            quiet, level = too_quiet(self.path, self.threshold)
            if quiet:
                self.result.emit(
                    False,
                    f"Zu leise – nichts erkannt (Pegel {level:.4f}, "
                    f"Schwelle {self.threshold})",
                )
                return
            # `recognize` hält das Modell nur lokal: mit seiner Rückkehr gibt
            # ctranslate2 das Modellgewicht wieder frei. Das ist der kalte
            # Betrieb — nicht spurlos: rund 500 MiB CUDA-Kontext und
            # cuBLAS-/cuDNN-Griffe bleiben bis zum Prozessende belegt
            # (gemessen, siehe Modul-Docstring).
            text = recognize(self.path, self.model, self.language, self.device)
            if not text:
                self.result.emit(False, "Nichts verstanden")
                return
            if self.cancelled:
                # Abgebrochen, während erkannt wurde: der Text darf jetzt nicht
                # mehr in ein Fenster fallen, in dem der Nutzer längst weiter
                # tippt. Die WAV räumt das `finally` weg.
                return
            self.clipboard_touched = True
            self.result.emit(*paste(text, restore=self.clipboard_restore))
        except SttError as error:
            self.result.emit(False, str(error))
        except Exception as error:      # noqa: BLE001 – siehe _fehlertext()
            self.result.emit(False, _fehlertext(error))
        finally:
            self.path.unlink(missing_ok=True)
