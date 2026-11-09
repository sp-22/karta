"""Proof is state, not words: words that were on screen before a step never prove the step happened.

Each test reproduces a failure seen live, on made-up apps and sites: a skip-ahead past a click for a static label, a
checkpoint passed by an old line, an end state met by a line left from an earlier message, a recipient field that
turns the address into a token, a collect that took the page's own search link."""

from __future__ import annotations

import pytest
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, FakeLineBrain, page, toolbox

from karta.harness import facts
from karta.harness.brain import HarnessBrain
from karta.harness.loop import end_state, run_task
from karta.harness.tools import beside, collect_candidates, same_page
from karta.harness.types import Item, Outcome, Screen, ToolResult

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)

SITE = "https://post.example.org/"


def yes(_q: str) -> bool:
    return True


# ---------------------------------------------------------------- the diff the checks read


def test_new_items_are_what_changed():
    before = Screen("Ledgerly", [Item("New draft", "text"), Item("Compose", BTN)], window="Inbox")
    same = Screen("Ledgerly", [Item("New draft", "text"), Item("Compose", BTN)], window="Inbox")
    assert facts.new_items(same, before) == [] and facts.since(same, before).window == ""
    panel = Screen("Ledgerly", [*same.items, Item("New draft", "heading", dialog=True), Item("To", "field", dialog=True)])
    assert [i.kind for i in facts.new_items(panel, before)] == ["heading", "field"]


def test_shows_new_never_counts_words_that_were_there_and_takes_a_new_line_under_an_old_header():
    old = Screen("Chatter", [Item("Rin Okafor", "heading"), Item("Message delivered", "text")])
    again = Screen("Chatter", [Item("Rin Okafor", "heading"), Item("Message delivered", "text"), Item("Send", BTN)])
    assert facts.shows(again, "Message delivered") and not facts.shows_new(again, old, "Message delivered")
    said = Screen("Chatter", [*again.items, Item("lunch at noon", "text")])
    assert facts.shows_new(said, old, "Rin Okafor: lunch at noon")  # the header was there, the message is new
    assert not facts.shows_new(said, said, "Rin Okafor: lunch at noon")


# ---------------------------------------------------------------- skip-ahead: only a kind with words, met by state


def mail_env(opens_dialog: bool):
    """A mail site whose inbox carries the words "New draft" as a static label; Compose opens the draft panel."""
    inbox = page(
        "Brave Browser",
        Item("Inbox", "heading"),
        Item("New draft", "text"),  # a static label of the inbox, there before anything is clicked
        Item("Compose", BTN),
        url=SITE + "inbox",
        profile="Personal",
    )
    draft = page(
        "Brave Browser",
        *inbox.items,
        Item("New draft", "heading", dialog=opens_dialog),
        Item("To", "field", dialog=opens_dialog),
        url=SITE + "inbox",
        profile="Personal",
    )
    return FakeEnv(
        {"finder": page("Finder", Item("Desktop", "row")), "inbox": inbox, "draft": draft},
        "finder",
        {
            ("finder", f"url {SITE} Brave Browser Work"): "inbox",
            ("finder", f"url {SITE} Brave Browser Personal"): "inbox",
            ("inbox", "click Compose"): "draft",
        },
    )


def test_a_static_label_on_a_page_just_opened_never_skips_the_click_that_opens_it():
    env = mail_env(opens_dialog=True)
    plan = (
        "open_place Postbox mail | site: post.example.org\nclick Compose | expect: editor_opened\n"
        "write ana@example.net | where: To"
    )
    brain = HarnessBrain(FakeLineBrain(plan), prompt="v2")
    result = run_task("start a draft to ana@example.net on postbox", env, FakeLaya(), brain, FACTS, confirm=yes)
    assert "click Compose" in env.log, result.ledger
    assert not any("met already" in line for line in result.ledger), result.ledger
    assert any(line.startswith("✓ click Compose") for line in result.ledger), result.ledger


def test_a_kind_with_no_words_never_skips_though_an_earlier_step_caused_it():
    env = mail_env(opens_dialog=True)
    env.at = "inbox"
    env.pages["inbox"].items.append(Item("Start", BTN))
    env.on[("inbox", "click Start")] = "draft"  # this click brings up the draft panel itself
    plan = "click Start | expect: dialog_opened\nclick Compose | expect: dialog_opened\nwrite ana@example.net | where: To"
    brain = HarnessBrain(FakeLineBrain(plan, "stop: done"), prompt="v2")
    result = run_task("start a draft on postbox", env, FakeLaya(), brain, FACTS, confirm=yes)
    assert not any("met already" in line for line in result.ledger), result.ledger
    assert any(line[2:].startswith("click Compose") for line in result.ledger), result.ledger  # it ran, never skipped


# ---------------------------------------------------------------- the end state: never an old line


def test_an_old_sent_line_on_the_page_is_not_the_end_state():
    sent_before = page(
        "Brave Browser", Item("Message delivered", "text"), Item("Body", "field"), url=SITE + "inbox", profile="Personal"
    )
    env = FakeEnv({"a": sent_before}, "a")
    tb = toolbox(env)
    start = page("Terminal", Item("~ %", "text"))
    done = [({"tool": "write", "text": "hello", "where": "Body"}, ToolResult(Outcome.OK))]
    env.set_value("Body", "hello")
    tb.pending["hello"] = "Body"
    # the line was there before the write: the old rule (on screen now, not at the start) took it as done
    assert not end_state(tb, "Message delivered", done, start, baseline=env.screen()).ok
    after = page("Brave Browser", *env.screen().items, Item("Message delivered", "text"), url=SITE, profile="Personal")
    env.pages["a"] = after  # a second line: a new one, this message's
    assert end_state(tb, "Message delivered", done, start, baseline=sent_before).ok


def test_a_plan_whose_end_words_were_left_from_an_earlier_message_is_not_done():
    inbox = page(
        "Brave Browser",
        Item("Message delivered", "text"),  # left on the page from an earlier message
        Item("Body", "field"),
        url=SITE + "inbox",
        profile="Personal",
    )
    env = FakeEnv(
        {"finder": page("Finder", Item("Desktop", "row")), "inbox": inbox},
        "finder",
        {("finder", f"url {SITE} Brave Browser Work"): "inbox", ("finder", f"url {SITE} Brave Browser Personal"): "inbox"},
    )
    plan = {
        "steps": [
            {"tool": "open_place", "what": "Postbox mail", "ready_shows": "Body", "site": "post.example.org"},
            {"tool": "write", "text": "see you at nine", "where": "Body"},
        ],
        "done_shows": "Message delivered",
    }
    result = run_task("tell the team see you at nine on postbox", env, FakeLaya(), FakeBrain(plan), FACTS, confirm=yes)
    assert not result.done, result.reason


def test_a_send_is_not_proven_by_a_sent_line_that_was_there_before_it():
    form = page("Chatter", Item("Message delivered", "text"), Item("Reply", "field"), Item("Send", BTN))
    cleared = page("Chatter", Item("Message delivered", "text"), Item("Reply", "field"), Item("Send", BTN))
    env = FakeEnv({"form": form, "cleared": cleared}, "form", {("form", "click Send"): "cleared"})
    tb = toolbox(env)
    assert tb.tool_write("on my way", "Reply").ok
    result = tb.tool_submit("Message delivered")
    assert not result.ok, result  # the box emptied, but nothing new says it went


# ---------------------------------------------------------------- a recipient field that makes a token


def token_env(makes_token: bool):
    form = page(
        "Brave Browser",
        Item("Recipients", "field", region="top"),
        Item("Subject", "field", region="top"),
        Item("Body", "field", region="middle"),
        url=SITE + "draft",
    )
    env = FakeEnv({"form": form}, "form")

    def set_text(item, text):
        env.log.append(f"set {item.name}={text}")
        if item.name == "Recipients":  # the field empties itself
            if makes_token:  # and the address becomes a token beside it
                env.pages["form"].items.insert(1, Item(text, BTN, region="top"))
            return
        env.set_value(item.name, text)

    env.set_text = set_text
    return env


def test_an_address_turned_into_a_token_beside_its_emptied_field_is_written_once():
    env = token_env(makes_token=True)
    tb = toolbox(env)
    result = tb.tool_write("ana@example.net", "Recipients")
    assert result.ok, result
    assert [a for a in env.log if a.startswith("set ")] == ["set Recipients=ana@example.net"]


def test_an_emptied_field_with_no_token_beside_it_is_not_written():
    env = token_env(makes_token=False)
    result = toolbox(env).tool_write("ana@example.net", "Recipients")
    assert not result.ok


def test_a_token_is_beside_its_field_by_row_frame_or_region_only():
    box = Item("Recipients", "field", region="top")
    assert beside(Item("ana@example.net", BTN, region="top"), box)
    assert not beside(Item("ana@example.net", BTN, region="bottom"), box)
    assert not beside(Item("ana@example.net", BTN), Item("Recipients", "field"))


# ---------------------------------------------------------------- collect: never the page's own search again


def test_collect_skips_a_link_back_to_the_same_search_with_other_parameters():
    here = "https://clips.example.com/results?q=ferns"
    scr = Screen(
        "Brave Browser",
        [
            Item("Ferns of the cloud forest, a field guide", "link", url="https://clips.example.com/play?v=a1"),
            Item("ferns indoor care tips for beginners", "link", url="https://clips.example.com/results?q=ferns&sort=new"),
            Item("Growing ferns from spores at home", "link", url="https://clips.example.com/play?v=b2"),
            Item("ferns again on this very page", "link", url="/results?q=ferns"),
        ],
        url=here,
    )
    names = [i.name for i in collect_candidates(scr, "links to fern clips")]
    assert names == ["Ferns of the cloud forest, a field guide", "Growing ferns from spores at home"]
    assert same_page("https://www.clips.example.com/results/?q=moss", here)
    assert not same_page("https://clips.example.com/play?v=a1", here) and not same_page("https://other.example.com/results", here)
