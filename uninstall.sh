#!/usr/bin/env bash
set -euo pipefail

install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/pc-sound-recorder"
launcher="$HOME/.local/bin/pc-sound-recorder"
applications_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}/pc-sound-recorder"

rm -rf "$install_dir"
rm -f "$launcher"
rm -f "$applications_dir/pc-sound-recorder.desktop"
rm -f "$autostart_dir/pc-sound-recorder.desktop"
rm -f "$icon_dir/pc-sound-recorder.svg"
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true
fi

echo "App, Starter, Desktop-Einträge und Icon entfernt."

answer=""
if [[ -t 0 ]]; then
  read -r -p "Auch Einstellungen und Aufnahmen löschen? [j/N] " answer || true
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
  # Sicherheitsnetz: niemals Home oder Wurzel löschen.
  if [[ -n "$recordings" && "$recordings" != "$HOME" && "$recordings" != "/" && -d "$recordings" ]]; then
    rm -rf "$recordings"
    echo "Einstellungen und Aufnahmen ($recordings) gelöscht."
  else
    echo "Einstellungen gelöscht; Aufnahmeordner nicht gefunden – nichts weiter gelöscht."
  fi
else
  echo "Einstellungen ($config_dir) und Aufnahmen bleiben erhalten."
fi
