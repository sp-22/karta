"""Carrying data between steps: collect (code takes the candidates, skips ads by structure, Laya says which fit), {name}
in a later step's text, tell as a question's last step, the plan checks, and the speed gate on the held-out carry
bench (karta/harness/carry_cases.py). Fakes only: nothing is said aloud and nothing touches the Mac."""

from __future__ import annotations

from itertools import pairwise

import pytest
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page, toolbox

from karta.harness import carry_cases, loop
from karta.harness.brain import HarnessBrain, check_step, parse_line, parse_plan, plan_mode, step_line
from karta.harness.decide import FITS, OTHER
from karta.harness.mac import url_text
from karta.harness.screen import item_to_json
from karta.harness.sim import ScriptedMac, to_items
from karta.harness.tools import AD_ASK, Collected, ad_label, collect_candidates, wanted_value
from karta.harness.types import Item, Outcome, Screen

# ---------------------------------------------------------------- the plan lines


def test_a_collect_line_with_its_count_and_name():
    kind, step = parse_line("collect links to charting libraries | count: 3 | as: libs")
    assert kind == "step" and step == {"tool": "collect", "what": "links to charting libraries", "count": 3, "slot": "libs"}
    assert step_line(step) == "collect links to charting libraries | count: 3 | as: libs"


def test_collect_defaults_and_drops_a_checkpoint():
    kind, step = parse_line("collect the overdue invoices | then: Invoices")
    assert kind == "step" and step == {"tool": "collect", "what": "the overdue invoices", "slot": "items"}


def test_collect_count_is_one_to_ten_and_its_name_one_word():
    assert parse_line("collect links | count: 12 | as: x")[0] == "error"
    assert parse_line("collect links | count: 0 | as: x")[0] == "error"
    assert parse_line("collect links | as: two words")[0] == "error"
    assert parse_line("collect links | count: 3 items | as: {Libs}")[1]["slot"] == "libs"


def test_a_tell_line_and_its_forms():
    kind, step = parse_line("tell which invoices are overdue | from: overdue | as: list")
    assert kind == "step" and step == {
        "tool": "tell",
        "question": "which invoices are overdue",
        "slot": "overdue",
        "form": "list",
    }
    assert parse_line("tell what is due | from: tasks")[1]["form"] == "answer"
    assert parse_line("tell what is due | from: tasks | as: poem")[0] == "error"
    kind, why = parse_line("tell what is due")
    assert kind == "error" and "from:" in why


def test_json_steps_take_as_and_from():
    plan = parse_plan(
        {
            "steps": [
                {"tool": "collect", "what": "open pull requests", "count": "2", "as": "prs"},
                {"tool": "tell", "question": "which are open", "from": "prs", "as": "list"},
            ]
        }
    )
    assert not plan.errors
    assert plan.steps[0]["slot"] == "prs" and plan.steps[0]["count"] == 2 and plan.steps[1]["slot"] == "prs"
    assert check_step({"tool": "collect", "what": "x", "count": "many"})[1]


def stream(*lines: str, known=()):
    hb = HarnessBrain(FakeLineBrain("\n".join(lines)))
    plan = hb._collect("sys", "user", "plan", known)
    return plan, hb


def test_a_name_needs_an_earlier_collect_and_tell_comes_last():
    plan, _ = stream(
        "write {libs} | where: description",
        "collect links to charting libraries | count: 2 | as: libs",
        "write Worth a look:\\n{libs} | where: description",
        "tell what was found | from: nothing",
        "tell what was found | from: libs",
        "click Save",
    )
    assert [s["tool"] for s in plan.steps] == ["collect", "write", "tell"]
    assert any("{libs} needs an earlier step" in e for e in plan.errors)
    assert any("nothing is collected as 'nothing'" in e for e in plan.errors)
    assert any("nothing comes after tell" in e for e in plan.errors)


def test_a_rescue_may_use_the_names_already_collected_and_braces_in_typed_code_are_text():
    plan, _ = stream("write {libs} | where: description", known=("libs",))
    assert not plan.errors and plan.steps[0]["text"] == "{libs}"
    plan, _ = stream("type function f() {return}\\n")
    assert not plan.errors


def test_the_plan_prompt_teaches_collect_names_and_tell_without_the_demo_words():
    from karta.harness.brain import PLAN_SYS, PLAN_SYS_JSON

    for text in (PLAN_SYS, PLAN_SYS_JSON):
        assert "collect" in text and "tell" in text and "{kites}" in text
        low = text.lower()
        assert not any(w in low for w in ("youtube", "gmail", "calendar", "cold brew", "coffee"))


# ---------------------------------------------------------------- reading items off the screen


def link(name: str, url: str, **kw) -> Item:
    return Item(name, "link", url=url, **kw)


RESULTS = Screen(
    "Brave Browser",
    [
        Item("Address and search bar", "field", value="https://find.example/?q=charts", chrome=True, search=True),
        link("Images", "https://find.example/images?q=charts"),
        Item("Sponsored", "text"),
        link("Buy a charting suite", "https://ads.example/c?1"),
        link("Chart.js — open source charts", "https://www.chartjs.org/"),
        link("chartjs.org", "https://www.chartjs.org/"),
        link("Ad · Premium dashboards", "https://ads.example/c?2"),
        link("Apache ECharts", "https://echarts.apache.org/"),
        link("Plotly for Python", "https://plotly.com/python/"),
        link("Recharts: composable charts for React", "https://recharts.org/"),
        link("This page", "https://find.example/?q=charts#top"),
        link("Privacy", "https://find.example/privacy"),
    ],
    url="https://find.example/?q=charts",
)


def test_an_ad_is_laya_s_call_asked_only_of_an_item_with_a_short_label_of_its_own():
    page = RESULTS.page()
    labelled = [i.name for i in page if ad_label(i, page) is not None]
    assert "Buy a charting suite" in labelled and "Apache ECharts" not in labelled  # a label over it, by structure
    env = FakeEnv({"r": RESULTS}, "r")

    def ad(what, item):  # Laya, made up: the label it is shown says it is paid for
        assert what == AD_ASK
        return ch("D9 collect", (FITS, 0.9) if item.name.startswith(("Sponsored:", "Ad ·")) else (OTHER, 0.9))

    laya = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9)), ad=ad)
    tb = toolbox(env, laya)
    assert tb.tool_collect("links to charting libraries", 0, "libs").ok
    taken = [c.title for c in tb.slots["libs"]]
    assert "Buy a charting suite" not in taken and "Ad · Premium dashboards" not in taken and "Apache ECharts" in taken
    assert "ad" in laya.asked


def test_links_asked_for_are_one_per_address_under_their_longest_name_never_ads_or_the_site_s_own_links():
    names = [i.name for i in collect_candidates(RESULTS, "links to charting libraries")]
    assert names == [
        "Buy a charting suite",  # an ad is Laya's to judge, at the collect (Toolbox.is_ad)
        "Chart.js — open source charts",
        "Ad · Premium dashboards",
        "Apache ECharts",
        "Plotly for Python",
        "Recharts: composable charts for React",
    ]


def test_values_asked_for_keep_only_items_holding_one():
    scr = Screen(
        "Chatterbox",
        [
            Item("Chats", "heading"),
            Item("Your parcel left the depot on 2 Oct", "text"),
            Item("Use 771204 to sign in. Expires in 10 minutes.", "text"),
            Item("3 chats", "text"),
        ],
        window="Chats",
    )
    got = collect_candidates(scr, "the sign-in code")
    assert [i.name for i in got] == ["Use 771204 to sign in. Expires in 10 minutes."]
    assert wanted_value("the sign-in code", got[0].name) == "771204"
    assert wanted_value("the sign-in code", "Your code: G-552901") == "G-552901"
    assert wanted_value("the price", "Kettle · ₹1,299 · in stock") == "₹1,299"
    assert wanted_value("the overdue invoices", "anything") == ""


def test_a_list_s_own_title_and_its_count_are_not_its_items():
    scr = Screen(
        "Tasklane",
        [Item("Today", "row"), Item("Due today", "heading"), Item("Pay rent", "row"), Item("4 tasks", "text"), Item("Add", BTN)],
        window="Today",
    )
    assert [i.name for i in collect_candidates(scr, "tasks due today")] == ["Pay rent"]


def collect_env(*pages_: Screen) -> FakeEnv:
    names = [f"p{n}" for n in range(len(pages_))]
    return FakeEnv(dict(zip(names, pages_, strict=True)), names[0], {(a, "scroll down"): b for a, b in pairwise(names)})


def test_collect_takes_the_first_n_that_fit_in_screen_order_and_stops_reading():
    laya = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (OTHER if "Apache" in i.name else FITS, 0.9)))
    tb = toolbox(collect_env(RESULTS), laya)
    result = tb.tool_collect("links to charting libraries", 2, "libs")
    assert result.ok and result.detail.startswith("2 items collected as {libs}")
    assert tb.slots["libs"] == [
        Collected("Chart.js — open source charts", "https://www.chartjs.org/"),
        Collected("Plotly for Python", "https://plotly.com/python/"),
    ]
    assert laya.asked.count("collect_fit") == 3  # Chart.js, ECharts (sure it is not one), Plotly: Recharts never asked
    assert "chartjs.org/" not in result.detail.replace("(www.chartjs.org)", "")  # hosts only, never a path


def test_collect_reads_more_only_when_fewer_show_and_says_how_many_it_found():
    more = Screen(
        "Brave Browser", [link("Recharts", "https://recharts.org/"), link("Apache ECharts", "https://echarts.apache.org/")]
    )
    first = Screen("Brave Browser", [link("Apache ECharts", "https://echarts.apache.org/")])
    yes = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9)))
    tb = toolbox(collect_env(first, more), yes)
    result = tb.tool_collect("links to charting libraries", 2, "libs")
    assert result.ok and [c.title for c in tb.slots["libs"]] == ["Apache ECharts", "Recharts"]
    # no title names the topic, so Laya's fill-ins wait until the page is read to its end
    assert tb.env.log.count("scroll down") == 2
    named = Screen(
        "Brave Browser", [link("Charts for Vue", "https://vue.example/"), link("Charts for React", "https://react.example/")]
    )
    fresh = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9)))
    tb = toolbox(collect_env(Screen("Brave Browser", [link("Charts for React", "https://react.example/")]), named), fresh)
    assert tb.tool_collect("links to charts", 2, "libs").ok  # titles that name it: reading stops once two do
    assert tb.env.log == ["scroll down"] and "collect_fit" not in tb.laya.asked
    tb = toolbox(collect_env(first), yes)
    result = tb.tool_collect("links to charting libraries", 3, "libs")
    assert result.ok and "1 of the 3 asked for" in result.detail  # fewer than asked is what there is: never a failure
    assert len(tb.slots["libs"]) == 1


def test_collect_without_a_count_takes_everything_that_fits_and_never_scrolls():
    tb = toolbox(collect_env(RESULTS), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("links to charting libraries", 0, "all").ok
    assert len(tb.slots["all"]) == 4 and tb.env.log == []


def test_laya_only_vetoes_what_it_is_sure_is_something_else():
    unsure = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (OTHER, 0.55), (FITS, 0.45), gate=0.6))
    tb = toolbox(collect_env(RESULTS), unsure)
    result = tb.tool_collect("links to charting libraries", 1, "libs")
    assert result.ok and "could not tell" in result.detail and len(tb.slots["libs"]) == 1
    no = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (OTHER, 0.95)))
    tb = toolbox(collect_env(RESULTS), no)
    result = tb.tool_collect("links to charting libraries", 1, "libs")
    assert result.outcome is Outcome.NOT_FOUND and "libs" not in tb.slots


# ---------------------------------------------------------------- {name} in a later step


def compose_env() -> FakeEnv:
    return FakeEnv(
        {
            "c": page(
                "Brave Browser",
                Item("Subject", "field"),
                Item("Message body", "field", multiline=True),
                Item("Send", BTN),
            )
        },
        "c",
    )


def with_slots(env) -> object:
    tb = toolbox(env)
    tb.slots["libs"] = [
        Collected("Apache ECharts", "https://echarts.apache.org/"),
        Collected("Recharts", "https://recharts.org/"),
    ]
    tb.planned.add("libs")
    return tb


def test_a_name_fills_a_box_of_several_lines_one_item_per_line():
    tb = with_slots(compose_env())
    result = tb.tool_write("Two picks:\n{libs}", "message body")
    assert result.ok and "{libs}" in result.detail and "recharts.org" not in result.detail
    assert tb.env.pages["c"].items[1].value == (
        "Two picks:\nApache ECharts — https://echarts.apache.org/\nRecharts — https://recharts.org/"
    )
    assert tb.last_write[1].endswith("https://recharts.org/")  # the send check compares the expanded text


def test_a_name_fills_a_one_line_field_comma_separated():
    tb = with_slots(compose_env())
    assert tb.tool_write("{libs}", "subject").ok
    assert tb.env.pages["c"].items[0].value == "Apache ECharts — https://echarts.apache.org/, Recharts — https://recharts.org/"


def test_a_value_goes_in_alone():
    tb = toolbox(compose_env())
    tb.slots["code"] = [Collected("Use 771204 to sign in", value="771204")]
    assert tb.tool_write("{code}", "subject").ok and tb.env.pages["c"].items[0].value == "771204"


def test_an_unknown_or_empty_name_fails_the_step_and_types_nothing():
    tb = with_slots(compose_env())
    result = tb.tool_write("{links}", "subject")
    assert result.outcome is Outcome.NO_DATA and "{links}" in result.detail and "{libs}" in result.detail
    assert tb.env.log == []
    tb = toolbox(compose_env())
    tb.planned.add("libs")  # collected, but it kept nothing
    assert tb.tool_write("{libs}", "subject").outcome is Outcome.NO_DATA


def test_without_any_collect_braces_are_text():
    tb = toolbox(compose_env())
    assert tb.tool_write("{draft}", "subject").ok and tb.env.pages["c"].items[0].value == "{draft}"


def test_the_send_check_refuses_a_box_holding_the_expanded_message_twice():
    tb = with_slots(compose_env())
    assert tb.tool_write("Two picks:\n{libs}", "message body").ok
    body = tb.env.pages["c"].items[1]
    assert tb.not_exact(tb.look()) is None
    tb.env.set_value("Message body", body.value + "\n" + body.value)
    assert "2 times" in (tb.not_exact(tb.look()) or "")
    tb.env.set_value("Message body", body.value + "\n-- \nSent from my Mac")  # a signature is fine
    assert tb.not_exact(tb.look()) is None


# ---------------------------------------------------------------- the loop: skip-ahead, tell, the gate


def test_skip_ahead_never_skips_a_collect():
    results = page(
        "Brave Browser", link("Apache ECharts", "https://echarts.apache.org/"), Item("Saved", "text"), url="https://find.example/"
    )
    env = FakeEnv({"r": results}, "r")
    brain = FakeLineBrain(
        "collect links to charting libraries | count: 1 | as: libs\nclick Save | then: Saved items here\ndone: Saved"
    )
    yes = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9)))
    result = loop.run_task("save one charting library", env, yes, brain, FACTS)
    assert result.ledger and result.ledger[0].startswith("✓ collect")


def told(monkeypatch, said: list):
    def speak(text):
        said.append(text)

        class Speech:
            def stop(self):
                said.append("(stopped)")

        return Speech()

    monkeypatch.setattr(loop, "speak", speak)


TASKS = page(
    "Tasklane",
    Item("Due today", "heading"),
    Item("Pay the electricity bill", "row"),
    Item("Return the library books", "row"),
    Item("Add task", BTN),
)


def test_a_question_ends_once_the_answer_is_said_with_one_brain_call_for_a_list(monkeypatch):
    said: list[str] = []
    told(monkeypatch, said)
    env = FakeEnv({"t": TASKS}, "t")
    brain = FakeLineBrain("collect the tasks due today | as: tasks\ntell what is due today | from: tasks | as: list")
    result = loop.run_task("what is due today?", env, FakeLaya(), brain, FACTS)
    assert result.done and result.brain_calls == 1, result.trace
    assert "Pay the electricity bill" in result.answer and "Return the library books" in result.answer
    assert said == [result.answer] and result.speech is not None
    assert result.ledger[-1].startswith("✓ tell")


def test_an_answer_worded_by_the_brain_costs_one_more_call_at_most(monkeypatch):
    told(monkeypatch, [])
    env = FakeEnv({"t": TASKS}, "t")
    brain = FakeLineBrain(
        "collect the tasks due today | as: tasks\ntell is the bill due today | from: tasks | as: answer",
        "Yes, the electricity bill is due today.",
    )
    result = loop.run_task("is the electricity bill due today?", env, FakeLaya(), brain, FACTS)
    assert result.done and result.brain_calls == 2 and result.answer == "Yes, the electricity bill is due today."


def test_a_collect_asking_for_more_than_there_is_returns_what_it_found_with_the_count(monkeypatch):
    """ "What's on" with `count: 10` and one item on screen: the one item is the collect's, not a failed step. The
    contract decides what it is worth: a list with no count is answered by it (read out by code, no call); a list of
    three is not, and the task is not done."""
    told(monkeypatch, [])
    env = FakeEnv({"t": page("Tasklane", Item("Due today", "heading"), Item("Pay the electricity bill", "row"))}, "t")
    plan = "collect the tasks due today | count: 10 | as: tasks\ntell what is due today | from: tasks | as: list"
    brain = FakeLineBrain("contract: list(items)\n" + plan)
    result = loop.run_task("what is due today?", env, FakeLaya(), brain, FACTS)
    assert result.done and result.rescues == 0 and result.answer == "Pay the electricity bill.", result.trace
    assert any("1 of the 10 asked for" in line for line in result.ledger) and result.brain_calls == 1
    brain = FakeLineBrain("contract: list(3, items)\n" + plan, "stop: only one task is due today")
    result = loop.run_task("what are the 3 tasks due today?", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.answer is None and "only 1 of the 3 items" in result.failed_why


def test_a_tell_with_nothing_collected_climbs_the_ladder(monkeypatch):
    told(monkeypatch, [])
    env = FakeEnv({"t": page("Tasklane", Item("Due today", "heading"), Item("Add task", BTN))}, "t")
    brain = FakeLineBrain("collect the tasks due today | as: tasks\ntell what is due today | from: tasks | as: list")
    result = loop.run_task("what is due today?", env, FakeLaya(), brain, FACTS)
    assert not result.done and result.answer is None and result.rescues >= 1


@pytest.mark.parametrize("prompt", ["v1", "v2"])
def test_the_held_out_carry_bench_passes_within_the_brain_call_gate(monkeypatch, prompt):
    monkeypatch.setenv("KARTA_PROMPT", prompt)
    rows = carry_cases.bench(FakeLaya())
    assert [r["case"] for r in rows] == ["search->mail", "list->tell", "code->signin"]
    for r in rows:
        assert r["ok"] and r["done"], r
        assert r["brain_calls"] <= r["gate"], r  # 1 per task (per page, planning per page), plus 1 for a worded tell
    if plan_mode() == "blind":
        assert sum(r["brain_calls"] for r in rows) / len(rows) <= 1.34


# ---------------------------------------------------------------- reading links on the Mac and in the simulator


def test_a_link_address_reads_from_an_nsurl_or_a_string_and_nothing_else():
    class NSURL:
        def absoluteString(self):
            return "https://echarts.apache.org/en/index.html"

    assert url_text(NSURL()) == "https://echarts.apache.org/en/index.html"
    assert url_text("mailto:mira@example.com") == "mailto:mira@example.com"
    assert url_text(None) == "" and url_text("not a url") == "" and url_text(3) == ""


def test_the_role_played_mac_gives_links_their_address_and_text_areas_their_lines():
    items, _ = to_items(
        [
            {"name": "Apache ECharts", "kind": "link", "url": "https://echarts.apache.org/"},
            {"name": "Body", "kind": "field", "multiline": True},
            {"name": "Save", "kind": "button", "url": "https://x.example/"},
        ]
    )
    assert items[0].url == "https://echarts.apache.org/" and items[1].multiline and items[2].url == ""
    assert item_to_json(items[0])["url"] == "https://echarts.apache.org/"


def test_the_scripted_mac_moves_by_exact_or_starting_action():
    mac = ScriptedMac(
        {"a": page("Finder"), "b": page("Brave Browser", Item("To", "field"))}, "a", {("a", "url https://mail.example"): "b"}
    )
    mac.open_url("https://mail.example/", "Brave Browser", "Work")
    assert mac.at == "b"
    mac.set_text(Item("To", "field"), "mira@example.com")
    assert mac.field_value("To") == "mira@example.com" and mac.log[-1] == "set To"


def test_a_search_box_that_keeps_its_query_counts_as_sent_once_the_page_moves_on():
    env = FakeEnv(
        {
            "h": page("Brave Browser", Item("Search", "field", search=True), url="https://find.example/"),
            "r": page(
                "Brave Browser",
                Item("Search", "field", value="charts", search=True),
                link("Apache ECharts", "https://echarts.apache.org/"),
                url="https://find.example/?q=charts",
            ),
        },
        "h",
        {("h", "press return"): "r"},
    )
    tb = toolbox(env)
    assert tb.tool_write("charts", "search box").ok
    assert tb.tool_submit("Apache ECharts").ok and env.at == "r"


def test_a_sites_own_subdomain_is_its_own_nav_not_a_result():
    from karta.harness.tools import _site_of

    assert _site_of("https://studio.example.com/channel") == _site_of("www.example.com")
    assert _site_of("shop.example.co.uk") == "example.co.uk"
    assert _site_of("https://other.org/watch") != _site_of("example.com")


def test_a_page_counts_as_on_a_site_by_its_address_not_its_words():
    from karta.harness.tools import on_site
    from karta.harness.types import Item, Screen

    mail = Screen("Brave Browser", [Item("VideoSite weekly digest", "text")], url="https://mail.example.org/inbox")
    assert not on_site(mail, "https://videosite.example/")
    assert on_site(Screen("Brave Browser", [], url="https://m.videosite.example/results"), "https://videosite.example/")


def test_collect_never_takes_a_system_prompt_or_a_dialog_over_the_page():
    from karta.harness.tools import collect_candidates

    prompt = Screen("UserNotificationCenter", [Item("Allow Full Access to your calendars", BTN, dialog=True)])
    assert collect_candidates(prompt, "the events on my calendar today") == []
    over = Screen(
        "Planbook",
        [Item("Team lunch at the canal cafe", "row"), Item("Keep these changes for later", BTN, dialog=True)],
    )
    assert [i.name for i in collect_candidates(over, "the events today")] == ["Team lunch at the canal cafe"]
