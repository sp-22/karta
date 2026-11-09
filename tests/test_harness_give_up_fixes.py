"""The give-up rules the live run found wrong (harness.md, "Ending a task" and "Local repair"): a rescue's new step
counts its own retries, a missed check waits for a late change locally before any rescue, and the user's no ends the
task at once, with the dialog the task opened cancelled. Made-up apps only."""

from __future__ import annotations

from test_harness_loop import FakeLineBrain
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, page

from karta.harness import loop
from karta.harness.loop import run_task
from karta.harness.types import Item


def tiles_env() -> FakeEnv:
    """Three tiles that do nothing; Shuffle does nothing; Refresh shows a new row."""
    grid = page("Gridly", Item("Tile one", BTN), Item("Tile two", BTN), Item("Tile three", BTN), Item("Shuffle", BTN),
                Item("Refresh", BTN), Item("Row a", "row"))  # fmt: skip
    fresh = page("Gridly", *grid.items[:5], Item("Row a", "row"), Item("Row b", "row"))
    return FakeEnv({"grid": grid, "fresh": fresh}, "grid", {("grid", "click Refresh"): "fresh"})


def test_a_rescues_new_step_counts_its_own_retries():
    """The first step spent its retries on next-best picks; the first fix missed once. Counted against the first
    step, that miss ended the task before the second fix was ever tried."""
    brain = FakeLineBrain(
        "click Tile | expect: list_changed\ndone: new_item",
        "click Shuffle | expect: list_changed",
        "click Refresh | expect: list_changed",
    )
    env = tiles_env()
    result = run_task("show the next rows in gridly", env, FakeLaya(), brain, FACTS)
    assert result.rescues == 2 and "click Refresh" in env.log, result.trace
    assert result.done and result.gave_up == ""


LATE = 1  # the done check re-reads once on a screen that has not changed yet


def test_a_done_check_that_missed_waits_for_a_late_change_before_any_rescue():
    """The done check found the new row not there yet; it comes a moment later: proven by the local wait, no rescue."""
    env = FakeEnv(
        {
            "a": page("Listly", Item("New item", BTN), Item("Milk", "row")),
            "b": page("Listly", Item("New item", BTN), Item("Milk", "row"), Item("Title", "field", focused=True)),
            "c": page("Listly", Item("New item", BTN), Item("Milk", "row"), Item("Eggs", "row")),
        },
        "a",
        {("a", "click New item"): "b"},
    )
    waits = []

    def late(e: FakeEnv) -> None:  # the row shows only once the done check's own re-read is over
        waits.append(1)
        if len(waits) > LATE:
            e.at = "c"

    env.on[("b", "wait")] = late
    brain = FakeLineBrain("click New item | expect: editor_opened\ndone: new_item(Eggs)")
    result = run_task("add eggs in listly", env, FakeLaya(), brain, FACTS)
    assert result.done and result.rescues == 0 and brain.calls == 1, result.trace
    assert result.repairs == 1


def test_the_users_no_ends_the_task_and_cancels_the_dialog_the_task_opened():
    """The confirmation the task brought up is answered no by the user: no rescue, no question, and the dialog is
    closed by its own cancel control so nothing is left half-done. (The up-front yes covered the click on "Delete
    note" only; the dialog's Delete was never in the plan, so it asks when it comes.)"""
    note = page("Notely", Item("Shopping", "row"), Item("Delete note", BTN))
    alert = page(
        "Notely",
        Item("Shopping", "row"),
        Item("Delete this note?", "text", dialog=True),
        Item("Delete", BTN, dialog=True),
        Item("Cancel", BTN, dialog=True, cancels=True),
    )
    env = FakeEnv(
        {"note": note, "alert": alert}, "note", {("note", "click Delete note"): "alert", ("alert", "click Cancel"): "note"}
    )
    brain = FakeLineBrain("click Delete note | expect: dialog_opened\nanswer yes | expect: item_gone\ndone: item_gone(Shopping)")
    asked: list[str] = []
    result = run_task(
        "delete the shopping note in notely", env, FakeLaya(), brain, FACTS, confirm=lambda q: "Delete note" in q,
        ask=lambda q: asked.append(q) or "",
    )  # fmt: skip
    assert not result.done and result.rescues == 0 and brain.calls == 1 and asked == [], result.trace
    assert env.log == ["click Delete note", "click Cancel"] and env.at == "note"
    assert result.reason.startswith("stopped: the user said no")


def test_a_rescues_stop_ends_the_task_without_a_question():
    env = FakeEnv({"a": page("Notely", Item("Folders", "heading"))}, "a")
    brain = FakeLineBrain(
        "collect the archived notes | count: 2 | as: notes\ntell what is archived | from: notes", "stop: there is no archive here"
    )
    asked: list[str] = []
    result = run_task("what is archived in notely", env, FakeLaya(), brain, FACTS, ask=lambda q: asked.append(q) or "n")
    assert not result.done and result.rescues == 1 and asked == [], result.trace
    assert result.reason == "stopped: there is no archive here" and loop.MAX_RESCUES >= 1
