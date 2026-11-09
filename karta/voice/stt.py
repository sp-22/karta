"""Speech to text via whisper.cpp's `whisper-server` (Metal accelerated, model stays loaded)."""

# Adapted from jev-voice (https://github.com/kevinbadi/jev-voice), MIT License.
from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
import time
import wave

import httpx
import numpy as np

from . import config

_BLANK = {"", "[BLANK_AUDIO]", "(silence)", "[silence]", "[inaudible]", "[Music]", "[MUSIC]", "(music)"}
# Whisper hallucinates these on near-silent audio.
START_TIMEOUT = 60  # seconds
_HALLUCINATIONS = {"thank you.", "thanks for watching.", "thank you for watching.", "you", "bye.", "thank you"}


def _wav_bytes(pcm: np.ndarray, rate: int = config.SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(pcm, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


class WhisperServer:
    def __init__(self, port: int = config.WHISPER_PORT) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.proc: subprocess.Popen | None = None
        self.http = httpx.Client(timeout=30.0)

    def _alive(self) -> bool:
        try:
            r = self.http.get(self.url + "/", timeout=1.0)
            return r.status_code < 500
        except Exception:
            return False

    def start(self) -> None:
        if self._alive():
            return
        exe = shutil.which("whisper-server")
        if not exe:
            raise SystemExit("whisper-server not found: run `karta setup` (it installs whisper-cpp with Homebrew)")
        if not config.WHISPER_MODEL.exists():
            raise SystemExit(f"Whisper model missing: {config.WHISPER_MODEL}\n  run `karta setup` to download it")
        self.proc = subprocess.Popen(
            [
                exe,
                "-m",
                str(config.WHISPER_MODEL),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "-t",
                str(config.WHISPER_THREADS),
                "-l",
                "en",
                "-nt",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(START_TIMEOUT * 20):  # the first start loads the model and compiles Metal shaders
            if self._alive():
                self._warm_up()
                return
            time.sleep(0.05)
        raise SystemExit("whisper-server failed to start")

    def _warm_up(self) -> None:
        """First inference compiles Metal shaders (~2 s); pay that before the user speaks."""
        tone = 0.05 * np.sin(np.linspace(0, 2 * np.pi * 220 * 0.6, int(config.SAMPLE_RATE * 0.6)))
        with contextlib.suppress(Exception):
            self.transcribe(tone.astype(np.float32))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def transcribe(self, pcm: np.ndarray) -> str:
        files = {"file": ("audio.wav", _wav_bytes(pcm), "audio/wav")}
        data = {"response_format": "json", "temperature": "0.0", "no_timestamps": "true", "language": "en"}
        r = self.http.post(self.url + "/inference", files=files, data=data)
        r.raise_for_status()
        text = (r.json().get("text") or "").strip()
        if text in _BLANK or text.lower() in _HALLUCINATIONS or len(text) < 2:
            return ""
        return text
