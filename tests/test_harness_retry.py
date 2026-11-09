"""Try, verify, next (karta/harness/retry.py): a wrong pick is undone and the next-best clicked before any brain call."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, FakeLineBrain, ch, page

from karta.core import events
from karta.harness import tools as tools_mod
from karta.harness.brain import HarnessBrain
from karta.harness.decide import NONE
from karta.harness.loop import run_task
from karta.harness.retry import MAX_TRIES
from karta.harness.types import Item


def v2(*replies: str) -> HarnessBrain:
    """A brain that plans in the v2 format: each click declares its kind of change."""
    return HarnessBrain(FakeLineBrain(*replies), prompt="v2")


SHOP = "https://shop.example.com/"


def prefer(*order: str) -> FakeLaya:
    """A Laya sure of the first name of `order` still on offer, then the next."""

    def target(_intent, items):
        ranked = sorted(items, key=lambda i: order.index(i.name) if i.name in order else len(order))
        return ch("D3 target", *[(i.name, 0.8 if n == 0 else 0.05) for n, i in enumerate(ranked)], (NONE, 0.0))

    return FakeLaya(target=target)


def shop_env(*links: str, good: str) -> FakeEnv:
    """A site whose links each open their own page; `good`'s page is the one the step wants; back returns home."""
    pages = {"home": page("Brave Browser", Item("Shop", "heading"), *[Item(n, "link") for n in links], url=SHOP)}
    on = {}
    for name in links:
        key = name.lower()
        pages[key] = page("Brave Browser", Item(f"{name} today", "heading"), Item("Home", "link"), url=f"{SHOP}{key}")
        on[("home", f"click {name}")] = key
        on[(key, "back")] = "home"
    return FakeEnv(pages, "home", on)


@pytest.fixture
def seen():
    got: list[dict] = []
    events.subscribe(got.append)
    yield got
    events._listeners.remove(got.append)


def clicks(env: FakeEnv) -> list[str]:
    return [a for a in env.log if a != "wait"]


def test_a_wrong_laya_pick_is_undone_by_back_and_the_next_best_meets_the_expectation(seen):
    env = shop_env("Offers", "Deals", "Help", good="Deals")
    brain = v2("click the sale page | expect: item_opened(Deals)\ndone: item_opened(Deals)")
    result = run_task("open the sale page", env, prefer("Offers", "Deals", "Help"), brain, FACTS)
    assert result.done, result.trace
    assert result.brain_calls == 1  # the plan only: no rescue
    assert clicks(env) == ["click Offers", "back", "click Deals"]
    assert result.ledger[0].startswith("✓ click") and "2nd try; 'Offers' was wrong" in result.ledger[0]
    assert result.steps[0]["tries"] == 2 and result.steps[0]["wrong"] == ["Offers"]
    trace = "\n".join(result.trace)
    assert "retry 2/3: 'Offers' was wrong" in trace and "went back" in trace
    picks = [e for e in seen if e["kind"] == "pick"]
    assert [p["label"] for p in picks] == ["Offers", "Deals"] and picks[0]["how"] == "Laya" and picks[0]["p"] == 0.8
    assert picks[1]["instead_of"] == "Offers"
    assert [e["wrong"] for e in seen if e["kind"] == "retry"] == ["Offers"]


def test_a_click_that_changed_nothing_goes_to_the_next_best_without_any_undo():
    env = FakeEnv(
        {
            "a": page("Notes", Item("Folders", "heading"), Item("Archive", BTN), Item("Export", BTN)),
            "b": page("Notes", Item("Saved a copy", "text")),
        },
        "a",
        {("a", "click Export"): "b"},
    )
    brain = v2("click save a copy of it | expect: list_changed\ndone: list_changed")
    result = run_task("save a copy of the note", env, prefer("Archive", "Export"), brain, FACTS)
    assert result.done, result.trace
    assert result.brain_calls == 1 and clicks(env) == ["click Archive", "click Export"]  # no back, no escape
    assert "nothing to undo" in "\n".join(result.trace)


def test_a_wrong_pick_that_opened_a_popup_is_closed_with_escape_first():
    rows = (Item("Folders", "heading"), Item("Groceries", "row"), Item("Share", BTN), Item("Export", BTN))
    env = FakeEnv(
        {
            "a": page("Notes", *rows),
            "popup": page("Notes", *rows, Item("Share with", "heading", dialog=True), Item("Invite", BTN, dialog=True)),
            "b": page("Notes", *rows, Item("Groceries.pdf", "row")),
        },
        "a",
        {("a", "click Share"): "popup", ("popup", "press escape"): "a", ("a", "click Export"): "b"},
    )
    brain = v2("click save it as a file | expect: new_item\ndone: new_item")
    result = run_task("save the note as a file", env, prefer("Share", "Export"), brain, FACTS)
    assert result.done, result.trace
    assert result.brain_calls == 1 and clicks(env) == ["click Share", "press escape", "click Export"]


def test_a_page_changed_in_place_stays_and_the_next_best_is_verified_by_its_type_before_the_brain():
    env = FakeEnv(
        {
            "a": page("Notes", Item("Folders", "heading"), Item("Pin", BTN), Item("Export", BTN)),
            "pinned": page("Notes", Item("Folders", "heading"), Item("Unpin", BTN), Item("Export", BTN)),
        },
        "a",
        {("a", "click Pin"): "pinned"},
    )
    brain = v2("click save it as a file | expect: new_item\ndone: new_item", "stop: cannot export")
    result = run_task("save the note as a file", env, prefer("Pin", "Export"), brain, FACTS)
    assert not result.done and result.brain_calls == 2  # Export does nothing either: then the brain
    assert [c for c in clicks(env) if c.startswith("click")][:2] == ["click Pin", "click Export"]
    assert sum(c.startswith("click") for c in clicks(env)) == MAX_TRIES
    assert "the next-best is verified by its type" in "\n".join(result.trace)


def test_a_risky_target_is_never_retried():
    env = FakeEnv({"a": page("Notes", Item("Folders", "heading"), Item("Delete draft", BTN), Item("Tidy up", BTN))}, "a")
    brain = FakeBrain(
        {"steps": [{"tool": "click", "target": "clear it away"}], "done_shows": "All clear"},
        {"steps": [], "stop": "nothing cleared"},
    )
    result = run_task("clear the draft away", env, prefer("Delete draft", "Tidy up"), brain, FACTS, confirm=lambda q: True)
    assert not result.done and result.brain_calls == 2
    assert clicks(env) == ["click Delete draft"]  # the next-best is never clicked after a hard-to-undo one
    assert "no retry: hard to undo" in "\n".join(result.trace)


def test_an_exact_label_match_is_not_retried():
    env = FakeEnv(
        {
            "a": page("Notes", Item("Folders", "heading"), Item("Export", BTN), Item("Export all", BTN)),
            "b": page("Notes", Item("Choose a format", "heading")),
        },
        "a",
        {("a", "click Export"): "b"},
    )
    brain = v2("click Export | expect: new_item\ndone: new_item", "stop: export needs a format")
    result = run_task("export the note", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.brain_calls == 2 and clicks(env) == ["click Export"]
    assert "no retry: an exact match" in "\n".join(result.trace)


def test_at_most_max_tries_then_the_rescue_sees_every_wrong_pick():
    env = shop_env("Offers", "Deals", "Coupons", "Help", good="none of them")
    brain = v2("click the sale page | expect: item_opened(sale)\ndone: item_opened(sale)", "stop: no sale page here")
    result = run_task("open the sale page", env, prefer("Offers", "Deals", "Coupons", "Help"), brain, FACTS)
    assert not result.done and result.brain_calls == 2
    assert clicks(env) == ["click Offers", "back", "click Deals", "back", "click Coupons"]
    assert sum(a.startswith("click") for a in env.log) == MAX_TRIES
    assert result.ledger[0].startswith("✗ click") and "tried 3 picks: 'Offers', 'Deals', 'Coupons'" in result.ledger[0]
    rescue = brain.brain.asked[1][1]
    assert "Offers" in rescue and "Deals" in rescue  # the notes: it does not suggest them again


def test_follow_mode_slows_a_laya_click_and_sends_its_frame(monkeypatch, seen):
    monkeypatch.setenv("KARTA_FOLLOW", "1")
    slept: list[float] = []
    monkeypatch.setattr(tools_mod.time, "sleep", slept.append)
    frame = SimpleNamespace(x=10.0, y=20.0, w=30.0, h=40.0, centre=(25.0, 40.0))
    env = FakeEnv(
        {
            "a": page("Notes", Item("Folders", "heading"), Item("Archive", BTN, ref=frame), Item("Export", BTN)),
            "b": page("Notes", Item("Saved a copy", "text")),
        },
        "a",
        {("a", "click Archive"): "b"},
    )
    brain = v2("click save a copy of it | expect: list_changed\ndone: list_changed")
    result = run_task("save a copy of the note", env, prefer("Archive", "Export"), brain, FACTS)
    assert result.done, result.trace
    pick = next(e for e in seen if e["kind"] == "pick")
    assert pick["frame"] == {"x": 10.0, "y": 20.0, "w": 30.0, "h": 40.0} and slept == [tools_mod.FOLLOW_S]


def test_the_hud_shows_how_each_pick_was_made():
    hud = pytest.importorskip("karta.hud")
    assert hud.pick_text({"label": "Deals", "how": "Laya", "p": 0.64, "instead_of": "Offers"}) == (
        "◎ 'Deals' (Laya 64% · 'Offers' was wrong)",
        "Laya 64% · 'Offers' was wrong",
    )
    assert hud.pick_text({"label": "ADD", "how": "code: named alike", "p": None})[1] == "code: named alike"
