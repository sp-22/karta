"""The outcome contract (karta/harness/contract.py; harness.md, "The contract"): the plan's first line declares what the
task must achieve, code checks it at the end, and memory and pins learn only from a run whose declared contract was
met. Made-up apps and sites only; fakes for the screen, Laya and the brain.

- list(3, titles) takes a result's own title: never an author's link, a banner line or a form's labels;
- a comparison holds only with both values said, each from its own row;
- created holds by a count that rose; changed by the control's own state (its pane open is not enough);
- a look or rescue cannot swap the contract for something easier;
- a pinned replay whose contract fails unpins itself; a recipe from before contracts is dropped on load;
- nothing is remembered or pinned from a run with no contract.
"""

from __future__ import annotations

import json

from karta.harness import content, contract, loop
from karta.harness.memory import HarnessMemory
from karta.harness.recipes import RecipeBook
from karta.harness.tools import Collected
from karta.harness.types import Item, Screen
from tests.test_harness_carry import told
from tests.test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, page

BRAVE = "Brave Browser"
SHELF = "https://shelfwise.example"


def yes(_q: str) -> bool:
    return True


def results(*, banner_only: bool = False) -> Screen:
    """A made-up book search: each result's own page is linked by its title, its cover and its editions counter; the
    author's link repeats on every result; a banner line and a site link sit above the list."""
    items = [
        Item("Borrow free books online", "text", region="top"),  # a banner line, no link
        Item("Shelfwise", "link", url=SHELF + "/", region="top-left"),
    ]
    if not banner_only:
        for n, title in enumerate(("The Glass Orchard", "Salt and Lanterns", "A River of Clocks"), 1):
            work = f"{SHELF}/works/W{n}"
            items += [
                Item("Odile Varga", "link", url=f"{SHELF}/authors/A7"),
                Item(f"Cover of: {title}", "link", url=work),
                Item(title, "link", url=work),
                Item(f"{10 + n} editions", "link", url=work),
                Item("Fantasy", "link", url=f"{SHELF}/subjects/fantasy"),
            ]
    return Screen(BRAVE, items, url=f"{SHELF}/search?q=odile+varga", window="search | Shelfwise")


def rolled(scr: Screen) -> list[Collected]:
    """Every item on the screen as a collect would keep it, with its role."""
    role = content.roles(scr)
    return [Collected(i.name, i.url or "", role=role[id(i)]) for i in scr.page()]


# ---------------------------------------------------------------- answers


def test_list_of_titles_rejects_an_author_link_a_banner_line_and_form_labels():
    c = contract.parse("list(3, titles)")[0]
    scr = results()
    kept, why = contract.listed(c, rolled(scr))
    assert not why and [k.title for k in kept] == ["The Glass Orchard", "Salt and Lanterns", "A River of Clocks"]
    # only the frame of the page: a banner line and the author's link are no result's own title
    frame = [k for k in rolled(scr) if k.title in ("Borrow free books online", "Odile Varga", "11 editions", "Fantasy")]
    kept, why = contract.listed(c, frame)
    assert kept == [] and "only 0 of the 3 titles" in why
    # an event editor's labels: buttons and text of a sheet that holds a field
    sheet = Screen(
        "Daybook",
        [
            Item("Title", "field", value="Standup", dialog=True),
            Item("Add Alert, Repeat or Travel Time", "button", dialog=True),
            Item("Add Notes, URL or Attachments", "button", dialog=True),
            Item("Standup", "row"),
        ],
        window="Daybook",
    )
    kept, why = contract.listed(contract.parse("list(events)")[0], rolled(sheet))
    assert [k.title for k in kept] == ["Standup"] and not why


def test_a_titles_collect_takes_the_results_own_titles_and_the_tell_reads_them_with_no_call(monkeypatch):
    said: list[str] = []
    told(monkeypatch, said)
    env = FakeEnv({"r": results()}, "r")
    plan = "contract: list(3, titles)\ncollect the titles of the books | count: 3 | as: books\ntell the first three titles | from: books | as: answer"
    brain = FakeLineBrain(plan)
    result = loop.run_task("read me the first three titles", env, FakeLaya(), brain, FACTS)
    assert result.done and result.brain_calls == 1, result.trace  # a list contract is read out by code
    assert result.answer == "Three things: The Glass Orchard, Salt and Lanterns, and A River of Clocks."


def test_a_list_contract_on_a_page_of_banners_is_not_done(monkeypatch):
    told(monkeypatch, [])
    env = FakeEnv({"r": results(banner_only=True)}, "r")
    plan = "contract: list(3, titles)\ncollect the titles of the books | count: 3 | as: books\ntell the titles | from: books | as: list"
    result = loop.run_task("read me the first three titles", env, FakeLaya(), FakeLineBrain(plan, "stop: no books"), FACTS)
    assert not result.done and result.answer is None and "titles" in result.failed_why


def test_a_comparison_without_both_prices_is_not_done(monkeypatch):
    told(monkeypatch, [])
    shop = page(
        BRAVE,
        Item("Lumen Desk Lamp", "link", url="https://lampery.example/p/1", value="₹2,499"),
        Item("Brightline Arc Floor Lamp", "link", url="https://lampery.example/p/2", value="₹4,999"),
        url="https://lampery.example/search?q=lamps",
    )
    plan = (
        "contract: comparison(Lumen lamp vs Brightline Arc on price)\n"
        "collect lamp prices | as: lamps\ntell which is cheaper, the Lumen lamp or the Brightline Arc | from: lamps | as: answer"
    )
    one = "The Lumen Desk Lamp is cheaper at ₹2,499. [1]"  # the other side's price never said
    result = loop.run_task("which is cheaper, the Lumen lamp or the Brightline Arc", FakeEnv({"s": shop}, "s"), FakeLaya(),
                           FakeLineBrain(plan, one, "stop: nothing more"), FACTS)  # fmt: skip
    assert not result.done and "Brightline Arc" in result.failed_why, result.trace
    both = "The Lumen Desk Lamp is cheaper at ₹2,499; the Brightline Arc is ₹4,999. [1]"
    result = loop.run_task("which is cheaper, the Lumen lamp or the Brightline Arc", FakeEnv({"s": shop}, "s"), FakeLaya(),
                           FakeLineBrain(plan, both), FACTS)  # fmt: skip
    assert result.done and result.answer.startswith("The Lumen Desk Lamp is cheaper"), result.trace


def test_a_value_and_a_decision_hold_only_as_their_contract_says():
    facts, items = ["Opens at 09:30 on weekdays"], [Collected("Opens at 09:30 on weekdays", role="content")]
    value = contract.parse("value(time)")[0]
    assert contract.answer_check(value, "It opens at 9:30.", items, [1], facts) == ""
    assert "no time" in contract.answer_check(value, "It opens in the morning.", items, [1], facts)
    assert "none of the facts" in contract.answer_check(value, "It opens at 10:15.", items, [1], facts)
    decision = contract.parse("decision")[0]
    assert contract.answer_check(decision, "Yes, it opens before ten.", items, [1], facts) == ""
    assert "yes or no" in contract.answer_check(decision, "It opens before ten.", items, [1], facts)
    frame = [Collected("Opens at 09:30 on weekdays", role="interface")]
    assert "frame" in contract.answer_check(value, "It opens at 9:30.", frame, [1], facts)


def test_a_question_that_ends_with_done_is_told_from_what_it_read(monkeypatch):
    """A comparison is an answer: a plan whose look replaced its tell with a done line is told again, never "Done."."""
    told(monkeypatch, [])
    shop = page(
        BRAVE,
        Item("Lumen Desk Lamp", "link", url="https://lampery.example/p/1", value="₹2,499"),
        Item("Brightline Arc Floor Lamp", "link", url="https://lampery.example/p/2", value="₹4,999"),
        url="https://lampery.example/search?q=lamps",
    )
    plan = "contract: comparison(Lumen lamp vs Brightline Arc on price)\ncollect lamp prices | as: lamps\ndone: list_changed"
    reply = "The Lumen Desk Lamp is cheaper at ₹2,499; the Brightline Arc is ₹4,999. [1]"
    result = loop.run_task("Lumen lamp or Brightline Arc, which costs less", FakeEnv({"s": shop}, "s"), FakeLaya(),
                           FakeLineBrain(plan, reply), FACTS)  # fmt: skip
    assert result.done and result.answer and result.said != "Done.", result.trace


# ---------------------------------------------------------------- outcomes


def reminders(*, counted: bool = True) -> FakeEnv:
    """A made-up reminders app: the new reminder lands in a list the window does not show; only the count moves."""
    lists = [Item("Today, 4 reminders", "button"), Item("All, 14 reminders" if counted else "All", "button")]
    home = page("Remindly", *lists, Item("Add Reminder", "button"), Item("Water the ferns", "row"))
    editing = page(
        "Remindly", *lists, Item("New reminder", "field", focused=True), Item("Save", "button"), Item("Water the ferns", "row")
    )
    after = [Item("Today, 4 reminders", "button"), Item("All, 15 reminders" if counted else "All", "button")]
    saved = page(  # the list in front is another one: the new reminder shows only in the count
        "Remindly", *after, Item("New reminder", "field", value="call the bank"), Item("Water the ferns", "row")
    )
    return FakeEnv(
        {"home": home, "edit": editing, "saved": saved},
        "home",
        {("home", "click Add Reminder"): "edit", ("edit", "set New reminder=call the bank"): "saved"},  # saved as typed
    )


PLAN_REMINDER = (
    "contract: created(reminder named call the bank)\nclick Add Reminder | expect: editor_opened\n"
    "write call the bank | where: New reminder\ndone: new_item(call the bank)"
)


def test_created_holds_by_a_count_that_rose():
    result = loop.run_task("add a reminder call the bank", reminders(), FakeLaya(), FakeLineBrain(PLAN_REMINDER), FACTS,
                           confirm=yes)  # fmt: skip
    assert result.done and "a count went from 14 to 15" in result.reason, result.trace
    result = loop.run_task("add a reminder call the bank", reminders(counted=False), FakeLaya(),
                           FakeLineBrain(PLAN_REMINDER, "stop: it is not listed"), FACTS, confirm=yes)  # fmt: skip
    assert not result.done


def focus_settings() -> FakeEnv:
    home = page("Settings", Item("Focus", "row"), Item("Sound", "row"), Item("Wi-Fi", "row"))
    pane = page("Settings", Item("Focus", "heading"), Item("Do Not Disturb", "checkbox", checked=False), Item("Sound", "row"))
    on = page("Settings", Item("Focus", "heading"), Item("Do Not Disturb", "checkbox", checked=True), Item("Sound", "row"))
    for scr, title in ((home, "Settings"), (pane, "Focus"), (on, "Focus")):
        scr.window = title
    return FakeEnv(
        {"home": home, "pane": pane, "on": on}, "home", {("home", "click Focus"): "pane", ("pane", "click Do Not Disturb"): "on"}
    )


def test_changed_holds_by_the_switch_state_and_its_pane_open_is_not_enough():
    pane_only = "contract: changed(Do Not Disturb to on)\nclick Focus | expect: item_opened\ndone: item_opened(Focus)"
    result = loop.run_task("turn on do not disturb", focus_settings(), FakeLaya(), FakeLineBrain(pane_only, "stop: done"),
                           FACTS, confirm=yes)  # fmt: skip
    assert not result.done and "Do Not Disturb" in result.failed_why, result.trace
    flipped = pane_only + "\nclick Do Not Disturb | expect: toggled(Do Not Disturb)"
    result = loop.run_task("turn on do not disturb", focus_settings(), FakeLaya(), FakeLineBrain(flipped), FACTS, confirm=yes)
    assert result.done and "'Do Not Disturb' is on" in result.reason, result.trace


def test_a_look_cannot_weaken_the_contract():
    """The plan ran out with the switch off; the look answers with an easier contract and done line ("the pane is
    open"): neither is taken, and the task is not done."""
    plan = "contract: changed(Do Not Disturb to on)\nclick Focus | expect: item_opened\ndone: item_opened(Focus)"
    easier = "contract: opened(Focus)\ndone: item_opened"
    brain = FakeLineBrain(plan, easier, easier, "stop: nothing more")
    result = loop.run_task("turn on do not disturb", focus_settings(), FakeLaya(), brain, FACTS, confirm=yes)
    assert not result.done and "changed(Do Not Disturb to on)" in result.failed_why, result.trace
    assert any("Expected: the contract changed(Do Not Disturb to on)" in user for _sys, user in brain.asked[1:])


def test_opened_holds_by_the_objects_own_view():
    inbox = Screen("Postbox", [Item("Ana Ruiz, Lunch on Friday, see you there", "row")], window="Inbox")
    message = Screen("Postbox", [Item("Lunch on Friday", "heading"), Item("see you there", "text")], window="Inbox")
    assert contract.opened(inbox, message, "the latest email", ["Ana Ruiz, Lunch on Friday, see you there"])
    assert not contract.opened(inbox, inbox, "the latest email", ["Ana Ruiz, Lunch on Friday, see you there"])
    other = Screen("Postbox", [Item("Drafts", "heading")], window="Drafts")
    assert not contract.opened(inbox, other, "the latest email", ["Ana Ruiz, Lunch on Friday, see you there"])


# ---------------------------------------------------------------- what is learned, and what re-checks itself


def test_nothing_is_remembered_or_pinned_from_a_run_with_no_contract():
    flipped = "click Focus | expect: item_opened\nclick Do Not Disturb | expect: toggled(Do Not Disturb)\ndone: toggled"
    memory, book = HarnessMemory(":memory:"), RecipeBook()
    result = loop.run_task("turn on do not disturb", focus_settings(), FakeLaya(), FakeLineBrain(flipped), FACTS, memory,
                           confirm=yes, pins=book)  # fmt: skip
    assert result.done and "no contract was declared" in "\n".join(result.trace)
    assert not memory.list() and not book.recipes
    declared = "contract: changed(Do Not Disturb to on)\n" + flipped
    result = loop.run_task("turn on do not disturb", focus_settings(), FakeLaya(), FakeLineBrain(declared), FACTS, memory,
                           confirm=yes, pins=book)  # fmt: skip
    assert result.done and book.recipes and book.recipes[0].contract == "changed(Do Not Disturb to on)"
    proofs = {p for (p,) in memory.db.execute("select proof from harness_facts")}
    assert proofs == {"changed(Do Not Disturb to on)"}  # each row keeps the contract that proved it


def test_a_pinned_replay_whose_contract_fails_unpins_itself(monkeypatch):
    told(monkeypatch, [])
    book = RecipeBook()
    plan = "contract: list(3, titles)\ncollect the titles of the books | count: 3 | as: books\ntell the titles | from: books | as: list"
    first = loop.run_task("read me the first three titles", FakeEnv({"r": results()}, "r"), FakeLaya(), FakeLineBrain(plan),
                          FACTS, pins=book)  # fmt: skip
    assert first.done and first.pinned.startswith("pinned r1") and book.get("r1").contract == "list({p1}, titles)"
    again = loop.run_task("read me the first three titles", FakeEnv({"r": results(banner_only=True)}, "r"), FakeLaya(),
                          FakeLineBrain("stop: no books here"), FACTS, pins=book)  # fmt: skip
    assert again.replayed == "exact" and not again.done
    assert "failed its contract" in again.pinned and "unpinned" in again.pinned and book.get("r1") is None


def test_pins_from_before_contracts_are_dropped_once_on_load(tmp_path):
    path = tmp_path / "pins.json"
    import time

    now = time.time()
    old = {"template": "add a reminder {p1}", "params": ["p1"], "values": {"p1": "x"}, "steps": [], "proofs": [],
           "created": now, "used": now}  # fmt: skip
    path.write_text(
        json.dumps(
            {"last": "r1", "next": 3, "recipes": [{"id": "r1", **old}, {"id": "r2", **old, "contract": "created(reminder)"}]}
        )
    )
    book = RecipeBook(path)
    assert [r.id for r in book.recipes] == ["r2"] and book.dropped == 1
    assert json.loads(path.read_text())["version"] == 2 and RecipeBook(path).dropped == 0


def test_the_plan_declares_it_and_code_keeps_it_as_written():
    from karta.harness.brain import HarnessBrain

    hb = HarnessBrain(
        FakeLineBrain("contract: list(3 title)\ncollect the titles | count: 3 | as: t\ntell them | from: t | as: list")
    )
    stream = hb.stream_plan("read me three titles", FACTS, page("Shelfwise"))
    list(stream)
    assert stream.plan.contract == "list(3, titles)"
    assert contract.parse("frob(x)")[0] is None and contract.parse("list(3, authors)")[0] is None
    guessed = HarnessBrain(FakeLineBrain("contract: created(note named Weekly Ledger)\nclick New | expect: new_item"))
    stream = guessed.stream_plan("make a note", FACTS, page("Pebble"))
    list(stream)
    assert stream.plan.contract == "created(note)"  # a name the task never said is a guess: dropped
