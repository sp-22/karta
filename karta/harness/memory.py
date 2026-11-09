"""Memory: small facts learned on this Mac, bounded and forgetting.

Two stores:
- HarnessMemory, on disk (SQLite, one small table in a file of its own). It keeps only confirmed facts: what worked
  in a task whose declared contract code checked and found met (contract.py; see loop.remember), each row with that
  contract as its proof (the `proof` column), and a count of how often. A
  remembered fact that turns out wrong is deleted at once; no failure is ever written to disk. Facts age:
  unused for STALE_DAYS they rank below fresh ones, unused for EXPIRE_DAYS they are deleted (on open and on every
  write). Each kind is capped per site or app (places: per intent), and the whole file at MAX_ROWS, the least useful and
  oldest going first. One kind is learned from reads instead (`boilerplate`, content.py): digests of short strings a
  site showed identically on two of its pages, never the text, capped per site.
- SessionNotes, in RAM only: what failed during this task and its re-runs in this Karta process (kind, app, key, value,
  reason), for the brain's rescue prompt and to steer Laya away from options that already failed. Bounded (the last
  NOTES_LIMIT notes) and gone when the process ends, or on clear(). Pass one SessionNotes (the module's SESSION, or
  your own) to every HarnessMemory of a process to share it; a HarnessMemory made without one gets its own.

An element row also keeps HOW it worked (the `how` column, written by code from the proven step: the tool, how text
went in, how it was sent, the kind of change it caused), and a pitfall row keeps a name that did not work for an
intent in a run proven done, with the one that did. Profile rows are the user's own details (an e-mail address or
name they gave in a task proven done, the browser and profile in use, the default browser). `brief` and `profile`
render them, with places, aliases and know-how, as the plan prompt's memory block (loop.know_how).

Where a fact belongs (the `app` column): know-how, elements, pitfalls and aliases by the page's exact host for a web
page ("mail.example.com", "example.com" for www.example.com: `key_of`, `host_key`) and by the app for a native app;
never by a browser. A lookup takes the exact host's rows, and its subdomains' only when the exact host has none
(`hosts`: a task naming "example.com" gets what "app.example.com" keeps; "mail.example.com" never gets what the search
page "example.com" keeps). Places are app-less: the value is the place itself, a site in a browser and profile
("example.com in Safari") or an app ("Notes app"), never an open tab's position (tabs move: the open tab is found again
by its address).

The loop reports a wrong remembered fact with `failed(kind, app, key, value, reason)`, or with the kind's own
`*_result(..., good=False)`; both delete it on disk and note it in the session. An element whose name looks like
browser chrome, a bare site name, a name read only off the pixels, the word None, or an intent that is a plan's
description and not the control's name is never saved (`unfit_element`); nor is a pitfall whose wrong name is the
plan's own words (`add_pitfall`).

Command line: scripts/karta_memory.py (show / forget / clear).
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
create table if not exists harness_facts (
  kind text not null, app text not null, key text not null, value text not null,
  good integer not null default 0, bad integer not null default 0, updated real not null,
  how text not null default '',
  proof text not null default '',
  primary key (kind, app, key, value)
);
"""  # `bad` is no longer written (failures stay in SessionNotes); kept so files from before still open
PLACE, ALIAS, KNOW_HOW, ELEMENT, PITFALL, PROFILE = "place", "alias", "know-how", "element", "pitfall", "profile"
CONSENT = "consent"  # the user's answer: may a mail or messaging app's text go to the brain (app: the app or host)
# a site's repeated frame (content.py, Boilerplate): key = a digest of a short string seen identically on two of its
# pages, never the text; app = the site's exact host, or the app
BOILERPLATE = "boilerplate"
KINDS = (PLACE, ALIAS, KNOW_HOW, ELEMENT, PITFALL, PROFILE, CONSENT, BOILERPLATE)
# per site or app (places: per intent; the profile: in all); the least useful go first
CAPS = {PLACE: 5, ALIAS: 50, KNOW_HOW: 12, ELEMENT: 40, PITFALL: 12, PROFILE: 12, CONSENT: 1, BOILERPLATE: 120}
BRAIN = "brain"  # a consent row's key: its text may go to the brain
BRIEF_APPS, BRIEF_LINES = 3, 8  # the plan prompt's memory block: at most this many sites or apps, lines for each
VERBS = {"click": "click", "write": "type into", "fill": "fill", "answer": "answer"}
DAY = 86400.0
STALE_DAYS = 60  # unused this long: ranks below every fresh fact
EXPIRE_DAYS = 180  # unused this long: deleted
MAX_ROWS = 5000  # the whole file
NOTES_LIMIT = 200
DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "karta" / "harness-memory.sqlite"
VERSION = 3  # pragma user_version: 1 = know-how, elements and aliases per site (never per browser); places without tabs;
# 2 = web facts per exact host, no junk names, one browser in use; 3 = learned facts keep the contract that proved them
# (`proof`): elements, pitfalls, aliases and know-how learned before contracts are dropped
LEARNED = ("element", "pitfall", "alias", "know-how")  # what a run teaches: kept only with a contract's proof
SINGLE = ("browser in use", "default browser")  # profile keys with one value: the latest replaces the others
PROFILE_LINES = 6  # the profile block's lines at most (its heading aside)
DESCRIPTION_WORDS = 4  # an intent of more words than this, not the control's own name, is a plan's description
TAB_SUFFIX = re.compile(r",\s*open tab \d+\.\d+\s*$")  # an open tab's position in an old place name: never used
HOSTLIKE = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)


def place_value(name: str) -> str:
    """A place as memory keeps it: without an open tab's position ("a.example in Safari, open tab 1.3" -> "a.example in
    Safari")."""
    return TAB_SUFFIX.sub("", name.strip())


def host_key(url: str) -> str:
    """A web page's key in memory: its exact host, without "www." or a port ("https://mail.example.com/u/0" ->
    "mail.example.com")."""
    from karta.harness.facts import host_of

    return host_of(url).split(":")[0].removeprefix("www.")


def key_of(scr, browsers=()) -> str:
    """Where a remembered fact belongs: the exact host for a web page, the app for a native app, and nothing ("") for a
    browser showing no web page: never the browser, whose pages are every site."""
    if scr.url and re.match(r"^https?://", scr.url, re.I):
        return host_key(scr.url)
    return "" if scr.app in browsers else scr.app


def unfit_element(name: str, where: str, accessible: bool = True, chrome: bool = False, intent: str = "") -> str:
    """Why an item name is not worth remembering for an intent, or "": the browser's chrome (`chrome`: the address
    bar, tab search, the find bar, by structure), a host or the bare name of the site itself, no name (or the word
    None), a name read only off the pixels (`accessible`: the name came from the accessibility tree), or an `intent`
    that is a plan's description ("the field that holds the title") rather than a control's name: more than
    DESCRIPTION_WORDS words and not the item's own name."""
    text = " ".join(name.split())
    low = text.lower()
    if not text or low == "none":
        return "no name"
    if chrome:
        return "the browser's own control"
    if HOSTLIKE.match(text):
        return "a web address"
    if "." in where and " " not in where:  # a site: its own name is no control
        from karta.harness.facts import site_of

        host, site = where.lower().removeprefix("www."), site_of(where.lower())
        if low.replace(" ", "") in (host, site, host.split(".")[0], site.split(".")[0], f"www.{site}"):
            return "the site's own name"
    if not accessible:
        return "not an accessible name (read off the pixels)"
    if len(_key(intent).split()) > DESCRIPTION_WORDS and _key(intent) != _key(text):
        return "the plan's description, not a control's name"
    return ""


def _key(text: str) -> str:
    return " ".join(text.lower().split())


def key_for(kind: str, key: str) -> str:
    """How each kind stores its key: know-how as written (trimmed), the others lower case with spaces collapsed."""
    return key.strip() if kind == KNOW_HOW else _key(key)


# ------------------------------------------------------------------ in RAM: this process's failures


@dataclass(frozen=True)
class Note:
    kind: str
    app: str
    key: str
    value: str
    reason: str = ""
    at: float = 0.0
    times: int = 1

    def line(self) -> str:
        where = f"{self.app}: " if self.app else ""
        what = f"{self.kind} {self.key!r}" + (f" -> {self.value!r}" if self.value else "")
        again = f" ({self.times} times)" if self.times > 1 else ""
        return f"{where}{what} failed{again}" + (f": {self.reason}" if self.reason else "")


class SessionNotes:
    """What failed in this process: never written to disk, the last `limit` notes kept (a repeat moves to the end)."""

    def __init__(self, limit: int = NOTES_LIMIT, clock: Callable[[], float] = time.time):
        self.limit = limit
        self.clock = clock
        self.notes: OrderedDict[tuple[str, str, str, str], Note] = OrderedDict()

    def add(self, kind: str, app: str, key: str, value: str = "", reason: str = "") -> None:
        ident = (kind, app, key_for(kind, key), value)
        old = self.notes.pop(ident, None)
        times = old.times + 1 if old else 1
        self.notes[ident] = Note(*ident, reason=reason or (old.reason if old else ""), at=self.clock(), times=times)
        while len(self.notes) > self.limit:
            self.notes.popitem(last=False)

    def for_app(self, app: str) -> list[Note]:
        """The app's notes (and those tied to no app, such as places), newest first."""
        return [n for n in reversed(self.notes.values()) if n.app in (app, "")]

    def tried(self, kind: str, app: str, key: str) -> set[str]:
        """The values that already failed for this key here: options to steer Laya away from."""
        key = key_for(kind, key)
        return {n.value for n in self.notes.values() if n.kind == kind and n.app == app and n.key == key}

    def lines(self, limit: int = 8, app: str | None = None) -> list[str]:
        """Short lines for the brain's rescue prompt, newest first; only the app's (and app-less) notes when given."""
        notes = self.for_app(app) if app is not None else list(reversed(self.notes.values()))
        return [n.line() for n in notes[:limit]]

    def clear(self) -> None:
        self.notes.clear()

    def __len__(self) -> int:
        return len(self.notes)


SESSION = SessionNotes()  # one per process, for callers that want every run of the process to share it


# ------------------------------------------------------------------ on disk: confirmed facts


@dataclass(frozen=True)
class Fact:
    kind: str
    app: str
    key: str
    value: str
    good: int
    updated: float
    stale: bool  # unused for STALE_DAYS or more
    how: str = ""  # an element's way ("write|pasted|Return|opened page /results"), a pitfall's right name


class HarnessMemory:
    """Places ("Chatterbox chats" -> the place name that worked), name aliases ("Marisal" -> "Marisol Okafor"),
    elements (which item an intent meant, per site or app), know-how ("Reminders: set the time in the info panel"),
    per site or app. Each fact counts how often it worked; see the module docstring for keys, deletion, ageing and caps.

    `readonly` opens an existing file without writing anything (no expiry either): for showing it.

    `pins`: the pinned tasks (recipes.RecipeBook) in pins.json beside the file; None in RAM or read-only."""

    def __init__(
        self,
        path: Path | str,
        clock: Callable[[], float] = time.time,
        notes: SessionNotes | None = None,
        readonly: bool = False,
    ):
        self.clock = clock
        self.notes = notes if notes is not None else SessionNotes(clock=clock)
        self.readonly = readonly
        self.pins = None
        self.proving = ""  # the contract the run being remembered met (loop.remember sets it): each row keeps it
        if readonly:
            self.db = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
            self.how = "how" if self._has_how() else "''"
            return
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        if not self._has_how():  # a file from before the column
            self.db.execute("alter table harness_facts add column how text not null default ''")
        if not self._has("proof"):
            self.db.execute("alter table harness_facts add column proof text not null default ''")
        self.how = "how"
        self.db.execute("delete from harness_facts where good <= bad")  # failure records from before: not kept
        self._migrate()
        self._tidy()
        self.db.commit()
        if str(path) != ":memory:":
            from karta.harness.recipes import FILE, RecipeBook

            self.pins = RecipeBook(Path(path).with_name(FILE), clock=clock)

    # ------------------------------------------------------------ the one table

    def _migrate(self) -> None:
        """A file from before VERSION 3: the facts a run teaches (elements, pitfalls, aliases, know-how) were learned
        from Karta's own judgement of success, never from a contract: they are dropped, to be learned again with their
        proof. Places lose an open tab's position (merged into the site's; before VERSION 1), and the profile keeps
        one browser in use (before VERSION 2)."""
        (version,) = self.db.execute("pragma user_version").fetchone()
        if version >= VERSION:
            return
        marks = ", ".join("?" * len(LEARNED))
        self.db.execute(f"delete from harness_facts where kind in ({marks}) and proof = ''", LEARNED)
        if version < 1:
            self._places_by_site()
        if version < 2:
            self._one_browser()
        self.db.execute(f"pragma user_version = {VERSION}")

    def _places_by_site(self) -> None:
        """A place kept with an open tab's position merges into its site's (tabs move: never used)."""
        rows = self.db.execute("select rowid, app, key, value, good, updated from harness_facts where kind = ?", (PLACE,))
        for rowid, app, key, value, good, updated in rows.fetchall():
            if place_value(value) == value:
                continue
            self.db.execute("delete from harness_facts where rowid = ?", (rowid,))
            self.db.execute(
                "insert into harness_facts (kind, app, key, value, good, updated) values (?, ?, ?, ?, ?, ?) "
                "on conflict (kind, app, key, value) do update set good = good + excluded.good, "
                "updated = max(updated, excluded.updated)",
                (PLACE, app, key, place_value(value), good, updated),
            )

    def _one_browser(self) -> None:
        """One value for a profile key that has one (SINGLE): the latest."""
        for key in SINGLE:
            self.db.execute(
                "delete from harness_facts where kind = ? and app = '' and key = ? and rowid not in (select rowid from "
                "harness_facts where kind = ? and app = '' and key = ? order by updated desc limit 1)",
                (PROFILE, key, PROFILE, key),
            )

    def _has_how(self) -> bool:
        return self._has("how")

    def _has(self, column: str) -> bool:
        return any(r[1] == column for r in self.db.execute("pragma table_info(harness_facts)"))

    def _stale_before(self) -> float:
        return self.clock() - STALE_DAYS * DAY

    def _tidy(self, keep: tuple[str, str, str, str] | None = None) -> None:
        """Delete what went unused for EXPIRE_DAYS, then hold the file to MAX_ROWS (stale, least useful, oldest first)."""
        self.db.execute("delete from harness_facts where updated < ?", (self.clock() - EXPIRE_DAYS * DAY,))
        (count,) = self.db.execute("select count(*) from harness_facts").fetchone()
        if count > MAX_ROWS:
            keep = keep or ("", "", "", "")
            self.db.execute(
                "delete from harness_facts where rowid in (select rowid from harness_facts "
                "where not (kind = ? and app = ? and key = ? and value = ?) "
                "order by (updated < ?) desc, good asc, updated asc limit ?)",
                (*keep, self._stale_before(), count - MAX_ROWS),
            )

    def result(self, kind: str, app: str, key: str, value: str, good: bool, how: str = "") -> None:
        """One use of a fact (key in its stored form: see key_for): good counts it and refreshes its age, keeping
        its kind within the cap; not good is `failed`. `how`, when given, replaces the row's (the latest way wins)."""
        if not good:
            self.failed(kind, app, key, value)
            return
        row = (kind, app, key, value)
        self.db.execute(
            "insert into harness_facts (kind, app, key, value, good, updated, how, proof) values (?, ?, ?, ?, 1, ?, ?, ?) "
            "on conflict (kind, app, key, value) do update set good = good + 1, updated = excluded.updated, "
            "how = case when excluded.how != '' then excluded.how else how end, "
            "proof = case when excluded.proof != '' then excluded.proof else proof end",
            (*row, self.clock(), how, self.proving),
        )
        group = "key" if kind == PLACE else "app"  # places are capped per intent, the rest per app
        self.db.execute(  # the fact just counted stays: a new one must get the chance to prove itself
            f"delete from harness_facts where rowid in (select rowid from harness_facts where kind = ? and {group} = ? "
            "order by (app = ? and key = ? and value = ?) desc, (updated < ?) asc, good desc, updated desc "
            "limit -1 offset ?)",
            (kind, key if kind == PLACE else app, app, key, value, self._stale_before(), CAPS[kind]),
        )
        self._tidy(keep=row)
        self.db.commit()

    def failed(self, kind: str, app: str, key: str, value: str = "", reason: str = "") -> None:
        """A remembered fact turned out wrong: deleted on disk at once (a fact not on disk is left alone), and the
        failure noted in this process's SessionNotes only. `key` in either form."""
        key = key_for(kind, key)
        if kind == PLACE:
            value = place_value(value)
        self.notes.add(kind, app, key, value, reason)
        self.db.execute("delete from harness_facts where kind = ? and app = ? and key = ? and value = ?", (kind, app, key, value))
        self.db.commit()

    def best(self, kind: str, app: str, key: str) -> str | None:
        """The most useful value that worked: fresh facts before stale ones, then the count, then the latest."""
        row = self.db.execute(
            "select value from harness_facts where kind = ? and app = ? and key = ? and good > bad "
            "order by (updated < ?) asc, good - bad desc, updated desc limit 1",
            (kind, app, key, self._stale_before()),
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------ places

    def place_for(self, what: str) -> str | None:
        """The place that worked for `what`: a site in a browser and profile, or an app; never a tab's position."""
        best = self.best(PLACE, "", _key(what))
        return place_value(best) if best else None

    def place_result(self, what: str, place: str, good: bool) -> None:
        self.result(PLACE, "", _key(what), place_value(place), good)

    def places(self) -> list[str]:
        """Every remembered place that worked (so it was signed in and ready then), fresh and most useful first."""
        rows = self.db.execute(
            "select value from harness_facts where kind = ? and good > bad group by value "
            "order by max(updated >= ?) desc, sum(good) desc, max(updated) desc",
            (PLACE, self._stale_before()),
        )
        return list(dict.fromkeys(place_value(r[0]) for r in rows))

    # ------------------------------------------------------------ name aliases

    def alias_for(self, name: str, app: str = "") -> str | None:
        """What a name said turned out to be on a site or app (or anywhere, for app "")."""
        return next((v for where in (self.hosts(app) if app else [""]) if (v := self.best(ALIAS, where, _key(name)))), None)

    def remember_alias(self, said: str, meant: str, app: str = "") -> None:
        self.result(ALIAS, app, _key(said), meant, True)

    def alias_result(self, said: str, meant: str, good: bool, app: str = "") -> None:
        """The alias turned out right (good) or wrong: "Hemansu" was not "Hemanshu Patel" after all."""
        self.result(ALIAS, app, _key(said), meant, good)

    # ------------------------------------------------------------ know-how, per site or app

    def hosts(self, app: str) -> list[str]:
        """Where a lookup for `app` reads: the app or exact host itself when anything is kept for it; else, for a host,
        its subdomains that keep something ("example.com" -> "app.example.com"), as a page on a subdomain is on the
        site (tools.on_site); never a parent or a sibling host."""
        if not app:
            return []
        if self.db.execute("select 1 from harness_facts where app = ? limit 1", (app,)).fetchone() or "." not in app:
            return [app]
        rows = self.db.execute(
            "select app from harness_facts where app like ? and app != '' group by app order by max(updated) desc",
            (f"%.{app}",),
        )
        return [r[0] for r in rows if " " not in r[0]] or [app]

    def know_how(self, app: str, limit: int = 5) -> list[str]:
        """The know-how kept for a site ("example.com", by `hosts`) or a native app; nothing for no key."""
        out: list[str] = []
        for where in self.hosts(app):
            rows = self.db.execute(
                "select key from harness_facts where kind = ? and app = ? and good > bad "
                "order by (updated < ?) asc, good - bad desc, updated desc limit ?",
                (KNOW_HOW, where, self._stale_before(), limit),
            )
            out += [r[0] for r in rows]
        return out[:limit]

    def add_know_how(self, app: str, fact: str) -> None:
        if app and fact.strip():  # never know-how tied to nothing
            self.result(KNOW_HOW, app, fact.strip(), "", True)

    def know_how_result(self, app: str, fact: str, good: bool) -> None:
        """A know-how the brain was given helped (good) or misled it (bad)."""
        self.result(KNOW_HOW, app, fact.strip(), "", good)

    # ------------------------------------------------------------ elements (which item an intent meant, per site or app)

    def element_result(
        self, app: str, intent: str, name: str, good: bool, accessible: bool = True, chrome: bool = False, how: str = ""
    ) -> str:
        """Count (good) or drop (not good) the item an intent meant on a site or app, with how it worked (`how`:
        "tool|text went in|sent by|the change", see loop.way). A good one that is not worth keeping (`unfit_element`)
        is not saved: the reason is returned ("" when saved or dropped). A pitfall that named it for the intent goes."""
        if good:
            why = "no site or app" if not app else unfit_element(name, app, accessible, chrome, intent)
            if why:
                return why
            self.forget(PITFALL, app, intent, name)
        self.result(ELEMENT, app, _key(intent), name, good, how)
        return ""

    def add_pitfall(self, app: str, intent: str, wrong: str, right: str) -> None:
        """In a run proven done, `wrong` did not work for `intent` here and `right` then did: "not X but Y". Never a
        name not worth keeping as an element, never one that is the right one, and never the plan's own words (a
        wrong name that is the intent itself: the plan's description, never a name read on screen)."""
        fine = not unfit_element(wrong, app) and not unfit_element(right, app)
        if app and _key(wrong) not in (_key(right), _key(intent)) and fine:
            self.result(PITFALL, app, _key(intent), wrong, True, right)

    def element_for(self, app: str, intent: str) -> str | None:
        return next((v for where in self.hosts(app) if (v := self.best(ELEMENT, where, _key(intent)))), None)

    def bad_elements(self, app: str, intent: str) -> set[str]:
        """The items that were wrong for this intent here, in this process (SessionNotes: never kept on disk)."""
        return self.notes.tried(ELEMENT, app, intent)

    # ------------------------------------------------------------ the user's own details (one profile, app "")

    def add_profile(self, key: str, value: str) -> None:
        """A detail of the user's own ("email", "name", "browser in use", "default browser"), only from a task proven
        done. A key with one value (SINGLE) keeps only the latest."""
        if key.strip() and value.strip():
            if _key(key) in SINGLE:
                self.db.execute(
                    "delete from harness_facts where kind = ? and app = '' and key = ? and value != ?",
                    (PROFILE, _key(key), value.strip()),
                )
            self.result(PROFILE, "", _key(key), value.strip(), True)

    # ------------------------------------------------------------ consent: an app's text to the brain

    def consent(self, app: str) -> bool | None:
        """The user's answer for `app` (a mail or messaging app, or a web page's host): may its text go to the brain?
        None: never asked (or forgotten). Each use keeps the answer fresh."""
        value = self.best(CONSENT, app, BRAIN)
        if value is None:
            return None
        self.result(CONSENT, app, BRAIN, value, True)
        return value == "yes"

    def set_consent(self, app: str, yes: bool) -> None:
        """Keep the user's answer for `app`, the latest only (CAPS: one per app)."""
        if app.strip():
            self.result(CONSENT, app, BRAIN, "yes" if yes else "no", True)

    # ------------------------------------------------------------ boilerplate: a site's repeated frame (content.py)

    def boilerplate(self, site: str) -> set[str]:
        """The digests of `site`'s learned frame (content.Boilerplate): short strings seen on two of its pages."""
        rows = self.db.execute("select key from harness_facts where kind = ? and app = ?", (BOILERPLATE, site))
        return {k for (k,) in rows}

    def add_boilerplate(self, site: str, digests) -> None:
        """Keep `digests` for `site`, at most CAPS[BOILERPLATE] (the oldest go first), in one write."""
        if self.readonly or not site:
            return
        now = self.clock()
        self.db.executemany(
            "insert into harness_facts (kind, app, key, value, good, updated) values (?, ?, ?, '', 1, ?) "
            "on conflict (kind, app, key, value) do update set good = good + 1, updated = excluded.updated",
            [(BOILERPLATE, site, d, now) for d in digests],
        )
        self.db.execute(
            "delete from harness_facts where rowid in (select rowid from harness_facts where kind = ? and app = ? "
            "order by updated desc, good desc limit -1 offset ?)",
            (BOILERPLATE, site, CAPS[BOILERPLATE]),
        )
        self._tidy()
        self.db.commit()

    # ------------------------------------------------------------ the plan prompt's memory block

    def _rows(self, kind: str, app: str, limit: int) -> list[tuple[str, str, str, int]]:
        """(key, value, how, count) of a kind here: fresh first, then the most used, then by key (a stable order)."""
        return self.db.execute(
            f"select key, value, {self.how}, good - bad from harness_facts where kind = ? and app = ? and good > bad "
            "order by (updated < ?) asc, good - bad desc, key, value limit ?",
            (kind, app, self._stale_before(), limit),
        ).fetchall()

    def places_named(self, text: str) -> list[str]:
        """The sites and apps of remembered places whose intent's words are all in `text` ("the ledger" -> the place
        that worked for "ledger"): where a plan for it is likely to go."""
        said = set(re.findall(r"\w+", text.lower()))
        rows = self.db.execute(
            "select key, value from harness_facts where kind = ? and good > bad order by good desc, key", (PLACE,)
        )
        out = []
        for key, value in rows:
            if set(re.findall(r"\w+", key)) <= said:
                where = place_value(value).split(" in ", 1)[0]
                out.append(where.removesuffix(" app"))
        return list(dict.fromkeys(out))

    def brief(self, apps: list[str], max_apps: int = BRIEF_APPS, max_lines: int = BRIEF_LINES) -> list[str]:
        """What worked before on these sites ("example.com") and apps, as plain lines for the plan prompt, the same
        wording and order every time: where it opens, each control or field with how it worked, the pitfalls, the
        know-how. At most `max_lines` lines for each of `max_apps` places that have any."""
        out = []
        for app in dict.fromkeys(where for a in apps if a for where in self.hosts(a)):
            if len([line for line in out if not line.startswith("- ")]) >= max_apps:
                break
            lines, counts = [], [0]
            for value, n in self._opens_in(app):
                lines.append(f"- opens in: {value}")
                counts.append(n)
            for key, name, how, n in self._rows(ELEMENT, app, CAPS[ELEMENT]):
                if how:  # rows without a way (from before) serve picks at step time only
                    lines.append(f"- {key}: {way_text(name, how)}")
                    counts.append(n)
            for key, wrong, right, n in self._rows(PITFALL, app, CAPS[PITFALL]):
                lines.append(f'- pitfall: for {key}, not "{wrong}" but "{right}"')
                counts.append(n)
            for key, _value, _how, n in self._rows(KNOW_HOW, app, 3):
                lines.append(f"- {key}")
                counts.append(n)
            if lines:
                runs = max(counts)
                out.append(f"Known about {app} (worked in {runs} proven run{'s' if runs != 1 else ''}):")
                out += lines[:max_lines]
        return out

    def _opens_in(self, app: str) -> list[tuple[str, int]]:
        """The browser and profile a site opened in, from its places ("example.com in Safari" -> "Safari")."""
        rows = self.db.execute(
            "select value, sum(good - bad) from harness_facts where kind = ? and good > bad group by value "
            "order by sum(good - bad) desc, value",
            (PLACE,),
        )
        out = []
        for value, n in rows:
            host, _, where = place_value(value).partition(" in ")
            host = host.lower().removeprefix("www.")
            if where and host == app.lower():
                out.append((where, n))
        return out[:1]

    def profile(self, task: str = "", limit: int = PROFILE_LINES) -> list[str]:
        """The user's own details, one line a key with one value (SINGLE), and the names they used (aliases) whose
        name `task` says, as plain lines for the plan prompt: at most `limit` lines."""
        rows = self.db.execute(
            "select key, value from harness_facts where kind = ? and app = '' and good > bad order by key, updated desc, value",
            (PROFILE,),
        ).fetchall()
        seen: set[str] = set()
        lines = []
        for key, value in rows:
            if key in SINGLE and key in seen:
                continue
            seen.add(key)
            lines.append(f"- {key}: {value}")
        said = set(re.findall(r"\w+", task.lower()))
        names = self.db.execute(
            "select key, value, app from harness_facts where kind = ? and good > bad and app != '' order by good - bad desc, key",
            (ALIAS,),
        )
        lines += [f'- "{k}" is "{meant}" on {app}' for k, meant, app in names if set(re.findall(r"\w+", k)) <= said]
        lines = lines[:limit]
        return (["About the user (from tasks proven done):", *lines]) if lines else []

    # ------------------------------------------------------------ looking and forgetting (scripts/karta_memory.py)

    def forget(self, kind: str, app: str, key: str, value: str | None = None) -> int:
        """Delete a fact (every value of the key, or only `value`); the number of facts deleted."""
        args: tuple[str, ...] = (kind, app, key_for(kind, key))
        extra = ""
        if value is not None:
            extra, args = " and value = ?", (*args, value)
        deleted = self.db.execute(f"delete from harness_facts where kind = ? and app = ? and key = ?{extra}", args)
        self.db.commit()
        return deleted.rowcount

    def clear(self) -> int:
        """Forget everything on disk; the number of facts deleted."""
        deleted = self.db.execute("delete from harness_facts")
        self.db.commit()
        return deleted.rowcount

    def list(self, kind: str | None = None, app: str | None = None) -> list[Fact]:
        """The facts kept, by kind, app and key; only the given kind and app when given."""
        filters = [(c, v) for c, v in (("kind = ?", kind), ("app = ?", app)) if v is not None]
        where = (" where " + " and ".join(c for c, _ in filters)) if filters else ""
        rows = self.db.execute(
            f"select kind, app, key, value, good - bad, updated, {self.how} from harness_facts{where} "
            "order by kind, app, key, good - bad desc, updated desc",
            tuple(v for _, v in filters),
        ).fetchall()
        stale = self._stale_before()
        return [Fact(*r[:6], stale=r[5] < stale, how=r[6]) for r in rows]


def way_text(name: str, how: str) -> str:
    """An element's way in words: "write|pasted|Return|opened page /results" -> 'type into "Search" (pasted), then
    Return → opened page /results'."""
    tool, entry, sent, change = [*how.split("|"), "", "", ""][:4]
    text = f'{VERBS.get(tool, tool)} "{name}"' + (f" ({entry})" if entry else "")
    return text + (f", then {sent}" if sent else "") + (f" → {change}" if change else "")
