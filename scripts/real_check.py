"""Six misses on made-up apps, fixed by the real brain (the claude CLI), per KARTA_PLAN mode: blind rescue, auto look,
page planning. `uv run python scripts/real_check.py <blind|auto|page>` prints one JSON object (done, calls, ledgers).
A scripted blind plan with one step wrong; the real brain answers every later call. Page plans with the real brain
from the first call. Fakes for the screen and Laya (sure only of a name the screen shows exactly)."""

import json
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))
from test_harness_tools import FACTS, FakeEnv, FakeLaya, ch, page

from karta.brain.claude_code import ClaudeCodeBrain
from karta.harness import loop
from karta.harness.brain import HarnessBrain
from karta.harness.decide import NONE, labels
from karta.harness.types import Item, MacFacts

loop.speak = lambda t: None
REAL = ClaudeCodeBrain()
CALLS = {"n": 0}


class Brain:
    def __init__(self, plan):
        self.plan = plan

    def stream_lines(self, system, user, model=None):
        if self.plan is not None:
            plan, self.plan = self.plan, None
            yield from plan.splitlines()
            return
        CALLS["n"] += 1
        lines = list(REAL.stream_lines(system, user, model=model) if model else REAL.stream_lines(system, user))
        LOG.append({"user_tail": user[-300:], "reply": lines})
        yield from lines


LOG = []


def literal():
    def target(intent, items):
        names = labels(items)
        hit = next((k for k, v in names.items() if v.name.lower() == intent.lower().strip()), None)
        return ch("D3 target", (hit, 0.95), (NONE, 0.05)) if hit else ch("D3 target", (NONE, 0.95))

    return FakeLaya(target=target)


def checked(env, page_name, name):
    def go(e):
        scr = e.pages[page_name]
        scr.items = [replace(i, checked=True) if i.name == name else i for i in scr.items]

    return go


def scenarios():
    out = []
    # 1. a control the app calls otherwise
    lst = page("Quillery", Item("Search notes", "field", region="top"), Item("Compose", "button", region="top-right"))
    new = page("Quillery", Item("Note text", "field", focused=True), Item("Keep", "button"))
    kept = page("Quillery", Item("Compose", "button", region="top-right"), Item("buy oat milk", "row"))
    out.append(
        (
            "renamed",
            "add a note buy oat milk in quillery",
            lambda: FakeEnv(
                {"l": replace(lst, items=list(lst.items)), "n": replace(new, items=list(new.items)), "k": kept},
                "l",
                {("l", "click Compose"): "n", ("n", "click Keep"): "k"},
            ),
            "click Add Note | expect: editor_opened\nwrite buy oat milk | where: Note text\n"
            "click Keep | expect: new_item(buy oat milk)\ndone: new_item(buy oat milk)",
        )
    )
    # 2. only a menu command does it
    home = page(
        "Ledgerly",
        Item("Invoices", "heading"),
        Item("March invoice", "row"),
        Item("File > Export Report…", "button", menu=True),
        Item("Edit > Copy", "button", menu=True),
    )
    dlg = page(
        "Ledgerly",
        Item("Export as PDF", "text", dialog=True),
        Item("Save", "button", dialog=True),
        Item("Cancel", "button", dialog=True, cancels=True),
    )
    after = page("Ledgerly", Item("Invoices", "heading"), Item("March invoice", "row"))
    out.append(
        (
            "menu-only",
            "export the invoices report in ledgerly",
            lambda: FakeEnv(
                {"h": home, "d": dlg, "a": after}, "h", {("h", "click File > Export Report…"): "d", ("d", "click Save"): "a"}
            ),
            "click Export | expect: dialog_opened\nclick Save | expect: dialog_closed\ndone: dialog_closed",
        )
    )
    # 3. a search box behind its button
    shut = page(
        "Brave Browser",
        Item("Open search", "button", region="top"),
        Item("Spring bouquets", "link"),
        url="https://petalpost.example/",
    )
    opened = page(
        "Brave Browser",
        Item("Find flowers", "field", focused=True, region="top"),
        Item("Spring bouquets", "link"),
        url="https://petalpost.example/",
    )
    results = page(
        "Brave Browser",
        Item("Find flowers", "field", value="roses", region="top"),
        Item("Red roses, a dozen", "link"),
        url="https://petalpost.example/search?q=roses",
    )

    def petal():
        env = FakeEnv(
            {"s": shut, "o": replace(opened, items=list(opened.items)), "r": results},
            "s",
            {("s", "click Open search"): "o", ("o", "press return"): "r"},
        )
        return env

    out.append(
        (
            "hidden-search",
            "search petalpost.example for roses",
            petal,
            "write roses | where: Search\nsubmit | expect: results_for(roses)\ndone: results_for(roses)",
        )
    )
    # 4. a conversation to open first
    inbox = page(
        "Harborchat", Item("Search chats", "field", region="top"), Item("Odalys Fenn", "row"), Item("Bram Ostrow", "row")
    )
    chat = page(
        "Harborchat",
        Item("Odalys Fenn", "heading"),
        Item("Message", "field", region="bottom"),
        Item("Send", "button", region="bottom"),
    )
    sent = page(
        "Harborchat", Item("Odalys Fenn", "heading"), Item("Message", "field", region="bottom"), Item("see you at noon", "text")
    )
    out.append(
        (
            "open-first",
            "message Odalys Fenn on harborchat: see you at noon",
            lambda: FakeEnv(
                {"i": inbox, "c": replace(chat, items=list(chat.items)), "s": sent},
                "i",
                {("i", "click Odalys Fenn"): "c", ("c", "click Send"): "s"},
            ),
            "write see you at noon | where: Message\nsubmit | expect: new_item(see you at noon)\ndone: new_item(see you at noon)",
        )
    )
    # 5. an icon named otherwise
    rec = page(
        "Crumbly",
        Item("Lemon tart", "heading"),
        Item("Bookmark", "button", region="top-right"),
        Item("Print", "button", region="top-right"),
    )

    def crumbly():
        env = FakeEnv({"r": replace(rec, items=list(rec.items))}, "r")
        env.on[("r", "click Bookmark")] = lambda e: setattr(
            e.pages["r"],
            "items",
            [Item("Remove bookmark", "button", region="top-right") if i.name == "Bookmark" else i for i in e.pages["r"].items],
        )
        return env

    out.append(
        ("icon-name", "save the lemon tart recipe in crumbly", crumbly, "click Save recipe | expect: toggled\ndone: toggled")
    )
    # 6. a tab to open before the switch
    gen = page(
        "Lumen",
        Item("General", "tab", region="left"),
        Item("Display", "tab", region="left"),
        Item("Launch at login", "checkbox", checked=False),
    )
    disp = page(
        "Lumen",
        Item("General", "tab", region="left"),
        Item("Display", "tab", region="left"),
        Item("Use dark appearance", "checkbox", checked=False),
    )

    def lumen():
        env = FakeEnv({"g": gen, "d": replace(disp, items=list(disp.items))}, "g", {("g", "click Display"): "d"})
        env.on[("d", "click Use dark appearance")] = checked(env, "d", "Use dark appearance")
        return env

    out.append(("tab-first", "turn on dark mode in lumen", lumen, "click Dark mode | expect: toggled(Dark mode)\ndone: toggled"))
    return out


FACTS2 = MacFacts(["Quillery", "Ledgerly", "Harborchat", "Crumbly", "Lumen", "Finder"], FACTS.browsers, [], FACTS.now)


def run(mode):
    rows = []
    for name, task, mk, plan in scenarios():
        CALLS["n"] = 0
        env = mk()
        hb = HarnessBrain(Brain(None if mode == "page" else plan), prompt="v2", plan=mode)
        try:
            r = loop.run_task(task, env, literal(), hb, FACTS2, confirm=lambda q: True, tabs=[])
            rows.append(
                {
                    "case": name,
                    "done": r.done,
                    "real_calls": CALLS["n"],
                    "calls": r.brain_calls,
                    "kinds": [b["kind"] for b in r.brain],
                    "ledger": r.ledger,
                    "reason": r.reason,
                }
            )
        except Exception as e:
            rows.append({"case": name, "done": False, "real_calls": CALLS["n"], "error": repr(e)})
    return rows


if __name__ == "__main__":
    mode = sys.argv[1]
    rows = run(mode)
    print(json.dumps({"mode": mode, "rows": rows, "log": LOG}, indent=1))
    REAL.close()
