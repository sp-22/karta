"""collect ranks what fits the asked topic before anything else: a site's own nav link that Laya calls a fit is taken
only when too few items name the topic, and then after the items in the list's own column. Made-up sites only."""

from __future__ import annotations

from dataclasses import dataclass

from karta.harness.decide import FITS
from karta.harness.types import Item, Screen
from tests.test_harness_carry import collect_env
from tests.test_harness_tools import FakeLaya, ch, toolbox

SITE = "https://clips.example"


@dataclass
class At:
    x: float
    y: float
    w: float
    h: float

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def nav(name: str, path: str, y: float = 400) -> Item:
    return Item(name, "link", url=f"{SITE}{path}", region="left", ref=At(10, y, 180, 30))


def result(name: str, vid: str, y: float) -> Item:
    return Item(name, "link", url=f"{SITE}/watch/{vid}", region="middle", ref=At(400, y, 700, 60))


def test_a_nav_link_laya_likes_waits_while_items_that_name_the_topic_come():
    first = Screen("Brave Browser", [nav("Films and shows", "/feed/films")], url=f"{SITE}/results")
    later = Screen(
        "Brave Browser",
        [
            nav("Films and shows", "/feed/films"),
            result("Easy iced oolong tea at home", "a1", 200),
            result("Iced oolong tea, three ways", "b2", 300),
            result("Why my iced oolong tea is bitter", "c3", 400),
        ],
        url=f"{SITE}/results",
    )
    laya = FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9)))
    tb = toolbox(collect_env(first, later), laya)
    assert tb.tool_collect("links to 3 good iced oolong tea videos", 3, "vids").ok
    titles = [c.title for c in tb.slots["vids"]]
    assert "Films and shows" not in titles and len(titles) == 3
    assert "collect_fit" not in laya.asked  # three named the topic: Laya was never needed


def test_too_few_fit_the_list_column_fills_in_before_a_sidebar():
    scr = Screen(
        "Brave Browser",
        [
            nav("Films and shows", "/feed/films", y=150),
            result("Easy iced oolong tea at home", "a1", 200),
            result("A slow morning ritual", "b2", 300),  # in the list's column, not naming the topic
            nav("Music and podcasts", "/feed/music", y=350),
        ],
        url=f"{SITE}/results",
    )
    tb = toolbox(collect_env(scr), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("links to 2 iced oolong tea videos", 2, "vids").ok
    assert [c.title for c in tb.slots["vids"]] == ["Easy iced oolong tea at home", "A slow morning ritual"]


def test_kept_in_screen_order_whatever_order_they_were_taken_in():
    scr = Screen(
        "Brave Browser",
        [result("A slow morning ritual", "b2", 100), result("Easy iced oolong tea at home", "a1", 200)],
        url=f"{SITE}/results",
    )
    tb = toolbox(collect_env(scr), FakeLaya(collect_fit=lambda w, i: ch("D9 collect", (FITS, 0.9))))
    assert tb.tool_collect("links to 2 iced oolong tea videos", 2, "vids").ok
    assert [c.title for c in tb.slots["vids"]] == ["A slow morning ritual", "Easy iced oolong tea at home"]
