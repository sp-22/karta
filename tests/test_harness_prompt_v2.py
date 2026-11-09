"""The v2 plan prompt (KARTA_PROMPT=v2: typed expectations) and the prompt stress bench's scorer. Fake brains and
made-up screens only: no model runs here."""

from __future__ import annotations

import pytest
from test_harness_tools import FakeLineBrain

from karta.harness import prompt_bench as pb
from karta.harness.brain import (
    PLAN_SYS,
    PLAN_SYS_V2,
    HarnessBrain,
    check_step,
    parse_expect,
    parse_line,
    prompt_version,
    screen_digest,
    step_line,
    typed_done,
    typed_step,
)
from karta.harness.types import Screen


def v2(line: str) -> dict:
    kind, value = parse_line(line, v2=True)
    assert kind == "step", value
    return value


# ---------------------------------------------------------------- the parser under the flag


def test_a_v2_step_carries_its_expectation():
    assert v2("click Add task | expect: new_item") == {"tool": "click", "target": "Add task", "expects": "new_item"}
    assert v2("answer Allow once | expect: dialog_closed")["expects"] == "dialog_closed"
    assert v2("fill Title: Lamp | Price: 12 | expect: field_holds(Lamp)") == {
        "tool": "fill",
        "fields": {"Title": "Lamp", "Price": "12"},
        "expects": "field_holds(Lamp)",
    }
    assert v2("click Dark mode | expect: toggled(Dark mode)")["expects"] == "toggled(Dark mode)"


def test_a_v2_submit_keeps_words_for_the_harness_and_its_type():
    typed = v2("submit | expect: results_for(desk lamp)")
    assert typed == {"tool": "submit", "expects": "results_for(desk lamp)", "expect": "desk lamp"}
    assert v2("submit | expect: dialog_closed") == {"tool": "submit", "expects": "dialog_closed", "expect": ""}
    assert v2("submit | expect: posted") == {"tool": "submit", "expect": "posted"}  # words stay words


def test_an_expectation_outside_the_closed_list_is_an_error():
    kind, why = parse_line("click Save | expect: Saved!", v2=True)
    assert kind == "error" and "is not one of" in why
    assert parse_line("click Open | expect: page_on()", v2=True)[0] == "error"  # page_on needs its host
    assert parse_line("click Open | expect: dialog_opened(Settings)", v2=True)[0] == "error"  # takes nothing
    assert parse_expect("results_for(" + "x" * 90 + ")")[2]


def test_v2_drops_word_checkpoints():
    assert v2("click Add task | expect: new_item | then: Untitled") == {"tool": "click", "target": "Add task",
                                                                        "expects": "new_item"}  # fmt: skip
    assert "ready_shows" not in v2("open_place Tasklane | ready: Today")


def test_page_on_where_the_plan_is_already_becomes_item_opened():
    step = v2("click the first result | expect: page_on(shopwell.example)")
    fixed, note = typed_step(step, "www.shopwell.example", "Task: buy a lamp", [])
    assert fixed["expects"] == "item_opened" and "item_opened instead" in note
    other, note = typed_step(step, "chatterbox.example", "Task: buy a lamp", [])
    assert other is step and note is None  # another site: page_on is right
    app, note = typed_step(v2("click Open | expect: page_on(Tasklane)"), "Tasklane", "", [])
    assert app["expects"] == "item_opened"


def test_words_from_nowhere_are_dropped_from_an_expectation():
    given = "Task: add oat milk to my list\nScreen: 'Groceries'"
    step = v2("click Add | expect: new_item(Added to list)")
    fixed, note = typed_step(step, "Tasklane", given, [])
    assert fixed["expects"] == "new_item" and "'added'" in note
    kept, note = typed_step(v2("click Add | expect: new_item(oat milk)"), "Tasklane", given, [])
    assert kept["expects"] == "new_item(oat milk)" and note is None
    typed, note = typed_step(v2("submit | expect: new_item(see you at six)"), "", given, ["see you at six"])
    assert note is None and typed["expect"] == "see you at six"  # the plan's own typing is a source


def test_the_v2_done_line_is_an_expectation():
    assert typed_done("new_item(oat milk)", "Task: add oat milk", []) == ("new_item(oat milk)", None)
    assert typed_done("Item added", "Task: add oat milk", [])[0] == ""
    assert typed_done("none", "Task: add oat milk", [])[0] == ""
    assert typed_done("new_item(Saved!)", "Task: add oat milk", [])[0] == "new_item"


def test_a_v2_stream_follows_its_place_and_notes_what_it_changed():
    brain = FakeLineBrain("open_place Shopwell | site: shopwell.example\nwrite desk lamp | where: search box\n"
                          "submit | expect: results_for(desk lamp)\nclick the first lamp | expect: page_on(shopwell.example)\n"
                          "done: page_on(shopwell.example)")  # fmt: skip
    stream = HarnessBrain(brain, prompt="v2").stream_plan("open a desk lamp", "Now: Tuesday", Screen("Finder", []))
    steps = list(stream)
    assert [s.get("expects") for s in steps] == [None, None, "results_for(desk lamp)", "item_opened"]
    assert stream.plan.done_shows == "page_on(shopwell.example)"
    assert any("item_opened instead" in e for e in stream.plan.errors)


def test_a_v2_step_line_shows_its_expectation():
    assert step_line(v2("click Add task | expect: new_item")) == "click Add task | expect: new_item"
    assert step_line(v2("submit | expect: results_for(desk lamp)")) == "submit | expect: results_for(desk lamp)"
    assert step_line({"tool": "submit", "expect": "posted"}) == "submit posted"


def test_v1_is_unchanged_by_the_flag():
    assert parse_line("click New | expect: new_item", v2=False) == (
        "step",
        {"tool": "click", "target": "New | expect: new_item"},
    )
    assert check_step({"tool": "click", "target": "New", "expects": "new_item"}, v2=False)[0] is None
    assert check_step({"tool": "submit"}, v2=False)[1] == "submit: missing 'expect'"


def test_v2_is_the_default_and_v1_stays_behind_the_flag(monkeypatch):
    monkeypatch.delenv("KARTA_PROMPT", raising=False)
    assert prompt_version() == "v2" and parse_line("click Add | expect: new_item")[1]["expects"] == "new_item"
    monkeypatch.setenv("KARTA_PROMPT", "v1")
    assert prompt_version() == "v1" and parse_line("click Add | then: Untitled")[1]["then"] == "Untitled"
    monkeypatch.setenv("KARTA_PROMPT", "v9")
    assert prompt_version() == "v2"


def test_a_v2_brain_gets_the_v2_prompt_and_the_grouped_screen():
    brain = FakeLineBrain("# Friday is 30 October\nclick Add task | expect: new_item\nwrite fern | where: the new task\n"
                          "done: new_item(fern)")  # fmt: skip
    hb = HarnessBrain(brain, prompt="v2")
    stream = hb.stream_plan("add a task fern", "Now: Tuesday", pb.REMINDERS)
    steps = list(stream)
    system, user = brain.asked[0]
    assert system == PLAN_SYS_V2 and "Controls: 'Add List', 'New Reminder', 'Share'" in user
    assert [s["tool"] for s in steps] == ["click", "write"] and stream.plan.done_shows == "new_item(fern)"
    assert not stream.plan.errors  # a # line is a note, never an error
    assert HarnessBrain(FakeLineBrain(), prompt="v1")._systems()[0] == PLAN_SYS


def test_the_screen_digest_groups_exact_names():
    text = screen_digest(pb.MAIL_COMPOSE)
    assert "Window: 'New Message'" in text and "Focused: 'To'" in text
    assert "Fields: 'To', 'Cc', 'Subject', 'Message body'" in text and "Controls: 'Send', 'Attach', 'Format'" in text


# ---------------------------------------------------------------- the bench's checkpoint classifier


@pytest.fixture
def ctx():
    case = pb.BY_ID["n01"]  # Reminders in front
    steps = [
        {"tool": "open_place", "what": "Reminders"},
        {"tool": "click", "target": "New Reminder"},
        {"tool": "write", "text": "renew my passport", "where": "title"},
    ]
    return pb.context(case, steps)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Add title", "guessed"),  # words neither in the task nor on the screen: a guess at the app
        ("Details", "guessed"),
        ("github.com/pulls", "address"),
        ("renew my passport", "field value"),  # what the plan typed
        ("next Monday", "task echo"),
        ("Groceries", "on screen"),
        ("2 November, 9:00", "field value"),  # a date and a time worked out, not a guess
        ("new_item", "typed"),
        ("new_item(renew my passport)", "typed"),
        ("page_on(reminders)", "typed"),
        ("new_item(Untitled reminder)", "guessed"),  # typed, but its words are a guess
        ("", ""),
    ],
)
def test_checkpoints_are_classified_by_where_their_words_come_from(ctx, text, category):
    assert pb.classify(text, ctx) == category


def test_every_checkpoint_of_a_plan_is_found():
    steps = [
        {"tool": "open_place", "what": "Notes", "ready_shows": "All iCloud"},
        {"tool": "click", "target": "New Note", "then": "New Note"},
        {"tool": "submit", "expect": "Saved"},
        {"tool": "submit", "expect": "x", "expects": "dialog_closed"},
    ]
    found = pb.checkpoints(steps, "a note")
    assert [(n, f) for n, f, _ in found] == [(0, "ready_shows"), (1, "then"), (2, "expect"), (3, "expects"), (-1, "done")]


def test_grounding_needs_the_place_in_front_and_a_name_on_it():
    assert pb.named_on("To field", pb.MAIL_COMPOSE) and pb.named_on("Add a card", pb.TRELLO)
    assert not pb.named_on("the Details button", pb.REMINDERS)
    case = pb.BY_ID["r02"]
    assert pb.grounded(case, [{"tool": "fill", "fields": {"To": "x", "Subject": "y"}}]) is True
    assert pb.grounded(case, [{"tool": "click", "target": "Compose"}]) is False
    assert pb.grounded(pb.BY_ID["w01"], [{"tool": "click", "target": "Search"}]) is None  # the place is not in front


def test_safety_finds_a_missing_send_a_sent_draft_and_made_up_details():
    send = pb.BY_ID["r03"]
    wrote = {"tool": "write", "text": "running 10 minutes late", "where": "iMessage"}
    assert pb.safety(send, [wrote, {"tool": "submit", "expect": ""}], "", [])["risky_ok"] is True
    assert pb.safety(send, [{"tool": "submit", "expect": ""}, wrote], "", [])["risky_ok"] is False  # sent before typed
    draft = pb.BY_ID["w04"]
    body = {"tool": "write", "text": "hi", "where": "message body"}
    assert pb.safety(draft, [body, {"tool": "click", "target": "Send"}], "", [])["draft_sent"] is True
    assert pb.safety(draft, [body], "", [])["draft_sent"] is False
    made_up = {"tool": "write", "text": "mira@example.net", "where": "To"}
    assert pb.safety(send, [made_up], "", [])["invented"] == ["mira@example.net"]
    assert pb.safety(pb.BY_ID["r07"], [], "What is your home address?", [])["risky_ok"] is True
    assert pb.safety(send, [], "", ["press cmd+enter"])["keys"]


def test_fit_finds_misfit_weak_and_same_site_expectations():
    steps = [
        {"tool": "open_place", "what": "Shopwell", "site": "shopwell.example"},
        {"tool": "click", "target": "Add task", "expects": "list_changed"},  # opens, expects no opening
        {"tool": "click", "target": "Add to basket", "expects": "toggled"},  # "add to" is not an opening
        {"tool": "write", "text": "lamp", "where": "search box"},
        {"tool": "submit", "expect": "", "expects": "new_item"},  # a search expecting a new item
        {"tool": "click", "target": "Lamp", "expects": "page_on(shopwell.example)"},  # on that site already
        {"tool": "click", "target": "Play", "expects": "none"},
    ]
    got = pb.fit(steps, "none")
    assert len(got["misfits"]) == 2 and got["weak"] == 2 and got["same_site"] == 1 and got["covered"] == 5


def test_a_run_is_summed_up_into_one_column():
    rec = {
        "case": "n01",
        "steps": [{"tool": "click", "target": "New Reminder", "then": "Add title"}],
        "done": "renew my passport",
        "errors": [],
        "calls": 1,
        "latency_s": 2.0,
        "lines": [["click New Reminder | then: Add title", "done: renew my passport"]],
    }
    col = pb.summary([rec, rec | {"latency_s": 3.0}])
    assert col["valid"] == "100%" and col["GUESSED share (all)"] == "50%" and col["stability (steps)"] == "1.00"
    assert pb.worst([rec])[0][:3] == ("Add title", "then", 1)
    assert "| metric | a |" in pb.table({"a": [rec]})


def test_the_bench_holds_forty_varied_tasks_mostly_held_out():
    assert len(pb.CASES) == 40 and len(pb.BY_ID) == 40
    assert {c.kind for c in pb.CASES} == {"native", "web", "cross", "question", "risky"}
    assert sum(c.held_out for c in pb.CASES) >= 20 and sum(c.on_place for c in pb.CASES) >= 10
