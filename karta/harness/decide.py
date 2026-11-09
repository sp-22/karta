"""Laya's narrow decisions D1 to D9: one question each, short semantic options, a decision policy.
Every method returns a Choice (the whole ranking), never acts.

Laya reads `[CLS] <type> question: instructions [SEP] [MASK] option ... [SEP] state [SEP]` within 512 tokens, and
the instructions and options share a 192-token head: a long option list silently cuts the instructions. So every
question here keeps its instructions to one line, its options to a few words each, and shrinks the option list and
then the state until both fit. Options are semantic phrases, never true/false (the English noul bug answered a
confident "no" to positive input), and the Choice carries stable keys (READY, SHOWN, an item's label, ...) so
callers never parse Laya's wording. Templates were chosen by scripts/decision_bench.py, which also writes each
decision's policy to gates.json.

The policy: act when the top answer is reasonably high and clearly ahead of the runner-up (p1 >= floor and
p1 - p2 >= margin), or very high (p1 >= high). A single "zero wrong above it" gate rejected right answers ("Got it"
at 0.70 against 0.91). Each decision has two risk tiers:
- "reversible" (the default): a wrong pick is caught right after (pick check, outcome check) and the loop drops it
  and takes the next option, so the policy is fitted for accuracy after one such round, with a moderate floor and a
  small margin.
- "risky": the action cannot be undone (send, post, pay, delete, a dialog answer that confirms one), so the policy
  is fitted for about 95% precision up front.
A Choice carries the REVERSIBLE policy as its `gate` (the p1 this ranking needs, min(high, max(floor, p2 +
margin))), so `Choice.sure` is the reversible verdict; a caller about to do something irreversible asks
`Laya.acts(choice, risk="risky")`.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path
from typing import ClassVar

import numpy as np

from karta.harness.types import Choice, Item, Screen

GATES = Path(__file__).with_name("gates.json")
DEFAULT_GATE = 0.5
DEFAULT_POLICY = {"floor": DEFAULT_GATE, "margin": 0.2, "high": 0.9}
RISKS = ("reversible", "risky")
POLICY_KEYS = {"reversible": "policy", "risky": "risky_policy"}  # where gates.json keeps each tier
DEFAULT_RISKY = {"floor": 0.9, "margin": 0.8, "high": 0.98}
PROFILE = re.compile(r"\s+in \S.*? profile \S.*$|\s+\(profile [^)]*\)$")  # "a.example in Brave profile Work"
MAX_TOKENS = 512
HEAD_TOKENS = 192  # instructions and options together; past it laya_mlx cuts the instructions
MAX_OPTIONS = 16
STATE_LINES = 20
LABEL_WORDS = 8  # an option label; the full line (kind, region, row) goes in the state

NONE = "none of these"
# D2 page states
READY, LOGIN, DIALOG, LOADING, NOT_FOUND, ELSEWHERE = (
    "ready",
    "login page",
    "dialog in the way",
    "loading",
    "not found",
    "something else",
)
PAGE_STATES = (READY, LOGIN, DIALOG, LOADING, NOT_FOUND, ELSEWHERE)
SCREEN_KINDS = {
    "the app's normal screen": READY,
    "a login screen": LOGIN,
    "a popup or question in front": DIALOG,
    "a loading screen": LOADING,
    "an error screen": NOT_FOUND,
}
CONTROLS = ("button", "field", "link", "row", "tab", "checkbox", "menu")
UNLISTED = "another app, not listed"  # D1's wording of NONE
ABSENT = "not on this screen"  # D3's wording of NONE: "none of these" drew picks away from a field it did not name
# D4 pick check
DOES_IT, DOES_OTHER = "carries it out", "does something else"
# D7 and D8 outcome
SHOWN, NOT_SHOWN = "shown", "not shown"
# D6 dialog role: is a dialog part of the current step, or an interruption to get rid of?
PART_OF_STEP, INTERRUPTION = "part of the step", "an interruption"
# D9 collect: is this item one of the things a collect step takes?
FITS, OTHER = "one of them", "something else"
# D2's private kind of screen: a mailbox or a conversation (consent before its text goes to the brain)
PRIVATE, NOT_PRIVATE = "a mailbox or a conversation", "another kind of page"
PRIVATE_KINDS = {
    "a mailbox, a list of emails or one email": PRIVATE,
    "a chat or a conversation with people": PRIVATE,
    "another kind of page": NOT_PRIVATE,
}
PRIVATE_ASK = "Which kind of page is this?"

DECISIONS = (
    "D1 place",
    "D2 page state",
    "D3 target",
    "D4 pick check",
    "D5 name match",
    "D6 dialog answer",
    "D6 dialog role",
    "D7 outcome",
    "D9 collect",
)


def _words(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 1}


def _short(text: str, words: int = LABEL_WORDS) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + ("..." if len(parts) > words else "")


def labels(items: list[Item]) -> dict[str, Item]:
    """Each item's option label, unique and short: its name, plus its row (or region) when the name repeats
    ("ADD (Lay's Magic Masala 52 g)"). Deterministic, so a caller maps a Choice's option back to its Item."""
    counts = Counter(i.name.strip().lower() for i in items)
    out: dict[str, Item] = {}
    for item in items:
        label = _short(item.name.strip() or item.kind)
        if counts[item.name.strip().lower()] > 1:
            where = next((r for r in item.row if r.strip().lower() != item.name.strip().lower()), "") or item.region
            if where:
                label = f"{label} ({_short(where, 5)})"
        base, n = label, 2
        while label in out or label == NONE:
            label, n = f"{base} #{n}", n + 1
        out[label] = item
    return out


def _names_place(what: str, screen: Screen) -> bool:
    """`what` is the front place itself, by identity: the app's name or its short name ("Notes", "Notes app"), or the
    page's host, its site or the site's name ("example.com", "Example"). Never a word in common: "Pebble News" is
    not the News app."""
    from karta.harness.facts import _app_names, site_of

    said = " ".join(_words(re.sub(r"\s+app$", "", what.strip(), flags=re.I)))
    names = {" ".join(_words(n)) for n in _app_names(screen.app)}
    if screen.url:
        site = site_of(screen.url)
        names |= {" ".join(_words(n)) for n in (_host(screen.url), site, site.split(".")[0])}
    return bool(said) and said in names


def load_gates(path: Path = GATES) -> dict[str, dict]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _host(url: str | None) -> str:
    if not url:
        return ""
    host = re.sub(r"^[a-z]+://", "", url).split("/", 1)[0]
    return host.removeprefix("www.")


class Laya:
    """The harness's questions to Laya. `agent` is laya_mlx's Agent (or anything with its `system_one`),
    `embed` a text -> vector function for shortlisting (LayaMatcher.embed), `templates` picks a question
    variant per decision (the benchmark's best, from gates.json, unless given)."""

    TEMPLATES: ClassVar[dict[str, tuple[str, ...]]] = {  # the first is the default when gates.json names none
        "D1 place": ("sites",),
        "D2 page state": ("screen-kind", "one-question", "poc"),
        "D3 target": ("controls",),
        "D4 pick check": ("contrast",),
        "D5 name match": ("wanted-only", "with-search"),
        "D6 dialog answer": ("task-only", "with-dialog"),
        "D6 dialog role": ("needed-or-close", "goes-on", "which-item"),
        "D7 outcome": ("page", "evidence"),
        "D9 collect": ("is-it", "which-is"),
    }

    def __init__(self, agent, embed=None, gates: dict[str, dict] | None = None, templates: dict[str, str] | None = None):
        self.agent, self.embed = agent, embed
        self.gates = load_gates() if gates is None else gates
        self.templates = {d: (self.gates.get(d) or {}).get("template") or names[0] for d, names in self.TEMPLATES.items()}
        self.templates.update(templates or {})

    # -- plumbing ---------------------------------------------------------------------------------------------

    def gate(self, decision: str) -> float:
        """The decision's reversible floor: no answer below it is acted on (see `policy` for the rest)."""
        return self.policy(decision)["floor"]

    def policy(self, decision: str, risk: str = "reversible") -> dict[str, float]:
        """floor, margin and high of one risk tier, from gates.json. An old single `gate` is a floor with no margin
        for both tiers; with nothing on file, the defaults (the risky one strict)."""
        if risk not in RISKS:
            raise ValueError(f"risk is one of {RISKS}, not {risk!r}")
        entry = self.gates.get(decision) or {}
        default = DEFAULT_POLICY if risk == "reversible" else DEFAULT_RISKY
        if POLICY_KEYS[risk] in entry:
            return {k: float(entry[POLICY_KEYS[risk]].get(k, default[k])) for k in default}
        if "gate" in entry:
            return {"floor": float(entry["gate"]), "margin": 0.0, "high": float(entry["gate"])}
        return dict(default)

    def needed(self, decision: str, ranked: list[tuple[str, float]], risk: str = "reversible") -> float:
        """The top probability this ranking needs to be acted on: min(high, max(floor, p2 + margin)). The runner-up
        is the best other option, not the same site in another browser profile (they share one probability)."""
        pol = self.policy(decision, risk)
        top = PROFILE.sub("", ranked[0][0]) if ranked else ""
        p2 = next((p for o, p in ranked[1:] if PROFILE.sub("", o) != top), 0.0)
        return round(min(pol["high"], max(pol["floor"], p2 + pol["margin"])), 6)

    def acts(self, choice: Choice, risk: str = "reversible") -> bool:
        """The policy's verdict on `choice` for an action of this risk: act on its best answer when p1 >= floor and
        p1 - p2 >= margin, or p1 >= high. "reversible" equals `choice.sure`; use "risky" before an action that
        cannot be undone (send, post, pay, delete, confirming one in a dialog)."""
        return bool(choice.ranked) and choice.ranked[0][1] >= self.needed(choice.decision, choice.ranked, risk)

    def choice(self, decision: str, ranked: list[tuple[str, float]], ms: float = 0.0) -> Choice:
        """A Choice whose gate is what its ranking needs under the reversible policy: `Choice.sure` is its verdict."""
        return Choice(decision, ranked, self.needed(decision, ranked), ms)

    def tokens(self, text: str) -> int:
        """Laya's token count: its tokenizer when the agent has one, else ~3.5 characters a token."""
        tok = getattr(self.agent, "tok", None)
        if tok is not None:
            return len(tok(text, add_special_tokens=False)["input_ids"])
        return math.ceil(len(text) / 3.5)

    def _head(self, instructions: str, options: dict[str, str | None]) -> int:
        rendered = [k if not v else f"{k}: {v}" for k, v in options.items()]
        return self.tokens(f"choice question: {instructions}") + sum(1 + min(48, self.tokens(" " + o)) for o in rendered) + 3

    def _fit_options(
        self, instructions: str, options: dict[str, str | None], keep: tuple[str, ...] = ()
    ) -> dict[str, str | None]:
        """Drop options from the end (the least related come last) until the head fits; `keep` always stays."""
        options = dict(options)
        while len(options) > MAX_OPTIONS or (len(options) > 2 and self._head(instructions, options) > HEAD_TOKENS):
            drop = next((k for k in reversed(options) if k not in keep), None)
            if drop is None:
                break
            del options[drop]
        return options

    def _fit_state(self, state: dict, lines_key: str | None, head: int) -> dict:
        """Drop the state's last lines (the least related) until the whole question fits in 512 tokens."""
        if not lines_key or lines_key not in state:
            return state
        lines = state[lines_key].split("\n")
        while (
            len(lines) > 1
            and head + self.tokens(json.dumps({**state, lines_key: "\n".join(lines)}, ensure_ascii=False)) > MAX_TOKENS - 2
        ):
            lines.pop()
        return {**state, lines_key: "\n".join(lines)}

    def ask(
        self,
        decision: str,
        state: dict,
        instructions: str,
        options: dict[str, str | None],
        keys: dict[str, str] | None = None,
        lines_key: str | None = None,
        keep: tuple[str, ...] = (),
    ) -> Choice:
        """One choice question. `keys` maps Laya's option wording to the option the Choice reports."""
        options = self._fit_options(instructions, options, keep)
        state = self._fit_state(state, lines_key, self._head(instructions, options))
        started = time.perf_counter()
        reply = self.agent.system_one(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}})
        ms = (time.perf_counter() - started) * 1000
        probs = self._calibrated(reply["answers"]["q"]["probabilities"])
        keys = keys or {}
        ranked = sorted(((keys.get(k, k), float(p)) for k, p in probs.items()), key=lambda kv: -kv[1])
        return self.choice(decision, ranked, ms)

    def _calibrated(self, probs: dict[str, float]) -> dict[str, float]:
        """Undo a sharpening temperature. The English checkpoint's "choice:11+" bucket is fitted at 0.10 and laya_mlx
        clamps it to 0.5, which still doubles the logits: with 11 or more options a wrong pick came back at 1.00.
        Re-temper those answers to the 6-10 bucket's temperature, so a gate means the same whatever the count."""
        by_options = getattr(self.agent, "temperature_by_options", None)
        if not by_options or len(probs) < 11:
            return probs
        applied, wanted = by_options.get("choice:11+", 1.0), by_options.get("choice:6-10", 1.0)
        if applied >= wanted:
            return probs
        powered = {k: max(p, 1e-12) ** (applied / wanted) for k, p in probs.items()}
        total = sum(powered.values())
        return {k: p / total for k, p in powered.items()}

    def shortlist(self, query: str, items: list[Item], k: int) -> list[Item]:
        """The k items most related to `query`, most related first: shared words, then embedding similarity."""
        if len(items) <= k:
            return list(items)
        words = _words(query)
        if self.embed is not None:
            vectors = np.asarray(self.embed([query] + [i.describe() for i in items]), dtype=float)
            vectors = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-9)
            similarity = vectors[1:] @ vectors[0]
        else:
            similarity = np.zeros(len(items))
        order = sorted(range(len(items)), key=lambda i: (-len(words & _words(items[i].name)), -float(similarity[i]), i))
        return [items[i] for i in order[:k]]

    @staticmethod
    def _lines(items: list[Item]) -> str:
        return "\n".join(i.describe() for i in items)

    @staticmethod
    def _exact(intent: str, items: list[Item]) -> Item | None:
        """The intent names an item exactly ("click Downloads", "the Search button"): no model needed."""
        text = intent.strip().strip("'\"").lower()
        for verb in ("click on ", "click ", "press ", "select ", "choose ", "open ", "the "):
            text = text.removeprefix(verb)
        text = text.strip().strip("'\"")
        candidates = [text] + [
            text.removesuffix(" " + k).strip() for k in ("button", "field", "link", "tab", "box", "folder", "checkbox")
        ]
        exact = [i for i in items if i.name.strip().lower() in candidates and i.enabled and not i.offscreen]
        return exact[0] if len(exact) == 1 else None

    # -- the decisions ------------------------------------------------------------------------------------------

    def place(self, what: str, places: list[str]) -> Choice:
        """D1: where can `what` be done? Ranks `places` and NONE. The same site in several browser profiles is one
        option: Laya cannot tell profiles apart, so they keep the caller's order (memory first) and share the site's
        probability. NONE is worded "another app, not listed": "none of these" drew 0.91 away from Terminal for
        "Claude Code in the karta folder"."""
        groups: dict[str, list[str]] = {}
        for p in places:
            groups.setdefault(PROFILE.sub("", p), []).append(p)
        keys = {_short(g, 10): g for g in groups} | {UNLISTED: NONE}
        choice = self.ask(
            "D1 place", {"wanted": what}, "Where can `wanted` be done?", dict.fromkeys(keys), keys, keep=(UNLISTED,)
        )
        # One site's profiles share its probability: `needed` judges the policy on the sites, not on a runner-up
        # that is the same site in another profile.
        ranked = [(p, prob) for g, prob in choice.ranked for p in groups.get(g, [NONE])]
        return self.choice(choice.decision, ranked, choice.ms)

    def here(self, what: str, screen: Screen) -> Choice:
        """Is the front window `what` at all (READY) or a different app or site (ELSEWHERE)? Code first, by identity:
        `what` is the front app's name (or its short name) or the site's host; Laya is asked otherwise (a tool running
        inside a terminal, a site named by its brand)."""
        if _names_place(what, screen):
            return self.choice("D2 page state", [(READY, 1.0), (ELSEWHERE, 0.0)])
        where = {
            "app": screen.app,
            **({"site": _host(screen.url)} if screen.url else {}),
            **({"window": screen.window} if screen.window else {}),
        }
        label = _short(what, 6)
        return self.ask(
            "D2 page state",
            {"wanted": what, **where},
            "Is this window `wanted`, or a different app or site?",
            {label: None, "a different app or site": None},
            {label: READY, "a different app or site": ELSEWHERE},
        )

    def page_state(self, what: str, screen: Screen) -> Choice:
        """D2: is this `what`, ready? Ranks READY, LOGIN, DIALOG, LOADING, NOT_FOUND, ELSEWHERE. Code checks
        (facts.is_login_page, facts.blocking_dialog) come first; this is for the rest.

        "screen-kind" (the default) asks two narrow questions: which place (`here`), then which kind of screen, from
        the page's items alone. One catch-all question drew "something else, not <the site>" at 0.77 to 0.97 on that
        very site; asked apart, and without the wanted name in the state, the kind of screen is read from the page."""
        variant = self.templates["D2 page state"]
        page = self.shortlist(what, screen.page(), STATE_LINES)
        if variant == "poc":  # the first, one-question form, kept as the benchmark's baseline
            options = {
                f"{what}, ready to use": None,
                "login": None,
                "loading": None,
                "dialog": None,
                "not found": None,
                f"something else, not {what}": None,
            }
            state = {"wanted": what, "screen": "; ".join(i.name for i in page)}
            return self.ask(
                "D2 page state",
                state,
                "Is `screen` `wanted`, ready to use?",
                options,
                dict(zip(options, PAGE_STATES, strict=True)),
            )
        kinds = dict(SCREEN_KINDS)
        if variant == "one-question":
            kinds["a different app or site"] = ELSEWHERE
            state = {"wanted": what, "app": screen.app, "site": _host(screen.url), "page": self._lines(page)}
            return self.ask(
                "D2 page state", state, "Which kind of screen is this?", dict.fromkeys(kinds), kinds, lines_key="page"
            )
        here = self.here(what, screen)
        p_here = dict(here.ranked).get(READY, 0.0)
        if p_here < 0.5:
            return here  # a different place: what its page shows does not matter
        kind = self.screen_kind(screen, page)
        ranked = sorted([(k, p * p_here) for k, p in kind.ranked] + [(ELSEWHERE, 1 - p_here)], key=lambda kv: -kv[1])
        return self.choice("D2 page state", ranked, here.ms + kind.ms)

    def screen_kind(self, screen: Screen, page: list[Item] | None = None) -> Choice:
        """D2's second question alone: which kind of screen is this (the app's normal screen, a login screen, a popup in
        front, a loading screen, an error screen), from the page's items alone. Asked as a place opens with no password
        field on it: only a sure "login screen" (under the risky tier) makes it a sign-in page (Toolbox.asks_sign_in)."""
        page = page if page is not None else screen.page()[:STATE_LINES]
        kinds = dict(SCREEN_KINDS)
        return self.ask(
            "D2 page state",
            {"page": self._lines(page)},
            "Which kind of screen is this?",
            dict.fromkeys(kinds),
            kinds,
            lines_key="page",
        )

    def private_screen(self, screen: Screen) -> Choice:
        """D2's private kind of screen: is this web page a mailbox or a conversation (PRIVATE) or another kind of page
        (NOT_PRIVATE)? From the page's items, its title and host. Asked before a web page's text goes to the brain
        (Toolbox.private_label): only a sure NOT_PRIVATE, under the risky tier, sends it without the user's yes."""
        page = [Item(f"window title: {screen.window}", "heading"), *screen.page()] if screen.window else screen.page()
        kinds = dict(PRIVATE_KINDS)
        got = self.ask(
            "D2 page state",
            {"site": _host(screen.url), "page": self._lines(page[:STATE_LINES])},
            PRIVATE_ASK,
            dict.fromkeys(kinds),
            kinds,
            lines_key="page",
        )
        merged: dict[str, float] = {}  # a mailbox and a chat are one answer: private
        for option, p in got.ranked:
            merged[option] = merged.get(option, 0.0) + p
        return self.choice("D2 page state", sorted(merged.items(), key=lambda kv: -kv[1]), got.ms)

    def target(self, intent: str, items: list[Item]) -> Choice:
        """D3: which item does `intent` mean ("the Search button", "the message box")? Ranks item labels (see
        `labels`) and NONE; an item named exactly is taken without asking. Only controls are offered when the
        screen has any (plain text drew picks: the product name over its ADD button), and a field says it is one
        ("(text box)"): "the message box" otherwise lost to "none of these"."""
        pool = [i for i in items if not i.menu or _words(intent) & _words(i.name)]
        if any(i.kind in CONTROLS for i in pool):
            pool = [i for i in pool if i.kind in CONTROLS]
        names = labels(pool)
        exact = self._exact(intent, pool)
        if exact is not None:
            label = next(k for k, v in names.items() if v is exact)
            return self.choice("D3 target", [(label, 1.0)] + [(k, 0.0) for k in names if k != label] + [(NONE, 0.0)])
        short = self.shortlist(intent, pool, MAX_OPTIONS - 1)
        keys = {}
        for label, item in sorted(names.items(), key=lambda kv: short.index(kv[1]) if kv[1] in short else len(short)):
            if item in short:
                keys[f"{label} (text box)" if item.kind == "field" and " (" not in label else label] = label
        keys[ABSENT] = NONE
        instructions = "Which control on screen would a person use to do `asked`?"
        return self.ask("D3 target", {"asked": intent}, instructions, dict.fromkeys(keys), keys, keep=(ABSENT,))

    def pick_ok(self, intent: str, item: Item) -> Choice:
        """D4: does using `item` carry out `intent`? Ranks DOES_IT and DOES_OTHER. Asked as a choice between the item
        and "some other control" (its row named when it has one), not as "what would it do": Laya compares names
        better than it predicts effects."""
        label = _short(item.name.strip() or item.kind) + (f" ({_short(item.row[0], 5)})" if item.row else "")
        label += " (text box)" if item.kind == "field" else ""
        keys = {label: DOES_IT, "some other control": DOES_OTHER}
        return self.ask("D4 pick check", {"asked": intent}, "Which does `asked` need?", dict.fromkeys(keys), keys)

    def name_match(self, meaning: str, searched: str, items: list[Item]) -> Choice:
        """D5: which result is `meaning`? Ranks item labels and NONE. "wanted-only" (the default) leaves out what was
        typed in the search box: the searched text pulled Laya toward results that merely contain it."""
        # a line saying nothing matched is ruled out first (Toolbox.no_results): Laya picked it over "none of these"
        results = [i for i in items if i.kind != "field" and not i.menu and not i.chrome]
        short = self.shortlist(meaning + " " + searched, results, MAX_OPTIONS - 1)
        names = labels(results)
        options = dict.fromkeys(sorted((k for k, v in names.items() if v in short), key=lambda k: short.index(names[k]))) | {
            NONE: None
        }
        state = {"wanted": meaning}
        if self.templates["D5 name match"] == "with-search":
            state["searched for"] = searched
        return self.ask("D5 name match", state, "Which result is `wanted`? None of these if none is.", options, keep=(NONE,))

    def dialog_answer(self, task: str, options: list[Item]) -> Choice:
        """D6: which of a dialog's options fits `task` ("Detect my location", "1. Yes, proceed")? Ranks labels.
        "task-only" (the default) gives Laya the task and the answers but not the dialog's own text, which pulled it
        toward the answer the dialog pushes ("Install Now", "Use the app")."""
        buttons = [i for i in options if i.kind not in ("text", "heading") and i.enabled] or list(options)
        prompt = [i.name for i in options if i.kind in ("text", "heading")]
        state = {"task": task}
        if self.templates["D6 dialog answer"] == "with-dialog" and prompt:
            state["dialog"] = _short(" ".join(prompt), 40)
        return self.ask("D6 dialog answer", state, "Which answer fits `task`?", dict.fromkeys(labels(buttons)))

    def dialog_role(self, intent: str, items: list[Item]) -> Choice:
        """D6 variant: is this dialog part of the current step (a settings sheet, a detail panel, a picker, a
        confirmation the step asked for) or an interruption to close (a promo, a cookie banner, an unrelated
        prompt)? Ranks PART_OF_STEP and INTERRUPTION. `items` are the dialog's own items, text and controls. A step
        that names one of the dialog's controls exactly is part of it without asking.

        "needed-or-close" asks it outright; "goes-on" asks whether the step goes on inside the popup; "which-item"
        asks which of its controls the step would use, and "none, it is in the way" is the interruption."""
        pool = [i for i in items if i.enabled and not i.offscreen]
        if self._exact(intent, pool) is not None:
            return self.choice("D6 dialog role", [(PART_OF_STEP, 1.0), (INTERRUPTION, 0.0)])
        variant = self.templates["D6 dialog role"]
        if variant == "which-item":
            controls = [i for i in pool if i.kind in CONTROLS] or pool
            names = labels(self.shortlist(intent, controls, MAX_OPTIONS - 1))
            away = "none, it is in the way"
            pick = self.ask(
                "D6 dialog role",
                {"step": intent, "popup": self._lines(items[:STATE_LINES])},
                "Which control in `popup` would a person use to do `step`?",
                dict.fromkeys([*names, away]),
                keep=(away,),
                lines_key="popup",
            )
            p_away = dict(pick.ranked).get(away, 0.0)
            p_part = max((p for k, p in pick.ranked if k != away), default=0.0)
            total = (p_part + p_away) or 1.0
            ranked = sorted([(PART_OF_STEP, p_part / total), (INTERRUPTION, p_away / total)], key=lambda kv: -kv[1])
            return self.choice("D6 dialog role", ranked, pick.ms)
        if variant == "goes-on":
            instructions, keys = (
                "Does `step` go on inside `popup`, or did `popup` appear unasked?",
                {"the step goes on in it": PART_OF_STEP, "it appeared unasked": INTERRUPTION},
            )
        else:
            instructions, keys = (
                "Is `popup` needed for `step`, or an interruption to close?",
                {"needed for the step": PART_OF_STEP, "an interruption to close": INTERRUPTION},
            )
        state = {"step": intent, "popup": self._lines(items[:STATE_LINES])}
        return self.ask("D6 dialog role", state, instructions, dict.fromkeys(keys), keys, lines_key="popup")

    def outcome(self, expected: str, screen: Screen) -> Choice:
        """D7 and D8: does the page show `expected` (a step's result or the task's end state)? Ranks SHOWN and
        NOT_SHOWN. Editable fields are left out: the harness's own typing is never the result. Code checks
        (facts.shows) come first; this is for a result worded differently from the page.

        "page" asks it of the page's most related lines at once; "evidence" first picks the line that would show it
        (the window title is one), then asks whether that line does."""
        page = [i for i in screen.page() if i.kind != "field"]
        if self.templates["D7 outcome"] == "evidence":
            if screen.window:
                page = [Item(f"window title: {screen.window}", "heading"), *page]
            names = labels(self.shortlist(expected, page, MAX_OPTIONS))
            pick = self.ask(
                "D7 outcome", {"expected": expected}, "Which item on the page shows `expected`?", dict.fromkeys(names)
            )
            keys = {"it shows it": SHOWN, "it shows something else": NOT_SHOWN}
            state = {"expected": expected, "item": names[pick.best].describe()}
            check = self.ask("D7 outcome", state, "Does `item` show `expected`?", dict.fromkeys(keys), keys)
            return Choice("D7 outcome", check.ranked, check.gate, pick.ms + check.ms)
        page = self.shortlist(expected, page, STATE_LINES)
        keys = {"the page shows it": SHOWN, "the page does not show it": NOT_SHOWN}
        state = {"expected": expected, "page": self._lines(page)}
        return self.ask("D7 outcome", state, "Does `page` show `expected`?", dict.fromkeys(keys), keys, lines_key="page")

    def collect_fit(self, what: str, item: Item) -> Choice:
        """D9: is `item` one of `what` (a collect step's "links to charting libraries", "the overdue invoices")? Ranks
        FITS and OTHER. Asked once per candidate, in screen order, until the step has as many as it wants; code has
        already left out controls, ads and anything without the value asked for.

        "which-is" (as the pick check) sets the item against "something else on the page": Laya compares names
        better than it judges one alone; "is-it" asks about the item's line outright."""
        label = _short(item.name.strip() or item.kind, 12)
        if self.templates["D9 collect"] == "is-it":
            keys = {"it is one of them": FITS, "it is something else": OTHER}
            state = {"wanted": what, "item": item.describe()}
            return self.ask("D9 collect", state, "Is `item` one of `wanted`?", dict.fromkeys(keys), keys)
        keys = {label: FITS, "something else on the page": OTHER}
        return self.ask("D9 collect", {"wanted": what}, "Which is one of `wanted`?", dict.fromkeys(keys), keys)
