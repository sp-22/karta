"""Exercise the real input adapter with OS calls replaced; no synthetic input reaches the host."""

from types import SimpleNamespace

import pytest

from karta.base import macos
from karta.base.models import Abort


@pytest.fixture
def input_events(monkeypatch):
    events = []
    quartz = SimpleNamespace(
        kCGEventMouseMoved="move",
        kCGEventLeftMouseDown="down",
        kCGEventLeftMouseUp="up",
        kCGMouseButtonLeft=0,
        CGEventCreateMouseEvent=lambda _, kind, point, button: {"kind": kind, "point": point},
        CGEventCreateKeyboardEvent=lambda _, code, down: {"down": down},
        CGEventKeyboardSetUnicodeString=lambda event, length, text: event.update(text=text),
        kCGEventFlagMaskCommand="command",
        CGEventSetFlags=lambda event, flags: event.update(flags=flags),
        kCGScrollEventUnitLine="line",
        CGEventCreateScrollWheelEvent=lambda _, unit, count, lines: {"kind": "scroll", "lines": lines},
    )
    monkeypatch.setattr(macos, "Quartz", quartz)
    monkeypatch.setattr(macos, "_post", events.append)
    return events


def test_corner_abort_prevents_the_click_from_moving_the_pointer(input_events, monkeypatch):
    monkeypatch.setattr(macos, "mouse_location", lambda: (0, 0))
    with pytest.raises(Abort):
        macos.click_at((200, 200))
    assert input_events == []


def test_typing_checks_abort_between_characters_and_releases_the_key(input_events, monkeypatch):
    monkeypatch.setattr(macos, "mouse_location", lambda: (0, 0) if input_events else (500, 500))
    with pytest.raises(Abort):
        macos.type_text("abc")
    assert input_events == [{"down": True, "flags": 0, "text": "a"}, {"down": False, "flags": 0, "text": "a"}]


def test_click_releases_the_button_when_interrupted_after_mouse_down(input_events, monkeypatch):
    def post(event):
        input_events.append(event)
        if event["kind"] == "down":
            raise KeyboardInterrupt

    monkeypatch.setattr(macos, "_post", post)
    with pytest.raises(KeyboardInterrupt):
        macos.click_at((200, 200))
    assert [event["kind"] for event in input_events] == ["move", "down", "up"]


def test_a_key_goes_back_up_when_interrupted_after_key_down(input_events, monkeypatch):
    def post(event):
        input_events.append(dict(event))
        if event["down"]:
            raise KeyboardInterrupt

    monkeypatch.setattr(macos, "_post", post)
    with pytest.raises(KeyboardInterrupt):
        macos.press("a", command=True)
    assert input_events == [{"down": True, "flags": "command"}, {"down": False, "flags": "command"}]


def test_every_posted_click_key_and_scroll_carries_its_modifier_flags_explicitly(input_events, monkeypatch):
    """An event created with no flags takes the session's last ones: a click or a letter right after cmd+a or a
    paste's cmd+v acted as if Command were held (a background tab, a new tab, a tab switch)."""
    monkeypatch.setattr(macos, "mouse_location", lambda: (500, 500))
    monkeypatch.setattr(macos, "frontmost_window_center", lambda: (300.0, 300.0))
    macos.press("a", command=True)
    chord = list(input_events)
    input_events.clear()
    macos.click_at((200, 200))
    macos.type_text("t1")
    macos.press("return")
    macos.scroll(-3)
    assert {event["flags"] for event in chord} == {"command"}
    assert input_events and all(event.get("flags") == 0 for event in input_events)


def test_corner_abort_stops_a_scroll_before_the_pointer_is_parked(input_events, monkeypatch):
    monkeypatch.setattr(macos, "frontmost_window_center", lambda: (300.0, 300.0))
    monkeypatch.setattr(macos, "mouse_location", lambda: (0, 0))
    with pytest.raises(Abort):
        macos.scroll(-10)
    assert input_events == []


@pytest.mark.parametrize(
    "action", [lambda: macos.open_url("Google Chrome", "https://example.com"), lambda: macos.activate("Finder")]
)
def test_corner_abort_stops_an_app_switch_before_any_applescript(action, monkeypatch):
    monkeypatch.setattr(macos, "mouse_location", lambda: (0, 0))
    with pytest.raises(Abort):
        action()


@pytest.mark.parametrize(
    "action", [lambda: macos.ax_press(object()), lambda: macos.ax_focus(object()), lambda: macos.ax_set_value(object(), "text")]
)
def test_accessibility_helpers_do_not_swallow_abort(action, monkeypatch):
    monkeypatch.setattr(macos, "mouse_location", lambda: (0, 0))
    with pytest.raises(Abort):
        action()
