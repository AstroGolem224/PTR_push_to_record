# PC-Ton & Vorlesen

Eine kleine KDE/Wayland-Tray-App mit vier getrennten Funktionen:

- **Meta+F8** startet die Aufnahme des Tons vom aktuellen Standard-Audioausgang.
  Ein zweiter Druck stoppt sie und speichert die Datei (MP3, FLAC, OGG oder WAV).
- **Meta+Rollen** liest den gerade markierten Text mit Mimic vor.
- **Meta+Pause** ist das Diktat: halten, sprechen, loslassen – der erkannte Text
  wird ins fokussierte Fenster eingefügt. Standardmäßig aus, in den
  Einstellungen einzuschalten.
- **Meta+Y** bricht ab, was gerade läuft: ein Vorlesen, ein laufendes Diktat.
  Intern ist das `KEY_Z`: evdev benennt Tasten nach US-Belegung, und auf einer
  deutschen Tastatur sitzt dort die Taste mit der Aufschrift Y. Die
  Einstellungen zeigen die Aufschrift, nicht den evdev-Namen.
  Es startet nie etwas Neues – anders als ein zweiter Druck auf das Vorlesen-Kürzel, der
  die Wiedergabe zwar beendet, aber sofort wieder von vorn anfängt. Läuft
  nichts, passiert nichts.

Alle vier Hotkeys sind in den Einstellungen frei wählbar (Modifier + Taste).

## Bedienung

- Linksklick auf das Tray-Symbol aktiviert oder deaktiviert den Hotkey.
- Rechtsklick öffnet das Menü, den Aufnahmeordner und die Einstellungen.
- Das Untermenü **Letzte Aufnahmen** zeigt die fünf neuesten Dateien und
  öffnet sie per Klick mit `xdg-open`.
- Während einer Aufnahme zeigen Tooltip und Menü die laufende Dauer (mm:ss).
- Die Mimic-Stimme lässt sich in den Einstellungen aus den lokal verfügbaren
  Stimmen auswählen und per **Probehören**-Button testen. Standard ist `forge`.
- Grün = bereit, Rot = Aufnahme läuft, Blau = Mimic spricht, Orange = Diktat
  (hört zu oder erkennt), Grau = deaktiviert.
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
  Passwortmanagern) enthalten. Zusammen mit dem Diktat überrascht das: Das
  Diktat legt seinen Text in der Zwischenablage ab, also liest das Vorlesen ohne
  Markierung genau den zuletzt diktierten Satz vor. Kein Fehler, aber gut zu
  wissen – wer das nicht will, markiert vorher Text oder lässt den Fallback aus.

## Diktat (Meta+Pause)

Taste halten, sprechen, loslassen. `pw-record` nimmt vom Standard-Mikrofon auf,
die gewählte Engine erkennt lokal, und der Text wird über die
Zwischenablage mit `ydotool` ins fokussierte Fenster eingefügt – unter Wayland
gibt es keinen anderen Weg in ein fremdes Fenster.

- **Zwei Engines, Vorgabe Parakeet.** `Parakeet` (sherpa-onnx,
  Parakeet-TDT-0.6B-v3 int8, aus Little Dictator übernommen) läuft rein auf
  der CPU, erkennt Deutsch und Englisch automatisch und belegt kein VRAM –
  `install.sh` lädt die Modelle (**rund 640 MB**) nach
  `~/.local/share/pc-sound-recorder/models/`, dazu Silero-VAD, das vor der
  Erkennung die Stille wegschneidet. Wer lieber `Whisper` (faster-whisper,
  GPU) will, schaltet in den Einstellungen die Diktat-Engine um; Modell,
  Sprache, Gerät und Quantisierung gelten nur dort.
- **Modell, Sprache und Stilleschwelle** stehen in den Einstellungen. Beim
  ersten Diktat lädt das gewählte Modell nach (`large-v3-turbo`: rund 1,5 GB);
  dafür braucht dieser eine Lauf eine Netzverbindung, danach arbeitet das
  Diktat offline.
- **Die Zwischenablage wird kurz überschrieben.** Mit dem Häkchen
  „wiederherstellen" liest PTR den alten Inhalt vorher aus und legt ihn danach
  zurück. Unabhängig davon schreibt KDE Klipper jede Änderung mit – jedes
  Diktat steht danach in der Klipper-Historie.
- **Das Modell bleibt warm.** Nach dem ersten Diktat steht der Text in rund
  0,1 s statt in 2,3 s – gemessen an 20 s Ton. Der Preis sind **1,6 GB VRAM**,
  solange das Modell geladen ist. Nach `stt_warm_minutes` ohne Diktat (Vorgabe
  10, `0` = nie) gibt PTR sie wieder frei; wer nebenher ein großes Sprachmodell
  auf dieselbe Karte lädt, stellt die Zahl kleiner.
- **Bekannte Grenze:** zwischen Loslassen und Einfügen liegt beim ersten Diktat
  rund 1,3 s, danach ein Bruchteil davon. Wechselt in dieser Zeit das aktive
  Fenster, landet der Text dort. Unter Wayland lässt sich das Zielfenster nicht
  festlegen.
- Auch nach der Freigabe bleiben rund **500 MiB VRAM** belegt (CUDA-Kontext),
  bis PTR beendet wird. Das Modellgewicht (1,6 GB) ist dann weg.
- **Ohne Netz nach dem ersten Lauf.** PTR lädt das Modell mit
  `local_files_only` aus dem lokalen Cache und fragt nicht beim HuggingFace-Hub
  nach. Das ist keine Kosmetik: hängt die Verbindung, wartet der Abgleich auf
  seinen Timeout – gemessen 135,8 s gegen 1,2 s aus dem Cache. Fehlt das Modell
  im Cache, lädt PTR es genau einmal nach.
- `stt_warm_minutes` und `stt_compute_type` (Vorgabe `int8_float16`) stehen nur
  in `~/.config/pc-sound-recorder/config.json`, nicht im Einstellungsdialog.

## Bekannte Stolperstellen

**Meta+F9 und Meta+F10 gehören unter KDE schon KWin.** Deshalb liegt das
Vorlesen ab Werk auf **Meta+Rollen** und nicht, wie ursprünglich, auf Meta+F9.
Wer eine F-Taste dafür wählt, trifft in der Standardbelegung auf
`~/.config/kglobalshortcutsrc`:

```
Expose    = Ctrl+F9 \t Meta+F9    "Fenster der aktuellen Arbeitsfläche anzeigen"
ExposeAll = Ctrl+F10 \t Meta+F10  "Fenster aller Arbeitsflächen anzeigen"
```

Beides feuert dann gleichzeitig: PTR liest die Tasten eine Ebene tiefer über
evdev, KWin hat seinen Griff zusätzlich. Das ist keine Fehlfunktion von PTR und
lässt sich in PTR auch nicht reparieren. Zwei Wege heraus:

- In den Systemeinstellungen unter **Kurzbefehle → KWin → „Fenster der
  aktuellen Arbeitsfläche anzeigen"** das Kürzel ändern oder entfernen.
- Oder in den Einstellungen von PTR eine andere Kombination fürs Vorlesen
  wählen.

Dasselbe gilt für jedes andere Kürzel, das der Desktop bereits belegt.

## Voraussetzungen

- Linux mit PipeWire-Pulse oder PulseAudio (`pactl`)
- `ffmpeg`, Python, PySide6 und `python-evdev`
  - `python-evdev` gehört ausdrücklich installiert:
    `sudo pacman -S --asexplicit python-evdev` (schon vorhanden, aber nur als
    Abhängigkeit: `sudo pacman -D --asexplicit python-evdev`). Ohne
    `--asexplicit` gilt es als verwaist und das nächste `pacman -Rns` an ganz
    anderer Stelle nimmt es mit.
  - Fehlt es, laufen Tray, Menü und Einstellungen weiter, aber **alle vier
    Kürzel** (Aufnahme, Vorlesen, Diktat, Abbrechen) lösen nicht mehr aus. Das
    Tray-Symbol bleibt grau und die Statuszeile nennt Paket und Befehl.
    `./install.sh` warnt in beiden Fällen.
- `wl-clipboard` (`wl-paste`) zum Lesen der Wayland-Primärauswahl;
  optional `xclip` für den X11-Zwischenablage-Fallback
- eine funktionierende lokale Mimic-Installation (`mimic say`, `mimic voices`)
- Der Benutzer muss `/dev/input/event*` lesen dürfen (hier: Gruppe `input`)
- Fürs Diktat zusätzlich: `pw-record` (Paket `pipewire-audio`), `ydotool` samt
  laufendem `ydotoold`, `wl-copy` und `uv` für die Diktat-Umgebung

## Installation

**Vorher lesen:** `./install.sh` legt eine eigene Python-Umgebung für das
Diktat an und lädt dafür **rund 2,7 GB** nach
`~/.local/share/pc-sound-recorder/venv` (faster-whisper samt CUDA-Bibliotheken;
eine eigene venv, weil ctranslate2 `libcublas.so.12` braucht). Wer das nicht
will, überspringt es – alles andere wird trotzdem installiert:

```bash
PTR_SKIP_STT=1 ./install.sh
```

Mit Diktat:

```bash
chmod +x install.sh
./install.sh
systemctl --user start pc-sound-recorder
```

Das Installationsskript prüft zuerst die Abhängigkeiten (mit klaren
Warnungen statt Abbruch), kopiert die App samt Versionsmarke nach
`~/.local/share`, legt einen Starter in `~/.local/bin`, installiert das
App-Icon ins hicolor-Theme und richtet den Dauerbetrieb ein. Bei einer
bestehenden Installation erkennt es die Version und meldet
„Aktualisiere X → Y".

## Dauerbetrieb

PTR läuft als systemd-Nutzer-Unit (`~/.config/systemd/user/pc-sound-recorder.service`).
Sie hängt an `graphical-session.target`, weil die Tray-Anwendung
`WAYLAND_DISPLAY`/`DISPLAY` und den Sitzungs-D-Bus braucht, und startet PTR
**nach einem Absturz von selbst neu** — höchstens fünf Versuche in fünf
Minuten, danach bleibt sie sichtbar im Zustand `failed` stehen. Ein
planmäßiges „Beenden" aus dem Tray-Menü löst keinen Neustart aus.

```bash
systemctl --user start pc-sound-recorder     # starten
systemctl --user stop pc-sound-recorder      # beenden
systemctl --user restart pc-sound-recorder   # neu starten
systemctl --user status pc-sound-recorder    # Zustand
journalctl --user -u pc-sound-recorder -f    # Ausgaben mitlesen
```

Das Häkchen „Automatisch starten und nach Absturz neu starten" in den
Einstellungen schaltet dieselbe Unit an und ab (`systemctl --user
enable/disable`). Den alten Eintrag in `~/.config/autostart` gibt es nicht
mehr; `./install.sh` entfernt ihn, weil PTR sonst zweimal startete. Nur wenn
kein systemd-Nutzer-Manager erreichbar ist, fällt die Installation auf diesen
Weg zurück — dann ohne Neustart nach Absturz.

Der Startmenü-Eintrag „PC-Ton & Vorlesen" bleibt davon unberührt und startet
die App wie bisher direkt.

Die Diktat-Umgebung wird geprüft, nicht nur gezählt: passt die Python-Version
nicht oder fehlt `faster_whisper` (etwa nach einem Abbruch), baut ein erneutes
`./install.sh` sie neu.

## Deinstallation

```bash
./uninstall.sh
```

Stoppt und deaktiviert die Nutzer-Unit und entfernt sie, dazu App, Starter,
Desktop-/Autostart-Einträge, Icon und die Diktat-Umgebung.
Einstellungen (`~/.config/pc-sound-recorder`), Aufnahmen und die geladenen
Diktat-Modelle (`~/.cache/huggingface/hub`, je Modell 1,5–2,9 GB) bleiben auf
Nachfrage erhalten (Standard: bleiben).

## Entwicklung

```bash
python -m pytest -q
python -m pc_sound_recorder
```
