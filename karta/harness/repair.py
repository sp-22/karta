"""Local repair (docs/harness.md, "Local repair"): a step that missed mechanically is fixed by code and
Laya before the brain is asked.

The brain plans once and looks at a step that is wrong in the plan (missing, in the wrong order, aimed at the wrong
thing). A step that is right but missed mechanically climbs a local ladder of up to LOCAL_TRIES tries first, each a
fraction of a brain call's time and cost, always in this order:

1. **wait**: wait for a late change and read the screen again (the step's check again; a missing target or field:
   the step again once the screen changed);
2. **another way**: the same action done otherwise (text: a real click and paste, the keys one by one, tab for a token
   field; a click: the same command by its other route, the menu bar's command for a control or the control for a
   menu command; a write or fill: the focused field when it fits; a collect: scroll, then read again; a dialog in
   the way: escape);
3. **the next-best**: Laya's other candidates for a click's target, best first (try, verify, next: retry.try_next
   for its own pick, else its ranking now), each verified by the step's declared kind of change and undone when
   wrong and undoable; a page that changed in place is left as it is and the next candidate verified by its type.

A rung that does not fit the miss is skipped and costs no try. A step that sends, answers or commits (submit, answer,
reach, a risky or commit-like click) only ever gets the wait: it is never run again, done another way or swapped for
another candidate. Every rung is bounded (waits that end when the pixels move, never fixed sleeps on the real Mac)
and verified by state. What each rung tried goes to the look's prompt, so the brain is asked about the plan, not
about the mechanics.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from karta.harness import facts as facts_mod
from karta.harness import risk
from karta.harness.decide import DOES_IT, NONE
from karta.harness.expect import UNNAMED
from karta.harness.progress import pressed, run_once
from karta.harness.retry import COMMITS, MAX_TRIES, mark_wrong, try_next, undo, why_not
from karta.harness.tools import (
    CHANGE_TIMEOUT,
    Toolbox,
    affirms,
    beside,
    code_named,
    holds,
    item_for,
    names_command,
    norm,
    spot,
)
from karta.harness.types import Item, Outcome, Screen, ToolResult

LOCAL_TRIES = 3  # local tries per step before any brain look: the wait, another way, the next-best
WAIT, ANOTHER_WAY, NEXT_BEST = "wait", "another way", "the next-best"
MAX_KEYS = 120  # text typed key by key at most this long (a recipient, a title); a longer text is only ever pasted
CLICK_WAIT = 1.0  # a click on a field changes little on screen: the wait for it is short
TYPED_MISS = re.compile(r"^typed .* but '.*' (holds|is gone)")
RAN_BUT = re.compile(r"^it ran, but ")  # the loop's typed miss: nothing changed, or a different change


@dataclass
class Repair:
    """What a local repair did: the step's result now, how it was fixed (for the ledger), each rung tried that missed
    (for the look's prompt), whether the step turned out not to be needed, how many rungs ran (each is one of the
    step's tries) and the clicks found wrong on the way (in order). Every action a rung ran is an edge of the task's
    state graph (progress.py): a rung never runs again what missed on the same screen."""

    result: ToolResult
    how: str = ""
    tried: list[str] = field(default_factory=list)
    skip: bool = False
    tries: int = 0
    wrong: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.result.ok


class Ladder:
    """The rungs one repair climbed: each that ran is one try (traced as "local try n/3"), each skipped says why."""

    def __init__(self, tools: Toolbox, result: ToolResult, room: int = LOCAL_TRIES):
        self.tools, self.result, self.room = tools, result, room
        self.tried: list[str] = []
        self.tries = 0
        self.wrong: list[str] = []

    def go(self, rung: str) -> bool:
        """One more try, when the step has one left (`room`: LOCAL_TRIES, fewer once the step used some already)."""
        if self.tries >= self.room:
            self.tools.trace.append(f"  local {rung}: no try left for this step")
            return False
        self.tries += 1
        self.tools.trace.append(f"  local try {self.tries}/{self.room}: {rung}")
        return True

    def note(self, line: str) -> None:
        self.tried.append(line)

    def skip(self, rung: str, why: str) -> None:
        self.tools.trace.append(f"  local {rung}: skipped ({why})")

    def fixed(self, result: ToolResult, how: str) -> Repair:
        return Repair(result, how=how, tried=self.tried, tries=self.tries, wrong=self.wrong)

    def missed(self, result: ToolResult | None = None) -> Repair:
        return Repair(result or self.result, tried=self.tried, tries=self.tries, wrong=self.wrong)


def kind_of(step: dict, result: ToolResult) -> str | None:
    """Which miss this is, from the step's tool, its Outcome and its reason; None when no local repair fits (the
    user said no, a permission, a place, a step that repeats itself, nothing collected for a name)."""
    tool, outcome, detail = step.get("tool"), result.outcome, result.detail or ""
    if outcome in (Outcome.OK, Outcome.REFUSED, Outcome.NEEDS_PERMISSION, Outcome.STUCK, Outcome.NO_DATA):
        return None
    if tool == END:  # the done check missed: a late change is waited for here, before any look
        return "expected"
    if tool == "open_place" or tool not in TOOLS_LOCAL:
        return None
    if step.get("expects") and (outcome is Outcome.NO_CHANGE or RAN_BUT.match(detail)):
        return "expected"  # nothing changed, or a different change
    if tool == "click" and outcome in (Outcome.NO_CHANGE, Outcome.UNEXPECTED):
        return "expected"  # a click with no expectation of its own: its tool's check, then the next-best pick
    if outcome is Outcome.UNEXPECTED and tool in ("write", "fill") and TYPED_MISS.match(detail):
        return "text"
    if outcome is Outcome.BLOCKED_BY_DIALOG and tool not in ("answer", "submit"):
        return "dialog"
    if outcome in (Outcome.NOT_FOUND, Outcome.NO_FIELD) and tool in ("click", "write", "fill", "find"):
        return "missing"
    if outcome is Outcome.UNSURE and tool in ("click", "write", "fill") and "no sure pick" in detail:
        return "unsure"
    if outcome is Outcome.NOT_FOUND and tool == "collect":
        return "collect"
    return None


END = "(end of plan)"  # the loop's pseudo-step for the done check
TOOLS_LOCAL = ("click", "write", "fill", "find", "type", "submit", "answer", "reach", "collect", "back", "scroll")
RISKY_TOOLS = ("submit", "answer", "reach")  # they send, answer or buy: never run again by a repair


def risky_step(step: dict) -> bool:
    """A step a repair never runs again: one that sends, answers or commits, or whose target is hard to undo."""
    tool = step.get("tool")
    if tool in RISKY_TOOLS:
        return True
    at = step.get("at")
    target = str((at.get("name") if isinstance(at, dict) else "") or step.get("target") or "")  # a #n item's own name
    return tool == "click" and (risk.risky("click", target) is not None or bool(COMMITS.match(target)))


def repair(
    tools: Toolbox,
    step: dict,
    result: ToolResult,
    verify: Callable[[dict, ToolResult], ToolResult],
    check: Callable[[Screen], bool],
    budget_left: Callable[[], bool] = lambda: True,
    room: int = LOCAL_TRIES,
) -> Repair | None:
    """The local ladder of a failed step, or None when no rung fits its miss. `verify(step, result)` is the loop's
    check of a step's result (its declared kind of change); `check(screen)` code's read of that kind on a screen,
    since the screen just before the step; `room` the tries the step has left (at most LOCAL_TRIES)."""
    kind = kind_of(step, result)
    if kind is None or room < 1 or not budget_left() or tools.drift() is not None:
        return None  # another app or a login page in front: the loop's own rung goes back to the place that worked
    tools.trace.append(f"  local repair ({kind}): {result.outcome.value}")
    ladder = Ladder(tools, result, min(room, LOCAL_TRIES))
    if kind == "text":
        fixed = text(tools, step, result, verify, budget_left, ladder)
    elif kind == "expected":
        fixed = expected(tools, step, result, verify, check, budget_left, ladder)
    elif kind == "dialog":
        fixed = dialog(tools, step, result, verify, ladder)
    elif kind == "collect":
        fixed = collect(tools, step, result, verify, budget_left, ladder)
    else:
        fixed = missing(tools, step, result, verify, budget_left, ladder, unsure=kind == "unsure")
    return fixed


# ---------------------------------------------------------------- text that did not stick


def focus_after_click(tools: Toolbox, box: Item) -> tuple[Item | None, Screen]:
    """A real click on the field, then the field that has the focus: of several copies of one name stacked at one spot
    (a covered field under the one on top), the one the click reached. (None, screen) when the field is gone."""
    ahead = tools.look()
    target = tools.field_named(ahead, box.name, box)
    if target is None:
        return None, ahead
    tools.do(f"click {target.name!r}", tools.env.click, target)
    scr = tools.settle(ahead.fingerprint(), timeout=CLICK_WAIT) or tools.look()
    focused = [f for f in scr.items if f.kind == "field" and f.focused and not f.menu and not f.chrome]
    mine = [f for f in focused if norm(f.name) == norm(box.name)] or [f for f in focused if beside(f, target)]
    if mine:
        if len([f for f in scr.items if f.kind == "field" and norm(f.name) == norm(box.name)]) > 1:
            tools.trace.append(f"  {box.name!r}: several at one spot; the one the click focused")
        return mine[0], scr
    return tools.field_named(scr, box.name, box), scr


def text(
    tools: Toolbox, step: dict, result: ToolResult, verify: Callable, budget_left: Callable[[], bool], ladder: Ladder
) -> Repair:
    """1. wait: the field may show its value late; 2. another way, escalating, each verified by the field holding the
    text or a token beside it, stopping at the first that works: a real click, the focused copy, its old text replaced
    by a paste (when the step did not do exactly that already); a click, then the text key by key (short, one line);
    tab, so a token field takes what was typed. Never typed again into a box that holds the text already, and never
    into another field than the one the step names (no next-best for text)."""
    put = tools.last_put
    if not put:
        ladder.note("text: nothing was typed into a known field")
        return ladder.missed()
    box, value, shown = put["box"], put["text"], put["shown"]
    said = repr(shown if shown is not None else value)
    if not ladder.go(WAIT):
        return ladder.missed()
    tools.hold(CLICK_WAIT)
    late = tools.field_named(tools.look(), box.name, box)
    if late is not None and holds(late.value, value):
        return finish(tools, step, verify, ToolResult(Outcome.OK, f"{late.name!r} holds {said}"), late, value, WAIT, ladder)
    ladder.note(f"wait: {box.name!r} still does not hold it")
    if not budget_left():
        return ladder.missed()
    if not ladder.go(ANOTHER_WAY):
        return ladder.missed()
    keys = callable(getattr(tools.env, "type_keys", None)) and "\n" not in value and len(value) <= MAX_KEYS
    tried = ladder.tried
    clicked: tuple[Item, Screen] | None = None  # the click of (a), reused by (b)
    for rung in ("click, paste", "click, keys", "tab"):
        if not budget_left():
            break
        if rung == "click, keys" and not keys:
            why = (
                "this Mac types by paste only"
                if not callable(getattr(tools.env, "type_keys", None))
                else "several lines are pasted only"
                if "\n" in value
                else "too long to type key by key"
            )
            tried.append(f"keys: {why}")
            continue
        if rung == "tab":
            ahead = tools.look()
            target = tools.field_named(ahead, box.name, box)
            if target is None:
                tried.append(f"tab: {box.name!r} is gone")
                break
            if not target.focused:  # tab moves the focus on from wherever it is: only from this field
                tried.append(f"tab: {box.name!r} does not hold the focus")
                break
            if not tools.do("press tab (a token field takes what was typed)", tools.env.press, "tab"):
                tried.append("tab: not pressed, the focus is in nothing of the app")
                break
        else:
            target, ahead = clicked if clicked is not None else focus_after_click(tools, box)
            clicked = None
            if target is None:
                tried.append(f"{rung}: {box.name!r} is gone")
                break
            if holds(target.value, value):  # never typed twice
                return finish(
                    tools, step, verify, ToolResult(Outcome.OK, f"{target.name!r} holds {said}"), target, value, rung, ladder
                )
            stacked = sum(f.kind == "field" and norm(f.name) == norm(target.name) for f in ahead.items) > 1
            if rung == "click, paste" and rung in put["tried"] and not stacked:
                tried.append("click, paste: the step did that already")
                clicked = (target, ahead)
                continue
            if rung == "click, paste":
                tools.do(f"replace the text in {target.name!r} with {said}", tools.env.set_text, target, value)
            else:
                # into that field only, its old text selected by the env (never a blind select all): replaces
                tools.do(f"type {said} key by key into {target.name!r}", tools.env.type_keys, value, target)
            box = target
        now, token = tools.landed(ahead, target, value)
        done = tools.took(target, now, token, value, said)
        if done is not None:
            return finish(tools, step, verify, done, target, value, rung, ladder)
        still = "is gone" if now is None else f"holds {now.value!r}" if shown is None else "does not hold it"
        tried.append(f"{rung}: {target.name!r} {still}")
    ladder.skip(NEXT_BEST, "text goes only into the field the step names")
    return ladder.missed()


def finish(
    tools: Toolbox, step: dict, verify: Callable, done: ToolResult, box: Item, value: str, rung: str, ladder: Ladder
) -> Repair:
    """The field holds the text now: the write's bookkeeping (what a submit sends and checks), the rest of a fill, and
    the step's own check."""
    tools.pending[value] = box.name
    tools.last_write = (box.name, value)
    tools.submitted = None
    if step["tool"] == "fill":
        rest = {k: v for k, v in step.get("fields", {}).items() if k not in tools.filled and k != tools.fill_at}
        filled = [*tools.filled, tools.fill_at]
        if rest:
            more = tools.run({"tool": "fill", "fields": rest})
            if not more.ok:
                ladder.note(f"{rung}: {box.name!r} took it, but the rest of the fill: {more.detail}")
                return ladder.missed(more)
            filled += list(rest)
        done = ToolResult(Outcome.OK, f"filled {filled}")
    return ladder.fixed(verify(step, done), f"{rung}: {done.detail}")


# ---------------------------------------------------------------- a kind of change that did not happen


def other_route(tools: Toolbox, step: dict, press) -> Item | None:
    """The same command by its other route: the menu bar's command the click's target names, for a control on the
    page (or a target not found on it); the control on the page named so, for a menu command. Never one found wrong
    for this step, nor a risky or commit-like one."""
    target, near = str(step.get("target") or ""), str(step.get("near") or "")
    if not target:
        return None
    scr = tools.look()
    wrong = tools.wrong.get((norm(target), norm(near)), set())
    was = spot(press.item) if press is not None else None

    def fits(i: Item) -> bool:
        return (
            i.enabled and spot(i) not in wrong and spot(i) != was and risk.risky("click", i.name) is None and not risk.commits(i)
        )

    if press is not None and press.item.menu:
        return code_named(
            target, [i for i in scr.page() if not i.menu and i.kind not in ("field", "text", "heading") and fits(i)]
        )
    return next((i for i in scr.items if i.menu and names_command(target, i.name) and fits(i)), None)


def expected(
    tools: Toolbox,
    step: dict,
    result: ToolResult,
    verify: Callable,
    check: Callable[[Screen], bool],
    budget_left: Callable[[], bool],
    ladder: Ladder,
) -> Repair:
    """The step ran but its declared kind of change did not happen (expect.py), or a click's own check missed.
    1. wait for a late change and verify again (the done check too; a different change may be on its way to the
    declared one); then, for a click that does not send or commit: 2. the same command by its other route
    (other_route), the first click undone when it made a different change; 3. the next-best: try, verify, next for
    Laya's own pick (retry.try_next), else Laya's ranking now (options), each verified by the same type. A page that
    changed in place stays as it is and the next candidate is verified by its type. What is left goes to the look,
    which is told exactly what changed."""
    tool = step["tool"]
    late = tool == END or result.outcome is Outcome.NO_CHANGE or UNNAMED in result.detail
    if step.get("expects") or tool == END:
        if not ladder.go(WAIT):
            return ladder.missed()
        tools.hold(CHANGE_TIMEOUT if late else CLICK_WAIT)
        scr = tools.look()
        if check(scr) or any(check(later) for later in tools.rechecks(scr)):
            return ladder.fixed(ToolResult(Outcome.OK, "it showed once the screen changed"), "it showed once the screen changed")
        ladder.note("wait: still not met")
    else:
        ladder.skip(WAIT, "no expectation to wait for")
    if tool != "click":
        return ladder.missed()
    press = tools.last_press
    if press is None or not budget_left():
        return ladder.missed()
    target = str(step.get("target") or "")
    risky = risky_step(step)  # it sends, answers or commits: never done again another way nor by a fresh ranking
    alt = None if risky else other_route(tools, step, press)
    undone_first = False
    if alt is None:
        ladder.skip(ANOTHER_WAY, "a step that sends or commits" if risky else "no other route to the same command")
    else:
        if not ladder.go(ANOTHER_WAY):
            return ladder.missed()
        if not unclick(tools, press, result, ladder):
            return ladder.missed()
        undone_first = True
        mark_wrong(tools, press, f"clicked for {target!r}: {result.outcome.value}", result.outcome is Outcome.NO_CHANGE)
        done = pressed(tools, step, alt, "another way", verify)
        if done.ok:
            return ladder.fixed(done, f"another way {alt.name!r}")
        ladder.note(f"another way {alt.name!r}: {done.outcome.value}")
        ladder.wrong.append(alt.name)
        if done.outcome is not Outcome.STUCK:  # it was pressed (a dead end here is never pressed again)
            mark_wrong(
                tools, tools.last_press, f"another way for {target!r}: {done.outcome.value}", done.outcome is Outcome.NO_CHANGE
            )
            if not unclick(tools, tools.last_press, done, ladder) or not budget_left():
                return ladder.missed(done)
            result = done
    if step.get("at"):  # an item the look named by number is never swapped for a guess: the brain looks again
        ladder.skip(NEXT_BEST, "a numbered item is not taken for another")
        return ladder.missed()
    no = why_not(step, result, press) if not undone_first else ""
    if no is None:  # Laya's own pick (or a near one), itself safe to undo: try, verify, next
        if not ladder.go(NEXT_BEST):
            return ladder.missed()
        again, wrong = try_next(tools, step, result, verify, budget_left)
        ladder.wrong += wrong
        if again.ok:
            return ladder.fixed(again, f"the next-best {tools.last_press.item.name!r}")
        ladder.note(f"the next-best: {again.detail}" if wrong else f"the next-best: none tried ({again.outcome.value})")
        return ladder.missed(again)
    if no:
        tools.trace.append(f"  no retry: {no}")
    if risky:
        ladder.note("a step that sends, answers or commits is never swapped for another candidate")
        return ladder.missed()
    if not step.get("expects"):
        ladder.skip(NEXT_BEST, "no expectation to verify a guess by")
        return ladder.missed()
    if not undone_first:
        if not unclick(tools, press, result, ladder):
            return ladder.missed()
        mark_wrong(tools, press, f"clicked for {target!r}: {result.outcome.value}", result.outcome is Outcome.NO_CHANGE)
    if not ladder.go(NEXT_BEST):
        return ladder.missed()
    return options(tools, step, result, verify, budget_left, ladder)


def unclick(tools: Toolbox, press, result: ToolResult, ladder: Ladder) -> bool:
    """Undo a click that missed (retry.undo); a page that changed in place stays as it is. False when what it opened
    could not be closed or left: then nothing more is clicked."""
    undone = undo(tools, press, result)
    if undone is None:
        ladder.note(f"{press.item.name!r}: what it opened could not be closed or left")
        return False
    if undone:
        ladder.note(f"a different change: {undone}" if result.outcome is Outcome.UNEXPECTED else f"{press.item.name!r}: {undone}")
    return True


# ---------------------------------------------------------------- no field or control, Laya unsure


def missing(
    tools: Toolbox,
    step: dict,
    result: ToolResult,
    verify: Callable,
    budget_left: Callable[[], bool],
    ladder: Ladder,
    unsure: bool = False,
) -> Repair:
    """No field or control was found, or Laya was unsure which. 1. wait: the screen may still be coming (the step
    again once it changed); 2. another way: a write or fill takes the focused field when it fits (code's reading of
    its name, else a sure pick check), a click the menu bar's command its target names; 3. the next-best: a click
    tries Laya's ranked options, not risky ones: try, verify, next, up to MAX_TRIES."""
    if risky_step(step):
        ladder.note("a step that sends or commits is never redone")
        return ladder.missed()
    if not ladder.go(WAIT):
        return ladder.missed()
    was = tools.look().fingerprint()
    changed = tools.hold(CHANGE_TIMEOUT)
    if changed is not False and tools.look().fingerprint() != was:
        again = run_once(tools, step, verify)
        if again.ok:
            return ladder.fixed(again, "the screen changed: the step again")
        ladder.note(f"wait: the screen changed, the step again: {again.outcome.value}")
    else:
        ladder.note("wait: nothing changed")
    if not budget_left():
        return ladder.missed()
    if step.get("at"):  # an item the page plan named by number is never swapped for a guess: the page is looked at again
        ladder.note("a numbered item is not taken for another")
        return ladder.missed()
    tool = step["tool"]
    if tool in ("write", "fill"):
        if not ladder.go(ANOTHER_WAY):
            return ladder.missed()
        fixed = focused_field(tools, step, verify, ladder)
        if fixed is not None:
            return fixed
        ladder.skip(NEXT_BEST, "text goes only into the field the step names")
        return ladder.missed()
    if tool != "click":
        return ladder.missed()
    target = str(step.get("target") or "")
    alt = other_route(tools, step, None)
    if alt is None:
        ladder.skip(ANOTHER_WAY, "no menu command by that name")
    else:
        if not ladder.go(ANOTHER_WAY):
            return ladder.missed()
        done = pressed(tools, step, alt, "another way", verify)
        if done.ok:
            return ladder.fixed(done, f"another way {alt.name!r}")
        ladder.note(f"another way {alt.name!r}: {done.outcome.value}")
        ladder.wrong.append(alt.name)
        if done.outcome is not Outcome.STUCK:  # it was pressed (a dead end here is never pressed again)
            mark_wrong(
                tools, tools.last_press, f"another way for {target!r}: {done.outcome.value}", done.outcome is Outcome.NO_CHANGE
            )
            if not unclick(tools, tools.last_press, done, ladder):
                return ladder.missed(done)
    if not budget_left():
        return ladder.missed()
    if not step.get("expects"):
        ladder.skip(NEXT_BEST, "no expectation to verify a guess by")
        return ladder.missed()
    if not ladder.go(NEXT_BEST):
        return ladder.missed()
    return options(tools, step, result, verify, budget_left, ladder)


def field_asked(tools: Toolbox, step: dict) -> str:
    return str(step.get("where") or "") if step["tool"] == "write" else tools.fill_at


def focused_field(tools: Toolbox, step: dict, verify: Callable, ladder: Ladder) -> Repair | None:
    """The focused field, when it fits the field the step names (its name by code, else Laya's sure pick check);
    None when it does not (noted)."""
    name = field_asked(tools, step)
    scr = tools.look()
    focus = next((f for f in tools.page_fields(scr) if f.focused), None)
    if not name or focus is None:
        ladder.note("no focused field")
        return None
    fits = code_named(name, [focus]) is not None or affirms(tools.ask("pick_ok", f"the {name} field", focus), DOES_IT)
    if not fits:
        ladder.note(f"the focused field {focus.name!r} is not {name!r}")
        return None
    done = type_into(tools, step, name, focus)
    if done.ok:
        return ladder.fixed(verify(step, done), f"the focused field {focus.name!r}")
    ladder.note(f"the focused field {focus.name!r}: {done.detail}")
    return None


def type_into(tools: Toolbox, step: dict, name: str, box: Item) -> ToolResult:
    """The write's text (or the fill's value for `name`) into `box`, as the tool would, then the rest of a fill."""
    said = str(step.get("text") if step["tool"] == "write" else step.get("fields", {}).get(name, ""))
    value, missing_data = tools.expand(said, box.multiline)
    if missing_data is not None:
        return missing_data
    put = tools.put(box, value, shown=said if said != value else None)
    if not put.ok:
        return put
    tools.pending[value] = box.name
    tools.last_write = (box.name, value)
    tools.submitted = None
    if step["tool"] == "fill":
        rest = {k: v for k, v in step.get("fields", {}).items() if k not in tools.filled and k != name}
        filled = [*tools.filled, name]
        if rest:
            more = tools.run({"tool": "fill", "fields": rest})
            if not more.ok:
                return more
            filled += list(rest)
        return ToolResult(Outcome.OK, f"filled {filled}")
    return put


def options(
    tools: Toolbox, step: dict, result: ToolResult, verify: Callable, budget_left: Callable[[], bool], ladder: Ladder
) -> Repair:
    """Laya's ranked options for a click's target (its own ranking when it was unsure, else a fresh one over the
    visible controls), best first, never a risky or commit-like one, nor one Laya's pick check is sure does something
    else: click, verify by the step's expectation (a click without one is never guessed), undo a wrong one (a page
    that changed in place stays, and the next is verified by its type), next."""
    target, near = str(step.get("target") or ""), str(step.get("near") or "")
    wrong = tools.wrong.get((norm(target), norm(near)), set())
    if tools.last_unsure is not None and tools.last_unsure[0] == target:
        _intent, pool, choice = tools.last_unsure
    else:
        pool = [
            i
            for i in tools.look().page()
            if i.enabled and i.kind not in ("field", "text", "heading") and not i.offscreen and spot(i) not in wrong
        ]
        pool = tools.shortlist(target, pool)
        if not pool:
            ladder.note("the next-best: no control to choose from")
            return ladder.missed()
        choice = tools.ask("target", target, pool)
    ranked = [item_for(label, pool) for label, _p in choice.ranked if label != NONE]
    picks = [
        i
        for i in dict.fromkeys(i for i in ranked if i is not None)
        if spot(i) not in wrong and risk.risky("click", i.name) is None and not risk.commits(i)
    ][:MAX_TRIES]
    if not picks:
        ladder.note("the next-best: Laya ranked nothing safe to try")
        return ladder.missed()
    names: list[str] = []
    for item in picks:
        if not budget_left():
            break
        here = next((i for i in tools.look().page() if spot(i) == spot(item)), None)
        if here is None:
            names.append(f"{item.name!r} gone")
            continue
        check = tools.ask("pick_ok", target, here)
        if check.sure and check.best != DOES_IT:  # Laya is sure it does something else: never a guess worth a click
            names.append(f"{here.name!r} (sure it does something else)")
            continue
        done = pressed(tools, step, here, "Laya's next-best", verify)
        if done.ok:
            return ladder.fixed(done, f"Laya's next-best {here.name!r}")
        if done.outcome is Outcome.STUCK:  # a dead end on this screen already: never pressed again
            names.append(f"{here.name!r} (tried here already)")
            continue
        names.append(repr(here.name))
        ladder.wrong.append(here.name)
        tools.wrong.setdefault((norm(target), norm(near)), set()).add(spot(here))
        tools.note("element", target, here.name, f"tried by local repair: {done.outcome.value}")
        press = tools.last_press
        if press is not None and not unclick(tools, press, done, ladder):
            names.append("(could not undo it: stopped)")
            break
    ladder.note("Laya's options tried: " + ", ".join(names))
    return ladder.missed()


# ---------------------------------------------------------------- a read that found nothing


def collect(
    tools: Toolbox, step: dict, result: ToolResult, verify: Callable, budget_left: Callable[[], bool], ladder: Ladder
) -> Repair:
    """A collect that found nothing like what it reads (it changes nothing, so it is safe to run again): 1. wait for
    the page to change (still loading) and read again; 2. another way: scroll down and read again (not for rows a look
    named by number on the screen it saw, nor a read with a count, which scrolls on by itself). No next-best: a read has
    no other candidate."""
    if not ladder.go(WAIT):
        return ladder.missed()
    was = tools.look().fingerprint()
    changed = tools.hold(CHANGE_TIMEOUT)
    if changed is not False and tools.look().fingerprint() != was:
        again = run_once(tools, step, verify)
        if again.ok:
            return ladder.fixed(again, "the screen changed: read again")
        ladder.note(f"wait: the screen changed, read again: {again.outcome.value}")
    else:
        ladder.note("wait: nothing changed")
    if step.get("rows") or step.get("at"):
        ladder.skip(ANOTHER_WAY, "rows named by number on the screen the look saw")
        return ladder.missed()
    if step.get("count"):
        ladder.skip(ANOTHER_WAY, "a read with a count scrolls on by itself")
        return ladder.missed()
    if not budget_left():
        return ladder.missed()
    if not ladder.go(ANOTHER_WAY):
        return ladder.missed()
    before = tools.look()
    if not tools.do("scroll down (read further down the page)", tools.env.scroll, "down"):
        ladder.note("another way: could not scroll")
        return ladder.missed()
    if tools.settle(before.fingerprint(), timeout=CLICK_WAIT) is None:
        ladder.note("another way: scrolling moved nothing")
        return ladder.missed()
    again = run_once(tools, step, verify)
    if again.ok:
        return ladder.fixed(again, "scrolled down: read again")
    ladder.note(f"another way: scrolled down, read again: {again.outcome.value}")
    return ladder.missed(again)


# ---------------------------------------------------------------- a dialog in the way


def in_way(scr: Screen) -> list[Item]:
    """What blocks the step: the browser's own dialog when one is up (a notice over the page), else the page's
    blocking dialog (facts.in_the_way)."""
    own = [i for i in scr.items if i.dialog and i.browser_ui and not i.menu and not i.panel]
    return own or facts_mod.in_the_way(scr)


def dialog(tools: Toolbox, step: dict, result: ToolResult, verify: Callable, ladder: Ladder) -> Repair:
    """A dialog in the way that the dismiss rules could not close: 1. wait for it to go by itself (a notice); 2.
    another way: escape, when it has no field of its own (escape could drop what a form holds; it answers nothing);
    then the step again once none of its items is left."""
    if risky_step(step):
        ladder.note("a step that sends or commits is never redone")
        return ladder.missed()
    scr = tools.look()
    items = in_way(scr)
    keys = {(i.name, i.kind) for i in items}

    def gone() -> bool:
        return not keys & {(i.name, i.kind) for i in in_way(tools.look())}

    for way in ("wait", "escape"):
        if not items or gone():
            break
        if way == "wait":
            if not ladder.go(WAIT):
                return ladder.missed()
            tools.hold(CHANGE_TIMEOUT)
        else:
            if any(i.kind == "field" and i.enabled for i in items):
                ladder.skip(ANOTHER_WAY, "no escape for a dialog with fields")
                ladder.note("escape: not for a dialog with fields")
                return ladder.missed()
            if not ladder.go(ANOTHER_WAY):
                return ladder.missed()
            tools.do("press escape (a dialog in the way)", tools.env.press, "escape")
            tools.settle(scr.fingerprint(), timeout=CLICK_WAIT)
        if gone():
            break
        ladder.note(f"{way}: the dialog is still there")
    else:
        return ladder.missed()
    again = run_once(tools, step, verify)
    if again.ok:
        return ladder.fixed(again, "the dialog went: the step again")
    ladder.note(f"the dialog went, the step again: {again.outcome.value}")
    return ladder.missed(again)
