"""Laya, the local decision model, loaded once per checkpoint through laya-mlx, and a cached embedder."""

from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

REPO = "convaiinnovations/laya"
LAYA_MODELS: dict[str, str | None] = {"english": None, "multilingual": "multilingual", "typed-decisions": "typed-decisions"}
_AGENTS: dict[str, Any] = {}  # one loaded model per checkpoint, for the life of the process


def load_agent(model: str) -> Any:
    if model not in _AGENTS:
        import laya_mlx

        with warnings.catch_warnings():  # laya-mlx notes this checkpoint's calibration on every load; nothing to act on
            warnings.filterwarnings("ignore", message="laya-mlx: this checkpoint ships temperatures")
            _AGENTS[model] = laya_mlx.load(REPO, subfolder=LAYA_MODELS[model])
    return _AGENTS[model]


def cached_embed(embed_fn: Callable[[Sequence[str]], np.ndarray]) -> Callable[[Sequence[str]], np.ndarray]:
    """Screen labels repeat from step to step; embed each distinct text once."""
    cache: dict[str, np.ndarray] = {}

    def embed(texts: Sequence[str]) -> np.ndarray:
        missing = [t for t in dict.fromkeys(texts) if t not in cache]
        if missing:
            for text, vector in zip(missing, embed_fn(missing), strict=True):
                cache[text] = np.asarray(vector)
        return np.stack([cache[t] for t in texts])

    return embed
