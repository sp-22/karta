"""Typed checks that missed a real change, or met a false one (harness.md, "Typed checks"): a toggle button that
renames itself, a new item measured from the step that made it and in a list that was redrawn, a row renamed in place
that is no new item, and a link that opens a page on another site. Made-up apps and sites only."""

from __future__ import annotations

from datetime import datetime

from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness.expect import Expect, verify
from karta.harness.loop import run_task
from karta.harness.mac import Ref
from karta.harness.types import Item, MacFacts, Screen

FACTS = MacFacts(["Taskdeck", "Finder"], {}, [], datetime(2026, 10, 6, 9, 0))


def at(x: float, y: float) -> Ref:
    return Ref(x, y, 80, 20)


def test_a_toggle_button_that_renames_itself_in_place_is_toggled():
    before = page(
        "Brave Browser", Item("Pin", BTN, ref=at(500, 90)), Item("Readme", "heading"), url="https://forge.example/ada/inkpad"
    )
    after = page(
        "Brave Browser", Item("Unpin", BTN, ref=at(500, 90)), Item("Readme", "heading"), url="https://forge.example/ada/inkpad"
    )
    assert verify(Expect("toggled"), before, after).ok
    assert verify(Expect("toggled", "Pin"), before, after).ok
    moved = page(
        "Brave Browser", Item("Unpin", BTN, ref=at(20, 400)), Item("Readme", "heading"), url="https://forge.example/ada/inkpad"
    )
    assert not verify(Expect("toggled"), before, moved).ok  # another control somewhere else: no toggle
    no_frames = (page("Notely", Item("Follow", BTN)), page("Notely", Item("Unfollow", BTN)))
    assert verify(Expect("toggled"), *no_frames).ok
    unrelated = (page("Notely", Item("Edit", BTN)), page("Notely", Item("Share", BTN)))
    assert not verify(Expect("toggled"), *unrelated).ok


def test_a_row_renamed_in_place_is_no_new_item():
    before = page("Taskdeck", Item("Inbox", "row", ref=at(10, 100)), Item("Today", "row", ref=at(10, 130)))
    after = page("Taskdeck", Item("Inbox", "row", ref=at(10, 100)), Item("Pay rent", "row", ref=at(10, 130)))
    assert not verify(Expect("new_item", "Pay rent"), before, after).ok
    added = page("Taskdeck", *before.items, Item("Pay rent", "row", ref=at(10, 160)))
    assert verify(Expect("new_item", "Pay rent"), before, added).ok


def test_a_new_item_in_a_list_that_was_redrawn_is_met():
    """A view that redrew most of its items (another day's events read afresh) still shows the new event."""
    before = Screen("Planbook", [Item(f"{h}:00", "text") for h in range(9, 17)], window="Planbook")
    after = Screen(
        "Planbook", [*(Item(f"{h} h", "text") for h in range(9, 17)), Item("Team lunch, 13:00", "row")], window="Planbook"
    )
    assert verify(Expect("new_item", "Team lunch"), before, after).ok
    already = Screen("Planbook", [*before.items, Item("Team lunch", "text")], window="Planbook")
    assert not verify(Expect("new_item", "Team lunch"), already, after).ok  # it was there before


def test_a_link_that_opens_a_page_on_another_site_is_item_opened():
    results = page("Brave Browser", Item("Keep a starter alive", "link"), url="https://findit.example/search?q=starter")
    article = page("Brave Browser", Item("Keep a starter alive", "heading"), url="https://breadnotes.example/keep")
    assert verify(Expect("item_opened"), results, article, tool="click", link=True).ok
    assert not verify(Expect("item_opened"), results, article, tool="click").ok  # a button that left the site: no


def test_done_new_item_is_measured_from_the_step_that_made_the_row():
    """The click added the row and the write named it: measured only from just before the write, the row looked
    renamed, not new."""
    rows = [Item("Pay the rent", "row", ref=at(10, 100)), Item("Add task", BTN, ref=at(10, 300))]
    pages = {
        "list": page("Taskdeck", *rows),
        "new": page("Taskdeck", *rows, Item("New task", "row", ref=at(10, 130)), Item("title", "field", focused=True, ref=at(90, 130))),
        "named": page("Taskdeck", *rows, Item("water the ferns", "row", ref=at(10, 130)), Item("title", "field", "water the ferns", ref=at(90, 130))),
    }  # fmt: skip
    env = FakeEnv(pages, "list", {("list", "click Add task"): "new", ("new", "set title=water the ferns"): "named"})
    brain = FakeLineBrain(
        "click Add task | expect: new_item\nwrite water the ferns | where: title\ndone: new_item(water the ferns)"
    )
    result = run_task("add a task water the ferns in taskdeck", env, FakeLaya(), brain, FACTS)
    assert result.done and result.rescues == 0, result.trace
