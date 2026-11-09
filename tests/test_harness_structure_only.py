"""A mail or messaging screen goes to the brain as its structure only until the user says yes (harness.md, "Consent for
mail and messages"): the plan's starting screen, a look's or a page call's numbered view and a rescue's screen alike,
by one rule (view.Structure, flagged by Toolbox.for_brain). Made-up apps and sites; fakes only."""

from __future__ import annotations

import pytest
from test_harness_carry import told
from test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page, toolbox

from karta.harness import facts as facts_mod
from karta.harness import loop, view
from karta.harness.brain import HarnessBrain, screen_digest, screen_text
from karta.harness.decide import NOT_PRIVATE, PRIVATE
from karta.harness.memory import HarnessMemory
from karta.harness.types import Item

ASK = "May I send text from Postbox to Claude to summarise it?"
PRIVATE_WORDS = ("Odile", "Varga", "Lunch moved", "Tomas", "Invoice 4471", "Kiri", "Board slides", "corner bistro")


def inbox(url: str | None = None):
    """Postbox (a made-up mail app): its controls, three messages as rows and the newest one open beside them."""
    scr = page(
        "Postbox",
        Item("Search mail", "field", region="top", value="from odile"),
        Item("Compose", "button", region="top-left"),
        Item("Inbox", "link", region="left"),
        Item("Sent", "link", region="left"),
        Item("Inbox", "heading", region="middle"),
        Item("Odile Varga, Lunch moved to Friday, 10:42", "row", region="middle"),
        Item("Tomas Reyes, Invoice 4471 overdue, 09:15", "row", region="middle"),
        Item("Kiri Ota, Board slides for Monday, Yesterday", "row", region="middle"),
        Item("Lunch moved to Friday", "heading", region="right"),
        Item("Odile Varga", "link", region="right"),
        Item("Hi, the team lunch moves to Friday at 1pm at the corner bistro.", "text", region="right"),
        Item("Reply", "button", region="right"),
        Item("Archive", "button", region="right"),
        url=url,
    )
    scr.window = "Inbox \u2013 3 messages"
    return scr


def mail_env(url: str | None = None) -> FakeEnv:
    return FakeEnv({"m": inbox(url)}, "m")


@pytest.fixture
def mail_app(monkeypatch):
    told(monkeypatch, [])
    monkeypatch.setattr(loop, "speak", lambda text: None)
    monkeypatch.setattr(facts_mod, "message_apps", lambda: frozenset({"Postbox"}))  # LaunchServices: a mail app


def plan_prompt(brain: FakeLineBrain) -> str:
    return next(user for _system, user in brain.asked if user.startswith("Task:"))


def leaks(text: str) -> list[str]:
    return [w for w in PRIVATE_WORDS if w in text]


# ---------------------------------------------------------------- the plan's starting screen


def test_without_consent_the_plan_sees_controls_and_row_numbers_never_row_text(mail_app):
    brain = FakeLineBrain("stop: nothing to do")
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), brain, FACTS, HarnessMemory(":memory:"), lambda q: False)
    prompt = plan_prompt(brain)
    assert leaks(prompt) == [], prompt
    assert "from odile" not in prompt and "Inbox \u2013 3 messages" not in prompt  # a field's text, the window title
    assert view.WITHHELD_NOTE in prompt
    assert "Rows: 3: row 1, row 2, row 3" in prompt, prompt
    for label in ("'Compose'", "'Reply'", "'Archive'", "'Search mail' (holds 10 chars)", "'Inbox'", "'Sent'"):
        assert label in prompt, (label, prompt)


def test_with_consent_the_plan_prompt_is_unchanged(mail_app):
    memory = HarnessMemory(":memory:")
    memory.set_consent("Postbox", True)
    brain, asked = FakeLineBrain("stop: nothing to do"), []
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), brain, FACTS, memory, asked.append)
    assert asked == [] and screen_digest(inbox()) in plan_prompt(brain)
    assert "Odile Varga, Lunch moved to Friday" in plan_prompt(brain)


def test_unknown_consent_is_asked_once_before_the_first_brain_call(mail_app):
    memory, order = HarnessMemory(":memory:"), []
    brain = FakeLineBrain("stop: nothing to do")
    yes = lambda q: order.append((q, brain.calls)) or True  # noqa: E731  (the brain calls made when it is asked)
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), brain, FACTS, memory, yes)
    assert order == [(ASK, 0)], order
    assert "Lunch moved to Friday" in plan_prompt(brain) and memory.consent("Postbox") is True
    order.clear()
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), FakeLineBrain("stop: no"), FACTS, memory, yes)
    assert order == []  # kept: not asked again


def test_no_one_to_ask_sends_structure_and_keeps_nothing(mail_app):
    memory, brain = HarnessMemory(":memory:"), FakeLineBrain("stop: nothing to do")
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), brain, FACTS, memory, None)
    assert leaks(plan_prompt(brain)) == [] and memory.consent("Postbox") is None


def test_a_web_mailbox_by_laya_follows_the_same_rule(monkeypatch):
    told(monkeypatch, [])
    monkeypatch.setattr(loop, "speak", lambda text: None)
    laya = FakeLaya(private_screen=lambda s: ch("D2 page state", (PRIVATE, 0.97), (NOT_PRIVATE, 0.03)))
    laya.private_screen = lambda s: laya._answer("private_screen", None, s)
    brain = FakeLineBrain("stop: nothing to do")
    env = mail_env("https://post.example/inbox?q=from%3Aodile")
    loop.run_task("archive the newest email", env, laya, brain, FACTS, None, lambda q: False)
    prompt = plan_prompt(brain)
    assert leaks(prompt) == [] and "q=from" not in prompt and "Site: post.example" in prompt, prompt


def test_other_screens_are_unchanged(monkeypatch):
    told(monkeypatch, [])
    monkeypatch.setattr(loop, "speak", lambda text: None)  # message_apps is empty (conftest): Postbox is not a mail app
    brain, asked = FakeLineBrain("stop: nothing to do"), []
    loop.run_task("archive the newest email", mail_env(), FakeLaya(), brain, FACTS, None, asked.append)
    assert asked == [] and screen_digest(inbox()) in plan_prompt(brain)
    tools = toolbox(mail_env())
    scr = inbox()
    assert tools.for_brain(scr) is scr
    assert view.build(tools.for_brain(scr), "archive it").text == view.build(scr, "archive it").text


# ---------------------------------------------------------------- a look's and a page call's view


def test_a_look_view_follows_the_same_rule(mail_app):
    tools = toolbox(mail_env(), confirm=lambda q: False)
    seen = view.build(tools.for_brain(inbox()), "archive the newest email")
    assert leaks(seen.text) == [] and view.WITHHELD_NOTE in seen.text, seen.text
    assert "#8..#10 3 rows (row 1 to row 3) @middle" in seen.text, seen.text
    assert "#1 field 'Search mail' (holds 10 chars) @top" in seen.text and "'Archive'" in seen.text
    assert "window: [its title]" in seen.text
    row_one = seen.labels.index("row 1") + 1  # the brain can aim at the newest message without reading it
    assert seen.item(row_one).name == "Odile Varga, Lunch moved to Friday, 10:42"
    assert view.identity(seen.item(row_one), seen.screen)["name"] == seen.item(row_one).name  # kept by identity, as ever
    with_yes = toolbox(mail_env(), confirm=lambda q: True)
    assert view.build(with_yes.for_brain(inbox()), "x").text == view.build(inbox(), "x").text


def test_a_page_call_clicks_row_one_by_number_without_reading_it(mail_app):
    opened = page("Postbox", Item("Reply", "button"))
    env = FakeEnv({"m": inbox(), "open": opened}, "m", {("m", "click Odile Varga, Lunch moved to Friday, 10:42"): "open"})
    brain = FakeLineBrain("click #8\ndone: page_on(Postbox)")
    hb = HarnessBrain(brain, prompt="v2", plan="page")
    loop.run_task("open the newest email", env, FakeLaya(), hb, FACTS, None, lambda q: False)
    first = brain.asked[0][1]
    assert leaks(first) == [] and "row 1 to row 3" in first, first
    assert env.log[0] == "click Odile Varga, Lunch moved to Friday, 10:42", env.log


def test_the_v1_screen_lines_follow_the_same_rule(mail_app):
    tools = toolbox(mail_env(), confirm=lambda q: False)
    text = screen_text(tools.for_brain(inbox()))
    assert leaks(text) == [] and "'row 1' row" in text and "'Compose' button" in text, text
