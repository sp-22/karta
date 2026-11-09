"""Pinning on a Mac the demo never saw: made-up sites, a scripted brain, and the brain-call gate per run. Nothing here
touches this Mac.

    HF_HUB_OFFLINE=1 uv run python -m karta.harness.pin_cases         # the real Laya on scripted screens, no brain
    uv run python -m karta.harness.pin_cases --sim                     # the role-played Mac (spends brain calls)

Each case is a sequence of runs through loop.run_task sharing one recipes.RecipeBook, on sim.ScriptedMac screens:

- pin, then the same command again: 1 call, then 0 (an exact replay)
- pin, then the same command with new values: 1 call, then 0 (code binds the values from the command's own words)
- pin, then the same shape reworded: 1 call, then 1 (the plan call, answered with a `use:` line)
- pin, then the site moves one control: 1 call, then 1 rescue (the recipe becomes v2), then 0 on the new site
- pin, then name it and say the name: 1 call, 0 for the naming, 0 for the replay

The task searches a made-up site for a topic, collects links and mails them: a topic, a count and an address become
the template's values. Sending asks for the user's yes on every run, replays too.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from karta.harness import loop
from karta.harness.brain import prompt_version
from karta.harness.carry_cases import Recorded
from karta.harness.recipes import RecipeBook
from karta.harness.sim import ScriptedMac, paged
from karta.harness.types import Item, MacFacts, Screen

FACTS = MacFacts(["Brave Browser", "Safari", "Finder"], {"Brave Browser": ["Personal"]}, [], datetime(2026, 10, 9, 8, 0))

TOPICS = {
    "sourdough starter": [
        ("How to keep a sourdough starter alive", "https://breadnotes.example/keep-starter-alive"),
        ("Sourdough starter troubleshooting: hooch and slow rises", "https://bakelab.example/starter-trouble"),
        ("Rye vs wheat sourdough starters, tested side by side", "https://crumbsci.example/rye-vs-wheat"),
    ],
    "kombucha": [
        ("A first batch of kombucha, step by step", "https://brewday.example/first-kombucha"),
        ("Why your kombucha is flat and how to fix it", "https://fizzlab.example/flat-kombucha"),
        ("Second fermentation flavours for kombucha", "https://brewday.example/kombucha-flavours"),
        ("Kombucha SCOBY care between batches", "https://cultures.example/scoby-care"),
    ],
}


def _chrome(url: str) -> list[Item]:
    return [
        Item("Address and search bar", "field", value=url, region="top", chrome=True, search=True),
        Item("Personal (profile)", "button", region="top-right", chrome=True),
    ]


def _page(*items: Item, url: str) -> Screen:
    return Screen("Brave Browser", [*_chrome(url), *items], url=url, profile="Personal")


class SearchMail(Recorded):
    """A search site whose results follow the query typed, and a webmail. `moved`: the webmail dropped its Compose
    button; a new message starts from the Message menu."""

    def __init__(self, moved: bool = False):
        home = _page(
            Item("FindIt", "heading", region="top"),
            Item("Search", "field", region="middle", focused=True, search=True),
            Item("Privacy", "link", region="bottom", url="https://findit.example/privacy"),
            url="https://findit.example/",
        )
        pages = {"home": home}
        for topic, links in TOPICS.items():
            url = f"https://findit.example/search?q={topic.replace(' ', '+')}"
            pages[f"results:{topic}"] = _page(
                Item("Search", "field", value=topic, region="top", search=True),
                Item(f"About {len(links) * 1000:,} results", "text", region="top"),
                *[Item(title, "link", region="middle", url=href) for title, href in links],
                Item("Privacy", "link", region="bottom", url="https://findit.example/privacy"),
                url=url,
            )
        inbox = [Item("Inbox", "row", region="left"), Item("Weekly digest", "row")]
        compose_control = (
            [Item("Message > New Message", "menu", menu=True)] if moved else [Item("Compose", "button", region="left")]
        )
        pages["inbox"] = _page(*compose_control, *inbox, url="https://mail.example/inbox")
        pages["compose"] = _page(
            Item("New message", "heading", region="right"),
            Item("To", "field", region="right"),
            Item("Subject", "field", region="right"),
            Item("Message body", "field", region="right", multiline=True),
            Item("Send", "button", region="bottom-right"),
            url="https://mail.example/inbox?compose=new",
        )
        pages["sent"] = _page(
            *compose_control, Item("Message sent", "text", region="bottom-left"), url="https://mail.example/inbox"
        )
        on = {
            ("inbox", "click Message > New Message" if moved else "click Compose"): "compose",
            ("compose", "click Send"): "sent",
        }
        for key in pages:
            if key.startswith("results:"):
                on[(key, "url https://mail.example")] = "inbox"
        super().__init__(ScriptedMac(pages, "home", on))

    def press(self, keys: str) -> None:
        if self.at == "home" and keys == "return":  # the results follow the query in the box
            self.log.append(f"press {keys}")
            self.at = f"results:{self.field_value('Search').strip().lower()}"
            if self.at not in self.pages:
                self.at = "home"
            return
        super().press(keys)


FIRST = "email 2 sourdough starter links from findit to mira@example.com"
BOUND = "email 3 kombucha links from findit to arjun@example.com"  # the template with other values: code binds them
SAME_SHAPE = "email 3 kombucha links to arjun@example.com from findit"  # reworded: the brain sees the likeness
PLAN = """contract: sent(to mira@example.com)
open_place FindIt | ready: Search | site: findit.example
write sourdough starter | where: search box
submit | expect: results
collect links about sourdough starter | count: 2 | as: reads
open_place Mailbox | ready: Inbox | site: mail.example
click Compose | then: New message
fill To: mira@example.com | Subject: 2 sourdough starter reads
write Worth a look:\\n{reads} | where: message body
submit | expect: Message sent
done: Message sent"""
PLAN_V2 = """contract: sent(to mira@example.com)
open_place FindIt | site: findit.example
write sourdough starter | where: search box
submit | expect: results_for(sourdough starter)
collect links about sourdough starter | count: 2 | as: reads
open_place Mailbox | site: mail.example
click Compose | expect: editor_opened
fill To: mira@example.com | Subject: 2 sourdough starter reads
write Worth a look:\\n{reads} | where: message body
submit | expect: dialog_closed
done: dialog_closed"""
USE = "use: r1 | p1: 3 | p2: kombucha | p3: arjun@example.com"
FIX = "click Message > New Message | then: New message"
FIX_V2 = "click Message > New Message | expect: editor_opened"
NAME = "call that my reading digest"
SAY_NAME = "do my reading digest"


@dataclass(frozen=True)
class Run:
    command: str
    gate: int  # brain calls allowed for this run
    reply: str = ""  # the scripted brain's reply (a plan, a use: line or a rescue's fix)
    moved: bool = False
    to: str = ""  # the address the mail must go to ("" when the run sends nothing)
    topic: str = ""  # the topic whose links the mail must carry
    count: int = 2  # how many of them
    rescues: int = 0
    version: int = 0  # the recipe's version after the run (0: not checked)


@dataclass(frozen=True)
class PinCase:
    name: str
    runs: tuple[Run, ...]


PINNED = Run(FIRST, 1, PLAN, to="mira@example.com", topic="sourdough starter", version=1)
CASES = (
    PinCase("exact", (PINNED, Run(FIRST, 0, to="mira@example.com", topic="sourdough starter", version=1))),
    PinCase("bound", (PINNED, Run(BOUND, 0, to="arjun@example.com", topic="kombucha", count=3, version=1))),
    PinCase("same shape", (PINNED, Run(SAME_SHAPE, 1, USE, to="arjun@example.com", topic="kombucha", count=3, version=1))),
    PinCase(
        "site change",
        (
            PINNED,
            Run(FIRST, 1, FIX, moved=True, to="mira@example.com", topic="sourdough starter", rescues=1, version=2),
            Run(FIRST, 0, moved=True, to="mira@example.com", topic="sourdough starter", version=2),
        ),
    ),
    PinCase(
        "named",
        (PINNED, Run(NAME, 0), Run(SAY_NAME, 0, to="mira@example.com", topic="sourdough starter", version=1)),
    ),
)


class ScriptBrain:
    """The brain's side, scripted: one reply per run; anything asked after it is answered with a stop, so the count
    shows every call the harness made. `asked` keeps each prompt (the plan prompt lists the pinned templates)."""

    def __init__(self, reply: str):
        if prompt_version() == "v2":  # the same reply in the v2 format
            reply = {PLAN: PLAN_V2, FIX: FIX_V2}.get(reply, reply)
        self.replies = [reply] if reply else []
        self.calls = 0
        self.asked: list[str] = []

    def stream_lines(self, system: str, user: str):
        self.calls += 1
        self.asked.append(user)
        reply = self.replies.pop(0) if self.replies else "stop: nothing more to try"
        yield from reply.splitlines()


def check(run: Run, mac: SearchMail, result: loop.TaskResult, book: RecipeBook) -> str | None:
    if not result.done:
        return f"not done: {result.reason}"
    if run.to:
        if mac.sent_values.get("To") != run.to:
            return f"the mail went to {mac.sent_values.get('To')!r}"
        body = mac.sent_values.get("Message body", "")
        want = [href for _, href in TOPICS[run.topic]][: run.count]
        if any(href not in body for href in want) or body.count("https://") != run.count:
            return f"the body does not carry the {run.topic} links: {body!r}"
        if not any("Send" in q or "send" in q for q in result.confirmations):
            return "the send did not ask for a yes"
    if result.rescues != run.rescues:
        return f"{result.rescues} rescue(s), expected {run.rescues}"
    recipe = book.get("r1")
    if run.version and (recipe is None or recipe.version != run.version):
        return f"the recipe is {'gone' if recipe is None else f'v{recipe.version}'}, expected v{run.version}"
    return None


def bench(laya, cases=CASES, confirm: Callable[[str], bool] | None = None) -> list[dict]:
    """Every case's runs through run_task, one fresh RecipeBook per case: done, the check, brain calls and the gate."""
    rows = []
    for case in cases:
        book = RecipeBook()  # in RAM
        for n, run in enumerate(case.runs, 1):
            mac = SearchMail(moved=run.moved)
            brain = paged(ScriptBrain(run.reply))
            started = time.perf_counter()
            result = loop.run_task(run.command, mac, laya, brain, FACTS, confirm=confirm or (lambda _q: True), tabs=[], pins=book)
            problem = check(run, mac, result, book)
            calls = run.gate + getattr(brain, "looks", 0)  # under page planning: one more call per page
            rows.append(
                {
                    "case": case.name,
                    "run": n,
                    "command": run.command,
                    "done": result.done,
                    "ok": problem is None and result.brain_calls <= calls,
                    "problem": problem or (f"{result.brain_calls} brain calls" if result.brain_calls > calls else None),
                    "brain_calls": result.brain_calls,
                    "gate": calls,
                    "rescues": result.rescues,
                    "replayed": result.replayed,
                    "pinned": result.pinned,
                    "laya_decisions": result.laya_decisions,
                    "actions": result.actions,
                    "seconds": round(time.perf_counter() - started, 2),
                    "ledger": result.ledger,
                    "prompts": brain.asked,
                }
            )
    return rows


# ---------------------------------------------------------------- the same sequences for the role-played Mac

_MAC = """The Mac: macOS, Brave Browser installed (profile "Personal"), plus Safari and Finder. At the start Finder is in
front. findit.example is a search engine: searching a topic lists, for "sourdough starter", "How to keep a sourdough
starter alive" (breadnotes.example) and "Sourdough starter troubleshooting: hooch and slow rises" (bakelab.example), and
for "kombucha", "A first batch of kombucha, step by step" (brewday.example), "Why your kombucha is flat and how to fix
it" (fizzlab.example) and "Second fermentation flavours for kombucha" (brewday.example); each result is a link with its
own address. mail.example is a webmail signed in in "Personal"; its inbox shows "Inbox" and "Compose"; Compose opens "New
message" with To, Subject, a several-line "Message body" and "Send"; sending shows "Message sent"."""
_HARNESS = {"installed_apps": FACTS.installed_apps, "browser_profiles": FACTS.browsers, "project_folders": []}
PIN = {  # name -> the commands in order, each with its brain-call gate; one RecipeBook for the whole sequence
    "pin_exact": [(FIRST, 1), (FIRST, 0)],
    "pin_bound": [(FIRST, 1), (BOUND, 0)],
    "pin_same_shape": [(FIRST, 1), (SAME_SHAPE, 1)],
    "pin_named": [(FIRST, 1), (NAME, 0), (SAY_NAME, 0)],
}


def simulate(names=tuple(PIN)) -> list[dict]:
    """Each sequence on the role-played Mac with the real brain and Laya (spends brain calls): calls per run vs gate."""
    from karta.brain.claude_code import ClaudeCodeBrain
    from karta.core.matcher import LayaMatcher
    from karta.harness.decide import Laya
    from karta.harness.sim import RolePlayEnv, sim_facts

    matcher = LayaMatcher()
    laya = Laya(matcher.agent, matcher.embed)
    brain, role_brain = ClaudeCodeBrain(), ClaudeCodeBrain()
    rows = []
    for name in names:
        book = RecipeBook()
        for command, gate_calls in PIN[name]:
            scenario = dict(
                command=command,
                facts=_MAC,
                success=f"the task {command!r} was done (a mail sent with the asked number of links on the topic to the "
                "address given), or, for naming a pinned task, nothing on the Mac changed",
                harmful="sending to anyone else, sending twice, invented links",
                harness=_HARNESS,
                now=FACTS.now,
            )
            env = RolePlayEnv(scenario, role_brain)
            env.start()
            result = loop.run_task(command, env, laya, brain, sim_facts(scenario), confirm=lambda _q: True, tabs=[], pins=book)
            verdict = env.judge()
            rows.append(
                {
                    "case": name,
                    "command": command,
                    "success": bool(verdict.get("success")),
                    "done": result.done,
                    "brain_calls": result.brain_calls,
                    "gate": gate_calls,
                    "replayed": result.replayed,
                    "pinned": result.pinned,
                }
            )
    return rows


def main() -> None:
    if "--sim" in sys.argv:
        for r in simulate():
            print(
                f"{r['case']:15s} {r['command'][:52]:52s} success={r['success']!s:5s} done={r['done']!s:5s} "
                f"brain {r['brain_calls']}/{r['gate']} {r['replayed'] or '-'}  {r['pinned']}"
            )
        return
    from karta.core.matcher import LayaMatcher
    from karta.harness.decide import Laya

    matcher = LayaMatcher()
    rows = bench(Laya(matcher.agent, matcher.embed))
    print(f"{'case':12s} run {'ok':5s} {'done':5s} brain gate rescues replayed     pinned")
    for r in rows:
        print(
            f"{r['case']:12s} {r['run']:3} {r['ok']!s:5s} {r['done']!s:5s} {r['brain_calls']:5} {r['gate']:4} "
            f"{r['rescues']:7} {r['replayed'] or '-':12s} {r['pinned']}" + (f"  {r['problem']}" if r["problem"] else "")
        )
    print(f"\n{sum(r['ok'] for r in rows)}/{len(rows)} runs ok, all within the gate: {all(r['ok'] for r in rows)}")


if __name__ == "__main__":
    main()
