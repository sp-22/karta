"""The hand-built replay cases: scripted Macs run through the real harness under the recorder, then scrubbed.

    HF_HUB_OFFLINE=1 uv run python -m tests.replay._seeds          # rewrites tests/replay/seed-*/

Each case is a condition that broke Karta live, made up from scratch (no one's content):

- seed-sent-before: the mailbox already shows "Message sent" (an earlier mail) before anything is done, and this
  send fails. The task must not end "done".
- seed-stale-after-open: the first read after opening a site still shows the previous site, whose headline names
  the new one. The task must wait for the new page and finish there.
- seed-full-screen: a full-screen browser read through the real AX walk (karta.harness.mac.walk_app): the tab bar
  and toolbar are windows of their own, the page's groups share its window's frame, and another window of the
  browser sits behind. The page must be read (and the window behind not), and the search done.

Laya is decide.Laya over a strict word-matching agent (deterministic, no model); the brain streams a fixed plan. Both are
recorded, so the cases replay without either. The scrub salt is fixed, so the files only change when the harness
does.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import numpy as np

from karta.harness import loop
from karta.harness.decide import Laya
from karta.harness.facts import MacMap, Tab
from karta.harness.replay import scrub_record
from karta.harness.sim import ScriptedMac
from karta.harness.snapshots import Recorder
from karta.harness.types import Item, MacFacts, Screen

HERE = Path(__file__).resolve().parent
SALT = "karta-replay-seeds"
FACTS = MacFacts(
    ["Brave Browser", "Safari", "Finder", "Notes"],
    {"Brave Browser": ["Personal"]},
    ["~/Projects/atlas"],
    datetime(2026, 10, 8, 9, 30),
)


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


class OracleAgent:
    """Laya's agent, made up and strict: whether an item or a page shows the expected words is answered by whether it
    holds all of them; any other question by the words an option shares with the question's state."""

    def system_one(self, state, spec):
        q = spec["q"]
        options = list(q["criteria"])
        if "expected" in state:
            wanted = words(state["expected"])
            if "item" in state or "page" in state:
                holds = wanted <= words(state.get("item") or state.get("page"))
                scores = [
                    (1.0 if holds else 0.0) if "something else" not in o and "not" not in o.split() else (0.0 if holds else 1.0)
                    for o in options
                ]
            else:
                scores = [1.0 if wanted <= words(o) else 0.0 for o in options]
        else:
            text = words(json.dumps(state))
            scores = [len(words(o) & text) for o in options]
        weights = [math.exp(4 * s) for s in scores]
        total = sum(weights)
        return {"answers": {"q": {"probabilities": {o: w / total for o, w in zip(options, weights, strict=True)}}}}


def embed(texts):
    out = np.zeros((len(texts), 32))
    for n, t in enumerate(texts):
        for w in t.lower().split():
            out[n, int(hashlib.md5(w.encode()).hexdigest(), 16) % 32] += 1.0
    return out + 1e-6


class PlanBrain:
    def __init__(self, plan: str):
        self.replies = [plan]

    def stream_lines(self, system: str, user: str):
        yield from (self.replies.pop(0) if self.replies else "stop: nothing more to try").splitlines()


class LikeMac(ScriptedMac):
    """A ScriptedMac that reads like the real one (mac.MacHarnessEnv): a read is kept until the next action, and the
    env has its own change waits. `stale_after_open`: the first read after opening a URL still shows the page before."""

    def __init__(self, *args, stale_after_open: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.stale_after_open = stale_after_open
        self.stale: Screen | None = None
        self._last: Screen | None = None

    def screen(self) -> Screen:
        if self._last is None:
            if self.stale is not None:
                self._last, self.stale = self.stale, None
            else:
                self._last = super().screen()
        return self._last

    def _go(self, action: str) -> None:
        self._last = None
        super()._go(action)

    def set_text(self, item: Item, text: str) -> None:
        self._last = None
        super().set_text(item, text)

    def open_url(self, url: str, browser: str, profile: str | None = None) -> None:
        before = self.screen()
        super().open_url(url, browser, profile)
        if self.stale_after_open:
            self.stale = before

    def changed_since(self, before: str, timeout: float) -> bool:
        self._last = None
        return self.screen().fingerprint() != before

    def wait_change(self, timeout: float) -> bool:
        self._last = None
        return True


def chrome(url: str) -> list[Item]:
    return [
        Item("Address and search bar", "field", value=url, region="top", chrome=True, search=True),
        Item("Personal (profile)", "button", region="top-right", chrome=True),
    ]


def page(*items: Item, url: str, window: str = "") -> Screen:
    return Screen("Brave Browser", [*chrome(url), *items], url=url, window=window, profile="Personal")


# ---------------------------------------------------------------- 1. "sent" words before anything is sent

INBOX = "https://mail.example/inbox"


def sent_before() -> LikeMac:
    toast = Item("Message sent", "text", region="bottom-left")
    inbox = page(Item("Compose", "button", region="left"), Item("Inbox", "row", region="left"), toast, url=INBOX)
    compose = page(
        Item("Compose", "button", region="left"),
        Item("New message", "heading", region="right"),
        Item("To", "field", region="right"),
        Item("Subject", "field", region="right"),
        Item("Message body", "field", region="right", multiline=True),
        Item("Send", "button", region="bottom-right"),
        toast,
        url=INBOX + "?compose=new",
    )
    failed = replace(compose, items=[*compose.items, Item("Couldn't send. Try again", "text", region="bottom-right")])
    return LikeMac(
        {"inbox": inbox, "compose": compose, "failed": failed},
        "inbox",
        {("inbox", "click Compose"): "compose", ("compose", "click Send"): "failed"},
    )


SENT_PLAN = """open_place Mailbox | ready: Compose | site: mail.example
click Compose | then: New message
fill To: sam@example.com | Subject: Report ready
write The report is ready. | where: message body
submit | expect: Message sent
done: Message sent"""


# ---------------------------------------------------------------- 2. the first read after opening shows the old site

NEWS = "https://news.example/today"
TASKS = "https://tasks.example/"


def stale_after_open() -> LikeMac:
    news = page(
        Item("Ten ways tasks.example users plan their week", "heading"),
        Item("Read more", "link", url=NEWS + "/plan-week"),
        url=NEWS,
    )
    tasks = page(
        Item("My tasks", "heading", region="top"),
        Item("New task", "field", region="top"),
        Item("Add task", "button", region="top-right"),
        Item("Water the plants", "row"),
        url=TASKS,
    )
    added = replace(tasks, items=[*tasks.items, Item("Buy stamps", "row")])
    return LikeMac(
        {"news": news, "tasks": tasks, "added": added},
        "news",
        {
            ("news", "url https://tasks.example"): "tasks",
            ("tasks", "click Add task"): "added",
            ("tasks", "press return"): "added",
        },
        stale_after_open=True,
    )


STALE_PLAN = """open_place Tasks | ready: Add task | site: tasks.example
write Buy stamps | where: New task
submit | expect: Buy stamps
done: Buy stamps"""


# ---------------------------------------------------------------- 3. a full-screen browser, read by the real AX walk

SHOP = "https://shop.example/"
DISPLAY = (1728.0, 1117.0)
PAGE = (0.0, 114.0, 1728.0, 1003.0)


def ax(role, label="", frame=(0.0, 0.0, 10.0, 10.0), children=(), subrole=None):
    return {"role": role, "label": label, "frame": frame, "children": list(children), "subrole": subrole}


def full_screen_tree(results: bool) -> tuple[dict, dict]:
    """(app element, focused window) of a full-screen browser: tab-bar and toolbar windows above the page's, the page
    under nameless groups framed like its window, and an older window of the same browser behind."""
    tabs = ax("AXWindow", "", (0.0, 0.0, 1728.0, 40.0), [ax("AXTab", "Shop", (8.0, 6.0, 220.0, 28.0))], "AXStandardWindow")
    toolbar = ax(
        "AXWindow",
        "",
        (0.0, 40.0, 1728.0, 74.0),
        [ax("AXTextField", "Address and search bar", (300.0, 52.0, 1000.0, 30.0))],
        "AXStandardWindow",
    )
    content = [ax("AXTextField", "Search", (500.0, 140.0, 600.0, 36.0), subrole="AXSearchField")]
    if results:
        content += [
            ax("AXStaticText", "12 results for blue mugs", (100.0, 200.0, 400.0, 24.0)),
            ax("AXLink", "Blue enamel mug", (100.0, 260.0, 300.0, 24.0)),
            ax("AXLink", "Cobalt stoneware mug", (100.0, 300.0, 300.0, 24.0)),
        ]
    else:
        content += [ax("AXLink", "Kitchen", (100.0, 200.0, 120.0, 24.0)), ax("AXLink", "Garden", (240.0, 200.0, 120.0, 24.0))]
    web = ax("AXWebArea", "", PAGE, [ax("AXGroup", "", PAGE, content)])
    page_window = ax(
        "AXWindow",
        "",
        PAGE,
        [ax("AXGroup", "", PAGE, [ax("AXGroup", "", PAGE, [ax("AXScrollArea", "", PAGE, [web])])])],
        "AXStandardWindow",
    )
    behind = ax(
        "AXWindow",
        "",
        (200.0, 150.0, 1000.0, 700.0),
        [ax("AXWebArea", "", (200.0, 230.0, 1000.0, 620.0), [ax("AXLink", "Old tab story", (260.0, 400.0, 300.0, 24.0))])],
        "AXStandardWindow",
    )
    app = ax("AXApplication", "Brave Browser", (0.0, 1117.0, 0.0, 0.0), [tabs, toolbar, page_window, behind])
    return app, page_window


def page_texts(app: dict, focused: dict) -> list[str]:
    """The static text of the windows a read walks (OCR sees the front window only)."""
    from karta.base.ax_walk import AxAttrs, front_windows

    out, todo = (
        [],
        front_windows(app["children"], focused, lambda n: AxAttrs(n["role"], n["label"], n["frame"]), lambda n: n["subrole"]),
    )
    while todo:
        node = todo.pop(0)
        if node["role"] == "AXStaticText" and node["label"]:
            out.append(node["label"])
        todo += node["children"]
    return out


def read_tree(results: bool) -> Screen:
    """The screen the harness gets from the AX walk of the full-screen tree (no OCR): mac.walk_app, then kinds and
    the browser chrome above the page's top edge, as MacHarnessEnv._read does."""
    from karta.base.ax_walk import AxAttrs
    from karta.harness.mac import Ref, kind_of, mark_chrome, walk_app

    app, focused = full_screen_tree(results)
    found, _hidden = walk_app(
        app,
        focused,
        lambda n: n["children"],
        lambda n: AxAttrs(n["role"], n["label"], n["frame"]),
        lambda n: [],
        lambda n: n["subrole"],
        *DISPLAY,
    )
    items = []
    for label in page_texts(app, focused):  # what OCR of the front window reads besides the controls
        items.append(Item(label, "text", region="top", source="ocr"))
    for node in found:
        region = "top" if node.y < 300 else "middle"
        search = "AXSearchField" in (node.role, node.ref.get("subrole"))  # as _ax_item reads it off the node
        item = Item(node.label, kind_of(node.role), region=region, search=search, ref=Ref(node.x, node.y, node.w, node.h))
        item = mark_chrome(item, PAGE[1])
        items.append(replace(item, value=SHOP if item.name.startswith("Address") else ""))
    return Screen("Brave Browser", items, url=SHOP + ("search" if results else ""), profile="Personal")


def full_screen() -> LikeMac:
    return LikeMac(
        {"home": read_tree(False), "results": read_tree(True)},
        "home",
        {("home", "press return"): "results"},
    )


FULL_PLAN = """write blue mugs | where: Search
submit | expect: results for
done: results for"""


# ---------------------------------------------------------------- the cases

SEEDS = {
    "seed-sent-before": dict(
        task="email sam@example.com that the report is ready",
        mac=sent_before,
        plan=SENT_PLAN,
        tabs=[Tab("Brave Browser", 1, 1, INBOX, "Inbox", "Personal")],
        expected={"done": False, "brain_calls": None, "why": "'Message sent' was on the page before this send, which failed"},
    ),
    "seed-stale-after-open": dict(
        task="add buy stamps to my list on tasks.example",
        mac=stale_after_open,
        plan=STALE_PLAN,
        tabs=[Tab("Brave Browser", 1, 1, NEWS, "News", "Personal")],
        expected={"done": True, "brain_calls": 1, "why": "the first read after opening the site still showed the last one"},
    ),
    "seed-full-screen": dict(
        task="search the shop for blue mugs",
        mac=full_screen,
        plan=FULL_PLAN,
        tabs=[Tab("Brave Browser", 1, 1, SHOP, "Shop", "Personal")],
        expected={"done": True, "brain_calls": 1, "why": "a full-screen browser's page must be read, its other window not"},
    ),
}


def record(name: str) -> tuple[dict, loop.TaskResult]:
    seed = SEEDS[name]
    mac = seed["mac"]()
    rec = Recorder(mac, seed["task"], HERE)
    parts = rec.wrap(
        laya=Laya(OracleAgent(), embed, gates=None),
        brain=PlanBrain(seed["plan"]),
        facts=MacMap(FACTS, tabs=list(seed["tabs"])),
        confirm=lambda question: True,
        ask=lambda question: "",
    )
    parts["refresh"] = lambda mac_map: mac_map  # the tabs stay as given: no AppleScript
    saved = loop.speak
    loop.speak = lambda text: None
    try:
        result = loop.run_task(seed["task"], rec, **parts)
    finally:
        loop.speak = saved
    return rec.record(result), result


def main() -> None:
    for name, seed in SEEDS.items():
        raw, result = record(name)
        case = scrub_record(raw, SALT)
        expected = dict(seed["expected"])
        if expected["brain_calls"] is None:
            expected["brain_calls"] = len(case["brain"])
        folder = HERE / name
        folder.mkdir(exist_ok=True)
        (folder / "run.json").write_text(json.dumps(case, indent=1, ensure_ascii=False) + "\n")
        old = folder / "expected.json"
        if old.exists():  # keep a hand-set xfail
            expected = {**expected, **{k: v for k, v in json.loads(old.read_text()).items() if k == "xfail"}}
        old.write_text(json.dumps(expected, indent=1) + "\n")
        print(f"{name}: done={result.done} ({result.reason}) · brain calls {result.brain_calls} · {len(raw['actions'])} actions")
        for line in result.ledger:
            print(f"   {line}")


if __name__ == "__main__":
    main()
