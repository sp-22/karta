"""Places by evidence: the ranking, moving only on proof (a login page, a page that failed to load), settling in place
otherwise (a wait, then the site's root in the same tab), the move cap, a rescue that cannot move, a locked Mac, the
HUD started when it is not running, and a commit-like control Laya only guessed. Made-up sites and apps only."""

from __future__ import annotations

import os
import socket
from datetime import datetime

from test_harness_tools import BTN, FakeBrain, FakeEnv, FakeLaya, ch, page

from karta.harness import facts as facts_mod
from karta.harness import risk
from karta.harness.decide import DOES_IT, ELSEWHERE, LOGIN, NONE, READY
from karta.harness.facts import Tab, is_login_page, places_for, rank_by_evidence, site_place
from karta.harness.loop import run_task
from karta.harness.mac import screen_locked
from karta.harness.tools import Toolbox
from karta.harness.types import Choice, Item, MacFacts, Outcome, Screen

FACTS = MacFacts(
    ["Brave Browser", "Google Chrome", "Safari", "Finder"],
    {"Brave Browser": ["Work", "Personal"], "Google Chrome": ["Person 1"], "Safari": []},
    [],
    datetime(2026, 10, 6, 9, 0),
)
SITE = "board.quillo.app"
ROOT = f"https://{SITE}/"
TAB = Tab("Brave Browser", 1, 3, f"https://{SITE}/b/7/card/2/edit", "", None)


def names(places):
    return [p.name for p in places]


def toolbox(env, laya=None, task="move the card on quillo", tabs=(TAB,), default="Google Chrome") -> Toolbox:
    tb = Toolbox(task, env, laya or FakeLaya(), FACTS, None, lambda q: True)
    tb.tabs, tb.default_browser, tb.upcoming = list(tabs), default, lambda: []
    return tb


def board(profile=None, url=ROOT):
    return page("Brave Browser", Item("Boards", "heading"), Item("Roadmap", "row"), url=url, profile=profile)


def login(app="Brave Browser", profile=None):
    return page(app, Item("Log in to Quillo", "heading"), Item("Password", "field", secret=True), url=ROOT, profile=profile)


LEFTOVER = page(  # the right site, a leftover full-page editor: no ready word, no login, no error
    "Brave Browser", Item("Card title", "field"), Item("Discard", BTN), url=TAB.url
)
SOMETHING_ELSE = FakeLaya(page_state=lambda w, s: ch("D2 page state", (ELSEWHERE, 0.34), gate=0.3))


# ---------------------------------------------------------------- the ranking (pure)


def site_places():
    return [site_place(ROOT, b, p) for b, ps in FACTS.browsers.items() for p in ps or [None]]


def test_the_ranking_puts_an_open_tab_then_its_browser_then_the_default_then_the_rest():
    tab = facts_mod.tab_place(Tab("Google Chrome", 1, 2, f"https://{SITE}/b/1", "", None), ROOT)
    ranked = rank_by_evidence([*site_places(), tab], default="Brave Browser")
    assert names(ranked) == [
        tab.name,
        f"{SITE} in Google Chrome, profile Person 1",  # the other profiles of the browser with a tab on the site
        f"{SITE} in Brave Browser, profile Work",  # the default browser
        f"{SITE} in Brave Browser, profile Personal",
        f"{SITE} in Safari",
    ]


def test_memory_comes_after_a_tab_and_the_browser_in_use_before_the_default():
    remembered = f"{SITE} in Safari"
    ranked = rank_by_evidence(site_places(), remembered=remembered, using="Google Chrome", default="Brave Browser")
    assert names(ranked) == [
        remembered,
        f"{SITE} in Google Chrome, profile Person 1",  # the browser in front when the task started
        f"{SITE} in Brave Browser, profile Work",
        f"{SITE} in Brave Browser, profile Personal",
    ]
    signed = rank_by_evidence(site_places(), signed_in=[f"{SITE} in Brave Browser, profile Personal"], default="Safari")
    assert names(signed)[:2] == [f"{SITE} in Brave Browser, profile Personal", f"{SITE} in Safari"]


def test_an_app_comes_before_browser_profiles_and_the_given_order_holds_within_a_group():
    app = facts_mod.app_place("Quillo")
    ranked = rank_by_evidence([*site_places(), app], default="Safari")
    assert names(ranked)[:2] == ["Quillo app", f"{SITE} in Safari"]
    assert names(ranked)[2:4] == [f"{SITE} in Brave Browser, profile Work", f"{SITE} in Brave Browser, profile Personal"]


def test_a_browser_the_brain_writes_into_what_does_not_narrow_the_places_the_users_words_do():
    by_brain = places_for("Google Chrome", "look up the roadmap on quillo", FACTS, site=SITE)
    assert {p.app for p in by_brain} == {"Brave Browser", "Google Chrome", "Safari"}
    by_user = places_for("Quillo", "look up the roadmap on quillo in chrome", FACTS, site=SITE)
    assert {p.app for p in by_user} == {"Google Chrome"}


def test_open_place_tries_the_browser_in_use_first():
    env = FakeEnv({"finder": page("Finder"), "board": board("Person 1")}, "finder")
    env.on[("finder", f"url {ROOT} Google Chrome Person 1")] = "board"
    env.pages["board"].app = "Google Chrome"
    tb = toolbox(env, tabs=(), default="Brave Browser")
    tb.using = "Google Chrome"
    assert tb.tool_open_place("Quillo", "Boards", site=SITE).ok
    assert env.log == [f"url {ROOT} Google Chrome Person 1"] and tb.moves == 0


# ---------------------------------------------------------------- settle in place: never move without proof


def test_a_page_on_the_site_that_loaded_is_ready_by_code_and_laya_is_never_asked():
    laya = SOMETHING_ELSE  # it would say "something else": it is not asked
    laya.asked.clear()
    env = FakeEnv({"finder": page("Finder"), "leftover": LEFTOVER}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "leftover"
    tb = toolbox(env, laya)
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert result.ok and "loaded, no login page, nothing in the way" in result.detail
    assert env.log == ["tab Brave Browser 1.3"] and tb.moves == 0 and "page_state" not in laya.asked


def busy(page_: Screen) -> Screen:
    return Screen(page_.app, page_.items, page_.url, page_.window, page_.profile, busy=True)


def test_a_page_still_loading_is_reloaded_at_its_root_in_the_same_tab_never_left():
    env = FakeEnv({"finder": page("Finder"), "loading": busy(LEFTOVER), "board": board()}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "loading"
    env.on[("loading", "press return")] = "board"
    tb = toolbox(env)
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert result.ok and "settled in place" in result.detail
    assert env.log[0] == "tab Brave Browser 1.3" and env.log[-3:] == ["press cmd+l", f"type {ROOT}", "press return"]
    assert tb.moves == 0 and not any(a.startswith("url ") for a in env.log)


def test_still_not_ready_after_the_reload_is_reported_and_the_place_is_kept():
    env = FakeEnv({"finder": page("Finder"), "loading": busy(LEFTOVER)}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "loading"
    tb = toolbox(env)
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert not result.ok and "still not ready" in result.detail
    assert not any(a.startswith(("url ", "open ")) for a in env.log) and tb.moves == 0
    assert tb.places["Quillo"].tab == (1, 3) and tb.last_place is tb.places["Quillo"]  # locked: a rescue works here


def test_an_unsure_page_state_never_moves_the_place():
    # code cannot tell (the window's profile is not the one opened), so Laya is asked; its unsure "something else"
    # only settles in place: a wait and the root address in the same tab, never another browser or profile
    unsure = FakeLaya(page_state=lambda w, s: ch("D2 page state", (ELSEWHERE, 0.2), gate=0.3))
    other = page("Google Chrome", Item("Card", "text"), url=ROOT, profile="Guest")
    env = FakeEnv({"finder": page("Finder"), "other": other}, "finder")
    env.on[("finder", f"url {ROOT} Google Chrome Person 1")] = "other"
    tb = toolbox(env, unsure, tabs=())
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert not result.ok and "page_state" in unsure.asked and tb.moves == 0
    assert [a for a in env.log if a.startswith("url ")] == [f"url {ROOT} Google Chrome Person 1"]
    sure = FakeLaya(page_state=lambda w, s: ch("D2 page state", (ELSEWHERE, 0.95)))
    env = FakeEnv({"finder": page("Finder"), "other": other}, "finder")
    env.on[("finder", f"url {ROOT} Google Chrome Person 1")] = "other"
    tb = toolbox(env, sure, tabs=())
    assert not tb.tool_open_place("Quillo", "Boards", site=SITE).ok and tb.moves == 0
    assert [a for a in env.log if a.startswith("url ")] == [f"url {ROOT} Google Chrome Person 1"]


# ---------------------------------------------------------------- move on proof, with one reason line


def test_a_login_page_moves_to_the_next_place_by_rank_with_its_reason(monkeypatch):
    lines = []
    from karta.core import events

    monkeypatch.setattr(events, "emit", lambda kind, **data: lines.append((kind, data.get("text"))))
    env = FakeEnv({"finder": page("Finder"), "login": login(), "board": board("Work")}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "login"
    env.on[("login", f"url {ROOT} Brave Browser Work")] = "board"
    tb = toolbox(env)
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert result.ok and "profile Work" in result.detail
    # the tab's own browser, its other profile next; the default browser (Chrome) never reached
    assert env.log == ["tab Brave Browser 1.3", f"url {ROOT} Brave Browser Work"]
    assert ("move", "moved to Brave Browser Work: login page in Brave Browser") in lines
    assert "  moved to Brave Browser Work: login page in Brave Browser" in tb.trace and tb.moves == 1


def test_a_page_that_failed_to_load_is_proof_too_by_its_address():
    # the browser's own error page is no address on the site (its words are never read)
    error = page("Brave Browser", Item("Ce site est inaccessible", "heading"), url="chrome-error://chromewebdata/")
    env = FakeEnv({"finder": page("Finder"), "error": error, "board": board("Work")}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "error"
    env.on[("error", f"url {ROOT} Brave Browser Work")] = "board"
    tb = toolbox(env)
    assert tb.tool_open_place("Quillo", "Boards", site=SITE).ok
    assert tb.moves == 1


def test_two_moves_then_the_user_decides_not_the_brain():
    env = FakeEnv(
        {"finder": page("Finder"), "l1": login(), "l2": login(profile="Work"), "l3": login(profile="Personal")}, "finder"
    )
    env.on[("finder", "tab Brave Browser 1.3")] = "l1"
    env.on[("l1", f"url {ROOT} Brave Browser Work")] = "l2"
    env.on[("l2", f"url {ROOT} Brave Browser Personal")] = "l3"
    plan = {"steps": [{"tool": "open_place", "what": "Quillo", "ready_shows": "Boards", "site": SITE}], "done_shows": "Roadmap"}
    brain, asked = FakeBrain(plan), []
    result = run_task(
        "open the roadmap on quillo", env, FakeLaya(), brain, FACTS, tabs=[TAB], ask=lambda q: asked.append(q) or ""
    )
    assert not result.done and brain.calls == 1 and result.rescues == 0  # no rescue: the user is asked
    assert len([a for a in env.log if a.startswith(("url ", "tab "))]) == 3  # the tab and two moves; Chrome never
    assert asked and "moved 2 times" in asked[0] and result.question == asked[0]


# ---------------------------------------------------------------- a rescue never moves


def test_a_rescue_cannot_move_to_another_browser_or_profile_once_a_place_is_locked():
    env = FakeEnv({"finder": page("Finder"), "board": board("Work")}, "finder")
    env.on[("finder", f"url {ROOT} Brave Browser Work")] = "board"
    tb = toolbox(env, tabs=())
    locked = site_place(ROOT, "Brave Browser", "Work")
    tb.places["Quillo"] = tb.last_place = locked
    tb.from_rescue = True
    other = tb.tool_open_place("Google Chrome", site="docs.quillo.app")  # another site, the brain naming Chrome
    assert all("Google Chrome" not in a and "Safari" not in a for a in env.log)
    assert [a for a in env.log if a.startswith(("url ", "open ", "tab "))] == ["url https://docs.quillo.app/ Brave Browser Work"]
    assert not other.ok  # that page never loaded here: reported, not moved
    env.log.clear()
    refused = tb.tool_open_place("Safari")  # another browser as an app
    assert refused.outcome is Outcome.WRONG_PLACE and "never moves" in refused.detail and env.log == []
    tb.from_rescue = False  # a planned step is not a rescue's: the ladder is open to it
    tb.tool_open_place("Safari")
    assert env.log[0] == "open Safari"


def test_a_rescue_in_the_loop_is_kept_to_the_locked_tab():
    env = FakeEnv({"finder": page("Finder"), "leftover": LEFTOVER}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "leftover"
    plan = {"steps": [{"tool": "open_place", "what": "Quillo", "ready_shows": "Boards", "site": SITE}], "done_shows": "Roadmap"}
    rescue = {"steps": [{"tool": "open_place", "what": "Google Chrome", "ready_shows": "Boards", "site": SITE}]}
    result = run_task("open the roadmap on quillo", env, SOMETHING_ELSE, FakeBrain(plan, rescue), FACTS, tabs=[TAB])
    assert not result.done and result.rescues >= 1
    assert not any(a.startswith(("url ", "open ")) for a in env.log), env.log  # never Chrome, never another profile


# ---------------------------------------------------------------- what the page proves


def test_a_sign_in_page_read_only_off_the_pixels_is_no_fact_for_code():
    ocr = [Item("Sign in", "text", source="ocr"), Item("Email or phone", "text", source="ocr"), Item("Next", "text")]
    assert not is_login_page(Screen("Brave Browser", ocr))  # wording is meaning: Laya's sure page state moves it


# ---------------------------------------------------------------- the Mac is locked


class LockedEnv(FakeEnv):
    def __init__(self, *args, locked_after: int = 0, **kwargs):
        super().__init__(*args, **kwargs)
        self.locked_after = locked_after

    def locked(self) -> bool:
        return len(self.log) >= self.locked_after


def test_a_locked_mac_stops_the_task_at_once_with_no_plan():
    env = LockedEnv({"a": page("Finder")}, "a")
    brain = FakeBrain({"steps": [{"tool": "open_place", "what": "Quillo"}], "done_shows": "Roadmap"})
    result = run_task("open quillo", env, FakeLaya(), brain, FACTS)
    assert not result.done and "the Mac is locked" in result.reason and brain.calls == 0 and env.log == []
    lock_screen = FakeEnv({"a": page("loginwindow", Item("Enter Password", "field"))}, "a")
    result = run_task("open quillo", lock_screen, FakeLaya(), FakeBrain(), FACTS)
    assert "the Mac is locked" in result.reason and lock_screen.log == []


def test_a_mac_that_locks_mid_task_stops_before_the_next_step():
    env = LockedEnv({"a": page("Finder", Item("Roadmap", BTN)), "b": page("Finder", Item("Done", "text"))}, "a", locked_after=1)
    env.on[("a", "click Roadmap")] = "b"
    plan = {"steps": [{"tool": "click", "target": "Roadmap"}, {"tool": "click", "target": "Roadmap"}], "done_shows": "Nope"}
    result = run_task("open the roadmap", env, FakeLaya(), FakeBrain(plan), FACTS)
    assert "the Mac is locked" in result.reason and env.log == ["click Roadmap"] and result.rescues == 0


def test_screen_locked_reads_the_session_flag_or_the_lock_screen_in_front():
    assert screen_locked(lambda: {"CGSSessionScreenIsLocked": True}, lambda: "Finder")
    assert screen_locked(lambda: {}, lambda: "loginwindow")
    assert not screen_locked(lambda: {"CGSSessionScreenIsLocked": False}, lambda: "Finder")
    assert not screen_locked(lambda: (_ for _ in ()).throw(RuntimeError()), lambda: "Finder")


# ---------------------------------------------------------------- the HUD starts when it is not running


def test_the_hud_is_started_only_when_none_is_running():
    from karta import app

    launched = []

    def popen(args, **kwargs):
        launched.append((args, kwargs["env"]["KARTA_PARENT_PID"]))
        return "proc"

    assert app.start_hud(running=lambda: True, popen=popen) is None and launched == []
    assert app.start_hud(running=lambda: False, popen=popen) == "proc"
    assert launched[0][0][-2:] == ["-m", "karta.hud"] and launched[0][1] == str(os.getpid())


def test_a_hud_is_running_when_its_port_is_taken():
    from karta import app

    taken = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    taken.bind(("127.0.0.1", 0))
    try:
        assert app.hud_running(taken.getsockname()[1])
    finally:
        port = taken.getsockname()[1]
        taken.close()
    assert not app.hud_running(port)


def test_karta_start_goes_through_the_same_hud_start_and_stops_it(monkeypatch):
    import signal

    from karta import app, setup_mac

    made = []

    class Fake:
        def __init__(self, hud=False, record=None):
            made.append(hud)
            self.closed = False

        def close(self):
            made.append("closed")

    monkeypatch.setattr(setup_mac, "setup_state", lambda: {"todo": []})
    monkeypatch.setattr(app, "Karta", Fake)
    monkeypatch.setattr(app, "voice_loop", lambda karta, act, budget: None)
    monkeypatch.setattr(signal, "signal", lambda *a: None)
    app.start()
    assert made == [True, "closed"]


# ---------------------------------------------------------------- a commit-like control Laya only guessed


class GatedLaya(FakeLaya):
    """FakeLaya with decide.Laya's policy: a reversible floor of 0.3, and a risky tier of 0.95."""

    def gate(self, decision: str) -> float:
        return 0.3

    def acts(self, choice: Choice, level: str = "reversible") -> bool:
        return choice.sure if level == "reversible" else bool(choice.ranked) and choice.ranked[0][1] >= 0.95


def guessed(top: str) -> GatedLaya:
    return GatedLaya(
        target=lambda i, items: ch("D3 target", (top, 0.30), ("Help", 0.23), (NONE, 0.1), gate=0.38),  # unsure
        pick_ok=lambda i, it: ch("D4 pick check", (DOES_IT, 0.58), gate=0.57),  # sure by a hair
    )


def test_a_commit_like_control_laya_only_guessed_is_never_clicked():
    items = [Item("Create account", "text"), Item("Next", "text"), Item("Help", "text")]
    env = FakeEnv({"a": page("Brave Browser", *items, url=ROOT)}, "a")
    result = Toolbox("draft a card", env, guessed("Next"), FACTS, None, lambda q: True).tool_click("Compose")
    assert result.outcome is Outcome.UNSURE and "commits a form" in result.detail and env.log == []
    assert risk.COMMITS.match("Next") and risk.COMMITS.match("Continue") and not risk.COMMITS.match("Archive")


def test_a_reversible_guess_still_goes_through_its_pick_check():
    env = FakeEnv({"a": page("Brave Browser", Item("Archive", "text"), Item("Help", "text"), url=ROOT)}, "a")
    Toolbox("archive the card", env, guessed("Archive"), FACTS, None, lambda q: True).tool_click("Put away")
    assert env.log[0] == "click Archive"  # not commit-like: the pick check's yes stands, verified by the step


def test_a_tab_showing_a_sign_in_page_read_off_the_pixels_is_left_not_worked_in():
    # the 'Next' case: the user's tab had signed out; its sign-in form was read only by OCR, and a click for the
    # step's control picked the form's Next. The login page is proof now: the tab is left before any click
    sign_in = page(
        "Brave Browser",
        Item("Sign in", "text", source="ocr"),
        Item("Email or phone", "text", source="ocr"),
        Item("Next", "text", source="ocr"),
        url=ROOT,
    )
    env = FakeEnv({"finder": page("Finder"), "sign_in": sign_in, "board": board("Work")}, "finder")
    env.on[("finder", "tab Brave Browser 1.3")] = "sign_in"
    env.on[("sign_in", f"url {ROOT} Brave Browser Work")] = "board"
    tb = toolbox(env)  # FakeLaya's page state says "ready": code's proof comes first
    assert tb.tool_open_place("Quillo", "Boards", site=SITE).ok
    moves = [a for a in env.log if a.startswith(("tab ", "url "))]
    assert moves == ["tab Brave Browser 1.3", f"url {ROOT} Brave Browser Work"] and not any("Next" in a for a in env.log)


# ---------------------------------------------------------------- a login page by structure

NEWS = "https://news.example.org/"


def test_a_page_with_a_login_link_is_ready_not_a_login_page():
    front = page(
        "Brave Browser",
        Item("Pebble News", "heading"),
        Item("login", "link"),
        Item("Sign in", "button"),  # a header's sign-in button with no form beside it
        Item("Search", "field", search=True),
        Item("A made-up story about lamps", "link"),
        url=NEWS,
    )
    assert not is_login_page(front)
    env = FakeEnv({"finder": page("Finder"), "front": front}, "finder")
    env.on[("finder", f"url {NEWS} Google Chrome Person 1")] = "front"
    env.pages["front"].app = "Google Chrome"

    class WeakLaya(FakeLaya):  # leans to "a login screen" (the header says sign in) but is not sure under the risky tier
        def acts(self, choice, level="reversible"):
            return choice.ranked[0][1] >= (0.95 if level == "risky" else choice.gate)

    laya = WeakLaya(screen_kind=lambda s, p: ch("D2 page state", (LOGIN, 0.7), (READY, 0.3)))
    tb = toolbox(env, laya, task="read the top story on pebble news", tabs=())
    result = tb.tool_open_place("Pebble News", "Top stories", site="news.example.org")
    assert result.ok and tb.moves == 0 and "page_state" not in laya.asked and "screen_kind" in laya.asked


def test_a_sign_in_page_the_next_step_types_into_is_the_place_not_a_reason_to_move():
    verify = page("Google Chrome", Item("Enter the code we sent you", "heading"), Item("One-time code", "field"), url=NEWS)
    sure_login = FakeLaya(screen_kind=lambda s, p: ch("D2 page state", (LOGIN, 0.9), (READY, 0.1)))

    def opened(upcoming):
        env = FakeEnv({"finder": page("Finder"), "verify": verify}, "finder")
        env.on[("finder", f"url {NEWS} Google Chrome Person 1")] = "verify"
        tb = toolbox(env, sure_login, task="sign in to pebble news with the code", tabs=())
        tb.upcoming = lambda: upcoming
        return tb, tb.tool_open_place("Pebble News", "", site="news.example.org")

    tb, result = opened([{"tool": "write", "text": "{code}", "where": "one-time code"}])
    assert result.ok and tb.moves == 0  # the page the plan signs in on
    tb, result = opened([{"tool": "click", "target": "Top stories"}])
    assert not result.ok or tb.moves > 0  # a step that types nothing there: a sure login page is still proof
    tb, result = opened([{"tool": "write", "text": "lamps", "where": "search box"}])
    assert not result.ok or tb.moves > 0  # a field the page does not show


def test_a_password_field_by_structure_is_a_login_page_whatever_it_is_called():
    secret = page("Brave Browser", Item("Willkommen", "heading"), Item("", "field", secret=True), Item("Weiter", BTN), url=ROOT)
    assert is_login_page(secret)
    form = page("Brave Browser", Item("Email address", "field"), Item("Sign in", BTN), url=ROOT)
    assert not is_login_page(form)  # no password field: whether it asks to sign in is Laya's


def test_a_redirect_to_a_sign_in_page_on_another_host_is_a_login_page():
    elsewhere = page(
        "Google Chrome", Item("Choose an account", "heading"), Item("Password", "field", secret=True),
        url="https://accounts.example.net/",
    )  # fmt: skip
    env = FakeEnv({"finder": page("Finder"), "elsewhere": elsewhere, "board": board("Personal")}, "finder")
    env.on[("finder", f"url {ROOT} Google Chrome Person 1")] = "elsewhere"
    env.on[("elsewhere", f"url {ROOT} Brave Browser Work")] = "board"
    env.pages["board"].profile = "Work"
    tb = toolbox(env, tabs=())
    tb.using = "Google Chrome"
    result = tb.tool_open_place("Quillo", "Boards", site=SITE)
    assert result.ok and tb.moves == 1
    assert tb.place_proof == "" and any("login page in Person 1" in line for line in tb.trace)


def test_a_redirect_to_another_host_with_no_sign_in_is_followed_and_that_host_is_the_place():
    """An address the brain names may send the browser on to the service's real host (a signed-in inbox on another
    host): judged by the page it landed on, never a sign-in only because the host changed."""
    landed = page(
        "Google Chrome", Item("Inbox", "heading"), Item("Compose", BTN), Item("A row", "row"),
        url="https://post.quillo.example/u/0/",
    )  # fmt: skip
    env = FakeEnv({"finder": page("Finder"), "landed": landed}, "finder")
    env.on[("finder", "url https://quillomail.example/ Google Chrome Person 1")] = "landed"
    tb = toolbox(env, tabs=())
    tb.using = "Google Chrome"
    result = tb.tool_open_place("Quillo mail", "", site="quillomail.example")
    assert result.ok and tb.moves == 0, (result, tb.trace)
    place = tb.places["Quillo mail"]
    assert place.url == "https://post.quillo.example/" and tb.showing(place, landed), tb.trace
    assert any("judged by the page it landed on" in line for line in tb.trace)


def test_a_page_on_a_sibling_host_is_not_on_the_site_but_a_subdomain_is():
    from karta.harness.tools import on_site

    results = page("Brave Browser", url="https://www.quillo.example/search?q=x")
    assert not on_site(results, "https://post.quillo.example/")  # a search host is never the mail host
    assert on_site(page("Brave Browser", url="https://post.quillo.example/u/0"), "https://quillo.example/")
    assert on_site(page("Brave Browser", url="https://quillo.example/x"), "https://www.quillo.example/")
    assert not on_site(page("Brave Browser", url="https://quillo.example.evil/x"), "https://quillo.example/")


def test_the_longest_app_name_wins_and_the_lock_never_stands_in_for_the_app_the_task_names():
    facts = MacFacts(["Pads", "Ink Pad", "Finder"], {}, [], datetime(2026, 10, 6, 9, 0))
    assert facts_mod.named_apps("how many styles does ink pad show", facts.installed_apps) == ["Ink Pad"]
    assert facts_mod.named_apps("open my pads and the ink pad", facts.installed_apps) == ["Pads", "Ink Pad"]
    assert [p.app for p in places_for("Ink Pad", "count the styles in Ink Pad", facts)] == ["Ink Pad"]
    env = FakeEnv({"pads": page("Pads", Item("Library", "heading")), "ink": page("Ink Pad", Item("Styles", "heading"))}, "pads")
    env.on[("pads", "open Ink Pad")] = "ink"
    tb = Toolbox("count the styles in Ink Pad", env, FakeLaya(), facts, None, lambda q: True)
    tb.upcoming = lambda: []
    tb.places["Ink Pad"] = facts_mod.app_place("Pads")  # a lock from a wrong resolution
    assert tb.found_place("Ink Pad app") is None
    assert tb.tool_open_place("Ink Pad app").ok and env.at == "ink"
