"""The local repair bench (karta/harness/repair.py): one case per kind of mechanical miss, on made-up apps, each fixed
with no brain rescue; and a plan that is truly wrong, which still gets its one rescue. Nothing here touches this Mac.

    HF_HUB_OFFLINE=1 uv run python -m karta.harness.repair_cases      # the real Laya on scripted screens, no brain

| Case | The miss | Fixed by |
|---|---|---|
| paste-ignored | a field that ignores a paste | the text key by key |
| token-field | keys land unseen; the field makes a token on tab | tab, then the token beside the field |
| stacked | two copies of a field at one spot; the covered one takes nothing | the copy a real click focuses |
| late-page | the change shows after the step's own waits | a change wait, then the type verified again |
| open-already | a click whose form is open already | none: its field takes the focus, so editor_opened is met |
| field-late | no field yet when the step runs | a change wait, then the step again |
| focused-field | the field is named otherwise than the step says | the focused field, when it fits |
| unsure-pick | Laya unsure which control | Laya's ranked options, verified by the expectation |
| notice | a notice in the way that no rule closes | escape, then the step again |
| drift | another app comes to the front mid-task | back to the place that worked |
| wrong-plan | a step missing from the plan | one brain rescue |

Each runs through loop.run_task on a scripted Mac with the plan streamed in by a scripted brain, which answers a rescue
from the case (or with a stop). The gate: done with brain calls per task 1 and no rescue for every miss, and the wrong
plan with exactly one rescue. Local repairs are counted; the offline tests (tests/test_harness_repair.py, the fake
Laya) also check that each case shows its miss and is fixed by a repair.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from karta.harness import loop
from karta.harness.brain import HarnessBrain
from karta.harness.sim import ScriptedMac, paged
from karta.harness.types import Item, MacFacts, Screen

FACTS = MacFacts(
    ["Gatherly", "Postbox", "Notely", "Tallybook", "Chirp", "Snapshelf", "Clockwork", "Finder"],
    {"Brave Browser": ["Personal"]},
    ["~/Projects/atlas"],
    datetime(2026, 10, 8, 9, 30),
)


def _page(app: str, *items: Item, window: str = "") -> Screen:
    return Screen(app, list(items), window=window)


class QuirkyMac(ScriptedMac):
    """A ScriptedMac with the quirks real fields have: `deaf` fields ignore a paste (set_text) but take keys;
    `unread` fields take keys but never show their value, and turn it into a token beside them on tab; with `stacked`,
    only a focused field takes a paste and a click on a field focuses its last copy (the one on top); `slow` moves a
    page on to the next once that much time was waited on it. `typed` keeps every text that went into each field."""

    def __init__(self, pages, start, on=None, deaf=(), unread=(), stacked=False, slow=None):
        super().__init__(pages, start, on)
        self.deaf, self.unread, self.stacked, self.slow = set(deaf), set(unread), stacked, dict(slow or {})
        self.typed: dict[str, list[str]] = {}
        self.waited = 0.0
        self.pending_token: str = ""

    def _focus(self, name: str) -> None:
        s = self.pages[self.at]
        copies = [n for n, i in enumerate(s.items) if i.kind == "field" and i.name == name]
        top = copies[-1] if copies else None
        s.items = [replace(i, focused=(n == top)) if i.kind == "field" else i for n, i in enumerate(s.items)]

    def click(self, item: Item) -> None:
        if item.kind == "field":
            self._focus(item.name)
            self.log.append(f"click {item.name}")
            return
        super().click(item)

    def set_text(self, item: Item, text: str) -> None:
        self.log.append(f"set {item.name}")
        if item.name in self.deaf or item.name in self.unread or (self.stacked and not item.focused):
            return
        self.typed.setdefault(item.name, []).append(text)
        self._set(item.name, text)

    def type_keys(self, text: str, item: Item | None = None) -> None:
        self.log.append("keys")
        focus = next((i for i in self.pages[self.at].items if i.kind == "field" and i.focused), None)
        if item is not None and (focus is None or focus.name != item.name):
            return  # the field does not hold the focus: the real Mac types nothing then
        if focus is None:
            return
        self.typed.setdefault(focus.name, []).append(text)
        if focus.name in self.unread:
            self.pending_token = text
            return
        self._set(focus.name, text)  # its old text was selected first: the keys replace what was there

    def press(self, keys: str) -> None:
        if keys == "tab" and self.pending_token:
            s = self.pages[self.at]
            field_at = next(n for n, i in enumerate(s.items) if i.kind == "field" and i.focused)
            s.items.insert(field_at + 1, Item(self.pending_token, "button", region=s.items[field_at].region))
            s.items = [replace(i, focused=False) if i.kind == "field" else i for i in s.items]
            self.pending_token = ""
            self.log.append("press tab")
            return
        super().press(keys)

    def wait(self, seconds: float) -> None:
        super().wait(seconds)
        self.waited += seconds
        if self.at in self.slow and self.waited >= self.slow[self.at][0]:
            self.at = self.slow[self.at][1]

    def _go(self, action: str) -> None:
        before = self.at
        super()._go(action)
        if self.at != before:
            self.waited = 0.0


# ---------------------------------------------------------------- the cases


def paste_ignored() -> QuirkyMac:
    form = _page(
        "Gatherly",
        Item("New event", "heading", region="top"),
        Item("Title", "field", region="middle"),
        Item("Invitees", "field", region="middle"),
        Item("Create", "button", region="bottom"),
        window="New event",
    )
    made = _page(
        "Gatherly",
        Item("Event created", "text", region="top"),
        Item("Upcoming", "heading"),
        Item("Team lunch", "row"),
        window="Upcoming",
    )
    return QuirkyMac({"form": form, "made": made}, "form", {("form", "click Create"): "made"}, deaf={"Invitees"})


PASTE_PLAN = """contract: created(event named Team lunch)
open_place Gatherly
write Team lunch | where: Title
write ana@example.com | where: Invitees
submit | expect: dialog_closed
done: dialog_closed"""


def token_field() -> QuirkyMac:
    compose = _page(
        "Postbox",
        Item("New message", "heading", region="right", dialog=True),
        Item("To", "field", region="right", dialog=True),
        Item("Subject", "field", region="right", dialog=True),
        Item("Send", "button", region="bottom-right", dialog=True),
        window="New message",
    )
    sent = _page("Postbox", Item("Inbox", "heading"), Item("Message sent", "text", region="bottom-left"), window="Inbox")
    return QuirkyMac({"compose": compose, "sent": sent}, "compose", {("compose", "click Send"): "sent"}, unread={"To"})


TOKEN_PLAN = """contract: sent(to ana@example.com)
open_place Postbox
write ana@example.com | where: To
write Lunch on Friday | where: Subject
submit | expect: dialog_closed
done: dialog_closed"""


def stacked() -> QuirkyMac:
    note = _page(
        "Notely",
        Item("Notes", "heading", region="left"),
        Item("Title", "field", region="middle"),  # the note behind, covered
        Item("Title", "field", region="middle"),  # the new note's, on top
        Item("Body", "field", region="middle", multiline=True),
        Item("Save", "button", region="top"),
        window="New note",
    )
    saved = _page(
        "Notely", Item("Notes", "heading", region="left"), Item("Note saved", "text"), Item("Packing list", "row"), window="Notes"
    )
    return QuirkyMac({"note": note, "saved": saved}, "note", {("note", "click Save"): "saved"}, stacked=True)


STACKED_PLAN = """contract: created(note named Packing list)
open_place Notely
write Packing list | where: Title
submit | expect: dialog_closed
done: dialog_closed"""


def late_page() -> QuirkyMac:
    books = _page("Tallybook", Item("Accounts", "heading"), Item("Export report", "button", region="top-right"))
    busy = _page("Tallybook", Item("Accounts", "heading"), Item("Preparing your file", "text", region="top-right"))
    ready = _page(
        "Tallybook",
        Item("Accounts", "heading"),
        Item("Report ready", "text", region="top-right"),
        Item("Download", "button", region="top-right"),
    )
    return QuirkyMac(
        {"books": books, "busy": busy, "ready": ready},
        "books",
        {("books", "click Export report"): "busy"},
        slow={"busy": (2.0, "ready")},
    )


LATE_PLAN = """open_place Tallybook
click Export report | expect: list_changed
done: list_changed"""


def open_already() -> QuirkyMac:
    inbox = [Item("Compose", "button", region="left"), Item("Inbox", "heading", region="top")]
    compose = _page(
        "Postbox",
        *inbox,
        Item("New message", "heading", region="right"),
        Item("To", "field", region="right"),
        Item("Subject", "field", region="right"),
        Item("Send", "button", region="bottom-right"),
        window="Inbox",
    )
    again = replace(compose, items=[replace(i, focused=True) if i.name == "To" else i for i in compose.items])
    sent = _page("Postbox", *inbox, Item("Message sent", "text", region="bottom-left"), window="Inbox")
    return QuirkyMac(
        {"compose": compose, "again": again, "sent": sent},
        "compose",
        {("compose", "click Compose"): "again", ("again", "click Send"): "sent", ("compose", "click Send"): "sent"},
    )


OPEN_PLAN = """contract: sent(to ana@example.com)
open_place Postbox
click Compose | expect: editor_opened
write ana@example.com | where: To
submit | expect: dialog_closed
done: dialog_closed"""


def field_late() -> QuirkyMac:
    loading = _page("Chirp", Item("Design team", "heading", region="top"), Item("Members", "button", region="top-right"))
    room = _page(
        "Chirp",
        Item("Design team", "heading", region="top"),
        Item("Members", "button", region="top-right"),
        Item("Message", "field", region="bottom"),
    )
    posted = _page(
        "Chirp", Item("Design team", "heading", region="top"), Item("you: standup moved to 11", "text"), Item("Message", "field")
    )
    return QuirkyMac(
        {"loading": loading, "room": room, "posted": posted},
        "loading",
        {("room", "press return"): "posted"},
        slow={"loading": (1.0, "room")},
    )


LATE_FIELD_PLAN = """contract: sent
open_place Chirp
write standup moved to 11 | where: Message
submit | expect: new_item(standup moved to 11)
done: new_item(standup moved to 11)"""


def focused_field() -> QuirkyMac:
    form = _page(
        "Gatherly",
        Item("Share event", "heading", region="top"),
        Item("Send to", "field", region="middle", focused=True),
        Item("Send", "button", region="bottom", default=True),  # the sheet's default button
        window="Share event",
    )
    shared = _page("Gatherly", Item("Event shared", "text"), window="Upcoming")
    return QuirkyMac({"form": form, "shared": shared}, "form", {("form", "click Send"): "shared"})


FOCUSED_PLAN = """contract: sent(to ana@example.com)
open_place Gatherly
fill Recipient: ana@example.com
submit | expect: dialog_closed
done: dialog_closed"""


def unsure_pick() -> QuirkyMac:
    shelf = _page(
        "Snapshelf",
        Item("Albums", "heading", region="left"),
        Item("Help", "button", region="top-right"),
        Item("⇪", "button", region="top-right"),
        Item("Slideshow", "button", region="top"),
    )
    picker = _page(
        "Snapshelf",
        Item("Albums", "heading", region="left"),
        Item("Choose files", "heading", region="middle", dialog=True),
        Item("Cancel", "button", region="middle", dialog=True),
    )
    return QuirkyMac({"shelf": shelf, "picker": picker}, "shelf", {("shelf", "click ⇪"): "picker"})


UNSURE_PLAN = """open_place Snapshelf
click the upload icon | expect: dialog_opened
done: dialog_opened"""


def notice() -> QuirkyMac:
    room = [Item("Design team", "heading", region="top"), Item("Message", "field", region="bottom")]
    blocked = _page("Chirp", *room, Item("Connection lost, retrying", "text", region="bottom", dialog=True))
    clear = _page("Chirp", *room)
    posted = _page("Chirp", room[0], Item("you: lunch at 1", "text"), room[1])
    return QuirkyMac(
        {"blocked": blocked, "clear": clear, "posted": posted},
        "blocked",
        {("blocked", "press escape"): "clear", ("clear", "press return"): "posted"},
    )


NOTICE_PLAN = """contract: sent
write lunch at 1 | where: Message
submit | expect: new_item(lunch at 1)
done: new_item(lunch at 1)"""


def drift() -> QuirkyMac:
    form = _page(
        "Gatherly",
        Item("New event", "heading", region="top"),
        Item("Title", "field", region="middle"),
        Item("Create", "button", region="bottom-right"),
        window="New event",
    )
    alarm = _page("Clockwork", Item("Timer done", "heading"), Item("Stop", "button"))
    made = _page("Gatherly", Item("Event created", "text", region="top"), Item("Team lunch", "row"), window="Upcoming")
    mac = QuirkyMac(
        {"form": form, "alarm": alarm, "made": made},
        "form",
        {("alarm", "open Gatherly"): "form", ("form", "click Create"): "made"},
    )
    typed = mac.set_text

    def set_text(item: Item, text: str) -> None:  # once the title is in, another app comes to the front, once
        typed(item, text)
        if "alarm" not in mac.log:
            mac.log.append("alarm")
            mac.at = "alarm"

    mac.set_text = set_text
    return mac


DRIFT_PLAN = """contract: created(event named Team lunch)
open_place Gatherly
write Team lunch | where: Title
click Create | expect: dialog_closed
done: dialog_closed"""


def wrong_plan() -> QuirkyMac:
    thread = _page(
        "Chirp",
        Item("Mira", "heading", region="top"),
        Item("Lunch at 1?", "text"),
        Item("Reply", "button", region="bottom"),
    )
    box = _page(
        "Chirp",
        Item("Mira", "heading", region="top"),
        Item("Lunch at 1?", "text"),
        Item("Reply to Mira", "field", region="bottom"),
    )
    posted = _page("Chirp", Item("Mira", "heading", region="top"), Item("you: see you at 1", "text"))
    return QuirkyMac(
        {"thread": thread, "box": box, "posted": posted},
        "thread",
        {("thread", "click Reply"): "box", ("box", "press return"): "posted"},
    )


WRONG_PLAN = """contract: sent(to mira)
open_place Chirp
write see you at 1 | where: Reply to Mira
submit | expect: new_item(see you at 1)
done: new_item(see you at 1)"""
WRONG_FIX = """click Reply | expect: editor_opened
write see you at 1 | where: Reply to Mira"""


# ---------------------------------------------------------------- the bench


@dataclass(frozen=True)
class RepairCase:
    name: str
    command: str
    mac: Callable[[], QuirkyMac]
    plan: str
    check: Callable[[QuirkyMac, loop.TaskResult], str | None] | None = None
    rescue: str = ""  # the brain's rescue reply, for a plan that is truly wrong
    rescues: int = 0  # the rescues it should take
    typed_alone: bool = False  # its type proves the step as it is: no miss, so no local repair


def typed_once(name: str, text: str) -> Callable[[QuirkyMac, loop.TaskResult], str | None]:
    """The field got `text`, once (no doubled typing)."""

    def check(mac: QuirkyMac, result: loop.TaskResult) -> str | None:
        got = mac.typed.get(name, [])
        if got.count(text) != 1:
            return f"{name!r} got {text!r} {got.count(text)} times"
        return None

    return check


CASES = (
    RepairCase("paste-ignored", "add a team lunch event in gatherly with ana@example.com", paste_ignored, PASTE_PLAN,
               typed_once("Invitees", "ana@example.com")),
    RepairCase("token-field", "mail ana@example.com about lunch on friday from postbox", token_field, TOKEN_PLAN,
               typed_once("To", "ana@example.com")),
    RepairCase("stacked", "make a note called packing list in notely", stacked, STACKED_PLAN,
               typed_once("Title", "Packing list")),
    RepairCase("late-page", "export the accounts report in tallybook", late_page, LATE_PLAN),
    RepairCase("open-already", "mail ana@example.com from postbox", open_already, OPEN_PLAN, typed_once("To", "ana@example.com"), typed_alone=True),
    RepairCase("field-late", "tell the design team on chirp standup moved to 11", field_late, LATE_FIELD_PLAN),
    RepairCase("focused-field", "share the event in gatherly with ana@example.com", focused_field, FOCUSED_PLAN,
               typed_once("Send to", "ana@example.com")),
    RepairCase("unsure-pick", "upload photos to snapshelf", unsure_pick, UNSURE_PLAN),
    RepairCase("notice", "tell the design team on chirp lunch at 1", notice, NOTICE_PLAN),
    RepairCase("drift", "add a team lunch event in gatherly", drift, DRIFT_PLAN),
    RepairCase("wrong-plan", "reply to mira on chirp: see you at 1", wrong_plan, WRONG_PLAN, rescue=WRONG_FIX, rescues=1),
)  # fmt: skip


class CaseBrain:
    """The brain's side, scripted: the plan streams in line by line; a rescue gets the case's fix (else a stop), and
    anything else a stop, so the count shows every call the harness made."""

    def __init__(self, plan: str, rescue: str = ""):
        self.replies = [plan, *([rescue] if rescue else [])]
        self.calls = 0

    def stream_lines(self, system: str, user: str):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else "stop: nothing more to try"
        yield from reply.splitlines()


def gate(case: RepairCase) -> int:
    return 1 + case.rescues


def bench(laya, cases=CASES) -> list[dict]:
    """Every case through run_task: done, its check, brain calls and rescues against the gate, local repairs."""
    rows = []
    for case in cases:
        mac = case.mac()
        scripted = paged(CaseBrain(case.plan, case.rescue))
        brain = HarnessBrain(scripted, prompt="v2")
        started = time.perf_counter()
        result = loop.run_task(case.command, mac, laya, brain, FACTS, confirm=lambda _q: True, tabs=[])
        calls = gate(case) + getattr(scripted, "looks", 0)  # under page planning: one more call per page
        problem = case.check(mac, result) if case.check is not None else None
        if not result.done:
            problem = problem or f"not done: {result.reason}"
        elif result.rescues != case.rescues:
            problem = f"{result.rescues} rescue(s), expected {case.rescues}"
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
    return rows


def report(rows: list[dict]) -> str:
    lines = [f"{'case':14s} {'ok':5s} {'done':5s} brain gate rescues repairs laya actions"]
    for r in rows:
        lines.append(
            f"{r['case']:14s} {r['ok']!s:5s} {r['done']!s:5s} {r['brain_calls']:5} {r['gate']:4} {r['rescues']:7} "
            f"{r['repairs']:7} {r['laya_decisions']:4} {r['actions']:7}" + (f"  {r['problem']}" if r["problem"] else "")
        )
    n = len(rows)
    lines.append(
        f"\n{sum(r['ok'] for r in rows)}/{n} ok; per task: brain calls {sum(r['brain_calls'] for r in rows) / n:.2f}, "
        f"rescues {sum(r['rescues'] for r in rows) / n:.2f}, local repairs {sum(r['repairs'] for r in rows) / n:.2f}; "
        f"all within the gate: {all(r['within_gate'] for r in rows)}"
    )
    return "\n".join(lines)


def main() -> None:
    from karta.core.matcher import LayaMatcher
    from karta.harness.decide import Laya

    matcher = LayaMatcher()
    rows = bench(Laya(matcher.agent, matcher.embed))
    print(report(rows))
    for r in rows:
        if not r["ok"]:
            print(f"\n{r['case']}: {r['reason']}")
            for line in r["ledger"]:
                print(f"    {line}")


if __name__ == "__main__":
    main()
