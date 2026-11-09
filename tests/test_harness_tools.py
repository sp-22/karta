"""Harness v2 tools on a scripted fake Mac, a fake Laya returning scripted Choices, and a fake brain.

The code facts (karta.harness.facts, risk) are the real ones: they are rules, not models. Laya never runs here.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime

import numpy as np

from karta.harness.brain import HarnessBrain, check_step, parse_plan
from karta.harness.decide import DOES_IT, DOES_OTHER, FITS, LOGIN, NONE, NOT_SHOWN, OTHER, READY, SHOWN, labels
from karta.harness.facts import Tab
from karta.harness.sim import to_items
from karta.harness.tools import AD_ASK, Toolbox
from karta.harness.types import Choice, Item, MacFacts, Outcome, Screen

FACTS = MacFacts(
    ["Brave Browser", "Finder", "Notes", "Terminal"],
    {"Brave Browser": ["Work", "Personal"]},
    ["~/Projects/atlas"],
    datetime(2026, 10, 6, 9, 0),
)


# ---------------------------------------------------------------- fakes


def page(app: str, *items: Item, url: str | None = None, profile: str | None = None) -> Screen:
    return Screen(app, list(items), url=url, profile=profile)


class FakeEnv:
    """Pages by name; an action moves to the page `on[(page, action)]` names (or runs it when callable)."""

    def __init__(self, pages: dict[str, Screen], start: str, on: dict | None = None):
        self.pages, self.at, self.on, self.log = pages, start, dict(on or {}), []
        self.sim_seconds = 0.0

    def screen(self) -> Screen:
        return replace(self.pages[self.at], items=list(self.pages[self.at].items))  # a new read each time, like a real Mac

    def _go(self, action: str) -> None:
        self.log.append(action)
        nxt = self.on.get((self.at, action))
        if callable(nxt):
            nxt(self)
        elif nxt:
            self.at = nxt

    def set_value(self, name: str, text: str) -> None:
        scr = self.pages[self.at]
        scr.items = [replace(i, value=text) if i.kind == "field" and i.name == name else i for i in scr.items]

    def click(self, item: Item) -> None:
        self._go(f"click {item.name}")

    def set_text(self, item: Item, text: str) -> None:
        if item.name not in getattr(self, "stubborn", ()):
            self.set_value(item.name, text)
        self._go(f"set {item.name}={text}")

    def type_at_focus(self, text: str) -> None:
        self._go(f"type {text}")

    def press(self, keys: str) -> None:
        self._go(f"press {keys}")

    def open_app(self, app: str) -> None:
        self._go(f"open {app}")

    def open_url(self, url: str, browser: str, profile: str | None = None) -> None:
        self._go(f"url {url} {browser} {profile}")

    def focus_tab(self, browser: str, window: int, tab: int) -> None:
        self._go(f"tab {browser} {window}.{tab}")

    def scroll(self, direction: str) -> None:
        self._go(f"scroll {direction}")

    def back(self) -> None:
        self._go("back")

    def wait(self, seconds: float) -> None:
        self._go("wait")


def ch(decision: str, *ranked: tuple[str, float], gate: float = 0.5) -> Choice:
    return Choice(decision, list(ranked), gate)


def _overlap(a: str, b: str) -> int:
    return len(set(a.lower().replace("'", " ").split()) & set(b.lower().split()))


class FakeLaya:
    """Scripted per method (a function of the question's args); sensible sure defaults otherwise."""

    def __init__(self, **script):
        self.script, self.asked = script, []
        self.embed = lambda texts: np.ones((len(texts), 3))

    def _answer(self, method, default, *args) -> Choice:
        self.asked.append(method)
        return (self.script.get(method) or default)(*args)

    def place(self, what, places):
        return self._answer(
            "place", lambda w, ps: ch("D1 place", *[(p, 0.9 - 0.1 * n) for n, p in enumerate(ps)], (NONE, 0.0)), what, places
        )

    def page_state(self, what, screen):
        return self._answer("page_state", lambda w, s: ch("D2 page state", (READY, 0.9)), what, screen)

    def screen_kind(self, screen, page=None):
        """By default, roughly as Laya reads a page: a sign-in screen when it says so ("sign in", "log in")."""

        def default(screen, page):
            said = " ".join(i.name.lower() for i in screen.page())
            return ch("D2 page state", (LOGIN, 0.95) if ("sign in" in said or "log in" in said) else (READY, 0.95))

        return self._answer("screen_kind", default, screen, page)

    def target(self, intent, items):
        def default(intent, items):
            names = labels(items)
            ranked = sorted(names, key=lambda k: -_overlap(intent, names[k].name))
            return ch("D3 target", *[(k, 0.9 if n == 0 else 0.01) for n, k in enumerate(ranked)], (NONE, 0.0))

        return self._answer("target", default, intent, items)

    def pick_ok(self, intent, item):
        return self._answer("pick_ok", lambda i, it: ch("D4 pick check", (DOES_IT, 0.9), (DOES_OTHER, 0.1)), intent, item)

    def name_match(self, meaning, searched, items):
        def default(meaning, searched, items):
            names = labels(items)
            hit = next((k for k, v in names.items() if searched.lower() in v.name.lower()), None)
            return ch("D5 name match", (hit, 0.9), (NONE, 0.1)) if hit else ch("D5 name match", (NONE, 0.9))

        return self._answer("name_match", default, meaning, searched, items)

    def dialog_answer(self, task, options):
        return self._answer("dialog_answer", lambda t, o: ch("D6 dialog answer", (next(iter(labels(o))), 0.9)), task, options)

    def outcome(self, expected, screen):
        def default(expected, screen):
            shown = any(expected.lower() in i.name.lower() for i in screen.items)
            return ch("D7 outcome", (SHOWN if shown else NOT_SHOWN, 0.9))

        return self._answer("outcome", default, expected, screen)

    def collect_fit(self, what, item):
        """By default, roughly as Laya reads it: an item fits when it shares a word of four letters or more with what was
        asked for, holds digits when a code is asked for, or is a list row or link named in four words or more."""

        def default(what, item):
            text = f"{item.name} {item.value}".lower()

            want = {w for w in re.findall(r"[a-z0-9]+", what.lower()) if len(w) > 3}
            fits = (
                bool(want & set(re.findall(r"[a-z0-9]+", text)))
                or ("code" in what and any(c.isdigit() for c in text))
                or (item.kind in ("row", "link") and len(item.name.split()) >= 4)
            )
            return ch("D9 collect", (FITS if fits else OTHER, 0.9), (OTHER if fits else FITS, 0.1))

        if what == AD_ASK:  # is it an ad: Laya knows an ad's label when it is shown one (scripted apart, as "ad")
            text = f"{item.name} {item.value}".lower()
            ad = ch("D9 collect", (FITS, 0.9) if re.match(r"^(sponsored|ad)\b", text) else (OTHER, 0.9))
            return self._answer("ad", lambda w, i: ad, what, item)
        return self._answer("collect_fit", default, what, item)


class FakeBrain:
    def __init__(self, *replies):
        self.replies, self.calls, self.asked = list(replies), 0, []

    def json(self, system: str, user: str) -> dict:
        self.calls += 1
        self.asked.append((system, user))
        reply = self.replies.pop(0) if self.replies else {"steps": [], "stop": "nothing more to try"}
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeLineBrain:
    """A brain that streams its reply line by line (like ClaudeCodeBrain.stream_lines); `seen` records, for each line
    handed out, what `probe()` said then (the env's log length, to show a tool ran before the plan was finished)."""

    def __init__(self, *replies: str, probe=None, delay: float = 0.0):
        self.replies, self.calls, self.asked, self.seen, self.probe, self.delay = list(replies), 0, [], [], probe, delay

    def stream_lines(self, system: str, user: str):
        self.calls += 1
        self.asked.append((system, user))
        reply = self.replies.pop(0) if self.replies else "stop: nothing more to try"
        for n, line in enumerate(reply.strip().splitlines()):
            if n and self.delay:
                time.sleep(self.delay)  # a model writing its next line
            self.seen.append(self.probe() if self.probe else None)
            yield line


def toolbox(env, laya=None, task="do the thing", confirm=None, memory=None, upcoming=()) -> Toolbox:
    tb = Toolbox(task, env, laya or FakeLaya(), FACTS, memory, confirm or (lambda q: True))
    tb.upcoming = lambda: list(upcoming)
    return tb


BTN = "button"


# ---------------------------------------------------------------- click (D3, D4, the gate)


def test_click_by_exact_name_needs_no_laya():
    env = FakeEnv(
        {"a": page("Notes", Item("New Note", BTN), Item("Delete", BTN)), "b": page("Notes", Item("Body", "field"))},
        "a",
        {("a", "click New Note"): "b"},
    )
    laya = FakeLaya()
    result = toolbox(env, laya).tool_click("the New Note button")
    assert result.ok and env.log == ["click New Note"] and laya.asked == []


def test_an_unsure_target_is_never_clicked():
    env = FakeEnv({"a": page("App", Item("Archive", BTN), Item("Archive all", BTN))}, "a")
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", ("Archive", 0.3), ("Archive all", 0.25), (NONE, 0.2)),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_IT, 0.4), (DOES_OTHER, 0.35)),
    )
    result = toolbox(env, laya).tool_click("archive this thread")
    assert result.outcome is Outcome.UNSURE and env.log == []


def test_a_failed_pick_check_eliminates_the_pick_and_asks_d3_again_over_the_rest():
    env = FakeEnv(
        {"a": page("App", Item("Share", BTN), Item("Export", BTN)), "b": page("App", Item("Saved", "text"))},
        "a",
        {("a", "click Export"): "b"},
    )
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", ("Share", 0.7), ("Export", 0.2), (NONE, 0.1)),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_IT, 0.9) if it.name == "Export" else (DOES_OTHER, 0.9)),
    )
    result = toolbox(env, laya).tool_click("save a copy as a file")
    assert result.ok and env.log == ["click Export"] and laya.asked.count("target") == 2


def test_a_disabled_control_is_reported_not_clicked():
    env = FakeEnv({"a": page("Notes", Item("New Note", BTN, enabled=False), Item("Notes", "row"))}, "a")
    result = toolbox(env).tool_click("New Note")
    assert result.outcome is Outcome.DISABLED and "click New Note" not in env.log
    assert "enabled options near it: ['Notes']" in result.detail  # what to choose first


def test_a_disabled_control_gets_the_option_it_needs_chosen_first():
    def pick_folder(env):
        env.pages["a"].items = [Item("New Note", BTN), Item("Notes", "row")]

    env = FakeEnv(
        {
            "a": page("Notes", Item("New Note", BTN, enabled=False), Item("Notes", "row")),
            "b": page("Notes", Item("Body", "field")),
        },
        "a",
        {("a", "click Notes"): pick_folder},
    )
    env.on[("a", "click New Note")] = "b"
    result = toolbox(env).tool_click("New Note")
    assert result.ok and env.log == ["click Notes", "click New Note"]


def test_a_risky_click_asks_first_and_a_no_stops_it():
    env = FakeEnv({"a": page("App", Item("Delete account", BTN))}, "a")
    asked = []
    result = toolbox(env, confirm=lambda q: asked.append(q) or False).tool_click("Delete account")
    assert result.outcome is Outcome.REFUSED and env.log == [] and asked and asked[0].startswith("delete")


# ---------------------------------------------------------------- write, type, fill (verify what was typed)


def test_write_never_uses_the_address_bar_and_checks_the_field_holds_the_text():
    env = FakeEnv(
        {
            "a": page(
                "Brave Browser",
                Item("Search or type a URL", "field", chrome=True, search=True),
                Item("Search or type a URL", "field", search=True),
                Item("Add a card title", "field"),
            )
        },
        "a",
    )
    tb = toolbox(env)
    result = tb.tool_write("Ship v2", "card title")
    assert result.ok and env.log == ["set Add a card title=Ship v2"] and tb.pending == {"Ship v2": "Add a card title"}


def test_write_that_does_not_land_is_retried_once_then_reported():
    env = FakeEnv({"a": page("App", Item("Title", "field"))}, "a")
    env.stubborn = {"Title"}
    result = toolbox(env).tool_write("Q4 plan", "title")
    assert result.outcome is Outcome.UNEXPECTED and env.log.count("set Title=Q4 plan") == 2 and "click Title" in env.log


def test_type_at_the_focus_for_a_text_only_app():
    env = FakeEnv(
        {"a": page("Terminal", Item("me@mac ~ %", "text")), "b": page("Terminal", Item("me@mac ~ % ls", "text"))},
        "a",
        {("a", "type ls"): "b"},
    )
    tb = toolbox(env)
    assert tb.tool_type("ls").ok and "ls" in tb.echo


def test_fill_turns_on_the_checkbox_that_shows_a_hidden_field():
    def show_date(env):
        env.pages["a"].items = [
            Item("Title", "field", value="Call Sam"),
            Item("Date", "checkbox", checked=True),
            Item("Date", "field"),
        ]

    env = FakeEnv(
        {"a": page("Calendar", Item("Title", "field", value="Call Sam"), Item("Date", "checkbox", checked=False))},
        "a",
        {("a", "click Date"): show_date},
    )
    result = toolbox(env).tool_fill({"Date": "tomorrow"})
    assert result.ok and env.log == ["click Date", "set Date=tomorrow"]


# ---------------------------------------------------------------- find (Search control, spellings, aliases)


def test_find_clicks_the_visible_search_control_then_tries_the_next_spelling():
    people = [Item("Omar Farouk", "row"), Item("Sofia Marin", "row")]
    env = FakeEnv(
        {
            "home": page("Chat", Item("Search", BTN, search=True), *people),
            "search": page("Chat", Item("Search people", "field", focused=True, search=True), *people),
        },
        "home",
        {("home", "click Search"): "search"},
    )

    def results(env):
        query = next(i.value for i in env.pages["search"].items if i.kind == "field")
        rows = [Item("No people found", "text")] if query == "Sofya" else [Item("Sofia Marin", "row")]
        env.pages["search"].items = [Item("Search people", "field", value=query, focused=True, search=True), *rows]

    env.on[("search", "set Search people=Sofya")] = results
    env.on[("search", "set Search people=Sof")] = results
    env.on[("search", "click Sofia Marin")] = lambda e: setattr(e, "at", "home")
    tb = toolbox(env)
    result = tb.tool_find("Sofya", "the chat with Sofya", ["Sof"])
    assert result.ok, result
    assert env.log[0] == "click Search" and "press cmd+k" not in env.log
    assert env.log[-1] == "click Sofia Marin" and tb.aliases == {"Sofya": ("Chat", "Sofia Marin")}


def test_find_with_no_search_anywhere_in_a_text_only_app_says_so():
    env = FakeEnv({"a": page("Terminal", Item("me@mac ~ %", "text"))}, "a")
    result = toolbox(env).tool_find("atlas", "the atlas folder")
    assert result.outcome is Outcome.NO_FIELD and env.log == []


def test_find_does_not_open_an_unsure_match():
    env = FakeEnv({"a": page("App", Item("Search", "field", search=True), Item("Rahul K", "row"))}, "a")

    def narrowed(env):
        env.pages["a"].items = [Item("Search", "field", value="Ra", search=True), Item("Rahul K", "row"), Item("Ravi S", "row")]

    env.on[("a", "set Search=Ra")] = narrowed
    laya = FakeLaya(
        name_match=lambda m, s, items: ch("D5 name match", ("Rahul K", 0.4), ("Ravi S", 0.35), (NONE, 0.25)),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_OTHER, 0.8)),
    )  # neither is Raj
    result = toolbox(env, laya).tool_find("Ra", "the chat with Raj")
    assert result.outcome is Outcome.UNSURE and not any(a.startswith("click") for a in env.log)


# ---------------------------------------------------------------- open_place (next place, reuse, dialogs)


def site_env():
    login = page(
        "Brave Browser",
        Item("Sign in to Acme", "heading"),
        Item("Sign in with email", BTN),
        url="https://chat.acme.com/",
        profile="Work",
    )
    ready = page(
        "Brave Browser", Item("Channels", "heading"), Item("general", "row"), url="https://chat.acme.com/", profile="Personal"
    )
    return FakeEnv(
        {"finder": page("Finder", Item("Desktop", "row")), "login": login, "ready": ready},
        "finder",
        {
            ("finder", "url https://chat.acme.com/ Brave Browser Work"): "login",
            ("login", "url https://chat.acme.com/ Brave Browser Personal"): "ready",
        },
    )


def test_open_place_moves_past_a_login_page_and_reuses_the_place_it_found():
    env = site_env()
    tb = toolbox(env, task="post in acme chat")
    first = tb.tool_open_place("Acme chat", "Channels", site="chat.acme.com")
    assert first.ok and "Personal" in first.detail
    assert env.log == ["url https://chat.acme.com/ Brave Browser Work", "url https://chat.acme.com/ Brave Browser Personal"]
    again = tb.tool_open_place("Acme chat", "Channels", site="chat.acme.com")
    assert again.ok and len(env.log) == 2  # the found place is still in front, nothing reopened


def test_a_found_place_is_locked_it_is_brought_back_never_searched_for_again():
    env = site_env()
    tb = toolbox(env, task="post in acme chat")
    assert tb.tool_open_place("Acme chat", "Channels", site="chat.acme.com").ok
    env.at = "finder"  # the screen wandered off (another window came to the front)
    env.on[("finder", "url https://chat.acme.com/ Brave Browser Personal")] = "ready"
    again = tb.tool_open_place("Acme", site="chat.acme.com")  # the same site, named a little differently
    assert again.ok and "found earlier" in again.detail
    assert env.log[2:] == ["url https://chat.acme.com/ Brave Browser Personal"]  # back to Personal only: no Work, no ladder


def test_open_place_ignores_a_site_that_is_not_a_host_and_never_offers_a_missing_app():
    env = FakeEnv({"a": page("Finder", Item("Desktop", "row"))}, "a")
    result = toolbox(env, task="open photoshop").tool_open_place("Photoshop", site="the photoshop app")
    assert result.outcome is Outcome.NOT_INSTALLED and env.log == []


def test_open_place_answers_a_popup_before_judging_the_page():
    popup = page(
        "Notes", Item("Welcome to Notes", "heading", dialog=True), Item("Continue", BTN, dialog=True), Item("Folders", "heading")
    )
    ready = page("Notes", Item("Folders", "heading"), Item("Notes", "row"))
    env = FakeEnv(
        {"finder": page("Finder"), "popup": popup, "ready": ready},
        "finder",
        {("finder", "open Notes"): "popup", ("popup", "click Continue"): "ready"},
    )
    result = toolbox(env, task="make a note in Notes").tool_open_place("Notes", "Folders")
    assert result.ok and env.log == ["open Notes", "click Continue"]


# ---------------------------------------------------------------- answer, submit


def test_answer_types_a_terminal_choice_number():
    prompt = page(
        "Terminal",
        Item("Run this command?", "text", dialog=True),
        Item("1. Continue", "text", dialog=True),
        Item("2. Cancel", "text", dialog=True),
    )
    env = FakeEnv({"a": prompt, "b": page("Terminal", Item("running", "text"))}, "a", {("a", "press return"): "b"})
    result = toolbox(env).tool_answer("Continue")
    assert result.ok and env.log == ["type 1", "press return"]


def test_submit_clicks_the_visible_button_after_the_user_says_yes():
    box = Item("Message #general", "field", value="build is green")
    sent = page("Chat", Item("Message #general", "field"), Item("build is green", "text"), Item("Send", BTN))
    env = FakeEnv({"a": page("Chat", box, Item("Send", BTN)), "b": sent}, "a", {("a", "click Send"): "b"})
    asked = []
    tb = toolbox(env, confirm=lambda q: asked.append(q) or True)
    tb.last_write, tb.pending = ("Message #general", "build is green"), {"build is green": "Message #general"}
    result = tb.tool_submit("build is green")
    assert result.ok and env.log == ["click Send"] and asked and asked[0].startswith("send")


def test_submit_is_not_done_while_our_text_still_sits_in_the_field():
    box = Item("Message #general", "field", value="build is green")
    env = FakeEnv({"a": page("Chat", box, Item("Send", BTN), Item("typing...", "text"))}, "a")
    env.on[("a", "click Send")] = lambda e: e.pages["a"].items.append(Item("Retry", BTN))
    tb = toolbox(env)
    tb.last_write = ("Message #general", "build is green")
    result = tb.tool_submit("build is green")
    assert result.outcome is Outcome.UNEXPECTED and "still in" in result.detail


# ---------------------------------------------------------------- brain replies: parse, validate, one repair


def test_unknown_tools_and_fields_are_rejected():
    assert check_step({"tool": "teleport"})[1].startswith("unknown tool")
    assert "unknown field" in check_step({"tool": "click", "target": "Save", "x": 3})[1]
    assert check_step({"tool": "find", "text": "Sof", "meaning": "Sofia"})[0]["alternatives"] == []
    plan = parse_plan({"steps": [{"tool": "click", "target": "Save"}, {"tool": "fly"}], "done_shows": "Saved"})
    assert [s["tool"] for s in plan.steps] == ["click"] and plan.errors and plan.done_shows == "Saved"


def test_a_bad_plan_gets_one_repair_ask():
    brain = FakeBrain({"steps": [{"tool": "open_app", "app": "Notes"}]}, {"steps": [{"tool": "open_place", "what": "Notes"}]})
    plan = HarnessBrain(brain).plan("make a note", FACTS, page("Finder"))
    assert brain.calls == 2 and plan.steps == [{"tool": "open_place", "what": "Notes"}]
    assert "rejected" in brain.asked[1][1]


def test_the_plan_prompt_carries_the_facts_and_the_know_how():
    brain = FakeBrain({"steps": [{"tool": "open_place", "what": "Notes"}]})
    HarnessBrain(brain).plan("make a note", FACTS, page("Finder", Item("Desktop", "row")), ["Notes: pick a folder first"])
    user = brain.asked[0][1]
    assert "Notes: installed app" in user and "Notes: pick a folder first" in user and "Desktop" in user
    assert "Installed apps" not in user and "Finder" not in user.split("Front app")[0]  # a slice of the Mac, not all of it


# ---------------------------------------------------------------- the role-played screen


def test_role_player_items_map_to_items_with_rows():
    items, rows = to_items(
        [
            {"name": "ADD", "kind": "button", "row": 3, "region": "right"},
            {"name": "Oat bar 50g", "kind": "text", "row": 3},
            {"name": "File > New", "kind": "menu"},
            {"name": "Search", "kind": "field", "value": "oat", "focused": "true", "chrome": False, "dialog": True},
            ["Sidebar", "AXButton"],
            {"name": ""},
        ]
    )
    assert [i.name for i in items] == ["ADD", "Oat bar 50g", "File > New", "Search", "Sidebar"]
    assert rows[:2] == [3.0, 3.0] and items[2].menu and items[3].focused and items[3].dialog and items[3].value == "oat"
    assert items[0].region == "right" and items[0].ref == "ADD"


# ---------------------------------------------------------------- code first, typing safety


def test_a_named_control_is_matched_by_code_before_laya():
    env = FakeEnv(
        {"a": page("App", Item("Got it", BTN), Item("Learn more", BTN)), "b": page("App", Item("Home", "heading"))},
        "a",
        {("a", "click Got it"): "b"},
    )
    laya = FakeLaya()
    assert toolbox(env, laya).tool_click("the 'Got it' button").ok and env.log == ["click Got it"] and laya.asked == []


def test_one_result_containing_the_searched_text_is_opened_without_laya():
    env = FakeEnv({"a": page("Chat", Item("Search people", "field", search=True), Item("Direct messages", "heading"))}, "a")

    def results(env):
        env.pages["a"].items = [Item("Search people", "field", value="Pri", search=True), Item("Priya Raghavan", "row")]

    env.on[("a", "set Search people=Pri")] = results
    env.on[("a", "click Priya Raghavan")] = lambda e: e.pages.__setitem__("a", page("Chat", Item("Priya Raghavan", "heading")))
    laya = FakeLaya()
    result = toolbox(env, laya).tool_find("Pri", "the direct message with Pria")
    assert result.ok and env.log == ["set Search people=Pri", "click Priya Raghavan"] and "name_match" not in laya.asked


def test_typing_never_goes_to_another_field_when_the_target_field_is_gone():
    env = FakeEnv({"a": page("Chat", Item("Message Priya", "field", focused=True))}, "a")
    tb = toolbox(env)
    result = tb.put(Item("Search people", "field", search=True), "Pria")
    assert result.outcome is Outcome.NO_FIELD and env.log == []
    assert not tb.return_into(Item("Search people", "field", search=True), "Pria") and env.log == []


def test_a_search_that_closed_is_reopened_for_the_next_spelling_never_typed_into_the_message_box():
    msg = Item("Message #general", "field")
    env = FakeEnv(
        {
            "home": page("Chat", Item("Search", BTN, search=True), msg),
            "search": page("Chat", Item("Search people", "field", focused=True, search=True), msg),
        },
        "home",
        {("home", "click Search"): "search"},
    )

    def closed(env):  # return closed the search: back home, the message box focused
        env.pages["search"].items = [Item("Search people", "field", focused=True, search=True), msg]
        env.pages["home"].items = [Item("Search", BTN, search=True), replace(msg, focused=True), Item("No matches", "text")]
        env.at = "home"

    def found(env):
        env.pages["search"].items = [
            Item("Search people", "field", value="Zedd", focused=True, search=True),
            Item("Zedd Ops", "row"),
            msg,
        ]

    env.on[("search", "press return")] = closed
    env.on[("search", "set Search people=Zedd")] = found
    env.on[("search", "click Zedd Ops")] = "home"
    result = toolbox(env).tool_find("Zed", "the chat with Zedd", ["Zedd"])
    assert result.ok, result
    assert not any(a.startswith("set Message") for a in env.log) and env.log.count("press return") == 1
    assert env.log.count("click Search") == 2  # re-opened for the second search


# ---------------------------------------------------------------- option elimination, risk tiers


def test_an_unsure_wrong_result_is_eliminated_and_laya_asked_again():
    env = FakeEnv({"a": page("Chat", Item("Search", "field", search=True), Item("Inbox", "heading"))}, "a")

    def results(env):  # the search lists a section header and the person
        env.pages["a"].items = [
            Item("Search", "field", value="Sofya", search=True),
            Item("Chats", "row"),
            Item("Sofia Marin", "row"),
        ]

    env.on[("a", "set Search=Sofya")] = results
    env.on[("a", "click Sofia Marin")] = lambda e: e.pages.__setitem__("a", page("Chat", Item("Sofia Marin", "heading")))

    def name_match(meaning, searched, items):
        names = {i.name for i in items}
        return (
            ch("D5 name match", ("Chats", 0.58), ("Sofia Marin", 0.3))
            if "Chats" in names
            else ch("D5 name match", ("Sofia Marin", 0.9))
        )

    laya = FakeLaya(
        name_match=name_match,
        pick_ok=lambda i, it: (
            ch("D4 pick check", (DOES_OTHER, 0.9)) if it.name == "Chats" else ch("D4 pick check", (DOES_IT, 0.9))
        ),
    )
    tb = toolbox(env, laya)
    result = tb.tool_find("Sofya", "the chat with Sofya")
    assert result.ok and env.log[-1] == "click Sofia Marin" and laya.asked.count("name_match") == 2
    assert "Chats" in tb.notes.tried("result", "Chat", "the chat with Sofya")


class RiskyLaya(FakeLaya):
    def acts(self, choice, risk="reversible"):
        return choice.sure if risk == "reversible" else choice.ranked[0][1] >= 0.99


def test_an_action_that_cannot_be_undone_needs_the_risky_verdict():
    env = FakeEnv({"a": page("Billing", Item("Send invoice", BTN), Item("Preview", BTN))}, "a")
    laya = RiskyLaya(target=lambda i, items: ch("D3 target", ("Send invoice", 0.95), ("Preview", 0.05)))
    result = toolbox(env, laya).tool_click("bill the client now")
    assert result.outcome is Outcome.UNSURE and env.log == []


def test_a_disabled_target_lists_the_enabled_options_beside_it():
    env = FakeEnv(
        {
            "a": page(
                "Shop",
                Item("Add to list", BTN, enabled=False, region="right"),
                Item("Blue", BTN, region="right"),
                Item("Green", BTN, region="right"),
                Item("Help", "link", region="bottom"),
            )
        },
        "a",
    )
    result = toolbox(env).tool_click("Add to list")
    assert result.outcome is Outcome.DISABLED and "['Blue', 'Green']" in result.detail


# ---------------------------------------------------------------- dialogs

SHEET = [
    Item("Schedule", "other", dialog=True),
    Item("Turn on until tomorrow", "checkbox", checked=False, dialog=True),
    Item("Done", BTN, dialog=True),
]
PROMO = [Item("Go Premium today", "heading", dialog=True), Item("Try free", BTN, dialog=True), Item("Not now", BTN, dialog=True)]
ASK = [  # the system's own prompt for another app: known by who puts it up (UserNotificationCenter), not its words
    Item("Maps would like to use your current location.", "text", dialog=True),
    Item("Allow Once", BTN, dialog=True),
    Item("Don't Allow", BTN, dialog=True),
]


class RoleLaya(FakeLaya):
    def __init__(self, role, **script):
        super().__init__(**script)
        self.role = role

    def dialog_role(self, intent, items):
        self.asked.append("dialog_role")
        return ch("D6 dialog role", (self.role, 0.9), ("other", 0.1))


def test_a_step_whose_target_is_inside_the_dialog_acts_inside_it():
    env = FakeEnv({"a": page("Settings", Item("Displays", "heading"), *SHEET)}, "a")
    env.on[("a", "click Turn on until tomorrow")] = lambda e: e.pages["a"].items.append(Item("On until tomorrow", "text"))
    laya = FakeLaya()
    tb = toolbox(env, laya)
    step = {"tool": "click", "target": "Turn On Until Tomorrow"}
    assert tb.clear_dialog(step) is None
    assert tb.run(step).ok and env.log == ["click Turn on until tomorrow"] and "dialog_answer" not in laya.asked


def test_an_unrelated_interruption_is_closed_by_rule_never_by_its_offer():
    env = FakeEnv(
        {"a": page("Music", Item("Home", "heading"), *PROMO), "b": page("Music", Item("Home", "heading"))},
        "a",
        {("a", "click Not now"): "b"},
    )

    def way_out(task, options):  # which closes it agreeing to nothing is Laya's (its words are never read by code)
        return ch("D6 dialog answer", (next(k for k, v in labels(options).items() if v.name == "Not now"), 0.9))

    laya = RoleLaya("an interruption", dialog_answer=way_out)
    result = toolbox(env, laya).clear_dialog({"tool": "find", "text": "song", "meaning": "the song"})
    assert result.ok and env.log == ["click Not now"] and "dialog_answer" in laya.asked and "dialog_role" in laya.asked
    # Laya's answer never agrees, pays or grants: a sure "Try free" is vetoed, never clicked
    env = FakeEnv({"a": page("Music", Item("Home", "heading"), *PROMO)}, "a")
    offer = RoleLaya("an interruption", dialog_answer=lambda t, o: ch("D6 dialog answer", ("Try free", 0.99)))
    toolbox(env, offer).clear_dialog({"tool": "find", "text": "song", "meaning": "the song"})
    assert "click Try free" not in env.log


def test_a_dialog_laya_calls_part_of_the_step_is_left_for_the_step():
    env = FakeEnv(
        {
            "a": page(
                "Shop",
                Item("Pick a size", "heading", dialog=True),
                Item("M", BTN, dialog=True),
                Item("Not now", BTN, dialog=True),
            )
        },
        "a",
    )
    result = toolbox(env, RoleLaya("part of the step")).clear_dialog({"tool": "click", "target": "a medium one"})
    assert result is None and env.log == []


def test_a_permission_prompt_always_takes_the_users_word_never_the_planned_answer():
    def env():
        return FakeEnv(
            {"a": page("UserNotificationCenter", *ASK), "b": page("Maps", Item("Directions", "heading"))},
            "a",
            {("a", "click Allow Once"): "b", ("a", "click Don't Allow"): "b"},
        )

    e1, asked = env(), []
    tb = toolbox(e1, confirm=lambda q: asked.append(q) or False, upcoming=[{"tool": "answer", "choice_meaning": "Allow once"}])
    assert tb.clear_dialog({"tool": "click", "target": "Directions"}).ok and e1.log == ["click Don't Allow"]  # the user said no
    assert len(asked) == 1

    e2 = env()
    tb = toolbox(e2, confirm=lambda q: asked.append(q) or False)
    assert tb.clear_dialog({"tool": "click", "target": "Directions"}).ok and e2.log == ["click Don't Allow"]
    assert len(asked) == 2 and asked[1].startswith("permission: UserNotificationCenter asks: Maps would like to use")

    e3 = env()
    assert toolbox(e3, confirm=lambda q: True).clear_dialog(None).ok and e3.log == ["click Allow Once"]


# ---------------------------------------------------------------- places: not set up, open tabs


def discord_facts():
    return replace(FACTS, installed_apps=[*FACTS.installed_apps, "Discord"])


def test_an_open_place_with_nothing_set_up_is_left_for_the_next_when_the_plan_reads_content():
    empty = page("Discord", Item("Welcome", "heading"), Item("Add an account", BTN), Item("No servers yet", "text"))
    site = page(
        "Brave Browser", Item("Servers", "heading"), Item("design-team", "row"), url="https://discord.com/app", profile="Work"
    )
    env = FakeEnv(
        {"finder": page("Finder"), "empty": empty, "site": site},
        "finder",
        {("finder", "open Discord"): "empty", ("empty", "url https://discord.com/app Brave Browser Work"): "site"},
    )
    empty_page = FakeLaya(outcome=lambda e, scr: ch("D7 outcome", (SHOWN if "nothing listed" in e else NOT_SHOWN, 0.9)))
    tb = Toolbox("message the design team on discord", env, empty_page, discord_facts(), None, lambda q: True)
    tb.upcoming = lambda: [{"tool": "find", "text": "design", "meaning": "the design-team server"}]
    result = tb.tool_open_place("Discord", "Servers", site="discord.com/app")
    assert result.ok and "discord.com" in result.detail and env.log[0] == "open Discord"
    assert "Discord app" in tb.notes.tried("place", "", "Discord")

    env2 = FakeEnv({"finder": page("Finder"), "empty": empty}, "finder", {("finder", "open Discord"): "empty"})
    tb2 = Toolbox("make my first discord server", env2, FakeLaya(), discord_facts(), None, lambda q: True)
    assert tb2.tool_open_place("Discord", "Welcome").ok  # creating something: an empty place is fine


def test_an_open_tab_of_the_site_is_switched_to_first():
    ready = page("Brave Browser", Item("Servers", "heading"), url="https://discord.com/channels/9", profile="Work")
    env = FakeEnv({"finder": page("Finder"), "ready": ready}, "finder", {("finder", "tab Brave Browser 2.4"): "ready"})
    tb = toolbox(env, task="post in discord")
    tb.tabs = [Tab("Brave Browser", 2, 4, "https://discord.com/channels/9", "", "Work")]
    result = tb.tool_open_place("Discord", "Servers", site="discord.com")
    assert result.ok and env.log == ["tab Brave Browser 2.4"] and "open tab" in result.detail


# ---------------------------------------------------------------- wait and re-check, fields, submit


def test_a_click_whose_effect_comes_late_is_waited_for_not_a_failure():
    env = FakeEnv({"a": page("Mail", Item("Compose", BTN)), "b": page("Mail", Item("To", "field"), Item("Send", BTN))}, "a")
    env.on[("a", "wait")] = "b"  # the new window shows a moment after the click
    result = toolbox(env).tool_click("Compose")
    assert result.ok and env.log == ["click Compose", "wait"]


def test_a_target_on_a_page_still_loading_is_re_read_with_backoff():
    env = FakeEnv(
        {
            "a": page("Docs", Item("Loading…", "text")),
            "b": page("Docs", Item("Blank document", BTN)),
            "c": page("Docs", Item("Untitled document", "heading")),
        },
        "a",
        {("a", "wait"): "b", ("b", "click Blank document"): "c"},
    )
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = toolbox(env, laya).tool_click("Blank document")
    assert result.ok and env.log == ["wait", "click Blank document"]


def test_the_real_envs_own_change_poll_is_used_when_it_has_one():
    env = FakeEnv({"a": page("App", Item("Go", BTN)), "b": page("App", Item("There", "text"))}, "a")
    polled = []

    def changed_since(before, timeout):
        polled.append(timeout)
        env.at = "b"
        return True

    env.changed_since = changed_since
    assert toolbox(env).tool_click("Go").ok and polled == [3.5] and "wait" not in env.log


def test_a_field_click_that_shows_nothing_new_is_fine_when_the_next_step_types():
    env = FakeEnv({"a": page("Shop", Item("Search products", "field", search=True), Item("Offers", "heading"))}, "a")
    tb = toolbox(env, upcoming=[{"tool": "write", "text": "rice", "where": "search products"}])
    assert tb.tool_click("Search products").ok
    assert toolbox(env).tool_click("Search products").outcome is Outcome.NO_CHANGE


def test_submit_never_presses_return_in_a_multi_line_field():
    body = Item("Message Body", "field", value="Thanks!", multiline=True)
    env = FakeEnv({"a": page("Mail", body, Item("Discard", BTN))}, "a")
    tb = toolbox(env, FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9))))  # Laya: no send control here
    tb.last_write, tb.pending = ("Message Body", "Thanks!"), {"Thanks!": "Message Body"}
    result = tb.tool_submit("Message sent")
    assert result.outcome is Outcome.NOT_FOUND and "press return" not in " ".join(env.log)


def test_text_still_in_the_field_after_submit_gets_the_visible_send_button():
    box = Item("Add a reply", "field", value="On it")
    env = FakeEnv(
        {
            "a": page("Tasks", box, Item("Add emoji", BTN)),
            "a2": page("Tasks", box, Item("Add emoji", BTN), Item("Comment", BTN)),  # the send shows once there is text
            "b": page("Tasks", Item("Add a reply", "field"), Item("You: On it", "text"), Item("Comment", BTN)),
        },
        "a",
        {("a", "click Add emoji"): "a2", ("a2", "click Comment"): "b"},
    )
    tb = toolbox(env)
    tb.last_write, tb.pending = ("Add a reply", "On it"), {"On it": "Add a reply"}
    result = tb.tool_submit("On it")
    assert result.ok and env.log[0] == "click Add emoji" and "click Comment" in env.log


def test_the_forms_default_button_sends_without_laya_and_whatever_its_words():
    box = Item("Add a reply", "field", value="On it")
    env = FakeEnv(
        {
            "a": page("Tasks", box, Item("Add emoji", BTN), Item("Kommentieren", BTN, default=True)),
            "b": page("Tasks", Item("Add a reply", "field"), Item("You: On it", "text"), Item("Kommentieren", BTN)),
        },
        "a",
        {("a", "click Kommentieren"): "b"},
    )
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", *[(it.name, 0.9 if it.name == "Add emoji" else 0.05) for it in items])
    )
    tb = toolbox(env, laya)
    tb.last_write, tb.pending = ("Add a reply", "On it"), {"On it": "Add a reply"}
    assert tb.tool_submit("On it").ok and env.log[0] == "click Kommentieren" and "target" not in laya.asked


def test_without_a_default_button_the_send_control_is_laya_s_pick_never_a_word_rule():
    box = Item("Add a reply", "field", value="On it")
    env = FakeEnv(
        {
            "a": page("Tasks", box, Item("Save draft", BTN), Item("Comment", BTN)),
            "b": page("Tasks", Item("Add a reply", "field"), Item("You: On it", "text"), Item("Comment", BTN)),
        },
        "a",
        {("a", "click Comment"): "b"},
    )
    laya = FakeLaya(  # ranked best first, as the real Laya ranks
        target=lambda i, items: ch(
            "D3 target", *sorted(((it.name, 0.9 if it.name == "Comment" else 0.05) for it in items), key=lambda r: -r[1])
        )
    )
    tb = toolbox(env, laya)
    tb.last_write, tb.pending = ("Add a reply", "On it"), {"On it": "Add a reply"}
    assert tb.tool_submit("On it").ok and env.log[0] == "click Comment" and "target" in laya.asked


def _form_with(button: str) -> FakeEnv:
    box = Item("Note", "field", value="On it")
    return FakeEnv(
        {
            "a": page("Tasks", box, Item(button, BTN)),
            "b": page("Tasks", Item("Note", "field"), Item("You: On it", "text"), Item(button, BTN)),
        },
        "a",
        {("a", f"click {button}"): "b"},
    )


def test_a_sure_pick_of_the_form_s_send_button_stands_with_no_pick_check():
    # the real model is sure which of a form's buttons submits it yet unsure in its pick check ("Send" 0.55, "Create"
    # 0.57): the submit's own check verifies the click, so the pick check would only veto a right pick
    env = _form_with("Create")
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", ("Create", 0.76), (NONE, 0.24), gate=0.4),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_IT, 0.55), (DOES_OTHER, 0.45), gate=0.6),
    )
    tb = toolbox(env, laya)
    tb.last_write, tb.pending = ("Note", "On it"), {"On it": "Note"}
    assert tb.tool_submit("On it").ok and env.log[0] == "click Create"
    assert "pick_ok" not in laya.asked and tb.decisions[0]["decision"] == "D3 target"


def test_an_unsure_pick_of_the_send_button_is_not_clicked_on_its_word():
    env = _form_with("Help")
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", ("Help", 0.52), (NONE, 0.48), gate=0.6),
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_OTHER, 0.57), (DOES_IT, 0.43), gate=0.5),
    )
    tb = toolbox(env, laya)
    tb.last_write, tb.pending = ("Note", "On it"), {"On it": "Note"}
    tb.tool_submit("On it")
    assert "click Help" not in env.log


def test_a_created_item_whose_title_stays_in_its_field_is_done():
    env = FakeEnv(
        {
            "a": page("Reminders", Item("Title", "field", value="Call Sam")),
            "b": page("Reminders", Item("Title", "field", value="Call Sam"), Item("Call Sam", "row")),
        },
        "a",
        {("a", "press return in Title"): "b"},
    )
    env.on[("a", "press return")] = "b"
    env.pages["a"].items[0] = replace(env.pages["a"].items[0], focused=True)
    tb = toolbox(env)
    tb.last_write, tb.pending = ("Title", "Call Sam"), {"Call Sam": "Title"}
    assert tb.tool_submit("Call Sam").ok and env.log.count("press return") == 1


# ---------------------------------------------------------------- reach a total


def shop_env(prices: dict[str, float]):
    counts = dict.fromkeys(prices, 0)

    def build(env):
        items = []
        for name, price in prices.items():
            label = f"₹{price:g}"
            if counts[name]:
                row = (name, label, str(counts[name]))
                items += [
                    Item(name, "text"),
                    Item(label, "text"),
                    Item("-", BTN, row=row),
                    Item(str(counts[name]), "text"),
                    Item("+", BTN, row=row),
                ]
            else:
                items += [Item(name, "text"), Item(label, "text"), Item("ADD", BTN, row=(name, label))]
        n = sum(counts.values())
        if n:
            items.append(Item(f"{n} items ₹{sum(prices[k] * c for k, c in counts.items()):g}", "text"))
        env.pages["a"].items = items

    env = FakeEnv({"a": page("Shop")}, "a")
    env.click = lambda item: (
        env.log.append(f"click {item.name} {'/'.join(item.row[:1])}"),
        {"ADD": add_of, "+": add_of, "-": remove_of}[item.name](item),
    )

    def add_of(item):
        counts[item.row[0]] += 1
        build(env)

    def remove_of(item):
        counts[item.row[0]] -= 1
        build(env)

    build(env)
    return env, counts


def stepper(intent, items):  # Laya, made up: "+" adds one more, "-" removes one
    want = "+" if intent.startswith("add") else "-"
    return ch("D3 target", *[(k, 0.9 if v.name == want else 0.01) for k, v in labels(items).items()], (NONE, 0.0))


def test_reach_adds_until_code_reads_the_total():
    env, counts = shop_env({"Oat bar 50g": 20, "Nut mix 200g": 50, "Trail mix 1kg": 110})
    result = toolbox(env, FakeLaya(target=stepper)).tool_reach("100 rs", "snacks")
    assert result.ok and "100" in result.detail and counts == {"Oat bar 50g": 0, "Nut mix 200g": 2, "Trail mix 1kg": 0}


def test_reach_takes_a_smaller_item_for_what_is_left_and_says_when_it_cannot():
    env, counts = shop_env({"Oat bar 50g": 20, "Nut mix 200g": 50})
    assert toolbox(env, FakeLaya(target=stepper)).tool_reach("₹90", "").ok and counts == {"Oat bar 50g": 2, "Nut mix 200g": 1}
    env, _ = shop_env({"Nut mix 200g": 50})
    result = toolbox(env).tool_reach("$30", "")
    assert result.outcome is Outcome.NOT_FOUND


# ---------------------------------------------------------------- the local ladder, results, places


def test_a_target_below_the_fold_is_scrolled_into_view():
    env = FakeEnv(
        {
            "a": page("Settings", Item("General", "row"), Item("Sound", "row")),
            "b": page("Settings", Item("Sound", "row"), Item("Night mode", BTN)),
            "c": page("Settings", Item("Night mode on", "text")),
        },
        "a",
        {("a", "scroll down"): "b", ("b", "click Night mode"): "c"},
    )
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = toolbox(env, laya).tool_click("Night mode")
    assert result.ok and env.log[-2:] == ["scroll down", "click Night mode"]


def test_the_click_tool_never_types_a_controls_name_into_the_pages_search_box():
    env = FakeEnv(
        {
            "a": page("Settings", Item("Search", "field", search=True), Item("General", "row")),
            "b": page("Settings", Item("Displays", "heading"), Item("Night mode", BTN)),
        },
        "a",
    )

    def results(env):
        env.pages["a"].items = [
            Item("Search", "field", value="Night mode", search=True),
            Item("Night mode", "row"),
            Item("General", "row"),
        ]

    env.on[("a", "set Search=Night mode")] = results
    env.on[("a", "click Night mode")] = "b"
    env.on[("b", "click Night mode")] = lambda e: e.pages["b"].items.append(Item("Scheduled", "text"))
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = toolbox(env, laya).tool_click("Night mode")
    assert not result.ok and not any(a.startswith(("set Search", "type")) for a in env.log)  # a search is find's


def test_fill_opens_the_control_that_holds_the_value_or_reveals_the_field():
    env = FakeEnv({"a": page("Settings", Item("Schedule", BTN, value="Off"), Item("Done", BTN))}, "a")
    env.on[("a", "click Schedule")] = lambda e: e.pages["a"].items.extend(
        [Item("Custom", "menu"), Item("Sunset to Sunrise", "other")]
    )
    env.on[("a", "click Sunset to Sunrise")] = lambda e: e.pages.__setitem__(
        "a", page("Settings", Item("Schedule", BTN, value="Sunset to Sunrise"))
    )
    assert toolbox(env).tool_fill({"Schedule": "Sunset to Sunrise"}).ok and env.log == [
        "click Schedule",
        "click Sunset to Sunrise",
    ]

    env = FakeEnv(
        {"a": page("Tasks", Item("Title", "field", value="Pay rent"), Item("Remind me at a time", "checkbox", checked=False))},
        "a",
    )
    env.on[("a", "click Remind me at a time")] = lambda e: e.pages["a"].items.append(Item("Time", "field"))
    laya = FakeLaya(target=lambda i, items: ch("D3 target", ("Remind me at a time", 0.9)))
    result = toolbox(env, laya).tool_fill({"Time": "09:00"})
    assert result.ok and env.log == ["click Remind me at a time", "set Time=09:00"]


def test_results_already_listed_count_after_return_whatever_their_kind():
    songs = [Item("Midnight City - M83", "text"), Item("Midnight Rain", "text")]
    env = FakeEnv({"a": page("Music", Item("What do you want to play?", "field", focused=True))}, "a")

    def listed(env):
        env.pages["a"].items = [Item("What do you want to play?", "field", value="Midnight City", focused=True), *songs]

    env.on[("a", "set What do you want to play?=Midnight City")] = listed
    env.on[("a", "click Midnight City - M83")] = lambda e: e.pages["a"].items.append(Item("Now playing", "text"))
    tb = toolbox(env)
    tb.boxes["Music"] = "What do you want to play?"  # found here before, whatever it is called
    result = tb.tool_find("Midnight City", "the song Midnight City")
    assert result.ok and env.log[-1] == "click Midnight City - M83"
    before = env.screen()
    assert tb.results(before, before, "Midnight", Item("What do you want to play?", "field")) == songs


def test_a_signed_in_place_that_looked_empty_beats_login_pages_everywhere_else():
    empty = page(
        "Brave Browser", Item("Inbox", "heading"), Item("No notifications", "text"), url="https://chat.acme.com/", profile="Work"
    )
    login = page(
        "Brave Browser",
        Item("Log in to Acme", "heading"),
        Item("Continue with email", BTN),
        Item("Password", "field"),
        url="https://chat.acme.com/",
        profile="Personal",
    )
    env = FakeEnv(
        {"finder": page("Finder"), "empty": empty, "login": login},
        "finder",
        {
            ("finder", "url https://chat.acme.com/ Brave Browser Work"): "empty",
            ("empty", "url https://chat.acme.com/ Brave Browser Personal"): "login",
            ("login", "url https://chat.acme.com/ Brave Browser Work"): "empty",
        },
    )
    tb = toolbox(env, task="find the login bug in acme chat", upcoming=[{"tool": "find", "text": "login", "meaning": "the bug"}])
    result = tb.tool_open_place("Acme chat", "Inbox", site="chat.acme.com")
    assert result.ok and "Work" in result.detail and env.at == "empty"


def test_laya_seeing_a_login_page_the_page_does_not_show_takes_its_next_answer():
    from karta.harness.decide import LOGIN

    env = FakeEnv(
        {"finder": page("Finder"), "t": page("Terminal", Item("Last login: Tue on ttys001", "text"), Item("me@mac ~ %", "text"))},
        "finder",
        {("finder", "open Terminal"): "t"},
    )
    laya = FakeLaya(page_state=lambda w, s: ch("D2 page state", (LOGIN, 0.6), (READY, 0.3)))
    assert toolbox(env, laya, task="open terminal").tool_open_place("Terminal", "").ok


def test_click_near_picks_the_repeated_control_in_that_items_row():
    env = FakeEnv(
        {
            "a": page(
                "Music",
                Item("Midnight Rain", "text"),
                Item("Play", BTN, row=("Midnight Rain",)),
                Item("Midnight City", "text"),
                Item("Play", BTN, row=("Midnight City",)),
            )
        },
        "a",
    )
    env.click = lambda item: (
        env.log.append(f"click {item.name} {item.row}") or env.pages["a"].items.append(Item("Now playing", "text"))
    )
    assert toolbox(env).tool_click("Play", near="Midnight City").ok and env.log == ["click Play ('Midnight City',)"]


def test_a_sidebar_left_after_a_search_is_not_a_result_and_an_empty_app_says_so():
    env = FakeEnv(
        {
            "a": page(
                "Mail",
                Item("Search", "field", search=True),
                Item("Inbox", "link"),
                Item("Sent", "link"),
                Item("No Messages", "text"),
            )
        },
        "a",
    )

    def narrowed(env):  # the search hid a mailbox; nothing matches
        env.pages["a"].items = [
            Item("Search", "field", value=env.pages["a"].items[0].value, search=True),
            Item("Inbox", "link"),
            Item("No Messages", "text"),
        ]

    for q in ("Arjun", "Arj"):
        env.on[("a", f"set Search={q}")] = narrowed

    def empty(expected, screen):  # Laya reads the page: nothing listed yet
        return ch("D7 outcome", (SHOWN if "nothing listed" in expected else NOT_SHOWN, 0.9))

    laya = FakeLaya(name_match=lambda m, s, items: ch("D5 name match", ("Inbox", 0.9)), outcome=empty)
    result = toolbox(env, laya).tool_find("Arjun", "the latest email from Arjun", ["Arj"])
    assert (
        result.outcome is Outcome.NOT_SET_UP and not any(a == "click Inbox" for a in env.log) and "name_match" not in laya.asked
    )


def test_a_focused_body_with_text_is_never_taken_as_the_search_box():
    from types import SimpleNamespace

    from karta.harness.tools import Toolbox
    from karta.harness.types import Item, Screen

    body = Item("note body text view", "field", value="Groceries: milk", focused=True)
    empty = Item("", "field", focused=True)
    fake = SimpleNamespace(page_fields=lambda scr: [i for i in scr.items if i.kind == "field"])
    assert Toolbox.focused_search(fake, Screen("Notes", [body])) is None
    assert Toolbox.focused_search(fake, Screen("Notes", [empty])) is empty
    named = Item("Search notes", "field", value="old query", focused=True, search=True)
    assert Toolbox.focused_search(fake, Screen("Notes", [named])) is named


def test_a_menu_command_is_clicked_only_when_the_step_names_it_in_full():
    from karta.harness.tools import names_command

    assert names_command("New Private Window", "File > New Private Window")
    assert names_command("the File menu's New Private Window", "File > New Private Window")
    assert not names_command("Add", "Profiles > Add Profile…")
    assert not names_command("ADD button on first product card", "Profiles > Add Profile…")


def test_the_browsers_own_dialog_is_the_users_never_answered_for_the_task():
    own = [
        Item("Rename your profile", "field", dialog=True, browser_ui=True),
        Item("Done", BTN, dialog=True, browser_ui=True),
        Item("Cancel", BTN, dialog=True, browser_ui=True, cancels=True),
    ]
    shop = page("Brave Browser", Item("ADD", BTN), *own, url="https://shop.example.com/")
    env = FakeEnv(
        {"a": shop, "b": page("Brave Browser", Item("ADD", BTN), url="https://shop.example.com/")},
        "a",
        {("a", "click Cancel"): "b"},
    )
    tb = toolbox(env, task="add chips to the cart", confirm=lambda q: False)
    tb.front = "Brave Browser"
    assert "Rename your profile" not in [i.name for i in shop.page()]  # never a target for the page tools
    result = tb.clear_dialog({"tool": "click", "target": "ADD"})
    assert result.ok and env.log == ["click Cancel"] and tb.confirmations  # the user said no: its own cancel
    stuck = page("Brave Browser", Item("Turn on sync", BTN, dialog=True, browser_ui=True), url="https://shop.example.com/")
    env2 = FakeEnv({"a": stuck}, "a")
    tb2 = toolbox(env2, task="add chips to the cart")
    stuck_result = tb2.clear_dialog(None)  # it may ask in words Karta cannot read, so the user decides
    assert stuck_result.outcome is Outcome.NEEDS_PERMISSION and env2.log == [] and tb2.confirmations


def test_near_in_a_grid_of_cards_takes_the_control_clearly_nearest_to_the_item():
    from types import SimpleNamespace as At

    def at(x, y):
        return At(centre=(x, y))

    items = [
        Item("Pepsi Soft Drink Refreshing 300 ml", "text", ref=at(100, 100)),
        Item("ADD", BTN, ref=at(100, 160)),
        Item("Sprite Lemon 250 ml", "text", ref=at(400, 100)),
        Item("ADD", BTN, ref=at(400, 160)),
    ]
    env = FakeEnv({"a": page("Brave Browser", *items)}, "a")
    tb = toolbox(env)
    item, _ = tb.find_target("ADD", env.screen(), near="Sprite 250 ml")
    assert item is not None and item.ref.centre == (400, 160)
    item, _ = tb.find_target("ADD", env.screen(), near="Pepsi Soft Drink 300 ml")
    assert item is not None and item.ref.centre == (100, 160)


def test_a_message_box_holding_the_text_twice_is_never_sent():
    box = Item("Type a message", "field", value="hello therehello there", focused=True)
    env = FakeEnv({"a": page("Chat", box, Item("Send", BTN))}, "a")
    tb = toolbox(env, task="send hello there")
    tb.last_write = ("Type a message", "hello there")
    tb.pending["hello there"] = "Type a message"
    result = tb.tool_submit("hello there")
    assert not result.ok and "holds more than the message" in result.detail and env.log == []


def test_a_list_row_read_as_its_title_and_preview_is_clicked_by_its_title():
    row = Item("Hemant Rao Studio see you at five", "text")
    env = FakeEnv({"a": page("Chat", Item("Hemant Rao", "heading"), row, Item("Hemant Kumar", "text"))}, "a")
    item, why = toolbox(env).find_target("Hemant Rao Studio", env.screen())
    assert item is row and "titled" in why.detail


def test_a_message_typed_at_the_focus_still_asks_before_it_is_sent():
    box = Item("Type a message to Sam", "field", focused=True)
    chat = page("Chat", box)

    class Typing(FakeEnv):
        def type_at_focus(self, text):
            self.log.append(f"type {text}")
            self.pages["a"].items[0] = replace(box, value=text)

    env, asked = Typing({"a": chat}, "a"), []
    tb = toolbox(env, task="tell Sam hi", confirm=lambda q: asked.append(q) or False)
    assert tb.tool_type("hi there").ok
    result = tb.tool_submit("hi there")
    assert not result.ok and asked and asked[0].startswith("send") and "press return" not in " ".join(env.log)


# ---------------------------------------------------------------- facts by structure, in any language


def test_money_is_read_by_format_in_any_locale():
    from karta.harness.tools import amount_of, money

    assert money("12,99 €") == [12.99] and money("1.234,50 €") == [1234.5] and money("¥1,200") == [1200.0]
    assert money("$4.50") == [4.5] and money("Gesamt: 23,40 €") == [23.4] and money("EUR 30.00") == [30.0]
    assert money("TOP 10") == [] and money("USB 3.0 cable") == [] and money("30 rupees") == []  # no word is money
    assert amount_of("₹120") == 120.0 and amount_of("reach 40") == 40.0


def test_a_calendars_own_labels_are_dates_in_the_platforms_words():
    from karta.harness.tools import bare_date

    assert bare_date("28 October 2026 Wednesday") and bare_date("8 AM") and bare_date("M 28 5")
    assert bare_date("Mittwoch, 28. Oktober", "de") and bare_date("miércoles, 28 de octubre", "es")
    assert not bare_date("Call Mira about the lamp") and not bare_date("Zero Dark Thirty", "de")


def test_one_count_line_is_the_lists_count_several_are_its_rows():
    from karta.harness.tools import collect_candidates

    summary = Screen("Tasks", [Item("3 Aufgaben", "text"), Item("Water the ferns today", "row"), Item("Pay the rent", "row")])
    assert [i.name for i in collect_candidates(summary, "my tasks")] == ["Water the ferns today", "Pay the rent"]
    shopping = Screen("Lists", [Item("2 lemons", "row"), Item("3 apples", "row"), Item("6 eggs", "row")])
    assert [i.name for i in collect_candidates(shopping, "the shopping list")] == ["2 lemons", "3 apples", "6 eggs"]


def test_a_token_is_in_the_fields_own_container_a_suggestion_popup_is_not():
    from karta.harness.facts import in_field_box
    from karta.harness.mac import Ref

    box = Item("To", "field", ref=Ref(0, 0, 100, 20, path=(1, 2, 3, 4, 5)))
    token = Item("Mira Okafor", "button", ref=Ref(0, 0, 50, 20, path=(1, 2, 3, 4, 9)))
    popup = Item("Mira Okafor <mira@example.com>", "row", ref=Ref(0, 30, 200, 20, path=(1, 2, 7, 8)))
    assert in_field_box(token, box) and not in_field_box(popup, box)


def test_find_opens_search_with_the_apps_find_command_never_a_key_pressed_blind():
    env = FakeEnv(
        {
            "a": page("Notely", Item("Groceries", "row"), Item("Bearbeiten > Suchen …", "menu", menu=True, shortcut="cmd+f")),
            "b": page("Notely", Item("Suchen", "field", focused=True, search=True), Item("Groceries", "row")),
        },
        "a",
        {("a", "click Bearbeiten > Suchen …"): "b"},
    )
    tb = toolbox(env)
    box, _why = tb.search_box()
    assert box is not None and box.name == "Suchen"
    assert env.log == ["click Bearbeiten > Suchen …"] and not any(a.startswith("press") for a in env.log)


def test_the_send_button_is_the_one_in_the_fields_own_form():
    from karta.harness.mac import Ref
    from karta.harness.tools import form_mates

    box = Item("Antworten", "field", ref=Ref(0, 0, 1, 1, path=(1, 2, 3, 4)))
    mine = Item("Senden", "button", ref=Ref(0, 0, 1, 1, path=(1, 2, 3, 5)))
    page_verb = Item("Erstellen", "button", ref=Ref(0, 0, 1, 1, path=(1, 2, 9)))
    assert form_mates([mine, page_verb], box) == [mine]
    assert form_mates([page_verb, Item("Teilen", "button", ref=Ref(0, 0, 1, 1, path=(1, 2, 8)))], box) is None  # the page only
