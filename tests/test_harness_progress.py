"""The task's state graph (karta/harness/progress.py): which action was tried on which screen and where it led. An
action that missed is never run again on the same screen (a step, a repair's rung, a look's step); a look proposing one
is refused with no brain call; looks are shown what was tried on the screen; a told look with nothing untried ends the
task with the list. Made-up apps on the fakes; nothing touches the Mac."""

from __future__ import annotations

from dataclasses import replace

from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, page, toolbox

from karta.harness import loop, progress
from karta.harness.brain import HarnessBrain
from karta.harness.types import Item, Outcome, Screen, ToolResult

TASK = "file the note in plumora"


def auto(*replies: str) -> tuple[HarnessBrain, FakeLineBrain]:
    brain = FakeLineBrain(*replies)
    return HarnessBrain(brain, prompt="v2", plan="auto"), brain


def clicks(env: FakeEnv) -> list[str]:
    return [a for a in env.log if a.startswith("click")]


def plumora() -> FakeEnv:
    """A made-up notes app whose Keep, Stash and Shelve buttons do nothing; Archive files the note."""
    items = [Item("Notes", "heading"), Item("Bills", "row"), *(Item(n, BTN) for n in ("Keep", "Stash", "Shelve", "Archive"))]
    filed = page("Plumora", *items, Item("Groceries", "row"))
    return FakeEnv({"list": page("Plumora", *items), "filed": filed}, "list", {("list", "click Archive"): "filed"})


# ---------------------------------------------------------------- nodes: a stable fingerprint


def test_a_clock_tick_a_counter_the_focus_and_typed_values_are_the_same_state():
    at = Screen(
        "Tidewell",
        [Item("10:41", "text", region="top-right"), Item("Inbox, 3 unread", "row"), Item("Search mail", "field"),
         Item("Compose", BTN)],
        window="Inbox (3)",
    )  # fmt: skip
    later = Screen(
        "Tidewell",
        [
            Item("10:42", "text", region="top-right"),
            Item("Inbox, 4 unread", "row"),
            Item("Search mail", "field", focused=True),
            Item("Compose", BTN),
            Item("09:58", "text", source="ocr"),  # read off the pixels: noise between reads
        ],
        window="Inbox (4)",
    )
    assert progress.state_of(at) == progress.state_of(later)
    assert at.fingerprint() != later.fingerprint()  # the waits' "anything changed" test still sees it


def test_what_the_screen_shows_is_another_state():
    base = page("Tidewell", Item("Compose", BTN), Item("Subject", "field"), url="https://mail.tidewell.example/inbox")
    dialog = replace(base, items=[*base.items, Item("Discard draft?", "text", dialog=True), Item("Discard", BTN, dialog=True)])
    typed = replace(base, items=[base.items[0], Item("Subject", "field", value="lunch")])
    other = replace(base, url="https://mail.tidewell.example/sent")
    off = replace(base, items=[*base.items, Item("Older", BTN)])
    states = {progress.state_of(s) for s in (base, dialog, typed, other, off)}
    assert len(states) == 5  # a dialog, a field now holding text, another address, a new control
    fragment = replace(base, url="https://mail.tidewell.example/inbox#label")
    assert progress.state_of(fragment) == progress.state_of(base)


# ---------------------------------------------------------------- edges: what counts as the same action


def test_a_numbered_item_and_a_blind_target_never_compare_but_two_resolved_ones_do():
    blind = progress.action_of({"tool": "click", "target": "Focus"}, item=("focus", "row", "AXRow", 0))
    again_blind = progress.action_of({"tool": "click", "target": "the Focus button"})
    numbered_same = progress.action_of({"tool": "click", "at": {"name": "Focus", "kind": "row", "roles": "AXRow"}})
    numbered_other = progress.action_of({"tool": "click", "at": {"name": "View > Focus", "kind": "menu"}})
    assert again_blind.same(blind)  # the blind step in other words
    assert numbered_same.same(blind)  # the very item the blind step pressed
    assert not numbered_other.same(blind)
    unresolved = progress.action_of({"tool": "click", "target": "Focus"})  # it never found a control
    assert not numbered_same.same(unresolved)  # a look aiming at an item it saw is a new approach


def test_text_and_tells_are_part_of_the_action():
    write = {"tool": "write", "text": "hello", "where": "Message"}
    assert not progress.action_of(write).same(progress.action_of({**write, "text": "goodbye"}))
    tell = {"tool": "tell", "question": "what is it?", "slot": "facts"}
    assert progress.action_of(tell, told="[1]").same(progress.action_of(tell, told="[1]"))
    assert not progress.action_of(tell, told="[1, 2]").same(progress.action_of(tell, told="[1]"))
    body, subject = ({"tool": "compose", "what": w, "from": "links"} for w in ("the body", "a subject line"))
    assert not progress.action_of(body).same(progress.action_of(subject))  # a tool with no target: by its arguments
    assert progress.action_of({**body, "slot": "text"}).same(progress.action_of(body))


def test_a_dead_end_is_refused_on_its_screen_and_nowhere_else():
    graph = progress.Progress()
    here, there = page("Plumora", Item("Keep", BTN)), page("Plumora", Item("Keep", BTN), Item("Groceries", "row"))
    step = {"tool": "click", "target": "Keep"}
    graph.record(here, step, here, ToolResult(Outcome.NO_CHANGE, "nothing changed"))
    assert graph.tried(here, step) is not None and graph.tried(there, step) is None
    assert graph.tried(here, {"tool": "wait"}) is None  # waiting is never a repeat
    worked = {"tool": "click", "target": "Groceries"}
    graph.record(there, worked, here, ToolResult(Outcome.OK, ""))
    assert graph.tried(there, worked) is None  # a path that worked is no dead end: it may be walked again
    assert graph.here(here) == ["click 'Keep' -> nothing changed"]


def test_the_tried_list_shows_target_names_never_text_or_long_content():
    graph = progress.Progress()
    scr = page("Tidewell", Item("Message", "field"), Item("Re: the quarterly numbers for our team", "row"))
    graph.record(scr, {"tool": "write", "text": "my secret plans", "where": "Message"}, scr, ToolResult(Outcome.NO_CHANGE))
    row = scr.items[1]
    graph.record(scr, {"tool": "click", "target": "the thread"}, scr, ToolResult(Outcome.NO_CHANGE), progress.identity(row, scr))
    shown = "\n".join(graph.here(scr))
    assert "Message" in shown and "secret" not in shown and "quarterly" not in shown and "a row [7 words]" in shown


def test_the_repair_never_presses_a_dead_end_again():
    env = FakeEnv({"a": page("Plumora", Item("Keep", BTN), Item("Stash", BTN))}, "a")
    tools = toolbox(env)
    graph = tools.progress = progress.Progress()
    scr = tools.look()
    keep = scr.items[0]
    step = {"tool": "click", "target": "file it", "expects": "new_item"}
    graph.record(scr, step, scr, ToolResult(Outcome.NO_CHANGE), progress.identity(keep, scr))
    result = progress.pressed(tools, step, keep, "Laya's next-best", lambda s, r: r)
    assert result.outcome is Outcome.STUCK and clicks(env) == []
    progress.pressed(tools, step, scr.items[1], "Laya's next-best", lambda s, r: r)
    assert clicks(env) == ["click Stash"]  # another candidate is a new action


# ---------------------------------------------------------------- the loop


def test_a_look_repeating_what_missed_here_is_refused_with_no_brain_call_and_the_task_stops_with_the_list():
    env = plumora()
    hb, brain = auto(
        "click Keep | expect: new_item\ndone: new_item",
        "click Stash | expect: new_item\ncontinue",  # look 1: a new approach, it misses too
        "click Keep | expect: new_item\ncontinue",  # look 2: what missed already: refused, never run
        "click Archive | expect: new_item\ncontinue",  # never asked
    )
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert not result.done and brain.calls == 3, result.trace
    assert clicks(env).count("click Keep") == 1 and "click Archive" not in clicks(env)
    assert "Already tried on this screen" in brain.asked[2][1] and "click 'Keep' (button)" in brain.asked[2][1]
    assert result.gave_up == loop.GAVE_UP_TRIED  # the last three that missed here, by name
    assert result.said.startswith("I tried '") and "'Stash'" in result.said
    assert result.said.endswith("on this page and none worked.")


def test_a_cycle_back_to_a_screen_is_refused_and_the_look_takes_another_way():
    items = [Item("Notes", "heading"), Item("Bills", "row"), Item("Open", BTN), Item("Save", BTN), Item("Save as", BTN)]
    detail = page("Plumora", Item("Bills", "heading"), Item("Back", BTN))
    saved = page("Plumora", *items, Item("Bills copy", "row"))
    env = FakeEnv(
        {"list": page("Plumora", *items), "detail": detail, "saved": saved},
        "list",
        {("list", "click Open"): "detail", ("detail", "click Back"): "list", ("list", "click Save as"): "saved"},
    )
    hb, brain = auto(
        "click Save | expect: new_item\ndone: new_item",  # Save does nothing here
        # look 1: around and back to the very screen, then the Save that missed on it: a cycle
        "click Open\nclick Back\nclick Save | expect: new_item\ncontinue",
        "click Save as | expect: new_item\ncontinue",  # look 2, told what was tried here: another way
    )
    result = loop.run_task("save a copy of the note in plumora", env, FakeLaya(), hb, FACTS)
    assert result.done, result.trace
    assert clicks(env).count("click Save") == 1  # back on the list, Save was never pressed again
    assert "click 'Save' (button) -> " in brain.asked[2][1]


def test_a_step_that_worked_before_still_runs_after_a_cycle():
    # a path that worked is a known path, not a dead end: walking it again after coming back is allowed
    a = page("Plumora", Item("Next", BTN), Item("Spring", "heading"))
    b = page("Plumora", Item("Next", BTN), Item("Summer", "heading"))
    env = FakeEnv({"a": a, "b": b}, "a", {("a", "click Next"): "b", ("b", "click Next"): "a"})
    hb, _brain = auto("click Next\nclick Next\nclick Next")
    loop.run_task("page through plumora", env, FakeLaya(), hb, FACTS)
    assert clicks(env).count("click Next") == 3


def test_the_no_progress_rule_is_the_graph_s():
    assert progress.STILL == 4
    graph = progress.Progress()
    for _ in range(progress.STILL):
        graph.attempt()
    assert graph.stalled()
    graph.moved()
    assert not graph.stalled()


def test_the_state_of_a_screen_with_no_address_is_its_app_and_title():
    one = Screen("Plumora", [Item("Keep", BTN)], window="Note 12")
    two = Screen("Plumora", [Item("Keep", BTN)], window="Note 13")
    assert progress.state_of(one) == progress.state_of(two)  # digits fold, as a counter in a title
    assert progress.state_of(one) != progress.state_of(Screen("Plumora", [Item("Keep", BTN)], window="Drafts"))
