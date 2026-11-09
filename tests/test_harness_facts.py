"""Harness v2 facts and risk: code decides what code can (installed apps, profiles, login, dialogs, text, risk)."""

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from karta.harness.facts import (
    blocking_dialog,
    in_the_way,
    installed_apps,
    is_login_page,
    mac_facts,
    page_fact,
    places_for,
    shows,
)
from karta.harness.risk import risky
from karta.harness.types import Item, MacFacts, Outcome, Screen

NOW = datetime(2026, 9, 29, 10, 30)


def local_state(path, cache, last=None, order=None):
    path.mkdir(parents=True, exist_ok=True)
    profile = {"info_cache": {d: {"name": n, "gaia_name": "x"} for d, n in cache.items()}}
    if last:
        profile["last_used"] = last
    if order:
        profile["profiles_order"] = order
    (path / "Local State").write_text(json.dumps({"profile": profile, "os_crypt": {"encrypted_key": "secret"}}))


@pytest.fixture
def home(tmp_path):
    apps = tmp_path / "Applications"
    for app in ("Brave Browser", "Google Chrome", "Safari", "Notes", "Slack"):
        (apps / f"{app}.app").mkdir(parents=True)
    (apps / "Utilities" / "Terminal.app").mkdir(parents=True)
    (apps / "Utilities" / "readme.txt").write_text("not an app")
    support = tmp_path / "Library" / "Application Support"
    local_state(
        support / "BraveSoftware" / "Brave-Browser",
        {"Default": "Work", "Profile 1": "Personal", "Profile 2": "Test"},
        last="Profile 1",
        order=["Default", "Profile 1", "Profile 2"],
    )
    local_state(support / "Google" / "Chrome", {"Default": "Person 1"})
    local_state(support / "Microsoft Edge", {"Default": "Edge one"})  # Local State left behind, Edge not installed
    for d in ("Desktop/Projects/karta/src", "Desktop/Projects/folio", "Desktop/Projects/.hidden", "Documents/Taxes"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "Desktop/Projects/karta/node_modules").mkdir()
    (tmp_path / "Desktop/notes.txt").write_text("never read")
    return tmp_path


def test_mac_facts_reads_apps_profiles_and_folders_from_a_fake_home(home):
    facts = mac_facts(home, app_dirs=(home / "Applications",), default="Google Chrome", now=NOW)
    assert facts.installed_apps == ["Brave Browser", "Google Chrome", "Notes", "Safari", "Slack", "Terminal"]
    assert list(facts.browsers) == ["Google Chrome", "Brave Browser", "Safari"]  # the default first; Edge not installed
    assert facts.browsers["Brave Browser"] == ["Personal", "Work", "Test"]  # the last used first
    assert facts.browsers["Safari"] == []
    assert "~/Desktop/Projects/karta" in facts.folders and "~/Desktop/Projects/karta/src" in facts.folders
    assert "~/Documents/Taxes" in facts.folders
    assert not any(".hidden" in f or "node_modules" in f or f.endswith(".txt") for f in facts.folders)
    assert len(facts.folders) == len(set(facts.folders))
    assert facts.now == NOW
    assert "Brave Browser profiles: Personal, Work, Test" in facts.summary()


def test_a_broken_local_state_gives_no_profiles_not_an_error(home):
    (home / "Library/Application Support/Google/Chrome/Local State").write_text("{not json")
    facts = mac_facts(home, app_dirs=(home / "Applications",), default="", now=NOW)
    assert facts.browsers["Google Chrome"] == []
    assert next(iter(facts.browsers)) == "Brave Browser"


def test_missing_app_folders_are_skipped(tmp_path):
    assert installed_apps((tmp_path / "nope", tmp_path)) == []


def test_mac_facts_never_opens_anything_but_local_state(home, monkeypatch):
    opened = []
    real = Path.read_text

    def spy(path, *args, **kwargs):
        opened.append(os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    mac_facts(home, app_dirs=(home / "Applications",), default="", now=NOW)
    assert opened and all(p.endswith("Local State") for p in opened)


# ------------------------------------------------------------------ places

FACTS = MacFacts(
    installed_apps=[
        "Brave Browser",
        "Calendar",
        "Claude",
        "Google Chrome",
        "Mail",
        "Messages",
        "Notes",
        "Reminders",
        "Safari",
        "Slack",
        "Terminal",
    ],
    browsers={"Brave Browser": ["Work", "Personal"], "Google Chrome": ["Person 1"], "Safari": []},
    folders=["~/Desktop/Projects/karta"],
    now=NOW,
)


def names(places):
    return [p.name for p in places]


def test_a_named_site_is_offered_in_every_browser_profile_and_no_unrelated_app():
    places = places_for("Chatterbox chats", "send a message to Mira on Chatterbox saying hi", FACTS, site="chat.example")
    assert names(places) == [
        "chat.example in Brave Browser, profile Work",
        "chat.example in Brave Browser, profile Personal",
        "chat.example in Google Chrome, profile Person 1",
        "chat.example in Safari",
    ]  # no Chatterbox app is installed; Messages ("a message") is never offered
    assert places[1].url == "https://chat.example/" and places[1].profile == "Personal"
    assert places[3].profile is None and places[3].app == "Safari"


def test_no_site_named_no_site_offered():
    places = places_for("Chatterbox chats", "send a message to Mira on Chatterbox", FACTS)
    assert all(p.url is None for p in places)  # no catalog: only apps the words name


def test_an_installed_app_the_task_names_comes_first():
    places = places_for("Slack", "post hello in #general on slack", FACTS, site="team.example")
    assert names(places)[0] == "Slack app"
    assert names(places)[1] == "team.example in Brave Browser, profile Work"


def test_a_named_browser_narrows_the_sites():
    places = places_for("Tracklet", "open tracklet in chrome and find the login bug", FACTS, site="tracklet.example")
    assert names(places) == ["tracklet.example in Google Chrome, profile Person 1"]


def test_an_app_the_task_names_and_nothing_else():
    assert names(places_for("Notes", "write a note saying buy milk", FACTS)) == ["Notes app"]
    assert names(places_for("Reminders", "remind me at 5", FACTS)) == ["Reminders app"]


def test_what_speaks_before_the_task():
    places = places_for("Terminal", "open terminal and run claude in the karta folder", FACTS)
    assert names(places) == ["Terminal app"]  # the Claude app is not what the step opens


def test_a_missing_app_is_never_offered():
    assert places_for("Spotify app", "play lofi on the Spotify app", MacFacts([], {}, [], NOW)) == []
    assert names(places_for("Photos", "open photos", FACTS)) == []


def test_the_remembered_place_comes_first_even_when_the_wording_changed():
    remembered = "web.whatsapp.com in Brave Browser, profile Personal"
    places = places_for("WhatsApp", "message Rahul on whatsapp", FACTS, remembered=remembered)
    assert names(places)[0] == remembered and names(places).count(remembered) == 1
    places = places_for("my chats", "text Rahul", FACTS, remembered=remembered)
    assert names(places) == [remembered]
    gone = "web.whatsapp.com in Brave Browser, profile Family"  # that profile is not on this Mac any more
    assert places_for("my chats", "text Rahul", FACTS, remembered=gone) == []


def test_a_site_the_brain_names_needs_no_catalog():
    places = places_for("Jira board", "move PROJ-12 to done in jira", FACTS, site="acme.atlassian.net")
    assert names(places) == [
        "acme.atlassian.net in Brave Browser, profile Work",
        "acme.atlassian.net in Brave Browser, profile Personal",
        "acme.atlassian.net in Google Chrome, profile Person 1",
        "acme.atlassian.net in Safari",
    ]
    assert places[0].url == "https://acme.atlassian.net/"
    deep = places_for("Jira", "open my issues", FACTS, site="https://Acme.atlassian.net/jira/your-work")
    assert deep[0].url == "https://acme.atlassian.net/jira/your-work"


def test_a_named_site_still_brings_its_own_app_and_keeps_everyday_apps_out():
    places = places_for("Slack", "send a message in #design on slack", FACTS, site="app.slack.com")
    assert names(places)[0] == "Slack app" and "Messages app" not in names(places)
    assert names(places)[1] == "app.slack.com in Brave Browser, profile Work"


def test_a_site_that_is_not_host_shaped_is_ignored():
    for site in ("tunes", "open tunes", "javascript:alert(1)", "file:///etc/passwd", "app tunes com"):
        assert places_for("the player", "play lofi", FACTS, site=site) == [], site


def test_apps_outside_the_catalog_are_offered_by_name():
    facts = MacFacts(["Spotify", "System Settings", "Safari"], {"Safari": []}, [], NOW)
    assert names(places_for("System Settings", "turn on dark mode", facts)) == ["System Settings app"]
    assert names(places_for("Spotify", "play lofi beats", facts))[0] == "Spotify app"


# ------------------------------------------------------------------ login pages


def test_a_password_field_makes_a_login_page_in_any_language():
    for name in ("Password", "Passwort", "Mot de passe", "Contraseña", "text field"):  # the name never matters
        screen = Screen("Brave Browser", [Item("E-Mail", "field"), Item(name, "field", secret=True), Item("Weiter", "button")])
        assert is_login_page(screen), name


def test_wording_alone_is_never_a_login_page():  # no password field: meaning, Laya's (Toolbox.asks_sign_in)
    sso = Screen("Brave Browser", [Item("Log in to Tracklet", "heading"), Item("Continue with Google", "button")])
    header = Screen("Brave Browser", [Item("Log in", "link"), Item("Main page", "heading"), Item("Tea", "row")])
    terminal = Screen("Terminal", [Item("Last login: Tue Sep 29 09:12:01 on ttys001", "text"), Item("~ % ", "text")])
    chrome = Screen("Brave Browser", [Item("Password", "field", secret=True, chrome=True), Item("Chats", "heading")])
    for screen in (sso, header, terminal, chrome):
        assert not is_login_page(screen), screen


# ------------------------------------------------------------------ text on the page


def test_shows_reads_the_page_not_fields_menus_or_chrome():
    screen = Screen(
        "Brave Browser",
        [
            Item("Search or start a new chat", "field", value="Hemanshu", search=True),
            Item("History > Hemanshu Patel", "menu", menu=True),
            Item("Hemanshu Patel - WhatsApp", "tab", chrome=True),
            Item("Chats", "heading"),
        ],
    )
    assert not shows(screen, "Hemanshu Patel")
    assert shows(screen, "the chat list shows Chats") is False  # "list", "shows"... only distinctive words count
    assert shows(screen, "Chats")


def test_shows_matches_distinctive_words_and_ocr_slips():
    screen = Screen("Brave Browser", [Item("Hemanshu Pate1", "text", source="ocr"), Item("online", "text")])
    assert shows(screen, "Hemanshu Patel")
    assert shows(screen, "Patel, Hemanshu")  # all distinctive words, any order
    assert shows(screen, "Hemanshoo Patel") is False  # two letters off is another name
    assert shows(screen, "Hemansu Patel")  # one letter dropped in a long word


def test_our_own_typing_does_not_count_until_the_field_lets_go():
    unsent = Screen(
        "Brave Browser", [Item("Type a message", "field", value="its fixed"), Item("its fixed", "text", source="ocr")]
    )
    assert not shows(unsent, "its fixed", typed={"its fixed"})
    sent = Screen("Brave Browser", [Item("Type a message", "field"), Item("its fixed", "text"), Item("10:31", "text")])
    assert shows(sent, "its fixed", typed={"its fixed"})
    terminal = Screen("Terminal", [Item("shubham@mac karta % claude", "text")])
    assert not shows(terminal, "claude", typed={"claude"})
    assert shows(terminal, "claude")


# ------------------------------------------------------------------ dialogs


def test_items_the_env_marks_as_dialog_are_the_dialog_whatever_their_words():
    screen = Screen(
        "Brave Browser",
        [
            Item("Where should we deliver?", "heading", region="middle", dialog=True),
            Item("Find me", "button", region="middle", dialog=True),
            Item("delivery address", "field", region="middle", dialog=True),
            Item("Cart", "button", region="top-right"),
            Item("Search", "field", region="top", search=True),
        ],
    )
    assert [i.name for i in blocking_dialog(screen)] == ["Find me", "delivery address"]
    assert page_fact(screen) is Outcome.BLOCKED_BY_DIALOG


def test_answer_words_without_a_dialog_role_are_the_pages_own_never_a_dialog():
    tunebox = Screen(  # an update offer drawn on the page, with no dialog role: the steps act on the page as usual
        "Tunebox",
        [
            Item("Home", "link", region="left"),
            Item("Update available. Restart now?", "text", region="bottom-right"),
            Item("Not now", "button", region="bottom-right"),
            Item("Restart", "button", region="bottom-right"),
            Item("Play", "button", region="bottom"),
        ],
    )
    assert blocking_dialog(tunebox) is None and in_the_way(tunebox) == []
    sheet = Screen(  # the same ask in a sheet the env marks: a dialog
        "Gadgetry Settings",
        [
            Item("Bluetooth", "row", region="left"),
            Item("Are you sure you want to turn off Bluetooth?", "text", region="middle", dialog=True),
            Item("Cancel", "button", region="middle", dialog=True),
            Item("Turn Off", "button", region="middle", dialog=True),
        ],
    )
    assert [i.name for i in blocking_dialog(sheet)] == ["Cancel", "Turn Off"]


def test_terminal_numbered_choices_are_a_dialog():
    screen = Screen(
        "Terminal",
        [
            Item("Do you trust the files in this folder?", "text"),
            Item("\u276f 1. Yes, proceed", "text"),
            Item("2. No, exit", "text"),
            Item("Enter to confirm \u00b7 Esc to exit", "text"),
        ],
    )
    assert [i.name for i in blocking_dialog(screen)] == ["\u276f 1. Yes, proceed", "2. No, exit"]


def test_a_permission_prompt_is_known_by_who_owns_it_never_by_its_words():
    items = [
        Item("\u201eKarta\u201c m\u00f6chte auf deine Kontakte zugreifen.", "text", dialog=True),
        Item("Erlauben", "button", dialog=True),
        Item("Nicht erlauben", "button", dialog=True),
    ]
    assert page_fact(Screen("UserNotificationCenter", items)) is Outcome.NEEDS_PERMISSION  # the system's prompter
    bubble = [replace(i, browser_ui=True) for i in items]
    assert page_fact(Screen("Brave Browser", bubble)) is Outcome.NEEDS_PERMISSION  # the browser's own dialog
    assert page_fact(Screen("Karta", items)) is Outcome.BLOCKED_BY_DIALOG  # a site's or app's own dialog: no words read


def test_ordinary_pages_are_not_dialogs():
    inbox = Screen("Mail", [Item("Inbox", "heading"), Item("Reply", "button"), Item("Delete", "button"), Item("OK", "text")])
    numbered = Screen("Notes", [Item("1. milk", "text"), Item("2. eggs", "text")])  # a list, no question
    many = Screen("Brave Browser", [Item("Do you want to proceed?", "text")] + [Item("OK", "button")] * 7)
    assert blocking_dialog(inbox) is None and blocking_dialog(numbered) is None and blocking_dialog(many) is None
    spread = Screen(  # a Jira board: answer-like buttons all over the page, a question in an issue title
        "Brave Browser",
        [
            Item("Can we ship the login fix?", "row", region="middle"),
            Item("Why is sync slow?", "text", region="middle"),
            Item("Cancel", "button", region="top-right"),
            Item("Continue", "button", region="bottom"),
        ],
    )
    assert blocking_dialog(spread) is None
    no_prompt = Screen("Slack", [Item("general", "row", region="left"), Item("OK", "button", region="middle")])
    assert blocking_dialog(no_prompt) is None


def test_page_fact_reads_login_then_ready():
    login = Screen(
        "Brave Browser", [Item("Log in", "button"), Item("Kennwort", "field", secret=True), Item("Trackly", "heading")]
    )
    ready = Screen("Brave Browser", [Item("My issues", "heading"), Item("ENG-231 Login bug", "row")])
    assert page_fact(login, "My issues") is Outcome.LOGIN_PAGE
    assert page_fact(ready, "My issues") is Outcome.OK
    assert page_fact(ready, "Inbox zero") is None  # only Laya can tell


# ------------------------------------------------------------------ risk


@pytest.mark.parametrize(
    ("action", "target", "kind"),
    [
        ("click", "Send", "send"),
        ("click", "Proceed to pay", "pay"),
        ("click", "Proceed to Checkout", "purchase"),
        ("click", "Place order", "purchase"),
        ("click", "Comment", "comment"),
        ("click", "Post", "post"),
        ("click", "Delete conversation", "delete"),
        ("click", "Remove from cart", "delete"),
        ("click", "Log out", "log out"),
        ("click 'Sign out'", "", "log out"),
        ("press return", "Type a message", "send"),
        ("press cmd+return", "Leave a comment...", "comment"),
        ("submit", "Message #general", "send"),
        ("press", "cmd+q", "quit"),
        ("press cmd+delete", "", "delete"),
        ("answer", "Submit", "submit"),
    ],
)
def test_risky_actions_name_their_risk(action, target, kind):
    assert risky(action, target) == kind


@pytest.mark.parametrize(
    ("action", "target"),
    [
        ("click", "Sent"),
        ("click", "Trash"),
        ("click", "Search"),
        ("click", "ADD"),
        ("click", "Don't send"),
        ("click", "Save draft"),
        ("click", "Close"),
        ("press return", "Search or start a new chat"),
        ("submit", "Search"),
        ("press", "escape"),
        ("write", "Send"),
        ("type", "delete everything"),
        ("open_place", "Payments"),
    ],
)
def test_safe_actions_are_not_risky(action, target):
    assert risky(action, target) is None


# ------------------------------------------------------------------ interruptions and permission asks

from karta.harness.facts import (  # noqa: E402
    MacMap,
    Tab,
    content_rows,
    dismiss_option,
    mac_map,
    open_tabs,
    refresh_tabs,
)
from karta.harness.memory import HarnessMemory  # noqa: E402


def button(name, **kw):
    return Item(name, "button", **kw)


def test_dismiss_option_is_the_dialogs_own_cancel_or_close_by_structure():
    later = button("Später", dialog=True)
    own = button("Schließen", dialog=True, cancels=True)  # its AXCancelButton or a close button, in any language
    assert dismiss_option([later, own]) is own
    assert dismiss_option([button("Maybe later", dialog=True), button("Not now", dialog=True)]) is None  # words: Laya's


def test_dismiss_option_skips_chrome_menus_disabled_and_page_items():
    window_close = Item("Close", "button", chrome=True, cancels=True)
    menu_close = Item("File > Close", "menu", menu=True, cancels=True)
    page_close = button("Close", cancels=True)  # not in a dialog
    assert (
        dismiss_option([window_close, menu_close, page_close, button("Skip", enabled=False, cancels=True, dialog=True)]) is None
    )


def text(line, **kw):
    return Item(line, "text", **kw)


def row(name):
    return Item(name, "row")


# ------------------------------------------------------------------ open but not usable


def scr(*items, app="Some App"):
    return Screen(app, list(items))


def test_content_rows_are_the_fact_whatever_the_page_says():
    assert content_rows(scr(text("No messages"), row("Design review notes"))) == [row("Design review notes")]
    assert content_rows(scr(Item("Inbox", "heading"), text("Keine Nachrichten"), button("Konto hinzufügen"))) == []
    assert content_rows(scr(Item("Add account", "menu", menu=True))) == []  # menus are not the page


# ------------------------------------------------------------------ open tabs first


def fake_osascript(tabs):
    """tabs: {browser: [(window, tab, url, title)]} -> an osascript runner answering like the TAB_SCRIPT."""

    def run(script):
        for browser, rows in tabs.items():
            if f'"{browser}"' in script:
                return "".join(f"{w}\x1f{t}\x1f{u}\x1f{title}\x1e" for w, t, u, title in rows)
        return ""

    return run


TABS = {
    "Brave Browser": [(1, 1, "https://mail.google.com/mail/u/0/#inbox", "Inbox (3)"), (1, 2, "https://web.whatsapp.com/", "WA")],
    "Safari": [(2, 4, "https://www.youtube.com/watch?v=x", "a video"), (2, 5, "file:///tmp/x.html", "local")],
}


def test_open_tabs_reads_positions_and_urls_and_filters_hosts():
    tabs = open_tabs(["Brave Browser", "Safari", "Firefox"], run=fake_osascript(TABS))
    assert [(t.browser, t.window_index, t.tab_index) for t in tabs] == [
        ("Brave Browser", 1, 1),
        ("Brave Browser", 1, 2),
        ("Safari", 2, 4),
    ]  # fmt: skip  (the file: tab and Firefox are skipped)
    only = open_tabs(["Brave Browser", "Safari"], hosts={"youtube.com"}, run=fake_osascript(TABS))
    assert [t.url for t in only] == ["https://www.youtube.com/watch?v=x"]


def test_open_tabs_off_a_mac_is_empty():
    assert open_tabs(["Google Chrome"], run=lambda script: "") == []


def test_an_open_tab_of_the_wanted_site_comes_first():
    tabs = [
        Tab("Google Chrome", 1, 3, "https://www.youtube.com/", "x", "Person 1"),
        Tab("Brave Browser", 2, 1, "https://www.youtube.com/feed", "y", None),
        Tab("Brave Browser", 2, 2, "https://www.youtube.com/other", "z", None),  # one per browser and profile
        Tab("Brave Browser", 1, 1, "https://mail.google.com/", "mail", None),  # another site never leaves
    ]
    remembered = "youtube.com in Brave Browser, profile Personal"
    places = places_for("YouTube", "play lofi on youtube", FACTS, remembered=remembered, site="youtube.com", tabs=tabs)
    assert names(places)[:3] == [
        "youtube.com in Google Chrome, profile Person 1, open tab 1.3",
        "youtube.com in Brave Browser, open tab 2.1",
        remembered,
    ]
    assert places[0].tab == (1, 3) and places[0].url == "https://www.youtube.com/"
    assert not any("mail.google.com" in n for n in names(places))
    assert places_for("YouTube", "play lofi", FACTS, site="youtube.com", tabs=[]) == places_for(
        "YouTube", "play lofi", FACTS, site="youtube.com"
    )


def test_a_remembered_tab_stands_for_its_site():
    remembered = "youtube.com in Brave Browser, profile Work, open tab 4.2"
    places = places_for("YouTube", "play lofi on youtube", FACTS, remembered=remembered)
    assert places[0].name == "youtube.com in Brave Browser, profile Work"  # the tab may be gone: its site
    assert places[0].tab is None and places[0].profile == "Work" and names(places).count(places[0].name) == 1


def test_a_named_browser_limits_the_tabs():
    tabs = [Tab("Google Chrome", 1, 1, "https://www.youtube.com/", "", None)]
    places = places_for("YouTube", "play lofi on youtube in Brave", FACTS, site="youtube.com", tabs=tabs)
    assert all(p.tab is None for p in places)


# ------------------------------------------------------------------ the Mac map


def test_mac_map_combines_facts_tabs_and_memory(home):
    memory = HarnessMemory(":memory:")
    memory.place_result("team chat", "Slack app", True)
    memory.place_result("mail", "mail.google.com in Brave Browser, profile Work", True)
    tabs = {"Brave Browser": [(1, 1, "https://mail.google.com/mail/u/0/", "secret subject")],
            "Google Chrome": [(1, 2, "https://docs.google.com/document/d/abc", "private doc")]}  # fmt: skip
    app_dirs = (home / "Applications",)
    mac = mac_map(memory, home=home, app_dirs=app_dirs, default="Brave Browser", now=NOW, run=fake_osascript(tabs),
                  running=["Brave Browser", "Google Chrome", "Finder"])  # fmt: skip
    assert mac.default_browser == "Brave Browser"
    assert [t.profile for t in mac.tabs] == [None, "Person 1"]  # Chrome has one profile: known
    summary = mac.summary()
    assert "Installed: Brave Browser, Google Chrome, Notes, Safari, Slack, Terminal" in summary
    assert "Brave Browser profiles: Personal, Work, Test" in summary
    assert "Open: mail.google.com (Brave Browser), docs.google.com (Google Chrome, Person 1)" in summary
    assert "Known signed in: " in summary and "Slack app" in summary
    assert "secret" not in summary and "private" not in summary and "/document/" not in summary  # hosts only
    assert "Running: Brave Browser, Google Chrome, Finder" in summary


def test_mac_map_caches_apps_and_refreshes_tabs(home, monkeypatch):
    app_dirs = (home / "Applications",)
    mac = mac_map(home=home, app_dirs=app_dirs, default="", now=NOW, refresh_tabs=False)
    assert mac.tabs == [] and mac.tabs_read == 0.0
    (home / "Applications" / "Figma.app").mkdir()
    again = mac_map(home=home, app_dirs=app_dirs, default="", now=NOW, refresh_tabs=False)
    assert "Figma" not in again.facts.installed_apps  # read once per process
    tabs = {"Safari": [(1, 1, "https://www.figma.com/files", "t")]}
    refresh_tabs(again, run=fake_osascript(tabs), running=["Safari", "Google Chrome"])
    assert [t.url for t in again.tabs] == ["https://www.figma.com/files"] and again.tabs_read > 0
    refresh_tabs(again, run=fake_osascript(tabs), running=["Google Chrome"])  # Safari quit: not asked
    assert again.tabs == []


def test_mac_map_summary_caps_open_hosts():
    mac = MacMap(MacFacts(["Safari"], {"Safari": []}, [], NOW))
    mac.tabs = [Tab("Safari", 1, n, f"https://site{n}.example.com/", "") for n in range(1, 21)]
    line = next(line for line in mac.summary(max_open=3).splitlines() if line.startswith("Open:"))
    assert line.endswith("site3.example.com (Safari), and 17 more")
