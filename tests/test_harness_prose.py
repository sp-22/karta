"""The strict v2 step grammar (harness.md, "The plan"): a line is a step only when it matches a tool's grammar
exactly; a sentence that starts with a tool's word is prose, ignored with a trace note, and never reaches the risk
gate as an action. A reply whose only "steps" were prose has no steps: the repair ask. Made-up apps, fake brain."""

from __future__ import annotations

from datetime import datetime

import pytest
from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness.brain import PROSE, HarnessBrain, parse_line
from karta.harness.loop import run_task
from karta.harness.types import Item, MacFacts

FACTS = MacFacts(["Basketly", "Finder"], {}, [], datetime(2026, 10, 6, 9, 0))

SENTENCES = [
    "Submit the order once the address is filled in.",
    "Submit the form",
    "Click the Pay button to finish the order.",
    "Open the cart and check the total.",
    "Write a short reply to Mira.",
    "Send the payment to Ana now.",
    "submit the order now",
    "click the pay button and then close the window once it is done.",
    "click Pay.",
    "answer yes, the order should go through now.",
    "back to the cart",
    "scroll down to the end of the page",
    "wait until the page loads",
    "reach the checkout page",
    "click Pay | and then confirm it",
    "open_place the store the user always buys from, which is Basketly.",
    "collect everything you need from the cart.",
    "Here is the plan:",
    "I'll open the cart first, then pay.",
]

STEPS = {
    "click Pay | expect: dialog_opened": {"tool": "click", "target": "Pay", "expects": "dialog_opened"},
    "click #12 | expect: toggled": {"tool": "click", "target": "#12", "expects": "toggled"},
    "click Save as... | expect: dialog_opened": {"tool": "click", "target": "Save as...", "expects": "dialog_opened"},
    "click Export…": {"tool": "click", "target": "Export…"},
    "click: the Archive folder": {"tool": "click", "target": "the Archive folder"},
    "submit": {"tool": "submit", "expect": ""},
    "submit | expect: new_item(see you at 6)": {"tool": "submit", "expects": "new_item(see you at 6)", "expect": "see you at 6"},
    "submit dialog_closed": {"tool": "submit", "expects": "dialog_closed", "expect": ""},
    "submit | expect: posted": {"tool": "submit", "expect": "posted"},
    "answer Allow once | expect: dialog_closed": {"tool": "answer", "choice_meaning": "Allow once", "expects": "dialog_closed"},
    "write see you at 6 | where: message box": {"tool": "write", "text": "see you at 6", "where": "message box"},
    'write #7 "Thanks. See you soon!"': {"tool": "write", "text": "Thanks. See you soon!", "where": "#7"},
    'fill #3:"Ana Lee" #4:on': {"tool": "fill", "fields": {"#3": "Ana Lee", "#4": True}},
    "type ls -la | grep src\\n": {"tool": "type", "text": "ls -la | grep src\n"},
    "open_place Basketly | site: basketly.example": {"tool": "open_place", "what": "Basketly", "site": "basketly.example"},
    "find Odile | meaning: the chat with Odile | alt: Odil": {
        "tool": "find",
        "text": "Odile",
        "meaning": "the chat with Odile",
        "alternatives": ["Odil"],
    },
    "reach $30 | of: printer paper": {"tool": "reach", "amount": "$30", "of": "printer paper"},
    "collect links to kite plans | count: 4 | as: kites": {
        "tool": "collect",
        "what": "links to kite plans",
        "count": 4,
        "slot": "kites",
    },
    "- scroll up": {"tool": "scroll", "direction": "up"},
    "wait 2": {"tool": "wait", "seconds": 2.0},
    "back": {"tool": "back"},
}


@pytest.mark.parametrize("line", SENTENCES)
def test_a_sentence_starting_with_a_tool_word_is_never_a_step(line):
    kind, why = parse_line(line, v2=True)
    assert kind == PROSE, (line, why)


@pytest.mark.parametrize("line", list(STEPS))
def test_a_real_step_still_parses(line):
    assert parse_line(line, v2=True) == ("step", STEPS[line])


def test_meta_lines_and_thoughts_are_not_prose():
    assert parse_line("done: dialog_closed", v2=True) == ("meta", ("done_shows", "dialog_closed"))
    assert parse_line("stop: the store is closed", v2=True)[0] == "meta"
    assert parse_line("# the order total is $42", v2=True)[0] == "skip"
    assert parse_line("press return", v2=True)[0] == "error"  # a known tool refused: the repair ask explains why


def brain(*replies: str, plan: str = "blind") -> tuple[HarnessBrain, FakeLineBrain]:
    lines = FakeLineBrain(*replies)
    return HarnessBrain(lines, prompt="v2", plan=plan), lines


def test_a_reply_of_only_prose_has_no_steps_and_gets_the_repair_ask():
    hb, lines = brain("Submit the order.\nSend the payment to Ana.", "click Pay | expect: dialog_opened\ndone: dialog_opened")
    plan = hb.stream_plan("pay for the cart in basketly", FACTS, page("Basketly", Item("Pay", BTN)))
    steps = list(plan)
    assert lines.calls == 2 and [s["tool"] for s in steps] == ["click"]
    assert "never a sentence" in lines.asked[1][1] and "Submit the order." in lines.asked[1][1]
    assert len(plan.plan.prose) == 2


def test_prose_beside_real_steps_is_ignored_with_no_repair_ask():
    hb, lines = brain("I will pay now.\nclick Pay | expect: dialog_opened\nSubmit the order.\ndone: dialog_opened")
    plan = hb.stream_plan("pay for the cart in basketly", FACTS, page("Basketly", Item("Pay", BTN)))
    assert [s["tool"] for s in plan] == ["click"] and lines.calls == 1
    assert plan.plan.errors == [] and len(plan.plan.prose) == 2


def cart() -> tuple[FakeEnv, list[str]]:
    asked: list[str] = []
    env = FakeEnv(
        {"cart": page("Basketly", Item("Cart", "heading"), Item("Submit order", BTN), Item("Send payment", BTN))}, "cart"
    )
    return env, asked


@pytest.mark.parametrize("mode", ["blind", "auto", "page"])
def test_the_risk_gate_is_never_reached_from_prose(mode):
    env, asked = cart()
    prose = "Submit the order now.\nSend the payment to Ana.\nClick Submit order to finish."
    hb, lines = brain(prose, prose, plan=mode)
    result = run_task("check my cart in basketly", env, FakeLaya(), hb, FACTS, confirm=lambda q: asked.append(q) or True)
    assert not result.done
    acted = [a for a in env.log if a != "wait"]  # the harness's own waits for the screen are no action
    assert asked == [] and acted == [], (asked, acted)
    assert "never a sentence" in lines.asked[1][1]  # the plan's one repair ask (then the end of the plan's own call)
    assert any("ignored as prose" in line for line in result.trace), result.trace


def test_a_look_s_prose_never_runs_and_the_plan_s_rest_carries_on():
    env = FakeEnv(
        {
            "cart": page("Basketly", Item("Cart", "heading"), Item("Checkout", BTN), Item("Submit order", BTN)),
            "checkout": page("Basketly", Item("Checkout", "heading"), Item("Submit order", BTN)),
        },
        "cart",
        {("cart", "click Checkout"): "checkout"},
    )
    asked: list[str] = []
    hb, _lines = brain(
        "click Go to checkout | expect: item_opened\ndone: item_opened",
        "Submit the order now.\ncontinue",
        plan="auto",
    )
    laya = FakeLaya()
    run_task("open the checkout in basketly", env, laya, hb, FACTS, confirm=lambda q: asked.append(q) or True)
    assert "click Submit order" not in env.log and not any("Submit order" in q for q in asked)
