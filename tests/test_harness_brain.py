"""Harness v2 brain: the plan as tool lines, streamed, the JSON fallback, the one repair, the end-state check,
and the small slice of the Mac the brain gets. Fake brains only: no model runs here."""

from __future__ import annotations

from dataclasses import replace

import pytest
from test_harness_tools import FACTS, FakeBrain, FakeLineBrain, page

from karta.harness.brain import END_SYS, PLAN_SYS, PLAN_SYS_JSON, HarnessBrain, brain_facts, parse_line, step_line
from karta.harness.facts import Tab
from karta.harness.types import Item, Outcome, ToolResult

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)


def step(line: str) -> dict:
    kind, value = parse_line(line)
    assert kind == "step", value
    return value


def test_each_tool_line_parses_to_its_tool_call():
    assert step("open_place Jira board | ready: Backlog | site: acme.atlassian.net") == {
        "tool": "open_place",
        "what": "Jira board",
        "ready_shows": "Backlog",
        "site": "acme.atlassian.net",
    }
    assert step("find Rohan | meaning: the direct message with Rohan | alt: Rohn, Roh") == {
        "tool": "find",
        "text": "Rohan",
        "meaning": "the direct message with Rohan",
        "alternatives": ["Rohn", "Roh"],
    }
    assert step("click New event") == {"tool": "click", "target": "New event"}
    assert step("click Play | near: Midnight City") == {"tool": "click", "target": "Play", "near": "Midnight City"}
    assert step("click: the Archive folder") == {"tool": "click", "target": "the Archive folder"}
    assert step("write Agenda: budget | where: event title") == {
        "tool": "write",
        "text": "Agenda: budget",
        "where": "event title",
    }
    assert step("type ls -la | grep src\\n") == {"tool": "type", "text": "ls -la | grep src\n"}  # a pipe is typed text
    assert step("fill Start date: 14 October 2026 | All day: off") == {
        "tool": "fill",
        "fields": {"Start date": "14 October 2026", "All day": False},
    }
    assert step("answer Allow once") == {"tool": "answer", "choice_meaning": "Allow once"}
    assert step("submit | expect: standup moved to 11") == {"tool": "submit", "expect": "standup moved to 11"}
    assert step("submit standup moved") == {"tool": "submit", "expect": "standup moved"}
    assert parse_line("press cmd+return")[0] == "error" and parse_line("press return")[0] == "error"  # UI actions only
    assert step("- scroll up") == {"tool": "scroll", "direction": "up"}
    assert step("3. wait 4") == {"tool": "wait", "seconds": 4.0}
    assert step("back") == {"tool": "back"}


def test_meta_lines_errors_and_blanks():
    assert parse_line("done: 3 items $45.00") == ("meta", ("done_shows", "3 items $45.00"))
    assert parse_line("ask: Which city should it deliver to?") == ("meta", ("ask", "Which city should it deliver to?"))
    assert parse_line("stop: nothing to reply to")[0] == "meta" and parse_line("learned: dates go in the info panel")[0] == "meta"
    assert parse_line("```")[0] == "skip" and parse_line("   ")[0] == "skip"
    assert parse_line("teleport home")[0] == "error"
    assert parse_line("write only text")[0] == "error"  # write needs where
    assert parse_line("fill no colon here")[0] == "error"


def test_a_tool_call_written_back_as_a_line_parses_to_itself():
    for s in [
        {"tool": "open_place", "what": "Calendar", "ready_shows": "Today"},
        {"tool": "find", "text": "Roh", "meaning": "the chat with Rohan", "alternatives": ["Rohan", "Ro"]},
        {"tool": "fill", "fields": {"Date": "Friday", "Alert": True}},
        {"tool": "type", "text": "make test\n"},
        {"tool": "submit", "expect": "Saved"},
        {"tool": "scroll", "direction": "down"},
    ]:
        assert step(step_line(s)) == s


def test_the_plan_streams_and_the_first_step_arrives_before_the_rest_is_written():
    brain = FakeLineBrain(
        "open_place Calendar | ready: Today\nclick New event\nwrite Dentist | where: event title\ndone: Dentist", delay=0.1
    )
    hb = HarnessBrain(brain)
    stream = hb.stream_plan("add a dentist event", FACTS, page("Finder"))
    it = iter(stream)
    first = next(it)
    assert first == {"tool": "open_place", "what": "Calendar", "ready_shows": "Today"}
    assert len(brain.seen) == 1  # only the first line was read when the first step came out
    rest = list(it)
    assert [s["tool"] for s in rest] == ["click", "write"] and stream.plan.done_shows == "Dentist" and stream.finished
    assert hb.calls == 1 and brain.asked[0][0] == PLAN_SYS and hb.stats[0]["format"] == "lines"


def test_a_json_reply_in_line_mode_is_still_read():
    brain = FakeLineBrain('```json\n{"steps": [{"tool": "click", "target": "New event"}],\n "done_shows": "Dentist"}\n```')
    plan = HarnessBrain(brain).plan("add an event", FACTS, page("Calendar"))
    assert plan.steps == [{"tool": "click", "target": "New event"}] and plan.done_shows == "Dentist" and not plan.errors


def test_a_json_only_brain_gets_the_json_prompt():
    brain = FakeBrain({"steps": [{"tool": "click", "target": "New event"}], "done_shows": "Dentist"})
    plan = HarnessBrain(brain).plan("add an event", FACTS, page("Calendar"))
    assert plan.steps == [{"tool": "click", "target": "New event"}] and brain.asked[0][0] == PLAN_SYS_JSON


def test_a_streamed_plan_with_no_valid_line_gets_one_repair_ask():
    brain = FakeLineBrain("teleport to Calendar\nopen_app Calendar", "open_place Calendar\ndone: Today")
    hb = HarnessBrain(brain)
    stream = hb.stream_plan("open my calendar", FACTS, page("Finder"))
    assert list(stream) == [{"tool": "open_place", "what": "Calendar"}]
    assert hb.calls == 2 and "rejected" in brain.asked[1][1] and stream.plan.done_shows == "Today"


def test_rescue_uses_the_same_lines_and_gets_the_failures_so_far():
    brain = FakeLineBrain("stop: the calendar is read-only here")
    hb = HarnessBrain(brain)
    done = [({"tool": "open_place", "what": "Calendar"}, ToolResult(Outcome.OK, "Calendar app"))]
    plan = hb.rescue(
        "add an event",
        FACTS,
        page("Calendar"),
        [],
        done,
        {"tool": "click", "target": "New event"},
        ToolResult(Outcome.DISABLED, "'New event' is disabled"),
        {"Calendar": "Calendar app"},
        [[{"tool": "click", "target": "New event"}]],
        ["Calendar: element 'New event' failed: disabled"],
    )
    user = brain.asked[0][1]
    assert plan.stop == "the calendar is read-only here" and not plan.steps
    assert "- open_place Calendar  -> Calendar app" in user and "Failed: click New event" in user
    assert "Failures so far" in user and "'New event' failed" in user


def test_the_end_check_reads_yes_or_no_with_why():
    screen = page("Notes", Item("groceries", "row"), Item("milk, eggs, bread", "text"))
    hb = HarnessBrain(FakeLineBrain("yes: the note groceries lists milk, eggs and bread"))
    assert hb.end_check("make a groceries note", "groceries", screen) == (True, "the note groceries lists milk, eggs and bread")
    assert hb.brain.asked[0][0] == END_SYS and hb.calls == 1
    assert HarnessBrain(FakeLineBrain("No - the note is still empty")).end_check("t", "x", screen)[0] is False
    assert HarnessBrain(FakeLineBrain("hmm")).end_check("t", "x", screen)[0] is None
    assert HarnessBrain(FakeBrain({"done": True, "why": "listed"})).end_check("t", "x", screen) == (True, "listed")


def test_the_brain_gets_a_small_slice_of_the_mac_not_the_whole_map():
    facts = replace(
        FACTS,
        installed_apps=[*FACTS.installed_apps, "Spreadsheet Pro", "Photo Lab"],
        folders=["~/Projects/atlas", "~/Code/zephyr"],
    )
    text = brain_facts("open terminal in my atlas project and run make", facts)
    assert "Now: Tuesday 06 October 2026, 09:00" in text and "Terminal: installed app" in text
    assert "~/Projects/atlas" in text and "zephyr" not in text and "Photo Lab" not in text and "Installed apps" not in text
    tabs = [Tab("Brave Browser", 2, 3, "https://code.example/acme/api/pulls", "Pulls", "Work")]
    text = brain_facts("approve my open pull request on code.example", facts, tabs)
    assert "code.example: open now in Brave Browser Work; can open in Brave Browser Work, Brave Browser Personal" in text
    assert len(text.splitlines()) <= 7


def test_a_folder_is_given_only_when_the_task_names_it_as_a_place():
    from karta.harness.brain import names_folder

    assert names_folder("open terminal in my karta project and run make", "~/Desktop/Projects/karta")
    assert names_folder("go to the karta folder", "~/Desktop/Projects/karta")
    assert names_folder("cd ~/Desktop/Projects/karta and run ls", "~/Desktop/Projects/karta")
    assert names_folder("open the report in atlas", "~/Projects/atlas")
    assert not names_folder("create a note called Karta test", "~/Desktop/Projects/karta")
    assert not names_folder("message Karta about lunch", "~/Desktop/Projects/karta")
    text = brain_facts("create a note called atlas notes", replace(FACTS))
    assert "Folders" not in text


def test_a_step_line_carries_its_checkpoint():
    from karta.harness.brain import parse_line, step_line

    kind, step = parse_line("submit | expect: Pull requests | then: github.com/pulls")
    assert kind == "step" and step == {"tool": "submit", "expect": "Pull requests", "then": "github.com/pulls"}
    kind, step = parse_line("fill Title: Dentist | Start time: 10:00 | then: Dentist")
    assert step == {"tool": "fill", "fields": {"Title": "Dentist", "Start time": "10:00"}, "then": "Dentist"}
    assert step_line(step).endswith("| then: Dentist")


def test_a_step_may_not_type_an_email_address_karta_never_gave_the_brain():
    from karta.harness.brain import foreign_detail

    step = {"tool": "write", "text": "owner@account.example", "where": "To"}
    assert foreign_detail(step, "Task: email me the notes") is not None
    assert foreign_detail(step, "Task: email owner@account.example the notes") is None
    assert foreign_detail(step, "You: email owner@account.example (from the user profile)") is None
    assert foreign_detail({"tool": "fill", "fields": {"To": "x@y.example"}, "then": "a@b.example"}, "Task: x") is not None
