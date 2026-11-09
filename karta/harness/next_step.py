"""Next step from the screen alone: once the right app or site is open, can Laya pick the next action with no brain?

Options are built by code from the screen, never from the answer: every enabled, visible control on the page as
"click <label>" (a row, a tab, a link, a button, a checkbox), every field as "type into <label>", and three fixed
options: DONE, SCROLL, BLOCKED. A label that repeats on the page carries its context ("click ADD (row: Crunchy
Salted Chips 50 g)"): the most wordy of its row mates, or its region. The list is shortlisted to Laya's budget
(<= 16 options, the head within 192 tokens) by relation to the task only, then offered in screen order, so the
right option is never favoured by its place. One question, in the style of decide.py; the Choice's ranking maps
back to Actions.

Used by scripts/next_step_bench.py on hand-made cases (karta.harness.next_step_cases) and on recorded runs
(karta.harness.snapshots, read back by `load_recorded`).
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from karta.harness.decide import HEAD_TOKENS, MAX_OPTIONS, _short, _words
from karta.harness.screen import count_tokens, fits_laya, state_lines
from karta.harness.types import Choice, Item, Screen

DECISION = "N1 next step"
DONE, SCROLL, BLOCKED = "the task is done", "scroll down", "something is in the way"
FIXED = (DONE, SCROLL, BLOCKED)
CLICKABLE = ("button", "link", "row", "tab", "checkbox", "menu")
INSTRUCTIONS = {
    "next": "Task: `task`. What should be done next on this screen?",
    "forward": "Which action moves `task` one step forward?",
}
STATE_LINES = 24


@dataclass(frozen=True)
class Action:
    """One option's meaning: ("click", item index) / ("type", item index), or a fixed option (index -1)."""

    verb: str  # "click", "type", or one of FIXED
    index: int = -1  # the item's index in screen.items

    @property
    def key(self) -> tuple[str, int]:
        return (self.verb, self.index)


@dataclass
class Case:
    name: str
    family: str  # "shop", "chat", "notes", ...
    task: str
    screen: Screen
    answers: frozenset[tuple[str, int]]  # Action keys that are right
    source: str = "made"  # "made" or "recorded"
    note: str = ""


@dataclass
class Options:
    options: dict[str, Action]  # label -> meaning, in the order offered
    dropped: list[str] = field(default_factory=list)  # labels cut by the budget


def actionable(screen: Screen) -> list[int]:
    """Indexes of the page's items one can act on: enabled, visible, a control or a field."""
    return [
        n
        for n, i in enumerate(screen.items)
        if not i.menu
        and not i.chrome
        and not i.browser_ui
        and i.enabled
        and not i.offscreen
        and (i.kind in CLICKABLE or i.kind == "field")
    ]


def _context(item: Item) -> str:
    """What sets a repeated label apart: its wordiest row mate that is not itself, else its region."""
    mates = [r for r in item.row if r.strip().lower() != item.name.strip().lower()]
    if mates:
        return "row: " + _short(max(mates, key=lambda m: (sum(c.isalpha() for c in m), -mates.index(m))), 6)
    return item.region


def label_all(screen: Screen, context: bool = True) -> dict[str, Action]:
    """Every actionable item's option label, unique, in screen order, then the fixed options."""
    idx = actionable(screen)
    counts = Counter(screen.items[n].name.strip().lower() for n in idx)
    out: dict[str, Action] = {}
    for n in idx:
        item = screen.items[n]
        verb = "type" if item.kind == "field" else "click"
        label = f"{'type into' if verb == 'type' else 'click'} {_short(item.name.strip() or item.kind, 7)}"
        if context and counts[item.name.strip().lower()] > 1:
            where = _context(item)
            if where:
                label = f"{label} ({where})"
        base, k = label, 2
        while label in out or label in FIXED:
            label, k = f"{base} #{k}", k + 1
        out[label] = Action(verb, n)
    for fixed in FIXED:
        out[fixed] = Action(fixed)
    return out


def head_tokens(instructions: str, labels: list[str]) -> int:
    """The question head as Laya.ask counts it (decide.Laya._head): instructions, options, 3 special tokens."""
    return count_tokens(f"choice question: {instructions}") + sum(1 + min(48, count_tokens(" " + o)) for o in labels) + 3


def relevance(task: str, screen: Screen, labels: dict[str, Action], embed=None) -> list[str]:
    """The item labels most related to the task first: shared words, then embedding similarity, then screen order.
    Uses the task and the screen only, never an answer."""
    items = [lab for lab, a in labels.items() if a.index >= 0]
    words = _words(task)
    if embed is not None and items:
        texts = [screen.items[labels[lab].index].describe() for lab in items]
        vectors = np.asarray(embed([task, *texts]), dtype=float)
        vectors = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
        similarity = list(vectors[1:] @ vectors[0])
    else:
        similarity = [0.0] * len(items)

    dialog = any(i.dialog for i in screen.page())
    active = {w for i in screen.page() if i.kind == "field" and (i.focused or i.value) for w in i.region.split("-") if w}

    def key(n: int) -> tuple:
        item = screen.items[labels[items[n]].index]
        return (
            -(dialog and item.dialog),  # a popup in front: its controls are what can be used now
            -len(words & (_words(item.name) | _words(" ".join(item.row)))),
            -bool(active & set(item.region.split("-"))),  # next to the field being filled in
            -float(similarity[n]),
            labels[items[n]].index,
        )

    return [items[n] for n in sorted(range(len(items)), key=key)]


def build(
    task: str, screen: Screen, context: bool = True, embed=None, instructions: str | None = None, state: dict | None = None
) -> Options:
    """The options for one question: the fixed three always, then the most related items that fit, in screen order."""
    instructions = instructions or INSTRUCTIONS["next"]
    labels = label_all(screen, context)
    ranked = relevance(task, screen, labels, embed)
    keep = list(ranked[: MAX_OPTIONS - len(FIXED)])
    state = state if state is not None else {"task": task}

    def offered(chosen: list[str]) -> list[str]:
        return sorted(chosen, key=lambda lab: labels[lab].index) + list(FIXED)

    while keep and (head_tokens(instructions, offered(keep)) > HEAD_TOKENS or not fits_laya(state, instructions, offered(keep))):
        keep.pop()
    dropped = [lab for lab in ranked if lab not in keep]
    return Options({lab: labels[lab] for lab in offered(keep)}, dropped)


def page_lines(screen: Screen, task: str = "", embed=None, k: int = STATE_LINES) -> str:
    """The page for Laya's state, one describe() line each (menus and chrome left out): with an embed, the k items
    most related to the task (screen.shortlist), else the first k; in screen order either way."""
    page = screen.page()
    if embed is not None and task:
        return state_lines(task, page, embed, k)
    return "\n".join(i.describe() for i in page[:k])


def ask(
    laya,
    task: str,
    screen: Screen,
    context: bool = True,
    with_page: bool = True,
    embed=None,
    shuffle: int | None = None,
    template: str = "next",
) -> tuple[Choice, Options]:
    """One Laya question: which of the built options is the next step. Returns the Choice (option labels) and options.
    `shuffle` (a seed) offers the item options in a random order instead of screen order: a check on position bias."""
    instructions = INSTRUCTIONS[template]
    embed = embed if embed is not None else getattr(laya, "embed", None)
    state = {"task": task}
    if with_page:
        state["page"] = page_lines(screen, task, embed)
    opts = build(task, screen, context, embed, instructions, {"task": task})
    fixed = tuple(FIXED)
    if shuffle is not None:
        items = [lab for lab in opts.options if lab not in fixed]
        random.Random(f"{shuffle}:{task}").shuffle(items)
        opts = Options({lab: opts.options[lab] for lab in [*items, *fixed]}, opts.dropped)
    choice = laya.ask(
        DECISION, state, instructions, dict.fromkeys(opts.options), lines_key="page" if with_page else None, keep=fixed
    )
    return choice, opts


# -- recorded runs --------------------------------------------------------------------------------------------------


def _target_text(action: dict) -> str:
    target = action.get("target") or {}
    return (target.get("name") or action.get("text") or "").strip().lower()


def label_recorded(record: dict) -> list[tuple[dict, bool]]:
    """Each recorded action with whether it counts as right: the first ledger line (from where the last one matched)
    that names its target decides (a checkmark is right); with no such line, the task's own `done` decides."""
    ledger = [str(line) for line in record.get("ledger", [])]
    pointer, out = 0, []
    for action in record.get("actions", []):
        text = _target_text(action)
        hit = next((n for n in range(pointer, len(ledger)) if text and text in ledger[n].lower()), None)
        if hit is None:
            out.append((action, bool(record.get("done"))))
        else:
            pointer = hit
            out.append((action, ledger[hit].lstrip().startswith("✓")))
    return out


def recorded_cases(record: dict, name: str = "recorded") -> list[Case]:
    """The cases in one snapshot file: each right click / typing / scroll down on a fresh screen, and DONE on the
    final screen of a finished task. Other tools (open, press, back) have no option here and are left out."""
    from karta.harness.snapshots import screen_back

    cases = []
    for n, (action, right) in enumerate(label_recorded(record)):
        if not right or not action.get("screen") or not action.get("fresh", True):
            continue
        tool, index = action.get("tool"), action.get("target_index")
        if tool in ("click", "set_text") and isinstance(index, int):
            key = ("click" if tool == "click" else "type", index)
        elif tool == "scroll" and action.get("text") == "down":
            key = (SCROLL, -1)
        else:
            continue
        screen = screen_back(action["screen"])
        cases.append(Case(f"{name}#{n}", screen.app or "recorded", record.get("task", ""), screen, frozenset({key}), "recorded"))
    if record.get("done") and record.get("final_screen"):
        screen = screen_back(record["final_screen"])
        cases.append(
            Case(f"{name}#end", screen.app or "recorded", record.get("task", ""), screen, frozenset({(DONE, -1)}), "recorded")
        )
    return cases


def load_recorded(folder: Path | str) -> list[Case]:
    """Every case in a folder of snapshot files (karta.harness.snapshots); unreadable files are skipped."""
    cases = []
    for path in sorted(Path(folder).expanduser().glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        cases.extend(recorded_cases(record, path.stem))
    return cases
