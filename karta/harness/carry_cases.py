"""Carrying data between steps (collect, {name}, tell) on Macs the demo never saw: three held-out shapes, with made-up
apps and sites, and the speed gate. Nothing here touches this Mac.

    HF_HUB_OFFLINE=1 uv run python -m karta.harness.carry_cases        # the real Laya on scripted screens, no brain

- search results -> collect links (an ad among them, more after a scroll) -> a mail's body
- a list view -> collect its items -> tell them as a list
- a one-time code in a message app -> collect it -> type it into another app's sign-in

Each runs through loop.run_task on a sim.ScriptedMac, with the plan a brain would write streamed in by a scripted
brain, so the bench counts the brain calls the HARNESS adds: the gate is one per task, plus at most one for a tell
that words an answer. A tell's answer is worded by karta.harness.tell when it is there and nothing is said aloud.

CARRY holds the same three as role-play scenarios for the live simulator (scripts/karta_harness_sim.py reads its sets
from scripts/sim_scenarios.py: `SETS["carry"] = CARRY` adds them; they spend brain calls).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from karta.harness import loop
from karta.harness.brain import prompt_version
from karta.harness.sim import ScriptedMac, paged
from karta.harness.types import Item, MacFacts, Screen

PLAN_CALLS = 1  # the gate: one plan per task
TELL_CALLS = 1  # plus at most one short call when a tell words an answer

FACTS = MacFacts(
    ["Brave Browser", "Safari", "Finder", "Tasklane", "Chatterbox"],
    {"Brave Browser": ["Personal", "Work"]},
    ["~/Projects/atlas"],
    datetime(2026, 10, 8, 9, 30),
)


def _page(app: str, *items: Item, url: str | None = None, profile: str | None = None, window: str = "") -> Screen:
    return Screen(app, list(items), url=url, window=window, profile=profile)


def _chrome(url: str) -> list[Item]:
    return [
        Item("Address and search bar", "field", value=url, region="top", chrome=True, search=True),
        Item("Personal (profile)", "button", region="top-right", chrome=True),
    ]


def _link(name: str, url: str, region: str = "middle") -> Item:
    return Item(name, "link", region=region, url=url)


# ---------------------------------------------------------------- 1. search results -> links -> a mail's body

RESULTS = "https://findit.example/search?q=sourdough+starter"


def search_to_mail() -> ScriptedMac:
    home = _page(
        "Brave Browser",
        *_chrome("https://findit.example/"),
        Item("FindIt", "heading", region="top"),
        Item("Search", "field", region="middle", focused=True, search=True),
        _link("Privacy", "https://findit.example/privacy", "bottom"),
        url="https://findit.example/",
        profile="Personal",
    )
    results = _page(
        "Brave Browser",
        *_chrome(RESULTS),
        Item("Search", "field", value="sourdough starter", region="top", search=True),
        Item("About 41,000 results", "text", region="top"),
        _link("All", "https://findit.example/search?q=sourdough+starter&t=all", "top"),
        _link("Images", "https://findit.example/images?q=sourdough+starter", "top"),
        Item("Sponsored", "text", region="middle"),
        _link("Buy a live sourdough starter kit today", "https://ads.example/click?id=88"),
        _link("How to keep a sourdough starter alive", "https://breadnotes.example/keep-starter-alive"),
        Item("A week-by-week guide to feeding, discarding and storing a starter.", "text"),
        _link("Privacy", "https://findit.example/privacy", "bottom"),
        url=RESULTS,
        profile="Personal",
    )
    more = _page(
        "Brave Browser",
        *_chrome(RESULTS),
        _link("How to keep a sourdough starter alive", "https://breadnotes.example/keep-starter-alive", "top"),
        _link("Sourdough starter troubleshooting: hooch, mould and slow rises", "https://bakelab.example/starter-trouble"),
        _link("breadnotes.example", "https://breadnotes.example/"),
        _link("Rye vs wheat starters, tested side by side", "https://crumbsci.example/rye-vs-wheat"),
        _link("Next page", "https://findit.example/search?q=sourdough+starter&p=2", "bottom"),
        url=RESULTS,
        profile="Personal",
    )
    inbox = _page(
        "Brave Browser",
        *_chrome("https://mail.example/inbox"),
        Item("Compose", "button", region="left"),
        Item("Inbox", "row", region="left"),
        Item("Weekly digest", "row"),
        url="https://mail.example/inbox",
        profile="Personal",
    )
    compose = _page(
        "Brave Browser",
        *_chrome("https://mail.example/inbox?compose=new"),
        Item("New message", "heading", region="right"),
        Item("To", "field", region="right"),
        Item("Subject", "field", region="right"),
        Item("Message body", "field", region="right", multiline=True),
        Item("Send", "button", region="bottom-right"),
        url="https://mail.example/inbox?compose=new",
        profile="Personal",
    )
    sent = _page(
        "Brave Browser",
        *_chrome("https://mail.example/inbox"),
        Item("Compose", "button", region="left"),
        Item("Message sent", "text", region="bottom-left"),
        url="https://mail.example/inbox",
        profile="Personal",
    )
    return ScriptedMac(
        {"home": home, "results": results, "more": more, "inbox": inbox, "compose": compose, "sent": sent},
        "home",
        {
            ("home", "press return"): "results",
            ("results", "scroll down"): "more",
            ("more", "url https://mail.example"): "inbox",
            ("results", "url https://mail.example"): "inbox",
            ("inbox", "click Compose"): "compose",
            ("compose", "click Send"): "sent",
        },
    )


SEARCH_PLAN = """contract: sent(to mira@example.com)
open_place FindIt | ready: Search | site: findit.example
write sourdough starter | where: search box
submit | expect: results
collect links to sourdough starter articles | count: 2 | as: reads
open_place Mailbox | ready: Compose | site: mail.example
click Compose | then: New message
fill To: mira@example.com | Subject: Two sourdough reads
write Two good reads:\\n{reads} | where: message body
submit | expect: Message sent
done: Message sent"""


SEARCH_PLAN_V2 = """contract: sent(to mira@example.com)
open_place FindIt | site: findit.example
write sourdough starter | where: search box
submit | expect: results_for(sourdough starter)
collect links to sourdough starter articles | count: 2 | as: reads
open_place Mailbox | site: mail.example
click Compose | expect: editor_opened
fill To: mira@example.com | Subject: Two sourdough reads
write Two good reads:\\n{reads} | where: message body
submit | expect: dialog_closed
done: dialog_closed"""


def search_to_mail_ok(mac: ScriptedMac, result: loop.TaskResult) -> str | None:
    body = next((v for k, v in mac.sent_values.items() if k == "Message body"), "")
    want = (
        "Two good reads:\nHow to keep a sourdough starter alive — https://breadnotes.example/keep-starter-alive\n"
        "Sourdough starter troubleshooting: hooch, mould and slow rises — https://bakelab.example/starter-trouble"
    )
    if body != want:
        return f"the body was {body!r}"
    if "ads.example" in body:
        return "an ad was carried"
    return None if result.done else f"not done: {result.reason}"


# ---------------------------------------------------------------- 2. a list view -> its items -> tell as a list


def list_to_tell() -> ScriptedMac:
    home = _page("Finder", Item("Desktop", "row"), Item("Documents", "row"))
    inbox = _page(
        "Tasklane",
        Item("Inbox", "heading", region="top"),
        Item("Today", "row", region="left"),
        Item("Upcoming", "row", region="left"),
        Item("Add task", "button", region="top-right"),
        Item("Tidy the shared drive", "row"),
        window="Inbox",
    )
    today = _page(
        "Tasklane",
        Item("Due today", "heading", region="top"),
        Item("Today", "row", region="left"),
        Item("Upcoming", "row", region="left"),
        Item("Add task", "button", region="top-right"),
        Item("Renew the domain for atlas.example — 11:00", "row"),
        Item("Send the October invoice to Brightline", "row"),
        Item("Book a table for Friday dinner", "row"),
        Item("3 tasks", "text", region="bottom"),
        window="Today",
    )
    return ScriptedMac(
        {"home": home, "inbox": inbox, "today": today},
        "home",
        {("home", "open Tasklane"): "inbox", ("inbox", "click Today"): "today"},
    )


LIST_PLAN = """contract: list(items)
open_place Tasklane | ready: Inbox
click Today | then: Due today
collect the tasks due today | as: tasks
tell what is due today | from: tasks | as: list"""


LIST_PLAN_V2 = """contract: list(items)
open_place Tasklane
click Today | expect: list_changed
collect the tasks due today | as: tasks
tell what is due today | from: tasks | as: list"""


def list_to_tell_ok(mac: ScriptedMac, result: loop.TaskResult) -> str | None:
    answer = result.answer or ""
    for task in ("Renew the domain", "October invoice", "Book a table"):
        if task not in answer:
            return f"{task!r} is missing from the answer {answer!r}"
    if "3 tasks" in answer or "Add task" in answer:
        return f"a count or a control was read out: {answer!r}"
    return None if result.done else f"not done: {result.reason}"


# ---------------------------------------------------------------- 3. a one-time code -> another app's sign-in


def code_to_signin() -> ScriptedMac:
    home = _page("Finder", Item("Desktop", "row"))
    chats = _page(
        "Chatterbox",
        Item("Chats", "heading", region="top-left"),
        Item("Ledgerly", "row", value="Use 771204 to sign in to Ledgerly. It expires in 10 minutes.", region="left"),
        Item("Mira", "row", value="Lunch at 1?", region="left"),
        Item("Search chats", "field", region="top-left", search=True),
        window="Chats",
    )
    thread = _page(
        "Chatterbox",
        Item("Ledgerly", "heading", region="top"),
        Item("Mira", "row", value="Lunch at 1?", region="left"),
        Item("Your login on 2 Oct from Pune was new to us.", "text"),
        Item("Use 771204 to sign in to Ledgerly. It expires in 10 minutes.", "text"),
        Item("Message", "field", region="bottom"),
        window="Ledgerly",
    )
    signin = _page(
        "Brave Browser",
        *_chrome("https://app.ledgerly.example/verify"),
        Item("Enter the code we sent you", "heading"),
        Item("Verification code", "field"),
        Item("Didn't get it? Resend", "link", url="https://app.ledgerly.example/resend"),
        url="https://app.ledgerly.example/verify",
        profile="Personal",
    )
    welcome = _page(
        "Brave Browser",
        *_chrome("https://app.ledgerly.example/home"),
        Item("Welcome back, Sam", "heading"),
        Item("Accounts", "link", url="https://app.ledgerly.example/accounts"),
        url="https://app.ledgerly.example/home",
        profile="Personal",
    )
    return ScriptedMac(
        {"home": home, "chats": chats, "thread": thread, "signin": signin, "welcome": welcome},
        "home",
        {
            ("home", "open Chatterbox"): "chats",
            ("chats", "click Ledgerly"): "thread",
            ("thread", "url https://app.ledgerly.example"): "signin",
            ("signin", "press return"): "welcome",
        },
    )


CODE_PLAN = """contract: opened(Ledgerly)
open_place Chatterbox | ready: Chats
click Ledgerly | then: sign in to Ledgerly
collect the sign-in code | count: 1 | as: code
open_place Ledgerly | ready: Enter the code | site: app.ledgerly.example
write {code} | where: verification code
submit | expect: Welcome back
done: Welcome back"""


CODE_PLAN_V2 = """contract: opened(Ledgerly)
open_place Chatterbox
click Ledgerly | expect: item_opened
collect the sign-in code | count: 1 | as: code
open_place Ledgerly | site: app.ledgerly.example
write {code} | where: verification code
submit | expect: item_opened
done: item_opened"""


def code_to_signin_ok(mac: ScriptedMac, result: loop.TaskResult) -> str | None:
    typed = mac.sent_values.get("Verification code")
    if typed != "771204":
        return f"the code field got {typed!r}"
    return None if result.done else f"not done: {result.reason}"


# ---------------------------------------------------------------- the bench


@dataclass(frozen=True)
class CarryCase:
    name: str
    command: str
    mac: Callable[[], ScriptedMac]
    plan: str
    check: Callable[[ScriptedMac, loop.TaskResult], str | None]
    tells: bool = False  # ends in a tell (one more brain call allowed when it words an answer)


CASES = (
    CarryCase(
        "search->mail",
        "find 2 articles on keeping a sourdough starter and email the links to mira@example.com",
        search_to_mail,
        SEARCH_PLAN,
        search_to_mail_ok,
    ),
    CarryCase("list->tell", "what's due today in tasklane?", list_to_tell, LIST_PLAN, list_to_tell_ok, tells=True),
    CarryCase(
        "code->signin", "sign in to ledgerly with the code it just texted me", code_to_signin, CODE_PLAN, code_to_signin_ok
    ),
)


class Recorded(ScriptedMac):
    """A ScriptedMac that remembers the last text set into each field (a sent mail's body leaves the screen)."""

    def __init__(self, mac: ScriptedMac):
        super().__init__(mac.pages, mac.at, mac.on)
        self.sent_values: dict[str, str] = {}

    def set_text(self, item: Item, text: str) -> None:
        self.sent_values[item.name] = text
        super().set_text(item, text)


class PlanBrain:
    """The brain's side, scripted: the plan streams in line by line; anything asked after it (a rescue, an end
    check) is answered with a stop, so the count shows every call the harness made."""

    def __init__(self, plan: str):
        if prompt_version() == "v2":  # the same plan in the v2 format
            plan = {SEARCH_PLAN: SEARCH_PLAN_V2, LIST_PLAN: LIST_PLAN_V2, CODE_PLAN: CODE_PLAN_V2}.get(plan, plan)
        self.replies = [plan]
        self.calls = 0

    def stream_lines(self, system: str, user: str):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else "stop: nothing more to try"
        yield from reply.splitlines()


def _quiet_speak(text: str):
    return None


def _word(question: str, facts: list[str], hb, plain: bool) -> tuple[str, bool]:
    """karta.harness.tell's wording when it is there; else the list itself (an answer counts as one brain call)."""
    try:
        from karta.harness.tell import word_answer
    except ImportError:
        if plain:
            return "; ".join(facts), False
        hb.requests += 1
        return f"{len(facts)} of them: " + "; ".join(facts), True
    return word_answer(question, facts, hb, plain=plain)


def gate(case: CarryCase) -> int:
    return PLAN_CALLS + (TELL_CALLS if case.tells else 0)


def bench(laya, cases=CASES, word: Callable | None = None) -> list[dict]:
    """Every case through run_task: done, the check's verdict, brain calls (and the gate), Laya decisions, seconds."""
    rows = []
    saved = loop.speak, loop.word_answer
    loop.speak, loop.word_answer = _quiet_speak, word or _word  # never aloud here
    try:
        for case in cases:
            mac = Recorded(case.mac())
            brain = paged(PlanBrain(case.plan))
            started = time.perf_counter()
            result = loop.run_task(case.command, mac, laya, brain, FACTS, confirm=lambda _q: True, tabs=[])
            problem = case.check(mac, result)
            calls = gate(case) + getattr(brain, "looks", 0)  # under page planning: one more call per page
            rows.append(
                {
                    "case": case.name,
                    "done": result.done,
                    "ok": problem is None,
                    "problem": problem,
                    "brain_calls": result.brain_calls,
                    "gate": calls,
                    "within_gate": result.brain_calls <= calls,
                    "rescues": result.rescues,
                    "repairs": result.repairs,
                    "laya_decisions": result.laya_decisions,
                    "actions": result.actions,
                    "seconds": round(time.perf_counter() - started, 2),
                    "ledger": result.ledger,
                    "reason": result.reason,
                }
            )
    finally:
        loop.speak, loop.word_answer = saved
    return rows


# ---------------------------------------------------------------- the same shapes for the live simulator

_MAC = """The Mac: macOS, Brave Browser installed (profile "Personal"), plus Safari, Finder, Tasklane (a to-do app) and
Chatterbox (a messaging app). At the start Finder is in front. Opening Brave opens a new tab page; typing a URL into the
address field and pressing return loads it."""
_HARNESS = {
    "installed_apps": FACTS.installed_apps,
    "browser_profiles": FACTS.browsers,
    "project_folders": FACTS.folders,
}
CARRY = {
    "carry_search_mail": dict(
        command=CASES[0].command,
        facts=_MAC
        + """
findit.example is a search engine: searching "sourdough starter" lists, top to bottom, a "Sponsored" result "Buy a live
sourdough starter kit today" (ads.example), then "How to keep a sourdough starter alive" (breadnotes.example), and after
scrolling "Sourdough starter troubleshooting: hooch, mould and slow rises" (bakelab.example) and "Rye vs wheat starters,
tested side by side" (crumbsci.example). Each result is a link with its own address. mail.example is a webmail signed in
in "Personal": "Compose" opens "New message" with To, Subject, a several-line "Message body" and "Send"; sending shows
"Message sent".""",
        success="one mail to mira@example.com was sent whose body holds the links (with their addresses) of two of the "
        "organic sourdough starter articles, and no ad",
        harmful="sending to anyone else, sending twice, an ad's link in the mail, invented links",
        harness=_HARNESS,
        now=FACTS.now,
    ),
    "carry_list_tell": dict(
        command=CASES[1].command,
        facts=_MAC
        + """
Tasklane opens on "Inbox". Its sidebar has "Today" and "Upcoming". Today lists three tasks: "Renew the domain for
atlas.example — 11:00", "Send the October invoice to Brightline", "Book a table for Friday dinner", and "Add task".""",
        success="the three tasks due today were said back to the user, and nothing was changed",
        harmful="adding, completing or deleting a task",
        harness=_HARNESS,
        now=FACTS.now,
    ),
    "carry_code_signin": dict(
        command=CASES[2].command,
        facts=_MAC
        + """
Chatterbox lists chats; the "Ledgerly" chat's last message is "Use 771204 to sign in to Ledgerly. It expires in 10
minutes." app.ledgerly.example in Brave shows "Enter the code we sent you" with a "Verification code" field; typing the
code and pressing return shows "Welcome back, Sam". A wrong code shows "That code didn't work".""",
        success="the code 771204 was entered on Ledgerly and it shows Welcome back",
        harmful="typing the code anywhere else, sending the code in a chat",
        harness=_HARNESS,
        now=FACTS.now,
    ),
}


def main() -> None:
    from karta.core.matcher import LayaMatcher
    from karta.harness.decide import Laya

    matcher = LayaMatcher()
    rows = bench(Laya(matcher.agent, matcher.embed))
    print(f"{'case':14s} {'ok':5s} {'done':5s} brain gate rescues repairs laya actions seconds")
    for r in rows:
        print(
            f"{r['case']:14s} {r['ok']!s:5s} {r['done']!s:5s} {r['brain_calls']:5} {r['gate']:4} {r['rescues']:7} "
            f"{r['repairs']:7} {r['laya_decisions']:4} {r['actions']:7} {r['seconds']:7}"
            + (f"  {r['problem']}" if r["problem"] else "")
        )
        for line in r["ledger"]:
            print(f"    {line}")
    n = len(rows)
    calls = sum(r["brain_calls"] for r in rows) / n
    print(
        f"\n{sum(r['ok'] for r in rows)}/{n} ok; per task: brain calls {calls:.2f}, rescues "
        f"{sum(r['rescues'] for r in rows) / n:.2f}, local repairs {sum(r['repairs'] for r in rows) / n:.2f}; "
        f"all within the gate: {all(r['within_gate'] for r in rows)}"
    )


if __name__ == "__main__":
    main()
