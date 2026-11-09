"""Harness v2 loop and ladder, on the fakes of test_harness_tools: plan once, Laya's next-best, the next place, a rescue
from what is done, the user; repeats, budget, the end-state rule, dialogs first, memory on success."""

from __future__ import annotations

import pytest
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, FakeLineBrain, ch, page, site_env

from karta.harness.decide import DOES_IT, DOES_OTHER, NONE, NOT_SHOWN
from karta.harness.loop import GAVE_UP_TRIED, run_task, with_rest
from karta.harness.memory import HarnessMemory
from karta.harness.types import Item

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)


def chat_env():
    """The site env (login in profile Work, signed in in Personal) plus a channel with a message box and Send."""
    env = site_env()
    env.pages["ready"].items += [Item("Message #general", "field"), Item("Send", BTN)]
    env.pages["sent"] = page(
        "Brave Browser",
        Item("Channels", "heading"),
        Item("build is green", "text"),
        Item("Message #general", "field"),
        Item("Send", BTN),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env.on[("ready", "click Send")] = "sent"
    return env


PLAN = {
    "steps": [
        {"tool": "open_place", "what": "Acme chat", "ready_shows": "Channels", "site": "chat.acme.com"},
        {"tool": "write", "text": "build is green", "where": "message box"},
        {"tool": "submit", "expect": "build is green"},
    ],
    "done_shows": "build is green",
}
TASK = "tell acme general chat the build is green"


def yes(_q: str) -> bool:
    return True


def test_one_brain_call_when_every_step_works_and_the_end_state_shows():
    env, brain = chat_env(), FakeBrain(PLAN)
    result = run_task(TASK, env, FakeLaya(), brain, FACTS, confirm=yes)
    assert result.done, result.trace
    assert result.brain_calls == 1 and result.rescues == 0
    assert (
        env.log[-1] == "click Send"
        and result.confirmations
        and result.confirmations[0].startswith("I'll")
        and "send it" in result.confirmations[0]
    )
    # the place by evidence and the field by name are code's; whether the open page asks to sign in (no password field
    # on it) and which control sends are Laya's: their meaning, never their words. The send button is one question:
    # a sure pick among the form's own buttons needs no pick check, since the submit's own check verifies it
    assert result.laya_decisions == 2 and result.timings["harness_s"] >= 0


def test_a_plan_that_runs_out_without_its_end_state_is_not_done():
    env = FakeEnv(
        {"a": page("Notes", Item("Folders", "heading"), Item("New Note", BTN)), "b": page("Notes", Item("Body", "text"))},
        "a",
        {("a", "click New Note"): "b"},
    )
    brain = FakeBrain(
        {"steps": [{"tool": "click", "target": "New Note"}], "done_shows": "groceries list"},
        {"steps": [], "stop": "the note has no title yet"},
    )
    result = run_task("make a groceries list note", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.brain_calls == 2 and "stopped" in result.reason
    assert "the plan ran out" in brain.asked[1][1]


def test_the_ladder_next_best_then_next_place_then_a_rescue_from_what_is_done():
    env = chat_env()
    env.pages["ready"].items = [i for i in env.pages["ready"].items if i.name != "Send"] + [Item("Post", BTN)]
    env.on[("ready", "click Post")] = "sent"
    plan = {"steps": [PLAN["steps"][0], {"tool": "click", "target": "the Publish button"}], "done_shows": "build is green"}
    rescue = {
        "steps": [
            PLAN["steps"][0],
            {"tool": "write", "text": "build is green", "where": "message box"},
            {"tool": "click", "target": "Post"},
        ],
        "done_shows": "build is green",
    }
    brain = FakeBrain(plan, rescue)
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", *[(it.name, 0.6 - 0.1 * n) for n, it in enumerate(items)], (NONE, 0.0)),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_OTHER, 0.9)) if "Publish" in i else ch("D4 pick check", (DOES_IT, 0.9)),
    )
    result = run_task(TASK, env, laya, brain, FACTS, confirm=yes)
    assert result.done, result.trace
    # the next place: Work was a login page, Personal worked; no brain call for that
    assert env.log[:2] == ["url https://chat.acme.com/ Brave Browser Work", "url https://chat.acme.com/ Brave Browser Personal"]
    # Laya's next-best: D4 was asked about more than one item before the step failed
    assert laya.asked.count("pick_ok") >= 3
    # the rescue: one call, given what is done and the honest reason, and it did not reopen the place
    assert result.brain_calls == 2 and "Done:" in brain.asked[1][1] and "Acme chat" in brain.asked[1][1]
    assert sum(a.startswith("url ") for a in env.log) == 2 and "already open" in "\n".join(result.trace)


def test_checkpoints_a_rescue_fixes_the_failed_step_and_the_plan_carries_on():
    env = chat_env()
    plan = {
        "steps": [PLAN["steps"][0], {"tool": "click", "target": "the Compose button"}, *PLAN["steps"][1:]],
        "done_shows": "build is green",
    }
    brain = FakeBrain(plan, {"steps": [], "skip": "the message box is already open"})
    laya = FakeLaya(
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_OTHER, 0.9)) if "Compose" in i else ch("D4 pick check", (DOES_IT, 0.9))
    )
    result = run_task(TASK, env, laya, brain, FACTS, confirm=yes)
    assert result.done, result.trace
    assert result.brain_calls == 2 and env.log[-1] == "click Send"
    rescue = brain.asked[1][1]
    assert "Still to do after it:" in rescue and "build is green" in rescue.split("Still to do after it:")[1]


def test_a_fix_is_followed_by_the_planned_steps_without_doubling_them():
    a, b, c, fix = ({"tool": "click", "target": t} for t in ("A", "B", "C", "Fix"))
    assert with_rest([fix], [b, c], replan=False) == [fix, b, c]
    assert with_rest([fix, b], [b, c], replan=False) == [fix, b, c]  # the fix began the rest itself
    assert with_rest([fix, b, c], [b, c], replan=False) == [fix, b, c]  # the fix gave the whole rest
    assert with_rest([fix, c], [b, c], replan=False) == [fix, c]  # the fix gave its own rest, B left out on purpose
    assert with_rest([a], [b, c], replan=True) == [a]


def test_a_rescue_that_repeats_a_tried_plan_stops():
    env = FakeEnv({"a": page("App", Item("Settings", BTN))}, "a")
    plan = {"steps": [{"tool": "click", "target": "the Export button"}], "done_shows": "Exported"}
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    brain = FakeBrain(plan, plan)
    result = run_task("export the file", env, laya, brain, FACTS)
    # the rescue's plan opens with the click that missed on this screen, and it was shown so: nothing untried is left
    assert not result.done and result.brain_calls == 2 and result.gave_up == GAVE_UP_TRIED
    assert result.reason == "gave up: I tried 'the Export button' on this page and none worked"


def test_the_same_call_on_the_same_screen_is_stuck_not_retried():
    env = FakeEnv({"a": page("App", Item("Refresh", BTN))}, "a")  # clicking changes nothing
    step = {"tool": "click", "target": "Refresh"}
    brain = FakeBrain(
        {"steps": [step], "done_shows": "Updated"},
        {"steps": [step, {"tool": "wait"}], "done_shows": "Updated"},
        {"steps": [], "stop": "nothing updates"},
    )
    result = run_task("refresh it", env, FakeLaya(), brain, FACTS)
    assert env.log.count("click Refresh") == 1
    assert "refused: click 'Refresh' (button) was tried on this screen" in "\n".join(result.trace) and not result.done


def test_the_budget_is_harness_time():
    ticks = iter(range(0, 10_000, 50))
    env = FakeEnv(
        {"a": page("App", Item("Next", BTN)), "b": page("App", Item("Next", BTN), Item("Page 2", "text"))},
        "a",
        {("a", "click Next"): "b", ("b", "click Next"): "a"},
    )
    brain = FakeBrain({"steps": [{"tool": "click", "target": "Next"}] * 3, "done_shows": "Page 9"})
    result = run_task("go to page 9", env, FakeLaya(), brain, FACTS, budget_s=120, clock=lambda: float(next(ticks)))
    assert not result.done and result.reason.startswith("out of time")


def test_role_player_time_is_not_harness_time():
    ticks = iter(range(0, 10_000, 100))
    env = FakeEnv({"a": page("App", Item("Go", BTN)), "b": page("App", Item("Arrived", "text"))}, "a", {("a", "click Go"): "b"})
    env.on[("a", "click Go")] = lambda e: (setattr(e, "at", "b"), setattr(e, "sim_seconds", e.sim_seconds + 1000))
    brain = FakeBrain({"steps": [{"tool": "click", "target": "Go"}], "done_shows": "Arrived"})
    result = run_task("go", env, FakeLaya(), brain, FACTS, budget_s=500, clock=lambda: float(next(ticks)))
    assert result.done and result.timings["env_s"] == 1000


def test_a_dialog_in_the_way_is_closed_by_its_own_cancel_before_the_step_never_answered():
    dialog = [
        Item("Save changes?", "text", dialog=True),
        Item("Don't Save", BTN, dialog=True),
        Item("Cancel", BTN, dialog=True, cancels=True),  # the sheet's AXCancelButton
        Item("Save", BTN, dialog=True),
    ]
    env = FakeEnv(
        {
            "a": page("Editor", *dialog, Item("Export", BTN)),
            "b": page("Editor", Item("Export", BTN)),
            "c": page("Editor", Item("Exported", "text")),
        },
        "a",
        {("a", "click Cancel"): "b", ("b", "click Export"): "c"},
    )
    laya = FakeLaya(dialog_answer=lambda t, o: ch("D6 dialog answer", ("Save", 0.8), ("Don't Save", 0.2)))
    brain = FakeBrain({"steps": [{"tool": "click", "target": "Export"}], "done_shows": "Exported"})
    result = run_task("export it", env, laya, brain, FACTS)
    assert result.done and env.log == ["click Cancel", "click Export"] and "dialog_answer" not in laya.asked


def test_an_unsure_laya_never_acts_and_the_ladder_climbs():
    env = FakeEnv({"a": page("App", Item("Archive", BTN), Item("Mute", BTN))}, "a")
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", ("Archive", 0.3), ("Mute", 0.3), (NONE, 0.4)),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_IT, 0.45), (DOES_OTHER, 0.55)),
    )
    brain = FakeBrain(
        {"steps": [{"tool": "click", "target": "hide this thread"}], "done_shows": "Hidden"},
        {"steps": [], "stop": "no control hides it"},
    )
    result = run_task("hide this thread", env, laya, brain, FACTS)
    assert [a for a in env.log if a != "wait"] == [] and not result.done and result.brain_calls == 2  # waited, never acted
    assert "not sure enough to act" in brain.asked[1][1]


def test_stuck_asks_the_user_and_their_reply_reaches_the_rescue():
    env = FakeEnv(
        {
            "a": page("Shop", Item("Delivery city", "field"), Item("Apply", BTN)),
            "b": page("Shop", Item("Delivering to Pune", "text")),
        },
        "a",
        {("a", "click Apply"): "b"},
    )
    brain = FakeBrain(
        {"steps": [], "done_shows": "Delivering to", "ask": "Which city should it deliver to?"},
        {"steps": [], "stop": "needs the city"},
        {
            "steps": [{"tool": "write", "text": "Pune", "where": "delivery city"}, {"tool": "click", "target": "Apply"}],
            "done_shows": "Delivering to Pune",
        },
    )
    asked = []
    result = run_task("set my delivery city", env, FakeLaya(), brain, FACTS, ask=lambda q: asked.append(q) or "Pune")
    assert asked == ["Which city should it deliver to?"]
    assert result.done and "The user" in brain.asked[2][1] and "Pune" in brain.asked[2][1]


def test_without_an_ask_callback_the_question_is_reported():
    env = FakeEnv({"a": page("Shop", Item("Delivery city", "field"))}, "a")
    brain = FakeBrain({"steps": [], "done_shows": "Delivering to", "ask": "Which city?"}, {"steps": [], "stop": "needs the city"})
    result = run_task("set my delivery city", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.question == "Which city?"


def test_success_remembers_the_place_and_the_alias():
    env = chat_env()
    env.pages["ready"].items += [Item("Search people", "field", search=True)]
    env.on[("ready", "set Search people=Sofya")] = lambda e: e.pages["ready"].items.append(Item("Sofia Marin", "row"))
    env.pages["dm"] = page(
        "Brave Browser",
        Item("Sofia Marin", "heading"),
        Item("Message Sofia", "field"),
        Item("Send", BTN),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env.pages["dm sent"] = page(
        "Brave Browser",
        Item("Sofia Marin", "heading"),
        Item("see you at 5", "text"),
        Item("Message Sofia", "field"),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env.on[("ready", "click Sofia Marin")] = "dm"
    env.on[("dm", "click Send")] = "dm sent"
    plan = {
        "steps": [
            PLAN["steps"][0],
            {"tool": "find", "text": "Sofya", "meaning": "the chat with Sofya"},
            {"tool": "write", "text": "see you at 5", "where": "message box"},
            {"tool": "submit", "expect": "see you at 5"},
        ],
        "done_shows": "see you at 5",
        "contract": "sent(to Sofya)",  # memory learns only from a run whose declared contract was met
    }
    memory = HarnessMemory(":memory:")
    laya = FakeLaya(
        name_match=lambda m, s, items: ch("D5 name match", ("Sofia Marin", 0.8), (NONE, 0.2))
    )  # D5 hears the misspelling
    result = run_task("tell Sofya on acme chat see you at 5", env, laya, FakeBrain(plan), FACTS, memory, yes)
    assert result.done, result.trace
    assert memory.place_for("Acme chat") == "chat.acme.com in Brave Browser, profile Personal"
    assert memory.alias_for("Sofya", "acme.com") == "Sofia Marin"  # per site, never per browser
    assert memory.alias_for("Sofya", "Brave Browser") is None

    # the next run starts from the remembered place and the remembered name: no login page, no misspelling
    env2 = chat_env()
    env2.on[("finder", "url https://chat.acme.com/ Brave Browser Personal")] = "ready"
    run_task(TASK, env2, FakeLaya(), FakeBrain(PLAN), FACTS, memory, yes)
    assert env2.log[0] == "url https://chat.acme.com/ Brave Browser Personal"


# ---------------------------------------------------------------- streamed lines, end check, drift

LINES = """open_place Acme chat | ready: Channels | site: chat.acme.com
write build is green | where: message box
submit | expect: build is green
done: build is green"""


def test_the_first_tool_starts_before_the_plan_is_finished():
    env = chat_env()
    brain = FakeLineBrain(LINES, probe=lambda: len(env.log), delay=0.1)
    result = run_task(TASK, env, FakeLaya(), brain, FACTS, confirm=yes)
    assert result.done, result.trace
    assert brain.seen[0] == 0 and brain.seen[-1] > 0  # the env had acted before the brain's last line was read
    assert result.brain_calls == 1 and result.brain[0]["format"] == "lines" and result.timings["first_step_s"] >= 0


def test_one_brain_end_check_when_code_and_laya_cannot_tell():
    env = FakeEnv(
        {
            "a": page("Notes", Item("Folders", "heading"), Item("New Note", BTN)),
            "b": page("Notes", Item("Notes", "heading"), Item("groceries", "row"), Item("milk, eggs", "text")),
        },
        "a",
        {("a", "click New Note"): "b"},
    )
    laya = FakeLaya(outcome=lambda e, s: ch("D7 outcome", (NOT_SHOWN, 0.45), ("shown", 0.40)))
    brain = FakeLineBrain("click New Note\ndone: Saved to iCloud", "yes: the groceries note lists milk")
    result = run_task("make a groceries note with milk", env, laya, brain, FACTS)
    assert result.done and "brain check" in result.reason and result.brain_calls == 2

    env.at = "a"
    brain = FakeLineBrain("click New Note\ndone: Saved to iCloud", "no: the note has no milk", "stop: cannot tell")
    result = run_task("make a groceries note with milk", env, laya, brain, FACTS)
    assert not result.done and sum(1 for s in result.brain if s["kind"] == "end check") == 1


def test_our_own_command_echoed_by_a_terminal_is_not_the_end_state():
    env = FakeEnv(
        {
            "a": page("Terminal", Item("me@mac atlas %", "text")),
            "b": page("Terminal", Item("me@mac atlas % make test", "text"), Item("running 12 tests", "text")),
        },
        "a",
        {("a", "type make test\n"): "b"},
    )
    brain = FakeLineBrain("type make test\\n\ndone: make test", "stop: the tests are still running")
    result = run_task("run make test in atlas", env, FakeLaya(), brain, FACTS)
    assert not result.done and "stopped" in result.reason


def test_a_login_page_mid_task_returns_to_the_place_that_worked_before_any_rescue():
    env = chat_env()
    expired = {"once": True}

    def session_expired(e):
        if expired.pop("once", False):
            e.at = "login"

    env.on[("ready", "set Message #general=build is green")] = session_expired
    result = run_task(TASK, env, FakeLaya(), FakeLineBrain(LINES), FACTS, confirm=yes)
    assert result.done, result.trace
    assert result.brain_calls == 1 and any("drifted" in line for line in result.trace)
    assert env.log.count("url https://chat.acme.com/ Brave Browser Personal") == 2


def test_failures_reach_the_rescue_and_a_remembered_place_that_fails_is_demoted():
    env = chat_env()
    memory = HarnessMemory(":memory:")
    memory.place_result("Acme chat", "chat.acme.com in Brave Browser, profile Work", True)
    brain = FakeLineBrain(
        "contract: sent\n" + LINES.replace("submit | expect: build is green", "click Publish"),
        "submit | expect: build is green\ndone: build is green",
    )

    def target(intent, items):  # nothing is "Publish"; the send control is Send
        if "publish" in intent.lower():
            return ch("D3 target", (NONE, 0.9))
        return ch("D3 target", *[(i.name, 0.9 if i.name == "Send" else 0.01) for i in items], (NONE, 0.0))

    laya = FakeLaya(target=target)
    result = run_task(TASK, env, laya, brain, FACTS, memory, yes)
    assert result.done, result.trace
    assert "Failures so far" in brain.asked[1][1] and "click Publish" in brain.asked[1][1]
    assert memory.place_for("Acme chat") == "chat.acme.com in Brave Browser, profile Personal"
    assert "chat.acme.com in Brave Browser, profile Work" in memory.notes.tried("place", "", "Acme chat")


def test_a_mac_map_computed_by_the_caller_is_used_and_its_tabs_refreshed_while_planning():
    class Map:
        facts, tabs = FACTS, []

    refreshed = []
    env = chat_env()
    result = run_task(TASK, env, FakeLaya(), FakeLineBrain(LINES), Map(), confirm=yes, refresh=lambda m: refreshed.append(1) or m)
    assert result.done and refreshed == [1]


def test_the_end_state_reads_names_as_found_and_a_sent_message_is_never_sent_again():
    env = chat_env()
    env.pages["ready"].items += [Item("Search people", "field", search=True)]
    env.on[("ready", "set Search people=Sofya")] = lambda e: e.pages["ready"].items.append(Item("Sofia Marin", "row"))
    env.pages["dm"] = page(
        "Brave Browser",
        Item("Sofia Marin", "heading"),
        Item("Message Sofia", "field"),
        Item("Send", BTN),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env.pages["dm sent"] = page(
        "Brave Browser",
        Item("Sofia Marin", "heading"),
        Item("see you at 5", "text"),
        Item("Message Sofia", "field"),
        Item("Send", BTN),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env.on[("ready", "click Sofia Marin")] = "dm"
    env.on[("dm", "click Send")] = "dm sent"
    lines = """open_place Acme chat | ready: Channels | site: chat.acme.com
find Sofya | meaning: the chat with Sofya
write see you at 5 | where: message box
submit | expect: see you at 5
done: Sofya: see you at 5"""
    laya = FakeLaya(name_match=lambda m, s, items: ch("D5 name match", ("Sofia Marin", 0.8), (NONE, 0.2)))
    result = run_task("tell Sofya on acme chat see you at 5", env, laya, FakeLineBrain(lines), FACTS, confirm=yes)
    assert result.done and result.brain_calls == 1, result.trace  # "Sofya" in done reads as "Sofia Marin"

    from karta.harness.tools import Toolbox

    tb = Toolbox("t", env, FakeLaya(), FACTS, None, yes)
    tb.submitted = "Send"
    assert tb.tool_click("Send").outcome.value == "done, but the screen did not change" and env.log[-1] == "click Send"
    assert tb.tool_submit("see you at 5").detail.startswith("already sent") and env.log.count("click Send") == 1


# ---------------------------------------------------------------- a lived-in Mac


def test_a_lived_in_map_sends_the_run_straight_to_the_signed_in_place():
    from karta.harness.sim import lived_in

    scenario = {
        "facts": "Hidden.",
        "harness": {"installed_apps": FACTS.installed_apps, "browser_profiles": FACTS.browsers, "project_folders": FACTS.folders},
        "now": FACTS.now,
        "lived_in": {"tabs": [], "signed_in": ["chat.acme.com in Brave Browser, profile Personal"]},
    }
    played, mac_map = lived_in(scenario)
    assert played["facts"] == "Hidden." and mac_map.signed_in and mac_map.tabs == []
    env = chat_env()
    env.on[("finder", "url https://chat.acme.com/ Brave Browser Personal")] = "ready"
    result = run_task(TASK, env, FakeLaya(), FakeLineBrain(LINES), mac_map, confirm=yes, refresh=lambda m: m)
    assert result.done and env.log[0] == "url https://chat.acme.com/ Brave Browser Personal"  # no login page first


def test_a_lived_in_tab_is_switched_to_and_the_role_player_is_told():
    from karta.harness.sim import lived_in

    scenario = {
        "facts": "Hidden.",
        "harness": {"installed_apps": FACTS.installed_apps, "browser_profiles": FACTS.browsers},
        "now": FACTS.now,
        "lived_in": {
            "tabs": [{"browser": "Brave Browser", "window": 2, "tab": 1, "url": "https://chat.acme.com/", "profile": "Personal"}],
            "signed_in": [],
        },
    }
    played, mac_map = lived_in(scenario)
    assert "window 2 (profile Personal), tab 1: https://chat.acme.com/ is already open" in played["facts"]
    env = chat_env()
    env.on[("finder", "tab Brave Browser 2.1")] = "ready"
    result = run_task(TASK, env, FakeLaya(), FakeLineBrain(LINES), mac_map, confirm=yes, refresh=lambda m: m)
    assert result.done and env.log[0] == "tab Brave Browser 2.1"


def test_written_content_read_back_is_the_end_state():
    from karta.harness.loop import written_into
    from karta.harness.types import Item, Outcome, Screen, ToolResult

    ok = ToolResult(Outcome.OK)
    wrote = [({"tool": "type", "text": "Karta test\nmilk"}, ok)]
    start = Screen("Notes", [Item("note body text view", "field", value="Old note")])
    after = Screen("Notes", [Item("note body text view", "field", value="Karta test\nmilk")])
    assert written_into(after, start, "Karta test milk", wrote) == "note body text view"
    # the same text there at the start proves nothing; a search box never counts; a send step means it must be sent
    assert written_into(after, after, "Karta test milk", wrote) is None
    search = Screen("Notes", [Item("Search", "field", value="Karta test milk", search=True)])
    assert written_into(search, start, "Karta test milk", wrote) is None
    assert written_into(after, start, "Karta test milk", [*wrote, ({"tool": "submit"}, ok)]) is None


def results_env():
    home = page("Brave Browser", Item("Search", BTN), Item("Query", "field"), url="https://videos.example.com/")
    results = page(
        "Brave Browser",
        Item("Results for cats", "heading"),
        Item("Cats compilation", "link"),
        url="https://videos.example.com/results?q=cats",
    )
    return FakeEnv({"home": home, "results": results}, "home", {("home", "click Search"): "results"})


def test_checkpoints_never_skip_a_step_that_writes_or_sends():
    env = results_env()
    plan = {
        "steps": [
            {"tool": "click", "target": "Search"},
            {"tool": "write", "text": "cats", "where": "Query"},
            {"tool": "submit", "expect": "Results", "then": "videos.example.com/results"},
        ],
        "done_shows": "Results for cats",
    }
    brain = FakeBrain(plan)
    result = run_task("search the video site for cats", env, FakeLaya(), brain, FACTS)
    # a results page already showing is no proof that *this* search ran: writing and sending are never skipped
    assert not any("reached already" in line for line in result.ledger), result.ledger


def test_a_fix_that_types_the_text_drops_the_plans_step_that_would_type_it_again():
    write = {"tool": "write", "text": "hello there", "where": "message box"}
    again = {"tool": "type", "text": "Hello there"}
    send = {"tool": "submit", "expect": "hello there"}
    assert with_rest([write], [again, send], replan=False) == [write, send]
