#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/pc-sound-recorder"
bin_dir="$HOME/.local/bin"
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

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
python3 -c "import PySide6" 2>/dev/null || warn "PySide6 ist für python3 nicht importierbar."
python3 -c "import evdev" 2>/dev/null || warn "evdev ist für python3 nicht importierbar."
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
cp "$applications_dir/pc-sound-recorder.desktop" "$autostart_dir/pc-sound-recorder.desktop"

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
  "$stt_venv/bin/python" -c 'import faster_whisper' >/dev/null 2>&1
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

cp "$project_dir/packaging/pc-sound-recorder.svg" "$icon_dir/pc-sound-recorder.svg"
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
echo "Starte mit: $launcher"
