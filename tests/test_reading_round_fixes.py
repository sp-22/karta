"""What a live run of reading tasks showed, held on made-up apps and sites (harness.md, "Pinning", "Tools",
"Carrying data between steps", "Typed checks", "Ending a task"):

- a question's pinned path that answers nothing is never pinned nor replayed;
- a submit with nothing written and no field focused presses no blind Return, so it asks nothing;
- back is proven by the address; a tab with no page before it opens the page before by its address;
- an item's own page is read as it loads and further down before the collect gives up on it;
- an answer's numbers count in any locale's format, a Roman numeral too; the page's text is asked once a question;
- a saved draft is proven by a row holding its typed title where it is listed;
- a result's own main link is its name, never its owner, its counters, a timestamp or a thumbnail's label;
- the end of the plan's fixes count toward its looks; a box that renamed itself is the one a submit returns into.
"""

from __future__ import annotations

import pytest

from karta.harness import expect, loop, recipes, tell
from karta.harness.decide import FITS
from karta.harness.tools import Collected
from karta.harness.types import Item, Outcome, Screen
from tests.test_accuracy_answers import hb_of
from tests.test_harness_auto import auto, literal, quillery
from tests.test_harness_carry import collect_env, told
from tests.test_harness_tools import FACTS, FakeEnv, FakeLaya, ch, page, toolbox

BRAVE = "Brave Browser"


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    monkeypatch.setattr(loop, "speak", lambda text: None)


# ---------------------------------------------------------------- a question's path holds its answer


def test_a_path_with_no_contract_is_never_pinned_nor_replayed():
    """A question's path is pinned only with the contract its answer met (an answer contract holds only by a tell):
    a run, or a recipe from before contracts, with none is never pinned nor replayed."""
    book = recipes.RecipeBook()
    book.typed = False  # these made-up paths have no typed steps: only the contract's rule is held here
    steps = [{"tool": "open_place", "what": "Tidewatch"}, {"tool": "find", "text": "Lowmoor", "meaning": "the place"}]
    trail = [(s, {"kind": "ran"}) for s in steps]
    assert book.after("What is the tide at Lowmoor?", None, True, True, trail, "") == ("", "")
    assert not book.recipes
    old = recipes.Recipe("r1", "What is the tide at {p1}?", ["p1"], {"p1": "Lowmoor"}, steps, [{}, {}])
    book.recipes.append(old)  # pinned before contracts
    assert book.match("What is the tide at Lowmoor?") is None
    told_too = recipes.Recipe("r2", "What is the swell at {p1}?", ["p1"], {"p1": "Lowmoor"},
                              [*steps, {"tool": "tell", "question": "the swell", "slot": "s"}], [{}, {}, {}],
                              contract="value(number)")  # fmt: skip
    book.recipes.append(told_too)
    assert book.match("What is the swell at Lowmoor?") is not None


def test_a_submit_with_nothing_written_and_no_field_focused_asks_nothing():
    asked = []
    env = FakeEnv({"a": page(BRAVE, Item("Local time: 14:02", "text"), url="https://clocks.example/lowmoor")}, "a")
    tb = toolbox(env, confirm=lambda q: asked.append(q) or False)
    result = tb.tool_submit("")
    assert result.outcome is Outcome.NO_FIELD and not asked and "press return" not in env.log


# ---------------------------------------------------------------- back, proven by the address


def test_back_that_does_not_leave_a_new_tab_opens_the_page_before_by_its_address():
    results = page(BRAVE, Item("Starter care, day by day", "link"), url="https://bake.example/search?q=starter")
    article = page(BRAVE, Item("Feed it twice a day", "text"), url="https://crumbs.example/starter-care")
    env = FakeEnv(
        {"results": results, "article": article},
        "results",
        {("article", "press return"): "results"},  # back does nothing: the link opened a new tab
    )
    tb = toolbox(env)
    tb.look()
    env.at = "article"
    assert tb.tool_back().ok, tb.trace
    assert env.at == "results" and "type https://bake.example/search?q=starter" in env.log


def test_back_that_moves_needs_nothing_more():
    env = FakeEnv(
        {
            "results": page(BRAVE, Item("a", "text"), url="https://bake.example/search?q=starter"),
            "article": page(BRAVE, Item("b", "text"), url="https://crumbs.example/starter-care"),
        },
        "results",
        {("article", "back"): "results"},
    )
    tb = toolbox(env)
    tb.look()
    env.at = "article"
    assert tb.tool_back().ok and env.log == ["back"]


def test_an_items_own_page_is_read_further_down_before_the_collect_gives_up():
    results = Screen(
        BRAVE,
        [Item("Lumen Desk Lamp 2, warm white", "link", url="https://shop.example/p/2"), Item("4.5 stars", "text")],
        url="https://shop.example/search?q=lumen+desk+lamp",
    )
    top = Screen(BRAVE, [Item("Lumen Desk Lamp 2", "heading"), Item("Ships in two days", "text")],
                 url="https://shop.example/p/2", window="Lumen Desk Lamp 2")  # fmt: skip
    lower = Screen(BRAVE, [Item("Lumen Desk Lamp 2", "heading"), Item("Warm white, from ₹2,499", "checkbox")],
                   url="https://shop.example/p/2", window="Lumen Desk Lamp 2")  # fmt: skip
    env = FakeEnv(
        {"results": results, "top": top, "lower": lower},
        "results",
        {
            ("results", "click Lumen Desk Lamp 2, warm white"): "top",
            ("top", "scroll down"): "lower",
            ("lower", "back"): "results",
        },
    )
    tb = toolbox(env, FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    result = tb.tool_collect("the price of the Lumen Desk Lamp", 1, "price")
    assert result.ok and tb.slots["price"][0].value == "₹2,499", tb.trace
    assert env.at == "results"


# ---------------------------------------------------------------- the answer's own check


def test_an_answers_numbers_count_in_any_format_and_a_roman_numeral_too():
    fact = ["La ciudadela, construida antes del siglo XV, está a 2430 m sobre el mar."]
    assert tell.cited("The citadel was built before the 15th century, at 2,430 metres. [1]", fact) is not None
    assert tell.cited("It stands at 2 430 metres. [1]", fact) is not None
    assert tell.cited("It stands at 3,100 metres. [1]", fact) is None  # a number the fact never says


def test_the_pages_text_is_asked_once_a_question(monkeypatch):
    told(monkeypatch, [])

    class Article(FakeEnv):
        def page_text(self) -> str:
            return "A long article about tides, read whole. " * 10

    tb = toolbox(Article({"a": Screen(BRAVE, [Item("Tides", "heading")], url="https://sea.example/t", window="Tides")}, "a"))
    tb.slots["bits"], tb.last_slot = [Collected("Subscribe for more")], "bits"
    hb, brain = hb_of("NOT FOUND", "NOT FOUND", "NOT FOUND", "NOT FOUND")
    step = {"tool": "tell", "question": "summarise the top story", "slot": "bits", "form": "answer"}
    assert not loop.tell(tb, hb, step)[0].ok
    assert len(brain.asked) == 2 and "read on the page 'Tides'" in brain.asked[1][1]
    assert not loop.tell(tb, hb, step)[0].ok
    assert len(brain.asked) == 3 and any("not asked again" in line for line in tb.trace)


# ---------------------------------------------------------------- a saved draft, where it is listed


def test_a_saved_draft_is_proven_by_a_row_holding_its_typed_title_where_it_is_listed():
    mail = [Item(f"Message {n} from a friend", "row") for n in range(8)]
    inbox = Screen(BRAVE, [Item("Compose", "button"), *mail], url="https://post.example/mail/", window="Inbox - Post")
    drafts = Screen(BRAVE, [Item("Compose", "button"), Item("Draft Quarterly plan - hello", "row")],
                    url="https://post.example/mail/", window="Drafts - Post")  # fmt: skip
    got = expect.Expect("new_item", "Quarterly plan")
    assert expect.verify(got, inbox, drafts, {"Quarterly plan"}).ok
    assert not expect.verify(got, inbox, drafts, set()).ok  # words the plan never typed prove nothing there
    other = Screen("Notes", [Item("Draft Quarterly plan - hello", "row")], window="Drafts")
    assert not expect.verify(got, inbox, other, {"Quarterly plan"}).ok  # another place


# ---------------------------------------------------------------- a result's own main link


FORGE = "https://forge.example"


def forge_row(owner: str, repo: str, stars: str, blurb: str) -> list[Item]:
    return [
        Item(repo, "link", url=f"{FORGE}/{owner}/{repo}"),
        Item(stars, "link", url=f"{FORGE}/{owner}/{repo}/stars"),
        Item("0 forks", "link", url=f"{FORGE}/{owner}/{repo}/forks"),
        Item(owner, "link", url=f"{FORGE}/{owner}"),
        Item(f"{blurb} Updated 9 minutes ago", "text"),
    ]


def test_a_results_name_is_its_main_link_never_its_owner_counters_or_a_timestamp():
    results = Screen(
        BRAVE,
        [
            Item("Explore", "link", url=f"{FORGE}/explore"),
            *forge_row("mira", "inkwell", "12 stars", "A small editor for plain notes"),
            *forge_row("tomas", "papermill", "3 stars", "Turns notes into a site"),
        ],
        url=f"{FORGE}/explore/repos?q=notes+editor",
    )
    tb = toolbox(collect_env(results), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("names of the top two repositories", 2, "repos").ok
    assert [c.title for c in tb.slots["repos"]] == ["inkwell", "papermill"], tb.trace


def test_a_covers_link_is_never_a_title_even_when_the_title_link_is_off_screen():
    works = "https://books.example/works/7/the-glass-orchard"
    shelf = Screen(
        BRAVE,
        [
            Item("The Glass Orchard", "link", url=works, offscreen=True),
            Item("Cover of edition glassorchard00", "image"),
            Item("Cover of edition glassorchard00", "link", url=works + "?edition=glassorchard00"),
            Item("The Salt Atlas", "link", url="https://books.example/works/9/the-salt-atlas"),
        ],
        url="https://books.example/search?q=quill",
    )
    tb = toolbox(collect_env(shelf), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the titles of the first books", 2, "books").ok
    assert [c.title for c in tb.slots["books"]] == ["The Salt Atlas"], tb.trace


# ---------------------------------------------------------------- bounds and boxes


def test_the_end_of_the_plans_fixes_count_toward_its_looks():
    env = quillery()
    env.pages["new"].items.append(Item("Tidy", "button", region="bottom"))  # passes, so no stall stops the looks
    plan = "click Add Note | expect: editor_opened\nwrite buy oat milk | where: Note text\ndone: new_item(buy oat milk)"
    looks = [
        f"click Tidy | expect: none\nclick {name} | expect: new_item\ncontinue" for name in ("Pencil", "Quill", "Feather", "Nib")
    ]
    hb, brain = auto(plan, *looks)
    result = loop.run_task("add a note buy oat milk in quillery", env, literal(), hb, FACTS)
    assert not result.done and brain.calls == 1 + loop.MAX_STEP_LOOKS, result.trace


def test_a_box_that_renamed_itself_is_the_one_a_submit_returns_into():
    def typed(env):  # the box's placeholder name goes once it holds text
        env.pages["a"].items = [Item("search text field", "field", value="ratatouille", focused=True, search=True)]

    env = FakeEnv({"a": page(BRAVE, Item("I am looking for", "field", search=True), url="https://cook.example/")}, "a",
                  {("a", "set I am looking for=ratatouille"): typed})  # fmt: skip
    tb = toolbox(env)
    assert tb.tool_write("ratatouille", "I am looking for").ok, tb.trace
    assert tb.last_write == ("search text field", "ratatouille")


def test_how_an_action_was_done_is_never_the_last_keys():
    env = FakeEnv({"a": page(BRAVE, Item("x", "text"), url="https://cook.example/")}, "a")
    env.pressed = "the menu command 'History > Back'"
    tb = toolbox(env)
    tb.do("scroll down", env.scroll, "down")
    assert not any(line.strip().startswith("by:") for line in tb.trace)
