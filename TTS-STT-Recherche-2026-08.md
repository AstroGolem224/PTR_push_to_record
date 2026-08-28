# TTS/STT Tiefenrecherche — Neueste & schnellste Modelle (Stand: 27.08.2026)

**Ziel-Hardware:** NVIDIA RTX 5090 (32 GB VRAM, Blackwell/sm_120, CUDA), AMD Ryzen 9 9900X3D (12C/24T), 30 GB RAM, CachyOS (Arch, Kernel 7.2.0-bore), Hyprland/Wayland.

**Kurzfazit:** Diese Hardware ist für alle aktuellen Speech-Modelle massiv überdimensioniert. „Near Realtime" ist überall weit übertroffen; die echten Unterscheidungskriterien sind **Streaming-Architektur vs. Batch**, **deutsche Sprachqualität** und **Lizenz**.

---

## 1. STT — Speech-to-Text

### 1.1 NVIDIA-Ökosystem (NeMo) — aktuelle Spitze

| Modell | Release | Params | Sprachen | Lizenz | Hinweise |
|---|---|---|---|---|---|
| `nvidia/parakeet-tdt-1.1b` | 01/2024 | 1.1B | nur EN | CC-BY-4.0 | Vorgänger |
| `nvidia/parakeet-tdt-0.6b-v2` | 01.05.2025 | 0.6B | nur EN | CC-BY-4.0 | RTFx 3380 (Batch 128) |
| `nvidia/parakeet-tdt-0.6b-v3` | 14.08.2025 | 0.6B | 25 EU-Sprachen inkl. **DE**, Auto-LID | CC-BY-4.0 | DE-WER: Fleurs 5,04 %, CoVoST 4,84 %; offiziell Blackwell-kompatibel; ~1,2 GB Download, <4 GB VRAM; Wort-Timestamps; bis 24 min Audio Full Attention |
| `nvidia/canary-1b-v2` | 14.08.2025 | 978M | 25 Sprachen, ASR + Übersetzung | CC-BY-4.0 | DE-Ø 4,96 % WER; RTFx 749; halluzinationsresistenter als Whisper |
| `nvidia/canary-qwen-2.5b` | 17.07.2025 | 2.5B | nur EN | CC-BY-4.0 | 5,63 % WER, Platz 1 Open-ASR-Leaderboard 2025; RTFx 418 |
| `nvidia/nemotron-speech-streaming-en-0.6b` | 05.01.2026 | 0.6B | nur EN, echtes Streaming | OpenMDW-1.1 | Vorgänger |
| `nvidia/nemotron-3.5-asr-streaming-0.6b` | 04.06.2026 | 0.6B | **40 Sprachen inkl. DE, echtes Streaming** | OpenMDW-1.1 (kommerziell nutzbar) | Cache-Aware FastConformer-RNNT; Chunks 80–1120 ms; <300 ms Latenz offiziell (erstes Partial ~750 ms); Language-ID-Prompting; native Interpunktion/Kapitalisierung |

**Wichtig:** Parakeet v2/v3 sind **keine echten Streaming-Modelle** (nur chunked buffered inference) — dafür ist die Nemotron-Streaming-Linie gedacht. Parakeet ist in sherpa-onnx strukturell nicht streaming-fähig.

**Deutscher WER-Vergleich** (Ø über Fleurs, MLS, Common Voice 23, TheStageAI-Benchmark):

| Modell | DE-WER |
|---|---|
| Whisper large-v3-turbo | 4,91 % |
| Canary-1B-v2 | 4,96 % |
| Parakeet-TDT-0.6B-v3 | 5,04 % |

**Benchmarks/RTFx (Referenz):**
- Parakeet-CTC-1.1B: RTFx 2793 vs. Whisper large-v3 nur 68
- Nemotron 3.5 Streaming: ~1 WER-Punkt hinter besten Batch-Modellen (6,93 % vs. 5,91 % EN)

### 1.2 Whisper-Stack (OpenAI + Community)

- **whisper-large-v3-turbo** (Okt 2024, 809M, MIT): 4 Decoder-Layer statt 32, ~4× schneller als large-v3 bei minimaler Qualitätseinbuße; Deutsch Tier-1.
- **faster-whisper** (v1.2.x, CTranslate2, MIT): RTX 5090 mit float16 ca. 6,5× Echtzeit (large-v3); int8-Batching ~12×+; **~130× Realtime für large-v3-turbo int8 auf RTX 5090** (gigagpu, 05/2026). Blackwell (sm_120) braucht CUDA ≥ 12.8 / CTranslate2 ≥ 4.6 — frühe RTX-50-Probleme (Issue #1287) sind mit aktuellen Versionen behoben.
- **whisper.cpp** (MIT, ggml): CUDA/Vulkan-Backend mit sm_120-Support seit 2025; v1.8.3 (01/2026) mit ~12×-Vulkan-iGPU-Boost. Auf NVIDIA meist langsamer als faster-whisper, glänzt auf CPU/Apple/Embedded. VRAM large-v3-turbo: f16 ≈ 3 GB, q5 ≈ 2 GB. Auf dem 9900X3D (CPU-only) large-v3-turbo q5 noch ~2–4× Echtzeit.
- Whisper ist **kein Streaming-Modell** — Streaming nur über VAD-Chunking (RealtimeSTT, faster-whisper-server). Naive Sliding-Window-Ansätze sind ~5× langsamer als Echtzeit wegen Puffer-Re-Evaluation.

### 1.3 Kyutai STT (06–07/2025)

- `kyutai/stt-1b-en_fr` (~1B, 0,5 s Delay, semantischer VAD), `kyutai/stt-2.6b-en` (~2.6B, 2,5 s Delay)
- Echtes Streaming, Wort-Timestamps; H100: 400 parallele Streams in Echtzeit
- **Nur Englisch/Französisch — kein Deutsch** → für DE-Szenarien irrelevant
- Code MIT/Apache, Weights CC-BY-4.0. Install: `uvx --with moshi` bzw. Rust-Server `cargo install --features cuda moshi-server`

### 1.4 Moonshine (Useful Sensors)

- tiny 27M / base 61M (Okt 2024, MIT): edge-optimiert, bessere WER als Whisper tiny/base bei ~5× Speed
- „Flavors of Moonshine" (09/2025): ar, zh, ja, ko, uk, vi — **kein offizielles Deutsch**
- Community-Fine-Tune `fidoriel/moonshine-base-de` (6,9 % WER CV22), aber **CC-BY-NC-SA-4.0** (nicht kommerziell)
- Für Deutsch nur bedingt relevant; ideal für englische Voice-Commands auf CPU

### 1.5 Kommerzielle Referenz (nur Einordnung)

AssemblyAI Universal-2/3 Pro (02/2026), Google Chirp 2 (GA 01/2025) + Gemini STT, Deepgram Nova-3 — proprietär; Open-Source-Spitze (Canary-Qwen, Parakeet) liegt auf dem Open-ASR-Leaderboard inzwischen auf Augenhöhe oder darüber.

### 1.6 STT-Empfehlungen für RTX 5090 / 9900X3D

- **Batch/Datei-Transkription, Deutsch:** `parakeet-tdt-0.6b-v3` — RTFx dreistellig bis vierstellig; 1 h Audio in Sekunden. Install: `pip install -U "nemo_toolkit[asr]"` (PyTorch mit CUDA 12.8+, sm_120) oder NeMo-Speech.cpp (GGUF).
- **Echtes Streaming, Deutsch (Voice-Agent, Live-Untertitel):** `nemotron-3.5-asr-streaming-0.6b` — einziges aktuelles Open-Source-Modell mit nativem deutschem Streaming, <300 ms Latenz; läuft auch CPU-only in Echtzeit (sherpa-onnx ≥ 1.13.4).
- **Robuster Allrounder, größtes Ökosystem:** faster-whisper large-v3-turbo — >10× Echtzeit, 2–3 GB VRAM, beste Tooling-Lage (whisperX, SubtitleEdit); minimal besserer DE-WER als Parakeet, dafür langsamer + Halluzinationsrisiko bei Stille.
- **CPU-Fallback:** whisper.cpp large-v3-turbo q5 (~2–4× RT auf 9900X3D).

---

## 2. TTS — Text-to-Speech

### 2.1 CPU-/kleine Modelle

| Modell | Release | Größe | Deutsch | Lizenz | Speed |
|---|---|---|---|---|---|
| **Kokoro-82M** (hexgrad) | 27.01.2025 (v1.0) | 82M | **nein** (nur inoffizielle G2P-Workarounds) | Apache 2.0 | CPU-RTF 0,64–0,67 (4-Core Xeon), UTMOS ~4,45 — beste CPU-Qualität; VRAM <2 GB; kein Cloning, 54 Stimmen |
| **Supertonic / Supertonic 3** | 2025 / 2026 | 66M / ~99M | v3: 31 Sprachen | Code MIT, Modell **OpenRAIL-M** (kommerzielle Einschränkungen prüfen!) | bis 167× RT (M4 Pro, 2 Steps); CPU-RTF 0,24 @5 Steps (MOS 4,32) — schnellste brauchbare CPU-Option |
| **Piper** (OHF-Voice) | laufend | VITS, klein | **ja** (thorsten low/medium/high, karlsson u. a.) | Code MIT, Stimm-Lizenzen einzeln prüfen (VOICES.md) | RTF 0,05–0,1 auf Desktop-CPU; läuft auf Raspberry Pi; solide, aber merkbar „TTS-artig"; kein Cloning |
| **KittenTTS** (KittenML) | v0.1: 08/2025; v0.8.1: 02/2026 | 15M/40M/80M (25–80 MB, ONNX) | **nein** (Multilingual angekündigt) | Apache 2.0 | ~300 ms Inferenz auf iPhone (Nano); Rust-Port `kitten_tts_rs` mit OpenAI-kompatiblem SSE-Streaming |
| **Kyutai Pocket TTS** | 01/2026; multilingual 04.05.2026 | ~100M | **ja** (EN, FR, DE, ES, PT, IT) | **MIT** | CPU-RTF 0,71 (4-Core Xeon), sehr flache Latenzkurve, UTMOS 4,10; Zero-Shot-Cloning aus ~5 s Referenz **auf CPU** |
| **NeuTTS Air** (Neuphonic) | 02.10.2025 | 748M (Qwen2-0.5B + NeuCodec) | primär EN; Community-Varianten | Apache 2.0 | Echtzeit auf CPU (llama.cpp, GGUF); Instant-Cloning aus 3–15 s Referenz |

### 2.2 GPU-Modelle mit Voice-Cloning

| Modell | Release | Größe | Deutsch | Lizenz | Speed/VRAM |
|---|---|---|---|---|---|
| **Chatterbox Turbo** (Resemble AI) | Ende 2025 | 350M | **nein** (nur EN) | MIT | **75 ms Latenz, 6× Echtzeit auf GPU**; Zero-Shot-Cloning ~5 s, Emotions-Steuerung, paralinguistische Tags (`[sigh]`, `[laugh]`), PerTh-Wasserzeichen; Blindtest: 65 % Win-Rate vs. ElevenLabs Turbo v2.5 |
| **Chatterbox Multilingual** | 2025 | 350M-Klasse | **ja** (23 Sprachen) | MIT | Deutsch-Option der Chatterbox-Familie |
| **F5-TTS** (SWivid) | v1 Base: 03/2025 | 336M Flow-Matching | kein offizielles DE; deutscher Fine-Tune existiert (f5-tts-german) | Code MIT, **Gewichte CC-BY-NC** (Emilia-Datensatz) | ~8 GB VRAM empfohlen, RTF ~0,1–0,2 auf starker GPU; Zero-Shot-Cloning |
| **XTTS v2** (Coqui → Community-Fork) | 11/2023 | ~1,8 GB | **ja** (17 Sprachen) | **Coqui Public Model License — nicht kommerziell** | Echtzeit auf GPU, ~4–8 GB VRAM, Streaming möglich; Coqui-Firma seit 01/2024 geschlossen |
| **Orpheus-TTS** (Canopy Labs) | 03/2025 | 3B (Llama-3.2-Basis) | kein natives DE; deutsche Community-Fine-Tunes existieren | Apache 2.0 | Streaming schneller als Echtzeit (A100), ~10+ GB VRAM, vLLM-Serving |
| **Sesame CSM-1B** | 03/2025 | 1B | **nein** | Apache 2.0 | Dialog-/kontextfokussiert, ~6–12 GB VRAM, eher Demo-/Forschungsqualität |
| **Dia 1.6B** (Nari Labs) | 04/2025 | 1.6B | **nein** | Apache 2.0 | Multi-Speaker-Dialog, nonverbale Laute; kein produktives Streaming |
| **Kyutai TTS 1.6B** | 07/2025 | 1.6B | nein (EN/FR) | Weights CC-BY-4.0 | Echtes Streaming via „Delayed Streams Modeling" — Audio beginnt, bevor Text komplett ist |
| **NVIDIA Magpie TTS Multilingual** | 05–06/2025 | 357M | **ja** (9–12 Sprachen) | NVIDIA Open Model License (kommerziell erlaubt) | NeMo-NanoCodec 22 kHz; **Magpie Zeroshot** (22.05.2025) für Cloning; läuft über NeMo/Riva; <2 GB VRAM — einzige NVIDIA-native Option mit gutem Deutsch |

### 2.3 Lizenz-Fallen

- **F5-TTS:** Gewichte CC-BY-NC → kein kommerzieller Einsatz ohne Retraining
- **XTTSv2:** CPML → nicht kommerziell
- **Supertonic:** OpenRAIL-M → kommerzielle Einschränkungen prüfen
- **Moonshine-base-de (Community):** CC-BY-NC-SA
- Sauber für Kommerz: Kokoro (Apache 2.0), Chatterbox (MIT), Pocket TTS (MIT), KittenTTS (Apache 2.0), NeuTTS Air (Apache 2.0), Piper-Code (MIT, Stimmen prüfen), Magpie (NVIDIA Open Model License)

### 2.4 TTS-Empfehlungen nach Zweck (mit Deutsch)

- **Schnellste deutsche Ausgabe, minimaler Aufwand:** Piper (thorsten-medium/high) — RTF <0,1, ältere Klangqualität
- **Bestes Gesamtpaket Deutsch + Cloning + saubere Lizenz:** Chatterbox Multilingual (GPU) oder Kyutai Pocket TTS (CPU, flache Latenz, ideal für Agenten)
- **GPU + Cloning + Deutsch + kommerziell:** Chatterbox Multilingual oder NVIDIA Magpie Multilingual/Zeroshot
- **Nur Englisch, höchste Qualität/Latenz:** Chatterbox Turbo (75 ms), Kokoro (CPU), KittenTTS (Edge)
- **Hinweis für Mimic_v2-Kontext:** Pocket TTS ist wegen MIT-Lizenz + CPU-Betrieb + 5-s-Cloning eine interessante Alternative/Ergänzung zu einem GPU-Worker-Setup
- CPU-Benchmarks oben liefen auf schwachem 4-Core-Xeon — der 9900X3D sollte Kokoro/Pocket/Supertonic/Kitten um Faktor 3–6 schneller laufen lassen

---

## 3. Linux-Diktat-Tools (Batch vs. Streaming)

### 3.1 Omarchy „Dictate" — das steckt dahinter

Omarchy (DHHs Arch/Hyprland-Distro) integriert seit **Omarchy 3.3 (Anfang 01/2026)** **Voxtype** als komplett lokale Diktat-Funktion (davor/parallel kursierte `hyprwhspr` im Omarchy-Umfeld).

- **Voxtype** (Rust-Binary, MIT, aktuell v0.7.5 vom 28.05.2026): systemweites Diktieren via Hotkey; Omarchy-Standard `Ctrl+Super+X`; alternativ Daemon-Hotkey (F13/ScrollLock) oder Hyprland `bind/bindr = SUPER, V, exec, voxtype record start/stop` (echtes Hold-to-talk)
- Engines (v0.7.x): Whisper ggml (tiny…large-v3-turbo), **Parakeet-TDT-0.6B-v3** (int8, ~670 MB), Moonshine, SenseVoice, Paraformer, Dolphin, Omnilingual, Cohere Transcribe (q4f16, 1,5 GB)
- GPU-Pfade: Vulkan (Whisper), CUDA 12/13, MIGraphX (AMD); **v0.7.3 (19.05.2026) liefert expliziten RTX-5090/Blackwell-Fix** (CUDA-13-Binary)
- Wayland first-class: Tippen via `wtype`, Fallback dotool → ydotool → Clipboard; Hyprland/Sway/River/Niri/KDE/GNOME
- Install: `yay -S voxtype-bin` (AUR), .deb/.rpm, AppImage, Nix-Flake
- **hyprwhspr** (MIT, Python): Backends Cohere/Parakeet-v3/Whisper-turbo + Cloud, Hotkey `Super+Alt+D`, AUR `hyprwhspr`

### 3.2 Batch-Tools im Vergleich

| Tool | Lizenz | Stand | Modelle | Wayland | Anmerkung |
|---|---|---|---|---|---|
| **Handy** | MIT | v0.9.6 | Whisper small→large-v3-turbo ggml (GPU), Parakeet V3 int8 (CPU ~5× RT), Parakeet-Unified GGUF | ja (wtype/dotool nötig; Hotkeys über Compositor + CLI-Flags) | Tauri/Rust, komplett offline, PTT + Toggle; Latenz 2–5 s nach Sprechende |
| **Whispering / Epicenter** | — | v7.5.1 | Whisper.cpp + Parakeet-v3 int8 ONNX, optional Cloud (Groq/OpenAI/Deepgram/ElevenLabs) | v7.5.1 fixt PipeWire | Tauri; Original-Repo 02/2026 archiviert → Epicenter-Monorepo |
| **Speech Note (dsnote)** | MPL-2.0 | 4.8.4 | whisper.cpp mit **Vulkan** (NVIDIA/AMD/Intel), Coqui, Vosk | ja | Qt-App, STT+TTS+Übersetzung; eher Notiz-/Transkriptionswerkzeug als Tipp-Ersatz |
| **nerd-dictation** | GPL-3.0 | letzter Commit 10/2025 | Vosk (~50 MB/Sprache) | nur via ydotool | **Echtes Streaming-Typing**, aber Vosk-Qualität deutlich hinter Whisper/Parakeet |
| **Vocalinux** | — | aktiv bis 06/2026 | Vosk | ja (IBus-Injection) | gepflegter Vosk-Fork mit Real-Time-Typing |
| **VoiceTypr** | AGPL-3.0 | — | Whisper lokal | **kein Linux** (nur macOS/Windows) | irrelevant |
| **Spokenly** | proprietär/Freemium | seit 2026 Linux | lokale Parakeet-/Whisper-Modelle | ja | komfortabel, aber closed source |
| Weitere | | | | | VOXD (whisper.cpp, CPU), OpenWhispr, ostt (Terminal), hyprvoice |

### 3.3 Streaming-Diktat — Wort-für-Wort live

**Warum das Block-Verhalten?** Parakeet/Whisper sind architekturell Offline-Modelle — sie können strukturell erst nach Sprechende liefern. Wort-für-Wort erfordert ein echtes Streaming-Modell.

**Tools mit echtem Live-Typing:**

| Tool | Streaming-Modell | Deutsch | Live-Ausgabe | Anmerkung |
|---|---|---|---|---|
| **Voxtype ≥ 0.7.2** (experimenteller Streaming-Modus) | `parakeet-unified-en-0.6b` (parakeet-rs, cache-aware) | **nein, nur EN** | inkrementell am Cursor (wtype/dotool) | Toggle- statt PTT-Modus erzwungen; seit v0.7.5 zusätzlich **Soniox-Cloud-Streaming** (kann DE, aber Cloud + API-Key) |
| **OpenWhispr ≥ 1.7.6** | **Nemotron 3.5 ASR Streaming** via sherpa-onnx ≥1.13.4 (INT8, CPU reicht) | **ja** | Partials live in Overlay-Pill, Commit beim Stoppen (kein zweiter Decode) | PTT auf Hyprland seit 1.9.0; AppImage/.deb; GPU nicht nötig |
| **nerd-dictation + Vosk-DE** | vosk-model-de-0.21 | ja | echte Partials am Cursor | Qualität spürbar schwächer; Kleinschreibung, keine Interpunktion |
| **Vocalinux** | Vosk | ja | echte Partials | aktiv gepflegt |
| **Saco93/voice-input** (07/2026, neu) | Qwen-Audio-Streaming | unklar | Live-Partials, Quickshell-HUD | Hyprland/Omarchy-spezifisch, sehr jung, Voxtype-Fallback |
| **shuvoice** | sherpa-onnx Zipformer-Streaming | **nein** (kein DE-Zipformer; DE nur Batch-Profil via NeMo/CUDA) | Live-Feedback im GTK4-Overlay | `yay -S shuvoice-git` |

**Deutsch-taugliche Streaming-Modelle (on-device):**

- **NVIDIA Nemotron 3.5 ASR Streaming 0.6B** (06/2026) — der relevante Neuzugang: 40 Sprachen inkl. DE, Auto-Detect, Chunks 80 ms–1,12 s, native Interpunktion. Lokal via **sherpa-onnx ≥ 1.13.4** (ONNX, realtime auf CPU; Community-ONNX-Export existiert) oder NeMo/NIM.
- **Vosk vosk-model-de-0.21** — echtes Streaming, läuft überall, aber sichtbar schwächer; keine Interpunktion/Kapitalisierung.
- **SimulStreaming (UFAL)** — Whisper large-v2/v3 (deutsch-tauglich) mit AlignAtt, ~1–2 s Latenz; Backend/Server, kein fertiges Diktat-Frontend. Nachfolger von whisper_streaming (deprecated).
- sherpa-onnx Streaming-Zipformer: **kein offizielles deutsches Online-Modell** (nur en/zh/fr/es/ko).

**DIY-Optionen:**

- **sherpa-onnx + Nemotron 3.5 + wtype** (empfohlen): `pip install sherpa-onnx` (≥1.13.4), Online-Recognizer, Partials per `wtype` ins fokussierte Feld. Latenz 0,3–1 s; CPU reicht, RTX 5090 überdimensioniert. Aufwand ~1 Abend.
- **RealtimeSTT** (KoljaB, aktiv): faster-whisper large-v3 → beste deutsche Rohqualität; `on_realtime_transcription_update`-Callback → wtype. Aber Pseudo-Streaming: Text wird alle ~0,5–1 s neu transkribiert und „springt"/korrigiert sich sichtbar; Latenz 1,5–3 s.
- **whisper.cpp `stream`-Beispiel**: bewusst naive Implementierung, höhere Latenz, Halluzinationen an Chunk-Grenzen — schlechter als RealtimeSTT.

**Ranking Streaming-Diktat (Deutsch + RTX 5090 + Hyprland):**

1. **OpenWhispr + Nemotron 3.5** — beste fertige Lösung: deutsch, lokal, CPU-genügsam, Hyprland-PTT, jedes Wort live sichtbar (im Overlay)
2. **DIY sherpa-onnx + Nemotron 3.5 + wtype** — wenn Partials direkt *ins fokussierte Feld* sollen
3. **Voxtype + Soniox-Streaming** — einziges fertiges Tool mit deutschen Partials am Cursor, aber Cloud
4. **Voxtype lokales Streaming** — perfekt integriert, aber nur Englisch
5. **nerd-dictation/Vocalinux + Vosk-DE** — funktioniert sofort, aber deutlich schlechtere Qualität

**Ehrliche Trade-offs:** Echte Streaming-Modelle liegen ~1 WER-Punkt hinter den besten Batch-Modellen und **revidieren ihre Hypothese sichtbar** (Wörter „setzen sich" — Architektur, kein Bug). Die RTX 5090 braucht keines der Streaming-Szenarien zwingend; der Engpass ist die Modell-Architektur, nicht die GPU.

---

## 4. Android on-device (TTS & STT)

### 4.1 System-STT (SpeechRecognizer)

- `createOnDeviceSpeechRecognizer()`/`isOnDeviceRecognitionAvailable()` seit API 31, `triggerModelDownload()`/`checkRecognitionSupport()` seit API 33, `EXTRA_PREFER_OFFLINE`
- Engine = **Google Speech Services** (Modelle via Google-App/Android System Intelligence): Referenzklasse in Qualität/Latenz (auch Deutsch), aber kein Modellzugriff, keine Garantie auf degoogled Geräten (GrapheneOS, /e/OS → On-device-STT fällt weg)
- Drittanbieter: **FUTO Voice Input** (proprietär, Whisper-basiert, komplett offline), **Sayboard** (F-Droid, Vosk/sherpa), whisper-to-input (Whisper-Tastatur)

### 4.2 sherpa-onnx — De-facto-Standard (Apache-2.0, v1.13.x, hochaktiv)

- Umfang: Streaming+Batch-ASR, TTS, VAD, Speaker-ID/Diarization, Keyword-Spotting; Android (arm64/arm32/x86_64), iOS, Desktop; NPU-Backends (Qualcomm QNN, Rockchip RKNN, Ascend, Axera)
- STT-Modelle: Streaming-Zipformer (EN 20M, läuft laut Doku auf Cortex-A7), SenseVoice, Moonshine, Whisper tiny–large (ONNX), Parakeet-TDT-0.6B-v2 int8, Paraformer, Dolphin, funasr-nano
- TTS-Modelle: VITS/Piper (100+ Stimmen inkl. DE), Matcha, Kokoro (v1.0 + multi-lang v1.1), KittenTTS, Pocket TTS, Supertonic, ZipVoice (Cloning)
- Integration: (a) Kotlin/Java-Lib (AAR); (b) als System-TTS-Engine via `TextToSpeechService` — fertige Apps: **SherpaTTS** (woheller69/ttsengine, GPLv3, F-Droid, v3.4 vom 20.07.2026, Piper/Coqui) und **VoxSherpa TTS** (Android 11+, Kokoro+Piper+VITS); danach nutzt jede App die normale `android.speech.tts.TextToSpeech`-API
- Desktop: `pip install sherpa-onnx`

### 4.3 Whisper auf Android

- whisper.cpp via NDK/JNI ist Standardweg; tiny/base schneller als Echtzeit auf modernen Phones; **small (~466 MB) auf Snapdragon 8 Gen 3: 30 s Audio in ~3–6 s**; medium grenzwertig, large kaum sinnvoll
- **Streaming-Falle:** `whisper_full` nicht streaming-fähig; naive Sliding-Window-Aufrufe ~5× langsamer als Echtzeit → für echtes Streaming sherpa-onnx-Modelle (Zipformer/SenseVoice) nutzen
- NPU: Qualcomm AI Hub liefert quantisiertes whisper-large-v3-turbo für Snapdragon-NPUs; sherpa-onnx kann QNN direkt

### 4.4 TTS-Benchmarks auf Phone (VoicePing 02/2026, Galaxy S10/Exynos 9820, sherpa-onnx)

| Modell | RTF | Median-Synthese |
|---|---|---|
| Android System TTS | 0,058 | 440 ms |
| Piper (VITS low) | 0,077 | ~500 ms |
| Matcha-Icefall | 0,135 | 1,1 s |
| Kitten Nano (fp16) | 0,387 | 3,5 s |
| Kokoro (en v0.19) | 1,13 | 8,2 s |
| Kokoro int8 multi-lang v1.1 | 2,42 | 15,3 s |

→ Piper/Matcha/KittenTTS auf jedem modernen Phone locker echtzeitfähig; Kokoro auf Flagships (SD 8 Gen 3/Elite) ca. RTF 0,3–0,6 (extrapoliert). eSpeak-NG (RTF ~0,001) und RHVoice klingen roboterhaft.

**Android-Empfehlung:** Piper/Matcha für Echtzeit-Interaktion, Kokoro-en für Qualität auf Flagships, KittenTTS Nano als sparsame Mittellösung; STT-Streaming mit sherpa-onnx SenseVoice/Zipformer statt Whisper-Streaming; identische ONNX-Modelle Desktop ↔ Phone für direkte Vergleichbarkeit.

---

## 5. Gesamt-Empfehlungen (RTX 5090 / 9900X3D / Hyprland)

| Zweck | Empfehlung |
|---|---|
| Diktat Desktop (Batch, max. Qualität) | Voxtype + Parakeet-TDT-0.6B-v3 (CUDA-13-Variante wegen Blackwell) |
| Diktat Desktop (Live Wort-für-Wort) | OpenWhispr + Nemotron 3.5; alternativ DIY sherpa-onnx + Nemotron 3.5 + wtype |
| Live-Streaming-STT (Voice-Agent) | Nemotron 3.5 ASR Streaming (sherpa-onnx oder NeMo) |
| Batch-Transkription Dateien (DE) | parakeet-tdt-0.6b-v3 via NeMo; Alternative faster-whisper large-v3-turbo |
| TTS Deutsch + Cloning + kommerziell | Chatterbox Multilingual (GPU) oder Kyutai Pocket TTS (CPU, MIT) |
| TTS minimal/schnell, Deutsch | Piper (thorsten) |
| TTS Englisch, niedrigste Latenz | Chatterbox Turbo (75 ms) oder KittenTTS (Edge) |
| Android TTS | sherpa-onnx-Engine (SherpaTTS/VoxSherpa) mit Piper/Kokoro |
| Android STT | sherpa-onnx Streaming (SenseVoice/Zipformer); Whisper small nur Batch |

---

## 6. Quellen

**STT:** huggingface.co/nvidia/parakeet-tdt-0.6b-v3 · huggingface.co/nvidia/canary-1b-v2 · huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b · marktechpost.com/2026/06/06 (Nemotron 3.5) · arxiv.org/html/2509.14128v1 (Parakeet v3) · github.com/TheStageAI/TheWhisper (DE-WER-Benchmark) · github.com/SYSTRAN/faster-whisper · gigagpu.com (RTX-5090-Whisper-Benchmarks) · github.com/kyutai-labs/delayed-streaming-modeling · arxiv.org/abs/2509.08753 · github.com/usefulsensors/moonshine · github.com/k2-fsa/sherpa-onnx/issues/2918 (Parakeet kein Streaming) · apiscout.dev (API-Vergleich 2026)

**TTS:** github.com/hexgrad/kokoro · spheron.network (TTS-Deploy-Guide 2026) · heyneo.com (CPU-Benchmark Kokoro/Supertonic/Pocket) · github.com/supertone-inc/supertonic · github.com/rhasspy/piper (VOICES.md) · kittenml-kittentts.mintlify.app · github.com/second-state/kitten_tts_rs · kyutai.org/tts + kyutai.org/blog/2026-05-04-pocket-tts-multilingual · github.com/neuphonic/neutts + marktechpost.com/2025/10/02 · resemble.ai/learn/models/chatterbox-turbo · github.com/resemble-ai/chatterbox · github.com/SWivid/F5-TTS · modelscope.cn (f5-tts-german) · whipscribe.com/tools/coqui-xtts · github.com/canopyai/Orpheus-TTS · developer.nvidia.com/blog (Magpie TTS, 14.07.2025) · build.nvidia.com/nvidia/magpie-tts-multilingual/modelcard · codesota.com/speech/best-open-source · picovoice.ai/blog/on-device-tts

**Diktat/Linux:** voxtype.io/news · github.com/peteonrails/voxtype · linuxiac.com (Omarchy 3.3, 08.01.2026) · sudomarchy.com (Remap-Guide) · github.com/goodroot/hyprwhspr · github.com/cjpais/handy · spokenly.app/blog/handy-review · github.com/braden-w/whispering · github.com/mkiol/dsnote · github.com/ideasman42/nerd-dictation · vocalinux.com · openwhispr.com/blog/local-streaming-speech-to-text + openwhispr.com/changelog · build.nvidia.com/nvidia/nemotron-asr-streaming/modelcard · github.com/codavidgarcia/nemotron-3.5-asr-streaming-onnx · alphacephei.com/vosk/models · github.com/ufal/SimulStreaming · github.com/KoljaB/RealtimeSTT · github.com/ggerganov/whisper.cpp (stream-Beispiel) · github.com/shuv1337/shuvoice · github.com/Saco93/voice-input · phoronix.com (whisper.cpp 1.8.3)

**Android:** developer.android.com/reference/android/speech/SpeechRecognizer · github.com/k2-fsa/sherpa-onnx · github.com/woheller69/ttsengine (SherpaTTS) · github.com/CodeBySonu95/VoxSherpa-TTS · voiceping.net/en/blog/research-offline-tts-eval · github.com/hrushik98/whisper-mobile · github.com/qualcomm/ai-hub-models · github.com/KittenML/KittenTTS · github.com/siva-sub/NekoSpeak · voiceinput.futo.tech · yaps.ai/blog/private-voice-keyboard-android · inferencebench.io (whisper-turbo 597× H100)

---

## 7. Offene Punkte / Unsicherheiten

- Nemotron 3.5 ASR Streaming ist noch sehr neu (06/2026) — Details gegen die HF-Modelcard verifizieren
- RTFx-Werte des Open-ASR-Leaderboards (A100, große Batches) sind nicht 1:1 auf Single-Stream-Latenz übertragbar
- Kokoro-RTF auf SD 8 Elite ist Extrapolation (S10/iPad-Werte gemessen)
- Kein offizieller KittenTTS-Android-SDK-Termin bekannt
- Exaktes Chatterbox-Turbo-Releasedatum nicht primärquellen-verifiziert
- Piper-Stimmlizenzen je Voice unterschiedlich (VOICES.md prüfen)
- Google veröffentlicht keine Specs zu den On-device-Speech-Services-Modellen
