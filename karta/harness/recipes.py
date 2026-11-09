"""Pinning: the first time Karta figures a task out; after that it is pinned (docs/harness.md, "Pinning").

A recipe is the path that worked: the steps that ran and were proven, in order (the ledger's ✓ steps; a rescue's fix as
it ran; failed tries and skipped steps left out), each with its proof (the address it reached, the field it filled,
what appeared), and the task as a template. Code makes the template at save time: a value a step typed (a write, type
or fill's text, a search, a collect's count, a reach's amount) that is also a run of the task's own words becomes a
parameter {p1}, {p2}…, in the steps and in the task alike. Collected values stay {name} slots and are never stored; a
task that types into a password or code field is never pinned.

Three ways back in, all through the normal loop with every check:
- exact: the same command, normalised -> the recipe's steps with its last values, no plan call;
- named: "call that my <name>" names the last pinned task; "<name>" / "do my <name>" replays it, no plan call;
- bound: the command is a recipe's template with other values, and code binds every one from the command's own words
  (`shaped`: the template's literal words aligned against it, each value of the kind the last one was: a number by
  its format, a date or time by the platform's date detector, other text by alignment) -> replayed, no plan call;
  any doubt (two ways to align, a kind that differs, a date in a text value that had none) leaves it to the brain;
- same shape: the plan prompt lists the recipes' templates (never their values) and the brain may answer with one
  line `use: <id> | p1: <value> | …` instead of a plan; code checks the id and the values and replays.

Proof: a recipe keeps the contract its run was verified by (contract.py) and is pinned only from a run whose declared
contract was met; every replay checks that contract again, and a replay whose contract does not hold unpins the recipe.
A recipe from before contracts is dropped when the book loads (`VERSION`).

Self-heal: a step that fails on replay climbs the normal ladder (a rescue fixes that step only); once the task ends
done with its contract met, the path that ran becomes the recipe's next version (the last KEEP versions are kept). A
recipe that fails BENCH times in a row is no longer replayed or offered until a planned run of it succeeds again.

Stored in pins.json beside the memory file (memory.HarnessMemory.pins), on this Mac only, capped and aged out.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from karta.harness import expect as expect_mod
from karta.harness.brain import Plan, check_step, guessed

FILE = "pins.json"
VERSION = 2  # 2: every recipe keeps the contract it was verified by; one from before is dropped on load
MAX_RECIPES = 50
KEEP = 3  # earlier versions kept for rollback
BENCH = 2  # failures in a row: no longer replayed or offered until it succeeds again
EXPIRE_DAYS = 180  # unused this long: deleted
OFFER = 5  # templates in a plan prompt at most
MAX_VALUE = 200  # characters in a value a `use:` line binds
LITERAL = 2  # significant literal words a template needs before code binds a command to it alone
DAY = 86400.0

STOP = frozenset(  # small words: never a value on their own, trimmed off a value's ends
    re.findall(r"\w+", "a an the to of in on for and or my me with at by from it is this that these those all some any as")
    + re.findall(r"\w+", "into up out be i you your our")
)
NUMBERS = {w: str(n) for n, w in enumerate(re.findall(r"\w+", "zero one two three four five six seven eight nine ten"))}
SECRET = re.compile(r"pass(word|code|phrase)|\bpin\b|\botp\b|one[- ]time|verification|security code|\bcv[cv]\b|card number", re.I)
TOKEN = re.compile(r"[^\s,;!?()\[\]\"“”]+")
PARAM = re.compile(r"\{(p\d+)\}")
SLOT_NAME = re.compile(r"^p\d+$")
KEPT = {"tool", "slot", "form", "site", "direction", "seconds", "kind", "region", "roles", "nth", "of"}  # never
# parametrised: a step's fields, and an item's identity a page plan's #n became (view.identity: only its name may be)
CHECKED_BY_ADDRESS = {"click", "submit", "answer", "back"}  # a pinned step with no expectation is held to its address
NUMBER = re.compile(r"[$€£]?\d+(?:[.,]\d+)*%?")  # a number by its format (number words compare as digits: _word)

FILLER = {"my", "the", "a", "thing", "routine", "task", "one", "please", "again"}
NAME_IT = (
    re.compile(r"^(?:call|name) (?:that|this|it)(?: task| one)? my (?P<name>.+)$"),
    re.compile(r"^name (?:that|this|it)(?: task| one)?(?: as)? (?P<name>.+)$"),
    re.compile(r"^(?:save|pin|keep|remember) (?:that|this|it)(?: task| one)? as (?P<name>.+)$"),
)
FORGET_LAST = re.compile(r"^(?:forget|unpin) (?:that|this|it|the last one)(?: one| task| pin)?$")
FORGET_NAMED = re.compile(r"^(?:forget|unpin) (?P<name>.+)$")
RUN_NAMED = re.compile(r"^(?:(?:do|run|start|replay|redo) )?(?P<name>.+?)(?: again)?$")


# ---------------------------------------------------------------- words


def normal(text: str) -> str:
    """A command as compared: lower case, spaces collapsed, no closing punctuation, no leading "please"."""
    t = " ".join(str(text).lower().split()).strip(" .!?")
    return re.sub(r"^(?:please|karta|hey karta)[, ]+", "", t)


def name_key(name: str) -> str:
    """A routine's name as compared: its words without filler ("my morning thing" -> "morning")."""
    return " ".join(w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in FILLER)


def _word(text: str) -> str:
    low = text.lower()
    return NUMBERS.get(low, low)


def _tokens(text: str) -> list[tuple[str, int, int]]:
    """(word as compared, start, end): split on spaces and list punctuation; an address or an email stays one word."""
    out = []
    for m in TOKEN.finditer(text):
        s, e = m.start(), m.end()
        while e > s and text[e - 1] in ".:'\u2019":
            e -= 1
        while s < e and text[s] in "'\u2018":
            s += 1
        if s < e:
            out.append((_word(text[s:e]), s, e))
    return out


def _find(seq: tuple[str, ...], words: list[str] | tuple[str, ...]) -> list[int]:
    n = len(seq)
    return [i for i in range(len(words) - n + 1) if tuple(words[i : i + n]) == seq]


def _trim(run: tuple[str, ...]) -> tuple[str, ...]:
    start, end = 0, len(run)
    while start < end and run[start] in STOP:
        start += 1
    while end > start and run[end - 1] in STOP:
        end -= 1
    return run[start:end]


def _significant(run: tuple[str, ...]) -> bool:
    return any(w not in STOP and (len(w) >= 3 or any(c.isdigit() for c in w)) for w in run)


def _runs(value: str, task: list[str]) -> list[tuple[str, ...]]:
    """The longest runs of `value`'s words that are also a run of the task's words, trimmed of small words."""
    words = [w for w, _, _ in _tokens(value)]
    runs, i = [], 0
    while i < len(words):
        best = 0
        for j in range(len(task)):
            k = 0
            while i + k < len(words) and j + k < len(task) and words[i + k] == task[j + k]:
                k += 1
            best = max(best, k)
        if best:
            run = _trim(tuple(words[i : i + best]))
            if run and _significant(run):
                runs.append(run)
            i += best
        else:
            i += 1
    return runs


def _atoms(runs: list[tuple[str, ...]]) -> set[tuple[str, ...]]:
    """The finest values: a run holding another run splits around it ("2 sourdough starter" around "sourdough starter"
    and "2"); what is left over stays a value only when it still means something."""
    found = set(runs)
    while True:
        pair = next(
            ((a, b) for a in sorted(found, key=len, reverse=True) for b in found if len(b) < len(a) and _find(b, a)),
            None,
        )
        if pair is None:
            return found
        a, b = pair
        found.discard(a)
        piece: list[str] = []
        i = 0
        while i <= len(a):
            if i == len(a) or tuple(a[i : i + len(b)]) == b:
                left = _trim(tuple(piece))
                if left and _significant(left):
                    found.add(left)
                piece = []
                i += len(b) if i < len(a) else 1
            else:
                piece.append(a[i])
                i += 1


def _splice(text: str, params: list[tuple[str, tuple[str, ...]]]) -> str:
    """`text` with each word run of a value replaced by its {name}, longest values first, never overlapping."""
    toks = _tokens(text)
    words = [w for w, _, _ in toks]
    taken: set[int] = set()
    cuts: list[tuple[int, int, str]] = []
    for name, seq in sorted(params, key=lambda p: -len(p[1])):
        for i in _find(seq, words):
            span = set(range(i, i + len(seq)))
            if span & taken:
                continue
            taken |= span
            cuts.append((toks[i][1], toks[i + len(seq) - 1][2], name))
    for start, end, name in sorted(cuts, reverse=True):
        text = text[:start] + "{" + name + "}" + text[end:]
    return text


def _creating(step: dict) -> list[str]:
    """The values a step types or counts: these become parameters when the task holds them."""
    tool = step.get("tool")
    if tool in ("write", "type", "find"):
        return [str(step.get("text") or "")]
    if tool == "fill":
        return [v for v in step.get("fields", {}).values() if isinstance(v, str)]
    if tool == "reach":
        return [str(step.get("amount") or ""), str(step.get("of") or "")]
    if tool == "collect" and isinstance(step.get("count"), int):
        return [str(step["count"])]
    return []


def _abstract(value, params: list[tuple[str, tuple[str, ...]]], key: str = ""):
    if key in KEPT:
        return value
    if isinstance(value, str):
        return _splice(value, params)
    if key == "count" and isinstance(value, int):
        s = _splice(str(value), params)
        return s if PARAM.fullmatch(s) else value
    if isinstance(value, list):
        return [_abstract(v, params) for v in value]
    if isinstance(value, dict):
        return {k: _abstract(v, params, k) for k, v in value.items()}
    return value


def abstract(step: dict, params: list[tuple[str, tuple[str, ...]]]) -> dict:
    """A step (or a proof) with the values replaced by their {pN}. A find whose text became a value keeps only the
    alternatives that are values too: spelling variants of one value are wrong for another."""
    out = _abstract(step, params)
    if out.get("tool") == "find" and PARAM.search(str(out.get("text") or "")):
        out["alternatives"] = [a for a in out.get("alternatives", []) if PARAM.search(a)]
    return out


def _bind(value, values: dict[str, str], key: str = ""):
    if isinstance(value, str):
        bound = PARAM.sub(lambda m: values.get(m[1], m[0]), value)
        if key == "count":
            word = _word(bound.strip())
            return int(word) if word.isdigit() else bound
        return bound
    if isinstance(value, list):
        return [_bind(v, values) for v in value]
    if isinstance(value, dict):
        return {k: _bind(v, values, k) for k, v in value.items()}
    return value


def fill(text: str, values: dict[str, str]) -> str:
    return _bind(text, values)


def whole(steps: list[dict]) -> bool:
    """Every submit has something written before it (write, type, fill or a collect it sends), so a replay never
    presses return or send into an empty box because the run that pinned it got the text there some other way."""
    wrote = False
    for step in steps:
        tool = step.get("tool")
        if tool in ("write", "type", "fill"):
            wrote = True
        elif tool == "submit":
            if not wrote:
                return False
            wrote = False
        elif tool == "open_place":
            wrote = False
    return True


def secret(steps: list[dict]) -> bool:
    """A step that types into a password or code field anything but a collected {name}: such a task is never pinned."""
    for step in steps:
        pairs = []
        if step.get("tool") == "write":
            pairs = [(str(step.get("where") or ""), str(step.get("text") or ""))]
        elif step.get("tool") == "fill":
            pairs = [(k, str(v)) for k, v in step.get("fields", {}).items()]
        for where, text in pairs:
            if SECRET.search(where) and re.sub(r"\{[A-Za-z][A-Za-z0-9_]*\}", "", text).strip():
                return True
    return False


def extract(task: str, steps: list[dict]) -> tuple[str, list[tuple[str, tuple[str, ...]]], dict[str, str]]:
    """The task's template, its parameters (name, words) in task order, and their values in this task."""
    toks = _tokens(task)
    words = [w for w, _, _ in toks]
    runs = [run for step in steps for value in _creating(step) for run in _runs(value, words)]
    taken: set[int] = set()
    placed: list[tuple[int, tuple[str, ...]]] = []
    for seq in sorted(_atoms(runs), key=lambda s: (-len(s), s)):
        free = [i for i in _find(seq, words) if not set(range(i, i + len(seq))) & taken]
        if free:
            taken |= set(range(free[0], free[0] + len(seq)))
            placed.append((free[0], seq))
    placed.sort()
    params = [(f"p{n}", seq) for n, (_, seq) in enumerate(placed, 1)]
    values = {name: task[toks[i][1] : toks[i + len(seq) - 1][2]] for (name, seq), (i, _) in zip(params, placed, strict=True)}
    return _splice(task, params), params, values


def typed_text(text: str, template: str) -> str:
    """A v2 expectation as a recipe keeps it: its words only when each is a value ({pN}) or the task's own; any other
    word (a date the brain worked out, a name it read that run) is dropped where the type stands without words, so a
    replay on another day never looks for that run's words. Not an expectation: kept as it is (v1 words)."""
    got = expect_mod.parse(text)[0]
    if got is None or not got.arg or expect_mod.TYPES[got.kind] is not None:
        return text
    return str(got) if not guessed(PARAM.sub(" ", got.arg), template) else got.kind


def typed(step: dict, template: str) -> dict:
    if not step.get("expects"):
        return step
    kept = typed_text(step["expects"], template)
    out = {**step, "expects": kept}
    if step.get("tool") == "submit":  # a typed submit's words are its expectation's own
        out["expect"] = expect_mod.parse(kept)[0].arg
    return out


ACTS = ("click", "answer", "submit")  # the steps that cause a change: each declares its kind under the typed format
TYPED_TEXT = (("write", "text"), ("type", "text"), ("find", "text"))


def is_typed(steps: list[dict], done_shows: str) -> bool:
    """A recipe in the typed format: every step that causes a change declares its kind (`expects`), none keeps `then:`
    words, and the done line, when there is one, is a type. One in the old words format (v1) proves nothing on
    replay, so it is never replayed, offered or pinned."""
    if any("then" in s or (s.get("tool") in ACTS and not s.get("expects")) for s in steps):
        return False
    return not done_shows or expect_mod.parse(done_shows)[0] is not None


def typed_words(steps: list[dict]) -> set[str]:
    """The literal words a recipe's steps type (a write, type or find's text, a fill's values), its {pN} values and
    {name} slots left out."""
    texts = [str(s.get(key) or "") for s in steps for tool, key in TYPED_TEXT if s.get("tool") == tool]
    texts += [str(v) for s in steps if s.get("tool") == "fill" for v in (s.get("fields") or {}).values()]
    return {w for t in texts for w, _, _ in _tokens(re.sub(r"\{[^{}]*\}", " ", t)) if re.search(r"\w", w)}


def _address(url: str | None) -> str:
    """host/path, without the scheme, www, the query or a closing slash."""
    if not url:
        return ""
    bare = re.sub(r"^[a-z]+://(www\.)?", "", url.strip().lower())
    return re.split(r"[?#]", bare, maxsplit=1)[0].rstrip("/")


def proof(step: dict, before_url: str | None = None, after_url: str | None = None, reached: bool = False) -> dict:
    """What showed a step worked: its kind of change, the address it reached, the field it filled (never words, never
    collected data)."""
    tool = step.get("tool")
    if tool == "collect":
        return {"kind": "collected", "as": step.get("slot", "")}
    if tool == "tell":
        return {"kind": "answered"}
    if step.get("expects"):  # the kind of change it caused, never words
        return {"kind": "reached" if reached else "expect", "type": step["expects"]}
    at, was = _address(after_url), _address(before_url)
    if at and at != was:
        return {"kind": "address", "at": at}
    if tool in ("write", "type", "fill"):
        return {"kind": "field", "where": step.get("where") or ", ".join(step.get("fields", {})) or "the focus"}
    return {"kind": "ran"}


# ---------------------------------------------------------------- binding a command's own words (no brain)


def date_spans(text: str) -> list[tuple[int, int]] | None:
    """Where the platform's date detector (NSDataDetector: the user's calendar, language and time zone) finds a date or
    time in `text`, as (start, end); None off a Mac."""
    try:
        from Foundation import NSDataDetector, NSTextCheckingTypeDate

        detector, _ = NSDataDetector.dataDetectorWithTypes_error_(NSTextCheckingTypeDate, None)
        found = detector.matchesInString_options_range_(text, 0, (0, len(text)))
    except Exception:
        return None
    return [(m.range().location, m.range().location + m.range().length) for m in found or []]


def kind_of(value: str) -> tuple[str, bool]:
    """A value's kind (number by its format, date when the detector's dates cover every word that is not a small one,
    else text) and whether it holds a date at all."""
    toks = _tokens(value)
    if toks and all(NUMBER.fullmatch(w) for w, _, _ in toks):
        return "number", False
    spans = date_spans(value) or []
    inside = [any(a <= s and e <= b for a, b in spans) for _, s, e in toks]
    whole = bool(spans) and all(hit or w in STOP for (w, _, _), hit in zip(toks, inside, strict=True))
    return ("date" if whole else "text"), bool(spans)


def _parts(template: str) -> list[tuple[str, str]]:
    """A template as ("word", w) and ("slot", pN) in order."""
    out: list[tuple[str, str]] = []
    for n, piece in enumerate(PARAM.split(template)):
        out += [("slot", piece)] if n % 2 else [("word", w) for w, _, _ in _tokens(piece)]
    return out


def _align(parts: list[tuple[str, str]], words: list[str], limit: int = 64) -> list[dict[str, tuple[int, int]]]:
    """Every way (at most `limit`) the template's words match the command's in order with each slot taking one word
    or more: slot -> (first word, past its last)."""
    found: list[dict[str, tuple[int, int]]] = []

    def go(i: int, j: int, got: dict[str, tuple[int, int]]) -> None:
        if len(found) >= limit:
            return
        if i == len(parts):
            if j == len(words):
                found.append(dict(got))
            return
        what, text = parts[i]
        if what == "word":
            if j < len(words) and words[j] == text:
                go(i + 1, j + 1, got)
            return
        for end in range(j + 1, len(words) + 1):
            go(i + 1, end, {**got, text: (j, end)})

    go(0, 0, {})
    return found


def shaped(recipe: Recipe, task: str) -> dict[str, str] | None:
    """The values of `task` when it is the recipe's template with other values and code can bind every one alone: one
    way to align the template's literal words (at least LITERAL significant ones) with the task's, each value of the
    kind the recipe's last value was (a number, a date or time, text: the kinds choose among the ways to align, and
    two ways left is a doubt), no date in a text value that held none, and
    no value the same as the last run's all through (that is the exact command). None otherwise: the brain's turn."""
    parts = _parts(normal(recipe.template))
    if sum(w not in STOP and _significant((w,)) for what, w in parts if what == "word") < LITERAL:
        return None
    command = re.sub(r"^(?:please|karta|hey karta)[, ]+", "", " ".join(str(task).split()).strip(" .!?"), flags=re.I)
    toks = _tokens(command)
    ways = _align(parts, [w for w, _, _ in toks])
    fits = []
    for way in ways:
        values = {p: command[toks[a][1] : toks[b - 1][2]] for p, (a, b) in way.items()}
        if set(values) != set(recipe.params):
            continue
        fine = True
        for p, value in values.items():
            new, old = kind_of(value), kind_of(str(recipe.values.get(p, "")))
            if new[0] != old[0] or (new[0] == "text" and new[1] and not old[1]):
                fine = False
            if len(value) > MAX_VALUE or "{" in value or "}" in value:
                fine = False
        if fine:
            fits.append(values)
    if len(fits) != 1:  # none, or more than one way of the right kinds: not code's to choose
        return None
    same = all(normal(v) == normal(str(recipe.values.get(p, ""))) for p, v in fits[0].items())
    return None if same else fits[0]


# ---------------------------------------------------------------- recipes and the book


@dataclass
class Recipe:
    id: str
    template: str  # the task with its values as {p1}, {p2}…
    params: list[str]
    values: dict[str, str]  # the last proven run's values: an exact or named replay types these
    steps: list[dict]  # tool calls, values as {pN}, collected data as {name}
    proofs: list[dict]  # per step: what showed it worked
    done_shows: str = ""
    contract: str = ""  # what the run achieved, checked by code (contract.py), values as {pN}: every replay checks it
    name: str = ""  # the routine's name, when the user gave one
    version: int = 1
    history: list[dict] = field(default_factory=list)  # earlier versions, newest first: version, steps, proofs, done_shows
    fails: int = 0  # replays in a row that ended not done
    uses: int = 1
    created: float = 0.0
    used: float = 0.0

    def command(self, values: dict[str, str] | None = None) -> str:
        return fill(self.template, self.values if values is None else values)

    @property
    def benched(self) -> bool:
        return self.fails >= BENCH


FIELDS = {f.name for f in fields(Recipe)}


@dataclass
class Hit:
    """A recipe bound to values, ready to replay."""

    recipe: Recipe
    values: dict[str, str]
    how: str  # exact | named | same shape
    steps: list[dict]
    done_shows: str
    checks: list[str]  # per step: the address it must reach ("" none), for an exact or named replay
    contract: str = ""  # the recipe's contract with these values: the replay is checked by it

    @property
    def command(self) -> str:
        return self.recipe.command(self.values)


class Replay:
    """A pinned path in place of the brain's streamed plan (loop.run_task reads `plan`, iterates the steps, closes)."""

    def __init__(self, hit: Hit):
        self.hit = hit
        self.plan = Plan(list(hit.steps), done_shows=hit.done_shows, contract=hit.contract)
        self.plan.pinned = hit
        self._it = iter(self.plan.steps)

    def __iter__(self):
        return self._it

    def close(self) -> None:
        pass


def bind(recipe: Recipe, values: dict[str, str], how: str) -> tuple[Hit | None, str | None]:
    """The recipe's steps with `values`, each checked as a plan line is; or why they cannot be."""
    steps = []
    for n, raw in enumerate(recipe.steps, 1):
        step, error = check_step(_bind(raw, values))
        if error:
            return None, f"use: {recipe.id} step {n}: {error}"
        if PARAM.search(json.dumps(step)):
            return None, f"use: {recipe.id} step {n} has a value with no binding"
        steps.append(step)
    checks = []
    for step, pr in zip(steps, recipe.proofs + [{}] * len(steps), strict=False):
        at = fill(str(pr.get("at") or ""), values) if pr.get("kind") == "address" else ""
        usable = how in ("exact", "named") and step["tool"] in CHECKED_BY_ADDRESS and not step.get("expects")
        checks.append(at if usable and at and not re.search(r"\d", at) else "")  # never for values code or the brain bound
    return Hit(recipe, dict(values), how, steps, fill(recipe.done_shows, values), checks, fill(recipe.contract, values)), None


class RecipeBook:
    """The pinned tasks, in a JSON file (or in RAM with no path). Every change is saved at once."""

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.path = Path(path) if path else None
        self.clock = clock
        self.recipes: list[Recipe] = []
        self.last = ""  # the recipe pinned or replayed last: "that" in "call that…", "forget that"
        self.next = 1
        self.typed = True  # runs plan in the typed format (v2): a recipe in the old words format is refused
        self.dropped = 0  # recipes from before contracts dropped on load (VERSION)
        self._load()

    # ------------------------------------------------------------ the file

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return  # an unreadable file starts empty (it is replaced on the next save)
        for raw in data.get("recipes", []) if isinstance(data, dict) else []:
            try:
                self.recipes.append(Recipe(**{k: v for k, v in raw.items() if k in FIELDS}))
            except TypeError:
                continue
        self.last = str(data.get("last") or "")
        self.next = max([int(data.get("next") or 1)] + [int(r.id[1:]) + 1 for r in self.recipes if r.id[1:].isdigit()])
        if int(data.get("version") or 1) < VERSION:  # once: a recipe with no contract was never verified as one
            kept = [r for r in self.recipes if r.contract]
            self.dropped = len(self.recipes) - len(kept)
            self.recipes = kept
            self._tidy()
            self.save()
            return
        self._tidy()

    def _tidy(self) -> None:
        now = self.clock()
        self.recipes = [r for r in self.recipes if now - (r.used or r.created) < EXPIRE_DAYS * DAY]
        if len(self.recipes) > MAX_RECIPES:  # named routines last, then the least recently used
            keep = sorted(self.recipes, key=lambda r: (bool(r.name), r.used), reverse=True)[:MAX_RECIPES]
            self.recipes = [r for r in self.recipes if r in keep]
        if self.get(self.last) is None:
            self.last = ""

    def save(self) -> None:
        self._tidy()
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"version": VERSION, "last": self.last, "next": self.next, "recipes": [asdict(r) for r in self.recipes]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    # ------------------------------------------------------------ looking up

    def get(self, rid: str) -> Recipe | None:
        return next((r for r in self.recipes if r.id == rid), None)

    def usable(self, recipe: Recipe) -> bool:
        """Replayed, offered or bound only with a contract to check it by, and in the format the run plans in (under
        v2, typed recipes only)."""
        return bool(recipe.contract) and (not self.typed or is_typed(recipe.steps, recipe.done_shows))

    def named(self, name: str) -> Recipe | None:
        key = name_key(name)
        return next((r for r in self.recipes if key and r.name and name_key(r.name) == key), None)

    def match(self, task: str) -> Hit | None:
        """The same command (normalised) as a pinned one, or a routine's name: a replay with its last values. A benched
        recipe is still found (its command is planned afresh). Else a recipe's template with other values that code
        binds alone (`bound`)."""
        key = normal(task)
        recent = sorted(self.recipes, key=lambda r: -r.used)
        found = next((r for r in recent if normal(r.command()) == key), None)
        how = "exact"
        if found is None:
            m = RUN_NAMED.match(key)
            found, how = (self.named(m["name"]) if m else None), "named"
        if found is None:
            return self.bound(task)
        if not self.usable(found):
            return None
        hit, _ = bind(found, found.values, how)
        return hit

    def bound(self, task: str) -> Hit | None:
        """The command is one pinned recipe's template with other values, bound by code from its own words (`shaped`):
        a replay with those values and no plan call. Never when two recipes fit, a value would come from anything but
        the command, the command speaks of a secret, or the recipe would type a word of the task it was pinned from
        that this one does not say; then the brain plans (and may answer `use:`)."""
        if SECRET.search(task):
            return None
        said = {w for w, _, _ in _tokens(normal(task))}
        hits = []
        for r in self.recipes:
            if not r.params or r.benched or not self.usable(r):
                continue
            values = shaped(r, task)
            if values is None:
                continue
            teacher = {w for w, _, _ in _tokens(normal(r.command()))}
            if (typed_words(r.steps) & teacher) - said:
                continue
            hit, _ = bind(r, values, "bound")
            if hit is not None:
                hits.append(hit)
        return hits[0] if len(hits) == 1 else None

    def offer(self, task: str) -> list[tuple[str, str]]:
        """(id, template) of the recipes a plan prompt lists: with values, not benched, sharing a word with the task."""
        words = {w for w, _, _ in _tokens(normal(task))} - STOP
        scored = []
        for r in self.recipes:
            if not r.params or r.benched or not self.usable(r):
                continue
            literal = {w for w, _, _ in _tokens(PARAM.sub(" ", normal(r.template)))} - STOP
            overlap = len(words & literal)
            if overlap:
                scored.append((-overlap, -r.used, r.id, r.template))
        return [(rid, template) for _, _, rid, template in sorted(scored)[:OFFER]]

    def bind_use(self, text: str, task: str = "") -> tuple[Hit | None, str | None]:
        """The brain's `use: <id> | p1: <value> | …` line, checked: a known recipe that is offered, every value given
        once, nothing else, each value one short line; and, given the new `task`, no literal word the recipe types
        that comes from the task it was pinned from and that the new task does not say (a stray word of that run's
        values; the plan's own words, "Worth a look:", stay)."""
        parts = [p.strip() for p in str(text).split("|")]
        rid = parts[0].strip().strip("\"'").lower()
        recipe = self.get(rid)
        if recipe is None or recipe.benched or not recipe.params or not self.usable(recipe):
            known = ", ".join(r.id for r in self.recipes if r.params and not r.benched) or "none"
            return None, f"use: no pinned task {rid!r} to use (pinned: {known}); plan the task instead"
        values: dict[str, str] = {}
        for part in parts[1:]:
            key, sep, value = part.partition(":")
            key, value = key.strip().lower(), value.strip().strip("\"'").strip()
            if not sep or key not in recipe.params:
                return None, f"use: {rid} takes {', '.join(recipe.params)}, not {part!r}"
            if key in values:
                return None, f"use: {key} is given twice"
            if not value or len(value) > MAX_VALUE or "\n" in value or "{" in value or "}" in value:
                return None, f"use: {key} needs one short value, not {value[:40]!r}"
            values[key] = value
        missing = [p for p in recipe.params if p not in values]
        if missing:
            return None, f"use: {rid} needs a value for {', '.join(missing)}"
        teacher = {w for w, _, _ in _tokens(normal(recipe.command()))}
        said = {w for w, _, _ in _tokens(normal(task))}
        stray = sorted((typed_words(recipe.steps) & teacher) - said) if task else []
        if stray:  # the recipe would type words of the task it was pinned from that this task never says
            return None, f"use: {rid} would type {', '.join(stray[:4])!r}, which this task does not say; plan the task instead"
        return bind(recipe, values, "same shape")

    # ------------------------------------------------------------ naming and forgetting

    def command(self, task: str) -> tuple[bool, str] | None:
        """A pin command, handled without the Mac or the brain: (done, what was done), or None for an ordinary task.
        "call that my <name>" / "name it <name>" / "save this as <name>" names the last pinned task; "forget that"
        removes it; "forget <name>" removes that routine (only when a routine has that name)."""
        t = normal(task)
        if FORGET_LAST.match(t):
            recipe = self.get(self.last)
            if recipe is None:
                return False, "nothing is pinned to forget"
            self.recipes.remove(recipe)
            self.last = ""
            self.save()
            return True, f"forgot the pinned task {recipe.template!r}"
        m = FORGET_NAMED.match(t)
        recipe = self.named(m["name"]) if m else None
        if recipe is not None:
            self.recipes.remove(recipe)
            self.save()
            return True, f"forgot {recipe.name!r}"
        for rx in NAME_IT:
            m = rx.match(t)
            if not m:
                continue
            name = re.sub(r"^(?:my|the|a) ", "", m["name"]).strip()
            if not name_key(name):
                return False, "a routine needs a name of a word or two"
            recipe = self.get(self.last)
            if recipe is None:
                return False, "nothing is pinned yet to name: do the task once first"
            for other in self.recipes:
                if other is not recipe and other.name and name_key(other.name) == name_key(name):
                    other.name = ""  # a name means one routine
            recipe.name = name
            self.save()
            return True, f"named the pinned task {recipe.template!r} {name!r}: say {name!r} to run it"
        return None

    def forget(self, which: str) -> Recipe | None:
        """Remove a pinned task by its id ("r3") or its routine's name; the one removed, or None."""
        recipe = self.get(which.strip()) or self.named(which)
        if recipe is None:
            return None
        self.recipes.remove(recipe)
        if self.last == recipe.id:
            self.last = ""
        self.save()
        return recipe

    def rollback(self, rid: str) -> bool:
        """Back to the recipe's previous version (the current one is dropped)."""
        recipe = self.get(rid)
        if recipe is None or not recipe.history:
            return False
        old = recipe.history.pop(0)
        recipe.steps, recipe.proofs, recipe.done_shows = old["steps"], old["proofs"], old.get("done_shows", "")
        recipe.version, recipe.fails = int(old.get("version") or recipe.version - 1), 0
        self.save()
        return True

    # ------------------------------------------------------------ after a run

    def after(
        self,
        task: str,
        hit: Hit | None,
        done: bool,
        proven: bool,
        trail: list[tuple[dict, dict]],
        done_shows: str,
        refused: bool = False,
        contract: str = "",
        unmet: str = "",
    ) -> tuple[str, str]:
        """Pin, update or count a failure once a run ends: (what happened, a short word for the HUD) or ("", "").
        `trail`: the ✓ steps in the order they ran, each with its proof; `proven`: the run's declared contract was met
        (checked by code); `contract`: that contract, as declared; `refused`: the user said no to a step; `unmet`: why
        the run's contract did not hold, when it was checked and failed (a replay so ended unpins its recipe)."""
        now = self.clock()
        if hit is not None and not done:
            if refused:
                return "", ""
            if unmet:  # the replay ran its path and the result was wrong: the recipe proves nothing any more
                self.recipes.remove(hit.recipe)
                self.last = "" if self.last == hit.recipe.id else self.last
                self.save()
                return f"pinned task {hit.recipe.id} failed its contract ({unmet}): unpinned", "unpinned"
            hit.recipe.fails += 1
            self.save()
            why = f"pinned task {hit.recipe.id} did not finish ({hit.recipe.fails} in a row)"
            return (why + ("; it is not replayed until it works again" if hit.recipe.benched else "")), ""
        if not done or not proven or not trail or not contract:
            return "", ""
        steps = [s for s, _ in trail]
        if self.typed and not is_typed(steps, done_shows):  # the old words format proves nothing on replay
            return "", ""
        if secret(steps) or any(s.get("tool") == "collect" and SLOT_NAME.match(str(s.get("slot"))) for s in steps):
            return "", ""
        if not whole(steps):  # a replay would press send into an empty box
            return "", ""
        if hit is not None:
            recipe, values = hit.recipe, hit.values
            params = [(n, tuple(w for w, _, _ in _tokens(v))) for n, v in values.items() if _tokens(v)]
        else:
            template, params, values = extract(task, steps)
            recipe = next((r for r in self.recipes if normal(r.template) == normal(template)), None)
            if recipe is not None and sorted(recipe.params) != sorted(values):
                recipe = None
        known = hit.recipe.template if hit is not None else template
        new_steps = [typed(abstract(s, params), known) for s in steps]
        new_proofs = [_abstract(p, params) for _, p in trail]
        new_done = typed_text(_splice(done_shows, params), known) if done_shows else ""
        new_contract = _splice(contract, params)
        if recipe is None:
            recipe = Recipe(
                f"r{self.next}",
                template,
                [n for n, _ in params],
                values,
                new_steps,
                new_proofs,
                new_done,
                new_contract,
                created=now,
                used=now,
            )
            self.next += 1
            self.recipes.append(recipe)
            said, short = f"pinned {recipe.id}: {recipe.template}", "pinned"
        else:
            changed = json.dumps(new_steps, sort_keys=True) != json.dumps(recipe.steps, sort_keys=True)
            if changed:
                old = {"version": recipe.version, "steps": recipe.steps, "proofs": recipe.proofs, "done_shows": recipe.done_shows}
                recipe.history = [old, *recipe.history][:KEEP]
                recipe.version += 1
                recipe.steps, recipe.proofs, recipe.done_shows = new_steps, new_proofs, new_done or recipe.done_shows
                said, short = (
                    f"{recipe.id} updated to v{recipe.version}: the path that worked this time",
                    f"pinned v{recipe.version}",
                )
            else:
                said, short = "", ""
            if recipe.fails and not said:
                said = f"{recipe.id} works again"
            recipe.values, recipe.fails, recipe.used = dict(values), 0, now
            recipe.contract = new_contract or recipe.contract
            recipe.uses += 1
        self.last = recipe.id
        self.save()
        return said, short


def book_of(pins, memory) -> RecipeBook | None:
    """The run's recipe book: the one given, else memory's (pins.json beside the memory file); None for neither."""
    book = pins if pins is not None else getattr(memory, "pins", None)
    return book if isinstance(book, RecipeBook) else None
