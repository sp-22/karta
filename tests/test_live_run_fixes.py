"""What an interrupted run of many live tasks showed, held on made-up apps: menu commands named only by their exact
last part and commands that run something are risky; a native app is a login page only by its password field; a new
window of the app in front is a window that opened; the user is asked once, up front, about a plan's key actions; and
one step gets at most two looks."""

from __future__ import annotations

import pytest
from test_harness_auto import auto, literal, quillery
from test_harness_loop import FakeLineBrain
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, ch, page, toolbox

from karta.harness import expect, loop, risk
from karta.harness.decide import LOGIN, READY
from karta.harness.loop import run_task
from karta.harness.tell import ahead_line
from karta.harness.tools import exact_name, titled
from karta.harness.types import Item, Screen


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(loop, "speak", lambda text: None)


# ---------------------------------------------------------------- menu commands


def shell_app(*extra: Item) -> Screen:
    return page(
        "Shellby",
        Item("~ — zsh", "text"),
        Item("Shell > New Window", "menu", menu=True),
        Item("Shell > New Window with Same Command", "menu", menu=True, shortcut="cmd+shift+n"),
        *extra,
    )


def test_a_menu_command_is_matched_only_by_its_exact_last_part():
    env = FakeEnv(
        {"a": shell_app(), "b": page("Shellby", Item("~ — zsh", "text"))}, "a", {("a", "click Shell > New Window"): "b"}
    )
    assert toolbox(env, laya=literal(), task="open a new window in shellby").tool_click("New Window").ok
    assert env.log == ["click Shell > New Window"]
    only = FakeEnv({"a": page("Shellby", Item("Shell > New Window with Same Command", "menu", menu=True))}, "a")
    result = toolbox(only, laya=literal(), task="open a new window in shellby").tool_click("New Window")
    assert not result.ok and not any(a.startswith("click") for a in only.log)  # never a command that merely starts so


def test_the_row_titled_so_rule_never_takes_a_menu_command_open_or_in_the_bar():
    open_menu = [Item("New Window with Same Command", "menu"), Item("New Window with Profile", "menu")]
    assert titled("New Window", [open_menu[0]]) is None
    assert titled("New Window", [Item("Shell > New Window with Same Command", "menu", menu=True)]) is None
    assert titled("Asha Rao", [Item("Asha Rao see you at five", "row")]) is not None  # a list row still is
    env = FakeEnv({"a": page("Shellby", Item("~ — zsh", "text"), open_menu[0])}, "a")
    assert not toolbox(env, laya=literal(), task="open a new window").tool_click("New Window").ok
    assert not any(a.startswith("click") for a in env.log)


def test_commands_that_run_or_rerun_something_are_risky():
    for label in ("New Window with Same Command", "Run Script…", "Execute", "Re-run", "Run again"):
        assert risk.risky("click", label) == "run", label
    assert risk.risky("click", "Running total") is None and risk.risky("click", "New Window") is None
    rerun = Item("Shell > New Window with Same Command", "menu", menu=True)
    assert not exact_name("New Window with Same Command", rerun)  # a risky command only when named in full
    assert exact_name("Shell > New Window with Same Command", rerun)


# ---------------------------------------------------------------- a native app is no login page by Laya's word


def test_a_native_app_is_never_a_login_page_without_a_password_field():
    sure_login = FakeLaya(
        screen_kind=lambda s, p: ch("D2 page state", (LOGIN, 0.99)),
        page_state=lambda w, s: ch("D2 page state", (LOGIN, 0.9), (READY, 0.09)),
    )
    terminal = page("Shellby", Item("~ — zsh", "text"), Item("Last login: Monday", "text"))
    tb = toolbox(FakeEnv({"a": terminal}, "a"), laya=sure_login)
    assert not tb.asks_sign_in(terminal)
    assert tb.ready("Shellby", "").ok  # Laya's "login" ignored on a native app
    web = page("Lumen", Item("Sign in to continue", "text"), url="https://keyring.example/")
    assert toolbox(FakeEnv({"a": web}, "a"), laya=sure_login).asks_sign_in(web)
    locked = page("Shellby", Item("Password", "field", secret=True))
    assert toolbox(FakeEnv({"a": locked}, "a"), laya=sure_login).ready("Shellby", "").outcome.value.startswith("a login")


# ---------------------------------------------------------------- a new window of the app in front


def test_a_new_window_of_the_front_app_is_window_opened():
    before = Screen("Shellby", [Item("~ — zsh", "text"), Item("old output", "text")], window="~ — zsh", windows=1)
    after = Screen("Shellby", [Item("~ — zsh", "text"), Item("Last login", "text")], window="~ — zsh", windows=2)
    verdict = expect.verify(expect.Expect("window_opened"), before, after)
    assert verdict.ok and "2" in verdict.proof
    moved = Screen("Shellby", before.items, window="~ — zsh", window_at="40,60,700,500")
    front = Screen("Shellby", before.items, window="~ — zsh", window_at="62,82,700,500")
    assert expect.verify(expect.Expect("window_opened"), moved, front).ok  # another window took the front
    assert front.fingerprint() != moved.fingerprint()  # a change the waits see, with nothing else different
    same = Screen("Shellby", after.items, window="~ — zsh", windows=1)
    assert not expect.verify(expect.Expect("window_opened"), before, same).ok  # its list changed, no window opened


# ---------------------------------------------------------------- one question up front about the key actions


def test_key_actions_are_read_off_the_plan():
    assert risk.key_action({"tool": "submit"}, "message") == "send"
    assert risk.key_action({"tool": "submit"}, "search box") is None
    assert risk.key_action({"tool": "submit"}, "text field") is None  # unnamed: the gate asks when it sends
    assert risk.key_action({"tool": "click", "target": "Add to cart"}) == "add to cart"
    assert risk.key_action({"tool": "click", "target": "Star"}) == "star"
    assert risk.key_action({"tool": "click", "target": "Follow"}) == "follow"
    assert risk.key_action({"tool": "click", "target": "Move to Trash"}) == "delete"
    assert risk.key_action({"tool": "click", "target": "Place your order"}) == "purchase"
    assert risk.key_action({"tool": "type", "text": "make test\n"}) == "run"
    assert risk.key_action({"tool": "type", "text": "dear Sam,\nthanks\n"}) is None
    assert risk.key_action({"tool": "click", "target": "Compose"}) is None
    steps = [{"tool": "open_place", "what": "Mailbox"}, {"tool": "write", "text": "hi there", "where": "message"},
             {"tool": "submit"}]  # fmt: skip
    assert (
        ahead_line(steps, {id(steps[2]): "send"})
        == "I'll open Mailbox, write 'hi there' in the message field, and send it. Go ahead?"
    )


def mailbox() -> FakeEnv:
    compose = page("Mailbox", Item("Message", "field"), Item("Send", BTN), Item("Delete draft", BTN), Item("Older mail", "row"))
    sent = page(
        "Mailbox",
        Item("Message", "field"),
        Item("Send", BTN),
        Item("Delete draft", BTN),
        Item("Older mail", "row"),
        Item("To Sam: hi there", "row"),
    )
    return FakeEnv({"compose": compose, "sent": sent}, "compose", {("compose", "click Send"): "sent"})


SEND_PLAN = "write hi there | where: Message\nclick Send | expect: new_item\ndone: new_item"


def test_the_user_is_asked_once_before_acting_and_the_yes_covers_those_steps():
    asked: list[str] = []
    env = mailbox()
    result = run_task(
        "tell Sam hi there", env, FakeLaya(), FakeLineBrain(SEND_PLAN), FACTS, confirm=lambda q: asked.append(q) or True
    )
    assert result.done and asked == ["I'll write 'hi there' in the Message field, and click 'Send' to send it. Go ahead?"]
    assert env.log == ["set Message=hi there", "click Send"]


def test_no_up_front_means_nothing_is_done():
    env = mailbox()
    result = run_task("tell Sam hi there", env, FakeLaya(), FakeLineBrain(SEND_PLAN), FACTS, confirm=lambda q: False)
    assert not result.done and env.log == [] and "the user said no" in result.reason
    assert result.said == "I couldn't do it: you said no."


def test_a_listed_step_is_not_asked_again_and_a_risky_one_not_listed_still_asks():
    asked: list[str] = []
    env = mailbox()
    env.on[("compose", "click Delete draft")] = "compose"
    plan = SEND_PLAN.replace("click Send", "click Delete draft | expect: none\nclick Send")
    run_task("tell Sam hi there", env, FakeLaya(), FakeLineBrain(plan), FACTS, confirm=lambda q: asked.append(q) or True)
    assert len(asked) == 1 and "click 'Delete draft' to delete it" in asked[0]  # listed: approved, never asked again
    note = page("Notely", Item("Shopping", "row"), Item("Delete note", BTN), Item("Archive", BTN))
    alert = page(
        "Notely", Item("Shopping", "row"), Item("Delete", BTN, dialog=True), Item("Cancel", BTN, dialog=True, cancels=True)
    )
    env = FakeEnv({"note": note, "alert": alert}, "note", {("note", "click Archive"): "alert", ("alert", "click Cancel"): "note"})
    plan = "click Archive | expect: dialog_opened\nanswer yes | expect: item_gone\ndone: item_gone(Shopping)"
    asked.clear()
    run_task("archive the shopping note", env, FakeLaya(), FakeLineBrain(plan), FACTS, confirm=lambda q: asked.append(q) or False)
    assert len(asked) == 1 and asked[0].startswith("delete")  # nothing key in the plan: no question up front; the
    # dialog's Delete, never in the plan, asks when it comes


# ---------------------------------------------------------------- at most two looks at one step


def test_one_step_gets_at_most_two_looks_then_fails_with_its_reason():
    env = quillery(add="Compose")
    plan = (
        "click Add Note | expect: editor_opened\nwrite buy oat milk | where: Note text\n"
        "click Keep | expect: new_item(buy oat milk)\ndone: new_item(buy oat milk)"
    )
    looks = [f"click {name} | expect: editor_opened\ncontinue" for name in ("Pencil", "Quill", "Feather", "Nib")]
    hb, brain = auto(plan, *looks)
    result = loop.run_task("add a note buy oat milk in quillery", env, literal(), hb, FACTS)
    assert not result.done and brain.calls == 1 + loop.MAX_STEP_LOOKS, result.trace
    assert result.gave_up == loop.GAVE_UP_LOOKS and f"after {loop.MAX_STEP_LOOKS} looks" in result.reason
