"""The loop and its ladder (docs/harness.md, "The loop and the ladder"): the brain plans once, code and Laya carry out each tool,
and a failing step climbs the ladder: option elimination (inside each tool) -> the next place (inside open_place; a
place found once is reused for the rest of the run) -> back to the place that worked when the run drifted
-> a brain rescue of that step only, from what is done, after which the plan carries on from its next step
(a failed step never costs the steps planned after it) -> the user. Each step declares the kind of change it causes
and code verifies it (expect.py). Before the rescue, a click whose pick was wrong (a different change, or nothing
changed), a mechanical miss climbs a local ladder of up to repair.LOCAL_TRIES tries, always in this order: a wait for a
late change, the same action another way, Laya's next-best candidates (repair.py; retry.try_next: try, verify, next);
only then the brain, at most MAX_STEP_LOOKS looks at one step (MAX_RESCUES blind rescues a task), for a step that is
wrong in the plan. A task gives up at the first give-up rule it hits (120 s of wall clock, a step tried past
MAX_STEP_TRIES times, progress.STILL steps tried with no progress, nothing untried left on the screen) and says why in
a line built by code; the task is done only when its end state is verified by type (a v1 plan's words: code, then
Laya, then once the brain).

The plan streams in, one tool per line, and each step starts as soon as its line is complete; the stream is
drained before a rescue (which sees the whole plan as tried). Stuck handling is one mechanism, the task's state graph
(progress.py): every action tried is an edge from the screen's state; an action that missed is never run again on
that state (a step, a repair's rung, a look's step), a look or rescue that proposes one is refused with no brain call,
looks and rescues are shown what was tried on the screen, and a told look that proposes nothing untried ends the task
with the list.

Every failure goes to the session notes (in RAM, for this task and its re-runs: memory.SessionNotes); their recent
lines reach the rescue prompt. The Mac map (facts.MacMap) stays in the harness; the brain gets brain.brain_facts, a
few task-relevant lines. The map's tabs are refreshed while the brain plans.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import unquote_plus

from karta.core import events
from karta.harness import content as content_mod
from karta.harness import contract as contract_mod
from karta.harness import expect as expect_mod
from karta.harness import facts as facts_mod
from karta.harness import memory as memory_mod
from karta.harness import progress as progress_mod
from karta.harness import recipes, repair, risk
from karta.harness import tools as tools_mod
from karta.harness import view as view_mod
from karta.harness.brain import SLOT_REF, SLOT_TOOLS, TOOLS, HarnessBrain, brain_facts, slot_texts, step_key, step_line
from karta.harness.decide import SHOWN
from karta.harness.retry import ordinal
from karta.harness.tell import ahead_line, done_line, failure_line, plain_readout, reason_words
from karta.harness.tools import FRONT_WORDS, TITLES_ONLY, SessionNotes, Toolbox, _site_of, affirms, norm
from karta.harness.types import HarnessEnv, MacFacts, Outcome, Screen, ToolResult

WORK_TOOLS = {"write", "type", "fill", "submit", "answer", *SLOT_TOOLS}  # steps skip-ahead never passes
CHANGES = {"write", "type", "fill", "submit", "answer"}  # the end state must be new since the last of these ran
NEEDS_PAGE = {"find", "click", "write", "type", "fill", "submit", "reach", "collect"}  # a dialog in the way is cleared first
PREPARES = {"open_place", "scroll", "wait", "back", "look"}  # steps that only get there: they run while
# the plan streams, before the one question about its key actions (ask_ahead)
# Giving up (harness.md, "Ending a task"): a task stops at the first of these it hits, says so in one line built by
# code (tell.failure_line: no brain call), and never asks the user a question to carry on past one.
BUDGET_S = 120.0  # wall clock per task, brain time included; the user's own answer time and a role-played Mac's left out
MAX_STEP_TRIES = 5  # tries of one step after it missed: each local rung that ran (repair.LOCAL_TRIES: the wait, another
# way, the next-best), each look or rescue, each re-run (back to the place, the user's reply); past it the task stops.
# Reset when the step passes
GAVE_UP_TIME, GAVE_UP_RETRIES, GAVE_UP_IDLE, GAVE_UP_LOOKS = "time", "retries", "no progress", "looks"
GAVE_UP_TRIED = "nothing untried"  # a told look or rescue proposed only what was tried on the screen (progress.py)
GAVE_UP_STOPPED = "stopped"  # the user said stop (run_task's `stop`), seen between steps
STOPPED_SAID = "Stopped as you asked."
MAX_STEP_LOOKS = 2  # looks (or page calls) at one step that missed, the steps that stand in for it included; then the
# step fails with its reason
MAX_STALLS = 2  # rescues in a row with no step succeeding in between
MAX_RESCUES = 2  # brain rescues per task: mechanical misses are repaired locally first (repair.py)
MAX_DRIFTS = 3  # returns to the last place that worked, per task
MAX_LOOKS = 8  # page calls per task (KARTA_PLAN=page: one per page, and one per step that missed) or looks (auto)
LOOK = {"tool": "look"}  # in the queue (KARTA_PLAN=auto): a look's steps ran, look at the screen they led to
END_DIALOGS = 2  # dialogs cleared once the plan ran out, before the end-state check
NOTE_LINES = 8
TAB_WAIT_S = 5.0
LOCKED = "the Mac is locked"
LOGIN_WINDOW = "loginwindow"  # the app in front while the screen is locked


def mac_locked(env, scr: Screen | None = None) -> bool:
    """The screen is locked: the env says so (mac.MacHarnessEnv.locked: the session's screen-locked flag), or the
    lock screen's own app is in front. Nothing can be done until the user unlocks it."""
    if scr is not None and scr.app == LOGIN_WINDOW:
        return True
    check = getattr(env, "locked", None)
    if not callable(check):
        return False
    try:
        return bool(check())
    except Exception:  # cannot tell: carry on
        return False


@dataclass
class TaskResult:
    task: str
    done: bool
    reason: str
    steps: list[dict] = field(default_factory=list)  # every tool run: tool, args, outcome, detail, seconds
    brain_calls: int = 0
    laya_decisions: int = 0
    confirmations: list[str] = field(default_factory=list)  # the harmful-risk questions put to the user
    actions: int = 0
    rescues: int = 0  # brain rescues
    repairs: int = 0  # steps fixed by a local repair (repair.py), with no brain call
    seconds: float = 0.0  # harness time
    timings: dict[str, float] = field(default_factory=dict)
    question: str | None = None  # what the harness would ask the user when it stopped stuck
    trace: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    brain: list[dict] = field(default_factory=list)  # per brain request: kind, format, first_step_s, total_s, tokens
    ledger: list[str] = field(default_factory=list)  # each step done (and its proof), failed or skipped
    answer: str | None = None  # what a tell step said aloud (the task was a question)
    replayed: str = ""  # a pinned task replayed: "exact", "named", "bound" or "same shape" (recipes.py)
    pinned: str = ""  # what pinning did: pinned, updated to a new version, named, forgot, a replay that failed
    speech: object = field(default=None, repr=False, compare=False)  # karta.voice.speak's Speech while it talks (.stop())
    said: str = ""  # how it ended, in one short line built by code: "Done.", or what was done and why it stopped
    gave_up: str = ""  # the give-up rule that ended it: GAVE_UP_TIME, GAVE_UP_RETRIES, GAVE_UP_IDLE, GAVE_UP_STOPPED, ...
    failed_step: str = ""  # the step it stopped on (its plan line) and that step's last reason
    failed_why: str = ""
    delegated: list = field(default_factory=list)  # delegate.Launched: Claude Code sessions it started (app.py watches)


def _never(_question: str) -> bool:
    return False


def answered(text: str) -> bool:
    """karta.harness.tell.answered: the words answer the question (never the not-found line)."""
    from karta.harness.tell import answered as found

    return found(text)


def word_answer(
    question: str, facts: list[str], hb: HarnessBrain, plain: bool, cites: list | None = None, shape: str = ""
) -> tuple[str, bool]:
    """karta.harness.tell.word_answer: (the answer's words, whether the brain was asked)."""
    from karta.harness.tell import word_answer as word

    return word(question, facts, hb, plain=plain, cites=cites, shape=shape)


def speak(text: str):
    """karta.voice.speak.speak: says `text` aloud, returns a Speech (.stop())."""
    from karta.voice.speak import speak as say

    return say(text)


def tell(tools: Toolbox, hb: HarnessBrain, step: dict) -> tuple[ToolResult, str, object]:
    """The last step of a question: the answer worded from what was collected and checked against the task's
    contract (contract.py), shown and said aloud. A list contract is read out by code, with no call, from the
    collected items of its kind (`contract.listed`: a result's own title, never an author's link, a banner or a
    label); any other answer is worded by one brain call that cites the facts, told the shape the contract asks for,
    with code's own comparison first for a comparison. When what was collected does not answer it, the page's main
    text, in document order, is the facts once more (one more call). An answer that does not meet the contract is no
    answer: the tell fails and the ladder takes it. (result, the answer, the Speech or None)."""
    c = tools.contract or contract_mod.Contract("answer", declared=False)
    slot = step["slot"]
    items = tools.slots.get(slot) or []
    if not items and tools.last_slot in tools.slots:  # the plan's name holds nothing: what was collected last
        slot = tools.last_slot
        items = tools.slots[slot]
    if not items:
        kept = ", ".join(f"{{{k}}}" for k in tools.slots) or "nothing"
        return ToolResult(Outcome.NO_DATA, f"nothing was collected as {{{step['slot']}}} (collected: {kept})"), "", None

    def unmet(why: str) -> tuple[ToolResult, str, object]:
        tools.redo.add(slot)  # collected again, it replaces what did not answer
        tools.unmet = f"{c}: {why}"
        tools.trace.append(f"  tell: the contract {c} does not hold: {why}")
        return ToolResult(Outcome.NOT_FOUND, f"the answer does not meet the contract {c}: {why}"), "", None

    pool = [i for kept in tools.slots.values() for i in kept] if c.kind == "comparison" else items
    if c.kind == "list":
        items, why = contract_mod.listed(c, items)
        if why:
            return unmet(why)
    line = contract_mod.comparison_line(c, pool) if c.kind == "comparison" else ""
    facts = [line, *[i.text() for i in pool]] if line else [i.text() for i in pool]
    calls, cites, source = hb.calls, [], f"{len(items)} item(s) of {{{slot}}}"
    try:
        plain = c.kind == "list" or (step.get("form") == "list" and c.kind == "answer")
        refused = [] if plain else tools.withheld(tools.screens_of([slot]))  # mail or messages: the user's yes first
        if refused:  # the user said no: the titles only, read out by code, and it says so
            text = f"{TITLES_ONLY.format(apps=' and '.join(refused))} {plain_readout(tools.titles(slot))}"
            used, source = False, f"the titles of {{{slot}}}"
        else:
            text, used = word_answer(step["question"], facts, hb, plain, cites, contract_mod.hint(c))
        reader = getattr(tools.env, "page_text", None)
        if not answered(text) and not plain and not refused and callable(reader) and not tools.withheld([tools.look()]):
            page = reader() or ""
            asked = (norm(step["question"]), hash(page))  # the same text for the same question: asked once a task
            if len(page) >= tools_mod.MIN_PAGE_TEXT and all(i.value != page for i in items) and asked not in tools.pages_told:
                tools.pages_told.add(asked)
                tools.trace.append(f"  tell: {{{slot}}} does not answer it: the page's main text ({len(page)} characters)")
                cites.clear()
                scr = tools.look()  # where the text was read: the question may name the list the page was opened from
                where = f"(read on the page {scr.window!r}, {tools_mod._host_of(scr.url)}) " if scr.window else ""
                facts = [where + page]
                pool = [tools_mod.Collected("the page's text", "", page, role=content_mod.TEXT)]
                text, used = word_answer(step["question"], facts, hb, False, cites, contract_mod.hint(c))
                source = "the page's main text"
            elif asked in tools.pages_told:
                tools.trace.append("  tell: the page's main text did not answer it already: not asked again")
    except Exception as error:  # the answer is still what was collected: read it out as a list
        tools.trace.append(f"  tell: the answer could not be worded ({error}): the items as they are")
        text, used = "; ".join(facts), False
    if used and hb.calls == calls:  # asked the brain outside the counted requests: it still counts
        hb.requests += 1
    text = str(text or "").strip()
    if not text:
        return ToolResult(Outcome.UNEXPECTED, "no answer could be worded from what was collected"), "", None
    why = contract_mod.answer_check(c, text, pool, cites, facts, step["question"])
    if why:  # not done, never pinned or remembered
        return unmet(why)
    tools.unmet = ""
    events.emit("tell", text=text)
    speech = None
    try:
        speech = speak(text)
    except Exception as error:  # shown and logged all the same
        tools.trace.append(f"  tell: could not speak ({error})")
    citing = f", citing fact(s) {', '.join(map(str, cites))}" if cites and used else ""
    tools.trace.append(f"  tell: answered from {source}" + (" (brain)" if used else "") + citing + f"; the contract {c} holds")
    return ToolResult(Outcome.OK, f"answered from {source}{citing}"), text, speech


def judged(tools: Toolbox, got: expect_mod.Expect, before: Screen, scr: Screen, tool: str, sent: str = ""):
    """expect.verify, with {name} in the expectation's words filled from what was collected, as a write fills it
    (one per line, or on one line): met by either."""
    tries = [got]
    if SLOT_REF.search(got.arg):
        tries = [
            expect_mod.Expect(got.kind, t) for t, missing in (tools.expand(got.arg, n) for n in (True, False)) if not missing
        ]
    press = tools.last_press if tool == "click" else None
    link = press is not None and (press.item.kind == "link" or bool(press.item.url))  # a link may lead to another site
    verdict = None
    for one in tries or [got]:
        verdict = expect_mod.verify(one, before, scr, tools.typed(), tool, sent, link)
        if verdict.ok:
            break
    return verdict


def typed_check(tools: Toolbox, step: dict, got: expect_mod.Expect, before: Screen, sent: str = "") -> ToolResult:
    """A v2 step's declared kind of change, verified by structure (expect.py) from the screen just before it to now,
    re-reading while the screen still changes: OK with its proof, NO_CHANGE when nothing changed, UNEXPECTED naming
    the change that did happen."""
    scr = tools.look()
    verdict = judged(tools, got, before, scr, step["tool"], sent)
    if not verdict.ok:
        for later in tools.rechecks(scr):
            verdict = judged(tools, got, before, later, step["tool"], sent)
            if verdict.ok:
                break
    if verdict.ok:
        return ToolResult(Outcome.OK, verdict.why())
    outcome = Outcome.NO_CHANGE if verdict.status == expect_mod.NOTHING else Outcome.UNEXPECTED
    return ToolResult(outcome, f"it ran, but {verdict.why()}")


def met_since(tools: Toolbox, step: dict, scr: Screen, since: Screen) -> bool:
    """Skip-ahead's proof under v2: the step's expectation, one with words (the task's: they name what must be
    there), is met by the state now, new since the plan was made. A kind with no words (a dialog, a list that
    changed) could be an earlier step's doing, so it never skips."""
    got = expect_mod.of(step)
    return bool(got and got.arg) and judged(tools, got, since, scr, "").ok


# The end check's reference per kind (harness.md, "The loop and the ladder": the end state). A state is judged on
# the screen now against the start ("it changed"); an event (something a step made, opened, closed or removed) from
# just before the step that should have caused it. Words of these kinds already on screen at the start prove nothing.
STATE_KINDS = ("page_on", "field_holds", "toggled", "results_for")
WORDS_KINDS = ("new_item", "item_opened", "field_holds", "results_for")
ACTS = ("click", "answer", "submit", *CHANGES)  # the steps that act on the screen


def causes(step: dict, got: expect_mod.Expect) -> bool:
    """The step could have caused the done expectation's change: it declared that kind, or one close to it (the
    same table a step's own check uses; on a submit, the form going and what it sent appearing)."""
    own = expect_mod.of(step)
    if own is None or own.kind == "none":
        return False
    close = got.kind in expect_mod.COMPATIBLE.get(own.kind, ()) or own.kind in expect_mod.COMPATIBLE.get(got.kind, ())
    sent = step.get("tool") == "submit" and {own.kind, got.kind} <= set(expect_mod.SENT)
    return own.kind == got.kind or close or sent


def old_words(got: expect_mod.Expect, start: Screen, now: Screen, typed: set[str]) -> bool:
    """The done expectation's words were on screen at the start, in the same place: they prove nothing now (a line
    left from an earlier message, a note that held the text already)."""
    if got.kind not in WORDS_KINDS or not got.arg or start is None:
        return False
    if (start.app, start.profile, facts_mod.site_of(start.url or "")) != (now.app, now.profile, facts_mod.site_of(now.url or "")):
        return False
    words = expect_mod.norm(got.arg)
    page = start.page()
    fields = any(i.kind == "field" and words in expect_mod.norm(i.value) for i in page)
    address = words in expect_mod.norm(unquote_plus(start.url or "")) or words in expect_mod.norm(start.window)
    return expect_mod._shows(page, got.arg, typed) or (got.kind in ("field_holds", "results_for") and (fields or address))


def end_refs(
    got: expect_mod.Expect, done: list[tuple[dict, ToolResult]], start: Screen, baseline: Screen, before_of: dict[int, Screen]
) -> list[Screen]:
    """The screens the done expectation is judged from, in order: from just before each step that declared its kind
    (or a close one), then the last step that acted, then the screen just before the last step that changed
    something, then the start; a state from the start first (a switch the start did not show: from the step that
    flipped it)."""
    ran = [(s, before_of[id(s)]) for s, r in reversed(done) if r.ok and id(s) in before_of]
    caused = [b for s, b in ran if causes(s, got)]
    acted = [b for s, b in ran if s.get("tool") in ACTS][:1]
    order = [start, *caused, *acted, baseline] if got.kind in STATE_KINDS else [*caused, *acted, baseline, start]
    out: list[Screen] = []
    for scr in order:
        if scr is not None and all(scr is not o for o in out):
            out.append(scr)
    return out


def reused(got: expect_mod.Expect, done: list[tuple[dict, ToolResult]]) -> ToolResult | None:
    """The last step that acted met the same expectation by its own check (nothing but reading after it): its proof
    is the end state's."""
    for step, result in reversed(done):
        if step.get("tool") in SLOT_TOOLS:
            continue
        own = expect_mod.of(step)
        if step.get("tool") not in ACTS or own is None or not result.ok or own.kind != got.kind:
            return None
        if got.arg and expect_mod.norm(got.arg) != expect_mod.norm(own.arg):
            return None
        return ToolResult(Outcome.OK, f"{got}: its last step proved it ({result.detail})")
    return None


def typed_end(
    tools: Toolbox,
    done_shows: str,
    done: list[tuple[dict, ToolResult]],
    baseline: Screen,
    start: Screen | None = None,
    before_of: dict[int, Screen] | None = None,
) -> ToolResult:
    """The end state under v2: the done expectation, judged by its kind from the right screen (`end_refs`: a state
    against the start, an event from just before the step that should have caused it; `baseline` is the screen just
    before the last step that changed something; `before_of` maps each step that ran to the screen just before it),
    re-reading while the screen still changes; the last step's own proof of the same expectation stands. Words on
    screen at the start never prove it. With no done line, the last step that acts (click, answer, submit) must have
    passed by its own expectation."""
    got = expect_mod.parse(done_shows)[0] if done_shows else None
    if got is None:
        last = next((s for s, _ in reversed(done) if s.get("tool") in ACTS), None)
        proof = next((r for s, r in reversed(done) if s is last), None)
        got_last = expect_mod.of(last) if last is not None else None
        if got_last is not None and got_last.kind != "none" and proof is not None and proof.ok:  # none proves nothing
            return ToolResult(Outcome.OK, f"its last step's expectation was met ({proof.detail})")
        return ToolResult(Outcome.UNEXPECTED, "no done expectation, and no last step proven by its kind of change")
    start = start if start is not None else baseline
    bases = end_refs(got, done, start, baseline, before_of or {})
    typed = tools.typed()
    scr = tools.look()
    if not old_words(got, start, scr, typed):
        again = reused(got, done)
        if again is not None:
            return again

    def judge(now: Screen):
        stale = old_words(got, start, now, typed)
        verdicts = [judged(tools, got, base, now, "done") for base in bases if base is start or not stale]
        return next((v for v in verdicts if v.ok), verdicts[0])

    verdict = judge(scr)
    if not verdict.ok:
        for later in tools.rechecks(scr):
            verdict = judge(later)
            if verdict.ok:
                break
    if verdict.ok:
        return ToolResult(Outcome.OK, verdict.why())
    outcome = Outcome.NO_CHANGE if verdict.status == expect_mod.NOTHING else Outcome.UNEXPECTED
    return ToolResult(outcome, f"done: {verdict.why()}")


def with_rest(fix: list[dict], rest: list[dict], replan: bool) -> list[dict]:
    """A rescue's fix for the failed step, then the plan's steps still to do. A fix that already ends with
    the rest of the plan (or replans) stands alone; one that ends with the rest's first steps is not doubled."""
    if replan or not rest:
        return fix
    said = {norm(str(s.get("text", ""))) for s in fix if s["tool"] in ("write", "type") and s.get("text")}
    rest = [s for s in rest if not (s["tool"] in ("write", "type") and norm(str(s.get("text", ""))) in said)]  # typed once
    if not rest:
        return fix
    keys, left = [step_key(s) for s in fix], [step_key(s) for s in rest]
    for n in range(min(len(keys), len(left)), 0, -1):
        if keys[-n:] == left[-n:]:
            return fix
        if keys[-n:] == left[:n]:
            return fix[:-n] + rest
    return fix + rest


def know_how(memory, task: str, scr: Screen, facts: MacFacts) -> list[str]:
    """The plan prompt's memory block (memory.brief, memory.profile): what worked before where the screen is (the
    page's exact host, else the native app in front: memory.key_of), on the hosts the task names, in the native apps it
    names and at the remembered places whose intent the task names; then the user's own details and the aliases the
    task names. Never for a browser, whose pages are every site."""
    if memory is None:
        return []
    sites = [memory_mod.host_key(m[2]) for m in facts_mod.HOST.finditer(task)]
    # an app named in the singular counts too ("a reminder": Reminders); the longest name wins ("ink pad" is not Pads)
    apps = facts_mod.named_apps(task.lower(), [a for a in facts.installed_apps if a not in facts.browsers])
    places = [memory_mod.host_key(p) if facts_mod.HOST.fullmatch(p) else p for p in memory.places_named(task) or []]
    keys = list(dict.fromkeys(k for k in [memory_mod.key_of(scr, facts.browsers), *sites, *apps, *places] if k))
    return [*(memory.brief(keys) or []), *(memory.profile(task) or [])]


def end_state(
    tools: Toolbox,
    done_shows: str,
    done: list[tuple[dict, ToolResult]],
    start: Screen,
    check: Callable[[str, Screen], ToolResult | None] | None = None,
    baseline: Screen | None = None,
) -> ToolResult:
    """The task's end state is on the page now: code first (not our typing, in a field or echoed by a text-only
    app; never words already there at the start, and words only in what is new since `baseline`, the screen just
    before the last step that changed something: a "sent" line left from an earlier message is no proof this one
    went), then Laya's outcome question over what is new; `check` (the one brain check, on what is new) only when
    Laya is unsure; never "the plan ran out"."""
    scr = tools.look()
    base = baseline or start
    expect = done_shows or next((s.get("expect", "") for s, _ in reversed(done) if s["tool"] == "submit"), "") or tools.task
    expect = tools.as_found(expect)
    typed = tools.typed()
    if facts_mod.shows_new(scr, base, expect, typed) and not facts_mod.shows(start, expect, typed):
        return ToolResult(Outcome.OK, f"{expect!r} on screen (new since the last step that changed something)")
    written = written_into(scr, start, expect, done, typed_any=bool(typed))
    if written is not None:
        return ToolResult(Outcome.OK, f"{written!r} holds {expect!r} (the new content, read back)")
    choice = tools.outcome_since(expect, scr, base)
    if choice is None:
        return ToolResult(Outcome.UNEXPECTED, f"{expect!r} is not on screen (nothing new since the last change)")
    if affirms(choice, SHOWN):
        return ToolResult(Outcome.OK, f"Laya: {choice.best}")
    if not choice.sure and check is not None:
        checked = check(expect, facts_mod.since(scr, base))
        if checked is not None:
            return checked
    return ToolResult(Outcome.UNSURE if not choice.sure else Outcome.UNEXPECTED, f"{expect!r} is not on screen")


def written_into(
    scr: Screen, start: Screen, expect: str, done: list[tuple[dict, ToolResult]], typed_any: bool = False
) -> str | None:
    """A task that creates or writes something and sends nothing (a note, a document, an event's title) is done when
    the content field it wrote into holds the expected words: that text is the result, not a draft waiting to be sent.
    Never a search or address field, never a field that held the same text at the start. Returns the field's name."""
    # typed_any: the harness did type (a step marked failed may still have put its text in)
    if any(s["tool"] == "submit" for s, _ in done) or not (
        typed_any or any(s["tool"] in ("type", "write", "fill") for s, _ in done)
    ):
        return None
    words = {w for w in re.findall(r"[a-z0-9']+", expect.lower()) if len(w) > 1}
    if not words:
        return None

    def holding(screen: Screen) -> list[tuple[str, str]]:
        out = []
        for f in screen.page():
            if f.kind != "field" or f.search or f.chrome or not f.value:
                continue
            if words <= set(re.findall(r"[a-z0-9']+", f.value.lower())):
                out.append((f.name or "the text area", " ".join(f.value.split())))
        return out

    before = set(holding(start))  # a field that already held this text (the same note open at the start) proves nothing
    fresh = [name for name, value in holding(scr) if (name, value) not in before]
    return fresh[0] if fresh else None


def dialog_first(tools: Toolbox, step: dict | None = None) -> ToolResult | None:
    """A dialog in the way of the step is cleared first, unless the step acts inside it."""
    return tools.clear_dialog(step)


def cancel_opened(tools: Toolbox, start: Screen) -> None:
    """After the user's no or stop, the dialog the task opened (one not there when the task began) is closed by its
    own cancel control (facts.dismiss_option), so nothing is left half-done; one with no cancel control is left as it
    is."""
    scr = tools.look()
    pool = facts_mod.blocking_dialog(scr) or []
    was = {(norm(i.name), i.kind) for i in start.items if i.dialog}
    if not pool or all((norm(i.name), i.kind) in was for i in pool):
        return
    cancel = facts_mod.dismiss_option(pool)
    if cancel is not None:
        tools.trace.append(f"  the user said no or stop: closing the dialog the task opened with {cancel.name!r}")
        tools.do(f"click {cancel.name!r}", tools.env.click, cancel)


def opens(step: dict, scr: Screen) -> bool:
    """An open_place of the place `scr` shows: its site (the host in `site`, or a host as its name), or its app named."""
    what = norm(" ".join(str(step.get("what") or "").split()))
    site = str(step.get("site") or "") or (what if facts_mod.HOST.fullmatch(what) else "")
    if site and scr.url:
        return _site_of(facts_mod.host_of(facts_mod.site_url(site) or site)) == _site_of(facts_mod.host_of(scr.url))
    return not site and bool(what) and what in (norm(scr.app), norm(scr.app).removesuffix(" app"))


def drop_done(steps: list[dict], tools: Toolbox) -> list[dict]:
    """A rescue starts from what is done: an open_place whose place was found and is still in front is not redone."""
    out = []
    for step in steps:  # anywhere in the fix: a place found once is done (open_place brings it back if it wandered)
        place = (
            tools.found_place(" ".join(str(step["what"]).split()[:6]), str(step.get("site") or ""))
            if step["tool"] == "open_place"
            else None
        )
        if place is not None and tools.showing(place, tools.look()):
            tools.trace.append(f"  already open: {place.name} (skipping {step['what']!r})")
            continue
        out.append(step)
    return out


CONTENT_KINDS = ("row", "link", "text", "heading", "image")  # an item named by what it holds: never a way to keep
ENTRY = {"click, paste": "pasted after a click", "click, keys": "typed key by key", "tab": "a chip, by Tab"}
EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
NAME = r"[A-Z][\w'-]+(?: [A-Z][\w'-]+){0,2}"
ASKED = r"asked (['\"])(?:(?!\1).)*\byour\b(?:(?!\1).)*\b{what}\b(?:(?!\1).)*\1, said: ({value})"
OWN = (  # the user's own details, only as they gave them: "my ... email is x", "my name is X", or a reply to "your ..."
    ("email", re.compile(rf"\bmy\b(?:\s+\w+){{0,2}}?\s+e-?mail(?:\s+address)?(?:\s+is|\s*:)?\s+({EMAIL})", re.I)),
    ("email", re.compile(ASKED.format(what="e-?mail", value=EMAIL), re.I)),
    ("name", re.compile(rf"\b(?i:my (?:full )?name is)\s+({NAME})")),
    ("name", re.compile(ASKED.replace("({value})", "({value})(?:\\)|$)").format(what="(?i:name)", value=NAME))),
)


def own_details(task: str) -> list[tuple[str, str]]:
    """The user's own e-mail addresses and names that `task` gives (with the user's replies to a question, as the
    loop appends them): never read off a screen."""
    out = []
    for key, pattern in OWN:
        out += [(key, m.groups()[-1]) for m in pattern.finditer(task)]
    return list(dict.fromkeys(out))


def change_of(before: Screen, after: Screen, task: str) -> str:
    """The kind of change a step caused, in words built by code, never content: another site, another page of the site
    (its first path segment when that is a plain word the task does not hold), a dialog, a new focused field, a new
    row; "" when none of these."""
    was, now = recipes._address(before.url), recipes._address(after.url)
    if now and now != was:
        host, _, path = now.partition("/")
        if not was or _site_of(host) != _site_of(was.partition("/")[0]):
            return f"opened {_site_of(host)}"
        first = path.split("/")[0]
        said = set(re.findall(r"[a-z]+", task.lower()))
        plain = re.fullmatch(r"[a-z][a-z_-]{1,23}", first) and not set(re.split(r"[_-]", first)) & said
        return f"opened page /{first}" if plain else "opened another page"
    if facts_mod.dialogs(after) > facts_mod.dialogs(before):
        return "a dialog opened"
    new = facts_mod.new_items(after, before)

    def fields(scr: Screen, name: str) -> int:
        return sum(i.kind == "field" and i.name == name for i in scr.items)

    if any(i.kind == "field" and i.focused and fields(after, i.name) > fields(before, i.name) for i in new):
        return "a new focused field"
    if any(i.kind == "row" for i in new):
        return "a new row"
    return ""


def way(
    tools: Toolbox,
    step: dict,
    result: ToolResult,
    fixed,
    before: Screen,
    after: Screen,
    picks: list[tuple],
    ways: dict[tuple[str, str, str], str],
    last_field: tuple[str, str, str] | None,
    pitfalls: list[tuple[str, str, str, str]],
    wrong: list[str],
    fixes: dict | None,
) -> tuple[str, str, str] | None:
    """A step passed: how each control or field it picked worked ("tool|text went in|sent by|the change": a click and
    the change it caused, a write and how its text went in, a submit adding how it sent the field written last and
    the change), and the pitfalls it shows ("not X but Y": a pick found wrong before the one that worked, or the
    target of the step this fix stands in for). Kept by memory only once the task ends proven (remember). Returns the
    field written last."""
    tool = step["tool"]
    if tool == "submit" and last_field in ways and tools.submitted:
        kind, entry = ways[last_field].split("|")[:2]
        sent = "Return" if tools.submitted == "return" else f'click "{tools.submitted}"'
        ways[last_field] = f"{kind}|{entry}|{sent}|{change_of(before, after, tools.task)}"
        return None
    for where, intent, name, *_ in picks:
        if next((i.kind for i in before.items if i.name == name), "") in CONTENT_KINDS:
            continue
        key = (where, intent, name)
        if tool in ("write", "fill"):
            entry = "a chip" if "as a token" in result.detail else "pasted"
            if fixed is not None and fixed.ok:
                entry = next((v for k, v in ENTRY.items() if fixed.how.startswith(k)), entry)
            ways[key] = f"{tool}|{entry}||"
            last_field = key
        elif tool == "click":
            ways[key] = f"click|||{change_of(before, after, tools.task)}"
        pitfalls += [(where, intent, w, name) for w in wrong]
        aim = str((fixes or {}).get("target") or (fixes or {}).get("where") or "")
        if aim and not any(p[:2] == (where, norm(aim)) for p in pitfalls):
            pitfalls.append((where, norm(aim), aim, name))
    return last_field


def remember(
    memory,
    tools: Toolbox,
    learned: list[tuple[str, str]],
    ways: dict | None = None,
    pitfalls: list[tuple] = (),
    proof: str = "",
) -> None:
    """Only once the task's declared contract was checked by code and met (`proof`: that contract, kept on each row;
    the rule pinning uses): the places that worked (a site in a browser and profile, or an app; never a tab's position), the
    names that turned out to be someone else's, the items each intent meant (never chrome, a bare site name, a name
    read off the pixels, None or a plan's description: memory.unfit_element) with how they worked (`ways`), the
    pitfalls ("not X but Y"), the know-how a rescue found, each under its site or native app (know-how under one the
    run acted on); and the user's own details the task gave, the browser and profile used and the
    default browser. (Failures live in the session notes; a remembered fact that failed was deleted where it
    failed.)"""
    if memory is None or not proof:
        return
    memory.proving = proof
    try:
        _remember(memory, tools, learned, ways, pitfalls)
    finally:
        memory.proving = ""


def _remember(memory, tools: Toolbox, learned: list[tuple[str, str]], ways: dict | None, pitfalls) -> None:
    # a web page's facts belong to its exact host: a key that is a registrable domain becomes the one host of it the
    # run read or opened, and is kept nowhere when the run saw several (they would mix: a mail host and a search page)
    screens = [*getattr(tools, "seen", {}).values(), *getattr(tools, "left_behind", {}).values()]
    seen = {memory_mod.host_key(u) for u in [*(s.url for s in screens), *(p.url for p in tools.places.values())] if u}

    def exact(where: str) -> str:
        hosts = [h for h in seen if facts_mod.site_of(h) == where] if "." in where and " " not in where else []
        return where if not hosts else hosts[0] if len(hosts) == 1 else ""

    for what, place in tools.places.items():
        memory.place_result(what, place.name, True)
        if place.url:
            memory.add_profile("browser in use", place.app + (f", profile {place.profile}" if place.profile else ""))
    for said, (where, meant) in tools.aliases.items():
        if exact(where):
            memory.remember_alias(said, meant, exact(where))
    for where, intent, name, accessible, chrome in tools.elements:
        how = (ways or {}).get((where, intent, name), "")
        why = memory.element_result(exact(where), intent, name, True, accessible=accessible, chrome=chrome, how=how)
        if why:
            tools.trace.append(f"  memory: {intent!r} -> {name!r} not kept ({why})")
    for where, intent, wrong, right in pitfalls:
        memory.add_pitfall(exact(where), intent, wrong, right)
    # know-how belongs where the run acted (picked a control, opened a place), never to an app it only started in
    opened = [memory_mod.host_key(p.url) if p.url else p.app for p in tools.places.values()]
    acted = list(dict.fromkeys(k for k in [*(exact(e[0]) for e in tools.elements), *opened] if k))
    for where, fact in learned:
        where = exact(where)
        memory.add_know_how(where if where in acted or not acted else acted[-1], fact)
    for key, value in own_details(tools.task):
        memory.add_profile(key, value)
    if tools.default_browser:
        memory.add_profile("default browser", tools.default_browser)


def mac_of(facts) -> tuple[MacFacts, object | None]:
    """MacFacts, and the Mac map when one was given (facts.MacMap, or a callable returning either: a cache the caller
    keeps and refreshes)."""
    obj = facts() if callable(facts) and not isinstance(facts, MacFacts) and not hasattr(facts, "facts") else facts
    if isinstance(obj, MacFacts):
        return obj, None
    inner = getattr(obj, "facts", None)
    if isinstance(inner, MacFacts):
        return inner, obj
    raise TypeError(f"run_task needs MacFacts or a MacMap, not {type(obj).__name__}")


COUNTERS = (("reads", "reads"), ("read_s", "read_seconds"), ("wait_s", "wait_seconds"), ("act_s", "act_seconds"))


def env_clock(env) -> dict[str, float]:
    """The env's own time counters (mac.MacHarnessEnv's): full screen reads and their seconds, seconds waiting for
    the screen, seconds acting. Empty for an env that keeps none (the role-played Mac, test fakes)."""
    if not isinstance(getattr(env, "read_seconds", None), (int, float)):
        return {}
    return {key: float(getattr(env, name, 0) or 0) for key, name in COUNTERS}


def spent(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    """What the env's counters say happened between two env_clock readings."""
    return {
        key: int(after[key] - before.get(key, 0)) if key == "reads" else round(after[key] - before.get(key, 0.0), 2)
        for key in after
    }


def run_task(
    task: str,
    env: HarnessEnv,
    laya,
    brain,
    facts,
    memory=None,
    confirm: Callable[[str], bool] | None = None,
    *,
    ask: Callable[[str], str] | None = None,
    budget_s: float = BUDGET_S,
    clock: Callable[[], float] = time.perf_counter,
    notes=None,
    tabs: list | None = None,
    refresh: Callable | None = None,
    pins=None,
    session=None,
    stop: Callable[[], bool] | None = None,
) -> TaskResult:
    """Carry out `task` on `env`. `brain`: anything with .stream_lines(system, user) or .json(system, user) (or a
    HarnessBrain); `laya`: karta.harness.decide.Laya; `facts`: MacFacts, a facts.MacMap computed once by the caller,
    or a callable returning one; `confirm(question) -> bool` before anything hard to undo or a permission;
    `ask(question) -> reply` for the last rung of the ladder (None: stop and report the question); `notes`: the
    session's SessionNotes (default: memory's, else a fresh one); `tabs`: the open tabs when no map gives them;
    `refresh(map)`: re-reads a map's tabs (default facts.refresh_tabs), run while the brain plans; `pins`: the
    recipes.RecipeBook of pinned tasks (default: memory's pins.json; none without either); `session`: the
    session.Session of recent tasks (its block goes to the plan when the task points back; the task is recorded in it
    at the end); `stop() -> bool`: the user said stop, checked between steps: the task ends GAVE_UP_STOPPED, closes a
    dialog it opened, and is never pinned."""
    hb = brain if isinstance(brain, HarnessBrain) else HarnessBrain(brain)
    book = recipes.book_of(pins, memory)
    if book is not None:
        book.typed = hb.v2  # a recipe in the old words format is never replayed, offered or pinned by a typed run
    said = book.command(task) if book is not None else None
    if said is not None:  # naming or forgetting a pinned task: no Mac, no brain
        events.emit("pinned", text=said[1], short="")
        return TaskResult(task=task, done=said[0], reason=said[1], trace=[f"pin: {said[1]}"], pinned=said[1])
    hit = book.match(task) if book is not None else None
    pin_notes = []
    if hit is not None:
        task = hit.command  # a named routine runs as the command it was pinned from
        if hit.recipe.benched:
            pin_notes.append(f"pinned {hit.recipe.id} failed {hit.recipe.fails} times in a row: planned afresh")
            hit = None
    calls0, brain_s0, stats0 = hb.calls, hb.seconds, len(hb.stats)
    started, sim0, clock0 = clock(), float(getattr(env, "sim_seconds", 0.0)), env_clock(env)

    def sim_s() -> float:
        return float(getattr(env, "sim_seconds", 0.0)) - sim0

    def env_s() -> float:  # the real Mac counts its reads and waits; the role-played one its role-player's time
        now = env_clock(env)
        if "read_s" in now:
            return now["read_s"] - clock0["read_s"] + now["wait_s"] - clock0["wait_s"]
        return sim_s()

    waited_on_user = 0.0  # the user's own time answering a question is not the task's

    def timed(fn: Callable | None) -> Callable | None:
        if fn is None:
            return None

        def asking_the_user(question: str):
            nonlocal waited_on_user
            t = clock()
            try:
                return fn(question)
            finally:
                waited_on_user += clock() - t

        return asking_the_user

    confirm, ask = timed(confirm), timed(ask)

    def harness_s() -> float:  # the wall clock since the task began, brain time included
        return clock() - started - sim_s() - waited_on_user

    def stopped() -> bool:
        return stop is not None and bool(stop())

    def in_time() -> bool:  # the budget, and no stop: checked before each try and each brain call
        return harness_s() <= budget_s and not stopped()

    mac, mac_map = mac_of(facts)
    if notes is None:
        notes = getattr(memory, "notes", None) or SessionNotes()
    tools = Toolbox(
        task, env, laya, mac, memory, confirm or _never, notes, tabs if tabs is not None else getattr(mac_map, "tabs", None)
    )
    tools.signed_in = list(getattr(mac_map, "signed_in", None) or [])
    tools.brain, tools.can_ask = hb, confirm is not None  # compose's one call; consent asked only of a user
    tools.recent_repo = session.last_repo() if session is not None else ""  # a delegate step with no repo: this one
    trace = tools.trace
    trace.extend(pin_notes)
    graph = tools.progress = progress_mod.Progress()  # the task's state graph: which action was tried where (progress.py)
    start = None if mac_locked(env) else tools.look()
    if start is None or mac_locked(env, start):  # stop at once: no plan, no waiting out the budget
        trace.append(f"stopped: {LOCKED}")
        return TaskResult(
            task=task,
            done=False,
            reason=f"stopped: {LOCKED}",
            trace=trace,
            question="Unlock the Mac, then ask again.",
            said=failure_line([], locked=True),
        )
    # the browser the user is using: the one in front at the start (places are ranked by it), and the default one
    graph.visit(start)
    tools.using = start.app if start.app in mac.browsers else None
    tools.default_browser = getattr(mac_map, "default_browser", None)
    know = know_how(memory, task, start, mac)
    asked_task = task  # as said: what the session records
    recent = session.listed(task) if session is not None else []  # the session's refs the task points at
    if session is not None:
        session.bind(tools, recent)  # {@n} is a collected name from here on: check_slots and expand take it
    hb.given = session.addresses() if session is not None else ""
    recent_lines = session.lines(task, recent) if recent else []
    mac_text = brain_facts(task, mac, tools.tabs)

    def note_lines() -> list[str]:
        lines = getattr(notes, "lines", None)
        return list(lines(NOTE_LINES, app=tools.front)) if callable(lines) else []

    done: list[tuple[dict, ToolResult]] = []
    looks = 0  # page calls (KARTA_PLAN=page)
    from_view: set[int] = set()  # the queued steps whose #n targets came from the view of the page they were planned on

    def page_call(scr: Screen, failed: str = "", offers=(), use=None):
        """A page call on the screen now (KARTA_PLAN=page): the task, what is done, memory's block for where it is,
        what was collected, the step that missed (if any) with what was tried on this screen, and the numbered view of
        `scr`."""
        nonlocal looks
        looks += 1
        failed = failed + graph.block(scr) if failed else failed
        seen_view = view_mod.build(tools.for_brain(scr), tools.task, tools.typed())
        trace.append(
            f"page call {looks}: {len(seen_view.items)} of {seen_view.total} items" + (f"; failed: {failed}" if failed else "")
        )
        return hb.page_plan(
            tools.task, mac_text, seen_view, (know_how(memory, tools.task, scr, mac) or know) + recent_lines, note_lines(),
            [step_line(s) for s, _ in done], {k: len(v) for k, v in tools.slots.items()}, failed, offers, use,
        )  # fmt: skip

    def look_call(scr: Screen, why: str, rest: list[dict]):
        """A look (KARTA_PLAN=auto) at the screen now: the task, what is done, memory's block for where it is, what was
        collected, `why` (the step that missed, what it expected and what changed; or a place the plan never named),
        what was tried on this screen (progress.py), the plan's steps still to do, and the numbered view of `scr`."""
        nonlocal looks
        looks += 1
        why += graph.block(scr)
        seen_view = view_mod.build(tools.for_brain(scr), tools.task, tools.typed())
        trace.append(f"look {looks}: {len(seen_view.items)} of {seen_view.total} items; {why.splitlines()[0]}")
        return hb.look(
            tools.task, mac_text, seen_view, (know_how(memory, tools.task, scr, mac) or know) + recent_lines, note_lines(),
            [step_line(s) for s, _ in done], {k: len(v) for k, v in tools.slots.items()}, why,
            [step_line(s) for s in rest if s.get("tool") in TOOLS],
        )  # fmt: skip

    def after_look(steps: list[dict], rest: list[dict], new) -> list[dict]:
        """A look's steps, then: the plan's rest (continue, or no last line: a fix), a look at the screen they lead
        to before the rest (look), or nothing more (done or replan: they replace the rest)."""
        if new.done_shows or new.replan:
            return steps
        if new.look and steps:
            return [*steps, dict(LOOK), *rest]
        return with_rest(steps, rest, False)

    named: set[str] = set()  # the places the plan names (open_place, page_on) or reached: no look for them (auto)

    def name_places(steps) -> None:
        for step in steps:
            if step.get("tool") == "open_place":
                named.update(norm(str(step.get(k) or "")) for k in ("what", "site"))
            got = expect_mod.of(step)
            if got is not None and got.kind == "page_on" and got.arg:
                named.add(norm(got.arg))
        named.discard("")

    def place_here(scr: Screen) -> str:
        """Where a screen is, as a place: a web page's site, else its app ("" for a browser with no page, a system
        prompt or the lock screen: never a place of the task's)."""
        if scr.url:
            return _site_of(facts_mod.host_of(scr.url)) or ""
        if scr.app in mac.browsers or facts_mod.system_prompt(scr) or scr.app == LOGIN_WINDOW:
            return ""
        return norm(scr.app)

    def unnamed(scr: Screen) -> str:
        """The place `scr` shows when the plan never named it (nor did the task start there): "" when it did."""
        here = place_here(scr)
        if not here or here in named:
            return ""
        label = here.split(".")[0]
        for name in named:
            if facts_mod.HOST.fullmatch(name) and _site_of(name) == here:
                return ""
            squashed = name.replace(" ", "")
            if squashed and (squashed == label or name in here or (not scr.url and here in name)):
                return ""
        return here

    named.add(place_here(start))
    named.discard("")

    if hit is not None:  # a pinned path: its steps run through the same loop and checks, with no plan call
        stream = recipes.Replay(hit)
        trace.append(f"pinned: {hit.how} replay of {hit.recipe.id} v{hit.recipe.version} ({len(hit.steps)} steps)")
        events.emit("replay", text=f"{hit.how} replay of a pinned task, {len(hit.steps)} steps")
    else:
        offers = book.offer(task) if book is not None else []
        use = (lambda text: book.bind_use(text, task)) if offers else None
        if hb.page:  # the first page call is the one that understands the request
            stream = page_call(start, offers=offers, use=use)
        else:
            stream = hb.stream_plan(
                task, mac_text, tools.for_brain(start), know + recent_lines, note_lines(), offers, use, tuple(tools.slots)
            )
    first = stream  # the plan a `use:` line bound a pinned task in
    expected: list[tuple[dict, str]] = list(zip(hit.steps, hit.checks, strict=True)) if hit is not None else []
    reader: threading.Thread | None = None
    if mac_map is not None and tabs is None:  # the tabs are re-read while the brain plans
        redo = refresh or getattr(facts_mod, "refresh_tabs", None)
        if callable(redo):

            def read_tabs() -> None:
                try:
                    tools.tabs = list(getattr(redo(mac_map) or mac_map, "tabs", None) or [])
                except Exception as error:  # the old tabs stand
                    trace.append(f"  open tabs not re-read: {error}")

            reader = threading.Thread(target=read_tabs, daemon=True)
            reader.start()
            # switching to an open tab re-reads them first: a tab is found by its address, never a position from before
            tools.read_tabs = lambda: getattr(redo(mac_map) or mac_map, "tabs", None) or []

    source = iter(stream)
    live = True
    queue: list[dict] = []
    front = [True] if FRONT_WORDS.search(task) and hit is None else []  # the task is about what is in front now
    tells: list[dict] = []  # the plan's and the looks' tells: a run with no contract line that holds one is a question
    pledged: contract_mod.Contract | None = None  # the contract the task's first plan declared: never changed after
    pressed: list[str] = []  # the content items the steps opened (a row, a link): what an opened() contract shows
    flipped: list[str] = []  # the controls a step proved it toggled: what a changed() contract may read
    done_shows, question = "", None
    first_step_at: float | None = None

    def finish_plan() -> None:
        nonlocal done_shows, question
        plan = stream.plan
        done_shows = (plan.done_shows or done_shows) if hb.page else (done_shows or plan.done_shows)  # a page's own done
        question = question or plan.ask or None
        trace.append(
            f"plan: {json.dumps(plan.steps, ensure_ascii=False)} done_shows={plan.done_shows!r}"
            + (f" ask={plan.ask!r}" if plan.ask else "")
            + (f" errors={plan.errors}" if plan.errors else "")
            + (f" ignored as prose={plan.prose}" if plan.prose else "")
        )

    def pull() -> bool:
        """One more step from the streaming plan into the queue; False once the plan is complete."""
        nonlocal live, first_step_at
        if not live:
            return False
        step = next(source, None)
        if step is None:
            live = False
            finish_plan()
            return False
        if first_step_at is None:
            first_step_at = harness_s()
        if getattr(stream, "view", None) is not None and stream.plan.pinned is None and (step.get("at") or step.get("rows")):
            from_view.add(id(step))  # its number was read off this page's view: stale once the page changes
        if front and step.get("tool") == "open_place":
            front.clear()  # only an open_place before any other: the first may be the place in front, the rest move on
            if opens(step, start):
                trace.append(f"  plan line: {step_line(step)}: the place in front already ('this' in the task): not opened")
                return pull()
        queue.append(step)
        if step.get("tool") == "tell":
            tells.append(step)
        name_places([step])
        trace.append(f"  plan line: {step_line(step)}")
        events.emit("plan", text=step_line(step))
        return True

    def upcoming() -> list[dict]:
        if len(queue) < 2:
            pull()
        return queue[1:]

    tools.upcoming = upcoming

    def begin(new) -> None:
        """A new page plan streams in: it replaces the queue (planned on the screen its view shows)."""
        nonlocal stream, source, live, planned_on
        stream, source, live = new, iter(new), True
        queue.clear()
        from_view.clear()
        planned_on = new.view.screen

    def replaying() -> bool:
        """The steps are a pinned path's (an exact or named replay, or a `use:` line): identities, not a page's numbers."""
        return hit is not None or getattr(first.plan, "pinned", None) is not None

    def new_page(before: Screen, scr: Screen) -> bool:
        """The step landed on another page: another app, web address or window, or a dialog opened or closed."""
        return view_mod.page_key(scr) != view_mod.page_key(before) or facts_mod.dialogs(scr) != facts_mod.dialogs(before)

    trail: list[tuple[dict, dict]] = []  # the ✓ steps in the order they ran, each with its proof (for pinning)
    ledger: list[str] = []
    planned_on = start  # the screen the steps in the queue were planned on
    last_before = start  # the screen just before the last step ran: what appeared since is that step's doing
    before_of: dict[int, Screen] = {}  # each step that passed: the screen just before it (the end check's references)
    baseline = start  # the screen just before the last step that changed something: the end state is new since

    def mark(sign: str, step: dict, why: str) -> None:
        ledger.append(
            f"{sign} {step_line(step) if step.get('tool') in TOOLS else step.get('tool')}" + (f"  ({why})" if why else "")
        )
        events.emit("ledger", text=ledger[-1])

    log: list[dict] = []
    learned: list[tuple[str, str]] = []
    # what worked and how, for memory once the task ends proven: (site, intent, name) -> its way (see `way`), the
    # field written last (a submit after it adds how it was sent), "not X but Y" pitfalls, the step each fix stands in for
    ways: dict[tuple[str, str, str], str] = {}
    last_field: tuple[str, str, str] | None = None
    pitfalls: list[tuple[str, str, str, str]] = []
    fixing: dict[int, dict] = {}
    picks0: list[tuple] = []
    rescues = repairs = stalls = asked = drifts = end_dialogs = 0
    local: dict[str, list[str]] = {}  # per failed step: the local repairs tried that missed (for its rescue)
    rescued: list[dict] = []  # the steps rescues sent: an open_place among them never moves (Toolbox.from_rescue)
    settled: tuple[str, str | None] | None = None
    checked = by_brain = False
    success, reason = False, ""
    answer, speech = "", None
    # giving up: retries per step (a rescue's steps count their own), the actions since a step passed
    gave_up, brain_stop = "", ""
    retries: dict[str, int] = {}
    last_fail: tuple[dict, ToolResult] | None = None
    sending = ""  # the text a submit sends (the last write before it), for its typed check

    def key_of(step: dict) -> str:
        return step_key(step) if step.get("tool") in TOOLS else str(step.get("tool"))

    step_looks: dict[str, int] = {}  # per step that missed (the one its fixes stand in for): the looks spent on it
    looking_for = ""  # the step the last look was for: a look at the screen its steps led to counts for it too

    def root_key(step: dict) -> str:
        """The step a fix stands in for, through every rescue's fix: looks at it and its fixes count together."""
        seen_ids: set[int] = set()
        while id(step) in fixing and id(step) not in seen_ids:
            seen_ids.add(id(step))
            step = fixing[id(step)]
        return key_of(step)

    def passed(step: dict, progress: bool = True) -> None:
        """A step passed (✓, its expectation met: progress; a ↷ is not progress): its retries start again."""
        if progress:
            graph.moved()
        retries.pop(key_of(step), None)

    def retried(step: dict, n: int = 1) -> int:
        key = key_of(step)
        retries[key] = retries.get(key, 0) + n
        return retries[key]

    def chain(step: dict) -> int:
        """The retries of a step and of the steps its rescue stands in for: past the cap, the user is not asked to go
        on (each rescue's step still counts its own retries for its ladder)."""
        total, seen_ids = 0, set()
        while step is not None and id(step) not in seen_ids:
            seen_ids.add(id(step))
            total += retries.get(key_of(step), 0)
            step = fixing.get(id(step))
        return total

    def told_of(step: dict) -> str:
        """What a tell tells from (the items collected under its name): telling from new items is a new action."""
        return json.dumps([c.text() for c in tools.slots.get(step.get("slot")) or []]) if step.get("tool") == "tell" else ""

    def idle() -> bool:  # progress.STILL steps tried with no new state and no expectation met
        return graph.stalled()

    def give_up(rule: str, step: dict | None = None) -> str:
        nonlocal gave_up
        gave_up = rule
        if rule == GAVE_UP_IDLE:
            why = f"gave up: no new screen and no step passed in the last {graph.still} tries"
        else:
            line = step_line(step) if step and step.get("tool") in TOOLS else str((step or {}).get("tool"))
            why = f"gave up: {line} was retried {retries.get(key_of(step or {}), 0)} times"
        trace.append(why)
        return why

    def brain_check(expect: str, scr: Screen) -> ToolResult | None:
        """The one end-of-task brain check."""
        nonlocal checked, by_brain
        if checked:
            return None
        checked = True
        yes, why = hb.end_check(tools.task, expect, tools.for_brain(scr), sorted(tools.typed()))
        trace.append(f"  brain end check: {'yes' if yes else 'no' if yes is False else 'unreadable'} ({why})")
        if yes is None:
            return None
        by_brain = bool(yes)  # a done only the brain saw is never pinned
        return ToolResult(Outcome.OK if yes else Outcome.UNEXPECTED, f"brain check: {why}")

    def verify(step: dict, result: ToolResult) -> ToolResult:
        """A step that ran OK is done once its declared kind of change is verified (expect.py); a step that declares
        none is proven by its tool. A pinned step with neither goes where it went before."""
        got = expect_mod.of(step)
        if not result.ok and result.outcome is Outcome.NO_CHANGE and got is not None:
            return ToolResult(Outcome.NO_CHANGE, f"{result.detail}: nothing changed (expected {got})")
        if not result.ok:
            return result
        press = tools.last_press if step["tool"] == "click" else None  # a retried click: the screen before that click
        if got is not None:
            checked = typed_check(tools, step, got, press.before if press is not None else last_before, sending)
            if not checked.ok:
                return checked
            result = ToolResult(Outcome.OK, "; ".join(x for x in (result.detail, checked.detail) if x))
        at = next((a for s, a in expected if s is step), "")
        if at and at not in recipes._address(tools.look().url):
            return ToolResult(Outcome.UNEXPECTED, f"it ran, but the page is not at {at!r}, where this pinned step led")
        return result

    def drifted() -> str | None:
        """A login page, or the app or profile changed between steps (not by the step that worked last)."""
        scr = tools.look()
        why = tools.drift(scr)
        if why is None or why.startswith("a login page"):
            return why
        return why if (scr.app, scr.profile) != settled else None

    def took_look(looked, rest: list[dict]) -> bool:
        """A look no step missed into (KARTA_PLAN=auto: a place the plan never named, a new page under steps aimed by
        number, the screen a look's steps led to): its steps run from the screen it saw, then the plan's rest as
        after_look says; numbered steps planned on an earlier screen are dropped. False when the brain stopped."""
        nonlocal reason, brain_stop, question, done_shows, planned_on
        steps = drop_done(list(looked), tools)
        new = looked.plan
        trace.append(
            f"  look: {json.dumps(steps, ensure_ascii=False)}"
            + (f" stop={new.stop!r}" if new.stop else "")
            + (f" done_shows={new.done_shows!r}" if new.done_shows else "")
            + (" then the plan's rest" if new.carry or not (new.done_shows or new.replan) else "")
            + (f" ignored as prose={new.prose}" if new.prose else "")
        )
        if new.learned and tools.here:
            learned.append((tools.here, new.learned))
        question = new.ask or question
        if new.done_shows:
            done_shows = new.done_shows
        if new.stop or (new.ask and not steps):
            reason, brain_stop = f"stopped: {new.stop or new.ask}", new.stop
            return False
        rest = [s for s in rest if id(s) not in from_view]
        name_places(steps)
        rescued.extend(steps)
        from_view.clear()
        from_view.update(id(s) for s in steps if s.get("at") or s.get("rows"))
        queue[:] = after_look(steps, rest, new)
        planned_on = looked.view.screen
        return True

    def pinned_as(step: dict) -> dict:
        """The step as a recipe keeps it (KARTA_PLAN=auto): a blind click or answer with the identity of the control
        it pressed (view.identity, as a page plan's #n), so a replay finds that control again with no Laya pick.
        Never content (a row, link or text: it changes, and may be the user's)."""
        press = tools.last_press if step.get("tool") in ("click", "answer") else None
        if not hb.auto or step.get("at") or press is None or press.item.kind in (*CONTENT_KINDS, "other"):
            return step
        return {**step, "at": view_mod.identity(press.item, press.before)}

    said_no, asked_ahead = "", confirm is None  # the one question before acting (ask_ahead), once a task

    def ask_ahead() -> str:
        """The one question before acting (harness.md, Safety): before the first step that acts (anything but getting
        there: PREPARES), the plan is read whole, and when it has key actions (send, post, pay or buy, add to cart,
        delete, star or follow, run a command) the user is asked once, in plain words; a yes covers exactly those steps
        (Toolbox.approved), and anything risky outside them still asks when it comes (Toolbox.gate). The question when
        the user said no, else ""."""
        while pull():
            pass
        keys, wrote = {}, ""
        for step in queue:
            kind = risk.key_action(step, wrote)
            if step.get("tool") == "delegate" and tools.resolve_repo(step)[0] is None:
                kind = None  # no such repo: the step fails as it runs, before anything is asked
            if kind:
                keys[id(step)] = kind
            if step.get("tool") == "write":
                wrote = str(step.get("where") or "")
            elif step.get("tool") == "fill" and isinstance(step.get("fields"), dict) and step["fields"]:
                wrote = str(list(step["fields"])[-1])
        if not keys:
            return ""
        question_ahead = ahead_line(queue, keys, tools.repos)
        tools.confirmations.append(question_ahead)
        yes = bool(confirm(question_ahead))
        trace.append(f"  confirm up front {question_ahead!r} -> {'yes' if yes else 'no'}")
        if yes:
            tools.approved.update(keys)
        return "" if yes else question_ahead

    def contract_now() -> contract_mod.Contract:
        """The task's contract: the one its first plan declared (fixed once read: a look's or rescue's lines never
        change it), else one derived by code from the plan (never learned from)."""
        nonlocal pledged
        if pledged is None and getattr(first.plan, "contract", ""):
            pledged = contract_mod.parse(first.plan.contract)[0]
            if pledged is not None:
                trace.append(f"  contract: {pledged}")
        return pledged or contract_mod.derived(tells, task, done_shows)

    def outcome(c: contract_mod.Contract) -> ToolResult:
        """An outcome contract, proven by structure on the screen now: created by a new item with its name or a
        list's count rising, deleted by it gone or the count falling, changed by the control's own state, opened by the
        object's own view in front, sent by the send's own proof."""
        now = tools.look()
        refs = [s for s in (start, *before_of.values()) if s is not None]

        def judge(text: str) -> ToolResult:
            return typed_end(tools, text, done, baseline, start, before_of)

        def met(why: str) -> ToolResult:
            return ToolResult(Outcome.OK, f"the contract {c} holds: {why}")

        if c.kind in ("created", "deleted"):
            kind, up = ("new_item", True) if c.kind == "created" else ("item_gone", False)

            def listed(scr: Screen) -> str:  # the thing named where no earlier read had it, or a list's count moved
                why = contract_mod.appeared(refs, scr, c.name) if up and c.name else ""
                return why or next((w for ref in refs if (w := contract_mod.counted(ref, scr, up))), "")

            if listed(now):
                return met(listed(now))
            for text in ([f"{kind}({c.name})"] if c.name else []) + [kind]:
                end = judge(text)
                if end.ok:
                    return met(end.detail)
            for later in tools.rechecks(now):
                if listed(later):
                    return met(listed(later))
            what = f"{c.of or 'item'} {c.name!r}".strip() if c.name else (c.of or "item")
            return ToolResult(
                Outcome.UNEXPECTED,
                f"the contract {c} does not hold: no {what} {'new' if up else 'gone'}, no list's count "
                f"{'rose' if up else 'fell'}",
            )
        if c.kind == "changed":
            for later in (now, *tools.rechecks(now)):
                why = contract_mod.state(later, c.name, c.value, flipped)
                if why:
                    return met(why)
            return ToolResult(
                Outcome.UNEXPECTED, f"the contract {c} does not hold: no control named {c.name!r} shows {c.value or 'the change'}"
            )
        if c.kind == "opened":
            for later in (now, *tools.rechecks(now)):
                why = contract_mod.opened(start, later, c.name, pressed)
                if why:
                    return met(why)
            return ToolResult(Outcome.UNEXPECTED, f"the contract {c} does not hold: its own view is not in front")
        if c.kind == "sent":
            typed_text = " ".join(t for s, r in done if r.ok for t in slot_texts(s)).casefold()
            if "@" in c.name and c.name.casefold() not in typed_text:
                return ToolResult(Outcome.UNEXPECTED, f"the contract {c} does not hold: {c.name!r} was never written")
            last = next(((s, r) for s, r in reversed(done) if s.get("tool") in ("click", "answer", "submit")), None)
            own = expect_mod.of(last[0]) if last is not None else None
            if own is not None and own.kind in expect_mod.SENT and last[1].ok:
                return met(f"its send proved it ({last[1].detail})")
            first_done = getattr(first.plan, "done_shows", "")
            got = expect_mod.parse(first_done)[0] if first_done else None
            if got is not None and got.kind in expect_mod.SENT:
                end = judge(first_done)
                if end.ok:
                    return met(end.detail)
            if first_done and got is None:  # a v1 plan's words: new since its send, by code (never the brain's check)
                end = end_state(tools, first_done, done, start, None, baseline)
                if end.ok:
                    return met(end.detail)
            return ToolResult(Outcome.UNEXPECTED, f"the contract {c} does not hold: no send closed its form or showed")
        return ToolResult(Outcome.UNEXPECTED, f"the contract {c} is no outcome")

    def end_check(c: contract_mod.Contract) -> ToolResult:
        """The end of the plan, by the task's contract: an answer only by its answer said, an outcome by structure; a
        run with no contract line by its done line (typed; a v1 plan's words: code, Laya, then once the brain)."""
        if c.answer:
            if answer:
                return ToolResult(Outcome.OK, f"the contract {c} holds: answered")
            return ToolResult(Outcome.NOT_FOUND, f"the contract {c} does not hold: no answer was said")
        if c.kind != "done":
            return outcome(c)
        if hb.v2 or expect_mod.parse(done_shows)[0] is not None:
            return typed_end(tools, done_shows, done, baseline, start, before_of)
        return end_state(tools, done_shows, done, start, brain_check, baseline)

    while True:
        if stopped():  # the user said stop: between steps, so nothing is cut off mid-action
            reason, gave_up = "stopped: you asked me to", GAVE_UP_STOPPED
            trace.append(reason)
            cancel_opened(tools, start)
            break
        if harness_s() > budget_s:
            reason, gave_up = f"out of time: {budget_s:g} s", GAVE_UP_TIME
            trace.append(reason)
            break
        if idle():
            reason = give_up(GAVE_UP_IDLE)
            break
        if mac_locked(env, tools.seen.get(tools.front)):
            reason, question = f"stopped: {LOCKED}", "Unlock the Mac, then ask again."
            trace.append(reason)
            break
        if not queue:
            pull()
        if queue and queue[0].get("tool") == LOOK["tool"]:  # a look's steps ran: the screen they led to (auto)
            queue.pop(0)
            if looking_for and step_looks.get(looking_for, 0) >= MAX_STEP_LOOKS:
                trace.append(f"  no look at where the fix led: {MAX_STEP_LOOKS} looks spent on that step; the plan's rest")
            elif looks < MAX_LOOKS and in_time():
                if looking_for:
                    step_looks[looking_for] = step_looks.get(looking_for, 0) + 1
                why = "Arrived: the steps of your last look ran; this is the screen they led to."
                if not took_look(look_call(tools.look(), why, list(queue)), list(queue)):
                    break
            continue
        if not asked_ahead and queue and queue[0].get("tool") not in PREPARES:
            asked_ahead, said_no = True, ask_ahead()
            if said_no:  # the user said no before anything was done on the page: nothing is
                reason, brain_stop = f"stopped: {Outcome.REFUSED.value} (asked first: {said_no})", "you said no"
                trace.append(reason)
                break
        if reader is not None and queue and queue[0]["tool"] == "open_place":
            reader.join(TAB_WAIT_S)  # the tabs are needed from the first open_place on
            reader = None
        if not queue and hb.page and getattr(stream, "view", None) is not None and stream.plan.look and not live:
            # the page plan ran out and the task goes on: look at the screen now
            scr = tools.look()
            if not stream.plan.steps and scr.fingerprint() == stream.view.screen.fingerprint():
                tools.hold(1.0)  # nothing to do on this screen, so far: once it changes, look again
                scr = tools.look()
            if looks >= MAX_LOOKS or (not stream.plan.steps and scr.fingerprint() == stream.view.screen.fingerprint()):
                reason = f"stopped: {stream.plan.look if stream.plan.look != 'look' else 'nothing more to do on this screen'}"
                brain_stop = "" if stream.plan.look == "look" else stream.plan.look
                trace.append(reason + (f" ({looks} page calls)" if looks >= MAX_LOOKS else ""))
                break
            begin(page_call(scr))
            continue
        if not queue:  # the plan ran out: done only if the end state shows
            if end_dialogs < END_DIALOGS:
                cleared = tools.clear_dialog(None)
                if cleared is not None:
                    end_dialogs += 1
                    trace.append(f"  end of plan, a dialog: {cleared.outcome.value} ({cleared.detail})")
                    if cleared.ok:
                        continue
            c = tools.contract = contract_now()
            if c.answer and not answer:  # an answer contract holds only by its answer, said: never a bare "Done."
                ask_step = tells[-1] if tells else {"tool": "tell", "question": task.strip(), "slot": "", "form": "answer"}
                slot = tools.last_slot or ask_step["slot"]  # the latest collect is what a look read the answer into
                again = {**ask_step, "slot": slot or ask_step["slot"] or "answer"}
                if (slot or callable(getattr(env, "page_text", None))) and retries.get(key_of(again), 0) < MAX_STEP_TRIES:
                    queue.append(again)  # the tell climbs the ladder like any step: its tries bound it
                    trace.append(f"  the plan ran out with its question unanswered ({c}): tell it from {{{again['slot']}}}")
                    continue
                tools.unmet = tools.unmet or f"{c}: the question was never answered"
                last_fail = (ask_step, ToolResult(Outcome.NOT_FOUND, "the question was never answered"))
                reason = f"stopped: the plan ran out with its contract {c} unmet"
                trace.append(reason)
                break
            end = end_check(c)
            if end.ok:
                success, reason = True, f"done: {end.detail}"
                tools.unmet = ""
                break
            tools.unmet = end.detail if c.kind != "done" else ""
            failed, result = {"tool": "(end of plan)"}, ToolResult(end.outcome, f"the plan ran out but {end.detail}")
            if c.kind != "done" or hb.v2 or expect_mod.parse(done_shows)[0] is not None:  # a late change, waited for
                late = repair.repair(
                    tools, failed, result, lambda s, r: r, lambda _scr, c=c: end_check(c).ok, in_time,
                )  # fmt: skip
                if late is not None and late.ok:
                    repairs += 1
                    success, reason = True, f"done: {late.how} (local repair)"
                    tools.unmet = ""
                    break
                if late is not None:
                    trace.append(f"  local repair: missed; tried {'; '.join(late.tried)}")
        else:
            # a later step whose expectation (with words) is met by state since the plan was made means every step up to
            # it is done, however the screen got there: no step is redone and no brain call spent on it
            if done:  # a later step's expectation can only be met anew once a step has run: see the whole plan
                while pull():
                    pass
            scr = tools.look()
            # never past a step that does work: what it writes, sends, reads or says is needed whatever the screen
            # shows (words of an old sent message are no proof this one went); only getting there is skipped
            reads = next((n for n, s in enumerate(queue) if s["tool"] in WORK_TOOLS), len(queue))
            ahead = next(
                (
                    k
                    for k in range(reads - 1, -1, -1)
                    if met_since(tools, queue[k], scr, planned_on)
                    # a step on the way with an expectation of its own (with words) is skipped only when it is met too
                    and all(not (expect_mod.of(s) and expect_mod.of(s).arg) or met_since(tools, s, scr, planned_on)
                            for s in queue[:k])
                ),
                None,
            )  # fmt: skip
            if ahead is not None:
                proof = f"{queue[ahead]['expects']} met already"
                trace.append(f"  {proof}: {ahead + 1} step(s) done already")
                for step in queue[: ahead + 1]:
                    passed(step)
                    before_of[id(step)] = planned_on  # what proved it was met
                    done.append((step, ToolResult(Outcome.OK, proof)))
                    trail.append((step, recipes.proof(step, reached=True)))
                    mark("✓", step, proof + " (reached already)")
                del queue[: ahead + 1]
                settled, stalls = (scr.app, scr.profile), 0
                continue
            failed, wrong = queue[0], []
            why = drifted() if failed["tool"] in NEEDS_PAGE and drifts < MAX_DRIFTS else None
            if why is not None:
                drifts += 1
                if tools.return_to_place(why):
                    scr = tools.look()
                    settled = (scr.app, scr.profile)
                    continue
            graph.attempt()
            known = set(graph.seen)  # a state none of these reached is this step's progress
            cleared = dialog_first(tools, failed) if failed["tool"] in NEEDS_PAGE else None
            if cleared is not None and not cleared.ok:
                keep = (Outcome.REFUSED, Outcome.NEEDS_PERMISSION)
                result = ToolResult(cleared.outcome if cleared.outcome in keep else Outcome.BLOCKED_BY_DIALOG, cleared.detail)
            else:
                now = tools.look()
                # told again from what was collected since: a new action
                told = told_of(failed)
                edge = graph.tried(now, failed, told=told)
                if edge is not None:  # it missed on this very screen already: never run again here (progress.py)
                    result = ToolResult(Outcome.STUCK, f"{edge.label} was tried on this screen already ({edge.verdict})")
                elif failed["tool"] == "delegate":  # Claude Code started: the task is done, its session goes on alone
                    result = tools.run(failed)
                    log.append({**failed, "outcome": result.outcome.value, "detail": result.detail})
                    trace.append(f"  delegate: {result.outcome.value} ({result.detail})")
                    if result.ok:
                        mark("✓", failed, result.detail)
                        passed(failed)
                        done.append((queue.pop(0), result))
                        success, reason = True, f"delegated: {result.detail}"
                    else:  # no repo, a no, or Claude Code did not start: never retried, never rescued
                        last_fail = (failed, result)
                        reason = f"stopped: {result.outcome.value} ({result.detail})"
                    break
                elif failed["tool"] == "tell":  # a question: done once the answer is said, not by an end state
                    tools.contract = contract_now()  # the answer is checked against the task's contract
                    result, answer, speech = tell(tools, hb, failed)
                    progress_mod.note(tools, now, failed, result, told=told)
                    log.append({**failed, "outcome": result.outcome.value, "detail": result.detail})
                    if result.ok:
                        mark("✓", failed, result.detail)
                        passed(failed)
                        trail.append((failed, recipes.proof(failed)))
                        done.append((queue.pop(0), result))
                        success, reason = True, f"answered: {answer}"
                        break
                else:
                    last_before = now
                    if failed["tool"] in CHANGES:
                        baseline = now
                    t, before, laya0 = clock(), env_clock(env), tools.laya_seconds
                    picks0 = list(tools.elements)
                    tools.from_rescue = any(failed is s for s in rescued)
                    sending = tools.last_write[1] if failed["tool"] == "submit" and tools.last_write else ""
                    result = verify(failed, tools.run(failed))
                    progress_mod.note(tools, now, failed, result)  # the edge: this action on this screen, and where it led
                    log.append(
                        {
                            **failed,
                            "outcome": result.outcome.value,
                            "detail": result.detail,
                            "s": round(clock() - t, 2),
                            **spent(before, env_clock(env)),
                            "laya_s": round(tools.laya_seconds - laya0, 2),
                        }
                    )
                    trace.append(f"  {failed['tool']}: {result.outcome.value}" + (f" ({result.detail})" if result.detail else ""))
            fixed = None
            if not result.ok and failed["tool"] in TOOLS and failed["tool"] not in ("tell", "compose"):
                # the local ladder before any brain call: a wait, another way, the next-best (repair.py)
                press = tools.last_press if failed["tool"] == "click" else None
                ran_on = press.before if press is not None else last_before
                got = expect_mod.of(failed)
                fixed = repair.repair(
                    tools,
                    failed,
                    result,
                    verify,
                    lambda scr, on=ran_on, got=got, tool=failed["tool"]: got is not None and judged(tools, got, on, scr, tool).ok,
                    in_time,
                    MAX_STEP_TRIES - retries.get(key_of(failed), 0),
                )
                if fixed is not None:
                    wrong = fixed.wrong
                    trace.append(
                        f"  local repair: {'fixed (' + fixed.how + ')' if fixed.ok else 'missed'}"
                        + (f"; tried {'; '.join(fixed.tried)}" if fixed.tried else "")
                    )
                    if log and log[-1].get("tool") == failed["tool"]:
                        log[-1]["repair"] = fixed.how if fixed.ok else "missed"
                        log[-1]["local_tries"] = fixed.tries
                        if wrong:
                            log[-1].update(tries=len(wrong) + 1, wrong=wrong)
                    if fixed.ok:
                        repairs += 1
                        if fixed.skip:  # what the step was for shows already: the steps after it prove themselves
                            mark("↷", failed, f"not needed: {fixed.result.detail} (local repair)")
                            passed(failed, progress=False)
                            queue.pop(0)
                            stalls = 0
                            continue
                        result = ToolResult(Outcome.OK, f"repaired locally ({fixed.how})")
                    else:
                        result = fixed.result
                        local.setdefault(step_key(failed), []).extend(fixed.tried)
            if not result.ok and fixed is not None:  # each local rung that ran is one of this step's tries
                retried(failed, fixed.tries)
                if progress_mod.state_of(progress_mod.latest(tools)) not in known:  # a screen never seen: progress
                    graph.moved()
            if result.ok:
                tries = (
                    (
                        f"{ordinal(len(wrong) + 1)} try; {', '.join(repr(w) for w in wrong)} "
                        f"{'was' if len(wrong) == 1 else 'were'} wrong; "
                    )
                    if wrong
                    else ""
                )
                mark("✓", failed, tries + (result.detail or ""))  # a typed step's proof is in its detail
                passed(failed)
                done.append((queue.pop(0), result))
                tools.used = []  # what memory gave this step worked
                scr = tools.look()
                trail.append((pinned_as(failed), recipes.proof(failed, last_before.url, scr.url)))
                press = tools.last_press if failed["tool"] == "click" else None
                ran_on = press.before if press is not None else last_before
                before_of[id(failed)] = ran_on
                if press is not None and press.item.kind in CONTENT_KINDS:
                    pressed.append(press.item.name)
                if press is not None and (expect_mod.of(failed) or expect_mod.Expect("")).kind == "toggled":
                    flipped.append(press.item.name)
                picks = [e for e in tools.elements if e not in picks0]
                last_field = way(
                    tools,
                    failed,
                    result,
                    fixed,
                    ran_on,
                    scr,
                    picks,
                    ways,
                    last_field,
                    pitfalls,
                    wrong,
                    fixing.pop(id(failed), None),
                )
                settled = (scr.app, scr.profile)
                stalls = 0
                if hb.page and from_view and new_page(ran_on, scr):  # numbers read off the last page mean nothing here
                    while pull():
                        pass
                    if any(id(s) in from_view for s in queue) and looks < MAX_LOOKS:
                        trace.append(f"  a new page: {len(queue)} step(s) planned on the last one are dropped; looking again")
                        begin(page_call(scr))
                elif hb.auto and not replaying() and looks < MAX_LOOKS and in_time():
                    why = ""
                    if from_view and new_page(ran_on, scr) and any(id(s) in from_view for s in queue):
                        stale = [step_line(s) for s in queue if id(s) in from_view]
                        why = (
                            f"Arrived: after {step_line(failed)}, another screen; these steps of your last look, aimed by"
                            " number at the screen before, were dropped:" + "".join(f"\n- {x}" for x in stale)
                        )
                    elif unnamed(scr):  # the rest of the plan may name it: see the whole plan first
                        while pull():
                            pass
                        place = unnamed(scr)
                        if place and queue:
                            named.add(place)  # one look per place
                            why = f"Arrived: after {step_line(failed)}, the screen is {place}, a place the plan never named."
                    if why and not took_look(look_call(scr, why, list(queue)), list(queue)):
                        break
                continue
        trace.append(f"  FAILED {failed['tool']}: {result.outcome.value} {result.detail}".rstrip())
        mark("✗", failed, f"{result.outcome.value}: {result.detail}" if result.detail else result.outcome.value)
        if failed["tool"] in TOOLS:  # a remembered element, alias or place the step used led here: forgotten at once
            tools.forget_used(result.outcome.value)
        tools.note("step", step_line(failed) if failed["tool"] in TOOLS else failed["tool"], result.outcome.value, result.detail)
        last_fail = (failed, result)
        if result.outcome is Outcome.REFUSED:  # the user said no: the task ends here, no rescue and no question
            cancel_opened(tools, start)
            reason = f"stopped: {result.outcome.value}" + (f" ({result.detail})" if result.detail else "")
            break
        if retries.get(key_of(failed), 0) >= MAX_STEP_TRIES or idle():  # no try left for it, or no progress
            reason = give_up(GAVE_UP_RETRIES if retries.get(key_of(failed), 0) >= MAX_STEP_TRIES else GAVE_UP_IDLE, failed)
            break

        # a login page or another place mid-task: back to the place that worked, then the step again (a local repair)
        if failed["tool"] not in ("open_place", "(end of plan)") and drifts < MAX_DRIFTS:
            why = tools.drift()
            if why is not None:
                drifts += 1
                if retried(failed) > MAX_STEP_TRIES:  # back to the place and the step again: a retry
                    reason = give_up(GAVE_UP_RETRIES, failed)
                    break
                if tools.return_to_place(why):
                    repairs += 1
                    scr = tools.look()
                    settled = (scr.app, scr.profile)
                    continue

        while pull():  # the rescue sees the whole plan (its steps still to do are kept)
            pass
        rest = queue[1:] if queue and queue[0] is failed else []
        if tools.must_ask:  # MAX_MOVES places left already: where to go next is the user's call, not the brain's
            question = tools.must_ask
        # the brain fixes this step only; the plan then carries on from its next step
        calls_left = looks < MAX_LOOKS if hb.page or hb.auto else rescues < MAX_RESCUES  # a miss is one look
        root = root_key(failed)
        if (hb.page or hb.auto) and step_looks.get(root, 0) >= MAX_STEP_LOOKS and not tools.must_ask:
            # looked at it MAX_STEP_LOOKS times already: no more brain calls on one step; it fails with its reason
            gave_up = GAVE_UP_LOOKS
            line = step_line(failed) if failed["tool"] in TOOLS else failed["tool"]
            reason = f"gave up: {line} still missed after {MAX_STEP_LOOKS} looks: {result.outcome.value}" + (
                f" ({result.detail})" if result.detail else ""
            )
            trace.append(reason)
            break
        rescue = not tools.must_ask and stalls < MAX_STALLS and calls_left and in_time()
        if rescue and (idle() or retries.get(key_of(failed), 0) >= MAX_STEP_TRIES):  # no try left for a look
            reason = give_up(GAVE_UP_IDLE if idle() else GAVE_UP_RETRIES, failed)
            break
        if rescue:
            rescues += 1
            retried(failed)
            if hb.page or hb.auto:
                step_looks[root] = step_looks.get(root, 0) + 1
                looking_for = root
            events.emit(
                "rescue", text=f"asking the brain to fix: {step_line(failed) if failed['tool'] in TOOLS else failed['tool']}"
            )
            stalls += 1
            here = tools.look()
            looked = None
            if hb.page:  # a missed check: one look at the screen now, told what missed (it replaces the blind rescue)
                line = step_line(failed) if failed["tool"] in TOOLS else failed["tool"]
                tried_local = local.get(step_key(failed), []) if failed["tool"] in TOOLS else []
                pinned_rest = [s for s in rest if id(s) not in from_view] if replaying() else []
                looked = page_call(
                    here,
                    f"{line} -> {result.outcome.value}" + (f" ({result.detail})" if result.detail else "")
                    + ("\nTried locally on this step (code and Laya), and missed:" + "".join(f"\n- {x}" for x in tried_local[-6:])
                       if tried_local else "")
                    + ("\nStill to do after it (a pinned path):" + "".join(f"\n- {step_line(x)}" for x in pinned_rest)
                       if pinned_rest else ""),
                )  # fmt: skip
                fresh = list(looked)  # read whole: compared with what was tried here before any of it runs
                new = looked.plan
                steps = drop_done(fresh, tools)
                if not pinned_rest:  # the page's plan now (its look or done, below); a pinned path's rest carries on
                    stream, source, live = looked, iter(()), False
            elif hb.auto:  # a missed check: one look at the screen at that checkpoint, told what was expected
                line = step_line(failed) if failed["tool"] in TOOLS else failed["tool"]
                tried_local = local.get(step_key(failed), []) if failed["tool"] in TOOLS else []
                got = (
                    expect_mod.of(failed)
                    if failed["tool"] in TOOLS
                    else (f"the contract {contract_now()}" if contract_now().kind != "done" else expect_mod.parse(done_shows)[0])
                )
                rest = [s for s in rest if id(s) not in from_view]  # numbers read off an earlier screen
                looked = look_call(
                    here,
                    f"Failed: {line}"
                    + (f"\nExpected: {got}" if got is not None else "")
                    + f"\nWhat happened: {result.outcome.value}" + (f" ({result.detail})" if result.detail else "")
                    + ("\nTried locally on this step (code and Laya), and missed:" + "".join(f"\n- {x}" for x in tried_local[-6:])
                       if tried_local else ""),
                    rest,
                )  # fmt: skip
                new = looked.plan
                steps = drop_done(list(looked), tools)  # read whole, like a rescue
                if new.carry and not steps and not new.stop and not new.done_shows:  # the step is not needed any more
                    new.skip = new.skip or "the look says the plan's rest carries on"
            else:
                graph.block(here)  # what was tried on this screen goes with the local tries (below)
                new = hb.rescue(
                    task,
                    mac_text,
                    tools.for_brain(here),
                    (know_how(memory, task, here, mac) or know) + recent_lines,  # the know-how where the step failed
                    done,
                    failed,
                    result,
                    {w: p.name for w, p in tools.places.items()},
                    [],
                    note_lines(),
                    rest=rest,
                    collected={k: len(v) for k, v in tools.slots.items()},
                    local=(local.get(step_key(failed), []) if failed["tool"] in TOOLS else [])
                    + [f"tried on this screen: {x}" for x in graph.here(here)],
                )
                steps = drop_done(new.steps, tools)
            trace.append(
                f"  rescue: {json.dumps(steps, ensure_ascii=False)}"
                + (f" stop={new.stop!r}" if new.stop else "")
                + (f" skip={new.skip!r}" if new.skip else "")
                + (f" replan={new.replan!r}" if new.replan else "")
                + (f" done_shows={new.done_shows!r}" if new.done_shows else "")
                + (f" then {len(rest)} planned step(s)" if rest and steps and not new.replan else "")
                + (f" ignored as prose={new.prose}" if new.prose else "")
            )
            if new.learned and tools.here:  # kept under the site or app it is about, once the task ends proven
                learned.append((tools.here, new.learned))
            question = new.ask or question
            if new.done_shows:
                done_shows = new.done_shows
            if new.stop:  # the brain's stop ends the task: no question for the user after it but the brain's own
                reason, brain_stop = f"stopped: {new.stop}", new.stop
                if not question:
                    break
            elif new.skip and not steps and failed["tool"] in TOOLS:  # the step is not needed: on to the next
                mark("↷", failed, f"not needed: {new.skip}")
                passed(failed, progress=False)
                queue[:] = rest
                continue
            elif (steps and graph.tried(here, steps[0], told=told_of(steps[0])) is not None) or not (
                steps or new.ask or new.done_shows
            ):  # nothing untried: its first step missed on this screen already, or no step at all (progress.py)
                again = graph.tried(here, steps[0], told=told_of(steps[0])) if steps else None
                trace.append(
                    "  refused: " + (f"{again.label} was tried on this screen ({again.verdict})" if again else "no step to try")
                )
                if graph.was_told(here):  # it was shown what was tried here: nothing untried is left
                    brain_stop = graph.exhausted(here)
                    reason, gave_up = f"gave up: {brain_stop}", GAVE_UP_TRIED
                    trace.append(reason)
                    break
                continue  # the step again: refused here, so the next look is told what was tried on this screen
            elif steps:
                tells.extend(s for s in steps if s.get("tool") == "tell")
                rescued.extend(steps)
                for step in steps:  # a new approach: the fix's steps count their own retries (the rescue was this step's)
                    fixing[id(step)] = failed  # the end of the plan too: its fixes' looks count for it
                if hb.auto:  # the look's steps on that screen, then the plan's rest as the reply says
                    name_places(steps)
                    from_view.clear()
                    from_view.update(id(s) for s in steps if s.get("at") or s.get("rows"))
                    queue[:] = after_look(steps, rest, new)
                    planned_on = looked.view.screen
                    continue
                if looked is not None:  # the look's steps are this page's plan; the old ones planned on it are stale
                    rest = [s for s in rest if id(s) not in from_view]
                    from_view.clear()
                    from_view.update(id(s) for s in steps if s.get("at") or s.get("rows"))
                    whole = bool(new.look or new.done_shows or new.replan) and not pinned_rest  # else a fix, then the rest
                    queue[:] = steps if whole else with_rest(steps, rest, bool(new.replan))
                    planned_on = looked.view.screen
                    continue
                queue[:] = with_rest(steps, rest, bool(new.replan))
                planned_on = tools.look()
                continue
            elif new.done_shows and not new.ask:  # the brain reads it as done: the end state still has to show
                queue.clear()
                continue
        if stopped():  # the user said stop meanwhile: no question for them; the loop's top ends it cleanly
            continue
        # the last rung: the user
        q = question or f"I'm stuck on {failed['tool']}: {result.outcome.value}. What should I do?"
        # the user is asked only while the task may still go on: never past a give-up cap (a risky yes/no is confirm)
        capped = (
            GAVE_UP_TIME
            if harness_s() > budget_s
            else GAVE_UP_IDLE
            if idle()
            else GAVE_UP_RETRIES
            if chain(failed) >= MAX_STEP_TRIES  # a reply would be one retry too many
            else ""
        )
        if ask is not None and asked < 1 and capped:
            gave_up = capped
            trace.append(f"  not asking the user ({capped}): stopping instead")
        if ask is not None and asked < 1 and not capped:
            asked += 1
            reply = (ask(q) or "").strip()
            trace.append(f"  asked the user: {q!r} -> {reply!r}")
            if reply:
                task = f"{task}\n(The user, asked {q!r}, said: {reply})"
                tools.task = task
                question, stalls = None, 0
                retried(failed)  # the step goes again with the reply: a retry like any other
                tools.must_ask, tools.moves = "", 0  # the user said where: the places may be tried again
                continue
        reason = reason or f"stuck on {failed['tool']}: {result.outcome.value}" + (f" ({result.detail})" if result.detail else "")
        question = q
        break

    if live:  # stopped before the plan finished streaming: let the brain's process go
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    c = contract_now()
    verified = success and c.declared and not by_brain  # a declared contract, checked by code and met
    if verified:
        remember(memory, tools, learned, ways, pitfalls, proof=str(c))
    elif success:
        trace.append("memory: nothing kept (no contract was declared and met)")
    pinned = ""
    used = hit if hit is not None else getattr(first.plan, "pinned", None)
    session_used = any(
        "{@" in json.dumps(s, ensure_ascii=False)
        or str(s.get("slot", "")).startswith("@")
        or s.get("tool") in ("compose", "delegate")
        for s, _ in done
    )  # it points at another task's result, which a replay would not have; a text the brain wrote; a coding agent
    if book is not None and not asked and gave_up != GAVE_UP_STOPPED and not session_used:
        # a task the user's reply changed is not the command that was given; a stopped one ran no whole path
        refused = bool(said_no) or any(s.get("outcome") == Outcome.REFUSED.value for s in log)
        unmet = "" if success else tools.unmet  # a replay whose contract was checked and did not hold
        pinned, short = book.after(task, used, success, verified, trail, done_shows, refused, str(c) if c.declared else "", unmet)
        if pinned:
            trace.append(f"pin: {pinned}")
            events.emit("pinned", text=pinned, short=short)
    # how it ended, in one line built by code (no brain call): said aloud in voice mode (app.py)
    stopped_on, stopped_by = last_fail if last_fail is not None else ({}, None)
    passed_steps = [s for s, _ in done]
    if success and tools.launched:
        said = f"Claude Code is starting in {tools.launched[-1].name}."
    elif success:
        said = "" if answer else done_line(done_shows)
    elif LOCKED in reason:
        said = failure_line(passed_steps, locked=True)
    elif gave_up == GAVE_UP_STOPPED:
        said = STOPPED_SAID
    elif gave_up == GAVE_UP_TIME:
        said = failure_line(passed_steps, seconds=budget_s)
    else:
        why = brain_stop or (
            reason_words(stopped_on, stopped_by.outcome.value, stopped_by.detail)
            if stopped_by is not None
            else "nothing I tried moved it forward"
        )
        said = failure_line(passed_steps, why)
    if tools.notices and not answer:  # titles only (consent said no): said first
        said = " ".join([*dict.fromkeys(tools.notices), said]).strip()
    if said:
        trace.append(f"said: {said}")
    if session is not None:  # what it produced or touched, for a later task's "that" (values in RAM only)
        try:
            kept = session.add(asked_task, passed_steps, tools, answer, success, said, done_shows)
            trace.append(
                f"session: {len(kept.refs)} ref(s) kept"
                + (f" ({', '.join(f'@{r.id} {r.kind}' for r in kept.refs)})" if kept.refs else "")
            )
        except Exception as error:  # the task's result stands
            trace.append(f"session: not kept ({type(error).__name__}: {error})")
    return TaskResult(
        task=task,
        done=success,
        reason=reason,
        said=said,
        gave_up="" if success else gave_up,
        failed_step=""
        if success or not stopped_on
        else step_line(stopped_on)
        if stopped_on.get("tool") in TOOLS
        else str(stopped_on.get("tool")),
        failed_why=""
        if success or stopped_by is None
        else f"{stopped_by.outcome.value}" + (f": {stopped_by.detail}" if stopped_by.detail else ""),
        steps=log,
        brain_calls=hb.calls - calls0,
        laya_decisions=len(tools.decisions),
        confirmations=list(tools.confirmations),
        actions=tools.actions,
        rescues=rescues,
        repairs=repairs,
        seconds=round(harness_s(), 2),
        timings={
            "harness_s": round(harness_s(), 2),
            "brain_s": round(hb.seconds - brain_s0, 2),
            "laya_s": round(tools.laya_seconds, 2),
            "env_s": round(env_s(), 2),
            "first_step_s": round(first_step_at, 2) if first_step_at is not None else -1.0,
            **spent(clock0, env_clock(env)),
        },
        question=None if success else question,
        trace=trace,
        decisions=tools.decisions,
        brain=hb.stats[stats0:],
        ledger=ledger,
        answer=answer or None,
        speech=speech,
        replayed=used.how if used is not None else "",
        pinned=pinned,
        delegated=list(tools.launched),
    )
