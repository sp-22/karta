"""Typed checks (karta/harness/expect.py): each type verified by structure on made-up apps and sites, a verdict of
met, nothing changed or a different change, the compatibility table, and the shapes of known false dones."""

from __future__ import annotations

from karta.harness import expect
from karta.harness.expect import NOTHING, OTHER, Expect, parse, verify
from karta.harness.types import Item, Screen


def scr(app: str, *items: Item, url: str | None = None, window: str = "") -> Screen:
    return Screen(app, list(items), url, window)


def row(name: str, **kw) -> Item:
    return Item(name, "row", **kw)


def btn(name: str, **kw) -> Item:
    return Item(name, "button", **kw)


def field(name: str, value: str = "", **kw) -> Item:
    return Item(name, "field", value, **kw)


def text(name: str, **kw) -> Item:
    return Item(name, "text", **kw)


def check(e: str, before: Screen, after: Screen, **kw) -> expect.Verdict:
    got, error = parse(e)
    assert error is None, error
    return verify(got, before, after, **kw)


LIST = [row("Water the ferns"), row("Pay the rent"), btn("Add task"), field("Search tasks", search=True)]


# ---------------------------------------------------------------- parsing


def test_parse_the_closed_list():
    assert parse("item_opened")[0] == Expect("item_opened")
    assert parse("item_gone(Old drafts)")[0] == Expect("item_gone", "Old drafts")
    assert parse("page_on")[1] and parse("dialog_closed(x)")[1] and parse("opened_thing")[1]
    assert str(Expect("new_item", "see you at 6")) == "new_item(see you at 6)"


def test_nothing_changed_and_a_different_change_are_named():
    before = scr("Taskdeck", *LIST, window="Today")
    assert check("new_item", before, before).status == NOTHING
    opened = scr("Taskdeck", *LIST, text("Settings", dialog=True), btn("Close", dialog=True), window="Today")
    v = check("new_item", before, opened)
    assert v.status == OTHER and "dialog_opened" in v.seen and "expected new_item, but dialog_opened" in v.why()


# ---------------------------------------------------------------- each type


def test_page_on_another_site_and_never_the_one_in_front():
    before = scr("Brave", text("Home"), url="https://www.chatterbox.example/")
    after = scr("Brave", text("Results"), url="https://www.shopwell.example/s")
    assert check("page_on(shopwell.example)", before, after).ok
    same = scr("Brave", text("Other page"), url="https://www.chatterbox.example/thread/4")
    assert not check("page_on(chatterbox.example)", before, same).ok  # true before the click: no proof
    mail = scr("Brave", text("Inbox"), url="https://mail.ledgerly.example/u/0")
    assert check("page_on(mail.ledgerly.example)", scr("Brave", url="https://www.ledgerly.example/"), mail).ok
    assert check("page_on(Quillnote)", before, scr("Quillnote", text("Notes"))).ok


def test_item_opened_is_another_page_of_the_same_site_or_view_of_the_app():
    before = scr("Brave", row("A story"), url="https://news.example/", window="News")
    after = scr("Brave", text("The story's text"), url="https://news.example/item?id=7", window="A story")
    assert check("item_opened", before, after).ok
    assert check("item_opened(A story)", before, after).ok
    assert not check("item_opened(Another story)", before, after).ok
    app_before = scr("Settingsly", row("Focus"), window="General")
    assert check("item_opened", app_before, scr("Settingsly", btn("Quiet hours"), window="Focus")).ok
    elsewhere = scr("Writerly", text("The story's text"), window="Welcome to Writerly")  # read in another app
    assert not check("item_opened", before, elsewhere).ok


def test_results_for_needs_the_query_and_the_page_moving_on():
    box = field("Search", "desk lamp", focused=True, search=True)
    before = scr("Brave", box, btn("Search"), url="https://shopwell.example/", window="Shopwell")
    after = scr("Brave", field("Search", "desk lamp", search=True), row("Lamp one"), url="https://shopwell.example/s?k=desk+lamp")
    assert check("results_for(desk lamp)", before, after).ok
    menu = scr("Brave", box, btn("Upload"), btn("Go live"), url="https://shopwell.example/", window="Shopwell")
    assert not check("results_for(desk lamp)", before, menu).ok  # our own text in the box is no result


def test_new_item_one_more_row_or_one_holding_the_words():
    before = scr("Taskdeck", *LIST, window="Today")
    after = scr("Taskdeck", *LIST, row("New task", focused=True), field("title", focused=True), window="Today")
    assert check("new_item", before, after).ok
    held = scr("Taskdeck", *LIST, row("buy stamps"), window="Today")
    assert check("new_item(buy stamps)", before, held).ok
    typed = scr("Taskdeck", *LIST, field("title", "buy stamps", focused=True), window="Today")
    assert not check("new_item(buy stamps)", before, typed, typed={"buy stamps"}).ok  # only our typing holds it


def test_new_item_a_document_named_so_but_never_a_search_titled_so():
    before = scr("Brave", text("Blank document"), url="https://docs.example/new", window="Untitled - Docs")
    named = scr("Brave", text("Trip plan"), url="https://docs.example/d/4f2a/edit", window="Trip plan - Docs")
    assert check("new_item(Trip plan)", before, named).ok
    search = scr("Brave", text("Results"), url="https://docs.example/search?q=trip+plan", window="trip plan - Docs")
    assert not check("new_item(Trip plan)", before, search).ok


def test_new_item_never_on_a_page_that_loaded():
    loading = scr("Brave", text("Loading"), url="https://mail.example/u/0")
    inbox = scr("Brave", *[row(f"mail {n}") for n in range(30)], url="https://mail.example/u/0")
    assert not check("new_item", loading, inbox).ok
    results = scr("Brave", *[text(f"blue mug {n}") for n in range(20)], url="https://shopwell.example/s")
    assert not check("new_item(blue mug)", scr("Brave", text("cart"), url="https://shopwell.example/s"), results).ok


def test_item_gone():
    before = scr("Quillnote", row("Old drafts"), row("Groceries"), row("Ideas"), window="Notes")
    after = scr("Quillnote", row("Groceries"), row("Ideas"), window="Notes")
    assert check("item_gone(Old drafts)", before, after).ok
    assert check("item_gone", before, after).ok
    assert not check("item_gone(Groceries)", before, after).ok


def test_editor_opened():
    before = scr("Mailroom", btn("Compose"), *LIST)
    after = scr("Mailroom", btn("Compose"), *LIST, field("To", focused=True, dialog=True), btn("Send", dialog=True))
    assert check("editor_opened", before, after).ok
    lost = scr("Mailroom", btn("Compose"), *LIST, field("To", dialog=True), btn("Send", dialog=True))
    still = scr("Mailroom", btn("Compose"), *LIST, field("To", focused=True, dialog=True), btn("Send", dialog=True))
    assert not check("editor_opened", still, lost).ok  # a field losing the focus opened nothing


def test_dialogs_open_and_close():
    before = scr("Taskdeck", *LIST)
    opened = scr("Taskdeck", *LIST, text("Share list", dialog=True), btn("Cancel", dialog=True, cancels=True))
    assert check("dialog_opened", before, opened).ok
    assert check("dialog_closed", opened, before).ok
    browser = scr("Taskdeck", *LIST, text("Allow the microphone?", dialog=True, browser_ui=True))
    assert not check("dialog_opened", before, browser).ok  # the browser asking is not the step's dialog


def test_a_notice_that_went_closes_but_never_proves_a_send():
    before = scr("Mailroom", *LIST, text("Turn on alerts?"), btn("Not now"), btn("OK"))
    assert check("dialog_closed", before, scr("Mailroom", *LIST)).ok
    form = [field("Subject", "Lunch"), field("Body", "see you at 6", multiline=True)]
    sending = scr("Mailroom", *form, text("Turn on alerts?"), btn("Not now"))
    v = check("dialog_closed", sending, scr("Mailroom", *form), tool="submit")
    assert not v.ok  # the form is still there holding the message


def test_window_opened():
    before = scr("Shellbox", field("shell", "~ %", multiline=True), window="~ — zsh — 80x24")
    after = scr("Shellbox", field("shell", "~ %", multiline=True, focused=True), window="~ — zsh — 120x40")
    assert check("window_opened", before, after).ok
    tab = scr("Brave", text("New tab"), url="chrome://newtab/", window="New Tab")
    assert check("window_opened", scr("Brave", text("A page"), url="https://news.example/"), tab).ok


def test_list_changed_and_toggled():
    before = scr("Daybook", Item("Day", "checkbox", checked=False), Item("Month", "checkbox", checked=True), *LIST)
    after = scr("Daybook", Item("Day", "checkbox", checked=True), Item("Month", "checkbox", checked=False), *LIST)
    assert check("list_changed", before, after).ok
    assert check("toggled(Day)", before, after).ok
    assert not check("toggled(Week)", before, after).ok
    pane = scr("Settingsly", text("Quiet hours"), btn("Schedule"), window="Quiet hours")
    v = check("toggled(Quiet hours)", scr("Settingsly", btn("Quiet hours"), window="Focus"), pane)
    assert not v.ok and "item_opened" in v.seen  # its pane opened, nothing turned on


def test_focus_moved_onto_a_field_only():
    before = scr("Mailroom", field("To"), btn("Compose"))
    assert check("focus_moved", before, scr("Mailroom", field("To", focused=True), btn("Compose"))).ok
    assert not check("focus_moved", before, scr("Mailroom", field("To"), btn("Compose", focused=True))).ok


def test_field_holds_only_a_documents_own_text():
    before = scr("Quillnote", field("body", "", multiline=True, focused=True), field("Search", search=True))
    after = scr("Quillnote", field("body", "buy stamps", multiline=True, focused=True), field("Search", search=True))
    assert check("field_holds(buy stamps)", before, after).ok
    form = scr("Mailroom", field("Subject", "Quarterly numbers"), field("Body", "", multiline=True))
    assert not check("field_holds(Quarterly numbers)", scr("Mailroom", field("Subject"), field("Body")), form).ok
    draft = scr("Mailroom", field("Subject", "Quarterly numbers"), field("Body", "see attached", multiline=True))
    assert not check("field_holds(see attached)", form, draft).ok  # one field of a form: the draft is not proven


# ---------------------------------------------------------------- the compatibility table


def test_the_opening_kinds_prove_each_other():
    before = scr("Taskdeck", *LIST)
    editor = scr("Taskdeck", *LIST, field("Title", focused=True, dialog=True), btn("Save", dialog=True))
    for kind in ("editor_opened", "new_item", "dialog_opened"):
        assert check(kind, before, editor).ok, kind
    panel = scr("Taskdeck", *LIST, text("Subtotal", dialog=True, panel=True), btn("Go to cart", dialog=True, panel=True))
    assert check("dialog_opened", before, panel).ok
    assert not check("new_item", before, panel).ok  # a panel with no field is no new item


def test_a_submit_closing_its_form_or_showing_what_it_sent():
    form = [field("To", "odile@example.com"), field("Message", "see you at 6", multiline=True)]
    before = scr("Mailroom", *form, btn("Send"), *LIST)
    after = scr("Mailroom", *LIST, btn("Compose"))
    assert check("new_item(see you at 6)", before, after, tool="submit").ok  # the form went
    thread = [text("Odile"), text("earlier"), text("hello"), field("Message", multiline=True)]
    sent = scr("Chatterbox", *thread, text("see you at 6"))
    held = scr("Chatterbox", *thread[:3], field("Message", "see you at 6", multiline=True))
    assert check("dialog_closed", held, sent, tool="submit", sent="see you at 6").ok


def test_an_old_sent_line_is_no_proof_this_one_went():
    """The old false done: "Message sent" was on screen from an earlier message; this send did nothing."""
    old = [text("Message sent"), field("Message", "lunch?", multiline=True), btn("Send")]
    before = scr("Mailroom", *old, window="Compose")
    assert check("dialog_closed", before, before, tool="submit").status == NOTHING
    assert not check("new_item(lunch?)", before, before, tool="submit", sent="lunch?").ok
