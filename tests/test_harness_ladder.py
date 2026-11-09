"""The per-step try policy (karta/harness/repair.py, loop.py): a step that missed gets up to LOCAL_TRIES local tries,
always in this order (a wait for a late change, the same action another way, Laya's next-best candidates), before any
brain look; at most MAX_STEP_LOOKS looks; MAX_STEP_TRIES tries in all; a step that sends or commits is never swapped
for another candidate. Made-up apps; fakes only."""

from __future__ import annotations

from itertools import pairwise

import pytest
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page, toolbox

from karta.harness import loop, repair
from karta.harness.brain import HarnessBrain
from karta.harness.types import Item, Outcome, ToolResult

TASK = "keep the grocery note in plumora"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(loop, "speak", lambda text: None)


def auto(*replies: str) -> tuple[HarnessBrain, FakeLineBrain]:
    brain = FakeLineBrain(*replies)
    return HarnessBrain(brain, prompt="v2", plan="auto"), brain


def plumora(works: str = "Stash", menu: bool = True) -> FakeEnv:
    """A made-up notes app: its "Keep" button and menu command do nothing; `works` files the note (a new row)."""
    items = [Item("Notes", "heading"), Item("Bills", "row"), Item("Keep", BTN), Item("Stash", BTN), Item("Shelve", BTN)]
    if menu:
        items.append(Item("Note > Keep", BTN, menu=True))
    kept = page("Plumora", *items, Item("Groceries", "row"))
    return FakeEnv({"list": page("Plumora", *items), "kept": kept}, "list", {("list", f"click {works}"): "kept"})


def local_tries(trace: list[str]) -> list[str]:
    return [line.split(": ", 1)[1] for line in trace if line.lstrip().startswith("local try ")]


def clicks(env: FakeEnv) -> list[str]:
    return [a for a in env.log if a.startswith("click")]


def test_the_rungs_run_in_order_wait_then_another_way_then_the_next_best():
    env = plumora()
    hb, brain = auto("click Keep | expect: new_item\ndone: new_item")
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert result.done, result.trace
    assert local_tries(result.trace) == [repair.WAIT, repair.ANOTHER_WAY, repair.NEXT_BEST]
    assert brain.calls == 1 and result.rescues == 0 and result.repairs == 1  # no look: fixed locally
    first, second, *_others, last = clicks(env)
    assert {first, second} == {"click Keep", "click Note > Keep"} and last == "click Stash"
    assert env.log.index("wait") < env.log.index(second)  # the wait came before any other way
    assert "repaired locally (Laya's next-best 'Stash')" in result.ledger[0]


def test_another_way_fixes_it_before_any_candidate_is_tried():
    env = plumora(works="Note > Keep")
    hb, brain = auto("click Keep | expect: new_item\ndone: new_item")
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert result.done and brain.calls == 1, result.trace
    assert local_tries(result.trace) == [repair.WAIT, repair.ANOTHER_WAY]
    assert clicks(env) == ["click Keep", "click Note > Keep"]


def test_a_late_change_is_caught_by_the_wait_and_nothing_else_is_clicked():
    env = plumora(works="never")
    waits = []

    def late(e):  # the note shows a moment after the click: once the click's own waits are over
        waits.append(1)
        if len(waits) == 5:
            e.at = "kept"

    env.on[("list", "wait")] = late
    hb, brain = auto("click Keep | expect: new_item\ndone: new_item")
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert result.done and brain.calls == 1, result.trace
    assert local_tries(result.trace) == [repair.WAIT] and clicks(env) == ["click Keep"]


def test_every_local_try_comes_before_the_first_look_and_looks_stop_at_two():
    env = plumora(works="never", menu=False)
    hb, brain = auto(
        "click Keep | expect: new_item\ndone: new_item",
        "click Stash | expect: new_item\ncontinue",  # look 1: a fix that misses too
        "click Shelve | expect: new_item\ncontinue",  # look 2: and so does this one
        "click Notes | expect: new_item\ncontinue",  # never asked: the step is out of looks
    )
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert not result.done and brain.calls == 1 + loop.MAX_STEP_LOOKS
    assert result.gave_up == loop.GAVE_UP_LOOKS and result.said  # stops with its spoken reason
    trace = result.trace
    first_look = next(n for n, line in enumerate(trace) if line.startswith("look 1:"))
    before = local_tries(trace[:first_look])
    assert before and before[0] == repair.WAIT and len(before) <= repair.LOCAL_TRIES
    assert all(len(local_tries(trace[a:b])) <= repair.LOCAL_TRIES for a, b in segments(trace))


def segments(trace: list[str]):
    """Each stretch of the trace between two brain looks."""
    cuts = [0, *[n for n, line in enumerate(trace) if line.startswith("look ")], len(trace)]
    return pairwise(cuts)


def test_the_try_policy_is_five_tries_three_local_then_two_looks():
    assert loop.MAX_STEP_TRIES == 5 and repair.LOCAL_TRIES == 3 and loop.MAX_STEP_LOOKS == 2
    assert repair.LOCAL_TRIES + loop.MAX_STEP_LOOKS == loop.MAX_STEP_TRIES
    assert loop.BUDGET_S == 120.0


def test_a_step_with_fewer_tries_left_climbs_only_that_far():
    env = plumora(works="never")
    tb = toolbox(env, FakeLaya(), task=TASK)
    step = {"tool": "click", "target": "Keep", "expects": "new_item"}
    assert tb.run(step).outcome is Outcome.NO_CHANGE
    miss = ToolResult(Outcome.NO_CHANGE, "clicked 'Keep': nothing changed (expected new_item)")
    fixed = repair.repair(tb, step, miss, lambda s, r: r, lambda scr: False, room=1)
    assert fixed is not None and not fixed.ok and fixed.tries == 1
    assert local_tries(tb.trace) == [repair.WAIT] and clicks(env) == ["click Keep"]
    assert repair.repair(tb, step, miss, lambda s, r: r, lambda scr: False, room=0) is None  # no try left: none


def test_the_step_counts_each_local_try_and_the_cap_ends_it():
    env = plumora(works="never", menu=False)
    hb, _brain = auto("click Keep | expect: new_item\ndone: new_item", "stop: nothing keeps it")
    result = loop.run_task(TASK, env, FakeLaya(), hb, FACTS)
    assert not result.done
    assert result.steps[0]["local_tries"] == 2  # the wait and the next-best (no other way to the same command)


def test_a_step_that_commits_is_never_swapped_for_another_candidate():
    items = [Item("Draft", "heading"), Item("Send", BTN), Item("Schedule", BTN), Item("Note > Send", BTN, menu=True)]
    env = FakeEnv(
        {"a": page("Plumora", *items), "b": page("Plumora", *items, Item("Sent", "row"))}, "a", {("a", "click Schedule"): "b"}
    )
    hb, _brain = auto("click Send | expect: new_item\ndone: new_item", "stop: it did not send")
    result = loop.run_task("send the draft in plumora", env, FakeLaya(), hb, FACTS, confirm=lambda q: True)
    assert not result.done
    assert clicks(env) == ["click Send"]  # never the menu's Send again, never the next-best
    assert local_tries(result.trace) == [repair.WAIT]
    assert any("never swapped for another candidate" in line for line in result.trace)


def test_a_page_changed_in_place_goes_on_to_the_next_best_verified_by_its_type():
    head = Item("Folders", "heading")
    env = FakeEnv(
        {
            "a": page("Plumora", head, Item("Bills.pdf", "row"), Item("Pin", BTN), Item("Export", BTN)),
            "pinned": page("Plumora", head, Item("Bills.pdf", "row"), Item("Unpin", BTN), Item("Export", BTN)),
            "done": page(
                "Plumora", head, Item("Bills.pdf", "row"), Item("Unpin", BTN), Item("Export", BTN), Item("Groceries.pdf", "row")
            ),
        },
        "a",
        {("a", "click Pin"): "pinned", ("pinned", "click Export"): "done"},
    )

    order = ("Pin", "Export")

    def target(_intent, items):  # sure of Pin first, then Export, then the rest
        ranked = sorted(items, key=lambda i: order.index(i.name) if i.name in order else len(order))
        return ch("D3 target", *[(i.name, 0.8 if n == 0 else 0.05) for n, i in enumerate(ranked)], ("none of these", 0.0))

    laya = FakeLaya(target=target)
    hb, brain = auto("click save it as a file | expect: new_item\ndone: new_item")
    result = loop.run_task("save the note as a file in plumora", env, laya, hb, FACTS)
    assert result.done and brain.calls == 1, result.trace
    assert clicks(env) == ["click Pin", "click Export"]  # Pin stayed (nothing to undo), Export proved new_item
    assert any("the next-best is verified by its type" in line for line in result.trace)


def test_a_read_that_found_nothing_scrolls_and_reads_again():
    top = page("Plumora", Item("Welcome", "heading"), Item("Menu", BTN))
    lower = page("Plumora", Item("Welcome", "heading"), Item("Watering ferns every week", "row"))
    env = FakeEnv({"top": top, "lower": lower}, "top", {("top", "scroll down"): "lower"})
    hb, brain = auto("collect watering guides | as: guides\ntell the watering guides | from: guides | as: list")
    result = loop.run_task("what watering guides does plumora have", env, FakeLaya(), hb, FACTS)
    assert result.done and brain.calls == 1, result.trace
    assert "scrolled down: read again" in result.ledger[0]
    assert local_tries(result.trace) == [repair.WAIT, repair.ANOTHER_WAY]
