#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/pc-sound-recorder"
bin_dir="$HOME/.local/bin"
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pc-sound-recorder"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/512x512/apps"

version_of() {
  sed -n 's/^version = "\(.*\)"/\1/p' "$1" 2>/dev/null || true
}

new_version="$(version_of "$project_dir/pyproject.toml")"
new_version="${new_version:-unbekannt}"
old_version=""
if [[ -f "$install_dir/pyproject.toml" ]]; then
  old_version="$(version_of "$install_dir/pyproject.toml")"
fi

# --- Abhängigkeits-Check: Warnungen statt Abbruch, außer python3 selbst ---
warnings=0
warn() { echo "WARNUNG: $1" >&2; warnings=$((warnings + 1)); }

if ! command -v python3 >/dev/null 2>&1; then
  echo "FEHLER: python3 fehlt – ohne Python kann die App nicht laufen." >&2
  exit 1
fi
command -v ffmpeg >/dev/null 2>&1 || warn "ffmpeg fehlt – Aufnahmen sind nicht möglich."
command -v pactl >/dev/null 2>&1 || warn "pactl fehlt – PipeWire-Pulse/PulseAudio ist erforderlich."
# xclip deckt nur den optionalen X11-Zwischenablage-Fallback ab; die
# Primärauswahl (Hauptpfad des Vorlesens) braucht zwingend wl-paste.
command -v wl-paste >/dev/null 2>&1 \
  || warn "wl-paste fehlt – das Vorlesen markierten Texts scheitert (xclip ist nur Fallback)."
command -v xclip >/dev/null 2>&1 \
  || warn "xclip fehlt – der optionale X11-Zwischenablage-Fallback entfällt."
command -v pw-record >/dev/null 2>&1 \
  || warn "pw-record fehlt – das Diktat kann nichts aufnehmen (Paket pipewire-audio)."
command -v ydotool >/dev/null 2>&1 \
  || warn "ydotool fehlt – der Diktattext kann nicht eingefügt werden."
command -v wl-copy >/dev/null 2>&1 \
  || warn "wl-copy fehlt – der Diktattext kann nicht eingefügt werden (Paket wl-clipboard)."
if ! command -v mimic >/dev/null 2>&1 && [[ ! -x "$HOME/.local/bin/mimic" ]]; then
  warn "mimic fehlt – das Vorlesen ist nicht möglich."
fi
# Bibliothekspakete kommen meist nur als Abhängigkeit eines anderen Pakets
# herein und gelten danach als verwaist: ein `pacman -Rns` an ganz anderer
# Stelle nimmt sie mit. Genau so verschwand python-evdev am 2026-08-30, und PTR
# startete danach gar nicht mehr. Werkzeuge wie ffmpeg, ydotool oder
# wl-clipboard installiert man ausdrücklich – dort wäre der Hinweis Lärm.
#
# Nur vorgeschlagen, nie ausgeführt: install.sh läuft ohne Root.
pin_hint() {
  local package="$1"
  if command -v pacman >/dev/null 2>&1 \
     && pacman -Qq "$package" >/dev/null 2>&1 \
     && ! pacman -Qe "$package" >/dev/null 2>&1; then
    warn "$package ist nur als Abhängigkeit installiert – das nächste \`pacman -Rns\` kann es mitnehmen. Festnageln mit: sudo pacman -D --asexplicit $package"
  fi
}

if python3 -c "import PySide6" 2>/dev/null; then
  pin_hint pyside6
else
  warn "PySide6 ist für python3 nicht importierbar – nachinstallieren mit: sudo pacman -S --asexplicit pyside6"
fi
if python3 -c "import evdev" 2>/dev/null; then
  pin_hint python-evdev
else
  warn "evdev ist für python3 nicht importierbar – alle vier Kürzel (Aufnahme, Vorlesen, Diktat, Abbrechen) lösen dann nicht aus. Nachinstallieren mit: sudo pacman -S --asexplicit python-evdev (ohne --asexplicit gilt es wieder als verwaist)."
fi
if ! id -nG | tr ' ' '\n' | grep -qx input; then
  warn "Benutzer ist nicht in der Gruppe input – die Hotkeys können Tastaturen nicht lesen."
fi

if [[ -n "$old_version" ]]; then
  echo "Aktualisiere $old_version → $new_version"
else
  echo "Installiere Version $new_version"
fi

mkdir -p "$install_dir" "$bin_dir" "$applications_dir" "$autostart_dir" "$icon_dir"
rm -rf "$install_dir/pc_sound_recorder"
cp -a "$project_dir/pc_sound_recorder" "$install_dir/"
cp "$project_dir/pyproject.toml" "$install_dir/pyproject.toml"

# python3 statt sed: Pfade mit |, & oder Leerzeichen dürfen die Ersetzung nicht brechen.
render() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import pathlib
import sys

template, target, placeholder, value = sys.argv[1:5]
content = pathlib.Path(template).read_text(encoding="utf-8")
pathlib.Path(target).write_text(content.replace(placeholder, value), encoding="utf-8")
PY
}

launcher="$bin_dir/pc-sound-recorder"
render "$project_dir/packaging/pc-sound-recorder.in" "$launcher" "@INSTALL_DIR@" "$install_dir"
chmod +x "$launcher"

render "$project_dir/packaging/pc-sound-recorder.desktop.in" \
  "$applications_dir/pc-sound-recorder.desktop" "@LAUNCHER@" "$launcher"

# --- Dauerbetrieb ---
#
# Bisher lag hier eine Kopie des Startmenü-Eintrags in ~/.config/autostart.
# Die startet PTR nur bei der Anmeldung; stirbt der Prozess, holt ihn niemand
# zurück. Die Nutzer-Unit tut beides. Kein sudo – Nutzer-Units brauchen keins.
autostart_wanted=1
if [[ -f "$config_dir/config.json" ]]; then
  autostart_wanted="$(python3 - "$config_dir/config.json" <<'PY' || echo 1
import json
import pathlib
import sys

try:
    print(int(bool(json.loads(pathlib.Path(sys.argv[1]).read_text()).get("autostart", True))))
except Exception:
    print(1)
PY
)"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$unit_dir"
  render "$project_dir/packaging/pc-sound-recorder.service.in" \
    "$unit_dir/pc-sound-recorder.service" "@LAUNCHER@" "$launcher"
  systemctl --user daemon-reload
  # Auch bei einer Aktualisierung über eine ältere Installation: liegt der
  # XDG-Eintrag noch daneben, startet PTR zweimal.
  rm -f "$autostart_dir/pc-sound-recorder.desktop"
  if [[ "$autostart_wanted" == "1" ]]; then
    systemctl --user enable pc-sound-recorder.service >/dev/null
    echo "Dauerbetrieb aktiv: pc-sound-recorder.service (Neustart nach Absturz)."
  else
    systemctl --user disable pc-sound-recorder.service >/dev/null 2>&1 || true
    echo "Unit angelegt, aber abgeschaltet (autostart=false in den Einstellungen)."
  fi
else
  # Kein systemd im Nutzerkontext: der bisherige Weg, damit die Installation
  # nicht abbricht. Ohne Neustart nach Absturz.
  warn "Kein systemd-Nutzer-Manager – Dauerbetrieb über ~/.config/autostart, ohne Neustart nach Absturz."
  if [[ "$autostart_wanted" == "1" ]]; then
    cp "$applications_dir/pc-sound-recorder.desktop" "$autostart_dir/pc-sound-recorder.desktop"
  fi
fi

# --- Diktat-Umgebung (faster-whisper) ---
#
# Eine eigene venv, weil ctranslate2 libcublas.so.12 braucht und systemweit
# .so.13 liegt. Dieselbe Python-Nebenversion wie python3, abgeleitet statt fest
# verdrahtet: der sys.path-Einschub aus stt.py schiebt sie in den *laufenden*
# Interpreter, und nach einem Distro-Sprung auf 3.15 käme sonst
# `_ext.cpython-314-*.so` in einen 3.15-Prozess.
python_version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
stt_venv="$install_dir/venv"

# Feste Versionen: ctranslate2 hängt an der cuBLAS-Soname, ein stiller Sprung
# dort nimmt das Diktat mit. Beim Anheben alle vier zusammen prüfen.
#
# ctranslate2 gehört ausdrücklich dazu: faster-whisper 1.2.1 lässt in seinen
# METADATA `ctranslate2<5,>=4.0` offen. Die cuBLAS-Pins allein sichern also
# nichts — ein ctranslate2 4.9, das gegen cuBLAS 13 gebaut ist, käme beim
# nächsten Neubau von selbst herein und nähme das Diktat mit.
stt_packages=(
  "faster-whisper==1.2.1"
  "ctranslate2==4.8.1"
  "nvidia-cublas-cu12==12.9.2.10"
  "nvidia-cudnn-cu12==9.24.0.43"
  # Parakeet-Engine (CPU). Gepinnt wie der Rest: sherpa-onnx ändert seine
  # model_type-Erkennung zwischen Nebenversionen.
  "sherpa-onnx==1.13.7"
)

# `-d` genügt nicht: ein Abbruch mitten in den 2,7 GB lässt ein Verzeichnis mit
# leerem site-packages zurück, und das galt bisher als „vorhanden". Jeder
# weitere Lauf meldete dann „Installiert." und das Diktat blieb dauerhaft tot.
# Geprüft wird deshalb, was zählt: passende Python-Version und importierbares
# faster_whisper.
stt_venv_ok() {
  local version
  [[ -x "$stt_venv/bin/python" ]] || return 1
  version="$("$stt_venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" || return 1
  [[ "$version" == "$python_version" ]] || return 1
  "$stt_venv/bin/python" -c 'import faster_whisper' >/dev/null 2>&1 || return 1
  # Auch sherpa-onnx: eine venv aus der Zeit vor der Parakeet-Engine gilt
  # sonst als vollständig, und die neue Vorgabe-Engine bliebe tot.
  "$stt_venv/bin/python" -c 'import sherpa_onnx' >/dev/null 2>&1
}

if stt_venv_ok; then
  echo "Diktat-Umgebung vorhanden: $stt_venv"
elif [[ "${PTR_SKIP_STT:-0}" != "0" ]]; then
  echo "Diktat-Umgebung übersprungen (PTR_SKIP_STT gesetzt) – das Diktat bleibt aus."
elif ! command -v uv >/dev/null 2>&1; then
  warn "uv fehlt – die Diktat-Umgebung wurde nicht angelegt, das Diktat bleibt aus."
else
  if [[ -d "$stt_venv" ]]; then
    echo "Diktat-Umgebung unbrauchbar (Abbruch oder Python-Wechsel) – wird neu gebaut."
    rm -rf "$stt_venv"
  fi
  echo "Die Diktat-Umgebung lädt faster-whisper samt CUDA-Bibliotheken:"
  echo "  rund 2,7 GB nach $stt_venv. Überspringen mit PTR_SKIP_STT=1 ./install.sh"
  # Strg-C darf keine halbe venv hinterlassen: sie sähe beim nächsten Lauf wie
  # ein fertiger Bau aus, wenn die Prüfung oben je nachlässiger wird.
  trap 'rm -rf "$stt_venv"; echo "Abgebrochen – die halbe Diktat-Umgebung wurde entfernt." >&2; exit 130' INT TERM
  if uv venv --python "$python_version" "$stt_venv" \
     && uv pip install --python "$stt_venv/bin/python" "${stt_packages[@]}"; then
    echo "Diktat-Umgebung angelegt."
  else
    rm -rf "$stt_venv"
    warn "Die Diktat-Umgebung konnte nicht angelegt werden – das Diktat bleibt aus."
  fi
  trap - INT TERM
fi

# --- Diktat-Modelle (Parakeet-Engine + Silero-VAD) ---
#
# Anders als faster-whisper lädt sherpa-onnx nichts selbst nach: die Dateien
# müssen liegen, bevor das erste Diktat läuft. Idempotent — vorhandene Dateien
# werden nie neu geladen.
models_dir="$install_dir/models"
parakeet_dir="$models_dir/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"

download_stt_models() {
  local parakeet_url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"
  # Fester Tag statt raw/master: ein master-Sprung könnte still ein anderes
  # Modell liefern, als wogegen PTR getestet ist (geprüft: v5.1.2 liefert 200).
  local silero_url="https://github.com/snakers4/silero-vad/raw/v5.1.2/src/silero_vad/data/silero_vad.onnx"
  mkdir -p "$models_dir"
  if [[ -f "$parakeet_dir/tokens.txt" ]]; then
    echo "Parakeet-Modell vorhanden: $parakeet_dir"
  else
    echo "Lade das Parakeet-Modell (rund 640 MB) nach $parakeet_dir …"
    local archive="$models_dir/parakeet.tar.bz2"
    # In ein Staging-Verzeichnis entpacken und erst nach Erfolg atomar
    # umbenennen: ein Abbruch mitten im tar ließe sonst ein halbes
    # Modellverzeichnis liegen, das beim nächsten Diktat den sherpa-C++-Lader
    # trifft statt der Prüfung in stt.py. mktemp unter $models_dir, damit das
    # mv auf demselben Dateisystem bleibt und wirklich atomar ist.
    local staging
    staging="$(mktemp -d "$models_dir/parakeet.staging.XXXXXX")"
    # Nur die int8-Gewichte und tokens.txt aus dem Archiv – die fp32-Dateien
    # daneben braucht PTR nicht und sie verdreifachten den Platz.
    if curl -L --fail -o "$archive" "$parakeet_url" \
       && tar -xjf "$archive" -C "$staging" --wildcards \
            "*/encoder.int8.onnx" "*/decoder.int8.onnx" "*/joiner.int8.onnx" "*/tokens.txt" \
       && [[ -f "$staging/${parakeet_dir##*/}/tokens.txt" ]]; then
      rm -rf "$parakeet_dir"            # halber Bestand aus alten Läufen
      mv "$staging/${parakeet_dir##*/}" "$parakeet_dir"
      echo "Parakeet-Modell geladen."
    else
      warn "Das Parakeet-Modell konnte nicht geladen werden – die Parakeet-Engine bleibt aus (Whisper funktioniert weiter)."
    fi
    rm -rf "$staging"
    rm -f "$archive"
  fi
  if [[ -f "$models_dir/silero_vad.onnx" ]]; then
    echo "Silero-VAD vorhanden."
  elif curl -L --fail -o "$models_dir/silero_vad.onnx.tmp" "$silero_url"; then
    mv "$models_dir/silero_vad.onnx.tmp" "$models_dir/silero_vad.onnx"
    echo "Silero-VAD geladen."
  else
    rm -f "$models_dir/silero_vad.onnx.tmp"
    # Kein Beinbruch: stt.py überspringt die Stille-Trimmung, wenn die Datei
    # fehlt – das Diktat wird nur langsamer, nicht falsch.
    warn "Silero-VAD konnte nicht geladen werden – das Diktat läuft ohne Stille-Trimmung."
  fi
}

if [[ "${PTR_SKIP_STT:-0}" != "0" ]]; then
  echo "Diktat-Modelle übersprungen (PTR_SKIP_STT gesetzt)."
elif ! command -v curl >/dev/null 2>&1; then
  warn "curl fehlt – die Diktat-Modelle (Parakeet, Silero-VAD) wurden nicht geladen."
else
  download_stt_models
fi

cp "$project_dir/packaging/pc-sound-recorder.png" "$icon_dir/pc-sound-recorder.png"
# Aufräumen nach älteren Installationen: dort lag ein SVG im scalable-Zweig,
# und das Theme bevorzugte es sonst vor dem neuen PNG.
rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps/pc-sound-recorder.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi
if command -v xdg-icon-resource >/dev/null 2>&1; then
  xdg-icon-resource forceupdate >/dev/null 2>&1 || true
fi

if (( warnings > 0 )); then
  echo "Installiert mit $warnings Warnung(en) – bitte oben nachlesen."
else
  echo "Installiert."
fi
if [[ -f "$unit_dir/pc-sound-recorder.service" ]]; then
  echo "Starten:  systemctl --user start pc-sound-recorder"
  echo "Zustand:  systemctl --user status pc-sound-recorder"
  echo "Läuft PTR bereits von Hand, erst beenden – sonst greift die Sperrdatei."
else
  echo "Starte mit: $launcher"
fi
