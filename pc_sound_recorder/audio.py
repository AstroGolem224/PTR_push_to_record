from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable


class RecorderError(RuntimeError):
    pass


# codec, option name, min, max for the quality value; wav ignores quality.
FORMATS: dict[str, tuple[str, str | None, int, int]] = {
    "mp3": ("libmp3lame", "-q:a", 0, 9),
    "flac": ("flac", "-compression_level", 0, 12),
    "ogg": ("libvorbis", "-q:a", -1, 10),
    "wav": ("pcm_s16le", None, 0, 0),
}

SILENCE_THRESHOLD_DB = -45.0

_MEAN_VOLUME = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def parse_mean_volume(stderr: str) -> float | None:
    """Extract mean_volume (dB) from ffmpeg volumedetect output; None on any mismatch."""
    match = _MEAN_VOLUME.search(stderr)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _codec_args(fmt: str, quality: int) -> list[str]:
    try:
        codec, option, low, high = FORMATS[fmt]
    except KeyError:
        raise RecorderError(f"Unbekanntes Aufnahmeformat: {fmt}") from None
    args = ["-c:a", codec]
    if option is not None:
        try:
            clamped = max(low, min(high, int(quality)))
        except (TypeError, ValueError):
            raise RecorderError(
                f"Ungültiger Qualitätswert für {fmt}: {quality!r}"
            ) from None
        args += [option, str(clamped)]
    return args


def build_command(
    monitor: str,
    mic_source: str | None,
    path: pathlib.Path | str,
    fmt: str = "mp3",
    quality: int = 2,
) -> list[str]:
    """Pure ffmpeg command builder; mic_source mixes the default microphone in."""
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "pulse", "-i", monitor,
    ]
    if mic_source:
        command += [
            "-f", "pulse", "-i", mic_source,
            # normalize=0: amix would otherwise scale each input by 1/n.
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[a]",
            "-map", "[a]",
        ]
    command += ["-ac", "2", "-ar", "48000"]
    command += _codec_args(fmt, quality)
    command.append(str(path))
    return command


def default_monitor() -> str:
    """Resolve the monitor belonging to the current default output sink."""
    if not shutil.which("pactl"):
        raise RecorderError("pactl fehlt; PipeWire-Pulse ist erforderlich.")
    result = subprocess.run(
        ["pactl", "get-default-sink"], capture_output=True, text=True, check=False
    )
    sink = result.stdout.strip()
    if result.returncode != 0 or not sink:
        raise RecorderError("Der Standard-Audioausgang konnte nicht ermittelt werden.")
    return f"{sink}.monitor"


def default_source() -> str | None:
    """Default microphone source; None (with no exception) when unavailable."""
    if not shutil.which("pactl"):
        return None
    result = subprocess.run(
        ["pactl", "get-default-source"], capture_output=True, text=True, check=False
    )
    source = result.stdout.strip()
    if result.returncode != 0 or not source:
        return None
    return source


def _check_silence(
    path: pathlib.Path,
    threshold_db: float,
    on_silence: Callable[[float], None],
) -> None:
    """Run volumedetect in the background; report only, never raise."""
    if not shutil.which("ffmpeg"):
        return
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    mean = parse_mean_volume(result.stderr or "")
    if mean is not None and mean < threshold_db:
        on_silence(mean)


class AudioRecorder:
    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.temporary_path: pathlib.Path | None = None
        self.started_at = 0.0
        self.last_warning: str | None = None

    @property
    def is_recording(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(
        self,
        output_dir: pathlib.Path,
        fmt: str = "mp3",
        quality: int = 2,
        mix_microphone: bool = False,
    ) -> pathlib.Path:
        if self.is_recording:
            raise RecorderError("Es läuft bereits eine Aufnahme.")
        if not shutil.which("ffmpeg"):
            raise RecorderError("ffmpeg fehlt und wird für die Aufnahme benötigt.")
        if fmt not in FORMATS:
            raise RecorderError(f"Unbekanntes Aufnahmeformat: {fmt}")

        self.last_warning = None
        mic_source = None
        if mix_microphone:
            mic_source = default_source()
            if mic_source is None:
                self.last_warning = (
                    "Keine Mikrofonquelle gefunden – es wird nur der PC-Ton aufgenommen."
                )

        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")[:-3]
        self.temporary_path = output_dir / f"PC-Ton_{stamp}.unfertig.{fmt}"
        monitor = default_monitor()
        command = build_command(monitor, mic_source, self.temporary_path, fmt, quality)
        try:
            self.process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            self.process = None
            raise RecorderError(f"Aufnahme konnte nicht gestartet werden: {error}") from error
        self.started_at = time.monotonic()
        time.sleep(0.08)
        if self.process.poll() is not None:
            self.process = None
            self.temporary_path.unlink(missing_ok=True)
            raise RecorderError("ffmpeg konnte die Monitorquelle nicht öffnen.")
        return self.temporary_path

    def stop(
        self,
        minimum_seconds: float = 0.20,
        silence_warn: bool = False,
        on_silence: Callable[[float], None] | None = None,
    ) -> pathlib.Path | None:
        process, temporary = self.process, self.temporary_path
        self.process = None
        self.temporary_path = None
        if process is None or temporary is None:
            return None
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=2)

        duration = time.monotonic() - self.started_at
        if duration < minimum_seconds or not temporary.exists() or temporary.stat().st_size < 1024:
            temporary.unlink(missing_ok=True)
            return None
        finished = temporary.with_name(temporary.name.replace(".unfertig.", "."))
        temporary.replace(finished)
        if silence_warn and on_silence is not None:
            threading.Thread(
                target=_check_silence,
                args=(finished, SILENCE_THRESHOLD_DB, on_silence),
                daemon=True,
            ).start()
        return finished

    def cancel(self) -> None:
        process, temporary = self.process, self.temporary_path
        self.process = None
        self.temporary_path = None
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
