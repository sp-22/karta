"""Local repair (karta/harness/repair.py): a mechanical miss is fixed by code and Laya before the brain is asked, and
the brain's rescue is kept for a plan that is wrong. The bench (karta/harness/repair_cases.py) on made-up apps with
the fakes: every kind of miss fixed with no rescue, a wrong plan with exactly one. Nothing touches the Mac."""

from __future__ import annotations

import pytest
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, FakeLineBrain, ch, page

from karta.harness import loop, progress, repair, repair_cases
from karta.harness.brain import HarnessBrain
from karta.harness.decide import NONE
from karta.harness.types import Item, Outcome, Screen, ToolResult

LAYA = {  # what the fake Laya says where its sure defaults would hide the miss a case is about
    "focused-field": {"target": lambda intent, items: ch("D3 target", (NONE, 0.9))},
    "unsure-pick": {"target": lambda intent, items: ch("D3 target", ("⇪", 0.4), ("Help", 0.3), (NONE, 0.3))},
}


def laya_for(name: str) -> FakeLaya:
    return FakeLaya(**LAYA.get(name, {}))


def run_case(name: str):
    case = next(c for c in repair_cases.CASES if c.name == name)
    mac = case.mac()
    result = loop.run_task(
        case.command, mac, laya_for(name), HarnessBrain(repair_cases.CaseBrain(case.plan, case.rescue), prompt="v2"), repair_cases.FACTS,
        confirm=lambda _q: True, tabs=[],
    )  # fmt: skip
    return case, mac, result


# ---------------------------------------------------------------- the bench


@pytest.mark.parametrize("name", [c.name for c in repair_cases.CASES if not c.rescues])
def test_each_mechanical_miss_is_fixed_locally_with_no_rescue(name):
    case, mac, result = run_case(name)
    assert result.done, result.reason
    assert result.rescues == 0 and result.brain_calls == 1, result.ledger
    assert result.repairs >= 1 or case.typed_alone, result.ledger
    assert case.check is None or case.check(mac, result) is None


def test_a_plan_that_is_truly_wrong_still_gets_its_one_rescue():
    _case, _mac, result = run_case("wrong-plan")
    assert result.done and result.rescues == 1 and result.brain_calls == 2, result.ledger


def test_the_bench_meets_the_gate():
    rows = [row for case in repair_cases.CASES for row in repair_cases.bench(laya_for(case.name), [case])]
    assert all(r["ok"] and r["within_gate"] for r in rows), [(r["case"], r["problem"]) for r in rows if not r["ok"]]
    per_task = sum(r["brain_calls"] for r in rows if not r["rescues"]) / len([r for r in rows if not r["rescues"]])
    assert per_task == 1.0
    text = repair_cases.report(rows)
    assert "rescues" in text and "local repairs" in text


def test_text_goes_in_once_however_many_ways_were_tried():
    for name, box in (("paste-ignored", "Invitees"), ("token-field", "To"), ("stacked", "Title")):
        _case, mac, _result = run_case(name)
        assert len(mac.typed[box]) == 1, (name, mac.typed)
    _case, mac, _result = run_case("token-field")
    assert mac.log.count("keys") == 1 and "press tab" in mac.log  # keys once, then tab made the token


# ---------------------------------------------------------------- the text rungs


class LazyMac(repair_cases.QuirkyMac):
    """A field whose value reads empty until its second click (the paste went in; the read lags)."""

    def __init__(self):
        form = Screen("Gatherly", [Item("Title", "field", region="middle"), Item("Create", "button", region="bottom")])
        super().__init__({"form": form}, "form")
        self.held, self.clicks = "", 0

    def set_text(self, item, text):
        self.log.append(f"set {item.name}")
        self.typed.setdefault(item.name, []).append(text)
        self.held = text

    def click(self, item):
        super().click(item)
        self.clicks += item.kind == "field"
        if self.clicks >= 2 and self.held:
            self._set("Title", self.held)


@pytest.mark.v1  # a v1 plan: words for the done line
def test_a_box_that_turns_out_to_hold_the_text_is_never_typed_into_again():
    mac = LazyMac()
    result = loop.run_task(
        "add a team lunch event in gatherly", mac, FakeLaya(), FakeLineBrain("write Team lunch | where: Title\ndone: Team lunch"),
        repair_cases.FACTS, confirm=lambda _q: True, tabs=[],
    )  # fmt: skip
    assert result.done and result.repairs == 1 and result.rescues == 0, result.ledger
    assert mac.typed["Title"] == ["Team lunch", "Team lunch"]  # the step's own two pastes, none by the repair
    assert "keys" not in mac.log


def test_a_long_or_several_line_text_is_never_typed_key_by_key():
    mac = repair_cases.paste_ignored()
    long = "x" * (repair.MAX_KEYS + 1)
    result = loop.run_task(
        "add an event", mac, FakeLaya(), FakeLineBrain(f"write {long} | where: Invitees"), repair_cases.FACTS,
        confirm=lambda _q: True, tabs=[],
    )  # fmt: skip
    assert "keys" not in mac.log and not result.done
    assert any("too long to type key by key" in line for line in result.trace)


# ---------------------------------------------------------------- what is never repaired


def test_which_misses_a_repair_takes():
    miss = ToolResult(Outcome.UNEXPECTED, "it ran, but expected new_item, but dialog_opened")
    still = ToolResult(Outcome.NO_CHANGE, "clicked 'Add': nothing changed (expected new_item)")
    typed = ToolResult(Outcome.UNEXPECTED, "typed 'x' but 'To' holds ''")
    clicked = {"tool": "click", "expects": "new_item"}
    assert repair.kind_of(clicked, miss) == "expected" and repair.kind_of(clicked, still) == "expected"
    # a click with no expectation: its tool's own check missed; the ladder still tries the next-best pick
    assert repair.kind_of({"tool": "click"}, still) == "expected"
    assert repair.kind_of({"tool": "write"}, still) is None  # any other step with no expectation: its tool is its proof
    assert repair.kind_of({"tool": "collect"}, ToolResult(Outcome.NOT_FOUND, "nothing like 'x' on screen (0 read)")) == "collect"
    assert repair.kind_of({"tool": "tell"}, ToolResult(Outcome.NOT_FOUND, "")) is None
    assert repair.kind_of({"tool": "write"}, typed) == "text"
    assert repair.kind_of({"tool": "fill"}, typed) == "text"
    assert repair.kind_of({"tool": "click"}, ToolResult(Outcome.NOT_FOUND, "")) == "missing"
    assert repair.kind_of({"tool": "click"}, ToolResult(Outcome.UNSURE, "no sure pick for 'x'")) == "unsure"
    assert repair.kind_of({"tool": "write"}, ToolResult(Outcome.BLOCKED_BY_DIALOG, "")) == "dialog"
    for outcome in (Outcome.REFUSED, Outcome.NEEDS_PERMISSION, Outcome.STUCK, Outcome.NO_DATA):
        assert repair.kind_of({"tool": "click"}, ToolResult(outcome, "")) is None
    assert repair.kind_of({"tool": "open_place"}, ToolResult(Outcome.NOT_FOUND, "")) is None
    assert repair.kind_of({"tool": "submit"}, ToolResult(Outcome.NOT_FOUND, "")) is None
    assert repair.kind_of({"tool": "submit"}, ToolResult(Outcome.BLOCKED_BY_DIALOG, "")) is None


def test_a_step_that_sends_or_commits_is_never_redone():
    assert repair.risky_step({"tool": "submit", "expect": "sent"})
    assert repair.risky_step({"tool": "answer", "choice_meaning": "yes"})
    assert repair.risky_step({"tool": "click", "target": "Send"})
    assert repair.risky_step({"tool": "click", "target": "Delete account"})
    assert not repair.risky_step({"tool": "click", "target": "Compose"})


def test_a_send_button_not_found_is_left_to_the_rescue():
    env = FakeEnv({"a": page("Chirp", Item("Design team", "heading"))}, "a")
    brain = FakeBrain({"steps": [{"tool": "click", "target": "Send", "then": "sent"}]}, {"steps": [], "stop": "no send"})
    result = loop.run_task("send it", env, FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9))), brain, FACTS)
    assert not result.done and result.repairs == 0
    assert not any(a.startswith("click") for a in env.log)


# ---------------------------------------------------------------- the brain: two rescues, never a repeat in other words


def stubborn_env() -> FakeEnv:
    env = FakeEnv({"a": page("Postbox", Item("To recipients", "field", region="right"), Item("Send", BTN, region="right"))}, "a")
    env.stubborn = {"To recipients"}
    return env


def test_a_rescue_that_rewords_the_failed_step_is_refused():
    # the rescue's write is the failed one in other words, on the same screen: refused with no run, and the rescue was
    # shown what was tried there, so nothing untried is left
    brain = FakeLineBrain("write ana@example.com | where: To recipients", "write ana@example.com | where: To - recipients field")
    env = stubborn_env()
    result = loop.run_task("mail ana@example.com", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.rescues == 1 and brain.calls == 2
    assert result.gave_up == loop.GAVE_UP_TRIED and "'To recipients'" in result.reason and "none worked" in result.reason
    look = brain.asked[1][1]
    assert "Already tried on this screen" in look and "- write 'To recipients' (field) -> " in look


@pytest.mark.blind
def test_the_rescue_prompt_says_what_was_tried_locally():
    brain = FakeLineBrain("write ana@example.com | where: To recipients", "stop: cannot")
    loop.run_task("mail ana@example.com", stubborn_env(), FakeLaya(), brain, FACTS)
    _system, user = brain.asked[1]
    assert "Tried locally on this step" in user and "tab:" in user
    assert "A rescue is for a step that is wrong in" in _system


def test_a_step_out_of_retries_stops_and_says_so_instead_of_asking_the_user():
    asked = []
    brain = FakeLineBrain(
        "click Archive everything | then: Archived",
        "click Archive all | then: Archived",
        "click Archive the lot | then: Archived",
        "click Archive again | then: Archived",
    )
    env = FakeEnv({"a": page("Postbox", Item("Inbox", "heading"))}, "a")
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = loop.run_task("archive my inbox", env, laya, brain, FACTS, ask=lambda q: asked.append(q) or "")
    assert result.rescues <= loop.MAX_RESCUES == 2 and brain.calls <= 3
    # each rescue's step counts its own retries, but the chain of the step and its fixes is past its cap: Karta
    # stops and says why, it does not ask to go on
    assert asked == [] and not result.done
    assert result.said.startswith("I couldn't find Archive") and result.failed_step.startswith("click Archive")


@pytest.mark.parametrize(
    ("fix", "same"),
    [
        ([{"tool": "write", "text": "hi", "where": "To - recipients field"}], True),
        ([{"tool": "write", "text": "hi", "where": "the To recipients box"}], True),
        ([{"tool": "write", "text": "hello", "where": "To recipients"}], False),  # other text
        ([{"tool": "click", "target": "To recipients"}, {"tool": "write", "text": "hi", "where": "To recipients"}], False),
        ([{"tool": "write", "text": "hi", "where": "Subject"}], False),
    ],
)
def test_what_counts_as_a_rewording(fix, same):
    # a fix opening with the failed step in other words is the same action (progress.Action.same)
    failed = progress.action_of({"tool": "write", "text": "hi", "where": "To recipients"})
    assert progress.action_of(fix[0]).same(failed) is same


def test_a_click_reworded_with_a_near_is_the_same_click():
    failed = progress.action_of({"tool": "click", "target": "Compose", "then": "New Message"})
    assert progress.action_of({"tool": "click", "target": "Compose", "near": "Mail", "then": "To"}).same(failed)
    icon, box = ({"tool": "click", "target": t} for t in ("the search icon", "the search box"))
    assert not progress.action_of(icon).same(progress.action_of(box))


def test_harness_brain_rescue_takes_the_local_tries():
    brain = FakeLineBrain("stop: no")
    hb = HarnessBrain(brain)
    hb.rescue(
        "t", FACTS, page("App"), [], [], {"tool": "click", "target": "x"}, ToolResult(Outcome.NOT_FOUND, ""), {}, [],
        local=["waited: nothing changed"],
    )  # fmt: skip
    assert "waited: nothing changed" in brain.asked[0][1]
