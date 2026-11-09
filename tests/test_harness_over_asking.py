"""No question the task did not need (harness.md, Safety; Tools, "click", "submit", "A dialog in the way"): Return in a
native list row's editor the plan just wrote commits the row, a view's own next and previous are no form commit, and
a bubble the browser floats over the page is not in the way. By structure (roles), never by words. Made-up apps."""

from __future__ import annotations

from test_harness_screen import _El
from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page, toolbox

from karta.harness import risk
from karta.harness.facts import ancestor_roles, in_list_row, view_control
from karta.harness.loop import run_task
from karta.harness.mac import Ref, mark_tree
from karta.harness.types import Item, MacFacts

FACTS = MacFacts(["Tickoff", "Finder"], {}, [], __import__("datetime").datetime(2026, 10, 6, 9, 0))


def at(*roles: str) -> Ref:
    """An item's place in a made-up accessibility tree: its ancestors' roles, outermost first, then its own."""
    return Ref(0, 0, 10, 10, path=tuple((r, n) for n, r in enumerate(roles)))


def test_ancestors_roles_come_from_the_tree_and_nothing_from_the_pixels():
    row_field = Item("text field", "field", ref=at("AXWindow", "AXOutline", "AXOutlineRow", "AXTextField"))
    assert ancestor_roles(row_field) == {"AXWindow", "AXOutline", "AXOutlineRow"} and in_list_row(row_field)
    assert not in_list_row(Item("text field", "field", ref=at("AXWindow", "AXGroup", "AXTextField")))
    assert not in_list_row(Item("text field", "field"))  # read off the pixels: nothing known


def test_return_in_the_new_list_row_the_plan_wrote_is_no_send():
    """The new row's field has no name of its own: Return in an unnamed box counted as a send and asked."""
    new_row = Item("text field", "field", focused=True, ref=at("AXWindow", "AXOutline", "AXOutlineRow", "AXTextField"))
    lists = page("Tickoff", Item("Today", "heading"), Item("Water the ferns", "row"), Item("New item", BTN))
    editing = page("Tickoff", Item("Today", "heading"), Item("Water the ferns", "row"), new_row)
    done = page("Tickoff", Item("Today", "heading"), Item("Water the ferns", "row"), Item("Pay rent", "row"))
    env = FakeEnv({"lists": lists, "editing": editing, "done": done}, "lists", {("lists", "click New item"): "editing"})
    env.on[("editing", "press return")] = "done"
    brain = FakeLineBrain(
        "click New item | expect: editor_opened\nwrite Pay rent | where: text field\nsubmit | expect: new_item(Pay rent)\n"
        "done: new_item(Pay rent)"
    )
    asked: list[str] = []
    result = run_task("add pay rent in tickoff", env, FakeLaya(), brain, FACTS, confirm=lambda q: asked.append(q) or False)
    assert result.done and asked == [], (result.trace, asked)
    assert any("a list row's editor" in line for line in result.trace)


def test_return_in_an_unnamed_box_of_no_list_row_still_asks():
    box = Item("text field", "field", focused=True, value="see you at five", ref=at("AXWindow", "AXGroup", "AXTextField"))
    env = FakeEnv({"a": page("Chatterly", box)}, "a")
    tb = toolbox(env, confirm=lambda q: False)
    tb.last_write = ("text field", "see you at five")
    assert not tb.tool_submit("").ok and tb.confirmations


def test_a_views_own_next_is_no_form_commit_but_a_forms_next_is():
    nav = Item("Next day", BTN, ref=at("AXWindow", "AXToolbar", "AXGroup", "AXButton"))
    form = Item("Next", BTN, ref=at("AXWindow", "AXSheet", "AXButton"))
    assert view_control(nav) and not risk.commits(nav)
    assert risk.commits(form) and risk.commits(Item("Next day", BTN))  # no tree: fails closed
    assert risk.commits(Item("Go", BTN, default=True, ref=at("AXWindow", "AXToolbar", "AXButton")))


def test_a_bubble_the_browser_floats_over_the_page_is_a_panel_and_never_a_permission_prompt():
    window = _El(AXRole="AXWindow", AXSubrole="AXStandardWindow")
    page_area = _El(window, AXRole="AXWebArea")
    search = _El(page_area, AXRole="AXTextField")
    bubble = _El(AXRole="AXWindow", AXSubrole="AXDialog")  # the browser's own popover: no sheet, not modal
    close = _El(bubble, AXRole="AXButton")
    prompt = _El(window, AXRole="AXSheet")  # the browser's own sheet does block
    stay = _El(prompt, AXRole="AXButton")
    items = [
        Item("Buscar", "field", ref=Ref(1, 1, 1, 1, search)),
        Item("Close", "button", ref=Ref(1, 1, 1, 1, close, True)),
        Item("Stay", "button", ref=Ref(1, 1, 1, 1, stay, True)),
    ]
    out, _busy = mark_tree(items, lambda el: el.parent, lambda el: {**el.attrs}, browser=True)
    assert [(i.dialog, i.browser_ui, i.panel) for i in out] == [(False, False, False), (True, True, True), (True, True, False)]

    scr = page("Brave Browser", out[0], out[1], url="https://wiki.example/")
    env = FakeEnv({"a": scr}, "a")
    tb = toolbox(env, FakeLaya(), confirm=lambda q: False)
    tb.front = "Brave Browser"
    assert tb.clear_dialog({"tool": "write", "where": "Buscar", "text": "x"}) is None and tb.confirmations == []
