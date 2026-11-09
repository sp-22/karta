"""What memory may keep and serve: know-how, elements and aliases per site (never per browser), places by identity
(never a tab's position), only from a task proven done, a remembered pick that fails forgotten at once, and no
chrome, bare site names or OCR fragments kept as elements. Made-up sites and apps only."""

from __future__ import annotations

import sqlite3

import pytest
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page, toolbox

from karta.harness.brain import HarnessBrain
from karta.harness.decide import NONE, NOT_SHOWN
from karta.harness.facts import Tab
from karta.harness.loop import know_how, remember, run_task
from karta.harness.memory import ELEMENT, KNOW_HOW, PLACE, HarnessMemory, place_value, unfit_element
from karta.harness.types import Item

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)

SHOP = "https://shop.pantrycart.com/"
CLIPS = "https://www.clipstream.tv/watch"
MAIL = "https://mailnest.com/inbox"
TIP = "the search control shows as a Q icon: click it"

PROOF = "created(a made-up thing)"  # the contract a proven run met: what memory keeps rows by


def yes(_q: str) -> bool:
    return True


def shop_env():
    """A grocery site whose search control is an icon named "Q"; clicking it shows the search page."""
    home = page("Brave Browser", Item("Aisles", "heading"), Item("Q", BTN), url=SHOP, profile="Personal")
    found = page(
        "Brave Browser", Item("Search the aisles", "field", search=True), Item("Results for you", "text"), url=SHOP + "search"
    )
    return FakeEnv({"home": home, "search": found}, "home", {("home", "click Q"): "search"})


NO_TARGET = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
RESCUED = (
    "contract: opened(search)\nclick the search bar | then: Results for you\ndone: Results for you",
    f"click Q | then: Results for you\nlearned: {TIP}",
)


# ---------------------------------------------------------------- know-how per site


def test_a_tip_learned_on_one_site_is_kept_under_that_site_and_never_served_on_another(tmp_path):
    memory = HarnessMemory(tmp_path / "m.sqlite")
    result = run_task("search the grocery aisles", shop_env(), NO_TARGET, FakeLineBrain(*RESCUED), FACTS, memory, yes)
    assert result.done, result.trace
    assert memory.know_how("shop.pantrycart.com") == [TIP]  # the page's host, not the browser
    assert memory.know_how("pantrycart.com") == [TIP]  # the site: its subdomain's, as it keeps nothing itself
    assert memory.know_how("Brave Browser") == []

    clips = page("Brave Browser", Item("Trending", "heading"), Item("Search videos", "field", search=True), url=CLIPS)
    assert know_how(memory, "search for a cooking video", clips, FACTS) == []  # the same browser, another site
    brain = FakeLineBrain("stop: nothing to do")
    run_task("search for a cooking video", FakeEnv({"a": clips}, "a"), FakeLaya(), brain, FACTS, memory, yes)
    assert TIP not in brain.asked[0][1]

    other = page("Brave Browser", Item("Deals", "heading"), url="https://deals.pantrycart.com/")
    assert know_how(memory, "find the deals", other, FACTS) == []  # another host of the site: its own facts only
    named = page("Finder", Item("Desktop", "row"))
    block = know_how(memory, "order milk on pantrycart.com", named, FACTS)
    assert block[0].startswith("Known about shop.pantrycart.com (") and f"- {TIP}" in block  # its one host


def test_a_browser_showing_no_web_page_keeps_and_serves_no_know_how(tmp_path):
    memory = HarnessMemory(tmp_path / "m.sqlite")
    blank = page("Brave Browser", Item("New Tab", "heading"))
    tb = toolbox(FakeEnv({"a": blank}, "a"), memory=memory)
    tb.look()
    assert tb.here == ""
    remember(memory, tb, [(tb.here, "never kept")], proof=PROOF)
    assert memory.list() == []
    assert know_how(memory, "open brave browser", blank, FACTS) == []


def test_an_element_remembered_on_one_site_is_not_picked_on_another():
    memory = HarnessMemory(":memory:")
    memory.element_result("shop.pantrycart.com", "the search bar", "Find groceries", True)
    shop = page("Brave Browser", Item("Find groceries", BTN), Item("Find stores", BTN), url=SHOP)
    tb = toolbox(FakeEnv({"a": shop}, "a"), memory=memory)
    item, result = tb.pick("the search bar", tb.look().items)
    assert item.name == "Find groceries" and "remembered" in result.detail

    clips = page("Brave Browser", Item("Find groceries", BTN), Item("Find videos", BTN), url=CLIPS)
    tb2 = toolbox(FakeEnv({"a": clips}, "a"), memory=memory)
    _item, result2 = tb2.pick("the search bar", tb2.look().items)
    assert "remembered" not in result2.detail


def test_old_browser_keyed_rows_are_dropped_and_tab_positions_merged_on_open(tmp_path):
    path = tmp_path / "m.sqlite"
    HarnessMemory(path)  # a file of the current version, then rows as an older Karta wrote them
    db = sqlite3.connect(path)
    db.executemany(
        "insert into harness_facts (kind, app, key, value, good, updated) values (?, ?, ?, ?, ?, 1e12)",
        [
            (KNOW_HOW, "Brave Browser", TIP, "", 3),
            (ELEMENT, "Brave Browser", "search box", "Search tabs", 2),
            ("alias", "Safari", "mira", "Mira Okafor", 1),
            (KNOW_HOW, "Notes", "pick a folder before New Note", "", 1),
            (PLACE, "", "clips", "clipstream.tv in Brave Browser, profile Work, open tab 1.13", 2),
            (PLACE, "", "clips", "clipstream.tv in Brave Browser, profile Work", 1),
        ],
    )
    db.execute("pragma user_version = 0")
    db.commit()
    db.close()
    memory = HarnessMemory(path)
    kept = sorted((f.kind, f.app, f.key, f.value, f.good) for f in memory.list())
    assert kept == [(PLACE, "", "clips", "clipstream.tv in Brave Browser, profile Work", 3)]  # nothing taught unproven
    assert memory.know_how("Brave Browser") == []


# ---------------------------------------------------------------- places by identity, never a tab's position


def clips_env():
    clips = page("Brave Browser", Item("Trending", "heading"), url=CLIPS, profile="Work")
    mail = page("Brave Browser", Item("Search mail", "field", search=True), Item("Inbox", "heading"), url=MAIL, profile="Work")
    return FakeEnv({"finder": page("Finder", Item("Desktop", "row")), "clips": clips, "mail": mail}, "finder")


def test_a_stored_tab_index_is_never_used():
    memory = HarnessMemory(":memory:")
    memory.place_result("clips", "clipstream.tv in Brave Browser, profile Work, open tab 1.13", True)
    assert memory.place_for("clips") == "clipstream.tv in Brave Browser, profile Work"
    assert memory.places() == ["clipstream.tv in Brave Browser, profile Work"]
    assert place_value("x.example in Safari, open tab 2.7") == "x.example in Safari"

    # the tabs moved since they were read: the site's tab is found again by its address in the tabs read now
    env = clips_env()
    env.on[("finder", "tab Brave Browser 2.1")] = "clips"
    env.on[("finder", "tab Brave Browser 1.13")] = "mail"
    tb = toolbox(env, task="watch something on clipstream")
    tb.tabs = [Tab("Brave Browser", 1, 13, CLIPS, "", "Work")]
    tb.read_tabs = lambda: [Tab("Brave Browser", 1, 13, MAIL, "", "Work"), Tab("Brave Browser", 2, 1, CLIPS, "", "Work")]
    result = tb.tool_open_place("clipstream", "Trending", site="clipstream.tv")
    assert result.ok and env.log == ["tab Brave Browser 2.1"], (result, env.log)

    # no tab is on the site now: the site is opened, never the old position
    env2 = clips_env()
    env2.on[("finder", f"url {CLIPS} Brave Browser Work")] = "clips"
    tb2 = toolbox(env2, task="watch something on clipstream")
    tb2.tabs = [Tab("Brave Browser", 1, 13, CLIPS, "", "Work")]
    tb2.read_tabs = lambda: [Tab("Brave Browser", 1, 13, MAIL, "", "Work")]
    assert tb2.tool_open_place("clipstream", "Trending", site="clipstream.tv").ok
    assert not any(a.startswith("tab ") for a in env2.log) and env2.log[0] == f"url {CLIPS} Brave Browser Work"


def test_a_tab_switch_that_lands_on_another_site_is_not_the_place():
    env = clips_env()
    env.on[("finder", "tab Brave Browser 2.1")] = "mail"  # the tab moved again between the read and the switch
    for profile in ("Work", "Personal"):
        env.on[("mail", f"url https://clipstream.tv/ Brave Browser {profile}")] = "clips"
    tb = toolbox(env, task="watch something on clipstream")
    tb.tabs = [Tab("Brave Browser", 2, 1, CLIPS, "", "Work")]
    tb.read_tabs = lambda: [Tab("Brave Browser", 2, 1, CLIPS, "", "Work")]
    result = tb.tool_open_place("clipstream", "Trending", site="clipstream.tv")
    assert result.ok and "open tab" not in result.detail, (result, env.log)
    assert env.log[0] == "tab Brave Browser 2.1" and any(a.startswith("url https://clipstream.tv/") for a in env.log)
    assert "click Search mail" not in env.log and env.at == "clips"
    assert "clipstream" in tb.notes.lines()[0] and "open tab" in tb.notes.lines()[0]  # the switch was a miss
    assert not tb.showing(tb.places["clipstream"], env.pages["mail"])


# ---------------------------------------------------------------- only a proven done is kept


def test_a_done_only_the_brain_saw_saves_nothing(tmp_path):
    memory = HarnessMemory(tmp_path / "m.sqlite")
    laya = FakeLaya(
        target=lambda i, items: ch("D3 target", (NONE, 0.9)),
        outcome=lambda e, s: ch("D7 outcome", (NOT_SHOWN, 0.45), ("shown", 0.40)),  # Laya unsure
    )
    brain = FakeLineBrain(
        "click the search bar | then: Results for you\ndone: Basket saved",
        f"click Q | then: Results for you\nlearned: {TIP}",
        "yes: it looks done",
    )
    result = run_task("search the grocery aisles", shop_env(), laya, brain, FACTS, memory, yes)
    assert result.done and "brain check" in result.reason, result.trace
    assert memory.list() == []  # no know-how, no element, no place
    assert any("nothing kept" in line for line in result.trace)


def test_a_rescue_tip_from_a_task_that_does_not_finish_is_not_kept(tmp_path):
    memory = HarnessMemory(tmp_path / "m.sqlite")
    brain = FakeLineBrain(
        "click the search bar | then: Results for you\ndone: Basket saved",
        f"click Q | then: Results for you\nlearned: {TIP}",
        "no: nothing saved",
    )
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)), outcome=lambda e, s: ch("D7 outcome", (NOT_SHOWN, 0.9)))
    result = run_task("search the grocery aisles", shop_env(), laya, brain, FACTS, memory, yes)
    assert not result.done
    assert memory.list() == []


# ---------------------------------------------------------------- a remembered pick that fails is forgotten


def test_a_remembered_element_whose_step_fails_is_deleted_at_once(tmp_path):
    path = tmp_path / "m.sqlite"
    memory = HarnessMemory(path)
    memory.element_result("shop.pantrycart.com", "the add button", "Add to basket", True)
    home = page("Brave Browser", Item("Oat milk", "heading"), Item("Add to basket", BTN), Item("Add to list", BTN), url=SHOP)
    moved = page("Brave Browser", Item("Oat milk", "heading"), Item("Pick a size", "text"), url=SHOP)
    env = FakeEnv({"home": home, "moved": moved}, "home", {("home", "click Add to basket"): "moved"})
    brain = HarnessBrain(FakeLineBrain("click the add button | expect: new_item\ndone: new_item", "stop: cannot add it"), "v2")
    result = run_task("add oat milk to the basket", env, FakeLaya(), brain, FACTS, memory, yes)
    assert not result.done and env.log[0] == "click Add to basket"
    assert memory.element_for("shop.pantrycart.com", "the add button") is None
    assert HarnessMemory(path).element_for("shop.pantrycart.com", "the add button") is None  # on disk too
    assert "Add to basket" in memory.notes.tried(ELEMENT, "shop.pantrycart.com", "the add button")


def test_a_remembered_alias_or_place_used_by_a_failed_step_is_deleted():
    memory = HarnessMemory(":memory:")
    memory.remember_alias("mira", "Mira Okafor", "pantrycart.com")
    memory.place_result("clips", "clipstream.tv in Brave Browser, profile Work", True)
    tb = toolbox(clips_env(), memory=memory)
    tb.remembered("alias", "pantrycart.com", "mira", "Mira Okafor")
    tb.remembered("place", "", "clips", "clipstream.tv in Brave Browser, profile Work")
    tb.forget_used("unexpected")
    assert memory.alias_for("mira", "pantrycart.com") is None and memory.place_for("clips") is None
    assert tb.used == []


# ---------------------------------------------------------------- what is never kept as an element


def test_the_hygiene_filter():
    site = "pantrycart.com"
    assert unfit_element("Basket", site, chrome=True) == "the browser's own control"  # by structure, never by name
    assert unfit_element("Pantrycart", site) == "the site's own name"
    assert unfit_element("pantrycart.com", site) == "a web address"
    assert unfit_element("AD", site, accessible=False) == "not an accessible name (read off the pixels)"
    assert unfit_element("Q", site, accessible=True) == ""  # an exact accessible name may be short
    assert unfit_element("Add to basket", site) == ""
    assert unfit_element("Pantrycart", "Notes") == ""  # an app's item may share a word with a site

    memory = HarnessMemory(":memory:")
    assert memory.element_result(site, "the search box", "Search tabs ⇧⌘A", True, chrome=True) != ""
    assert memory.element_result(site, "detect my location", "Pantrycart", True) != ""
    assert memory.element_result(site, "add to cart", "AD", True, accessible=False) != ""
    assert memory.element_result(site, "add to cart", "Add to basket", True) == ""
    assert [(f.key, f.value) for f in memory.list()] == [("add to cart", "Add to basket")]


def test_a_chrome_pick_in_a_proven_task_is_not_kept():
    memory = HarnessMemory(":memory:")
    shop = page("Brave Browser", Item("Search tabs", BTN, chrome=True), Item("Aisles", "heading"), url=SHOP)
    tb = toolbox(FakeEnv({"a": shop}, "a"), memory=memory)
    scr = tb.look()
    tb.picked("the search box", scr.items[0], "Laya")
    tb.picked("the aisles", scr.items[1], "Laya")
    remember(memory, tb, [], proof=PROOF)
    assert [(f.app, f.key, f.value) for f in memory.list()] == [("shop.pantrycart.com", "the aisles", "Aisles")]
    assert any("not kept" in line for line in tb.trace)
