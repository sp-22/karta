"""The task's progress model (docs/harness.md, "Progress: the state graph"): one graph per task of which
action was tried on which screen and where it led, so the loop never walks into the same dead end twice.

- **A node** is a screen's state (`state_of`): its place (app, profile, web address without its fragment, else the
  window title) and its visible structure (each item's kind and name, a field empty or not, a switch on or off, a
  control enabled or not, the dialogs open). Digits fold to one mark, so a clock, a counter or a badge ticking is the
  same state; the focus, field values, busy flags, the window's position, menu commands, browser chrome and text read
  only off the pixels are left out. (`Screen.fingerprint` is not used: it holds every value and the focus, so a clock
  tick or a moved cursor is a new screen to it; it stays the "did anything change" test of the waits.)
- **An edge** is an action tried on a state and the state it led to. **An action** (`action_of`) is the tool, the
  item it acted on (`view.identity`: name, kind, AX roles, which of its namesakes; the item a blind step's pick
  resolved to, else the step's numbered item) or, before a blind step resolves, its target's words (a kind word counts
  as its family: "box" is "field"), and its text (what it types, finds, reads or tells from).
- **Rules** (the loop and the local repair keep them): an action that missed (a dead end) on a state is never run
  again on that state, whether a plan step, a repair's rung or a look's step (a step that worked is a known path and
  may be walked again: a known path, not a dead end); a look's or rescue's proposal that opens with such an action is refused
  with no brain call, and the next look is told what was tried on this screen (`here`); a look that was told and
  still proposes nothing untried means nothing untried is left: the task stops with the list (`exhausted`).
- **Progress** is reaching a state not seen before in this task, or a step's expectation met (`record`). `STILL`
  steps in a row with neither stop the task (the loop's "no progress" rule).

Shown to looks and rescues by the target's name (a content item's long name as its kind and word count), never the
text typed or read.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from karta.harness import facts as facts_mod
from karta.harness import view as view_mod
from karta.harness.types import Item, Outcome, Screen, ToolResult

STILL = 4  # steps tried in a row with no progress (no new state, no expectation met): the task stops
NAME_CHARS = 60  # an item's name, as the state keeps it
SHOWN = 8  # edges shown per state to a look
SHAPE_WORDS = 4  # a content item's name longer than this is shown as its kind and word count
CONTENT = ("row", "link", "text", "heading", "image")  # named by what they hold: may be the user's
FILLER = {"the", "a", "an", "-", "this", "that", "my"}
KIND_WORDS = {  # a kind word says which family of control, not which one: "box" and "field" are one, "icon" is not
    **dict.fromkeys(("field", "box", "input", "area", "textbox"), "(a field)"),
    **dict.fromkeys(("button", "icon", "link", "control", "item"), "(a control)"),
}
DIGITS = re.compile(r"\d+")
AIMED = ("click", "write", "find", "answer")  # a target's words or item, and the text typed or found
NOT_TEXT = ("tool", "expects", "expect", "then", "at", "slot")  # how a step is checked or named, not what it does


def _fold(text: str, cut: int = NAME_CHARS) -> str:
    return DIGITS.sub("#", " ".join(str(text or "").lower().split()))[:cut]


def state_of(scr: Screen) -> str:
    """The screen's state as a node of the graph: its place and its visible structure, digits folded; the focus,
    values, chrome, menu commands and pixels-only text left out (module doc)."""
    place = [scr.app, scr.profile, scr.url.split("#", 1)[0].rstrip("/")] if scr.url else [scr.app, _fold(scr.window)]
    shown = sorted(
        {
            (
                i.kind,
                _fold(i.name),
                bool(i.value) if i.kind == "field" else None,
                i.checked,
                i.enabled,
                i.dialog,
            )
            for i in scr.items
            if not (i.offscreen or i.menu or i.chrome or i.source == "ocr")
        },
        key=lambda k: json.dumps(k, default=str),
    )
    key = [place, facts_mod.dialogs(scr), shown]
    return hashlib.sha1(json.dumps(key, default=str).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Action:
    """What was done: the tool, the item it acted on (its identity) or, for a blind step not yet resolved, its target's
    words, and its text."""

    tool: str
    item: tuple = ()
    words: frozenset = frozenset()
    text: str = ""

    def same(self, other: Action) -> bool:
        """The same action: one tool, one text, and the same item (both resolved), else the same target in other words
        (one's words within the other's). A numbered item and a blind target never compare: a look aiming at an item
        it saw is a new approach, whatever the blind step was called."""
        if self.tool != other.tool or self.text != other.text:
            return False
        if self.item and other.item:
            return self.item == other.item
        if self.words and other.words:
            return _alike(self.words, other.words)
        return not (self.item or other.item or self.words or other.words)  # no target: a submit, a scroll, a tell


def _alike(a: frozenset, b: frozenset) -> bool:
    """Two blind targets name one thing: the same words but for a kind word ("To recipients", "the To recipients box"),
    and no two kinds that differ ("the search icon" is not "the search box")."""
    families = set(KIND_WORDS.values())
    kinds_a, kinds_b = a & families, b & families
    return a - families == b - families and (not kinds_a or not kinds_b or kinds_a == kinds_b)


def item_key(at: dict | None) -> tuple:
    """A numbered item's identity (`view.identity`), as an action keeps it: its name, kind, roles, which namesake."""
    if not isinstance(at, dict) or not at.get("name"):
        return ()
    return (_fold(at["name"]), at.get("kind", ""), at.get("roles", ""), at.get("nth", 0))


def identity(item: Item | None, scr: Screen | None) -> tuple:
    """The identity of an item a step resolved to (the control a click pressed, the field a write typed into)."""
    if item is None:
        return ()
    return item_key(view_mod.identity(item, scr) if scr is not None else {"name": item.name, "kind": item.kind})


def _words(text: str) -> frozenset:
    found = re.findall(r"[a-z0-9@.'-]+", " ".join(str(text or "").lower().split()))
    return frozenset({KIND_WORDS.get(w, w) for w in found} - FILLER)


def aim_of(step: dict) -> str:
    """What a blind step acts on, as the plan said it: its target, field or fields, a find's meaning, an answer's."""
    return str(
        {
            "click": step.get("target"),
            "write": step.get("where"),
            "fill": " ".join(map(str, step.get("fields") or {})),
            "find": step.get("meaning"),
            "answer": step.get("choice_meaning"),
            "open_place": " ".join(str(step.get(k) or "") for k in ("what", "site")),
        }.get(step.get("tool"), "")
        or ""
    )


def text_of(step: dict, told: str = "") -> str:
    """The step's text: what a step aimed at a target types or finds (a fill's values, an answer's choice), and for
    any other tool its arguments as the plan gave them (what a collect reads, a compose writes, the keys, the
    direction); a tell's is its question and what it tells from (`told`), so telling again from new items is a new
    action."""
    tool = step.get("tool")
    if tool == "tell":
        return f"{_plain(step.get('question'))} {told}"
    if tool == "fill":
        return " ".join(_plain(v) for v in (step.get("fields") or {}).values())
    if tool in AIMED:
        return _plain(step.get("text"))
    args = {k: v for k, v in step.items() if k not in NOT_TEXT}
    return _plain(json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))


def _plain(text) -> str:
    return " ".join(str(text or "").split()).lower()


def action_of(step: dict, item: tuple = (), told: str = "", words: bool = True) -> Action:
    """A step as an action: its numbered item (`at`) or the item it resolved to, and a blind step's target words (not
    for one candidate pressed by itself: `words` False)."""
    at = step.get("at") if isinstance(step.get("at"), dict) else None
    own = item or item_key(at)
    words = frozenset() if at or not words else _words(aim_of(step))
    return Action(str(step.get("tool")), own, words, text_of(step, told))


@dataclass
class Edge:
    action: Action
    label: str  # by the target's name, never the text typed or read
    led: str  # the state it led to
    ok: bool
    verdict: str


@dataclass
class Progress:
    """The task's state graph: the states seen, the edges tried from each, and the steps since the last progress."""

    edges: dict[str, list[Edge]] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    still: int = 0  # steps tried since the last progress
    told: set[str] = field(default_factory=set)  # the states a look was shown the tried list of

    def visit(self, scr: Screen) -> bool:
        """The state is seen; True when it was never seen before (progress)."""
        state = state_of(scr)
        new = state not in self.seen
        self.seen.add(state)
        return new

    def tried(self, scr: Screen, step: dict, item: tuple = (), told: str = "", words: bool = True) -> Edge | None:
        """The dead end `step` would repeat on this screen: the same action missed here already. None when it did not
        (a step that worked is a known path, walked again freely; a wait is never a repeat). `words` False: `item`
        alone is the action (a candidate the local repair would press), never a blind step's words."""
        if step.get("tool") in ("wait", "look"):
            return None
        act = action_of(step, item, told, words)
        for edge in self.edges.get(state_of(scr), []):
            if not edge.ok and edge.action.same(act):
                return edge
        return None

    def record(self, before: Screen, step: dict, after: Screen, result: ToolResult, item: tuple = (), told: str = "") -> bool:
        """An action ran: its edge from the state before it, and whether that was progress (a state never seen
        before, or its expectation met)."""
        state = state_of(before)
        self.seen.add(state)
        led = state_of(after)
        new = led not in self.seen
        self.seen.add(led)
        act = action_of(step, item, told)
        mine = self.edges.setdefault(state, [])
        old = next((e for e in mine if e.action == act), None)
        if old is not None:  # a path that worked, walked again: its last outcome stands
            old.ok, old.led, old.verdict = result.ok, led, verdict_of(result, led != state)
        else:
            mine.append(Edge(act, label_of(step, item, before), led, result.ok, verdict_of(result, led != state)))
        return new or result.ok

    def attempt(self) -> None:
        """The loop tries a step: one more towards STILL, until progress (`moved`)."""
        self.still += 1

    def moved(self) -> None:
        """Progress: a step's expectation met, or a state never seen before reached."""
        self.still = 0

    def stalled(self) -> bool:
        return self.still >= STILL

    def here(self, scr: Screen) -> list[str]:
        """What was tried on this screen and what it did, by the targets' names: the "tried on this screen" block."""
        return [f"{e.label} -> {e.verdict}" for e in self.edges.get(state_of(scr), [])[-SHOWN:]]

    def block(self, scr: Screen) -> str:
        """The block a look or rescue gets: what was tried on this screen; "" when nothing was. The look is marked told,
        so a proposal of nothing untried after it means nothing untried is left."""
        lines = self.here(scr)
        if not lines:
            return ""
        self.told.add(state_of(scr))
        return "\nAlready tried on this screen (do not send these again; try another item, path or place):" + "".join(
            f"\n- {line}" for line in lines
        )

    def was_told(self, scr: Screen) -> bool:
        return state_of(scr) in self.told

    def exhausted(self, scr: Screen) -> str:
        """Why nothing untried is left on this screen, in one short clause: "I tried 'A', 'B' and 'C' on this page and
        none worked" (the last three that missed, a click by its target's name, another tool with its verb)."""
        names = list(dict.fromkeys(_said(e.label) for e in self.edges.get(state_of(scr), []) if not e.ok))[-3:]
        if not names:
            return "nothing I tried here moved it forward"
        listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        return f"I tried {listed} on this page and none worked"


def _said(label: str) -> str:
    """A label as a person says it, short: "'Focus'" for a click on it, "typing into 'To'", "answering from what I
    found"."""
    verb, _, rest = label.partition(" ")
    if verb in ("tell", "collect"):  # a slot's name is the plan's word, not the user's
        return "answering from what I found" if verb == "tell" else "reading the page"
    rest = " ".join(re.sub(r" \((?:[a-z ]+)\)$", "", rest).split()[:3])
    if rest.startswith("'") and not rest.endswith("'"):
        rest += "…'"
    ing = {"write": "typing into", "fill": "filling", "find": "searching", "submit": "sending", "answer": "answering",
           "collect": "reading", "tell": "answering from", "scroll": "scrolling", "open_place": "opening",
           "type": "typing", "back": "going back", "compose": "writing"}  # fmt: skip
    return rest if verb == "click" and rest else f"{ing.get(verb, verb)} {rest}".strip()


def _shown(name: str, kind: str) -> str:
    words = " ".join(str(name or "").split())
    if kind in CONTENT and len(words.split()) > SHAPE_WORDS:
        return f"a {kind} [{len(words.split())} words]"
    return repr(words[:48])


def label_of(step: dict, item: tuple, scr: Screen | None) -> str:
    """The edge's label, by the target's name (as resolved, else as the plan named it), never the text typed or read."""
    tool = str(step.get("tool"))
    at = step.get("at") if isinstance(step.get("at"), dict) else None
    if item:
        name, kind = next(((i.name, i.kind) for i in (scr.items if scr else []) if _fold(i.name) == item[0]), (item[0], item[1]))
        target = f"{_shown(name, kind)} ({kind})" if kind else _shown(name, "")
    elif at:
        target = f"{_shown(at['name'], at.get('kind', ''))} ({at.get('kind', 'item')})"
    else:
        aim = aim_of(step)
        target = repr(" ".join(aim.split())[:48]) if aim else ""
    if tool == "tell":
        target = f"{{{step.get('slot') or ''}}}"
    elif tool == "collect":
        target = f"as {{{step.get('slot') or ''}}}"
    elif tool == "scroll":
        target = str(step.get("direction") or "down")
    return " ".join(f"{tool} {target}".split())


def verdict_of(result: ToolResult, moved: bool) -> str:
    if result.ok:
        return "worked" + ("" if moved else " (same screen)")
    if result.outcome is Outcome.NO_CHANGE:
        return "nothing changed"
    if result.outcome is Outcome.UNEXPECTED:
        return "a different change" if moved else "not what was expected"
    return result.outcome.value


def of(tools) -> Progress | None:
    """The run's graph, when the loop gave the toolbox one (a bare Toolbox in a test has none)."""
    graph = getattr(tools, "progress", None)
    return graph if isinstance(graph, Progress) else None


def run_once(tools, step: dict, verify, item: tuple = ()) -> ToolResult:
    """The local repair's way to run a step again: refused, with no action, when it would repeat a dead end on this
    screen; else run, verified, and recorded as an edge."""
    graph = of(tools)
    before = latest(tools)
    if graph is not None:
        edge = graph.tried(before, step, item)
        if edge is not None:
            tools.trace.append(f"  not run again: {edge.label} was tried on this screen ({edge.verdict})")
            return ToolResult(Outcome.STUCK, f"{edge.label} was tried on this screen already ({edge.verdict})")
    result = verify(step, tools.run(step))
    note(tools, before, step, result)
    return result


def latest(tools) -> Screen:
    """The screen read last (the tools read after each action), else a fresh read."""
    seen = getattr(tools, "seen", None)
    scr = seen.get(getattr(tools, "front", None)) if isinstance(seen, dict) else None
    return scr if isinstance(scr, Screen) else tools.look()


def note(tools, before: Screen, step: dict, result: ToolResult, item: tuple = (), told: str = "") -> bool:
    """Record what a step just did from `before` (the item it pressed or typed into, when `item` is not given): the
    edge to the screen read last. True when that was progress."""
    graph = of(tools)
    if graph is None:
        return False
    if not item:
        item = resolved(tools, step)
    return graph.record(before, step, latest(tools), result, item, told)


def resolved(tools, step: dict) -> tuple:
    """The item the step's tool resolved its target to (the control a click pressed, the field a write typed into), else
    its numbered item."""
    press = getattr(tools, "last_press", None)
    if step.get("tool") in ("click", "answer") and press is not None:
        return identity(press.item, press.before)
    put = getattr(tools, "last_put", None)
    if step.get("tool") in ("write", "fill") and put and put.get("box") is not None:
        return identity(put["box"], put.get("ahead"))
    return item_key(step.get("at") if isinstance(step.get("at"), dict) else None)


def pressed(tools, step: dict, item: Item, how: str, verify) -> ToolResult:
    """The local repair's press of another candidate for a click: refused when that item was a dead end on this screen;
    else pressed, verified and recorded as an edge of the step."""
    target, near = str(step.get("target") or ""), str(step.get("near") or "")
    graph = of(tools)
    before = latest(tools)
    key = identity(item, before)
    if graph is not None:
        edge = graph.tried(before, step, key, words=False)
        if edge is not None:
            tools.trace.append(f"  not pressed again: {edge.label} was tried on this screen ({edge.verdict})")
            return ToolResult(Outcome.STUCK, f"{edge.label} was tried on this screen already ({edge.verdict})")
    result = verify(step, tools.press_item(target, item, how, near))
    note(tools, before, step, result, key)
    return result
