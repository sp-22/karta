"""The screen layer's pure parts: rows, OCR clean-up, shortlist, token budget, AX role words, name vs value."""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from karta.base.models import AxNode
from karta.base.models import Item as UpItem
from karta.harness import screen as sc
from karta.harness.mac import (
    Ref,
    drop_field_echoes,
    identifier_words,
    is_dialog,
    kind_of,
    mark_chrome,
    mark_tree,
    name_and_value,
    name_of,
    profile_from_title,
    profile_menu_item,
    region_word,
    rich_label,
)
from karta.harness.screen import clean_ocr, count_tokens, fits_laya, shortlist, state_lines, with_rows
from karta.harness.types import KINDS, REGIONS, Item

FIXTURES = Path(__file__).parent / "fixtures" / "screens"


@pytest.fixture(autouse=True)
def no_tokenizer():
    sc.use_tokenizer(None)
    yield
    sc.use_tokenizer(None)


def fake_embed(texts):
    """A bag of letters: texts sharing letters point the same way."""
    out = np.zeros((len(texts), 26), dtype=np.float32)
    for i, t in enumerate(texts):
        for c in t.lower():
            if "a" <= c <= "z":
                out[i, ord(c) - 97] += 1
    return out


# ---------------------------------------------------------------- rows


def test_a_repeated_name_gets_its_row_mates_left_to_right():
    items = [Item("Crisps 100g"), Item("₹50"), Item("ADD", "button"), Item("Nuts 90g"), Item("₹40"), Item("ADD", "button")]
    out = with_rows(items, [100, 100, 101, 200, 200, 202], tolerance=[12] * 6)
    assert out[2].row == ("Crisps 100g", "₹50")
    assert out[5].row == ("Nuts 90g", "₹40")
    assert out[0].row == ()  # a unique name needs no row


def test_rows_take_at_most_three_mates_and_row_numbers_by_default():
    items = [Item("a"), Item("b"), Item("c"), Item("d"), Item("Buy"), Item("Buy")]
    out = with_rows(items, [1, 1, 1, 1, 1, 2])
    assert out[4].row == ("a", "b", "c")
    assert out[5].row == ()  # alone on its row


# ---------------------------------------------------------------- OCR clean-up


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("/ Compose", "Compose"),
        ("• Inbox  \u203a", "Inbox"),
        ("Q Search mail", "Search mail"),
        ("₹50", "₹50"),
        ("$4.99", "$4.99"),
        ("#general", "#general"),
        ("-5 items", "-5 items"),
        ("+ Add reminder", "Add reminder"),
        ("(3) Unread", "(3) Unread"),
        ("| >", ""),
        ("  ", ""),
        ("Done.", "Done."),
    ],
)
def test_clean_ocr(raw, clean):
    assert clean_ocr(raw) == clean


# ---------------------------------------------------------------- shortlist


def test_shortlist_ranks_shared_words_then_embedding_and_keeps_screen_order():
    items = [Item(f"filler {i}") for i in range(20)] + [Item("Search mail", "field", search=True), Item("Compose", "button")]
    got = shortlist("search mail", items, fake_embed, k=3)
    assert Item("Search mail", "field", search=True) in got and len(got) == 3
    positions = [items.index(i) for i in got]
    assert positions == sorted(positions)


def test_shortlist_returns_everything_when_small_without_embedding():
    items = [Item("a"), Item("b")]
    assert shortlist("x", items, lambda t: pytest.fail("no embed needed"), k=5) == items


def test_state_lines_are_describe_lines():
    items = [Item("ADD", "button", row=("Crisps",)), Item("Search", "field", value="chips", search=True)]
    assert state_lines("add", items, fake_embed).splitlines() == [i.describe() for i in items]


# ---------------------------------------------------------------- token budget


def test_count_tokens_estimates_without_a_loaded_laya():
    assert count_tokens("abcd" * 10) == 10


def test_count_tokens_uses_a_given_tokenizer():
    sc.use_tokenizer(lambda text, add_special_tokens=False: {"input_ids": text.split()})
    assert count_tokens("one two three") == 3


def test_count_tokens_finds_a_loaded_agents_tokenizer(monkeypatch):
    import sys
    import types

    laya = types.ModuleType("karta.decider.laya")
    laya._AGENTS = {"english": types.SimpleNamespace(tok=lambda text, add_special_tokens=False: {"input_ids": [1]})}
    monkeypatch.setitem(sys.modules, "karta.decider.laya", laya)
    assert count_tokens("anything at all") == 1


def test_fits_laya_counts_state_instructions_and_options():
    options = ["shows the chat list", "shows a login page", "shows something else"]
    assert fits_laya({"page": "Chats\nSearch"}, "What does the page show?", options)
    assert not fits_laya({"page": "word " * 600}, "What does the page show?", options)
    assert not fits_laya({}, "x", ["a long option " * 10] * 16)  # the question part alone is past 192


# ---------------------------------------------------------------- AX: kinds, names, values


@pytest.mark.parametrize(
    ("role", "subrole", "kind"),
    [
        ("AXButton", "", "button"),
        ("AXPopUpButton", "", "button"),
        ("AXTextField", "", "field"),
        ("AXTextField", "AXSecureTextField", "field"),
        ("AXSearchField", "", "field"),
        ("AXTextArea", "", "field"),
        ("AXLink", "", "link"),
        ("AXRow", "", "row"),
        ("AXCell", "", "row"),
        ("AXStaticText", "", "text"),
        ("AXHeading", "", "heading"),
        ("AXMenuItem", "", "menu"),
        ("AXCheckBox", "", "checkbox"),
        ("AXCheckBox", "AXSwitch", "checkbox"),
        ("AXRadioButton", "AXTabButton", "tab"),
        ("AXTab", "", "tab"),
        ("AXImage", "", "image"),
        ("AXGroup", "", "other"),
        ("AXWebArea", "", "other"),
    ],
)
def test_ax_roles_map_to_plain_kinds(role, subrole, kind):
    assert kind_of(role, subrole) == kind
    assert kind in KINDS


def test_a_field_keeps_its_name_apart_from_its_text():
    attrs = {"AXRole": "AXTextField", "AXPlaceholderValue": "Search or start a new chat", "AXValue": "Mum"}
    assert name_and_value(attrs) == ("Search or start a new chat", "Mum", None)
    assert name_and_value({"AXRole": "AXTextField", "AXValue": "hello"}) == ("text field", "hello", None)


def test_a_checkbox_value_is_its_checked_state():
    assert name_and_value({"AXRole": "AXCheckBox", "AXTitle": "Remember me", "AXValue": 1}) == ("Remember me", "", True)
    assert name_and_value({"AXRole": "AXCheckBox", "AXTitle": "Remember me", "AXValue": 0})[2] is False


def test_a_nameless_cell_is_named_by_its_short_value():
    assert name_and_value({"AXRole": "AXCell", "AXValue": "Groceries"}) == ("Groceries", "", None)


def test_icon_only_buttons_get_a_name_from_help_identifier_or_role_description():
    assert name_of({"AXRole": "AXButton", "AXHelp": "New message"}) == "New message"
    assert name_of({"AXRole": "AXButton", "AXIdentifier": "composeButton"}) == "compose button"
    close = {"AXRole": "AXButton", "AXSubrole": "AXCloseButton", "AXIdentifier": "_NS:123", "AXRoleDescription": "close button"}
    assert name_of(close) == "close button"  # a subrole's description says what it is
    assert name_of({"AXRole": "AXButton", "AXRoleDescription": "button"}) == ""
    assert name_of({"AXRole": "AXButton", "AXRoleDescription": "Taste"}) == ""  # a role's, in any language, says nothing
    assert identifier_words("sidebar-toggle") == "sidebar toggle"
    assert identifier_words("a1b2c3") == ""


def test_the_walk_label_keeps_icon_buttons_and_names_fields_by_label():
    button = {"AXRole": "AXButton", "AXHelp": "Share"}
    assert rich_label("AXButton", button.get) == "Share"
    field = {"AXRole": "AXTextField", "AXValue": "typed text", "AXPlaceholderValue": "To"}
    assert rich_label("AXTextField", field.get) == "To"
    assert rich_label("AXTextField", {}.get) == "text field"
    assert rich_label("AXGroup", {"AXIdentifier": "composeButton"}.get) == ""  # no fallbacks for containers


def test_regions_are_contract_words():
    for row in ("top", "middle", "bottom"):
        for col in ("left", "center", "right"):
            assert region_word(f"{row}-{col}") in REGIONS


def test_profile_from_window_title():
    profiles = ["Work", "Personal", "Sam (Home)"]
    assert profile_from_title("Inbox - Brave - Work", profiles) == "Work"
    assert profile_from_title("Chats - Navigateur - Personal", profiles) == "Personal"  # any brand, any language
    assert profile_from_title("Plans - Brave - Sam (Home)", profiles) == "Sam (Home)"
    assert profile_from_title("Chats", profiles) is None
    assert profile_from_title("A - B - Google Chrome", profiles) is None


def test_profile_menu_item_prefers_the_exact_entry():
    items = [
        Item("History > Work notes", "menu", menu=True),
        Item("Profils > Sam (Work)", "menu", menu=True),  # the menu's title is localized: found by the names it lists
        Item("Profils > Personal", "menu", menu=True),
    ]
    profiles = ["Work", "Personal"]
    assert profile_menu_item(items, "Personal", profiles).name == "Profils > Personal"
    assert profile_menu_item(items, "Work", profiles).name == "Profils > Sam (Work)"
    assert profile_menu_item(items, "Family", profiles) is None
    assert profile_menu_item(items[:1], "Work", profiles) is None  # one entry naming a profile is no profile menu


def test_items_above_the_page_are_chrome():
    bar = Item("Search or type a URL", "field", ref=Ref(0, 40, 600, 30), search=True)
    page = Item("Chats", "text", ref=Ref(0, 200, 100, 20))
    assert mark_chrome(bar, 120).chrome and not mark_chrome(page, 120).chrome
    assert not mark_chrome(Item("File > New Tab", "menu", menu=True, ref=Ref()), 120).chrome


def test_ocr_of_a_fields_own_text_is_dropped():
    field = AxNode("AXTextField", "Search", 10, 10, 200, 20, True)
    typed = UpItem(0, "chips", 1.0, 30, 30, 90, 50)  # capture pixels, scale 2: inside the field
    elsewhere = UpItem(1, "chips", 1.0, 30, 300, 90, 320)
    merged = [(typed, None), (elsewhere, None), (UpItem(2, "Search", 1.0, 20, 20, 400, 60, "field", "ax"), 0)]
    kept = drop_field_echoes(merged, [field], lambda n: "chips", 2.0)
    assert [up.index for up, _ in kept] == [1, 2]


# ---------------------------------------------------------------- fixtures (H2)


def test_fixtures_round_trip_and_describe():
    for path in FIXTURES.glob("*.json"):
        data = json.loads(path.read_text())
        screen = sc.screen_from_json(data)
        assert all(i.kind in KINDS and (not i.region or i.region in REGIONS) for i in screen.items)
        assert sc.screen_from_json(sc.screen_to_json(screen)).fingerprint() == screen.fingerprint()
        assert all(not i.menu and not i.chrome for i in screen.page())
        assert all(i.describe() for i in screen.items)


def test_screen_json_drops_refs():
    item = Item("ADD", "button", row=("Crisps",), ref=object())
    data = sc.screen_to_json(sc.Screen(app="Brave Browser", items=[item]))
    assert "ref" not in data["items"][0] and data["items"][0]["row"] == ["Crisps"]
    json.dumps(data)


@pytest.mark.skipif(os.environ.get("KARTA_LAYA") != "1", reason="loads Laya; set KARTA_LAYA=1")
def test_laya_tokenizer_counts_and_embeds():  # pragma: no cover - needs the model
    from karta.core.matcher import LayaMatcher

    matcher = LayaMatcher()
    sc.use_tokenizer(matcher.agent.tok)
    assert 0 < count_tokens("Search or start a new chat") < 20
    items = [Item(f"filler {i}") for i in range(30)] + [Item("Search or start a new chat", "field", search=True)]
    assert Item("Search or start a new chat", "field", search=True) in shortlist(
        "find the chat search box", items, matcher.embed, k=5
    )


# ---------------------------------------------------------------- dialogs (contract: Item.dialog)


class _El:
    def __init__(self, parent=None, **attrs):
        self.parent, self.attrs = parent, attrs


@pytest.mark.parametrize(
    "attrs",
    [
        {"AXRole": "AXSheet"},
        {"AXRole": "AXWindow", "AXSubrole": "AXDialog"},
        {"AXRole": "AXWindow", "AXSubrole": "AXSystemDialog"},
        {"AXRole": "AXGroup", "AXSubrole": "AXApplicationDialog"},
        {"AXRole": "AXGroup", "AXModal": True},
    ],
)
def test_dialog_containers_are_recognised(attrs):
    assert is_dialog(attrs)


def test_a_role_description_never_makes_a_dialog():
    assert not is_dialog({"AXRole": "AXGroup", "AXRoleDescription": "dialog"})  # localized words, not structure
    assert not is_dialog({"AXRole": "AXGroup", "AXRoleDescription": "Dialogfeld"})


def test_plain_groups_and_windows_are_no_dialog():
    assert not is_dialog({"AXRole": "AXGroup", "AXRoleDescription": "group"})
    assert not is_dialog({"AXRole": "AXWindow", "AXSubrole": "AXStandardWindow"})


def test_items_inside_a_dialog_and_ocr_text_on_it_are_marked():
    window = _El(AXRole="AXWindow", AXSubrole="AXStandardWindow")
    page = _El(window, AXRole="AXWebArea")
    modal = _El(page, AXRole="AXGroup", AXSubrole="AXApplicationDialog", frame=(100, 100, 400, 300))
    inner = _El(modal, AXRole="AXGroup")
    detect = _El(inner, AXRole="AXButton")
    search = _El(page, AXRole="AXTextField")
    items = [
        Item("Detect my location", "button", ref=Ref(150, 300, 200, 30, detect, True)),
        Item("Search", "field", ref=Ref(10, 10, 80, 20, search), search=True),
        Item("Welcome to the store", "text", source="ocr", ref=Ref(150, 150, 200, 20)),
        Item("Chips", "text", source="ocr", ref=Ref(600, 600, 50, 20)),
        Item("File > New Tab", "menu", menu=True, ref=Ref(element=detect, pressable=True)),
    ]
    out, busy = mark_tree(items, lambda el: el.parent, lambda el: {**el.attrs})
    assert [i.dialog for i in out] == [True, False, True, False, False] and not busy


def test_keys_parse_in_the_ways_a_brain_writes_them():
    from karta.harness.mac import parse_keys

    mods = {"cmd": 1, "shift": 2, "alt": 4, "ctrl": 8}
    assert parse_keys("cmd+space", mods) == (["cmd"], "space")
    assert parse_keys("command space", mods) == (["cmd"], "space")
    assert parse_keys("Cmd-Shift-N", mods) == (["cmd", "shift"], "n")
    assert parse_keys("⌘ ⇧ n", mods) == (["cmd", "shift"], "n")
    assert parse_keys("enter", mods) == ([], "return")


def test_a_web_dialog_without_aria_modal_is_a_panel_and_its_own_cancel_is_marked():
    from karta.harness.mac import blocks

    window = _El(AXRole="AXWindow", AXSubrole="AXStandardWindow")
    page = _El(window, AXRole="AXWebArea")
    cart = _El(page, AXRole="AXGroup", AXSubrole="AXApplicationDialog")  # a side cart: role=dialog, no aria-modal
    plus = _El(cart, AXRole="AXButton")
    modal = _El(page, AXRole="AXGroup", AXSubrole="AXApplicationDialog", AXModal=True)
    ok = _El(modal, AXRole="AXButton")
    sheet_cancel = _El(None, AXRole="AXButton")
    sheet = _El(window, AXRole="AXSheet", AXCancelButton=sheet_cancel)
    sheet_cancel.parent = sheet
    items = [
        Item("Increase quantity by one", "button", ref=Ref(1, 1, 1, 1, plus, True)),
        Item("OK", "button", ref=Ref(1, 1, 1, 1, ok, True)),
        Item("Stay", "button", ref=Ref(1, 1, 1, 1, sheet_cancel, True)),
    ]
    out, _busy = mark_tree(items, lambda el: el.parent, lambda el: {**el.attrs}, browser=True)
    assert [(i.dialog, i.panel, i.cancels) for i in out] == [(True, True, False), (True, False, False), (True, False, True)]
    assert blocks({"AXRole": "AXSheet"}) and blocks({"AXRole": "AXWindow", "AXSubrole": "AXSystemDialog"})
    assert blocks({"AXRole": "AXGroup", "AXRoleDescription": "alert dialog", "AXModal": True})
    assert not blocks({"AXRole": "AXGroup", "AXSubrole": "AXApplicationAlertDialog"})


def test_a_page_says_it_is_loading_by_its_own_attributes():
    from karta.harness.mac import page_busy

    assert page_busy({"AXElementBusy": True}) and page_busy({"AXLoaded": False})
    assert not page_busy({"AXLoaded": True}) and not page_busy({})


def test_the_tree_marks_search_default_busy_and_the_browsers_own_by_structure():
    window = _El(AXRole="AXWindow", AXSubrole="AXStandardWindow")
    toolbar = _El(window, AXRole="AXToolbar")
    page = _El(window, AXRole="AXWebArea")
    landmark = _El(page, AXRole="AXGroup", AXSubrole="AXLandmarkSearch")  # role=search, whatever it is called
    box = _El(landmark, AXRole="AXTextField")
    go = _El(landmark, AXRole="AXButton")
    results = _El(page, AXRole="AXGroup", AXElementBusy=True)  # aria-busy
    row = _El(results, AXRole="AXLink")
    address = _El(toolbar, AXRole="AXTextField")
    items = [
        Item("Buscar", "field", ref=Ref(1, 1, 1, 1, box)),
        Item("Ir", "button", ref=Ref(1, 1, 1, 1, go, True)),
        Item("Resultado", "link", ref=Ref(1, 1, 1, 1, row, True)),
        Item("Dirección", "field", ref=Ref(1, 1, 1, 1, address)),
    ]
    out, busy = mark_tree(items, lambda el: el.parent, lambda el: {**el.attrs}, browser=True)
    assert [i.search for i in out] == [True, True, False, False] and busy
    assert [i.chrome for i in out] == [False, False, False, True]  # outside the web page: the browser's own

    sheet = _El(AXRole="AXSheet")
    save = _El(sheet, AXRole="AXButton")
    cancel = _El(sheet, AXRole="AXButton")
    sheet.attrs |= {"AXDefaultButton": save, "AXCancelButton": cancel}
    out, busy = mark_tree(
        [
            Item("Sichern", "button", ref=Ref(1, 1, 1, 1, save, True)),
            Item("Abbrechen", "button", ref=Ref(1, 1, 1, 1, cancel, True)),
        ],
        lambda el: el.parent,
        lambda el: {**el.attrs},
    )
    assert [(i.default, i.cancels, i.dialog) for i in out] == [(True, False, True), (False, True, True)] and not busy


def test_menu_key_equivalents_and_language_tags():
    from karta.harness.mac import key_equivalent, language_tag

    assert key_equivalent("F", 0) == "cmd+f" and key_equivalent("F", 1) == "cmd+shift+f"
    assert key_equivalent("K", 8) == "k" and key_equivalent(None, 0) == "" and key_equivalent("", 0) == ""
    assert language_tag("de-DE") == "de" and language_tag("es_ES") == "es" and language_tag("Base") == ""
    assert language_tag(None) == ""
