#!/usr/bin/env bash
set -euo pipefail

install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/pc-sound-recorder"
launcher="$HOME/.local/bin/pc-sound-recorder"
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pc-sound-recorder"
# Die Diktat-Modelle liegen nicht in der venv, sondern im huggingface-Cache
# (gemessen: 1,5 G für medium, 2,9 G für large-v3). Sie überleben `rm -rf
# $install_dir` und blieben bisher unerwähnt liegen.
model_cache="${HF_HOME:-$HOME/.cache/huggingface}/hub"

# Nimmt die Diktat-Umgebung ($install_dir/venv, rund 2,7 GB) mit.
rm -rf "$install_dir"
rm -f "$launcher"
rm -f "$applications_dir/pc-sound-recorder.desktop"
rm -f "$autostart_dir/pc-sound-recorder.desktop"
rm -f "$icon_dir/pc-sound-recorder.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi

echo "App, Starter, Desktop-Einträge, Icon und Diktat-Umgebung entfernt."

# Nur die vier Modelle, die PTR überhaupt anbietet (Einstellungsdialog:
# large-v3-turbo, large-v3, medium, small), ausgeschrieben statt geglobt. Ein
# `models--*faster-whisper*` trifft auch, was ein anderes Programm geladen hat
# — hier lag `models--Systran--faster-whisper-base` (142 M), das PTR nie
# angefordert haben kann, und wäre mitgelöscht worden.
models=()
for name in \
  "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" \
  "models--Systran--faster-whisper-large-v3" \
  "models--Systran--faster-whisper-medium" \
  "models--Systran--faster-whisper-small"
do
  if [[ -e "$model_cache/$name" ]]; then
    models+=("$model_cache/$name")
  fi
done
if (( ${#models[@]} > 0 )); then
  size="$(du -shc "${models[@]}" 2>/dev/null | tail -1 | cut -f1)"
  echo "Diktat-Modelle im Cache: $model_cache (${size:-Größe unbekannt})"
fi

answer=""
if [[ -t 0 ]]; then
  read -r -p "Auch Einstellungen, Aufnahmen und Diktat-Modelle löschen? [j/N] " answer || true
fi
if [[ "$answer" =~ ^[jJyY]$ ]]; then
  recordings=""
  if [[ -f "$config_dir/config.json" ]] && command -v python3 >/dev/null 2>&1; then
    recordings="$(python3 - "$config_dir/config.json" <<'PY' || true
import json
import pathlib
import sys

try:
    print(json.loads(pathlib.Path(sys.argv[1]).read_text()).get("output_dir", ""))
except Exception:
    pass
PY
)"
  fi
  rm -rf "$config_dir"
  if (( ${#models[@]} > 0 )); then
    rm -rf "${models[@]}"
    echo "Diktat-Modelle gelöscht."
  fi
  # Sicherheitsnetz: niemals Home oder Wurzel löschen.
  if [[ -n "$recordings" && "$recordings" != "$HOME" && "$recordings" != "/" && -d "$recordings" ]]; then
    rm -rf "$recordings"
    echo "Einstellungen und Aufnahmen ($recordings) gelöscht."
  else
    echo "Einstellungen gelöscht; Aufnahmeordner nicht gefunden – nichts weiter gelöscht."
  fi
else
  echo "Einstellungen ($config_dir), Aufnahmen und Diktat-Modelle ($model_cache) bleiben erhalten."
fi
