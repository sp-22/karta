"""karta-mcp on the fake Mac of test_harness_tools: screen formatting, what an action reports, the confirm gate for
anything hard to undo, and the dry mode. Nothing here reaches the real machine."""

from __future__ import annotations

import asyncio
import json

import mcp.server.fastmcp  # noqa: F401  (imported before the machine guard: its Popen annotations need the real class)

from karta.harness.types import Item, Screen
from karta.mcp_server import Session, build, changes, host, item_line, screen_text
from tests.test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, page


def session(env, laya=None, dry=False) -> Session:
    return Session(env, laya or FakeLaya(), FACTS, dry_run=dry)


def notes_env() -> FakeEnv:
    return FakeEnv(
        {
            "a": page("Notes", Item("New Note", BTN, region="top"), Item("Search", "field", search=True)),
            "b": page("Notes", Item("Body", "field", focused=True)),
        },
        "a",
        {("a", "click New Note"): "b"},
    )


def chat_env() -> FakeEnv:
    return FakeEnv(
        {"a": page("Chat", Item("Type a message", "field"), Item("Send", BTN), Item("Ada", "row"))},
        "a",
    )


# ---------------------------------------------------------------- reading


def test_screen_lists_items_compactly_with_the_host_only_dialogs_first_and_a_cap():
    items = [Item(f"Row {n}", "row") for n in range(130)]
    items.insert(0, Item("File > New Note", "menu", menu=True))
    items.append(Item("Allow", BTN, dialog=True, region="middle"))
    scr = Screen("Brave Browser", items, url="https://www.example.com/private/path?q=secret", window="Inbox")
    text = screen_text(scr)
    lines = text.splitlines()
    assert lines[0].startswith("app: Brave Browser · window: Inbox · site: example.com · 132 items (first 120 shown)")
    assert "private" not in text and "secret" not in text
    assert lines[1] == "1. button 'Allow' @middle [in dialog]"
    assert len(lines) == 121 and "File > New Note" not in text  # menu commands come last, past the cap


def test_item_line_trims_long_values_and_shows_the_flags():
    line = item_line(Item("Message", "field", value="x" * 200, focused=True, enabled=False, region="bottom"))
    assert line.startswith("field 'Message' = 'xxx") and "…" in line
    assert line.endswith("@bottom [focused, disabled]")
    assert host("github.com/acme/repo") == "github.com" and host(None) == ""


def test_check_answers_by_code_first_then_laya():
    env = FakeEnv({"a": page("Notes", Item("Saved", "text"), Item("Title", "field", value="Groceries"))}, "a")
    laya = FakeLaya()
    s = session(env, laya)
    assert s.check("Saved") == {"shows": True, "why": "code: 'Saved' is on the page"}
    assert s.check("Groceries")["shows"] and "field 'Title'" in s.check("Groceries")["why"]
    assert laya.asked == []
    no = s.check("Deleted forever")
    assert no["shows"] is False and "Laya" in no["why"] and laya.asked == ["outcome"]


# ---------------------------------------------------------------- acting


def test_an_action_reports_its_outcome_what_was_done_and_what_changed():
    env = notes_env()
    out = session(env).click("New Note")
    assert out["outcome"] == "ok" and out["ok"] is True
    assert out["did"] == ["click 'New Note'"]
    assert "new: field 'Body' [focused]" in out["changed"] and "gone: button 'New Note' @top" in out["changed"]
    assert changes(env.screen(), env.screen()) == "nothing changed"


def test_a_risky_click_is_never_run_without_confirm_and_runs_with_it():
    env = chat_env()
    s = session(env)
    out = s.click("Send")
    assert out["outcome"] == "needs confirmation" and out["risk"] == "send" and out["action"] == "click 'Send'"
    assert env.log == []
    out = s.click("Send", confirm=True)
    assert env.log[0] == "click Send" and out["outcome"] != "needs confirmation"


def test_a_vague_target_that_turns_out_risky_still_needs_confirm():
    env = FakeEnv({"a": page("Chat", Item("Send", BTN))}, "a")
    out = session(env).click("the blue arrow")  # Laya picks "Send": the Toolbox's own gate stops it
    assert out["outcome"] == "needs confirmation" and out["risk"] == "send" and "'Send'" in out["action"]
    assert env.log == []


def test_submitting_a_typed_message_needs_confirm_but_typing_does_not():
    env = chat_env()
    s = session(env)
    typed = s.type_text("hello there", into="Type a message")
    assert typed["ok"] and env.log == ["set Type a message=hello there"]
    out = s.press_submit("hello there")
    assert out["outcome"] == "needs confirmation" and out["risk"] == "send"
    assert env.log == ["set Type a message=hello there"]


def test_dry_mode_says_what_it_would_do_and_does_nothing():
    env = notes_env()
    s = session(env, dry=True)
    out = s.click("New Note")
    assert out["outcome"] == "dry run" and "would click button 'New Note'" in out["detail"]
    assert s.open("https://example.com/x")["would"].startswith("open_place ")
    assert s.type_text("hi", into="Search")["outcome"] == "dry run"
    assert s.scroll("up")["would"] == 'scroll {"direction": "up"}'
    risky = session(chat_env(), dry=True).click("Send")
    assert risky["outcome"] == "dry run" and risky["risk"] == "send" and risky["needs"] == "confirm=true"
    assert env.log == []


# ---------------------------------------------------------------- the server


def test_the_server_offers_the_primitives_and_no_task_tool():
    server = build(session(notes_env()))
    names = {t.name for t in asyncio.run(server.list_tools())}
    assert names == {"screen", "click", "type_text", "press_submit", "press", "open", "scroll", "back", "check"}
    content = asyncio.run(server.call_tool("check", {"expect": "New Note"}))
    blocks = content[0] if isinstance(content, tuple) else content
    assert json.loads(blocks[0].text)["shows"] is True
