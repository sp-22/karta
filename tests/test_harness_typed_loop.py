"""The loop under v2 (typed checks): a step's declared kind of change verified by structure, each verdict's path
(met; nothing changed -> the local ladder; a different change -> undo, the ladder, then a rescue told what changed),
skip-ahead by state, the end state by type, and false dones that typed checks refuse. Made-up apps, fake brain."""

from __future__ import annotations

from datetime import datetime

from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness.brain import HarnessBrain
from karta.harness.loop import run_task
from karta.harness.types import Item, MacFacts

FACTS = MacFacts(["Taskdeck", "Shopwell", "Settingsly", "Mailroom"], {}, [], datetime(2026, 10, 6, 9, 0))
ROWS = [Item("Pay the rent", "row"), Item("Call the plumber", "row"), Item("Add task", BTN), Item("Sort", BTN)]


def v2(*replies: str) -> tuple[HarnessBrain, FakeLineBrain]:
    lines = FakeLineBrain(*replies)
    return HarnessBrain(lines, prompt="v2"), lines


def yes(_q: str) -> bool:
    return True


def tasks_env(on_add: str = "new") -> FakeEnv:
    """Taskdeck: a list; Add task adds a row with an empty focused title (or opens a share dialog, or nothing)."""
    pages = {
        "start": page("Finder", Item("Desktop", "row")),
        "list": page("Taskdeck", *ROWS),
        "new": page("Taskdeck", *ROWS, Item("New task", "row", focused=True), Item("title", "field", focused=True)),
        "named": page("Taskdeck", *ROWS, Item("water the ferns", "row"), Item("title", "field", "water the ferns")),
        "share": page("Taskdeck", *ROWS, Item("Share list", "text", dialog=True), Item("Cancel", BTN, dialog=True)),
    }
    on = {("start", "open Taskdeck"): "list", ("share", "press escape"): "list", ("new", "set title=water the ferns"): "named"}
    if on_add:
        on[("list", "click Add task")] = on_add
    return FakeEnv(pages, "start", on)


PLAN = "open_place Taskdeck\nclick Add task | expect: new_item\nwrite water the ferns | where: title\ndone: new_item(water the ferns)"


def test_met_steps_and_a_typed_done_need_one_brain_call():
    hb, brain = v2(PLAN)
    result = run_task("add a task water the ferns", tasks_env(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.calls == 1 and result.rescues == 0
    assert any(line.startswith("✓ click Add task") and "new_item: 1 more row" in line for line in result.ledger)
    assert "done: new_item(water the ferns)" in result.reason


def test_nothing_changed_goes_to_the_local_ladder_not_the_brain():
    env = tasks_env(on_add="")  # Add task does nothing; the toolbar's New task works
    env.pages["list"].items.append(Item("New task", BTN))
    env.on[("list", "click New task")] = "new"
    hb, brain = v2(PLAN)
    result = run_task("add a task water the ferns", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.calls == 1 and result.rescues == 0 and result.repairs == 1
    assert "click New task" in env.log


def test_nothing_changed_after_the_ladder_reaches_the_rescue_with_that_reason():
    hb, brain = v2(PLAN, "stop: nothing adds a task here")
    result = run_task("add a task water the ferns", tasks_env(on_add=""), FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done and result.rescues == 1
    rescue = brain.asked[1][1]
    assert "nothing changed (expected new_item)" in rescue and "wait: still not met" in rescue


def test_a_different_change_is_undone_then_the_rescue_is_told_what_changed():
    hb, brain = v2(PLAN, "stop: that control shares the list")
    result = run_task("add a task water the ferns", tasks_env(on_add="share"), FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done
    assert any("a different change: closed what it opened (escape)" in line for line in result.trace), result.trace
    assert "expected new_item, but dialog_opened" in brain.asked[1][1]


def shop_env() -> FakeEnv:
    """Shopwell: typing into its search box runs the search at once (the window shows the query)."""
    home = page("Shopwell", Item("Search", "field", search=True), Item("Search", BTN), Item("Deals", "row"))
    home.window = "Shopwell"
    found = page("Shopwell", Item("Search", "field", "desk lamp", search=True), Item("Lamp one", "row"), Item("Lamp two", "row"))
    found.window = "desk lamp — Shopwell"
    pages = {"start": page("Finder", Item("Desktop", "row")), "home": home, "found": found}
    return FakeEnv(pages, "start", {("start", "open Shopwell"): "home", ("home", "set Search=desk lamp"): "found"})


def test_skip_ahead_when_a_later_expectation_is_met_by_state_already():
    plan = (
        "open_place Shopwell\nwrite desk lamp | where: Search\nclick Search | expect: results_for(desk lamp)\n"
        "done: results_for(desk lamp)"
    )
    hb, _ = v2(plan)
    env = shop_env()
    result = run_task("search shopwell for a desk lamp", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert "click Search" not in env.log
    assert any("results_for(desk lamp) met already" in line for line in result.ledger)


def test_a_work_step_is_never_skipped():
    plan = "open_place Shopwell\nwrite desk lamp | where: Search\nsubmit | expect: results_for(desk lamp)"
    env = shop_env()
    env.on[("found", "press return")] = "found"
    hb, _ = v2(plan, "stop: done already")
    result = run_task("search shopwell for a desk lamp", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert not any("met already" in line for line in result.ledger)


def test_the_end_state_is_the_done_type_never_the_plan_running_out():
    plan = "open_place Taskdeck\nclick Add task | expect: new_item\ndone: new_item(water the ferns)"
    hb, _ = v2(plan, "stop: the title was never typed")
    result = run_task("add a task water the ferns", tasks_env(), FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done
    assert any("done: " in line and "new_item(water the ferns)" in line for line in result.trace)


def test_with_no_done_line_the_last_step_proven_by_its_type_ends_it():
    hb, _ = v2("open_place Taskdeck\nclick Add task | expect: new_item")
    result = run_task("add a task", tasks_env(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done and "last step's expectation was met" in result.reason


def test_a_pane_that_opened_is_not_a_switch_turned_on():
    """A false done once: clicking a setting's name opened its pane, and words on it read as "on"."""
    focus = page("Settingsly", Item("Quiet hours", BTN), Item("Sleep", BTN))
    focus.window = "Focus"
    pane = page("Settingsly", Item("Quiet hours", "heading"), Item("Allowed people", BTN), Item("Schedule: On", "text"))
    pane.window = "Quiet hours"
    env = FakeEnv(
        {"start": page("Finder"), "focus": focus, "pane": pane},
        "start",
        {("start", "open Settingsly"): "focus", ("focus", "click Quiet hours"): "pane"},
    )
    plan = "open_place Settingsly\nclick Quiet hours | expect: toggled(Quiet hours)\ndone: toggled(Quiet hours)"
    hb, brain = v2(plan, "stop: the switch is not on this pane")
    result = run_task("turn on quiet hours", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done
    assert "expected toggled(Quiet hours), but item_opened" in brain.asked[1][1]


def test_an_old_sent_line_never_proves_this_send():
    """A false done once: "Message sent" left from an earlier message read as proof the new one went."""
    form = page(
        "Mailroom", Item("Message sent", "text"), Item("Message", "field", multiline=True), Item("Send", BTN, dialog=True)
    )
    env = FakeEnv({"start": page("Finder"), "form": form}, "start", {("start", "open Mailroom"): "form"})
    plan = "open_place Mailroom\nwrite lunch? | where: Message\nsubmit | expect: dialog_closed\ndone: dialog_closed"
    hb, _ = v2(plan, "stop: the send did not go")
    result = run_task("send lunch? in mailroom", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done


def test_a_pinned_recipe_stores_types_and_never_a_worked_out_date():
    """A pin keeps each step's kind of change and the done type, with words only from the task ({pN} or its own):
    a date the brain worked out (here from the facts' "Now:" line) is dropped from them, never replayed."""
    import json

    from karta.harness.recipes import RecipeBook

    env = tasks_env()
    env.on[("new", "set title=call the bank 11 October")] = "dated"
    env.pages["dated"] = page("Taskdeck", *ROWS, Item("call the bank 11 October", "row"), Item("title", "field"))
    plan = (
        "contract: created(task named call the bank)\n"
        "open_place Taskdeck\nclick Add task | expect: new_item\nwrite call the bank 11 October | where: title\n"
        "done: new_item(call the bank 11 October)"
    )
    hb, _ = v2(plan)
    book = RecipeBook()
    result = run_task("add a task call the bank tomorrow", env, FakeLaya(), hb, FACTS, confirm=yes, pins=book)
    assert result.done and result.pinned.startswith("pinned r1"), result.trace
    recipe = book.get("r1")
    assert [s.get("expects") for s in recipe.steps] == [None, "new_item", None]
    assert [p["kind"] for p in recipe.proofs] == ["ran", "expect", "field"] and recipe.proofs[1]["type"] == "new_item"
    assert recipe.done_shows == "new_item"  # "11 October" is no word of the task: dropped, the type stands
    assert "then" not in json.dumps(recipe.steps) and "October" not in json.dumps(recipe.proofs) + recipe.done_shows


def test_an_expectation_naming_what_was_collected_is_checked_with_it_filled_in():
    from test_harness_tools import toolbox

    from karta.harness.expect import Expect
    from karta.harness.loop import judged
    from karta.harness.tools import Collected

    tb = toolbox(FakeEnv({"a": page("Quillnote")}, "a"))
    tb.slots["reads"] = [Collected("Keep a starter", "https://bread.example/a"), Collected("Hooch", "https://bake.example/b")]
    before = page("Quillnote", Item("body", "field", multiline=True))
    body = "Worth a look:\nKeep a starter — https://bread.example/a\nHooch — https://bake.example/b"
    after = page("Quillnote", Item("body", "field", body, multiline=True))
    assert judged(tb, Expect("field_holds", "Worth a look:\n{reads}"), before, after, "done").ok
    assert not judged(tb, Expect("field_holds", "Worth a look:\n{reads}"), before, before, "done").ok
