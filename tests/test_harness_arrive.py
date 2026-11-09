"""Opening a page: the address changes before the page does, so a read counts as the new page only once the page
itself changed (tools.Toolbox.arrive, still_old). Made-up sites and apps only."""

from __future__ import annotations

from dataclasses import replace

from karta.harness.tools import still_old
from karta.harness.types import Item, Screen
from tests.test_harness_tools import FakeEnv, FakeLaya, toolbox

BRAVE = "Brave Browser"
CHROME = [Item("Address and search bar", "field", region="top", chrome=True, search=True)]


def inbox(url: str = "https://mail.example.org/inbox", source: str = "ax") -> Screen:
    return Screen(
        BRAVE,
        [
            *CHROME,
            Item("Search mail", "field" if source == "ax" else "text", region="top-left", source=source),
            Item("Write", "button" if source == "ax" else "text", region="top-left", source=source),
            Item("Weekly digest from the garden club", "row" if source == "ax" else "text", source=source),
            Item("Your parcel is on its way", "row" if source == "ax" else "text", source=source),
        ],
        url=url,
        profile="Personal",
    )


def clips(url: str = "https://clips.example/") -> Screen:
    return Screen(
        BRAVE,
        [
            *CHROME,
            Item("Search", "field", region="top", search=True),
            Item("Upload", "button", region="top-right"),
            Item("Trending", "link"),
        ],
        url=url,
        profile="Personal",
    )


class AddressFirst(FakeEnv):
    """Opening a site shows the new address first while the page still shows the old one for `lag` reads; the env
    has the real Mac's change waits (each re-read is a new picture)."""

    def __init__(self, pages, start, on, stale: Screen, lag: int = 2):
        super().__init__(pages, start, on)
        self.stale, self.lag, self.left = stale, lag, 0

    def screen(self) -> Screen:
        if self.left > 0:
            self.left -= 1
            return replace(self.stale, items=list(self.stale.items))
        return super().screen()

    def _go(self, action: str) -> None:
        super()._go(action)
        if action.startswith(("url ", "tab ")):
            self.left = self.lag

    def changed_since(self, before: str, timeout: float) -> bool:
        return self.screen().fingerprint() != before

    def wait_change(self, timeout: float) -> bool:
        return True


def test_a_read_with_the_new_address_but_the_old_page_is_not_the_new_site():
    old = inbox()
    stale = replace(old, url="https://clips.example/")  # the address bar moved on, the page did not
    env = AddressFirst(
        {"mail": old, "clips": clips()}, "mail", {("mail", "url https://clips.example/ Brave Browser Personal"): "clips"}, stale
    )
    tb = toolbox(env, task="find a clip on clips.example")
    tb.look()  # the browser's page before: the toolbox saw it
    result = tb.tool_open_place("Clips", "Search", site="clips.example")
    assert result.ok, result
    scr = tb.look()
    assert scr.url == "https://clips.example/" and any(i.name == "Upload" for i in scr.items)
    assert env.left == 0  # it read past the stale picture before calling the place ready


def test_still_old_by_content_and_by_structure():
    old = inbox()
    assert still_old(replace(old, url="https://clips.example/"), old)
    assert not still_old(clips(), old)
    # the browser came from behind another app: its last page is unknown, but text read only off the pixels while the
    # page exposes nothing of its own is a picture of the page before
    pixels = inbox(url="https://clips.example/", source="ocr")
    assert still_old(pixels, None)
    assert not still_old(clips(), None)
    assert not still_old(Screen(BRAVE, list(CHROME), url="https://clips.example/"), None)  # blank: loading's to judge


def test_opening_from_another_app_waits_for_the_page_not_the_address():
    term = Screen("Terminal", [Item("shell", "field", focused=True, multiline=True)])
    stale = inbox(url="https://clips.example/", source="ocr")
    env = AddressFirst(
        {"term": term, "clips": clips()},
        "term",
        {("term", "url https://clips.example/ Brave Browser Personal"): "clips"},
        stale,
        lag=1,
    )
    tb = toolbox(env, task="find a clip on clips.example")
    tb.look()
    assert tb.tool_open_place("Clips", "Search", site="clips.example").ok
    assert not any(i.name == "Search mail" for i in tb.look().items)


def test_a_tab_switch_is_judged_the_same_way():
    from karta.harness.facts import Tab

    old = inbox()
    stale = replace(old, url="https://clips.example/watch")
    env = AddressFirst(
        {"mail": old, "clips": clips("https://clips.example/watch")}, "mail", {("mail", "tab Brave Browser 1.2"): "clips"}, stale
    )
    tb = toolbox(env, task="find a clip on clips.example")
    tb.tabs = [Tab(BRAVE, 1, 2, "https://clips.example/watch", "", "Personal")]
    tb.look()
    assert tb.tool_open_place("Clips", "Search", site="clips.example").ok
    assert env.log[0] == "tab Brave Browser 1.2" and tb.look().url.endswith("/watch")
    assert any(i.name == "Upload" for i in tb.look().items)


def test_a_page_that_never_changes_is_not_called_the_new_site():
    old = inbox()
    stale = replace(old, url="https://clips.example/")
    env = AddressFirst({"mail": old}, "mail", {}, stale, lag=99)
    env.on[("mail", "url https://clips.example/ Brave Browser Personal")] = lambda e: None
    laya = FakeLaya()
    tb = toolbox(env, laya, task="find a clip on clips.example")
    tb.look()
    result = tb.tool_open_place("Clips", "Search", site="clips.example")
    assert not result.ok and "still shows the last one" in result.detail
    assert "page_state" not in laya.asked  # never judged ready on the old page's words ("Search mail")
