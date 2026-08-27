# PC-Ton & Vorlesen

Eine kleine KDE/Wayland-Tray-App mit zwei getrennten Funktionen:

- **Meta+F8** startet die Aufnahme des Tons vom aktuellen Standard-Audioausgang.
  Ein zweiter Druck stoppt sie und speichert die Datei (MP3, FLAC, OGG oder WAV).
- **Meta+F9** liest den gerade markierten Text mit Mimic vor.

Beide Hotkeys sind in den Einstellungen frei wählbar (Modifier + Taste).

## Bedienung

- Linksklick auf das Tray-Symbol aktiviert oder deaktiviert den Hotkey.
- Rechtsklick öffnet das Menü, den Aufnahmeordner und die Einstellungen.
- Das Untermenü **Letzte Aufnahmen** zeigt die fünf neuesten Dateien und
  öffnet sie per Klick mit `xdg-open`.
- Während einer Aufnahme zeigen Tooltip und Menü die laufende Dauer (mm:ss).
- Die Mimic-Stimme lässt sich in den Einstellungen aus den lokal verfügbaren
  Stimmen auswählen und per **Probehören**-Button testen. Standard ist `forge`.
- Grün = bereit, Rot = Aufnahme läuft, Blau = Mimic spricht, Grau = deaktiviert.
- Standardziel ist `Musik/PC-Aufnahmen` (über `xdg-user-dir` lokalisiert).

Die App zeichnet den Monitor des beim Start einer Aufnahme aktuellen
Standardausgangs auf. Auf Wunsch (Checkbox „Mikrofon zusätzlich …") wird das
Standard-Mikrofon ohne Lautstärke-Normalisierung (`amix normalize=0`)
beigemischt; fehlt die Mikrofonquelle, wird mit Warnung nur der PC-Ton
aufgenommen.

## Einstellungen

- **Format & Qualität**: MP3 (`-q:a` 0–9), FLAC (`-compression_level` 0–12),
  OGG Vorbis (`-q:a` -1–10) oder WAV (verlustfrei, ohne Qualitätsstufe).
  Kleinere Werte = bessere Qualität (außer FLAC: nur Kompressionsaufwand).
- **Maximale Dauer**: Auto-Stopp nach n Minuten, 0 = unbegrenzt.
- **Benachrichtigungen**: Unterdrückt nur Info-Meldungen; Warnungen und
  Fehler kommen immer.
- **Stille-Warnung**: Prüft nach dem Speichern im Hintergrund die mittlere
  Lautstärke (`volumedetect`) und warnt unter -45 dB.
- **Zwischenablage-Fallback**: Wenn kein Text markiert ist, wird zusätzlich
  die Zwischenablage gelesen (`wl-paste`, unter X11 `xclip`). Bewusstes
  Opt-in – die Zwischenablage kann vertrauliche Inhalte (z. B. aus
  Passwortmanagern) enthalten.

## Voraussetzungen

- Linux mit PipeWire-Pulse oder PulseAudio (`pactl`)
- `ffmpeg`, Python, PySide6 und `python-evdev`
- `wl-clipboard` (`wl-paste`) zum Lesen der Wayland-Primärauswahl;
  optional `xclip` für den X11-Zwischenablage-Fallback
- eine funktionierende lokale Mimic-Installation (`mimic say`, `mimic voices`)
- Der Benutzer muss `/dev/input/event*` lesen dürfen (hier: Gruppe `input`)

## Installation

```bash
chmod +x install.sh
./install.sh
pc-sound-recorder
```

Das Installationsskript prüft zuerst die Abhängigkeiten (mit klaren
Warnungen statt Abbruch), kopiert die App samt Versionsmarke nach
`~/.local/share`, legt einen Starter in `~/.local/bin`, installiert das
App-Icon ins hicolor-Theme und aktiviert den Start bei der nächsten
Anmeldung. Bei einer bestehenden Installation erkennt es die Version und
meldet „Aktualisiere X → Y".

## Deinstallation

```bash
./uninstall.sh
```

Entfernt App, Starter, Desktop-/Autostart-Einträge und Icon. Einstellungen
(`~/.config/pc-sound-recorder`) und Aufnahmen bleiben auf Nachfrage erhalten
(Standard: bleiben).

## Entwicklung

```bash
python -m pytest -q
python -m pc_sound_recorder
```
