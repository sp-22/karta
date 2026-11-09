"""Keys on made-up apps: what a key is for is done through Accessibility where the app exposes it (the address field,
a menu's or dialog's cancel, a field's confirm, the app's own menu command, the scroll bar), the key only as the
fallback, and every key is checked: a key that acts on the focus goes only to a focus in the app in front, and a
chain of keys stops at the first that went wrong. The user's clipboard comes back whole after every paste."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_harness_retry import clicks, prefer, v2
from test_harness_tools import BTN, FACTS, FakeEnv, page, toolbox

from karta.base import macos
from karta.core import fast
from karta.harness import repair
from karta.harness.loop import run_task
from karta.harness.mac import FIELD_ROLES, MacHarnessEnv, Ref, address_field
from karta.harness.types import Item, Outcome, Screen, ToolResult

MODIFIERS = {"cmd": 1, "shift": 2, "alt": 4, "ctrl": 8}


class El:
    """A made-up accessibility element: its attributes, the actions it offers and what each does."""

    def __init__(self, role: str, parent: El | None = None, actions=(), pid: int = 7, **attrs):
        self.attrs = {"AXRole": role, "AXParent": parent, **attrs}
        self.actions, self.pid, self.effects = list(actions), pid, {}

    @property
    def role(self) -> str:
        return self.attrs["AXRole"]


class KeysMac(MacHarnessEnv):
    """A MacHarnessEnv over made-up elements. `state` is what the screen shows: an AX action or a key whose effect
    changes it shows as a change; `on_key` and El.effects say what each does."""

    def __init__(self, items=(), browser: bool = True, menu=()):
        self._env = SimpleNamespace(app="Lumen", pid=7, _settle=lambda *a: None, followed=lambda **k: self.log.append("followed"))
        self._m = SimpleNamespace(BROWSERS={"Lumen"} if browser else set(), MODIFIERS=MODIFIERS)
        self._last, self.refused, self.pressed, self.scrolled = None, "", "", ""
        self._watch(thumb=lambda: None)
        self.items, self.menu, self.log, self.on_key = list(items), list(menu), [], {}
        self.focus = self.window = self.lands = None
        self.state = 0

    def screen(self) -> Screen:
        return Screen("Lumen", self.items, window=f"view {self.state}")

    def changed_since(self, before: str, timeout: float) -> bool:
        return self.screen().fingerprint() != before

    # the Mac calls, on made-up elements
    def _ax_attr(self, element, name):
        return element.attrs.get(name) if element is not None else None

    def _ax_actions(self, element):
        return element.actions

    def _ax_perform(self, element, action) -> bool:
        self.log.append(f"{action} {element.role}")
        if action not in element.actions:
            return False
        element.effects.get(action, lambda: None)()
        return True

    def _ax_set(self, element, name, value) -> bool:
        self.log.append(f"set {name}")
        element.attrs[name] = value
        return True

    def _ax_parent(self, element):
        return element.attrs.get("AXParent")

    def _ax_focused(self):
        return self.focus

    def _ax_editable(self, element) -> bool:
        return element.role in FIELD_ROLES

    def _ax_frame(self, element):
        return element.attrs.get("frame")

    def _ax_pid(self, element):
        return element.pid

    def _front_pid(self) -> int:
        return 7

    def _ax_value(self, element):
        return element.attrs.get("AXValue")

    def _ax_focus(self, element) -> bool:
        return False  # a web field: the AX focus is not trusted, a click is

    def _ax_select(self, element, length: int) -> bool:
        self.log.append("ax select")
        return True

    def _click_at(self, point) -> None:
        self.log.append(f"click {point}")
        if self.lands is not None:
            self.focus = self.lands

    def _pause(self, seconds: float) -> None:
        pass

    def _keys(self, keys: str) -> None:
        self.log.append(f"key {keys}")
        self.on_key.get(keys, lambda: None)()

    def _menu_items(self):
        return self.menu

    def _front_window(self):
        return self.window

    def _wheel(self, lines: int) -> None:
        self.log.append(f"wheel {lines}")

    def _paste(self, text: str) -> None:
        self.log.append("paste")
        if self.focus is not None and self.focus.role in FIELD_ROLES:
            self.focus.attrs["AXValue"] = text

    def _type(self, text: str) -> None:
        self.log.append("keys")

    def changes(self) -> None:
        self.state += 1


def keys_of(mac: KeysMac) -> list[str]:
    return [e for e in mac.log if e.startswith("key ")]


# ---------------------------------------------------------------- the address bar


def omnibox(element: El, w: float = 600, value: str = "") -> Item:
    return Item("Address and search", "field", value=value, chrome=True, ref=Ref(200, 40, w, 24, element, True))


def test_the_address_bar_is_focused_by_its_own_field_with_no_key():
    bar = El("AXTextField", AXValue="shop.example.com/cart")
    search = El("AXTextField")
    mac = KeysMac([omnibox(bar), omnibox(search, w=200)])
    mac.lands = bar  # the click focuses it
    mac.press("cmd+l")
    assert not keys_of(mac) and "ax select" in mac.log and mac.pressed.startswith("the address field") and not mac.refused
    assert address_field(mac.screen()).ref.element is bar  # the widest of the browser's own fields


def test_without_its_field_the_address_bar_is_the_menu_command_then_the_key_and_the_focus_is_checked():
    location = El("AXMenuItem", actions=["AXPress"])
    mac = KeysMac(menu=[("File > Open Location", location, "cmd+l")])
    bar = El("AXTextField")
    location.effects["AXPress"] = lambda: setattr(mac, "focus", bar)
    mac.press("cmd+l")
    assert not keys_of(mac) and "File > Open Location" in mac.pressed and not mac.refused
    mac = KeysMac()  # no field, no menu command: the key, then the focus must be in something editable
    mac.on_key["cmd+l"] = lambda: setattr(mac, "focus", El("AXTextField"))
    mac.press("cmd+l")
    assert keys_of(mac) == ["key cmd+l"] and not mac.refused
    mac = KeysMac()
    mac.focus = El("AXButton")  # the key went nowhere editable
    mac.press("cmd+l")
    assert keys_of(mac) == ["key cmd+l"] and "nothing editable" in mac.refused


def test_opening_an_address_stops_at_the_first_step_that_went_wrong():
    mac = KeysMac()
    mac.focus = El("AXButton")
    mac._address_open("https://shop.example.com/")
    assert "paste" not in mac.log and "key return" not in mac.log and mac.refused  # cmd+l failed: nothing typed
    bar = El("AXTextField", AXValue="")
    mac = KeysMac([omnibox(bar)])
    mac.lands = bar
    mac._address_open("https://shop.example.com/")
    assert mac.log.count("paste") == 1 and keys_of(mac) == ["key return"] and not mac.refused
    assert bar.attrs["AXValue"] == "https://shop.example.com/" and mac.log[-1] == "followed"
    other = El("AXTextField", AXValue="")
    mac = KeysMac([omnibox(other)])
    mac.lands = other
    mac._paste = lambda text: other.attrs.update(AXValue="something else")  # the paste went wrong
    mac._address_open("https://shop.example.com/")
    assert "key return" not in mac.log and "does not hold the address" in mac.refused


# ---------------------------------------------------------------- escape


def test_escape_closes_an_open_menu_by_its_axcancel_with_no_key():
    menu = El("AXMenu", actions=["AXCancel"])
    mac = KeysMac()
    mac.focus = El("AXMenuItem", parent=menu)
    menu.effects["AXCancel"] = mac.changes
    mac.press("escape")
    assert mac.pressed == "AXCancel" and not keys_of(mac)


def test_escape_presses_a_sheets_cancel_button_and_the_key_only_when_ax_changed_nothing():
    cancel = El("AXButton", actions=["AXPress"])
    window = El("AXWindow", AXSubrole="AXStandardWindow")
    sheet = El("AXSheet", parent=window, AXCancelButton=cancel)
    window.attrs["AXChildren"] = [sheet]
    mac = KeysMac()
    mac.window = window  # no focused element: the front window's sheet
    cancel.effects["AXPress"] = mac.changes
    mac.press("escape")
    assert "cancel button" in mac.pressed and not keys_of(mac)
    cancel.effects["AXPress"] = lambda: None  # says yes, changes nothing: the key after all
    mac.log.clear()
    mac.press("escape")
    assert keys_of(mac) == ["key escape"] and mac.pressed == "key"


def test_escape_never_sends_axcancel_to_a_field_it_would_clear_a_search():
    page_area = El("AXWebArea")
    mac = KeysMac()
    mac.focus = El("AXSearchField", parent=page_area, actions=["AXCancel"])
    mac.press("escape")
    assert keys_of(mac) == ["key escape"] and not any(e.startswith("AXCancel") for e in mac.log)


# ---------------------------------------------------------------- return, tab


SUBMITS = ("AXConfirm", "AXPress", "key return", "click")


def submits(mac: KeysMac) -> list[str]:
    """Every event that could send: an AXConfirm, an AXPress, a return, a real click."""
    return [e for e in mac.log if e.startswith(SUBMITS)]


def test_a_return_that_may_send_is_one_key_never_axconfirm_and_a_key_after_it():
    field = El("AXTextField", actions=["AXConfirm"])
    mac = KeysMac()
    mac.focus = field
    field.effects["AXConfirm"] = mac.changes
    mac.press("return")
    assert submits(mac) == ["key return"] and mac.pressed == "key"


def test_a_search_or_address_return_is_axconfirm_and_may_fall_back_to_the_key():
    field = El("AXSearchField", actions=["AXConfirm"])
    mac = KeysMac()
    mac.focus = field
    field.effects["AXConfirm"] = mac.changes
    mac.confirm_or_return()
    assert submits(mac) == ["AXConfirm AXSearchField"] and mac.pressed == "AXConfirm"
    field.effects["AXConfirm"] = lambda: None  # says yes, changes nothing in time: the key after it
    mac.log.clear()
    mac.confirm_or_return()
    assert submits(mac) == ["AXConfirm AXSearchField", "key return"]
    mac.focus = El("AXSearchField")  # offers no AXConfirm: the key alone
    mac.log.clear()
    mac.confirm_or_return()
    assert submits(mac) == ["key return"]


def test_a_send_button_is_pressed_exactly_once_even_when_the_screen_changes_late():
    send = El("AXButton", actions=["AXPress"])
    mac = KeysMac()
    late = []
    send.effects["AXPress"] = lambda: late.append("sent")  # the message goes, the screen shows it only later
    mac.click_once(Item("Send", BTN, ref=Ref(10, 10, 80, 20, send, True)))
    assert submits(mac) == ["AXPress AXButton"] and late == ["sent"]  # no click after a press that showed nothing yet
    mac.log.clear()
    mac.click(Item("Send", BTN, ref=Ref(10, 10, 80, 20, send, True)))  # the ordinary click would add a second way
    assert submits(mac) == ["AXPress AXButton", "click (50.0, 20.0)"]
    refused = El("AXButton")  # refuses AXPress (it may still have gone through): never a click after it
    mac.log.clear()
    mac.click_once(Item("Pay", BTN, ref=Ref(10, 10, 80, 20, refused, True)))
    assert submits(mac) == ["AXPress AXButton"] and "refused" in mac.pressed
    mac.log.clear()
    mac.click_once(Item("Post", BTN, ref=Ref(10, 10, 80, 20)))  # read off the pixels: one real click
    assert submits(mac) == ["click (50.0, 20.0)"]


class OneWayEnv(FakeEnv):
    """A fake Mac with the real env's single-shot calls: each logs as itself."""

    def click_once(self, item: Item) -> None:
        self._go(f"once {item.name}")

    def confirm_or_return(self) -> None:
        self._go("confirm or return")


def test_a_risky_submit_makes_one_submit_event_and_a_search_may_take_the_fallback():
    box = Item("Message", "field", value="see you at six", focused=True)
    send = Item("Send", BTN, default=True)
    env = OneWayEnv({"a": page("Chatterly", box, send), "b": page("Chatterly", replace(box, value=""), send)}, "a")
    env.on[("a", "once Send")] = lambda e: None  # the screen changes late: nothing yet
    tb = toolbox(env)
    tb.last_write, tb.pending = ("Message", "see you at six"), {"see you at six": "Message"}
    tb.tool_submit("")
    sent = [e for e in env.log if e.startswith(("once", "click", "press return", "confirm"))]
    assert sent == ["once Send"]
    box = Item("Message", "field", value="see you at six", focused=True)  # no send button: one return, the key
    env = OneWayEnv({"a": page("Chatterly", box)}, "a")
    tb = toolbox(env)
    tb.last_write, tb.pending = ("Message", "see you at six"), {"see you at six": "Message"}
    tb.tool_submit("")
    assert [e for e in env.log if "return" in e] == ["press return"]
    search = Item("Search", "field", value="lamp oil", focused=True, search=True)
    env = OneWayEnv({"a": page("Lumen", search)}, "a")
    tb = toolbox(env)
    tb.last_write, tb.pending = ("Search", "lamp oil"), {"lamp oil": "Search"}
    tb.tool_submit("")
    assert [e for e in env.log if "return" in e] == ["confirm or return"]


def test_one_way_controls_are_those_that_send_commit_or_are_key_actions():
    from karta.harness.tools import one_way

    assert one_way(Item("Send", BTN)) and one_way(Item("Pay now", BTN)) and one_way(Item("Add to cart", BTN))
    assert one_way(Item("Done", BTN, default=True)) and one_way(Item("Next", BTN))
    assert not one_way(Item("Filters", BTN)) and not one_way(Item("Groceries", "row"))


def test_return_and_tab_go_only_to_a_focus_in_the_app_in_front():
    mac = KeysMac()
    for focus in (None, El("AXTextField", pid=99)):
        mac.focus = focus
        for key in ("return", "tab"):
            mac.press(key)
            assert not keys_of(mac) and "not pressed" in mac.refused
    mac.press("shift+tab")
    assert not keys_of(mac) and "not pressed" in mac.refused
    mac.focus = El("AXTextField")
    mac.press("tab")
    assert keys_of(mac) == ["key tab"] and not mac.refused
    mac.focus = None
    mac.press("tab")
    mac.click(Item("Save", BTN, ref=Ref(10, 10, 80, 20)))
    assert not mac.refused  # a refusal speaks of the last action only


# ---------------------------------------------------------------- shortcuts, back


def test_a_shortcut_is_the_apps_own_menu_command_and_back_is_history_back():
    back = El("AXMenuItem", actions=["AXPress"])
    mac = KeysMac(menu=[("History > Back", back, "cmd+["), ("History > Forward", El("AXMenuItem"), "cmd+]")])
    mac.back()
    assert mac.log == ["AXPress AXMenuItem"] and mac.pressed == "the menu command 'History > Back'"
    mac = KeysMac(menu=[("File > Print", El("AXMenuItem", actions=["AXPress"]), "cmd+p")])
    mac.back()  # no Back command: the key
    assert keys_of(mac) == ["key cmd+["]
    twice = [
        ("A > One", El("AXMenuItem", actions=["AXPress"]), "cmd+k"),
        ("B > Two", El("AXMenuItem", actions=["AXPress"]), "cmd+k"),
    ]
    mac = KeysMac(menu=twice)
    mac.press("cmd+k")  # two commands claim it: which one the key does is the app's, so the key
    assert keys_of(mac) == ["key cmd+k"]
    mac = KeysMac(menu=[("Edit > Find", El("AXMenuItem", actions=["AXPress"]), "cmd+shift+f")])
    mac.press("Cmd-Shift-F")
    assert not keys_of(mac) and "Edit > Find" in mac.pressed


def test_select_all_as_the_last_resort_is_the_menu_command_when_the_app_has_it():
    combo = El("AXComboBox", AXValue="old draft")
    select_all = El("AXMenuItem", actions=["AXPress"])
    mac = KeysMac(menu=[("Edit > Select All", select_all, "cmd+a")], browser=False)
    mac._ax_select = lambda element, length: False  # no AX selection
    mac._ax_clear = lambda element: False
    mac.lands = combo
    item = Item("To recipients", "field", value="old draft", ref=Ref(100, 100, 300, 24, combo, True))
    mac.set_text(item, "mira@example.com")
    assert "key cmd+a" not in mac.log and "AXPress AXMenuItem" in mac.log and combo.attrs["AXValue"] == "mira@example.com"


# ---------------------------------------------------------------- scrolling, off-screen items


def scroll_window(value: float = 0.0, content_h: float = 3000.0) -> tuple[El, El]:
    bar = El("AXScrollBar", AXValue=value)
    contents = El("AXGroup", frame=(0, 100, 800, content_h))
    area = El("AXScrollArea", AXVerticalScrollBar=bar, AXContents=[contents], frame=(0, 100, 800, 1000))
    side = El("AXScrollArea", frame=(0, 100, 100, 1000))  # a sidebar: smaller
    window = El("AXWindow", AXChildren=[El("AXGroup", AXChildren=[side, area])])
    return window, bar


def test_scroll_moves_the_scroll_bar_by_a_page_and_the_wheel_is_the_fallback():
    mac = KeysMac()
    mac.window, bar = scroll_window()
    mac.scroll("down")
    assert mac.scrolled == "scroll bar" and bar.attrs["AXValue"] == pytest.approx(0.8 * 1000 / 2000)
    mac.scroll("up")
    assert bar.attrs["AXValue"] == pytest.approx(0.0) and not any(e.startswith("wheel") for e in mac.log)
    mac.scroll("up")  # at the top already: the wheel (which does nothing there either)
    assert mac.scrolled == "wheel" and mac.log[-1] == "wheel 10"
    mac.window = El("AXWindow", AXChildren=[])  # no scroll area exposed
    mac.scroll("down")
    assert mac.log[-1] == "wheel -10"


def test_an_off_screen_field_is_scrolled_to_by_ax_before_any_click_on_it():
    field = El("AXTextField", actions=["AXScrollToVisible"], frame=(100, 400, 300, 24))
    mac = KeysMac(browser=False)
    mac.lands = field
    item = Item("Notes", "field", offscreen=True, ref=Ref(100, 2400, 300, 24, field, False))
    mac.set_text(item, "Bring the lamp")
    assert mac.log[:2] == ["AXScrollToVisible AXTextField", "click (250.0, 412.0)"] and field.attrs["AXValue"] == "Bring the lamp"


def test_an_off_screen_control_that_refuses_axpress_is_scrolled_to_then_clicked_never_clicked_blind():
    button = El("AXButton", actions=["AXScrollToVisible"], frame=(10, 300, 80, 20))
    mac = KeysMac()
    mac.click(Item("Load more", BTN, offscreen=True, ref=Ref(10, 2300, 80, 20, button, True)))
    assert mac.log == ["AXPress AXButton", "AXScrollToVisible AXButton", "click (50.0, 310.0)"]
    stuck = El("AXButton")
    mac.log.clear()
    mac.click(Item("Load more", BTN, offscreen=True, ref=Ref(10, 2300, 80, 20, stuck, True)))
    assert mac.log == ["AXPress AXButton", "AXScrollToVisible AXButton"]  # no click at a spot that is not it


# ---------------------------------------------------------------- clearing a field


def test_clearing_a_field_checks_it_is_empty_after_delete():
    field = El("AXTextField", AXValue="old draft")
    mac = KeysMac()
    mac.lands = field
    mac.on_key["delete"] = lambda: field.attrs.update(AXValue="")
    item = Item("Title", "field", value="old draft", ref=Ref(100, 100, 300, 24, field, True))
    mac.set_text(item, "")
    assert keys_of(mac) == ["key delete"] and not mac.refused
    field.attrs["AXValue"] = "old draft"
    mac.on_key.clear()  # the delete did not take
    mac.set_text(item, "")
    assert "still holds text" in mac.refused


def test_macos_clear_field_empties_the_focus_through_ax_never_select_all(monkeypatch):
    state = {"value": "old draft", "focus": "field", "keys": []}
    monkeypatch.setattr(macos, "ax_focused_element", lambda: state["focus"])
    monkeypatch.setattr(macos, "ax_value", lambda el: state["value"])
    monkeypatch.setattr(macos, "ax_set_value", lambda el, text: state.update(value=text) or True)
    monkeypatch.setattr(macos, "press", lambda key, command=False: state["keys"].append((key, command)))
    assert macos.clear_field() and state["value"] == "" and not state["keys"]
    state["value"] = "old draft"
    monkeypatch.setattr(macos, "ax_set_value", lambda el, text: False)  # a web field: its value is not settable
    monkeypatch.setattr(macos, "ax_select_range", lambda el, s, n: True)
    monkeypatch.setattr(macos, "ax_selected_range", lambda el: (0, 9))

    def delete(key, command=False):
        state["keys"].append((key, command))
        state["value"] = ""

    monkeypatch.setattr(macos, "press", delete)
    assert macos.clear_field() and state["keys"] == [("delete", False)]
    state.update(value="old draft", keys=[])
    monkeypatch.setattr(macos, "ax_selected_range", lambda el: None)  # cannot be selected: nothing pressed
    assert not macos.clear_field() and not state["keys"]


# ---------------------------------------------------------------- the user's clipboard


class Board:
    """A made-up general pasteboard: its items as {type: value}, and a change count as the real one keeps."""

    def __init__(self, items: list[dict]):
        self.items, self.count, self.writes = [dict(i) for i in items], 0, []

    def pasteboardItems(self):
        return [SimpleNamespace(types=lambda i=i: list(i), dataForType_=i.get) for i in self.items]

    def clearContents(self):
        self.items, self.count = [], self.count + 1

    def writeObjects_(self, objects):
        self.items, self.count = [*self.items, *objects], self.count + 1
        self.writes.append(objects)

    def changeCount(self):
        return self.count


@pytest.fixture
def board(monkeypatch):
    def install(*items: dict) -> Board:
        b = Board(list(items))
        monkeypatch.setattr(fast, "NSPasteboard", SimpleNamespace(generalPasteboard=lambda: b))
        monkeypatch.setattr(fast, "pasteboard_item", dict)
        monkeypatch.setattr(fast.time, "sleep", lambda s: None)
        return b

    return install


def test_the_users_clipboard_comes_back_whole_after_a_paste(board):
    image = {"public.png": b"\x89PNG", "public.utf8-plain-text": "a caption"}
    b = board(image, {"public.file-url": "file:///made/up.txt"})
    seen = []
    fast.paste("mira@example.com", lambda: seen.append(dict(b.items[0])))
    assert seen[0]["public.utf8-plain-text"] == "mira@example.com"
    assert set(fast.QUIET_TYPES) <= set(seen[0])  # clipboard managers keep none of it
    assert b.items == [image, {"public.file-url": "file:///made/up.txt"}]  # every item, every type


def test_the_clipboard_is_not_put_back_over_something_copied_meanwhile_and_is_put_back_when_the_paste_raises(board):
    b = board({"public.utf8-plain-text": "mine"})

    def copied():
        b.clearContents()
        b.writeObjects_([{"public.utf8-plain-text": "copied just now"}])

    fast.paste("hello", copied)
    assert b.items == [{"public.utf8-plain-text": "copied just now"}]
    b = board({"public.utf8-plain-text": "mine"})
    with pytest.raises(RuntimeError):
        fast.paste("hello", lambda: (_ for _ in ()).throw(RuntimeError("abort corner")))
    assert b.items == [{"public.utf8-plain-text": "mine"}]


def test_the_clipboard_goes_back_only_once_the_paste_landed_or_the_wait_is_over(board, monkeypatch):
    b = board({"public.utf8-plain-text": "mine"})
    clock = iter(float(n) / 100 for n in range(1000))
    monkeypatch.setattr(fast.time, "perf_counter", lambda: next(clock))
    polls = []

    def landed():
        polls.append(b.items[0]["public.utf8-plain-text"])  # still ours while it waits
        return len(polls) == 3

    fast.paste("hello", lambda: None, landed)
    assert polls == ["hello"] * 3 and b.items == [{"public.utf8-plain-text": "mine"}]


# ---------------------------------------------------------------- the toolbox: chains stop, the dialog's own way out


class Refusing(FakeEnv):
    """A browser whose keys the env refuses: `refuse` names the action that goes wrong."""

    refuse = ""

    def _go(self, action: str) -> None:
        super()._go(action)
        self.refused = "the focus was not there" if action.startswith(self.refuse) and self.refuse else ""


SHOP = "https://shop.example.com/"


def test_the_address_bar_chain_stops_when_its_focus_went_wrong():
    env = Refusing({"a": page("Brave Browser", Item("Cart", BTN), url=SHOP + "cart")}, "a")
    env.refuse = "press cmd+l"
    tb = toolbox(env)
    tb.look()
    result = tb.tool_write("shop.example.com", "address bar")
    assert result.outcome is Outcome.NO_FIELD and env.log == ["press cmd+l"]  # nothing typed
    env = Refusing({"a": page("Brave Browser", Item("Cart", BTN), url=SHOP + "cart")}, "a")
    env.refuse = "type"
    tb = toolbox(env)
    tb.look()
    assert tb.tool_write("shop.example.com", "address bar").outcome is Outcome.NO_FIELD
    env.refuse = ""
    tb.open_root(SimpleNamespace(url=SHOP + "cart"))
    assert env.log[:5] == [
        "press cmd+l",
        "type shop.example.com",
        "press cmd+l",
        "type https://shop.example.com/",
        "press return",
    ]
    env.log.clear()
    env.refuse = "type"
    tb.open_root(SimpleNamespace(url=SHOP + "cart"))
    assert env.log == ["press cmd+l", "type https://shop.example.com/"]  # no return after typing that went nowhere


def test_return_in_the_address_bar_needs_it_to_hold_the_address():
    bar = Item("Address and search", "field", value="other.example.org", chrome=True, focused=True)
    env = FakeEnv({"a": page("Brave Browser", bar, url=SHOP)}, "a")
    tb = toolbox(env)
    tb.last_write = ("address bar", "shop.example.com/deals")
    result = tb.tool_submit("")
    assert result.outcome is Outcome.NO_FIELD and "press return" not in env.log


def test_a_terminal_choice_is_not_confirmed_when_its_number_went_nowhere():
    env = Refusing({"a": page("Terminal", Item("1. Yes", "text", dialog=True))}, "a")
    env.refuse = "type"
    result = toolbox(env).choose_option(Item("1. Yes", "text", dialog=True), env.screen(), "the plan's answer")
    assert result.outcome is Outcome.NO_FIELD and env.log == ["type 1"]


def test_a_wrong_pick_that_opened_a_dialog_is_closed_by_its_own_cancel_control_before_escape():
    rows = (Item("Folders", "heading"), Item("Groceries", "row"), Item("Share", BTN), Item("Export", BTN))
    popup = (Item("Share with", "heading", dialog=True), Item("Close", BTN, dialog=True, cancels=True))
    env = FakeEnv(
        {
            "a": page("Notes", *rows),
            "popup": page("Notes", *rows, *popup),
            "b": page("Notes", *rows, Item("Groceries.pdf", "row")),
        },
        "a",
        {("a", "click Share"): "popup", ("popup", "click Close"): "a", ("a", "click Export"): "b"},
    )
    result = run_task(
        "save the note as a file",
        env,
        prefer("Share", "Export"),
        v2("click save it as a file | expect: new_item\ndone: new_item"),
        FACTS,
    )
    assert result.done, result.trace
    assert clicks(env) == ["click Share", "click Close", "click Export"] and "press escape" not in env.log


def test_the_repairs_tab_goes_only_from_the_field_that_holds_the_focus():
    box = Item("To recipients", "field", ref=Ref(100, 100, 300, 24))
    env = FakeEnv({"a": page("Lumen", box, Item("Subject", "field", focused=True))}, "a")
    env.stubborn = ("To recipients",)  # the text never shows
    tb = toolbox(env)
    tb.last_put = {"box": box, "text": "mira@example.com", "shown": None, "tried": ["click, paste"]}
    step = {"tool": "write", "text": "mira@example.com", "where": "To recipients"}
    miss = ToolResult(Outcome.UNEXPECTED, "typed")
    fixed = repair.text(tb, step, miss, lambda s, r: r, lambda: True, repair.Ladder(tb, miss))
    assert "press tab" not in env.log and any("does not hold the focus" in t for t in fixed.tried)


def test_the_envs_key_events_go_through_macos_so_the_test_guard_holds_them():
    mac = KeysMac()
    mac._m.KEYS = {"return": 36}
    with pytest.raises(RuntimeError, match="real machine"):
        MacHarnessEnv._keys(mac, "return")
