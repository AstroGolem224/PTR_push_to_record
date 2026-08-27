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
