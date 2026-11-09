"""Try, verify, next: a click whose pick was wrong is undone and Laya's next-best clicked, before any brain call.

Laya picks in tens of milliseconds; a brain rescue costs seconds. So when a click step's pick lands on the wrong thing
(a different change than the step declared, or the click changed nothing), the loop does not go straight to the brain:
if the item was chosen by Laya or by a code rule that is not an exact match of its name, and the click was one that
can be undone, it undoes it (chosen by code from what changed since just before the click), marks that item wrong
for this step (Toolbox.wrong, and the session notes the rescue reads), and runs the step again, so the next-best is
picked and verified the same way. At most MAX_TRIES clicks per step, and only while harness budget is left. It is the
third local try of the step's ladder (repair.py: a wait, another way, then the next-best), before any brain look.

Never retried: an exact match of the step's words (its miss is not a picking problem), a menu command, a dialog's or
popup's answer, a checkbox or toggle, anything hard to undo (risk.risky) or that commits (send, save, submit...), a
step the user refused. A page that changed in place has nothing code can undo: the change stays (IN_PLACE) and the
next-best is verified by the step's own type from the screen just before it. When what the click opened cannot be
closed or left (an address that does not go back, a popup that stays), no retry.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable

from karta.core import events
from karta.harness import facts, risk
from karta.harness.brain import step_line
from karta.harness.progress import latest, note
from karta.harness.tools import EXACT, Press, Toolbox, norm, spot
from karta.harness.types import Outcome, Screen, ToolResult

MAX_TRIES = 3  # clicks per step, the first one included
COMMITS = risk.COMMITS  # a control that commits or answers, whatever risk says
NOT_PICKS = ("enabled",)  # a control clicked once an option enabled it: two clicks, not one pick
IN_PLACE = "the page changed in place: nothing to undo"  # undo's answer when only the page itself changed


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def why_not(step: dict, result: ToolResult, press: Press | None) -> str | None:
    """Why this step's miss is not retried with the next pick, or None when it may be."""
    if step.get("tool") != "click":
        return "not a click"
    if press is None:
        return "no item was picked and clicked"
    if result.outcome not in (Outcome.NO_CHANGE, Outcome.UNEXPECTED):
        return f"it ended {result.outcome.value}, not a wrong pick"
    item = press.item
    if press.how == EXACT:
        return "an exact match of its name: a miss is not a picking problem"
    if press.how in NOT_PICKS:
        return "a control clicked after the option that enabled it"
    if item.menu:
        return "a menu command"
    if item.dialog or item.browser_ui:
        return "an answer to a dialog or popup"
    if item.kind == "checkbox" or item.checked is not None:
        return "a checkbox or toggle"
    if risk.risky("click", item.name) is not None or risk.commits(item):
        return "hard to undo"
    return None


def new_dialog(before: Screen, now: Screen) -> list:
    was = {(i.name, i.kind) for i in before.items if i.dialog}
    return [i for i in now.items if i.dialog and (i.name, i.kind) not in was]


def undo(tools: Toolbox, press: Press, result: ToolResult) -> str | None:
    """Undo the wrong click, chosen from what changed since just before it: another app or profile in front -> back to
    the place that worked; a new web address -> back; a new dialog, popup or menu -> its own cancel or close
    control, else escape; nothing -> nothing; the same page and window changed in place -> IN_PLACE (nothing code can
    undo; a next try is verified by its type from the screen just before it). Returns what was done ("" for
    nothing), or None when what it opened could not be closed or left (then there is no retry)."""
    if result.outcome is Outcome.NO_CHANGE:
        return ""
    before, now = press.before, tools.look()
    if now.fingerprint() == before.fingerprint():
        return ""
    if (now.app, now.profile) != (before.app, before.profile):
        place = tools.last_place
        if place is None or place.app != before.app:
            return None
        why = f"{now.app} is in front, not {before.app}" if now.app != before.app else f"profile {now.profile} is in front"
        return f"back to {place.name}" if tools.return_to_place(why) else None
    if (now.url or "") != (before.url or ""):
        if not before.url:
            return None
        tools.do("go back (the click was wrong)", tools.env.back)
        first = tools.settle(now.fingerprint()) or tools.look()
        for scr in itertools.chain([first], tools.rechecks(first)):  # lazily: stop re-reading once it is back
            if scr.url == before.url:
                return "went back"
        return None
    popup = new_dialog(before, now)
    if popup:
        way_out = facts.dismiss_option(popup)  # its own cancel or close control, by structure; escape only without one
        if way_out is not None:
            tools.do(f"click {way_out.name!r} (close what the wrong click opened)", tools.env.click, way_out)
        else:
            tools.do("press escape (close what the wrong click opened)", tools.env.press, "escape")
        scr = tools.settle(now.fingerprint()) or tools.look()
        how = f"its {way_out.name!r}" if way_out is not None else "escape"
        return f"closed what it opened ({how})" if not new_dialog(before, scr) else None
    return IN_PLACE


def mark_wrong(tools: Toolbox, press: Press, reason: str, noted: bool = False) -> None:
    """That item is wrong for this step: never offered for it again (Toolbox.wrong), and noted for the rescue."""
    tools.wrong.setdefault((norm(press.target), norm(press.near)), set()).add(spot(press.item))
    if not noted:  # a click that changed nothing was noted by the click itself
        tools.reject_element(press.target, press.item, press.how, reason)


def try_next(
    tools: Toolbox,
    step: dict,
    result: ToolResult,
    verify: Callable[[dict, ToolResult], ToolResult],
    budget_left: Callable[[], bool],
) -> tuple[ToolResult, list[str]]:
    """After a click step that failed verification: undo, mark the pick wrong, run the step again, verify again; at
    most MAX_TRIES clicks in all. Each click is an edge of the task's state graph (progress.py). Returns the last
    result and the names found wrong, in order."""
    wrong: list[str] = []
    while not result.ok and len(wrong) + 1 < MAX_TRIES and budget_left():
        press = tools.last_press
        no = why_not(step, result, press)
        if no is not None:
            if press is not None:
                tools.trace.append(f"  no retry: {no}")
            break
        missed = "clicking it changed nothing" if result.outcome is Outcome.NO_CHANGE else result.detail
        undone = undo(tools, press, result)
        if undone is None:
            tools.trace.append(f"  no retry: could not undo clicking {press.item.name!r} (what it opened stayed)")
            break
        mark_wrong(tools, press, f"clicked for {step_line(step)}: wrong ({missed})", result.outcome is Outcome.NO_CHANGE)
        wrong.append(press.item.name)
        tools.trace.append(
            f"  retry {len(wrong) + 1}/{MAX_TRIES}: {press.item.name!r} was wrong ({missed})"
            + (f"; {undone}" if undone else "; nothing to undo")
            + ("; the next-best is verified by its type" if undone == IN_PLACE else "; asking for the next-best")
        )
        events.emit("retry", wrong=press.item.name, why=missed, attempt=len(wrong) + 1, of=MAX_TRIES)
        tools.instead_of = press.item.name
        before = latest(tools)
        result = verify(step, tools.run(step))
        note(tools, before, step, result)
        tools.instead_of = ""
    if wrong and not result.ok:
        picks = [*wrong, tools.last_press.item.name] if tools.last_press is not None else wrong
        result = ToolResult(
            result.outcome, f"{result.detail} (tried {len(picks)} picks: {', '.join(repr(w) for w in picks)})".strip()
        )
    return result, wrong
