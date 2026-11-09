"""Submitting what was written: fill records its text as write does, a search runs from its own box, and a send or
save control must belong to the form being sent (its dialog, row, region or beside it), never a verb elsewhere on the
page. Made-up apps and sites only."""

from __future__ import annotations

from dataclasses import dataclass

from karta.harness.tools import belongs
from karta.harness.types import Item, Screen
from tests.test_harness_tools import FakeEnv, toolbox

BRAVE = "Brave Browser"


@dataclass
class At:
    """A frame in screen points, as the real env's Ref."""

    x: float
    y: float
    w: float
    h: float

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def masthead(query: str = "", focused: bool = False) -> list[Item]:
    return [
        Item("Search", "field", value=query, region="top", focused=focused, ref=At(500, 20, 500, 40), search=True),
        Item("Make", "button", region="top-right", ref=At(1200, 20, 90, 40)),  # a page verb on the same bar
        Item("Create", "button", region="top-right", ref=At(1300, 20, 90, 40)),
    ]


def clips_env() -> FakeEnv:
    home = Screen(BRAVE, [*masthead(), Item("Trending now", "link", region="middle")], url="https://clips.example/")
    results = Screen(
        BRAVE,
        [*masthead("river otters"), Item("River otters at play", "link", region="middle")],
        url="https://clips.example/results",
    )
    return FakeEnv({"home": home, "results": results}, "home", {("home", "press return"): "results"})


def test_fill_records_what_it_wrote_so_a_search_returns_in_its_box():
    env = clips_env()
    tb = toolbox(env, confirm=lambda q: True)
    assert tb.tool_fill({"Search": "river otters"}).ok
    assert tb.last_write == ("Search", "river otters")
    result = tb.tool_submit("river otters")
    assert result.ok, result
    assert "click Create" not in env.log and "click Make" not in env.log
    assert "press return" in env.log and env.at == "results"


def test_a_focused_search_box_with_text_is_a_search_even_with_nothing_written():
    env = clips_env()
    env.pages["home"].items[0] = Item(
        "Search", "field", value="river otters", region="top", focused=True, ref=At(500, 20, 500, 40), search=True
    )
    tb = toolbox(env, confirm=lambda q: True)
    assert tb.tool_submit("river otters").ok
    assert env.log[0] == "press return" and not any(a.startswith("click") for a in env.log)


def test_a_search_that_did_not_run_never_falls_back_to_a_page_verb():
    env = clips_env()
    env.on.clear()  # return does nothing here
    tb = toolbox(env, confirm=lambda q: True)
    assert tb.tool_fill({"Search": "river otters"}).ok
    result = tb.tool_submit("river otters")
    assert not result.ok
    assert not any(a.startswith("click") for a in env.log)


def form_in_dialog() -> FakeEnv:
    behind = [
        Item("Create", "button", region="top-left", ref=At(20, 80, 120, 40)),  # the page's own verb, behind the dialog
        Item("Inbox", "row", region="left", ref=At(20, 300, 400, 30)),
    ]
    dialog = [
        Item("Title", "field", region="middle", dialog=True, ref=At(600, 300, 400, 30)),
        Item("Notes", "field", region="middle", dialog=True, multiline=True, ref=At(600, 360, 400, 200)),
        Item("Save", "button", region="bottom", dialog=True, ref=At(900, 700, 80, 30)),
    ]
    a = Screen("Planner", [*behind, *dialog])
    saved = Screen("Planner", [*behind, Item("Pick up the cake", "row", region="left")])
    return FakeEnv({"a": a, "saved": saved}, "a", {("a", "click Save"): "saved"})


def test_a_dialogs_form_is_sent_by_its_own_control_never_the_page_behind():
    env = form_in_dialog()
    tb = toolbox(env, confirm=lambda q: True)
    assert tb.tool_fill({"Title": "Pick up the cake"}).ok
    result = tb.tool_submit("Pick up the cake")
    assert result.ok, result
    assert "click Save" in env.log and "click Create" not in env.log


def test_a_verb_across_the_page_is_not_the_forms_control():
    box = Item("Reply", "field", region="bottom-left", focused=True, ref=At(20, 900, 500, 40))
    far = Item("Create", "button", region="top-right", ref=At(1300, 20, 90, 40))
    near = Item("Send", "button", region="bottom-left", ref=At(540, 900, 80, 40))
    assert belongs(near, box) and not belongs(far, box)
    assert not belongs(Item("Save", "button", region="bottom-left", dialog=True), box)  # another dialog's control
    a = Screen("Chat", [box, far])
    env = FakeEnv({"a": a}, "a")
    tb = toolbox(env, confirm=lambda q: True)
    assert tb.submit_control(a) is None  # nothing of this form: return in the field instead


def test_two_identical_forms_send_from_the_nearest_copy():
    left = Item("Message Body", "field", region="bottom-left", dialog=True, multiline=True, ref=At(100, 600, 400, 200))
    right = Item(
        "Message Body", "field", region="bottom-right", dialog=True, multiline=True, focused=True, ref=At(900, 600, 400, 200)
    )
    send_left = Item("Send", "button", region="bottom-left", dialog=True, ref=At(100, 820, 80, 30))
    send_right = Item("Send", "button", region="bottom-right", dialog=True, ref=At(900, 820, 80, 30))
    scr = Screen("Mail", [left, send_left, right, send_right])
    tb = toolbox(FakeEnv({"a": scr}, "a"))
    tb.last_write = ("Message Body", "")
    picked = tb.submit_control(scr)
    assert picked is not None and picked.ref is send_right.ref


def test_a_message_window_sends_with_its_send_not_its_save_and_close():
    def window(x: float, focused: bool) -> list[Item]:
        return [
            Item("Save & close", "button", region="middle", dialog=True, ref=At(x + 360, 300, 20, 20)),
            Item(
                "Message Body", "field", region="bottom", dialog=True, multiline=True, focused=focused, ref=At(x, 420, 400, 200)
            ),
            Item("Send (cmd+return)", "button", region="bottom", dialog=True, ref=At(x, 640, 60, 30)),
        ]

    scr = Screen("Mail", [*window(100, False), *window(600, True)])
    tb = toolbox(FakeEnv({"a": scr}, "a"))
    tb.last_write = ("Message Body", "")
    picked = tb.submit_control(scr)
    assert picked is not None and picked.name.startswith("Send") and picked.ref.x == 600
