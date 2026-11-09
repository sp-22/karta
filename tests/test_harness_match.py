"""Harness v2 code-first matching: the brain's words to an on-screen item, a search to its one result."""

import pytest

from karta.harness.match import named, normalise, one_result, similarity
from karta.harness.types import Item


def b(name, **kw):
    return Item(name, "button", **kw)


def row(name):
    return Item(name, "row")


# ------------------------------------------------------------------ normalise / similarity


def test_normalise_drops_case_accents_quotes_and_marks():
    assert normalise("  \u201cDon\u2019t Allow\u201d  ") == "dont allow"
    assert normalise("Caf\u00e9 Cr\u00e8me\u2026") == "cafe creme"
    assert normalise("Save & Close") == "save close"
    assert normalise("\u00d7") == "x"


def test_similarity_near_spellings_and_word_order():
    assert similarity("Priyanca", "Priyanka") >= 0.85
    assert similarity("Raghavan, Priya", "Priya Raghavan") == 1.0
    assert similarity("Close", "Clone") < 0.85  # one letter in a short word is another word
    assert similarity("Save", "Same") < 0.85
    assert similarity("", "Share") == 0.0
    assert similarity("Export", "EXPORT!") == 1.0


# ------------------------------------------------------------------ named


FIGMA_DIALOG = [
    b("Got it", dialog=True),
    b("Learn more", dialog=True),
    b("Share"),
    Item("Search files", "field", search=True),
    Item("Recent", "tab"),
]


@pytest.mark.parametrize(
    "said, want",
    [
        ("the 'Got it' button", "Got it"),
        ('"got it"', "Got it"),
        ("Got it.", "Got it"),
        ("Share button", "Share"),
        ("the Recent tab", "Recent"),
        ("the search field", "Search files"),  # the brain named a field: the one field starting with the words
        ("Learn more link", "Learn more"),
    ],
)
def test_named_finds_the_item_the_brain_means(said, want):
    assert named(said, FIGMA_DIALOG).name == want


def test_named_keeps_apostrophes_inside_quotes():
    items = [b("Don't Allow"), b("Allow")]
    assert named("the 'Don't Allow' button", items).name == "Don't Allow"
    assert named("Allow", items).name == "Allow"


def test_named_takes_a_near_spelling_only_when_it_is_the_only_one_that_close():
    assert named("Unsubscibe", [b("Unsubscribe"), b("Archive")]).name == "Unsubscribe"
    assert named("Archve", [b("Archive"), b("Archived")]) is None  # two are that close


def test_named_never_takes_a_name_that_only_contains_the_words():
    assert named("Send", [b("Send later"), b("Schedule send")]) is None
    assert named("New", [b("New Folder")]) is None


def test_named_several_same_names_narrow_by_kind_then_enabled_then_visible():
    items = [b("Download"), Item("Download", "link")]
    assert named("Download link", items).kind == "link"
    assert named("Download", items) is None  # a button and a link, nothing to tell them apart
    assert named("Continue", [b("Continue", enabled=False), b("Continue")]).enabled
    assert named("Export", [b("Export", offscreen=True), b("Export")]).offscreen is False
    assert named("Add to cart", [b("Add to cart"), b("Add to cart")]) is None  # two rows' buttons: Laya or the row


def test_named_nothing_fits_or_nothing_said():
    assert named("Checkout", FIGMA_DIALOG) is None
    assert named("   ", FIGMA_DIALOG) is None
    assert named("Share", []) is None


def test_named_a_cross_is_a_close_button():
    items = [Item("\u00d7", "button", dialog=True), b("Upgrade")]
    assert named("x", items).name == "\u00d7"


# ------------------------------------------------------------------ one_result


CONTACTS = [row("Priya Raghavan"), row("Arjun Mehta"), row("Kavya Iyer")]


def test_one_result_containing_the_search():
    assert one_result("Pri", CONTACTS).name == "Priya Raghavan"
    assert one_result("raghavan pri", CONTACTS).name == "Priya Raghavan"  # its words, any order
    assert one_result("mehta", CONTACTS).name == "Arjun Mehta"


def test_one_result_several_containing_is_none_unless_one_is_exact():
    songs = [row("Blue in Green"), row("Blue in Green (Take 3)"), row("Kind of Blue")]
    assert one_result("Blue", songs) is None
    assert one_result("Blue in Green", songs).name == "Blue in Green"
    assert one_result("Blue in", [row("Blue in Green"), row("Blue in Green (Take 3)")]) is None


def test_one_result_word_start_only():
    assert one_result("ya", CONTACTS) is None  # inside a word is not what a search matched


def test_one_result_one_clear_fuzzy_winner():
    people = [row("Priyanka Shah"), row("Rohan Das"), row("Meera Nair")]
    assert one_result("Priyanca", people).name == "Priyanka Shah"
    twins = [row("Priyanka Shah"), row("Priyanka Sharma")]
    assert one_result("Priyanca", twins) is None  # two equally near: not clear
    assert one_result("Zed", people) is None


def test_one_result_empty():
    assert one_result("", CONTACTS) is None
    assert one_result("Priya", []) is None
    assert one_result("Priya", [row("")]) is None
