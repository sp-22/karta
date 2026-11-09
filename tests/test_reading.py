"""Reading content right, so an answer is never a confident wrong one. Made-up sites and apps only.

- a page's main text from its accessibility tree (ax_walk.page_text): the article, off-screen paragraphs included,
  never its nav, sidebar, footer or a link list; the ledger shows its length, never the text;
- rows that keep their fields together (tools.priced_rows): each title with its own price, a case "for" the product
  after the product, never a lone price from another row;
- a calendar's day view with its mini-month: the events only, never the month's number cells, a date or hour label,
  an empty state or a button's tooltip;
- OCR: a table read by rows, never column by column (perception.merge_blocks, reading_order);
- the walk reads each node's attributes in one batched call (mac.ax_reader).
"""

from __future__ import annotations

from dataclasses import dataclass

from karta.base.ax_walk import page_text
from karta.base.perception import merge_blocks, to_items
from karta.harness.content import node_class
from karta.harness.decide import FITS, OTHER
from karta.harness.mac import ax_reader, named_by_hint
from karta.harness.tools import Collected, collect_candidates, priced_rows
from karta.harness.types import Item, Screen
from tests.test_harness_carry import collect_env
from tests.test_harness_tools import FakeEnv, FakeLaya, ch, toolbox

SHOP = "https://shop.example/search?q=lamp"


@dataclass
class At:
    """An item's frame and place in the accessibility tree, as mac.Ref carries them."""

    x: float
    y: float
    w: float
    h: float
    path: tuple = ()

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


# ---------------------------------------------------------------- a page's main text


def el(role, *children, value=None, subrole=None):
    return {"AXRole": role, "AXSubrole": subrole, "AXValue": value, "AXChildren": list(children)}


def text(value):
    return el("AXStaticText", value=value)


def link(words):
    return el("AXLink", text(words))


ARTICLE = [
    "Tidal pools hold more life per square metre than almost any other shore habitat.",
    "Twice a day the sea retreats and leaves each pool to warm, cool and change its salt.",
    "Only animals that can take those swings, like anemones and hermit crabs, stay for long.",
]  # the third is far below the window: off screen, still in the tree


def article_page(main_landmark=True):
    body = [
        el("AXHeading", text("Life in a tidal pool")),
        el("AXGroup", text(ARTICLE[0])),
        el(
            "AXGroup",
            text("Twice a day"),
            link("the sea retreats"),
            text("and leaves each pool to warm, cool and change its salt."),
        ),
        el("AXGroup", text(ARTICLE[2])),
        el("AXList", link("Kelp forests"), link("Sea glass"), link("Salt marshes"), link("Rock pools at night")),
        el("AXTextField", value="my own half-typed comment"),
        el("AXButton", text("Share")),
    ]
    main = el("AXGroup", *body, subrole="AXLandmarkMain") if main_landmark else el("AXGroup", *body)
    return el(
        "AXWebArea",
        el("AXGroup", link("Home"), link("Topics"), subrole="AXLandmarkBanner"),
        el("AXGroup", link("Oceans"), link("Coasts"), link("Rivers"), subrole="AXLandmarkNavigation"),
        main,
        el("AXGroup", el("AXHeading", text("Trending")), link("Whale songs decoded"), subrole="AXLandmarkComplementary"),
        el("AXGroup", text("Copyright Shoreline Notes"), link("Privacy"), subrole="AXLandmarkContentInfo"),
    )


def test_main_text_is_the_article_in_order_with_its_off_screen_paragraph_and_nothing_around_it():
    for landmark in (True, False):
        got = page_text(article_page(landmark), lambda n: n, node_class)
        assert got.splitlines()[0] == "Life in a tidal pool"
        assert all(p in got for p in ARTICLE), landmark  # the inline link's words stay in their sentence
        assert got.index(ARTICLE[0]) < got.index(ARTICLE[1]) < got.index(ARTICLE[2])
        for other in ("Oceans", "Trending", "Whale songs", "Copyright", "Privacy", "Home", "Kelp forests", "Share", "half-typed"):
            assert other not in got, (landmark, other)


def test_main_text_is_capped_at_a_word():
    long = el("AXWebArea", el("AXGroup", text("tide " * 3000)))
    got = page_text(long, lambda n: n, node_class, cap=500)
    assert len(got) <= 502 and got.endswith(" …") and "tid …" not in got


class ArticleEnv(FakeEnv):
    def page_text(self) -> str:
        return page_text(article_page(), lambda n: n, node_class)


def test_collect_takes_the_pages_main_text_and_the_ledger_shows_only_its_length():
    shown = Screen("Brave Browser", [Item("Fork 1.4k", "link"), Item(ARTICLE[0][:40], "text")], url="https://notes.example/a")
    tb = toolbox(ArticleEnv({"a": shown}, "a"), FakeLaya())
    result = tb.tool_collect("the main text of the article", 1, "story")
    assert result.ok
    (got,) = tb.slots["story"]
    assert all(p in got.text() for p in ARTICLE)  # the whole article goes on, to the tell's brain
    for line in [result.detail, *tb.trace]:  # the ledger and the trace: its length, never its words
        assert "tidal" not in line.lower() and "anemones" not in line
    assert str(len(got.value)) in result.detail


def test_a_collect_of_items_never_reads_the_page_text_and_a_short_one_falls_back_to_the_screen():
    shown = Screen("Brave Browser", [Item("Kelp forests and the otters that guard them", "link")], url="https://notes.example/")

    class Short(FakeEnv):
        def page_text(self) -> str:
            return "Kelp"

    tb = toolbox(Short({"a": shown}, "a"), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("the main text of the page", 1, "story").ok
    assert tb.slots["story"][0].title.startswith("Kelp forests")  # too short to be an article: the items, as before


# ---------------------------------------------------------------- rows: a title and its own price


def card(n: int, title: str, price: str, *, x: float, y: float, extra: str = "") -> list[Item]:
    """One product card of a grid: its title link, its price, a detail line; all under one card in the tree."""
    grid = (1, 2, 3)  # app, window, the results grid
    out = [
        Item(title, "link", url=f"https://shop.example/p/{n}", ref=At(x, y, 260, 40, (*grid, 100 + n, 1000 + n))),
        Item(price, "text", ref=At(x, y + 80, 90, 20, (*grid, 100 + n, 2000 + n))),
    ]
    if extra:
        out.append(Item(extra, "link", ref=At(x, y + 110, 200, 20, (*grid, 100 + n, 3000 + n))))
    return out


GRID = Screen(
    "Brave Browser",
    [
        Item("Up to ₹400", "link", region="left", ref=At(10, 300, 120, 20, (1, 2, 9, 90))),  # a filter in the sidebar
        *(
            Item(f"Apply the filter {brand} to narrow results", "link", ref=At(10, 330 + 30 * k, 160, 20, (1, 2, 9, 91 + k)))
            for k, brand in enumerate(("Brightline", "Lumen", "Orbit", "Cedar", "Harbour"))
        ),
        *card(1, "Silicone shade cover for Lumen Desk Lamp, grey", "₹99", x=300, y=200, extra="+1 other colour or pattern"),
        *card(2, "Lumen Desk Lamp 2, warm white, dimmable", "₹2,499", x=600, y=200),
        *card(3, "Brightline Arc Floor Lamp with remote", "₹4,999", x=900, y=200, extra="+2 other colours or patterns"),
    ],
    url=SHOP,
)


def test_each_price_comes_with_its_own_rows_title_and_a_case_for_the_product_comes_after_it():
    def collect_fit(what, item):  # Laya, made up: a shade cover is something made for the lamp, not the lamp
        return ch("D9 collect", (OTHER, 0.95) if "cover" in item.name else (FITS, 0.95))

    tb = toolbox(collect_env(GRID), FakeLaya(collect_fit=collect_fit))
    assert tb.tool_collect("the price of the Lumen Desk Lamp results", 2, "lamp").ok
    got = tb.slots["lamp"]
    assert [(c.title, c.value) for c in got] == [
        ("Lumen Desk Lamp 2, warm white, dimmable", "₹2,499"),
        ("Silicone shade cover for Lumen Desk Lamp, grey", "₹99"),
    ]
    assert got[0].text() == "Lumen Desk Lamp 2, warm white, dimmable — ₹2,499"  # the tell sees whose price it is
    assert all("Brightline" not in c.title for c in got) and "₹4,999" not in str(got)


def test_a_lone_price_is_never_taken_for_a_named_thing():
    rows = priced_rows(GRID.items, [GRID.items[0]], "the price of the Brightline Arc")
    assert rows == []  # "Up to ₹400" sits among filter links, not in a Brightline card


def test_rows_read_off_the_pixels_pair_by_where_they_are_drawn():
    """No accessibility tree (an app drawn on a canvas): a price is its column's nearest title above it."""
    ocr = Screen(
        "Brave Browser",
        [
            Item("Orbit Wall Clock, oak frame", "text", source="ocr", ref=At(100, 100, 300, 20)),
            Item("Cedar Bookend Pair, walnut", "text", source="ocr", ref=At(500, 100, 300, 20)),
            Item("₹849", "text", source="ocr", ref=At(100, 150, 60, 20)),
            Item("₹1,250", "text", source="ocr", ref=At(500, 150, 60, 20)),
        ],
        url=SHOP,
    )
    rows = collect_candidates(ocr, "the price of the Cedar Bookend Pair")
    assert [(r.name, r.value) for r in rows] == [("Cedar Bookend Pair, walnut", "₹1,250")]


def test_prices_with_nothing_named_keep_their_titles_where_they_have_rows():
    rows = collect_candidates(GRID, "prices of the lamps on this page")  # "lamps" names something: rows only
    assert ("Brightline Arc Floor Lamp with remote", "₹4,999") in [(r.name, r.value) for r in rows]
    assert all(r.name != "Up to ₹400" for r in rows)
    bare = collect_candidates(GRID, "the prices")
    assert {"Up to ₹400"} <= {r.name for r in bare}  # nothing named: a price with no row is kept alone, as before
    assert Collected("Lamp", value="₹1", titled=True).shown() == "Lamp: a value of 2 characters"


# ---------------------------------------------------------------- a calendar's day view


def test_a_day_view_with_a_mini_month_gives_its_events_only():
    cal = Screen(
        "Diary",
        [
            Item("close button", "button", region="top-left", hint=True),
            Item("this button also has an action to zoom the window", "button", region="top-left", hint=True),
            Item("Wednesday 28 October 2026", "text", source="ocr"),
            *(Item(cells, "text", source="ocr") for cells in ("M T W T F S S", "M 28 5", "14 21 28", "10 17 24 31 7", "4")),
            Item("all-day", "text", source="ocr"),
            *(Item(hour, "text", source="ocr") for hour in ("8 AM", "9 AM", "Midday", "1 PM")),
            Item("Pottery class with Ines 9:00 to 10:30", "text", source="ocr"),
            Item("Dentist, Harbour Road 13:15", "text", source="ocr"),
            Item("No Event Selected", "text", source="ocr"),
        ],
    )
    got = [i.name for i in collect_candidates(cal, "today's events with their times")]
    events = ["Pottery class with Ines 9:00 to 10:30", "Dentist, Harbour Road 13:15"]
    assert got == [*events, "No Event Selected"]  # an empty-state line is meaning: Laya's, below

    def collect_fit(what, item):  # Laya, made up: an empty-state line is no event
        return ch("D9 collect", (OTHER, 0.95) if item.name.startswith("No ") else (FITS, 0.9))

    tb = toolbox(collect_env(cal), FakeLaya(collect_fit=collect_fit))
    assert tb.tool_collect("today's events with their times", 0, "events").ok
    assert [c.title for c in tb.slots["events"]] == events


def test_a_tooltip_names_an_icon_but_is_no_label_it_shows():
    assert named_by_hint(
        {"AXHelp": "this button also has an action to zoom the window"}, "this button also has an action to zoom the window"
    )
    assert named_by_hint({"AXIdentifier": "composeButton"}, "compose button")
    assert not named_by_hint({"AXTitle": "Add Event", "AXHelp": "Create a new event"}, "Add Event")
    assert not named_by_hint({"AXDescription": "Search"}, "Search")


def test_titles_are_the_results_links_never_a_snippet_beside_them():
    results = Screen(
        "Brave Browser",
        [
            Item("To keep a sourdough starter alive, feed it flour and water every day and keep it warm", "text", source="ocr"),
            Item("Keeping a sourdough starter alive: a baker's guide", "link", url="https://crumb.example/starter"),
            Item("crumb.example", "link", url="https://crumb.example/"),
            Item("Starter care, week by week", "link", url="https://loafnotes.example/care"),
        ],
        url="https://find.example/search?q=starter",
    )
    got = [i.name for i in collect_candidates(results, "titles of two articles about keeping a sourdough starter alive")]
    assert got == ["Keeping a sourdough starter alive: a baker's guide", "Starter care, week by week"]


# ---------------------------------------------------------------- OCR rows


def line(t, x1, y1, x2, y2):
    return (t, 1.0, (float(x1), float(y1), float(x2), float(y2)))


def test_a_tight_table_read_off_the_pixels_stays_in_rows():
    """Vision returns a table column by column; three rows 40 px apart would join as three columns of text."""
    rows = [
        ("Lumen desk lamp", "in stock", "₹1,299"),
        ("Orbit wall clock", "2 left", "₹849"),
        ("Cedar bookend pair", "new", "₹499"),
    ]
    cols = [
        [line(r[c], (40, 620, 1200)[c], 40 + 40 * k, (240, 730, 1290)[c], 70 + 40 * k) for k, r in enumerate(rows)]
        for c in range(3)
    ]
    vision_order = [ln for col in cols for ln in col]
    assert [it.text for it in to_items(merge_blocks(vision_order), 255)] == [cell for r in rows for cell in r]


def test_a_row_whose_middles_straddle_a_line_is_still_one_row():
    lines = [line("₹849", 900, 131, 990, 161), line("Orbit wall clock", 100, 129, 400, 159)]  # middles 146 and 144
    lines += [line("Header", 100, 40, 300, 70)]
    assert [it.text for it in to_items(lines, 255)] == ["Header", "Orbit wall clock", "₹849"]


def test_two_columns_of_prose_still_wrap():
    lines = [
        line("Harbour festival moves", 100, 100, 400, 130),
        line("Ferry times change", 470, 100, 760, 130),
        line("to the old pier", 100, 136, 330, 166),
        line("for the weekend", 470, 136, 700, 166),
    ]
    assert sorted(t for t, _, _ in merge_blocks(lines)) == [
        "Ferry times change for the weekend",
        "Harbour festival moves to the old pier",
    ]


# ---------------------------------------------------------------- one batched read per node


def test_the_walk_reads_each_node_once_in_a_batch():
    calls = {"batch": 0, "single": 0}
    tree = {
        "w": {"AXRole": "AXWindow", "AXChildren": ["b", "g"]},
        "b": {"AXRole": "AXButton", "AXTitle": "Save", "AXChildren": []},
        "g": {"AXRole": "AXGroup", "AXChildren": []},
    }

    def batch(node, names):
        calls["batch"] += 1
        return {k: tree[node].get(k) for k in names}

    def single(node, name):
        calls["single"] += 1
        return tree[node].get(name)

    attrs, children, subrole = ax_reader(batch, single, lambda pos, size: None)
    for node in ("w", "b", "g"):
        attrs(node), children(node), subrole(node)
    assert attrs("b").label == "Save" and children("w") == ["b", "g"]
    assert calls == {"batch": 3, "single": 0}


def test_a_batched_reads_error_entry_is_no_value_and_a_frame_comes_from_its_two_values():
    import ApplicationServices as AS
    import Quartz

    from karta.base import macos

    missing = AS.AXValueCreate(AS.kAXValueAXErrorType, -25205)  # the attribute is not supported
    pos = AS.AXValueCreate(AS.kAXValueCGPointType, Quartz.CGPoint(10, 20))
    size = AS.AXValueCreate(AS.kAXValueCGSizeType, Quartz.CGSize(30, 40))
    assert macos._ax_error(missing) and not macos._ax_error(pos) and not macos._ax_error("Save")
    assert macos.ax_frame_of(pos, size) == (10.0, 20.0, 30.0, 40.0)
