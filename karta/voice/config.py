"""Voice settings. Adapted from jev-voice (https://github.com/kevinbadi/jev-voice), MIT License."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SUPPORT = Path.home() / "Library" / "Application Support" / "karta"
WHISPER_FILE = "ggml-base.en.bin"
WHISPER_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{WHISPER_FILE}"
# `karta setup` downloads the model into Application Support; a copy in a clone's models/ folder still works
_CLONE_MODEL = ROOT / "models" / WHISPER_FILE
WHISPER_MODEL = Path(
    os.environ.get("WHISPER_MODEL") or (_CLONE_MODEL if _CLONE_MODEL.exists() else SUPPORT / "models" / WHISPER_FILE)
)
WHISPER_PORT = int(os.environ.get("WHISPER_PORT", "8178"))
WHISPER_THREADS = int(os.environ.get("WHISPER_THREADS", "6"))
SAMPLE_RATE = 16000
