"""The content model (karta/harness/content.py): every item of a read is interface or content, decided once, and every
reader of content takes that label. Made-up apps and sites only.

- an event editor's labels are never events;
- a site's banner repeated on two of its pages is never a result title;
- the main list's rows are content, a button inside a row is not;
- navigation links are interface;
- a page never seen before still classifies by its structure;
- boilerplate is learned across two pages of one host, never across hosts, and kept as digests only.
"""

from __future__ import annotations

import itertools

from karta.base.ax_walk import page_text
from karta.harness import content, view
from karta.harness.content import CONTENT, INTERFACE, Boilerplate, classify, node_class
from karta.harness.mac import Ref
from karta.harness.memory import BOILERPLATE, HarnessMemory
from karta.harness.tools import collect_candidates, window_text
from karta.harness.types import Item, Screen

_ids = itertools.count(1)


def node(role: str) -> tuple[str, int]:
    """One AX element on a path, as ax_walk keeps it: its role and an identity."""
    return (role, next(_ids))


def at(*path: tuple[str, int], landmarks=(), editor=None, web=False, bar=False, y=0.0) -> Ref:
    return Ref(10.0, y, 200.0, 20.0, element=object(), path=path, landmarks=landmarks, editor=editor, web=web, bar=bar)


def labels(scr: Screen) -> dict[str, str]:
    return {i.name: i.role_class for i in classify(scr, Boilerplate()).items}


# ---------------------------------------------------------------- an editor's labels are never events


def planner() -> Screen:
    """A made-up day planner: its day's list (a table of rows) and an event editor open in a popover beside it."""
    win, table, pop = node("AXWindow"), node("AXTable"), node("AXPopover")
    rows = [node("AXRow") for _ in range(3)]
    editor = pop[1]
    return Screen(
        "Dayplan",
        [
            Item("New Item", "button", ref=at(win, node("AXToolbar"), node("AXButton"), bar=True)),
            Item("Kiln firing 9:00 to 10:30", "row", ref=at(win, table, rows[0], y=100)),
            Item("Harbour walk 13:15", "row", ref=at(win, table, rows[1], y=130)),
            Item("Book club 19:00", "row", ref=at(win, table, rows[2], y=160)),
            Item("Kiln review", "field", value="Kiln review", ref=at(win, pop, node("AXTextField"), editor=editor)),
            Item("Kiln review. Starts on 14 March at 15:00", "text", ref=at(win, pop, node("AXStaticText"), editor=editor)),
            Item("Add Reminder, Repeat or Travel", "button", ref=at(win, pop, node("AXButton"), editor=editor)),
            Item("Add Notes, Link or Files", "button", ref=at(win, pop, node("AXButton"), editor=editor)),
        ],
        window="Dayplan",
    )


def test_an_event_editors_labels_are_never_events():
    got = labels(planner())
    for name in ("Add Reminder, Repeat or Travel", "Add Notes, Link or Files", "Kiln review. Starts on 14 March at 15:00"):
        assert got[name] == INTERFACE, name  # inside an editor that holds a field: the form, never the day's list
    assert got["New Item"] == INTERFACE
    events = [i.name for i in collect_candidates(planner(), "today's events")]
    assert events == ["Kiln firing 9:00 to 10:30", "Harbour walk 13:15", "Book club 19:00"]


def test_an_editors_buttons_are_interface_even_read_without_a_tree():
    """As a recording keeps a read (no AX paths): a button is a control, never an event, whatever its words."""
    scr = Screen(
        "Dayplan",
        [
            Item("Kiln firing 9:00 to 10:30", "row"),
            Item("New Event", "field", value="Kiln review"),
            Item("Add Reminder, Repeat or Travel", "button"),
            Item("Add Notes, Link or Files", "button"),
        ],
    )
    assert [i.name for i in collect_candidates(scr, "today's events")] == ["Kiln firing 9:00 to 10:30"]
    assert window_text(Screen("Dayplan", [Item("Add Notes, Link or Files", "button"), Item("Kiln firing", "text")])) == (
        "Kiln firing"
    )


# ---------------------------------------------------------------- a banner on two pages is never a result


def shelf_home() -> Screen:
    return Screen(
        "Browser",
        [
            Item("Borrow free books online", "text", source="ocr"),
            Item("Millions of books to read", "text", source="ocr"),
            Item("Set a reading goal this year", "text", source="ocr"),
            Item("Shelf of the week: night skies", "text", source="ocr"),
        ],
        url="https://shelf.example/",
        window="Welcome | Shelf",
    )


def shelf_results() -> Screen:
    return Screen(
        "Browser",
        [
            Item("Borrow free books online", "text", source="ocr"),
            Item("Millions of books to read", "text", source="ocr"),
            Item("The Salt Road", "link", url="https://shelf.example/works/1"),
            Item("Tides of the North", "link", url="https://shelf.example/works/2"),
            Item("A Lantern for Ravens", "link", url="https://shelf.example/works/3"),
        ],
        url="https://shelf.example/search?q=ravens",
        window="search | Shelf",
    )


def test_a_site_banner_repeated_on_two_pages_is_never_a_result_title():
    book = Boilerplate()
    classify(shelf_home(), book, learn=True)
    results = classify(shelf_results(), book, learn=True)
    got = [i.name for i in collect_candidates(results, "the first three titles")]
    assert got == ["The Salt Road", "Tides of the North", "A Lantern for Ravens"]
    later = classify(shelf_results(), book)  # learned: a later read of the site knows its banner without the page before
    assert {i.name: i.role_class for i in later.items}["Borrow free books online"] == INTERFACE


def test_a_page_still_showing_the_page_before_gives_no_results_and_teaches_nothing():
    book = Boilerplate()
    classify(shelf_home(), book, learn=True)
    stale = shelf_home()
    stale.url, stale.window = "https://shelf.example/search?q=ravens", "shelf.example/search?q=ravens"
    classify(stale, book, learn=True)  # the address moved, the page did not yet
    assert collect_candidates(stale, "the first three titles") == []
    assert not book.knows("shelf.example", "Set a reading goal this year")  # the old page's own text: not learned


# ---------------------------------------------------------------- the main list, navigation, a page never seen


def forge_results() -> Screen:
    """A made-up code forge's results: a banner with its navigation, a list of repositories (each a row of a link, a
    description and a menu button), a footer."""
    web, ul = node("AXWebArea"), node("AXList")
    nav, foot = node("AXGroup"), node("AXGroup")
    items = [
        Item("Explore", "link", url="https://forge.example/explore", ref=at(web, nav, node("AXLink"), landmarks=("AXLandmarkNavigation",), web=True)),
        Item("Sign in", "link", url="https://forge.example/login", ref=at(web, nav, node("AXLink"), landmarks=("AXLandmarkNavigation",), web=True)),
    ]  # fmt: skip
    for n, (name, about) in enumerate((("quillpad", "A small markdown editor"), ("inkwell", "Notes in plain text"),
                                       ("slate-md", "Write markdown, see it rendered"))):  # fmt: skip
        row = node("AXGroup")
        items += [
            Item(name, "link", url=f"https://forge.example/owner{n}/{name}", ref=at(web, ul, row, node("AXLink"), web=True)),
            Item(about, "text", ref=at(web, ul, row, node("AXStaticText"), web=True)),
            Item(f"More options for {name}", "button", ref=at(web, ul, row, node("AXButton"), web=True)),
        ]
    items.append(Item("Terms of use", "link", url="https://forge.example/terms", ref=at(web, foot, node("AXLink"), landmarks=("AXLandmarkContentInfo",), web=True)))  # fmt: skip
    return Screen("Browser", items, url="https://forge.example/search?q=markdown", window="Search - Forge")


def test_main_list_rows_are_content_and_a_button_inside_a_row_is_not():
    got = labels(forge_results())
    for name in ("quillpad", "A small markdown editor", "inkwell", "slate-md"):
        assert got[name] == CONTENT, name  # a one-word name in the main list is still a result
    assert got["More options for quillpad"] == INTERFACE


def test_navigation_links_are_interface():
    got = labels(forge_results())
    assert got["Explore"] == got["Sign in"] == got["Terms of use"] == INTERFACE
    plain = Screen(  # no landmarks: the site's own link named in a word or two is its navigation
        "Browser",
        [
            Item("Pricing", "link", url="https://forge.example/pricing"),
            Item("How to keep a sourdough starter alive", "link", url="https://blog.example/starter"),
        ],
        url="https://forge.example/search?q=bread",
    )
    assert labels(plain) == {"Pricing": INTERFACE, "How to keep a sourdough starter alive": CONTENT}
    names = [i.name for i in collect_candidates(forge_results(), "the top two names")]
    assert names[:2] == ["quillpad", "inkwell"]


def test_a_page_never_seen_before_classifies_by_its_structure():
    """No boilerplate known, one read: the structure alone decides (landmarks, the main list, the controls)."""
    book = Boilerplate()
    scr = classify(forge_results(), book, learn=True)
    assert not book.known.get("forge.example")
    got = {i.name: i.role_class for i in scr.items}
    assert got["quillpad"] == CONTENT and got["Explore"] == INTERFACE


def test_outside_the_main_landmark_is_the_sites_frame():
    web, main, hero = node("AXWebArea"), node("AXGroup"), node("AXGroup")
    scr = Screen(
        "Browser",
        [
            Item("Read the field notes", "link", url="https://notes.example/a", ref=at(web, hero, node("AXLink"), web=True)),
            Item(
                "Field notes on moss",
                "link",
                url="https://notes.example/b",
                ref=at(web, main, node("AXLink"), landmarks=("AXLandmarkMain",), web=True),
            ),
        ],
        url="https://notes.example/",
    )
    assert labels(scr) == {"Read the field notes": INTERFACE, "Field notes on moss": CONTENT}


# ---------------------------------------------------------------- boilerplate: per host, digests only


def page(host: str, path: str, *names: str) -> Screen:
    return Screen("Browser", [Item(n, "text", source="ocr") for n in names], url=f"https://{host}{path}", window=path)


def test_boilerplate_is_learned_across_two_pages_of_one_host_and_never_across_hosts():
    book = Boilerplate()
    classify(page("a.example", "/", "Help centre and contact", "Spring sale on garden tools"), book, learn=True)
    classify(page("b.example", "/", "Help centre and contact", "Opening hours for the library"), book, learn=True)
    assert not book.knows("a.example", "Help centre and contact") and not book.knows(
        "b.example", "Help centre and contact"
    )  # two hosts
    classify(page("a.example", "/tools", "Help centre and contact", "Pruning shears, steel"), book, learn=True)
    assert book.knows("a.example", "Help centre and contact")  # two pages of one host
    assert not book.knows("a.example", "Pruning shears, steel") and not book.knows("b.example", "Help centre and contact")
    fresh = classify(page("b.example", "/hours", "Help centre and contact", "Closed on Sundays"), Boilerplate())
    assert {i.name: i.role_class for i in fresh.items}["Help centre and contact"] == CONTENT


def test_boilerplate_goes_to_memory_as_digests_only_and_comes_back():
    memory = HarnessMemory(":memory:")
    book = Boilerplate(memory)
    classify(page("a.example", "/", "Help centre and contact", "Spring sale"), book, learn=True)
    classify(page("a.example", "/tools", "Help centre and contact", "Pruning shears"), book, learn=True)
    rows = memory.list(BOILERPLATE)
    assert [r.app for r in rows] == ["a.example"] and all("help" not in r.key.lower() for r in rows)
    assert Boilerplate(memory).knows("a.example", "help centre and contact!")  # case and punctuation aside


def test_a_native_apps_pages_are_its_window_titles():
    book = Boilerplate()
    one = Screen("Ledgerly", [Item("Accounts", "text"), Item("Rent paid in March", "row")], window="March")
    two = Screen("Ledgerly", [Item("Accounts", "text"), Item("Gas bill in April", "row")], window="April")
    classify(one, book, learn=True)
    classify(two, book, learn=True)
    assert book.knows("Ledgerly", "Accounts") and not book.knows("Ledgerly", "Gas bill in April")


# ---------------------------------------------------------------- one rule for the other readers


def el(role, *children, value=None, subrole=None):
    return {"AXRole": role, "AXSubrole": subrole, "AXValue": value, "AXChildren": list(children)}


def test_page_text_leaves_out_a_form_and_the_sites_learned_frame():
    tree = el(
        "AXWebArea",
        el(
            "AXGroup",
            el("AXStaticText", value="Moss grows on the shaded side of stones in damp woods."),
            el("AXStaticText", value="Subscribe to our letters"),
            subrole="AXLandmarkMain",
        ),
        el("AXForm", el("AXStaticText", value="Your e-mail address"), el("AXTextField", value="")),
    )
    known = Boilerplate()
    known._keep("notes.example", {content.digest("Subscribe to our letters")})
    keep = content.kept_text(Screen("Browser", [], url="https://notes.example/moss"), known)
    got = page_text(tree, lambda n: n, node_class, keep)
    assert got == "Moss grows on the shaded side of stones in damp woods."


def test_the_page_view_lists_rows_and_text_as_content_and_the_rest_as_interface():
    scr = forge_results()
    scr.items.append(Item("Shop", "text"))  # a word of its own beside longer content: a label
    shown = view.build(scr).text
    rows_text = shown.split("interface:")[0]
    assert "'Shop'" in shown.split("interface:")[1] and "'Shop'" not in rows_text
    assert "'A small markdown editor'" in rows_text
