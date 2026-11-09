"""The page view (karta/harness/view.py): one read as numbered items, grouped, capped, privacy-filtered by code, and
an item found again by its identity, never its number. Made-up apps and sites only; nothing touches the Mac."""

from __future__ import annotations

from types import SimpleNamespace

from karta.harness import view
from karta.harness.types import Item, Screen


def at(x: float, y: float, w: float = 80, h: float = 20, path=()) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, w=w, h=h, path=path)


def shop() -> Screen:
    return Screen(
        "Brave Browser",
        [
            Item("Back", "button", region="top-left", chrome=True),
            Item("Address and search bar", "field", value="https://shop.example/search?q=teapot", chrome=True),
            Item("Search products", "field", value="teapot", region="top"),
            Item("Glazed teapot, one litre", "link", region="middle", url="https://shop.example/p/1", ref=at(100, 200, 300)),
            Item("Add to basket", "text", region="middle", source="ocr", ref=at(100, 230)),
            Item("How to warm a teapot", "link", region="middle", url="https://videohost.example/w/9"),
            Item("Sort", "button", region="top-right"),
            Item("Shop > Quit Shop", "menu", menu=True),
            Item("File > New Window", "menu", menu=True),
            Item("Help > Search", "menu", menu=True),
            Item("Free delivery over 30", "text", region="bottom"),
            Item("", "row", hint=True),
            Item("Hidden deal", "button", offscreen=True),
        ],
        url="https://shop.example/search?q=teapot",
        window="teapot - Shop",
    )


def test_items_are_grouped_and_numbered_in_display_order():
    v = view.build(shop(), "find a teapot on shop.example")
    lines = v.text.splitlines()
    assert lines[0].startswith("app: Brave Browser") and "site: shop.example" in lines[0]
    groups = [ln[:-1] for ln in lines if ln.endswith(":") and not ln.startswith("#")]
    assert groups == ["fields", "controls", "menu commands", "text"]
    numbers = [int(ln.strip().split()[0][1:]) for ln in lines if ln.strip().startswith("#")]
    assert numbers == list(range(1, len(v.items) + 1))
    assert [i.name for i in v.items[:2]] == ["Address and search bar", "Search products"]


def test_the_browser_chrome_but_its_address_field_off_screen_and_nameless_items_are_dropped():
    names = [i.name for i in view.build(shop()).items]
    assert "Back" not in names and "Hidden deal" not in names and "" not in names
    assert "Address and search bar" in names


def test_the_address_field_shows_its_host_only():
    text = view.build(shop(), "teapot").text
    assert "= 'shop.example'" in text and "q=teapot" not in text


def test_a_link_to_another_site_shows_its_host_beside_its_name():
    text = view.build(shop()).text
    assert "'How to warm a teapot' @middle (videohost.example)" in text
    assert "'Glazed teapot, one litre' @middle\n" in text  # a link on this site: no host


def test_an_ocr_item_sits_under_the_row_it_is_drawn_in_and_is_marked():
    v = view.build(shop())
    names = [i.name for i in v.items]
    assert names.index("Add to basket") == names.index("Glazed teapot, one litre") + 1  # the controls tier, not text
    assert "  #" in v.text and "[ocr] 'Add to basket'" in v.text


def test_the_app_s_own_menu_and_the_last_menu_come_after_the_other_commands():
    names = [i.name for i in view.build(shop()).items if i.menu]
    assert names == ["File > New Window", "Shop > Quit Shop", "Help > Search"]


def test_the_cap_keeps_the_focused_field_dialog_fields_and_controls_before_rows_and_text():
    items = [Item(f"note line {n} about plums", "text") for n in range(200)]
    items += [Item(f"Entry {n}", "row") for n in range(60)]
    items += [Item("Save", "button", dialog=True), Item("Title", "field", focused=True), Item("Tags", "field")]
    v = view.build(Screen("Plotline", items), cap=view.CAP)
    assert len(v.items) == view.CAP and v.total == 263
    assert [i.name for i in v.items[:3]] == ["Title", "Save", "Tags"]
    assert all(i.kind == "row" for i in v.items[3:63])


def test_a_field_shows_its_text_only_when_the_task_or_karta_typed_it():
    scr = Screen("Plotline", [Item("Title", "field", value="call the bank"), Item("Notes", "field", value="pin 4471")])
    text = view.build(scr, "add call the bank").text
    assert "'Title' = 'call the bank'" in text and "'Notes' (holds 8 chars)" in text
    assert "'Notes' = 'pin 4471'" in view.build(scr, "", typed={"pin 4471"}).text
    secret = Screen("Plotline", [Item("Password", "field", value="hunter2", secret=True)])
    assert "hunter2" not in view.build(secret, "hunter2").text


def test_personal_content_shows_as_its_shape_and_a_run_of_rows_folds():
    rows = [Item(f"lunch with a friend at noon {n}", "row", region="middle") for n in range(4)]
    scr = Screen("Plotline", [Item("New Entry", "button"), *rows, Item("A long private line of text here", "text")])
    v = view.build(scr)
    assert "#2..#5 4 rows, [7 words] @middle" in v.text
    assert "lunch" not in v.text and "private" not in v.text and "window:" not in v.text
    assert v.labels[1] == "[7 words]" and v.items[1].name.startswith("lunch")
    web = Screen("Brave Browser", [Item("Re: the plan for Saturday morning", "link")], url="https://mail.quillbox.example/")
    assert "[6 words]" in view.build(web).text


def test_an_email_address_is_masked_unless_the_task_holds_it():
    scr = Screen("Brave Browser", [Item("Account: ana@quillbox.example", "button")], url="https://shop.example/")
    assert "<email>" in view.build(scr).text
    assert "ana@quillbox.example" in view.build(scr, "mail ana@quillbox.example").text


def test_one_of_several_alike_controls_shows_the_row_it_is_in():
    scr = Screen(
        "Brave Browser",
        [
            Item("Add", "button", region="middle", row=("Blue mug",)),
            Item("Add", "button", region="middle", row=("Green mug",)),
        ],
        url="https://shop.example/",
    )
    text = view.build(scr).text
    assert "#1 button 'Add' @middle (row: 'Blue mug')" in text and "#2 button 'Add' @middle (row: 'Green mug')" in text


def test_numbering_is_stable_within_one_read():
    scr = shop()
    assert view.build(scr).text == view.build(scr).text


# ---------------------------------------------------------------- identity: never the number


def test_an_item_is_found_again_by_its_identity_on_a_new_read():
    scr = shop()
    item = view.build(scr).items[1]
    again = view.resolve(view.identity(item, scr), shop())
    assert again is not None and again.name == "Search products" and again is not item


def test_of_several_alike_the_same_one_is_found_while_there_are_as_many():
    def page(n: int) -> Screen:
        return Screen("Plotline", [Item("Done", "checkbox", region="middle") for _ in range(n)])

    scr = page(3)
    ident = view.identity(scr.items[1], scr)
    assert ident["nth"] == 1 and ident["of"] == 3
    fresh = page(3)
    assert view.resolve(ident, fresh) is fresh.items[1]
    assert view.resolve(ident, page(4)) is None  # one more now: which one is no longer known, so never guessed


def test_the_ax_roles_tell_namesakes_apart():
    a = Item("Delete", "button", ref=at(0, 0, path=(("AXWindow", 1), ("AXSheet", 2), ("AXButton", 3))))
    b = Item("Delete", "button", ref=at(0, 0, path=(("AXWindow", 1), ("AXToolbar", 5), ("AXButton", 6))))
    scr = Screen("Plotline", [b, a])
    ident = view.identity(a, scr)
    assert ident["roles"] == "AXWindow>AXSheet>AXButton"
    assert view.resolve(ident, Screen("Plotline", [a, b])) is a


def test_a_gone_item_is_none():
    scr = shop()
    ident = view.identity(scr.items[2], scr)
    assert view.resolve(ident, Screen("Brave Browser", [Item("Sort", "button")])) is None


def test_a_page_is_its_app_and_address_or_its_window():
    assert view.page_key(Screen("A", [], url="https://x.example/a#top")) == view.page_key(
        Screen("A", [], url="https://x.example/a")
    )
    assert view.page_key(Screen("Plotline", [], window="Inbox")) != view.page_key(Screen("Plotline", [], window="Today"))
