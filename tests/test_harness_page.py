"""Per-page planning (KARTA_PLAN=page): the brain plans each page from its numbered view, aims at items by number, and
ends with look or done; a missed check is one look at the screen then; a number from a page that is gone is never
used; a pinned run keeps identities, never numbers, and replays with no call. Made-up apps and sites; fakes only."""

from __future__ import annotations

import json

import pytest
from test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness import loop, view
from karta.harness.brain import PAGE_SYS, HarnessBrain, parse_line
from karta.harness.recipes import RecipeBook
from karta.harness.types import Item, Screen


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(loop, "speak", lambda text: None)


def paged(*replies: str) -> tuple[HarnessBrain, FakeLineBrain]:
    brain = FakeLineBrain(*replies)
    return HarnessBrain(brain, prompt="v2", plan="page"), brain


def plotline() -> FakeEnv:
    """A made-up list app: Add Entry opens a focused new-entry field; Save lists the entry."""
    lst = page("Plotline", Item("Search entries", "field", region="top"), Item("Add Entry", "button", region="top-right"))
    new = page(
        "Plotline",
        Item("Search entries", "field", region="top"),
        Item("New entry", "field", focused=True, region="middle"),
        Item("Save", "button", region="middle"),
    )
    saved = page(
        "Plotline",
        Item("Search entries", "field", region="top"),
        Item("Add Entry", "button", region="top-right"),
        Item("repot the cactus", "row", region="middle"),
    )
    return FakeEnv(
        {"list": lst, "new": new, "saved": saved}, "list", {("list", "click Add Entry"): "new", ("new", "click Save"): "saved"}
    )


PAGE_ONE = "contract: created(entry named repot the cactus)\nclick #2 | expect: editor_opened\nlook"
PAGE_TWO = 'write #1 "repot the cactus"\nclick #3 | expect: new_item(repot the cactus)\ndone: new_item(repot the cactus)'


def test_each_page_is_planned_from_its_numbered_view_and_ends_with_look_or_done():
    hb, brain = paged(PAGE_ONE, PAGE_TWO)
    laya = FakeLaya()
    result = loop.run_task("add an entry repot the cactus in plotline", plotline(), laya, hb, FACTS)
    assert result.done, result.ledger
    assert result.brain_calls == 2 and [b["kind"] for b in result.brain] == ["page", "page"]
    (system, first), (_, second) = brain.asked
    assert system == PAGE_SYS and "#2 button 'Add Entry' @top-right" in first and "Done so far: (nothing yet)" in first
    assert "- click Add Entry | expect: editor_opened" in second and "#1 field 'New entry' [focused]" in second
    assert not {"target", "pick_ok"} & set(laya.asked)  # an item named by number is never Laya's pick


def test_the_parser_reads_numbers_look_and_the_numbered_forms():
    assert parse_line("look", True) == ("meta", ("look", "look"))
    assert parse_line("look: the results load next", True) == ("meta", ("look", "the results load next"))
    assert parse_line('write #7 "see you at 6"', True)[1] == {"tool": "write", "text": "see you at 6", "where": "#7"}
    assert parse_line('fill #3:"Ana Lee" #4:on', True)[1]["fields"] == {"#3": "Ana Lee", "#4": True}
    got = parse_line("collect #11, #14 | what: teapot pages | as: pots", True)[1]
    assert got["rows"] == "#11, #14" and got["what"] == "teapot pages" and got["slot"] == "pots"


def test_a_number_not_on_the_screen_is_refused_and_asked_again():
    hb, brain = paged("click #99 | expect: editor_opened\nlook", PAGE_ONE)
    scr = plotline().screen()
    stream = hb.page_plan("add an entry", "Now: Tuesday", view.build(scr))
    steps = list(stream)
    assert brain.calls == 2 and any("#99 is not on the screen given" in e for e in stream.plan.errors)
    assert steps[0]["target"] == "Add Entry" and steps[0]["at"] == {"name": "Add Entry", "kind": "button", "region": "top-right"}


def test_numbers_from_a_page_that_is_gone_are_dropped_and_the_new_page_looked_at():
    home = page("Brave Browser", Item("Search teapots", "field", region="top"), url="https://pots.example/")
    results = page(
        "Brave Browser",
        Item("Search teapots", "field", value="teapot", region="top"),
        Item("Glazed teapot, one litre", "link", region="middle", url="https://pots.example/p/1"),
        url="https://pots.example/search?q=teapot",
    )
    product = page("Brave Browser", Item("Glazed teapot, one litre", "heading"), url="https://pots.example/p/1")
    env = FakeEnv(
        {"home": home, "results": results, "product": product},
        "home",
        {("home", "press return"): "results", ("results", "click Glazed teapot, one litre"): "product"},
    )
    first = 'write #1 "teapot"\nsubmit | expect: results_for(teapot)\nclick #1 | expect: item_opened\ndone: item_opened'
    hb, brain = paged(first, "click #2 | expect: item_opened\ndone: item_opened")
    result = loop.run_task("open the glazed teapot on pots.example", env, FakeLaya(), hb, FACTS, tabs=[])
    assert result.done, result.ledger
    assert brain.calls == 2 and any("a new page" in line for line in result.trace)
    assert "click Glazed teapot, one litre" in env.log and "click Search teapots" not in env.log


def test_a_missed_check_is_one_look_at_the_screen_told_what_missed_and_never_a_guess():
    env = FakeEnv({"a": page("Plotline", Item("Archive", "button", region="top"), Item("Old entry", "row"))}, "a")
    hb, brain = paged("click #1 | expect: item_gone\ndone: item_gone", "stop: archiving does nothing here")
    laya = FakeLaya()
    result = loop.run_task("archive the old entry in plotline", env, laya, hb, FACTS)
    assert not result.done and result.rescues == 1 and brain.calls == 2
    _system, user = brain.asked[1]
    assert "Failed: click Archive | expect: item_gone -> done, but the screen did not change" in user
    assert "Tried locally on this step (code and Laya), and missed:" in user
    assert "target" not in laya.asked and "archiving does nothing here" in result.said


def test_a_numbered_click_on_something_hard_to_undo_still_asks_the_user():
    env = FakeEnv({"a": page("Plotline", Item("Delete entry", "button", region="top"))}, "a")
    asked = []
    hb, _brain = paged("click #1 | expect: item_gone\ndone: item_gone")
    result = loop.run_task("delete the entry in plotline", env, FakeLaya(), hb, FACTS, confirm=lambda q: asked.append(q) or False)
    assert asked and not result.done and "click Delete entry" not in env.log


def test_a_page_plan_with_nothing_to_do_on_an_unchanged_screen_stops_instead_of_looking_again():
    env = FakeEnv({"a": page("Plotline", Item("Loading", "text"))}, "a")
    hb, brain = paged("look: the list has not loaded")
    result = loop.run_task("open my entries in plotline", env, FakeLaya(), hb, FACTS)
    assert not result.done and brain.calls == 1 and "the list has not loaded" in result.reason


def test_collect_by_number_takes_exactly_those_items_with_no_laya_question():
    env = FakeEnv(
        {
            "r": page(
                "Brave Browser",
                Item("Brewing a first pot", "link", url="https://leafnotes.example/first-pot"),
                Item("Sponsored: buy a kettle", "link", url="https://ads.example/k"),
                Item("Cold steeping, tested", "link", url="https://steepwell.example/cold"),
                url="https://findit.example/search?q=tea",
            )
        },
        "r",
    )
    hb, _brain = paged("collect #1, #3 | what: tea guides | as: guides\ntell which tea guides | from: guides | as: list")
    laya = FakeLaya()
    result = loop.run_task("list two tea guides on findit.example", env, laya, hb, FACTS, tabs=[])
    assert result.done and "Brewing a first pot" in result.answer and "Cold steeping" in result.answer
    assert "kettle" not in result.answer and "collect_fit" not in laya.asked


def test_a_proven_page_run_pins_identities_never_numbers_and_replays_with_no_call():
    book = RecipeBook()
    hb, _brain = paged(PAGE_ONE, PAGE_TWO)
    first = loop.run_task("add an entry repot the cactus in plotline", plotline(), FakeLaya(), hb, FACTS, pins=book)
    assert first.done and first.pinned.startswith("pinned")
    steps = book.recipes[0].steps
    assert steps[0]["at"] == {"name": "Add Entry", "kind": "button", "region": "top-right"}
    assert not any(str(v).startswith("#") for s in steps for v in s.values()), steps
    hb2, brain2 = paged()
    again = loop.run_task("add an entry repot the cactus in plotline", plotline(), FakeLaya(), hb2, FACTS, pins=book)
    assert again.done and again.replayed == "exact" and again.brain_calls == 0 and brain2.calls == 0
    assert '"#' not in json.dumps(book.recipes[0].steps)


def test_page_calls_go_to_the_page_model_on_a_brain_that_takes_one(monkeypatch):
    seen = []

    class ModelBrain:
        def stream_lines(self, system, user, model=None):
            seen.append(model)
            yield "stop: nothing to do"

    monkeypatch.setenv("KARTA_PAGE_MODEL", "a-page-model")
    hb = HarnessBrain(ModelBrain(), prompt="v2", plan="page")
    list(hb.page_plan("x", "Now: Tuesday", view.build(Screen("Plotline", [Item("Add Entry", "button")]))))
    assert seen == ["a-page-model"]


def test_blind_planning_stays_behind_the_flag(monkeypatch):
    monkeypatch.setenv("KARTA_PLAN", "blind")
    assert not HarnessBrain(FakeLineBrain(), prompt="v2").page
    monkeypatch.setenv("KARTA_PLAN", "page")
    assert HarnessBrain(FakeLineBrain(), prompt="v2").page
    assert not HarnessBrain(FakeLineBrain(), prompt="v1").page  # the old words format plans blind


def test_a_look_after_a_miss_that_says_done_goes_to_the_end_state_not_another_look():
    env = plotline()
    env.at = "saved"  # the entry is listed already
    hb, _brain = paged("click #2 | expect: new_item(repot the cactus)\nlook", "done: new_item(repot the cactus)")
    result = loop.run_task("add an entry repot the cactus in plotline", env, FakeLaya(), hb, FACTS)
    # the done line is checked as the end state (the entry was there before, so it is no proof): no look in between
    assert result.ledger[-1].startswith("✗ (end of plan)") and "page call 3: 3 of 3 items; failed: (end of plan)" in "\n".join(
        result.trace
    )


def test_a_pinned_step_that_misses_gets_one_look_for_that_step_and_the_recipe_s_rest_carries_on():
    book = RecipeBook()
    hb, _brain = paged(PAGE_ONE, PAGE_TWO)
    assert loop.run_task("add an entry repot the cactus in plotline", plotline(), FakeLaya(), hb, FACTS, pins=book).done
    moved = plotline()  # the app renamed its control: the pinned item is gone
    lst = moved.pages["list"]
    lst.items = [Item("Search entries", "field", region="top"), Item("New Entry", "button", region="top-right")]
    moved.on = {("list", "click New Entry"): "new", ("new", "click Save"): "saved"}
    hb2, brain2 = paged("click #2 | expect: editor_opened")
    result = loop.run_task("add an entry repot the cactus in plotline", moved, FakeLaya(), hb2, FACTS, pins=book)
    assert result.done and result.replayed == "exact" and brain2.calls == 1, result.ledger
    assert "Still to do after it (a pinned path):" in brain2.asked[0][1]
    assert book.recipes[0].version == 2 and book.recipes[0].steps[0]["at"]["name"] == "New Entry"
