"""Session memory: what the last tasks produced or touched, with short ids (`@n`), so a later task can say "that".

Each finished task leaves refs: the items it collected (a page's text among them), the answer it said, a text it
wrote, a person it addressed, a thing it made, the page it ended on. Values (the items, the text, the address) stay
in RAM, for the last `KEEP_TASKS` tasks; a ref older than `FRESH_S` never reaches a prompt.

The plan prompt gets a block (`lines`) only when the task points back (a reference word: `REFERENCE`) or shares a
distinctive word with a ref's title; otherwise the prompt is unchanged. The block shows ids, kinds, short titles,
sizes and ages, never a value: the brain writes `{@n}` (or `tell … | from: @n`), `bind` puts the ref's items into
`Toolbox.slots` under `@n`, and the existing `check_slots` and `Toolbox.expand` type the value by code. An email
address a ref holds counts as given by Karta (`addresses`, for `brain.foreign_detail`).

On disk (optional, `session.sqlite`, mode 600): task text, refs' titles and ids for `DAYS`, never a value, with an
FTS5 index for "what did I ask about X" (`recall`). Ideas borrowed (no code): Google ADK's session state and
`output_key`, Hermes's session store searched with FTS5 and returned raw.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from karta.harness.delegate import shown
from karta.harness.tools import ADDRESS_WORDS, CODE_WANTED, EMAIL, SEARCH_WORD, Collected, _host_of, _page_of, topic_words

KEEP_TASKS = 8  # tasks kept in RAM, values and all
FRESH_S = 30 * 60.0  # a ref older than this never reaches a prompt
BLOCK_LINES = 6  # the block in the plan prompt, its header and its last place included
POINTED_TASKS = 2  # a reference word shows the refs of this many latest tasks
DAYS = 14  # on disk: task text and titles, never values
MAX_EVENTS = 2000
TITLE_CHARS = 60
DRAFT_CHARS = 20  # a text written into a field, shorter than this, is no draft (a name, a search)
REFERENCE = re.compile(r"\b(that|it|this|them|him|her|the same|again)\b", re.I)  # the task points back
HEADER = "Recent (this session; put one in a write as {@n}, or tell from: @n; never retype it):"


@dataclass
class Ref:
    """One thing a later task may point at. `items` (the values) stay in RAM and never go into a prompt."""

    id: int
    kind: str  # items | text | answer | draft | person | made | page | delegate
    title: str
    source: str = ""  # the site's host, or the app
    items: list[Collected] = field(default_factory=list, repr=False)
    task: str = ""
    at: float = 0.0
    seen: list = field(default_factory=list, repr=False)  # the screens its text was read on (consent: Toolbox.withheld)


@dataclass
class Event:
    """One finished task."""

    task: str
    done: bool
    place: str  # where it ended: "Safari, example.org", or the app
    refs: list[Ref]
    at: float


def _short(text: str, n: int = TITLE_CHARS) -> str:
    text = EMAIL.sub("<address>", " ".join(str(text).split()))  # an address is a value: never in a title
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _ago(seconds: float) -> str:
    minutes = int(seconds // 60)
    return "just now" if minutes < 1 else f"{minutes} min ago"


class Session:
    """This process's recent tasks. `path`: the on-disk index (None: RAM only). `clock`: time.time (tests)."""

    def __init__(self, path: Path | str | None = None, clock=time.time):
        self.clock = clock
        self.events: deque[Event] = deque(maxlen=KEEP_TASKS)
        self.next_id = 1
        self.lock = threading.RLock()  # a delegated session's watcher keeps its result from a background thread
        self.db: sqlite3.Connection | None = None
        if path is not None:
            self.db = _open(Path(path))

    # ------------------------------------------------------------ recording

    def add(
        self, task: str, steps: list[dict], tools, answer: str = "", done: bool = False, said: str = "", done_shows: str = ""
    ) -> Event:
        """Turn what a finished task produced or touched into refs: `steps` are the steps that passed, `tools` its
        Toolbox (slots, where it ended), `answer` what a tell said, `done_shows` its done line (a thing it made)."""
        with self.lock:
            return self._add(task, steps, tools, answer, done, said, done_shows)

    def _add(self, task, steps, tools, answer, done, said, done_shows) -> Event:
        now = self.clock()
        refs: list[Ref] = []

        screens = getattr(tools, "slot_screens", {})

        def ref(kind: str, title: str, source: str, items: list[Collected], seen=()) -> None:
            refs.append(Ref(self.next_id, kind, _short(title), source, list(items), task, now, list(seen)))
            self.next_id += 1

        secret = {s.get("slot") for s in steps if s.get("tool") == "collect" and CODE_WANTED.search(str(s.get("what", "")))}
        end = tools.seen.get(tools.front)
        end_page = (end.url or "") if end is not None else ""
        texts_on: set[str] = set()
        for slot, items in tools.slots.items():
            if slot.startswith("@") or slot in secret or not items:  # a bound ref, or a code: never kept
                continue
            source = _host_of(tools.slot_pages.get(slot) or "") or tools.front
            one = items[0]
            if len(items) == 1 and len(one.value) >= 200:  # a page's main text, under the page's own title
                on = _page_of(one.url) if one.url else tools.slot_pages.get(slot, "")
                texts_on.add(on)
                read = next((x for x in tools.seen.values() if x.url and on and _page_of(x.url) == on and x.window), None)
                title = read.window if read is not None else one.title
                ref("text", f'"{_short(title, 40)}", {len(one.value):,} chars', source, items, screens.get(slot, ()))
            elif len(items) == 1:
                ref("items", f'"{_short(one.title, 50)}"', source, items, screens.get(slot, ()))
            else:
                titles = ", ".join(f'"{_short(c.title, 24)}"' for c in items[:2])
                ref("items", f"{len(items)} items: {titles}" + (", …" if len(items) > 2 else ""), source, items,
                    screens.get(slot, ()))  # fmt: skip
        if answer:
            told = [scr for seen in screens.values() for scr in seen]
            ref("answer", f'to "{_short(task, 40)}"', tools.front, [Collected("answer", value=answer)], told)
        people: set[str] = set()
        kept_trace = len(tools.trace)
        for step in steps:
            texts = [step.get("text")] if step.get("tool") in ("write", "type") else []
            if step.get("tool") == "fill" and isinstance(step.get("fields"), dict):
                texts = [v for v in step["fields"].values() if isinstance(v, str)]
            where = str(step.get("where") or "")
            for raw in texts:
                text, missing = tools.expand(str(raw), True)
                if missing is not None:
                    continue
                people.update(m[0] for m in EMAIL.finditer(text))
                bare = EMAIL.sub("", text).strip(" ,;<>")
                if len(bare) >= DRAFT_CHARS and not SEARCH_WORD.search(where) and not ADDRESS_WORDS.search(where):
                    ref("draft", f"text written in {_short(where or 'a field', 30)}, {len(text):,} chars", tools.front,
                        [Collected("text", value=text)])  # fmt: skip
        del tools.trace[kept_trace:]  # filling {name} again here is no step of the task's
        people.update(m[0] for m in EMAIL.finditer(task))
        for address in sorted(people):
            ref("person", _name_for(address, task, tools) or "an email address", tools.front, [Collected("", value=address)])
        for launched in getattr(tools, "launched", []):  # a Claude Code session started: its repo, for "there" later
            ref("delegate", f"Claude Code session in {launched.name}", shown(launched.repo),
                [Collected("the request", value=launched.request)])  # fmt: skip
        if done:
            made = _made(done_shows)
            if made:
                ref("made", f'"{made}"', tools.front, [Collected(made)])
        if end_page and _page_of(end_page) not in texts_on:
            ref("page", f'"{_short(end.window or _host_of(end_page), 50)}"', _host_of(end_page),
                [Collected(end.window or _host_of(end_page), end_page)])  # fmt: skip
        place = tools.front + (f", {_host_of(end_page)}" if end_page else "") if tools.front else ""
        event = Event(task, done, place, refs, now)
        self.events.append(event)
        self._save(event, said)
        return event

    def keep(self, task: str, kind: str, title: str, source: str, value: str) -> Event:
        """One result that came after its task ended (a delegated Claude Code session's last message) as a task of its
        own with one ref, so "what did it do?" points at it."""
        with self.lock:
            now = self.clock()
            ref = Ref(self.next_id, kind, _short(title), source, [Collected(title, value=value)], task, now)
            self.next_id += 1
            event = Event(task, True, "", [ref], now)
            self.events.append(event)
            self._save(event, "")
            return event

    def last_repo(self) -> str:
        """The repo a recent task delegated to (a delegate ref's source), newest first; "" for none."""
        for event in self.fresh():
            for r in reversed(event.refs):
                if r.kind == "delegate" or (r.kind == "text" and r.source.startswith(("~/", "/"))):
                    return r.source
        return ""

    # ------------------------------------------------------------ the prompt

    def fresh(self) -> list[Event]:
        """The kept tasks young enough for a prompt, newest first."""
        now = self.clock()
        return [e for e in reversed(self.events) if now - e.at <= FRESH_S]

    def listed(self, task: str) -> list[Ref]:
        """The refs the block shows for `task`, newest first: a reference word shows the latest tasks' refs; else
        the refs whose title shares a distinctive word with the task; else none."""
        events = self.fresh()
        room = BLOCK_LINES - 1 - (1 if events and events[0].place else 0)
        if REFERENCE.search(task):
            refs = [r for e in events[:POINTED_TASKS] for r in reversed(e.refs)]
        else:
            want = topic_words(task)
            refs = [r for e in events for r in reversed(e.refs) if want & topic_words(r.title)] if want else []
        return refs[:room]

    def lines(self, task: str, refs: list[Ref] | None = None) -> list[str]:
        """The block for the plan prompt, at most BLOCK_LINES lines: ids, kinds, titles, sizes and ages, never a
        value. [] when the task points at nothing here (the prompt is then unchanged)."""
        refs = self.listed(task) if refs is None else refs
        if not refs:
            return []
        now = self.clock()
        out = [HEADER]
        for r in refs:
            source = f" ({r.source})" if r.source else ""
            out.append(f'@{r.id} {r.kind} {r.title}{source}, from "{_short(r.task, 40)}", {_ago(now - r.at)}')
        events = self.fresh()
        if events and events[0].place:
            out.append(f"Ended on: {events[0].place}")
        return out[:BLOCK_LINES]

    def bind(self, tools, refs: list[Ref]) -> None:
        """Each listed ref's items into `tools.slots` as `@n`, so `{@n}` passes check_slots and expands by code."""
        for r in refs:
            tools.slots[f"@{r.id}"] = list(r.items)
            if hasattr(tools, "slot_screens"):
                tools.slot_screens[f"@{r.id}"] = list(r.seen)

    def addresses(self) -> str:
        """The email addresses Karta holds from recent tasks (their refs and their words): Karta gave them, so a step
        may type them (brain.foreign_detail). Never put into a prompt."""
        found = {m[0] for e in self.fresh() for r in e.refs for c in r.items for m in EMAIL.finditer(f"{c.title} {c.value}")}
        found |= {m[0] for e in self.fresh() for m in EMAIL.finditer(e.task)}
        return " ".join(sorted(found))

    # ------------------------------------------------------------ on disk: titles and task text only

    def _save(self, event: Event, said: str) -> None:
        if self.db is None:
            return
        titles = " | ".join(f"@{r.id} {r.kind} {r.title}" for r in event.refs)
        with self.db:
            cur = self.db.execute(
                "insert into events(at, task, done, said, place, titles) values (?, ?, ?, ?, ?, ?)",
                (event.at, event.task, int(event.done), said, event.place, titles),
            )
            self.db.execute("insert into recall(rowid, task, titles) values (?, ?, ?)", (cur.lastrowid, event.task, titles))
            old = event.at - DAYS * 86400
            self.db.execute("delete from events where at < ? or id <= (select max(id) from events) - ?", (old, MAX_EVENTS))
            self.db.execute("delete from recall where rowid not in (select id from events)")

    def recall(self, query: str, limit: int = 5, since: float | None = None) -> list[tuple[float, str, str]]:
        """Past tasks whose words or titles match `query` (FTS5, no brain call): (when, task, titles), newest first."""
        words = re.findall(r"\w+", query.lower())
        if self.db is None or not words:
            return []
        match = " OR ".join(f'"{w}"' for w in words)
        rows = self.db.execute(
            "select e.at, e.task, e.titles from recall join events e on e.id = recall.rowid "
            "where recall match ? and e.at >= ? order by e.at desc limit ?",
            (match, since or 0.0, limit),
        )
        return list(rows)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None


def _open(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), check_same_thread=False)  # voice mode's screen worker records; one at a time
    db.executescript(
        "create table if not exists events(id integer primary key, at real, task text, done integer, said text, "
        "place text, titles text);"
        "create virtual table if not exists recall using fts5(task, titles);"
    )
    os.chmod(path, 0o600)
    return db


def _made(done_shows: str) -> str:
    """The thing a task proven done made: its done line's `new_item(<name>)`."""
    m = re.fullmatch(r"\s*new_item\((.{1,80})\)\s*", done_shows or "")
    return m[1].strip() if m else ""


def _name_for(address: str, task: str, tools) -> str:
    """A person's name for an address, by code: a collected item that holds it ("Riya Shah <riya@…>"), else a
    capitalised name right before it in the task ("Riya at riya@…", "Riya <riya@…>")."""
    for items in tools.slots.values():
        for c in items:
            if address in f"{c.title} {c.value}" and EMAIL.sub("", c.title).strip(" <>,"):
                return _short(EMAIL.sub("", c.title).strip(" <>,()"), 40)
    m = re.search(r"([A-Z][\w'-]+(?: [A-Z][\w'-]+)?)\s*(?:at\s+|<)?" + re.escape(address), task)
    return m[1] if m else ""
