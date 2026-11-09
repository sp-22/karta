"""Recorded runs as offline tests: scrub a `--record` file, then replay it through the real harness.

A run recorded with `karta --record` (karta/harness/snapshots.py) keeps every screen the harness read, each action,
the brain's replies, Laya's answers, memory's reads and the user's answers. `replay` runs the same task through
`loop.run_task` again with all of them served back from the file, so nothing reaches the Mac, the network or a
model:

- `ReplayEnv` (a HarnessEnv) serves the recorded screens in order as actions happen. Each action the harness takes
  must be the next recorded one; anything else stops the replay with a diff of what ran and what should have run.
- `ReplayBrain` hands back the recorded replies, request by request (a request of another kind is a divergence).
- Laya is the real `decide.Laya` with a recorded agent: each question gets the recorded probabilities, and each
  embedding the recorded similarities. `laya="real"` uses the real Laya instead (loaded offline, HF_HUB_OFFLINE=1),
  which shows where the model now decides differently.
- memory's reads, the user's yes/no answers and replies come from the file too.

`Scrubber` makes a recording safe to commit (scripts/promote_run.py): every word of page text, titles, names,
emails, messages, typed text, brain lines and web paths and queries is replaced by a made-up stand-in, unless it is
one of LEXICON (interface and harness words), part of a site's own domain, or part of an app's name; a Laya
question's placeholder values are scrubbed like typed text (their names and its screen index are kept). A stand-in is
a fixed function of the word and a secret salt, so the same word becomes the same stand-in everywhere in the file
(checkpoints still match what the page shows) and a guessed name cannot be confirmed without the salt. Structure,
kinds, regions, flags, frames and app names are kept. `leaks` lists any word a case still shows that is none of those.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import secrets
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from karta.harness.decide import NOT_PRIVATE, PRIVATE_ASK, PRIVATE_KINDS
from karta.harness.snapshots import CUT, MEMORY_READS, brain_kind, host, item_json, screen_back
from karta.harness.types import Item, MacFacts, Screen

CASES = Path(__file__).resolve().parents[2] / "tests" / "replay"
SALT_FILE = Path.home() / "Library" / "Application Support" / "karta" / "scrub-salt"

# Words the harness itself says, keys on, or that interfaces share (frozen, so a promoted case scrubs the same way
# whatever the code says later): string constants of the harness (decide, brain, tools, loop, facts, risk, retry,
# match, screen, tell, types; lowercase only, so no example name) plus common words, menu and date words.
LEXICON = frozenset(
    """
a about accept access accessibility account accounts acme across act acting action actions activity acts ad add
added adding address addresses adds ads advertisement after again agree ahead alerts alias alike all allow almost
alone aloud already also alt alternatives alts always am amazon among amount amounts an and another answer answered
answers any anything app appear appeared appears apple application apply approach apps apr april archive are area
article articles as ask asked asking asks assistant at atlassian attach attachment audio aug august automation
available away ax b back backspace bag balances banner bar basket bcc be been before begin being belongs below
beside best between block bluetooth board body book bookmarks books both bottom box brain brings brought browser btn
build but button buy by cache calculator calendar call called calls came camera can cancel cannot card carries cart
cc cd centre change changed changes character characters charting chat chatgpt chats check checkbox checked checkout
checkpoint checks choice choices choose choosing chose chosen click clicked clicking clicks clock close closed
closing cmd co code collect collected collects com comes comma command commands comment comments companies
completion compose computer concrete configured confirm connect connecting console contacts containing content
continue contrast control controls copy costs could couldn count create created criteria current cut d date day dec
december decider deciding decision decline decrease decrement default delete deleted demoted deny description
desktop detail details dev dialog dictionary did different dir direct direction directory disabled discard discord
disk dismiss dist dm do docs document does dollars don done dont down download draft drafts drifted drive due e each
earlier edit editor eight element eleven eliminated else email emoji emptied empty enable enabled end ended ends
enough enter entries entry env erase error errors es escape euros even evening event events every everything
evidence exact exactly example existing exists expect expected f facebook facts failed failures false far fares feb
february fees fence fetching few field fields figma file files fill filled filter find finder finish finished first
fit fits five fix fixes floor focus focused focuses folder folders for force forever form format forward found four
frame free freeform friday from front full g games gate gates gave generic get getting github give given go goes
gone good google got grocery guess h had happening hard harness has have havent having he heading help her here hers
hide high him his history holds home honest host how html http https i icon id ids if ignored image important in
inbox increase increment info ing input inr insert inside install installed instead instructions interested internet
interrupt interruption into invent invoices io is issue issues it item items its itself jan january join journal
json jul july jun june junk just k karta keep keeps kept key keyboard keys kind know knowing knows l label labeled
labelled last later latest laya leads learn learned leave leaves ledger left letters libraries library libs like
line linear lines link linkedin links list listed lists loading loads local localized location log login logout
look looked lost loud m mail mailbox main make map maps mar march margin markdown match matched matches may maybe me
meaning means meeting meetings memory ment menu message messages meta microphone microsoft middle might mind mine
minus misheard miss missing misspoken mobile modal model modules moment monday money month more morning most move
moved moves ms music must my n name named names naming near nearest need needed needs neither net network never new
newest news next nine no nobody node none nor normal not note notes nothing notification notifications notion nov
november now nowhere number numbered numbering o object ocr oct october of off offer offers office ok okay olst omit
omnibox on once one online only onto open opened opening opens option options or order orders org other otherwise
otp our ours out outbox outcome outlook output over overdue own p page pages panel paper part passcode passed
passkey password passwords paste pay payment payments per permission person personal phone photo photos php pick
picked picking picks pin place places plain plan planned plans playground please plus pm poc podcasts policy pop
popup position post posted posts preferences press pressed pressing preview previous price priced prices printed
printer prints probabilities problem proceed profile profiles progress project promoted prompt prose provider
publish pulls purchase push puts pycache q qr question quit ran ranked re reach reached reaches read readable reads
ready reason recent recipient record recording redo ref refresh reject rejected remaining remembered remind reminder
reminders removable remove removed removes renamed repair repeat repeated repeating repeats replace replaces replan
reply repo repository requests rescue restart restated result results retry return reused reveal reversible right
risk risky role row rows rs run running rupees s said same saturday save saves saw say says scan screen scroll
search searched searches searching seconds see seen select selected send sending sends sent sentences sep separated
september servers serves service set setting settings setup seven several shall share she sheet sheets shift
shopping short shortcut shortcuts shorter should show shown shows shut sidebar sign signed signin signing sim since
site sites six skip skipped skipping skips slack slot small so some something soon sort source spam speak speech
spelling spoken sponsored spotify sso standup starred start started starts state status stay stayed stays step
steps still stocks stop stopped stream structure stuck sub subject submit subscribe subtotal success successfully
such sunday sure surely swiggy switch synonym system t tab tabs take taken takes tap target task tasks telegram tell
temperature template ten terminal test text textbox texts th than thank thanks that the their theirs them then there
these they things this those though three through thursday ti tickets time times tip tips title titled to today
toggle tok token tokens tomorrow tonight too took tool toolbar tools top total totals tour track tracking transfer
trash trello trial tried tries trolley true try tuesday turn tweet twelve twitter two type typed types uk unasked
unchecked under undo undone uninstall unknown unreadable unsubscribe unsure until up update upgrade url urls us
usable usage use used user username users uses using utf valid value values variants ve venv very video videos view
visible volumes w wait waited waiting waits wanted wants was way we weather web website wednesday week went were
what whatsapp when where whether which while who whom whose why wi will window windows with without word worded
words work worked works worth would write written wrong www x xywh y year yes yesterday yet you your yours yourself
youtube z zero zomato zoom
""".split()  # noqa: SIM905 - a word list reads better as text
)
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "ne", "or"}  # example.co.uk: three labels are the site
CONSONANTS, VOWELS = "bdfgklmnprstvz", "aeiou"
STANDIN = re.compile(r"^x(?:[bdfgklmnprstvz][aeiou]){3}$")
WORD = re.compile(r"[^\W_]+")
# Values that are structure, never content: they are kept as they are.
STRUCTURE_KEYS = {"kind", "region", "tool", "via", "call", "source", "version", "prompt", "laya_temperatures", "now", "events"}


# ---------------------------------------------------------------- scrubbing


def site_labels(hostname: str | None) -> list[str]:
    """The labels that name the site itself: "mail.example.co.uk" -> ["example", "co", "uk"]; a subdomain stays out
    (it can be a person's own: "sam.example.io")."""
    labels = [x for x in (hostname or "").lower().split(".") if x]
    if len(labels) >= 3 and labels[-2] in SECOND_LEVEL and len(labels[-1]) == 2:
        return labels[-3:]
    return labels[-2:]


def _strings(value: Any, key: str = "") -> Iterator[tuple[str, str]]:
    """Every (key, text) inside a record, structure keys left out."""
    if key in STRUCTURE_KEYS:
        return
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            if key == "browsers":
                yield key, str(k)
            yield from _strings(v, k if not isinstance(k, int) else key)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, key)


def kept_words(record: dict) -> set[str]:
    """The words a record keeps besides LEXICON: its sites' own domains and its apps' names."""
    words: set[str] = set()
    hosts: list[str | None] = []
    for screen in [*record.get("screens", []), *(a.get("screen") or {} for a in record.get("actions", []))]:
        hosts.append(host(screen.get("url")))
        hosts += [host(i.get("url")) for i in screen.get("items", [])]
    mac = record.get("mac") or {}
    hosts += [host(t.get("url")) for t in [*(mac.get("tabs") or []), *(record.get("tabs_refreshed") or [])]]
    hosts += [str(a.get("detail", "")).split(" in ", 1)[0] for a in record.get("actions", []) if a.get("tool") == "open_url"]
    for h in hosts:
        if h and "." in h:
            words |= set(site_labels(h))
    apps = [s.get("app", "") for s in record.get("screens", [])]
    apps += list(mac.get("installed_apps", [])) + list(mac.get("browsers", {})) + list(mac.get("running", []))
    apps += [a.get("detail", "") for a in record.get("actions", []) if a.get("tool") in ("open_app", "focus_tab")]
    for app in apps:
        words |= {w.lower() for w in WORD.findall(str(app))}
    return words


def local_salt() -> str:
    """This Mac's own scrub salt, made once: the same recording scrubs the same way here, and nowhere else."""
    try:
        return SALT_FILE.read_text().strip()
    except OSError:
        salt = secrets.token_hex(16)
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SALT_FILE.write_text(salt + "\n")
        return salt


class Scrubber:
    """Replaces every word that could be someone's content with a stand-in; the same word always gets the same one."""

    def __init__(self, salt: str, keep: Iterable[str] = ()):
        self.salt = salt
        self.keep = set(LEXICON) | {w.lower() for w in keep}

    def _digest(self, word: str, n: int = 32) -> bytes:
        out, i = b"", 0
        while len(out) < n:
            out += hashlib.sha256(f"{self.salt}\0{i}\0{word}".encode()).digest()
            i += 1
        return out[:n]

    def word(self, word: str) -> str:
        low = word.lower()
        if low in self.keep or STANDIN.match(low) or (len(low) == 1 and not low.isdigit()):
            return word
        if low.isdigit():
            if len(low) <= 2:
                return word  # a count, a day, an hour: no one's number
            digits = "".join(str(b % 10) for b in self._digest(low, len(low)))
            return ("1" + digits[1:]) if low[0] != "0" and digits[0] == "0" else digits
        h = self._digest(low, 6)
        made = "x" + "".join(CONSONANTS[h[2 * i] % len(CONSONANTS)] + VOWELS[h[2 * i + 1] % len(VOWELS)] for i in range(3))
        if len(word) > 1 and word.isupper():
            return made.upper()
        return made.capitalize() if word[0].isupper() else made

    def text(self, text: str | None) -> str | None:
        if not text:
            return text
        return WORD.sub(lambda m: self.word(m.group()), text)

    def deep(self, value: Any, key: str = "") -> Any:
        if key in STRUCTURE_KEYS:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            rename = key == "browsers"
            return {(self.text(k) if rename else k): self.deep(v, k if not rename else key) for k, v in value.items()}
        if isinstance(value, list):
            return [self.deep(v, key) for v in value]
        return value


def scrub_record(record: dict, salt: str) -> dict:
    """A recording with every piece of content replaced (see the module docstring); structure kept as it is."""
    scrubber = Scrubber(salt, kept_words(record))
    out = scrubber.deep(record)
    out["scrubbed"] = True
    return out


def leaks(record: dict) -> list[str]:
    """Words in a case that are no stand-in, number, single letter, LEXICON word, site domain or app name."""
    keep = set(LEXICON) | kept_words(record)
    found = []
    for _key, text in _strings(record):
        for w in WORD.findall(text):
            low = w.lower()
            if low in keep or STANDIN.match(low) or low.isdigit() or len(low) == 1:
                continue
            found.append(w)
    return sorted(set(found))


# ---------------------------------------------------------------- replaying


class ReplayDivergence(BaseException):
    """The harness did something the recorded run did not (a BaseException: no `except Exception` in the harness
    can hide it)."""


def item_line(data: dict) -> str:
    return f"{data.get('name', '')!r} {data.get('kind', '')}".rstrip()


def action_line(entry: dict) -> str:
    """One recorded action as a line: "click 'Send' button", "set_text 'To' field = 'Sam'", "open_url x.com in Safari"."""
    tool = entry.get("tool", "")
    target = entry.get("target")
    parts = [tool]
    if target:
        parts.append(item_line(target))
    if entry.get("text"):
        parts.append(("= " if target else "") + repr(entry["text"]))
    if entry.get("detail") and tool != "wait":
        parts.append(entry["detail"])
    return " ".join(parts)


def _cut(text: str) -> str:
    return text if len(text) <= CUT else text[: CUT - 1] + "…"


def _same_text(ran: str, recorded: str) -> bool:
    """Typed text against the recorded text, which may be cut at 80 characters (and scrubbed after the cut)."""
    if ran == recorded:
        return True
    if recorded.endswith("…"):
        words = recorded[:-1].split()[:-1]  # the last word may be cut in half
        return ran.split()[: len(words)] == words
    return False


class Replay:
    """The shared state of one replay: where in the recording the env, the brain, Laya and the user are."""

    def __init__(self, record: dict):
        self.record = record
        self.screens = [screen_back(s) for s in record.get("screens", [])]
        self.events = list(record.get("events", []))
        self.actions = list(record.get("actions", []))
        self.expected = [action_line(a) for a in self.actions]
        self.ran: list[str] = []
        self.cursor = 0
        self.current: Screen | None = None
        self.brain = list(record.get("brain", []))
        self.laya = list(record.get("laya", []))
        self.embeds = list(record.get("embeds", []))
        self.confirms = list(record.get("confirms", []))
        self.asks = list(record.get("asks", []))
        self.problems: list[str] = []  # divergences found where raising would hang the harness (the brain's thread)

    # ---- the screen

    def _show(self, n: int) -> Screen:
        s = self.screens[n]
        self.current = Screen(s.app, list(s.items), s.url, s.window, s.profile)
        return self.current

    def read(self) -> Screen:
        if self.cursor < len(self.events) and "read" in self.events[self.cursor]:
            n = self.events[self.cursor]["read"]
            self.cursor += 1
            return self._show(n)
        if self.current is None:  # a read before any recorded read: the first one there is
            first = next((e["read"] for e in self.events if "read" in e), None)
            if first is None:
                raise ReplayDivergence("the recording has no screen")
            return self._show(first)
        return self.current

    def _pending_change(self, before: str) -> bool:
        """A recorded read before the next action that differs from `before` (for a wait the run did not do)."""
        for event in self.events[self.cursor :]:
            if "act" in event:
                return False
            if "read" in event and self.screens[event["read"]].fingerprint() != before:
                return True
        return False

    def waited(self, kind: str, before: str | None) -> bool:
        if self.cursor < len(self.events) and kind in self.events[self.cursor]:
            self.cursor += 1
            return bool(self.events[self.cursor - 1][kind])
        before = before or (self.current.fingerprint() if self.current else "")
        return self._pending_change(before)

    # ---- the actions

    def _index(self, item: Item) -> int | None:
        if self.current is None:
            return None
        found = next((n for n, i in enumerate(self.current.items) if i is item), None)
        return found if found is not None else next((n for n, i in enumerate(self.current.items) if i == item), None)

    def act(self, tool: str, item: Item | None = None, text: str = "", detail: str = "") -> None:
        mine: dict[str, Any] = {"tool": tool}
        if item is not None:
            mine["target"] = item_json(item)
            mine["target_index"] = self._index(item)
        if text:
            mine["text"] = _cut(text)
        if detail:
            mine["detail"] = _cut(detail)
        while self.cursor < len(self.events) and "act" not in self.events[self.cursor]:
            if "read" in self.events[self.cursor]:
                self._show(self.events[self.cursor]["read"])  # read live but not now: the screen it acted on
            self.cursor += 1
        line = action_line(mine)
        if self.cursor >= len(self.events):
            self.ran.append(line)
            raise ReplayDivergence(self.diff(f"an action the recorded run never took: {line}"))
        k = self.events[self.cursor]["act"]
        want = self.actions[k]
        why = self._differs(mine, want)
        if why:
            self.ran.append(line)
            raise ReplayDivergence(
                self.diff(f"action {k + 1} differs ({why}):\n  ran:             {line}\n  should have run: {self.expected[k]}")
            )
        self.ran.append(self.expected[k])
        self.cursor += 1

    @staticmethod
    def _differs(mine: dict, want: dict) -> str:
        if mine["tool"] != want.get("tool"):
            return "another tool"
        a, b = mine.get("target"), want.get("target")
        if (a is None) != (b is None):
            return "a target on one side only"
        if a and b:
            if (a["name"], a["kind"]) != (b.get("name"), b.get("kind")):
                return "another target"
            if None not in (mine.get("target_index"), want.get("target_index")) and mine["target_index"] != want["target_index"]:
                return f"another item of that name (#{mine['target_index']}, not #{want['target_index']})"
        if not _same_text(mine.get("text", ""), want.get("text", "")):
            return "other text"
        if mine["tool"] != "wait" and mine.get("detail", "") != want.get("detail", ""):
            return "another place"
        return ""

    def diff(self, head: str) -> str:
        ran = "\n".join(f"    {n + 1}. {line}" for n, line in enumerate(self.ran)) or "    (nothing)"
        should = "\n".join(f"    {n + 1}. {line}" for n, line in enumerate(self.expected)) or "    (nothing)"
        return f"replay diverged: {head}\n  ran:\n{ran}\n  should have run:\n{should}"

    # ---- the user

    def confirm(self, question: str) -> bool:
        if not self.confirms:
            raise ReplayDivergence(self.diff(f"asked the user a yes/no question the run never asked: {question!r}"))
        return bool(self.confirms.pop(0).get("answer"))

    def ask(self, question: str) -> str:
        if not self.asks:
            raise ReplayDivergence(self.diff(f"asked the user a question the run never asked: {question!r}"))
        return str(self.asks.pop(0).get("reply", ""))


class ReplayEnv:
    """types.HarnessEnv over a recording: the recorded screens in order, each action checked against the recorded one."""

    def __init__(self, replay: Replay):
        self.replay = replay
        # the change waits the recorded env had (the real Mac has both): the harness takes another path without them
        waits = replay.record.get("waits", ["changed_since", "wait_change"])
        if "changed_since" in waits:
            self.changed_since = self._changed_since = self._changed
        if "wait_change" in waits:
            self.wait_change = self._wait_change

    def screen(self) -> Screen:
        return self.replay.read()

    def _changed(self, before: str, timeout: float) -> bool:
        return self.replay.waited("changed_since", before)

    def _wait_change(self, timeout: float) -> bool:
        return self.replay.waited("wait_change", None)

    def click(self, item: Item) -> None:
        self.replay.act("click", item)

    def set_text(self, item: Item, text: str) -> None:
        self.replay.act("set_text", item, text)

    def type_at_focus(self, text: str) -> None:
        self.replay.act("type_at_focus", text=text)

    def type_keys(self, text: str, item: Item | None = None) -> None:
        self.replay.act("type_keys", text=text)

    def press(self, keys: str) -> None:
        self.replay.act("press", text=keys)

    def open_app(self, app: str) -> None:
        self.replay.act("open_app", detail=app)

    def open_url(self, url: str, browser: str, profile: str | None = None) -> None:
        self.replay.act("open_url", detail=f"{host(url)} in {browser}")

    def focus_tab(self, browser: str, window: int, tab: int) -> None:
        self.replay.act("focus_tab", detail=browser)

    def scroll(self, direction: str) -> None:
        self.replay.act("scroll", text=direction)

    def back(self) -> None:
        self.replay.act("back")

    def wait(self, seconds: float) -> None:
        self.replay.act("wait", detail=f"{seconds:g}")


class ReplayBrain:
    """The recorded brain replies, request by request. It never raises (its stream runs in a thread of its own): a
    request the run never made is noted as a problem and answered with a stop."""

    def __init__(self, replay: Replay):
        self.replay = replay
        vias = {e.get("via") for e in replay.brain}
        if vias != {"json"}:
            self.stream_lines = self._stream_lines
        if "json" in vias:
            self.json = self._json

    def _next(self, system: str) -> dict | None:
        kind = brain_kind(system)
        if not self.replay.brain:
            self.replay.problems.append(f"a {kind} brain request the run never made")
            return None
        entry = self.replay.brain.pop(0)
        if kind == "page" and entry.get("kind") in ("plan", "rescue"):  # a page call stands in for the plan or rescue
            return entry
        if kind == "look" and entry.get("kind") in ("rescue", "page"):  # a look stands in for a rescue
            return entry
        if entry.get("kind") != kind:
            self.replay.problems.append(f"a {kind} brain request where the run made a {entry.get('kind')} one")
            return None
        return entry

    def _stream_lines(self, system: str, user: str) -> Iterator[str]:
        entry = self._next(system)
        if entry is None:
            yield "stop: the replay has no such brain reply"
            return
        if entry.get("via") == "json":
            yield json.dumps(entry.get("reply"))
            return
        yield from entry.get("lines", [])

    def _json(self, system: str, user: str) -> dict:
        entry = self._next(system)
        if entry is None:
            return {"steps": [], "stop": "the replay has no such brain reply"}
        return entry.get("reply") or {}


class ReplayAgent:
    """Laya's agent over the recording: each question gets the recorded probabilities, option by option in order
    (names are the recording's own, so they line up); an option the run did not have gets 0."""

    def __init__(self, replay: Replay):
        self.replay = replay
        temps = replay.record.get("laya_temperatures")
        if temps:
            self.temperature_by_options = dict(temps)

    def system_one(self, state, spec):
        options = list(spec["q"].get("criteria") or {})
        if spec["q"].get("instructions") == PRIVATE_ASK and not any(
            e.get("instructions") == PRIVATE_ASK for e in self.replay.laya
        ):
            # recorded before every prompt's screen was asked about (a mail or messaging page): not one, as it ran
            return {"answers": {"q": {"probabilities": {o: float(PRIVATE_KINDS.get(o) == NOT_PRIVATE) for o in options}}}}
        if not self.replay.laya:
            raise ReplayDivergence(
                self.replay.diff(f"Laya was asked a question the run never asked: {spec['q'].get('instructions')!r}")
            )
        entry = self.replay.laya.pop(0)
        recorded = list(entry.get("options", []))
        probs = list(entry.get("probs", []))
        if len(recorded) == len(options):
            answer = dict(zip(options, probs, strict=True))
        else:
            by_name = dict(zip(recorded, probs, strict=False))
            answer = {o: by_name.get(o, 0.0) for o in options}
        return {"answers": {"q": {"probabilities": answer}}}


def _hashed_vector(text: str, dims: int = 32) -> np.ndarray:
    v = np.zeros(dims)
    for w in WORD.findall(text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % dims] += 1.0
    return v


def replay_embed(replay: Replay):
    """Embeddings whose similarity to the query is the recorded one (all a shortlist uses); a call the run did not
    make gets word-count vectors."""

    def embed(texts):
        texts = list(texts)
        sims = replay.embeds.pop(0) if replay.embeds else None
        if sims is None or len(sims) != len(texts):
            return np.stack([_hashed_vector(t) + 1e-6 for t in texts])
        return np.array([[s, math.sqrt(max(0.0, 1.0 - s * s))] for s in sims])

    return embed


class ReplayMemory:
    """Memory's recorded reads; writes do nothing."""

    notes = None  # the loop makes its own session notes
    EMPTY: ClassVar[dict[str, Any]] = {"know_how": [], "places": [], "bad_elements": set(), "brief": [], "places_named": []}

    def __init__(self, replay: Replay):
        self.reads = list(replay.record.get("memory", []))

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name not in MEMORY_READS:
            return lambda *args, **kwargs: None

        def read(*args):
            entry = next((e for e in self.reads if e.get("call") == name and e.get("args") == list(args)), None)
            if entry is None:
                return self.EMPTY.get(name)
            result = entry.get("result")
            return set(result or []) if name == "bad_elements" else result

        return read


def mac_back(data: dict):
    """The recorded Mac map as a facts.MacMap (or MacFacts when only those were recorded)."""
    from karta.harness.facts import MacMap, Tab

    facts = MacFacts(
        list(data.get("installed_apps", [])),
        {b: list(p) for b, p in (data.get("browsers") or {}).items()},
        list(data.get("folders", [])),
        datetime.fromisoformat(data["now"]) if data.get("now") else datetime(2026, 1, 1, 9, 0),
    )
    if "tabs" not in data:
        return facts
    return MacMap(
        facts=facts,
        default_browser=data.get("default_browser"),
        running=list(data.get("running") or []),
        tabs=[Tab(**t) for t in data.get("tabs") or []],
        signed_in=list(data.get("signed_in") or []),
    )


def real_laya():
    """The real Laya, loaded offline as the benches do; None when it is not on this Mac."""
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from karta.core.matcher import LayaMatcher
        from karta.harness.decide import Laya

        matcher = LayaMatcher()
        return Laya(matcher.agent, matcher.embed)
    except Exception:
        return None


@dataclass
class ReplayResult:
    result: Any  # loop.TaskResult, or None when the replay diverged
    ran: list[str]
    expected: list[str]
    divergence: str | None = None
    problems: list[str] = field(default_factory=list)
    laya: str = "recorded"

    @property
    def done(self) -> bool:
        return bool(self.result is not None and self.result.done)

    @property
    def brain_calls(self) -> int:
        return int(getattr(self.result, "brain_calls", 0) or 0)


@contextlib.contextmanager
def _silent():
    """No speech during a replay: a tell's answer is shown, never said."""
    from karta.harness import loop

    old = getattr(loop, "speak", None)
    if old is not None:
        loop.speak = lambda text: None
    try:
        yield
    finally:
        if old is not None:
            loop.speak = old


def replay(record: dict, laya: str = "recorded") -> ReplayResult:
    """Run the recorded task through loop.run_task again, everything served from `record`. `laya`: "recorded" (the
    recorded answers) or "real" (the model on this Mac, offline; the recorded answers when it cannot load)."""
    from karta.harness.brain import HarnessBrain
    from karta.harness.decide import Laya
    from karta.harness.loop import run_task

    rp = Replay(record)
    used = laya
    model = real_laya() if laya == "real" else None
    if model is None:
        model, used = Laya(ReplayAgent(rp), replay_embed(rp)), "recorded"
    mac = mac_back(record.get("mac") or {})
    refreshed = record.get("tabs_refreshed")

    def refresh(mac_map):
        if refreshed is not None:
            from karta.harness.facts import Tab

            mac_map.tabs = [Tab(**t) for t in refreshed]
        return mac_map

    result, divergence = None, None
    with _silent():
        try:
            result = run_task(
                record.get("task", ""),
                ReplayEnv(rp),
                model,
                HarnessBrain(ReplayBrain(rp), prompt=record.get("prompt", "v1")),  # runs recorded before v2 were v1
                mac,
                ReplayMemory(rp),
                rp.confirm,
                ask=rp.ask,
                refresh=refresh,
            )
        except ReplayDivergence as error:
            divergence = str(error)
    if divergence is None and rp.ran != rp.expected:
        divergence = rp.diff("the run stopped before the recorded actions were all taken")
    return ReplayResult(result, list(rp.ran), list(rp.expected), divergence, list(rp.problems), used)


# ---------------------------------------------------------------- committed cases


def load_case(folder: Path | str) -> tuple[dict, dict]:
    """A committed case: tests/replay/<name>/run.json (the scrubbed recording) and expected.json (its outcome)."""
    folder = Path(folder)
    record = json.loads((folder / "run.json").read_text())
    expected = json.loads((folder / "expected.json").read_text())
    return record, expected


def case_folders(root: Path | str = CASES) -> list[Path]:
    return sorted(p for p in Path(root).iterdir() if (p / "run.json").exists() and (p / "expected.json").exists())


def outcome_problems(expected: dict, out: ReplayResult) -> list[str]:
    """How a replay misses its case's expected outcome: the actions, done or failed honestly, the brain calls."""
    problems = []
    if out.divergence:
        problems.append(out.divergence)
    problems += [f"brain: {p}" for p in out.problems]
    if out.result is None:
        return problems
    if bool(expected.get("done")) != out.done:
        problems.append(
            ("a false done: " if out.done else "not done: ")
            + f"expected done={bool(expected.get('done'))}, got {out.result.reason!r}"
        )
    limit = expected.get("brain_calls")
    if limit is not None and out.brain_calls > int(limit):
        problems.append(f"{out.brain_calls} brain calls, at most {limit} expected")
    for words in expected.get("reason_has", []):
        if words.lower() not in str(out.result.reason).lower():
            problems.append(f"the reason {out.result.reason!r} does not say {words!r}")
    return problems
