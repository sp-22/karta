"""A question ends with a verified spoken answer, values come from their own row, and a search that Return did not
run is sent by its form's own button (harness.md, "Carrying data between steps", "Ending a task", "Tools"). Made-up
apps and sites only.

- a task that asks for information is done only once an answer is said: never a bare "Done.";
- the answer cites the facts it rests on, and a number none of them holds is no answer;
- a definition the collect missed is read from the page's main text before the task gives up on it;
- "the first N" of a list are its top's, not the scrolled viewport's; the same result is kept once;
- a price comes from its own row, an accessory after it; a price not on the results is read on the item's page;
- a comparison contract is compared by code, and holds only with both values said; "the link of this page" is the
  page's own address;
- the form's own button sends a search Return did not run, and never a message.
"""

from __future__ import annotations

from types import SimpleNamespace

from karta.harness import loop, tell
from karta.harness.decide import FITS, NONE, OTHER
from karta.harness.tools import Collected
from karta.harness.types import Item, Outcome, Screen
from tests.test_harness_carry import collect_env, told
from tests.test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, ch, page, toolbox
from tests.test_reading import GRID, At

BRAVE = "Brave Browser"
LONG = (
    "quillet\n"
    "Etymology: from an old word for a small reed.\n"
    "Noun\n"
    "1. A short pen cut from a reed, used for writing on wax tablets. "
    "2. (by extension) Any small writing tool. " + "Usage notes follow here for a while. " * 10
)


def hb_of(*replies: str):
    brain = FakeLineBrain(*replies)
    return SimpleNamespace(lines=True, calls=0, requests=0, seconds=0.0, stats=[], brain=brain), brain


# ---------------------------------------------------------------- a question ends with its answer said


def mugs() -> FakeEnv:
    shelf = Screen(
        BRAVE,
        [Item("Shelf", "heading"), Item("Pebble mug", "link", url="https://potterly.example/p/1")],
        url="https://potterly.example/shelf",
        window="Shelf",
    )
    mug = Screen(
        BRAVE,
        [Item("Pebble mug", "heading"), Item("Comes in three sizes: small, medium and large", "text")],
        url="https://potterly.example/p/1",
        window="Pebble mug",
    )
    return FakeEnv({"shelf": shelf, "mug": mug}, "shelf", {("shelf", "click Pebble mug"): "mug"})


PLAN_NO_TELL = (
    "click Pebble mug | expect: item_opened\n"
    "collect the sizes the mug comes in | count: 1 | as: sizes\n"
    "done: item_opened(Pebble mug)"
)


def test_a_question_whose_plan_lost_its_tell_is_told_from_what_was_collected(monkeypatch):
    said: list[str] = []
    told(monkeypatch, said)
    brain = FakeLineBrain(PLAN_NO_TELL, "It comes in small, medium and large. [1]")
    result = loop.run_task("how many sizes does the Pebble mug come in?", mugs(), FakeLaya(), brain, FACTS)
    assert result.done and result.answer == "It comes in small, medium and large.", result.trace
    assert result.said != "Done." and said[0] == result.answer
    assert any("unanswered" in line for line in result.trace)


def test_a_question_that_ends_without_an_answer_is_not_done(monkeypatch):
    told(monkeypatch, [])
    brain = FakeLineBrain(PLAN_NO_TELL, "NOT FOUND", "stop: the page does not say")
    result = loop.run_task("how many sizes does the Pebble mug come in?", mugs(), FakeLaya(), brain, FACTS)
    assert not result.done and result.answer is None and result.said != "Done.", result.trace
    nothing = FakeLineBrain("click Pebble mug | expect: item_opened\ndone: item_opened(Pebble mug)")
    result = loop.run_task("how many sizes does the Pebble mug come in?", mugs(), FakeLaya(), nothing, FACTS)
    assert not result.done and result.answer is None and "Done" not in result.said, result.trace


def test_a_task_that_asks_nothing_still_ends_done():
    brain = FakeLineBrain("click Pebble mug | expect: item_opened\ndone: item_opened(Pebble mug)")
    result = loop.run_task("open the Pebble mug on potterly", mugs(), FakeLaya(), brain, FACTS)
    assert result.done and result.said.startswith("Done"), result.trace


# ---------------------------------------------------------------- the answer cites its facts, and adds none


def test_an_answer_cites_its_facts_and_the_citations_are_never_said():
    hb, brain = hb_of("A quillet is a short reed pen. [2]")
    cites: list[int] = []
    text, used = tell.word_answer("the first definition of quillet", ["Site logo", "1. A short reed pen"], hb, cites=cites)
    assert (text, used, cites) == ("A quillet is a short reed pen.", True, [2])
    assert "[1] Site logo" in brain.asked[0][1] and "[2] A short reed pen" in brain.asked[0][1]


def test_an_answer_with_a_number_no_fact_holds_or_a_fact_that_is_not_there_is_no_answer():
    facts = ["Lumen Desk Lamp — ₹2,499"]
    hb, _ = hb_of("It costs 1,999 rupees. [1]")
    assert tell.word_answer("what does the lamp cost", facts, hb)[0] == tell.NOT_FOUND
    hb, _ = hb_of("It costs 2,499 rupees. [4]")
    assert tell.word_answer("what does the lamp cost", facts, hb)[0] == tell.NOT_FOUND
    hb, _ = hb_of("It costs 2,499 rupees. [1]")
    assert tell.word_answer("what does the lamp cost", facts, hb)[0] == "It costs 2,499 rupees."
    hb, _ = hb_of("There are 3 of them.")  # a count of the facts is the facts' own
    assert tell.word_answer("how many", ["a", "b", "c"], hb)[0] == "There are 3 of them."


def test_the_prompt_lets_a_fact_holding_the_thing_answer_it():
    assert "a definition, a sentence" in tell.SYSTEM and "need not name it" in tell.SYSTEM


class Dictionary(FakeEnv):
    def page_text(self) -> str:
        return LONG


def test_a_definition_the_collect_missed_is_read_from_the_pages_main_text(monkeypatch):
    told(monkeypatch, [])
    entry = Screen(BRAVE, [Item("A wiki anyone can edit", "text")], url="https://words.example/wiki/quillet")
    tb = toolbox(Dictionary({"a": entry}, "a"))
    tb.slots["meaning"], tb.last_slot = [Collected("A wiki anyone can edit")], "meaning"
    hb, brain = hb_of("NOT FOUND", "Its first definition is a short pen cut from a reed. [1]")
    step = {"tool": "tell", "question": "the first definition of quillet", "slot": "meaning", "form": "answer"}
    result, text, _ = loop.tell(tb, hb, step)
    assert result.ok and text == "Its first definition is a short pen cut from a reed.", tb.trace
    assert "the page's main text" in result.detail and len(brain.asked) == 2
    assert "short pen cut from a reed" in brain.asked[1][1]


# ---------------------------------------------------------------- the first N, from the list's top, each once


class Scrolled(FakeEnv):
    """A tab left scrolled down: its scroll bar moves to the top on request."""

    tops = 0

    def scroll_top(self) -> bool:
        self.tops += 1
        if self.at != "down":
            return False
        self.at = "top"
        return True


def board() -> Scrolled:
    def story(n: int, title: str) -> Item:
        return Item(title, "link", url=f"https://board.example/s/{n}")

    top = Screen(BRAVE, [story(1, "Otters learn to juggle pebbles"), story(2, "A lighthouse built of glass")],
                 url="https://board.example/")  # fmt: skip
    down = Screen(BRAVE, [story(11, "Quiet streets of a harbour town"), story(12, "Kites over the salt flats")],
                  url="https://board.example/")  # fmt: skip
    return Scrolled({"top": top, "down": down}, "down", {("down", "scroll down"): "down"})


def test_the_top_story_is_the_lists_first_not_the_viewports():
    env = board()
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the title of the top story", 1, "top").ok
    assert [c.title for c in tb.slots["top"]] == ["Otters learn to juggle pebbles"], tb.trace


def test_a_page_the_task_scrolled_itself_is_read_where_it_is():
    env = board()
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    tb.look()
    tb.do("scroll down", env.scroll, "down")
    assert tb.tool_collect("the title of the story shown", 1, "top").ok
    assert env.tops == 0 and tb.slots["top"][0].title == "Quiet streets of a harbour town"


def test_the_same_result_twice_is_kept_once():
    shelf = Screen(
        BRAVE,
        [
            Item("Cover of: The Glass Orchard", "text"),
            Item("The Glass Orchard Mara Quill 1981", "row"),
            Item("Cover of: The Salt Atlas", "text"),
            Item("The Salt Atlas Mara Quill 1990", "row"),
            Item("The Paper Tide Mara Quill 1994", "row"),
        ],
    )
    tb = toolbox(collect_env(shelf), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the first three book titles", 3, "books").ok
    assert [c.title for c in tb.slots["books"]] == [
        "The Glass Orchard Mara Quill 1981",
        "The Salt Atlas Mara Quill 1990",
        "The Paper Tide Mara Quill 1994",
    ], tb.trace


def test_a_thumbnails_link_is_never_a_title_beside_the_title_link():
    works = "https://books.example/works/7/the-glass-orchard"
    shelf = Screen(
        BRAVE,
        [
            Item("Cover of edition glassorchard00", "image"),
            Item("Cover of edition glassorchard00", "link", url=works + "?edition=glassorchard00"),
            Item("The Glass Orchard", "link", url=works),
            Item("The Salt Atlas", "link", url="https://books.example/works/9/the-salt-atlas"),
        ],
        url="https://books.example/search?q=quill",
    )
    tb = toolbox(collect_env(shelf), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the titles of the first two books", 2, "books").ok
    assert [c.title for c in tb.slots["books"]] == ["The Glass Orchard", "The Salt Atlas"], tb.trace


# ---------------------------------------------------------------- values tied to their row


def test_a_price_comes_from_its_own_row_with_an_accessory_row_beside_it():
    def collect_fit(what, item):  # Laya, made up: a shade cover is made for the lamp, it is not the lamp
        return ch("D9 collect", (OTHER, 0.95) if "cover" in item.name else (FITS, 0.95))

    tb = toolbox(collect_env(GRID), FakeLaya(collect_fit=collect_fit))
    assert tb.tool_collect("the price of the Lumen Desk Lamp", 1, "price").ok
    assert [(c.title, c.value) for c in tb.slots["price"]] == [("Lumen Desk Lamp 2, warm white, dimmable", "₹2,499")]


def test_a_price_not_on_the_results_is_read_on_the_items_own_page():
    results = Screen(
        BRAVE,
        [
            Item("Lumen Desk Lamp 2, warm white, dimmable", "link", url="https://shop.example/p/2"),
            Item("See options", "link", url="https://shop.example/p/2#options"),
            Item("Desk organiser tray, bamboo", "link", url="https://shop.example/p/8"),
            Item("₹349", "text"),
        ],
        url="https://shop.example/search?q=lumen+desk+lamp",
    )
    product = Screen(
        BRAVE,
        [
            Item("Lumen Desk Lamp 2", "heading"),
            Item("Warm white, from ₹2,499", "checkbox"),  # an option's price: the item's own
            Item("Cool white, from ₹2,699", "checkbox"),
        ],
        url="https://shop.example/p/2",
        window="Lumen Desk Lamp 2, warm white - Shop",
    )
    env = FakeEnv(
        {"results": results, "product": product},
        "results",
        {("results", "click Lumen Desk Lamp 2, warm white, dimmable"): "product", ("product", "back"): "results"},
    )
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    result = tb.tool_collect("the price of the Lumen Desk Lamp", 1, "price")
    assert result.ok and "own page" in result.detail, tb.trace
    (got,) = tb.slots["price"]
    assert got.value == "₹2,499" and "Lumen Desk Lamp" in got.title and "₹349" not in got.text()
    assert env.at == "results" and env.log[-1] == "back"  # read there, then back to the list


def test_a_comparison_is_compared_by_code_after_a_search_each(monkeypatch):
    from karta.harness import contract

    told(monkeypatch, [])
    tb = toolbox(FakeEnv({"a": page("Shop")}, "a"))
    tb.contract = contract.parse("comparison(Lumen lamp vs Brightline Arc on price)")[0]
    tb.slots["lamp"] = [Collected("Lumen Desk Lamp 2", value="₹2,499", titled=True, role="title")]
    tb.slots["arc"] = [Collected("Brightline Arc Floor Lamp", value="₹4,999", titled=True, role="title")]
    hb, brain = hb_of("The Lumen Desk Lamp is cheaper at 2,499 rupees; the Brightline Arc costs 4,999. [1]")
    step = {"tool": "tell", "question": "which is cheaper, the Lumen lamp or the Brightline Arc", "slot": "lamp"}
    result, text, _ = loop.tell(tb, hb, step)
    assert result.ok and text.startswith("The Lumen Desk Lamp is cheaper"), tb.trace
    asked = brain.asked[0][1]
    assert "lowest: Lumen Desk Lamp 2 at ₹2,499" in asked and "highest: Brightline Arc Floor Lamp at ₹4,999" in asked


def test_the_link_of_this_page_is_its_own_address():
    recipe = Screen(
        BRAVE,
        [Item("Herb tart", "heading"), Item("More tarts", "link", url="https://cook.example/tarts")],
        url="https://cook.example/r/2231-herb-tart",
        window="Herb tart - Cook",
    )
    tb = toolbox(FakeEnv({"r": recipe}, "r"))
    assert tb.tool_collect("the address link of this recipe page", 1, "link").ok
    assert tb.slots["link"] == [Collected("Herb tart - Cook", "https://cook.example/r/2231-herb-tart")]


def test_a_second_pages_items_under_the_same_name_are_added_once_and_a_retry_replaces():
    first = Screen(BRAVE, [Item("Feed the starter twice a day at room warmth", "text")], url="https://bake.example/a")
    second = Screen(BRAVE, [Item("Keep the starter in the fridge between bakes", "text")], url="https://bake.example/b")
    env = FakeEnv({"a": first, "b": second}, "a")
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the main points of this article", 1, "points").ok
    env.at = "b"
    assert tb.tool_collect("the main points of this article", 1, "points").ok
    assert len(tb.slots["points"]) == 2  # two articles read in turn
    tb.redo.add("points")  # they did not answer the tell: collected again, they replace
    assert tb.tool_collect("the main points of this article", 1, "points").ok
    assert len(tb.slots["points"]) == 1


# ---------------------------------------------------------------- a search Return did not run


def search_form(box_name: str, laya=None):
    form = (1, 2, 5)  # app, window, the search form
    box = Item(box_name, "field", value="", focused=True, ref=At(400, 20, 300, 30, (*form, 50)))
    go = Item("Go", "button", hint=True, ref=At(710, 20, 30, 30, (*form, 51)))
    verb = Item("New project", "button", ref=At(900, 20, 120, 30, (1, 2, 6, 60)))  # a page verb on the same bar
    home = Screen(BRAVE, [box, go, verb, Item("Explore", "heading")], url="https://forge.example/explore")
    results = Screen(BRAVE, [Item("markdown-quill", "link", url="https://forge.example/r/1")],
                     url="https://forge.example/explore?q=markdown")  # fmt: skip
    env = FakeEnv({"home": home, "results": results}, "home", {("home", "click Go"): "results"})
    tb = toolbox(env, laya or FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9))))
    return env, tb


def test_a_search_return_did_not_run_is_sent_by_its_forms_own_button():
    env, tb = search_form("Search projects…")
    assert tb.tool_write("markdown", "Search projects…").ok
    tb.by_type = True  # the step declares its kind of change: the loop verifies it
    result = tb.tool_submit("results_for(markdown)")
    assert result.ok and "click Go" in env.log and "click New project" not in env.log, tb.trace
    assert env.log.index("press return") < env.log.index("click Go")


def test_a_send_return_did_not_send_is_never_pressed_a_second_way():
    env, tb = search_form("Write a message")
    assert tb.tool_write("see you at noon", "Write a message").ok
    result = tb.tool_submit("see you at noon")
    assert not result.ok and result.outcome is not Outcome.OK
    assert not any(a.startswith("click") for a in env.log), env.log
