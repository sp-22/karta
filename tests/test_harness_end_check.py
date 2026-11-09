"""The end check's reference per kind (harness.md, "The loop and the ladder": the end state). An event (a dialog
closed, an item made or gone) is measured from just before the step that should have caused it, never from the
start screen where that dialog did not exist yet; a state (a switch on) on the screen now, against the start; the
last step's own proof of the same expectation stands; words on screen at the start never prove it. Made-up apps,
fake brain."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page, toolbox

from karta.harness.brain import HarnessBrain
from karta.harness.loop import run_task, typed_end
from karta.harness.types import Item, MacFacts, Outcome, ToolResult

FACTS = MacFacts(["Tallybook", "Pebblenote", "Lumenpad", "Chirpline"], {}, [], datetime(2026, 10, 6, 9, 0))


def yes(_q: str) -> bool:
    return True


def v2(*replies: str) -> tuple[HarnessBrain, FakeLineBrain]:
    lines = FakeLineBrain(*replies)
    return HarnessBrain(lines, prompt="v2", plan="blind"), lines


def tally() -> FakeEnv:
    """Tallybook: Export opens a dialog, Save closes it; no dialog at the start."""
    home = [Item("Ledger", "heading"), Item("March", "row"), Item("April", "row"), Item("Export", BTN)]
    dialog = [
        Item("Export as PDF", "text", dialog=True),
        Item("Save", BTN, dialog=True),
        Item("Cancel", BTN, dialog=True, cancels=True),
    ]
    pages = {"home": page("Tallybook", *home), "dialog": page("Tallybook", *home, *dialog), "saved": page("Tallybook", *home)}
    return FakeEnv(pages, "home", {("home", "click Export"): "dialog", ("dialog", "click Save"): "saved"})


def test_dialog_closed_is_measured_from_before_the_step_that_closed_it_not_the_start():
    # the start had no dialog: "nothing changed since the start" is no verdict on a dialog the task opened and closed
    hb, brain = v2("click Export | expect: dialog_opened\nclick Save | expect: dialog_closed\ndone: dialog_closed")
    result = run_task("export the ledger in tallybook", tally(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.calls == 1 and result.rescues == 0
    assert "dialog_closed" in result.reason and "last step proved it" in result.reason


def test_dialog_closed_with_a_last_click_that_declared_nothing_is_measured_from_before_that_click():
    hb, brain = v2("click Export | expect: dialog_opened\nclick Save\ndone: dialog_closed")
    result = run_task("export the ledger in tallybook", tally(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.calls == 1 and "a dialog closed" in result.reason


def notes() -> FakeEnv:
    """Pebblenote: four folders at the start; Recipes lists two notes; New note opens a sheet whose Create adds one."""
    folders = [Item(n, "row") for n in ("Recipes", "Travel", "Work", "Ideas")]
    recipes = [Item("Recipes", "heading"), Item("Soup", "row"), Item("Bread", "row"), Item("New note", BTN)]
    sheet = [
        Item("Title", "field", dialog=True, focused=True),
        Item("Create", BTN, dialog=True),
        Item("Cancel", BTN, dialog=True, cancels=True),
    ]
    pages = {
        "folders": replace(page("Pebblenote", *folders), window="Folders"),
        "recipes": replace(page("Pebblenote", *recipes), window="Recipes"),
        "sheet": replace(page("Pebblenote", *recipes, *sheet), window="Recipes"),
        "made": replace(page("Pebblenote", *recipes[:3], Item("Untitled", "row"), recipes[3]), window="Recipes"),
    }
    on = {("folders", "click Recipes"): "recipes", ("recipes", "click New note"): "sheet", ("sheet", "click Create"): "made"}
    return FakeEnv(pages, "folders", on)


def test_new_item_is_measured_from_before_the_step_that_made_it_not_the_start():
    # from the start (four folders) the Recipes view with three notes is no new item; from before Create it is
    plan = (
        "click Recipes | expect: item_opened\nclick New note | expect: dialog_opened\n"
        "click Create | expect: dialog_closed\ndone: new_item"
    )
    hb, brain = v2(plan)
    result = run_task("add a note in pebblenote recipes", notes(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.calls == 1 and "1 more row" in result.reason


def lumen() -> FakeEnv:
    """Lumenpad: the switch is on the Display tab, not on the General tab the task starts on."""
    general = page("Lumenpad", Item("General", "tab"), Item("Display", "tab"), Item("Launch at login", "checkbox", checked=False))
    display = page(
        "Lumenpad", Item("General", "tab"), Item("Display", "tab"), Item("Use dark appearance", "checkbox", checked=False)
    )
    general, display = replace(general, window="General"), replace(display, window="Display")
    env = FakeEnv({"general": general, "display": display}, "general", {("general", "click Display"): "display"})

    def flip(e: FakeEnv) -> None:
        scr = e.pages["display"]
        scr.items = [Item(i.name, i.kind, checked=True) if i.kind == "checkbox" else i for i in scr.items]

    env.on[("display", "click Use dark appearance")] = flip
    return env


def test_a_switch_not_on_the_start_screen_is_toggled_from_before_the_click_that_flipped_it():
    plan = "click Display | expect: item_opened\nclick Use dark appearance\ndone: toggled(Use dark appearance)"
    hb, _brain = v2(plan)
    result = run_task("turn on dark appearance in lumenpad", lumen(), FakeLaya(), hb, FACTS, confirm=yes)
    assert result.done, result.trace
    assert "turned on" in result.reason


def test_a_dialog_that_was_never_closed_is_still_not_done():
    env = tally()
    env.on.pop(("dialog", "click Save"))  # Save does nothing: the dialog stays
    hb, _brain = v2("click Export | expect: dialog_opened\nclick Save\ndone: dialog_closed", "stop: it will not close")
    result = run_task("export the ledger in tallybook", env, FakeLaya(), hb, FACTS, confirm=yes)
    assert not result.done


def chat(old: bool) -> tuple[object, object, object]:
    """Chirpline: the thread at the start (holding an earlier "see you at noon" when `old`), a screen mid-task that
    shows no messages, and the thread now holding the line."""
    said = [Item("see you at noon", "text")] if old else []
    start = page("Chirpline", Item("Mira", "heading"), *said, Item("Message", "field"))
    between = page("Chirpline", Item("Mira", "heading"), Item("Loading", "text"), Item("Message", "field"))
    now = page("Chirpline", Item("Mira", "heading"), Item("see you at noon", "text"), Item("Message", "field"))
    return start, between, now


def test_words_on_screen_at_the_start_never_prove_done_from_a_later_screen():
    for old, want in ((True, False), (False, True)):
        start, between, now = chat(old)
        tools = toolbox(FakeEnv({"now": now}, "now"), task="tell mira see you at noon")
        step = {"tool": "click", "target": "Reload", "expects": "list_changed"}
        done = [(step, ToolResult(Outcome.OK, "list_changed: 3 items changed"))]
        end = typed_end(tools, "new_item(see you at noon)", done, start, start, {id(step): between})
        assert end.ok is want, (old, end)


def test_the_last_step_s_own_proof_never_stands_for_words_shown_at_the_start():
    start, between, now = chat(True)
    tools = toolbox(FakeEnv({"now": now}, "now"), task="tell mira see you at noon")
    step = {"tool": "submit", "expect": "see you at noon", "expects": "new_item(see you at noon)"}
    done = [(step, ToolResult(Outcome.OK, "new_item(see you at noon): a new text holding 'see you at noon'"))]
    assert not typed_end(tools, "new_item(see you at noon)", done, between, start, {id(step): between}).ok
