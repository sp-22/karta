"""Text entry on made-up apps: the keyboard focus is made sure before any typing, old text is selected through
Accessibility (never a blind select all), and typing is proven by what shows in or beside the field."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from karta.harness import expect, facts
from karta.harness.mac import FIELD_ROLES, MacHarnessEnv, Ref, focus_holds
from karta.harness.types import Item, Screen
from tests.test_harness_tools import FakeEnv, page, toolbox

MODIFIERS = {"cmd": 1, "shift": 2, "alt": 4, "ctrl": 8}  # the names mac_env's flags go by


class El:
    """A made-up accessibility element."""

    def __init__(self, role: str, parent: El | None = None, value: str = "", selectable: bool = True, pid: int = 7):
        self.role, self.parent, self.value, self.selectable, self.pid = role, parent, value, selectable, pid
        self.selected: tuple[int, int] | None = None


class MadeUpMac(MacHarnessEnv):
    """A MacHarnessEnv over made-up elements: a web form with one combo box. Its keyboard focus moves as a real one
    would; `steals` is where cmd+a sends it (a combo box's suggestion list takes the focus on select all)."""

    def __init__(self, field: El, app: str = "Lumen", browser: bool = True, steals: El | None = None):
        self._env = SimpleNamespace(app=app, pid=7, _settle=lambda *a: None)
        self._m = SimpleNamespace(BROWSERS={app} if browser else set(), MODIFIERS=MODIFIERS)
        self._last, self.refused = None, ""
        self._watch(thumb=lambda: None)
        self.field, self.steals, self.focus, self.log = field, steals, None, []
        self.clicks_land = True

    # the Mac calls, on made-up elements
    def _ax_focused(self):
        return self.focus

    def _ax_parent(self, element):
        return element.parent

    def _ax_editable(self, element) -> bool:
        return element.role in FIELD_ROLES

    def _ax_frame(self, element):
        return None

    def _ax_pid(self, element):
        return element.pid

    def _front_pid(self) -> int:
        return 7

    def _ax_value(self, element):
        return element.value

    def _ax_focus(self, element) -> bool:
        self.log.append("ax focus")
        return False  # a web field: the AX focus is not trusted, a click is

    def _ax_select(self, element, length: int) -> bool:
        if not element.selectable:
            return False
        element.selected = (0, length)
        self.log.append("ax select")
        return True

    def _ax_clear(self, element) -> bool:
        return False

    def _click_at(self, point) -> None:
        self.log.append("click")
        if self.clicks_land:
            self.focus = self.field

    def _pause(self, seconds: float) -> None:
        pass

    def _menu_items(self) -> list[tuple]:
        return []  # no Edit > Select All: select all is the key

    def _keys(self, keys: str) -> None:
        self.log.append(keys)
        if keys == "cmd+a":
            if self.steals is not None:
                self.focus = self.steals
            else:
                self.field.selected = (0, len(self.field.value))
        elif keys == "delete" and self.focus is self.field:
            self._put("")

    def _paste(self, text: str) -> None:
        self.log.append("paste")
        if self.focus is self.field:
            self._put(text)
        self._last = None

    def _type(self, text: str) -> None:
        self.log.append("keys")
        if self.focus is self.field:
            self._put(text)

    def _put(self, text: str) -> None:
        start, length = self.field.selected or (len(self.field.value), 0)
        self.field.value = self.field.value[:start] + text + self.field.value[start + length :]
        self.field.selected = None


def field_item(element: El, value: str = "") -> Item:
    return Item("To recipients", "field", value=value, ref=Ref(100, 100, 300, 24, element, True))


def test_a_combo_box_whose_select_all_moves_the_focus_is_typed_into_without_it():
    combo = El("AXComboBox")
    mac = MadeUpMac(combo, steals=El("AXList"))
    mac.set_text(field_item(combo), "mira@example.com")
    assert combo.value == "mira@example.com" and "cmd+a" not in mac.log and not mac.refused


def test_old_text_is_replaced_through_an_ax_selection_never_a_select_all():
    combo = El("AXComboBox", value="old draft")
    mac = MadeUpMac(combo, steals=El("AXList"))
    mac.set_text(field_item(combo, "old draft"), "mira@example.com")
    assert combo.value == "mira@example.com" and "ax select" in mac.log and "cmd+a" not in mac.log


def test_select_all_is_the_last_resort_and_never_types_once_it_took_the_focus_away():
    combo = El("AXComboBox", value="old draft", selectable=False)
    mac = MadeUpMac(combo, steals=El("AXList"))
    mac.set_text(field_item(combo, "old draft"), "mira@example.com")
    assert "cmd+a" in mac.log and "paste" not in mac.log and combo.value == "old draft"
    assert mac.refused and mac.log[-1] == "click"  # the focus is given back to the field, and nothing typed
    plain = El("AXTextField", value="old draft", selectable=False)
    mac = MadeUpMac(plain)  # select all keeps this field's focus: the last resort works
    mac.set_text(field_item(plain, "old draft"), "new title")
    assert plain.value == "new title" and "cmd+a" in mac.log


def test_the_focus_guard_refuses_to_type_when_the_focus_is_elsewhere():
    combo = El("AXComboBox")
    mac = MadeUpMac(combo)
    mac.clicks_land = False  # something over the field takes the click
    mac.focus = El("AXButton")
    mac.set_text(field_item(combo), "mira@example.com")
    assert combo.value == "" and "paste" not in mac.log and mac.log.count("click") == 2 and mac.refused
    mac.log.clear()
    mac.type_keys("mira", field_item(combo))
    assert "keys" not in mac.log and mac.refused
    mac.log.clear()
    mac.type_at_focus("ls\n")  # a button has the focus: nothing editable
    assert "paste" not in mac.log and mac.refused
    mac.focus = El("AXTextArea", pid=99)  # editable, but another app's
    mac.type_at_focus("ls\n")
    assert "paste" not in mac.log
    mac.focus = El("AXTextArea")
    mac.type_at_focus("ls\n")
    assert "paste" in mac.log and not mac.refused


def test_key_by_key_into_a_field_replaces_its_text_through_an_ax_selection():
    combo = El("AXComboBox", value="mi")
    mac = MadeUpMac(combo, steals=El("AXList"))
    mac.type_keys("mira@example.com", field_item(combo, "mi"))
    assert combo.value == "mira@example.com" and "cmd+a" not in mac.log


def test_focus_holds_on_the_field_its_editor_or_the_box_around_it_never_the_page():
    page_area = El("AXWebArea")
    combo = El("AXComboBox", parent=page_area)
    editor = El("AXTextField", parent=combo)
    parent = lambda e: e.parent  # noqa: E731
    editable = lambda e: e.role in FIELD_ROLES  # noqa: E731
    assert focus_holds(combo, combo, parent, editable) and focus_holds(combo, editor, parent, editable)
    assert focus_holds(editor, combo, parent, editable)  # the field's own combo box holds the focus
    assert not focus_holds(combo, page_area, parent, editable) and not focus_holds(combo, None, parent, editable)
    assert not focus_holds(combo, El("AXList"), parent, editable)
    # a field read off the pixels: an editable focused element drawn around its centre
    assert focus_holds(None, editor, parent, editable, (0, 0, 200, 30), (50, 15))
    assert not focus_holds(None, editor, parent, editable, (0, 100, 200, 30), (50, 15))


# ---------------------------------------------------------------- what proves the typing


class TokenField(FakeEnv):
    """A recipient field that turns a pasted address into a token drawn inside its own frame, read only off the pixels
    ("<address> ✕"), its value staying empty."""

    def set_text(self, item: Item, text: str) -> None:
        self.log.append(f"set {item.name}={text}")
        scr = self.pages[self.at]
        scr.items = [*scr.items, Item(f"{text} ✕", "text", source="ocr", ref=Ref(104, 102, 150, 20))]


def test_a_pasted_value_that_becomes_a_chip_read_only_by_ocr_in_the_field_passes():
    box = Item("To recipients", "field", ref=Ref(100, 100, 300, 24))
    env = TokenField({"a": page("Lumen", box, Item("Subject", "field", ref=Ref(100, 140, 300, 24)))}, "a")
    result = toolbox(env).put(box, "mira@example.com")
    assert result.ok and "token" in result.detail and env.log == ["set To recipients=mira@example.com"]


def test_a_suggestion_below_the_field_is_no_token_and_a_field_holding_other_text_is_not_either():
    box = Item("To recipients", "field", ref=Ref(100, 100, 300, 24, path=(1, 2, 3, 4, 5)))
    before = Screen("Lumen", [box])
    popup = Item("mira@example.com", "row", ref=Ref(100, 300, 300, 24, path=(1, 9, 8, 7)))
    assert facts.text_landed(box, "mira@example.com", before, Screen("Lumen", [box, popup])) is None
    chip = Item("mira@example.com ✕", "text", source="ocr", ref=Ref(104, 102, 150, 20))
    held = replace(box, value="something else")
    assert facts.text_landed(held, "mira@example.com", before, Screen("Lumen", [held, chip])) is None
    assert facts.text_landed(box, "mira@example.com", before, Screen("Lumen", [box, chip])) is chip
    kept = replace(box, value="mira@example.com")
    assert facts.text_landed(kept, "mira@example.com", before, Screen("Lumen", [kept])) is kept


def test_field_holds_reads_typed_text_by_the_same_check():
    body = Item("Body", "field", multiline=True)
    before, after = Screen("Jotter", [body]), Screen("Jotter", [replace(body, value="Buy lamp oil\nand wicks")])
    assert expect.verify(expect.Expect("field_holds", "buy lamp oil"), before, after).ok
