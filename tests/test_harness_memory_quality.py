"""Memory quality: web facts per exact host, no junk names, know-how under the place the fix acted on, a lean
profile, and the migration that cleans a file from before (made-up apps and sites only)."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from karta.harness.facts import Place
from karta.harness.loop import know_how, remember
from karta.harness.memory import ELEMENT, KNOW_HOW, PITFALL, PROFILE, HarnessMemory, host_key, key_of, unfit_element
from karta.harness.types import Item, MacFacts, Screen

FACTS = MacFacts(["Brave Browser", "Finder", "Tallybook", "Clockwork"], {"Brave Browser": ["Work"]}, [], None)
WAY = "click|||a dialog opened"

PROOF = "created(a made-up thing)"  # the contract a proven run met: what memory keeps rows by


def web(url: str) -> Screen:
    return Screen("Brave Browser", [Item("Home", "heading")], url=url)


# ---------------------------------------------------------------- 1. by exact host


def test_the_key_is_the_exact_host_never_the_registrable_domain():
    assert host_key("https://www.mailbox.example/u/0") == "mailbox.example"
    assert host_key("https://post.mailbox.example:8443/inbox") == "post.mailbox.example"
    assert key_of(web("https://post.mailbox.example/inbox"), ["Brave Browser"]) == "post.mailbox.example"
    assert key_of(Screen("Brave Browser", []), ["Brave Browser"]) == ""
    assert key_of(Screen("Tallybook", []), ["Brave Browser"]) == "Tallybook"


def test_a_mail_host_and_a_search_page_of_one_domain_never_share_facts():
    memory = HarnessMemory(":memory:")
    memory.element_result("post.findit.example", "compose", "Write", True, how=WAY)
    memory.element_result("findit.example", "search box", "Ask", True, how="write|pasted|Return|")
    mail = know_how(memory, "write to sam", web("https://post.findit.example/inbox"), FACTS)
    search = know_how(memory, "look up otters", web("https://www.findit.example/"), FACTS)
    assert any("Write" in line for line in mail) and not any("Ask" in line for line in mail)
    assert any("Ask" in line for line in search) and not any("Write" in line for line in search)
    assert memory.element_for("post.findit.example", "search box") is None


def test_subdomains_serve_only_when_the_exact_host_keeps_nothing():
    memory = HarnessMemory(":memory:")
    memory.element_result("app.ledgerly.example", "new expense", "Log spend", True, how=WAY)
    block = know_how(memory, "log a taxi on ledgerly.example", Screen("Finder", []), FACTS)
    assert block[0].startswith("Known about app.ledgerly.example (")  # nothing for the host the task names
    memory.element_result("ledgerly.example", "sign in", "Enter", True, how=WAY)
    block = know_how(memory, "log a taxi on ledgerly.example", Screen("Finder", []), FACTS)
    assert block[0].startswith("Known about ledgerly.example (") and not any("Log spend" in x for x in block)
    assert know_how(memory, "x", web("https://deals.app.ledgerly.example/"), FACTS) == []  # never a parent host


# ---------------------------------------------------------------- 2. no junk names


def test_only_real_accessible_names_are_kept():
    assert unfit_element("None", "Clockwork") == "no name"
    assert unfit_element("Due", "Clockwork", accessible=False) == "not an accessible name (read off the pixels)"
    assert unfit_element("Title", "Clockwork", intent="the text field that holds the title") != ""
    assert unfit_element("Search for city or town", "Clockwork", intent="search for city or town") == ""
    assert unfit_element("Title", "Clockwork", intent="the title field") == ""
    memory = HarnessMemory(":memory:")
    assert memory.element_result("Clockwork", "the date of next friday, 6 november", "None", True)
    memory.add_pitfall("Clockwork", "the timer name field", "the timer name field", "Label")
    assert memory.list(ELEMENT) == [] and memory.list(PITFALL) == []
    memory.add_pitfall("Clockwork", "start", "Begin", "Start timer")  # a real wrong pick stays
    assert [(p.value, p.how) for p in memory.list(PITFALL)] == [("Begin", "Start timer")]


# ---------------------------------------------------------------- 3. know-how where the fix acted


def tools(elements=(), places=None) -> SimpleNamespace:
    return SimpleNamespace(
        places=places or {}, aliases={}, elements=list(elements), trace=[], task="turn on quiet hours", default_browser=None
    )


def test_know_how_goes_to_the_place_the_fix_acted_on_not_the_app_in_front():
    memory = HarnessMemory(":memory:")
    tip = "quiet hours live in Clockwork, under Schedule"
    acted = tools([("Clockwork", "quiet hours", "Quiet Hours", True, False)])
    remember(memory, acted, [("Finder", tip)], proof=PROOF)
    assert memory.know_how("Clockwork") == [tip] and memory.know_how("Finder") == []
    opened = tools(
        places={"ledger": Place("app.ledgerly.example in Brave Browser", "Brave Browser", "https://app.ledgerly.example/")}
    )
    remember(memory, opened, [("Finder", "the ledger opens on its last month")], proof=PROOF)
    assert memory.know_how("app.ledgerly.example") == ["the ledger opens on its last month"]
    remember(
        memory,
        tools([("Finder", "desktop", "Desktop", True, False)]),
        [("Finder", "folders open on a double click")],
        proof=PROOF,
    )
    assert memory.know_how("Finder") == ["folders open on a double click"]  # it acted there: it stays


# ---------------------------------------------------------------- 4. a lean profile


def test_the_profile_keeps_one_browser_and_names_only_aliases_the_task_says():
    clock = [1000.0]
    memory = HarnessMemory(":memory:", clock=lambda: clock[0])
    for value in ("Brave Browser", "Brave Browser, profile Work", "Brave Browser, profile Home"):
        clock[0] += 1
        memory.add_profile("browser in use", value)
    memory.add_profile("email", "jane.doe@example.com")
    memory.add_profile("email", "jd@example.org")  # two addresses of the user's own: both kept
    memory.remember_alias("reykjavik", "Local time", "clocks.example")
    memory.remember_alias("bea", "Bea Lund", "chirpline.example")
    lines = memory.profile("tell bea the build is green")
    assert lines == [
        "About the user (from tasks proven done):",
        "- browser in use: Brave Browser, profile Home",
        "- email: jane.doe@example.com",
        "- email: jd@example.org",
        '- "bea" is "Bea Lund" on chirpline.example',
    ]
    for n in range(10):
        memory.add_profile("name", f"Jane {n}")
    assert len(memory.profile("x")) == 1 + 6  # capped


# ---------------------------------------------------------------- the migration


def test_a_file_from_before_is_cleaned_on_open(tmp_path):
    path = tmp_path / "m.sqlite"
    db = sqlite3.connect(path)
    db.executescript(
        "create table harness_facts (kind text not null, app text not null, key text not null, value text not null,"
        " good integer not null default 0, bad integer not null default 0, updated real not null,"
        " how text not null default '', primary key (kind, app, key, value)); pragma user_version = 1;"
    )
    now = 1e9
    rows = [
        ("place", "", "mailbox", "post.findit.example in Brave Browser", 2, now, ""),
        ("place", "", "findit", "findit.example in Brave Browser", 2, now, ""),
        ("place", "", "dictionary", "dict.wordly.example in Brave Browser", 1, now, ""),
        ("element", "findit.example", "compose", "Write", 3, now, WAY),  # two hosts mixed: dropped
        ("element", "wordly.example", "search box", "Look up", 2, now, WAY),  # one host: moved to it
        ("element", "Clockwork", "the date of next friday, 6 november", "None", 1, now, ""),
        ("element", "Clockwork", "the reminder text field that holds", "Note", 1, now, ""),
        ("element", "Clockwork", "start", "Start timer", 2, now, WAY),
        ("pitfall", "Clockwork", "the timer name field", "the timer name field", 1, now, "Label"),
        ("profile", "", "browser in use", "Brave Browser", 9, now - 5, ""),
        ("profile", "", "browser in use", "Brave Browser, profile Work", 2, now, ""),
    ]
    db.executemany("insert into harness_facts (kind, app, key, value, good, updated, how) values (?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    memory = HarnessMemory(path, clock=lambda: now)
    # what a run taught before contracts was learned from Karta's own judgement: dropped, to be learned with its proof
    assert memory.list(ELEMENT) == [] and memory.list(PITFALL) == [] and memory.list(KNOW_HOW) == []
    assert len(memory.list("place")) == 3  # places re-check themselves on use (one that does not open is deleted)
    assert [f.value for f in memory.list(PROFILE)] == ["Brave Browser, profile Work"]
    (version,) = memory.db.execute("pragma user_version").fetchone()
    assert version == 3


def test_a_step_keyed_by_the_domain_is_kept_under_the_one_host_the_run_read():
    memory = HarnessMemory(":memory:")
    run = tools([("findit.example", "compose", "Write", True, False)])
    run.seen = {"Brave Browser": web("https://post.findit.example/inbox")}
    remember(memory, run, [], proof=PROOF)
    assert [(f.app, f.value) for f in memory.list(ELEMENT)] == [("post.findit.example", "Write")]
    both = tools([("findit.example", "search box", "Ask", True, False)])
    both.seen = {"Brave Browser": web("https://post.findit.example/")}
    both.left_behind = {"Brave Browser": web("https://www.findit.example/")}
    remember(memory, both, [], proof=PROOF)
    assert memory.element_for("findit.example", "search box") is None  # two hosts this run: kept nowhere
