"""Screen: turn what the Mac shows into Items Laya can read, within its 512 tokens.

Reuses karta.base (perception, models.Screen.region) wherever it can.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import fields, replace
from typing import Any

import numpy as np

from karta.harness.types import Item, Screen

Embed = Callable[[Sequence[str]], np.ndarray]
LAYA_TOKENS = 512
LAYA_HEAD_TOKENS = 192  # laya_mlx keeps instructions plus options within this; longer options are cut
ROW_MATES = 3  # a repeated name carries up to three row neighbours
CHARS_PER_TOKEN = 4  # the estimate when no Laya tokenizer is loaded
MARGIN_TOKENIZED = 8  # slack on an exact count: the special tokens laya_mlx adds
MARGIN_ESTIMATED = 32  # slack on a guess

_tokenizer: Any = None  # laya_mlx's Tokenizer, found once on a loaded Agent


# ---------------------------------------------------------------- rows


def with_rows(items: list[Item], lines: Sequence[int | float], tolerance: float | Sequence[float] = 0.5) -> list[Item]:
    """Give every item whose name repeats the names sharing its row (`lines[i]`: item i's row, e.g. its centre y).

    A repeated name gets the others on its row, left to right (the items are in screen
    order), up to three. Two items share a row when their lines differ by less than `tolerance`: 0.5 for row
    numbers, or half the repeated item's height when `lines` are centre ys (one value per item).
    """
    counts = Counter(it.name for it in items)
    tol = [float(tolerance)] * len(items) if isinstance(tolerance, int | float) else list(tolerance)
    out = []
    for i, it in enumerate(items):
        if not it.name or counts[it.name] < 2:
            out.append(it)
            continue
        mates = [o.name for j, o in enumerate(items) if j != i and o.name and abs(lines[j] - lines[i]) < tol[i]]
        out.append(replace(it, row=tuple(mates[:ROW_MATES])) if mates else it)
    return out


# ---------------------------------------------------------------- OCR clean-up

_KEEP_LEAD = "(\"'$€£₹¥#@"  # a quote, a price, a hashtag or a mention starts real text
_LEAD = re.compile(rf"^(?:[^\w\s{re.escape(_KEEP_LEAD)}+\-]|[+\-](?!\d)|\s)+")
_ICON_Q = re.compile(r"^[QO]\s+(?=[A-Z])")  # a magnifier read as a letter: "Q Search mail"
_TRAIL = re.compile("[\\s|<>\u2039\u203a«»•·*~^_=/\\\\]+$")  # chevrons, bullets, bars after the text


def clean_ocr(text: str) -> str:
    """Drop icon debris and stray symbols from an OCR line ("/ Compose" -> "Compose"); "" when nothing is left."""
    s = " ".join(text.split())
    s = _ICON_Q.sub("", _LEAD.sub("", s))
    s = _TRAIL.sub("", s)
    return s if any(c.isalnum() for c in s) else ""


# ---------------------------------------------------------------- shortlist


def _words(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 2}


def _text(item: Item) -> str:
    return f"{item.name} {item.value}".strip()


def shortlist(query: str, items: list[Item], embed: Embed, k: int = 16) -> list[Item]:
    """The k items most related to `query` (shared words first, then embedding similarity), in screen order."""
    if len(items) <= k:
        return list(items)
    words = _words(query)
    vectors = np.asarray(embed([query] + [_text(i) for i in items]), dtype=np.float32)
    vectors = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
    similarity = vectors[1:] @ vectors[0]
    order = sorted(range(len(items)), key=lambda i: (-len(words & _words(_text(items[i]))), -float(similarity[i])))
    return [items[i] for i in sorted(order[:k])]


def state_lines(query: str, items: list[Item], embed: Embed, k: int = 20) -> str:
    """The page for a Laya state: the k most related items, one `Item.describe()` per line."""
    return "\n".join(i.describe() for i in shortlist(query, items, embed, k))


# ---------------------------------------------------------------- token budget


def use_tokenizer(tok: Any) -> None:
    """Count with this tokenizer (laya_mlx's Agent.tok, or anything called like it); None goes back to looking."""
    global _tokenizer
    _tokenizer = tok


def _find_tokenizer() -> Any:
    """A loaded Agent's tokenizer, never a load: karta.decider.laya keeps the agents it loaded."""
    global _tokenizer
    if _tokenizer is None:
        laya = sys.modules.get("karta.decider.laya")
        for agent in getattr(laya, "_AGENTS", {}).values():
            if getattr(agent, "tok", None) is not None:
                _tokenizer = agent.tok
                break
    return _tokenizer


def count_tokens(text: str) -> int:
    """Laya's token count for `text` (its tokenizer when loaded, else a close estimate)."""
    tok = _find_tokenizer()
    if tok is not None:
        return len(tok(text, add_special_tokens=False)["input_ids"])
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def fits_laya(state: dict, instructions: str, options: Sequence[str], limit: int = LAYA_TOKENS) -> bool:
    """Whether one question (state + instructions + options) fits Laya's context; callers shorten when it does not.

    Counted the way laya_mlx builds it: "choice question: <instructions> [MASK] opt ... <state json>", with
    a margin. The question part has its own cap (192 tokens), past which laya_mlx cuts the options.
    """
    margin = MARGIN_TOKENIZED if _find_tokenizer() is not None else MARGIN_ESTIMATED
    head = count_tokens(f"choice question: {instructions}") + sum(1 + count_tokens(" " + o) for o in options)
    body = count_tokens(json.dumps(state, ensure_ascii=False))
    return head <= LAYA_HEAD_TOKENS and head + body + margin <= limit


# ---------------------------------------------------------------- fixtures

_ITEM_FIELDS = tuple(f.name for f in fields(Item) if f.name != "ref")


def item_to_json(item: Item) -> dict:
    return {k: list(item.row) if k == "row" else getattr(item, k) for k in _ITEM_FIELDS}


def screen_to_json(screen: Screen) -> dict:
    """A Screen as plain JSON, without the env's handles: a fixture to replay to Laya later."""
    return {
        "app": screen.app,
        "url": screen.url,
        "window": screen.window,
        "profile": screen.profile,
        "items": [item_to_json(i) for i in screen.items],
    }


def screen_from_json(data: dict) -> Screen:
    """A fixture back into a Screen; its items have no ref, so nothing can act on them."""
    items = [Item(**{k: tuple(v) if k == "row" else v for k, v in raw.items() if k in _ITEM_FIELDS}) for raw in data["items"]]
    return Screen(app=data["app"], items=items, url=data.get("url"), window=data.get("window", ""), profile=data.get("profile"))
