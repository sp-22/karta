"""Loads Laya for the harness: its agent (karta.decider.laya.load_agent) and a cached embedding function, both on
first use. The questions themselves are karta/harness/decide.py's."""

from __future__ import annotations

from typing import Any


class LayaMatcher:
    def __init__(self, agent: Any = None, model: str = "english", embed: Any = None):
        self._agent = agent
        self.model = model
        self._embed = embed

    @property
    def embed(self) -> Any:
        if self._embed is None:
            import laya_mlx

            from karta.decider.laya import cached_embed

            self._embed = cached_embed(laya_mlx.embed_fn_from_agent(self.agent))
        return self._embed

    @property
    def agent(self) -> Any:
        if self._agent is None:
            from karta.decider.laya import load_agent

            self._agent = load_agent(self.model)
        return self._agent
