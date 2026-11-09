"""Auto planning (KARTA_PLAN=auto, the default): the brain plans the whole task once, blind, with memory; a step whose
check misses gets one look, the numbered view of the screen at that checkpoint with what was expected and what
changed, and its `#n` steps fix that step before the plan's rest carries on; a number gone stale gets a look again; a
place the plan never named gets one look; a proven run pins identities and replays with no call. Made-up apps; fakes
only."""

from __future__ import annotations

import pytest
from test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page

from karta.harness import loop, view
from karta.harness.brain import LOOK_SYS, PLAN_SYS_V2, HarnessBrain, parse_line
from karta.harness.decide import NONE, labels
from karta.harness.recipes import RecipeBook
from karta.harness.types import Item


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(loop, "speak", lambda text: None)


def auto(*replies: str, brain=None) -> tuple[HarnessBrain, FakeLineBrain]:
    brain = brain or FakeLineBrain(*replies)
    return HarnessBrain(brain, prompt="v2", plan="auto"), brain


def quillery(add: str = "Add Note") -> FakeEnv:
    """A made-up notes app: `add` opens a focused new-note field; Keep lists the note. "Add Note" does nothing
    when the app calls its control otherwise."""
    lst = page("Quillery", Item("Search notes", "field", region="top"), Item(add, "button", region="top-right"))
    new = page(
        "Quillery",
        Item("Search notes", "field", region="top"),
        Item("Note text", "field", focused=True, region="middle"),
        Item("Keep", "button", region="middle"),
    )
    kept = page(
        "Quillery",
        Item("Search notes", "field", region="top"),
        Item(add, "button", region="top-right"),
        Item("buy oat milk", "row", region="middle"),
    )
    return FakeEnv(
        {"list": lst, "new": new, "kept": kept}, "list", {("list", f"click {add}"): "new", ("new", "click Keep"): "kept"}
    )


def literal() -> FakeLaya:
    """Laya sure of a control only when the step names it as the screen does; else sure it is none of them."""

    def target(intent, items):
        names = labels(items)
        hit = next((k for k, v in names.items() if v.name.lower() == intent.lower()), None)
        return ch("D3 target", (hit, 0.95), (NONE, 0.05)) if hit else ch("D3 target", (NONE, 0.95))

    return FakeLaya(target=target)


PLAN = (
    "contract: created(note named buy oat milk)\nclick Add Note | expect: editor_opened\nwrite buy oat milk | where: Note text\n"
    "click Keep | expect: new_item(buy oat milk)\ndone: new_item(buy oat milk)"
)
TASK = "add a note buy oat milk in quillery"


def test_auto_is_the_default_and_blind_and_page_stay_selectable(monkeypatch):
    monkeypatch.delenv("KARTA_PLAN", raising=False)
    assert HarnessBrain(FakeLineBrain(), prompt="v2").auto
    monkeypatch.setenv("KARTA_PLAN", "blind")
    hb = HarnessBrain(FakeLineBrain(), prompt="v2")
    assert not hb.auto and not hb.page
    monkeypatch.setenv("KARTA_PLAN", "page")
    assert HarnessBrain(FakeLineBrain(), prompt="v2").page
    assert parse_line("continue", True) == ("meta", ("carry", "continue"))


def test_a_run_whose_steps_pass_their_checks_makes_one_call():
    hb, brain = auto(PLAN)
    result = loop.run_task(TASK, quillery(), FakeLaya(), hb, FACTS)
    assert result.done and result.brain_calls == 1, result.ledger
    assert [b["kind"] for b in result.brain] == ["plan"] and brain.asked[0][0] == PLAN_SYS_V2
    assert not any(line.startswith("look") for line in result.trace)


def test_a_missed_check_looks_at_the_numbered_screen_and_its_steps_fix_it_then_the_plan_carries_on():
    env = quillery(add="Compose")  # the app calls it Compose: the planned Add Note is not there
    hb, brain = auto(PLAN, "click #2 | expect: editor_opened\ncontinue")
    result = loop.run_task(TASK, env, literal(), hb, FACTS)
    assert result.done and result.brain_calls == 2 and result.rescues == 1, result.ledger
    system, user = brain.asked[1]
    assert system == LOOK_SYS
    assert "Failed: click Add Note | expect: editor_opened" in user and "Expected: editor_opened" in user
    assert "What happened: " in user
    assert "Still to do after it (the plan):\n- write buy oat milk | where: Note text\n- click Keep" in user
    assert "#2 button 'Compose' @top-right" in user  # the view of the screen at the checkpoint
    assert "click Compose" in env.log and env.log.index("click Compose") < env.log.index("click Keep")
    assert any("look 1:" in line for line in result.trace)


class Shifting(FakeLineBrain):
    """A brain whose first look's number goes stale before it runs: the app renames its control meanwhile."""

    def __init__(self, env: FakeEnv, *replies: str):
        super().__init__(*replies)
        self.env = env

    def stream_lines(self, system: str, user: str):
        if system == LOOK_SYS and self.calls == 1:  # the first look: the control is renamed as the reply comes
            lst = self.env.pages["list"]
            lst.items = [Item("Start a page", "button", region="top-right") if i.name == "Compose" else i for i in lst.items]
            self.env.on[("list", "click Start a page")] = "new"
        yield from super().stream_lines(system, user)


def test_a_number_gone_stale_gets_a_look_again_never_a_guess():
    env = quillery(add="Compose")
    brain = Shifting(env, PLAN, "click #2 | expect: editor_opened\ncontinue", "click #2 | expect: editor_opened\ncontinue")
    hb, _ = auto(brain=brain)
    result = loop.run_task(TASK, env, literal(), hb, FACTS)
    assert result.done and [b["kind"] for b in result.brain] == ["plan", "look", "look"], result.ledger
    assert "no longer on screen as the page showed it" in brain.asked[2][1]
    assert "click Compose" not in env.log and "click Start a page" in env.log


def test_a_place_the_plan_never_named_gets_one_look():
    home = page("Brave Browser", Item("Sign in with Keyring", "link", region="middle"), url="https://quillery.example/")
    other = page("Brave Browser", Item("Continue", "link", region="middle"), url="https://keyring.example/auth")
    done = page("Brave Browser", Item("buy oat milk", "row"), url="https://quillery.example/notes")
    env = FakeEnv(
        {"home": home, "other": other, "done": done},
        "home",
        {("home", "click Sign in with Keyring"): "other", ("other", "click Continue"): "done"},
    )
    plan = "click Sign in with Keyring | expect: item_opened\nclick Continue | expect: item_opened\ndone: item_opened"
    hb, brain = auto(plan, "continue")
    result = loop.run_task("open my notes on quillery.example", env, FakeLaya(), hb, FACTS, tabs=[])
    assert result.done and [b["kind"] for b in result.brain] == ["plan", "look"], result.ledger
    assert "Arrived: after click Sign in with Keyring" in brain.asked[1][1] and "keyring.example" in brain.asked[1][1]
    assert "Still to do after it (the plan):\n- click Continue" in brain.asked[1][1]


def test_a_proven_auto_run_pins_identities_and_replays_with_no_call():
    book = RecipeBook()
    env = quillery(add="Compose")
    hb, _ = auto(PLAN, "click #2 | expect: editor_opened\ncontinue")
    first = loop.run_task(TASK, env, literal(), hb, FACTS, pins=book)
    assert first.done and first.pinned.startswith("pinned"), first.trace
    clicks = [s for s in book.recipes[0].steps if s["tool"] == "click"]
    assert clicks[0]["at"] == {"name": "Compose", "kind": "button", "region": "top-right"}  # the look's #2
    assert clicks[1]["at"] == {"name": "Keep", "kind": "button", "region": "middle"}  # a blind click, as it pressed
    assert not any(str(v).startswith("#") for s in book.recipes[0].steps for v in s.values())
    hb2, brain2 = auto()
    again = loop.run_task(TASK, quillery(add="Compose"), literal(), hb2, FACTS, pins=book)
    assert again.done and again.replayed == "exact" and again.brain_calls == 0 and brain2.calls == 0


def test_a_look_that_only_says_continue_needs_no_repair_ask_for_a_stray_sentence():
    hb, brain = auto("The plan's rest fits this screen.\ncontinue")
    why = "Arrived: after click Add Note, the screen is quillery."
    stream = hb.look("add a note", "Now: Tuesday", view.build(quillery().screen()), why=why, rest=["click Keep"])
    assert list(stream) == [] and stream.plan.carry == "continue" and brain.calls == 1
