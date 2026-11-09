"""Snapshots of real runs, for later data and for replay tests: everything the harness saw and was told.

Opt-in only (`karta --record DIR`): nothing here runs unless asked. `Recorder` wraps the env a run acts through
(app.py's `Shown`); it never reads the screen itself, it keeps each Screen the harness read, so a recorded run
behaves exactly like an unrecorded one. `Recorder.wrap` also wraps what the harness asks besides the screen: the
brain (its replies, line by line), Laya (each question's options and probabilities, and each embedding's
similarities), memory's reads, the user's answers, and the Mac map. At the end of the task `finish` writes one JSON
file per task into DIR:

    {"version": 2, "task": ..., "done": ..., "reason": ..., "ledger": [...], "steps": [...],
     "actions": [{"tool": "click", "target": {...}, "target_index": 12, "text": "", "screen": {...}, "fresh": true,
                  "t": 3.2}, ...],
     "final_screen": {...},
     "screens": [{...}, ...],                       every distinct screen read, once
     "events": [{"read": 0}, {"act": 0}, {"wait_change": true}, ...],   reads, actions and waits, in order
     "brain": [{"kind": "plan", "via": "lines", "lines": [...],
                "context": {"memory": [...], "profile": [...], "pinned": [...]}}, ...],   what the request was given
     "laya": [{"instructions": ..., "values": {"wanted": ...}, "read": 3, "options": [...], "probs": [...]}, ...],
     "laya_temperatures": {...},                   values: the question's placeholders filled in; read: the screen
                                                    it was asked against (an index into screens), null before any
     "embeds": [[1.0, 0.31, ...], ...],             each text's cosine similarity to the first (the query)
     "memory": [{"call": "place_for", "args": [...], "result": ...}, ...],
     "confirms": [{"question": ..., "answer": true}], "asks": [{"question": ..., "reply": ...}],
     "mac": {...}, "tabs_refreshed": [...] | null, "waits": ["changed_since", "wait_change"], "brain_calls": 1,
     "answer": null}                                  waits: the change waits the env had (the harness uses them if so)

Each brain request keeps the context it was given (`prompt_context`): memory's block, the profile lines and the pinned
templates, read off the request as the brain got it, each line cut the same way; the user's own details (an e-mail
address or name) are kept as their key only. A replay ignores it.

A screen keeps each item's name, kind, region, row and value (cut to 80 characters), its flags and a link's address;
a web address keeps its host and path, never the query or fragment (they carry search terms, ids and tokens). A Laya
question's placeholder values keep each line cut the same way, and any web address in them without its query. The
files stay on this Mac (default: SNAPSHOTS); they are never part of the repo as they are: scripts/promote_run.py
scrubs one into a replay test (karta/harness/replay.py). scripts/next_step_bench.py reads them back as cases
(karta.harness.next_step.load_recorded).
"""

from __future__ import annotations

import copy
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from karta.harness.types import Item, MacFacts, Screen

SNAPSHOTS = Path.home() / "Library" / "Application Support" / "karta" / "snapshots"
VERSION = 2
CUT = 80  # characters kept of any name, value, row mate or typed text
ITEM_KEYS = (
    "name", "kind", "region", "row", "value", "focused", "enabled", "dialog", "chrome", "browser_ui",
    "checked", "offscreen", "menu", "source", "url", "multiline", "hint", "panel", "cancels", "secret", "role_class",
)  # fmt: skip
ACTIONS = (
    "click",
    "set_text",
    "type_at_focus",
    "type_keys",
    "press",
    "open_app",
    "open_url",
    "focus_tab",
    "scroll",
    "back",
    "wait",
)
WAITS = ("changed_since", "_changed_since", "wait_change")  # the env's own change waits: their answers are kept
# memory's reads a recording keeps; never the profile (a brain request's context keeps its lines, own details as keys)
MEMORY_READS = ("place_for", "alias_for", "element_for", "bad_elements", "know_how", "places", "best", "brief", "places_named")


URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"]+", re.I)
OWN_DETAIL = re.compile(r"^- (email|name): .*$")  # a profile line with the user's own detail: kept as its key only


def _cut(text: Any) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= CUT else text[: CUT - 1] + "…"


def host(url: str | None) -> str | None:
    """Only the host of a URL: no path, query or fragment (they can carry search terms, ids and tokens)."""
    if not url:
        return None
    return (
        re.sub(r"^[a-z][a-z0-9+.-]*://", "", url.strip(), flags=re.I).split("/", 1)[0].split("?", 1)[0].split("#", 1)[0] or None
    )


def address(url: str | None) -> str | None:
    """A web address without its query or fragment: scheme, host and path (a checkpoint can be part of the path)."""
    if not url:
        return None
    return url.strip().split("#", 1)[0].split("?", 1)[0] or None


def value_json(value: Any) -> str:
    """A Laya question's placeholder value as a recording keeps it: each line cut, web addresses without query."""
    text = URL.sub(lambda m: address(m[0]) or "", "" if value is None else str(value))
    return "\n".join(_cut(line) for line in text.split("\n"))


def item_json(item: Item) -> dict:
    data = {
        "name": _cut(item.name),
        "kind": item.kind,
        "region": item.region,
        "row": [_cut(r) for r in item.row],
        "value": _cut(item.value),
        "focused": item.focused,
        "enabled": item.enabled,
        "dialog": item.dialog,
        "chrome": item.chrome,
        "browser_ui": item.browser_ui,
    }
    # the rest only when set, so a file stays short
    extra = {
        "checked": item.checked,
        "offscreen": item.offscreen or None,
        "menu": item.menu or None,
        "source": item.source if item.source != "ax" else None,
        "url": address(item.url) or None,
        "multiline": item.multiline or None,
        "hint": item.hint or None,
        "panel": item.panel or None,
        "cancels": item.cancels or None,
        "secret": item.secret or None,
        "role_class": item.role_class or None,  # the content model's label (content.py), as the run read it
    }
    return data | {k: v for k, v in extra.items() if v is not None}


def screen_json(screen: Screen) -> dict:
    return {
        "app": screen.app,
        "url": address(screen.url),
        "window": _cut(screen.window),
        "profile": screen.profile,
        **({"busy": True} if screen.busy else {}),
        **({"windows": screen.windows} if screen.windows else {}),
        **({"window_at": screen.window_at} if screen.window_at else {}),
        "items": [item_json(i) for i in screen.items],
    }


def screen_back(data: dict) -> Screen:
    """A recorded screen as a Screen again (no refs: nothing can act on it). A version 1 screen kept the host only."""
    items = [
        Item(**{k: tuple(v) if k == "row" else v for k, v in raw.items() if k in ITEM_KEYS}) for raw in data.get("items", [])
    ]
    url = data.get("url")
    if url and "://" not in url and not url.startswith(("about:", "chrome:", "file:")):
        url = f"https://{url}/"
    return Screen(
        app=data.get("app", ""),
        items=items,
        url=url or None,
        window=data.get("window", ""),
        profile=data.get("profile"),
        busy=bool(data.get("busy")),
        windows=int(data.get("windows") or 0),
        window_at=str(data.get("window_at") or ""),
    )


def _slug(text: str, words: int = 6) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", text.lower())[:words]) or "task"


def _plain(value: Any) -> Any:
    """A value as JSON: sets as sorted lists, tuples as lists, anything else as its text."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return str(value)


def brain_kind(system: str) -> str:
    """Which of the harness's prompts a brain request used: plan, rescue, page, look, end or tell ("other" when none)."""
    from karta.harness import brain, tell

    known = {
        "page": ("PAGE_SYS",),
        "look": ("LOOK_SYS",),
        "plan": ("PLAN_SYS", "PLAN_SYS_JSON", "PLAN_SYS_V2"),
        "rescue": ("RESCUE_SYS", "RESCUE_SYS_JSON", "RESCUE_SYS_V2"),
        "end": ("END_SYS", "END_SYS_JSON"),
    }
    for kind, names in known.items():
        if any(system == getattr(brain, n, None) for n in names):
            return kind
    if system.startswith(getattr(tell, "SYSTEM", "\0")):
        return "tell"
    return "other"


def tab_json(tab) -> dict:
    return {
        "browser": tab.browser,
        "window_index": tab.window_index,
        "tab_index": tab.tab_index,
        "url": address(tab.url) or "",
        "title": _cut(tab.title),
        "profile": tab.profile,
    }


def mac_json(facts) -> dict:
    """The Mac map a task ran with (facts.MacMap or MacFacts): apps, browser profiles, folders, open tabs."""
    inner = facts if isinstance(facts, MacFacts) else getattr(facts, "facts", None)
    if not isinstance(inner, MacFacts):
        return {}
    data: dict[str, Any] = {
        "installed_apps": list(inner.installed_apps),
        "browsers": {b: list(p) for b, p in inner.browsers.items()},
        "folders": list(inner.folders),
        "now": inner.now.isoformat(timespec="minutes"),
    }
    if inner is not facts:
        data |= {
            "default_browser": getattr(facts, "default_browser", None),
            "running": list(getattr(facts, "running", []) or []),
            "tabs": [tab_json(t) for t in getattr(facts, "tabs", []) or []],
            "signed_in": list(getattr(facts, "signed_in", []) or []),
        }
    return data


class _RecordingAgent:
    """Laya's agent with each question noted: its instructions, its placeholders' values (value_json), the screen read
    it was asked against (`read()`, an index into the recording's screens), its options and probabilities."""

    def __init__(self, agent, log: list, read=lambda: None):
        self._agent, self._log, self._read = agent, log, read

    def __getattr__(self, name):
        return getattr(self._agent, name)

    def system_one(self, state, spec):
        reply = self._agent.system_one(state, spec)
        try:
            q = spec["q"]
            options = list(q.get("criteria") or {})
            probs = reply["answers"]["q"]["probabilities"]
            self._log.append(
                {
                    "instructions": q.get("instructions", ""),
                    "values": {str(k): value_json(v) for k, v in (state or {}).items()},
                    "read": self._read(),
                    "options": options,
                    "probs": [round(float(probs.get(o, 0.0)), 6) for o in options],
                }
            )
        except Exception:  # an odd reply is the harness's business, not the recorder's
            pass
        return reply


def _recording_embed(embed, log: list):
    def call(texts):
        vectors = embed(texts)
        try:
            v = np.asarray(vectors, dtype=float)
            v = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)
            log.append([round(float(x), 6) for x in v @ v[0]])
        except Exception:
            pass
        return vectors

    return call


def prompt_context(user: str) -> dict[str, list[str]]:
    """What memory and pinning gave a brain request, read off its message: memory's block ("Known about …" and its
    lines, loop.know_how), the profile ("About the user …" and its lines) and the pinned templates ("Pinned tasks …"
    and their lines), each line cut as a recording cuts lines, the user's own details as their key only."""
    out: dict[str, list[str]] = {"memory": [], "profile": [], "pinned": []}
    block = None
    for line in str(user).splitlines():
        if line.startswith("Known about "):
            block = "memory"
        elif line.startswith("About the user ("):
            block = "profile"
            continue
        elif line.startswith("Pinned tasks ("):
            block = "pinned"
            continue
        elif not line.startswith("- "):
            block = None
        if block == "profile":
            line = OWN_DETAIL.sub(lambda m: f"- {m[1]}: …", line)
        if block:
            out[block].append(value_json(line))
    return out


class _RecordingBrain:
    """The brain with each reply noted line by line, as it streams (a reply cut short is kept as far as it went)."""

    def __init__(self, brain, log: list):
        self._brain, self._log = brain, log
        if callable(getattr(brain, "stream_lines", None)):
            self.stream_lines = self._stream_lines
        if callable(getattr(brain, "json", None)):
            self.json = self._json

    def __getattr__(self, name):
        return getattr(self._brain, name)

    def _stream_lines(self, system: str, user: str):
        entry: dict[str, Any] = {"kind": brain_kind(system), "via": "lines", "lines": [], "context": prompt_context(user)}
        self._log.append(entry)
        for line in self._brain.stream_lines(system, user):
            entry["lines"].append(str(line))
            yield line

    def _json(self, system: str, user: str) -> dict:
        entry: dict[str, Any] = {"kind": brain_kind(system), "via": "json", "reply": None, "context": prompt_context(user)}
        self._log.append(entry)
        reply = self._brain.json(system, user)
        entry["reply"] = _plain(reply)
        return reply


class _RecordingMemory:
    """Memory with every read noted (writes pass straight through: a replay never writes)."""

    def __init__(self, memory, log: list):
        self._memory, self._log = memory, log

    def __getattr__(self, name):
        attr = getattr(self._memory, name)
        if name not in MEMORY_READS or not callable(attr):
            return attr

        def read(*args):
            result = attr(*args)
            self._log.append({"call": name, "args": _plain(list(args)), "result": _plain(result)})
            return result

        return read


class Recorder:
    """Wraps an env: it notes each screen the harness reads and each action before it runs; `finish` writes it.

    `fresh` says whether the harness read that screen after the previous action (False: the screen may be stale)."""

    def __init__(self, env, task: str, folder: Path | str = SNAPSHOTS, clock=time.monotonic):
        self._env, self._clock = env, clock
        self.task, self.folder = task, Path(folder).expanduser()
        self.actions: list[dict] = []
        self.screens: list[dict] = []
        self.events: list[dict] = []
        self.brain_log: list[dict] = []
        self.laya_log: list[dict] = []
        self.embeds: list[list[float]] = []
        self.memory_log: list[dict] = []
        self.confirms: list[dict] = []
        self.asks: list[dict] = []
        self.mac: dict = {}
        self.tabs_refreshed: list[dict] | None = None
        self.laya_temperatures: dict | None = None
        self._screen_ids: dict[str, int] = {}
        self._read: int | None = None  # the screen last read (an index into screens): Laya's questions name it
        self._last: Screen | None = None
        self._fresh = False
        self._t0 = clock()

    def __getattr__(self, name):
        attr = getattr(self._env, name)
        if name in WAITS and callable(attr):

            def waited(*args, **kwargs):
                result = attr(*args, **kwargs)
                self.events.append({"wait_change" if name == "wait_change" else "changed_since": bool(result)})
                return result

            return waited
        return attr

    # ---------------------------------------------------------------- what else the harness asks

    def wrap(self, *, laya=None, brain=None, facts=None, memory=None, confirm=None, ask=None) -> dict:
        """run_task's arguments with each wrapped so the run is recorded whole: pass the result as keywords."""
        self.mac = mac_json(facts)
        out: dict[str, Any] = {"laya": self._laya(laya), "brain": self._brain(brain), "facts": facts}
        out["memory"] = _RecordingMemory(memory, self.memory_log) if memory is not None else None
        out["confirm"] = self._confirm(confirm) if confirm is not None else None
        out["ask"] = self._ask(ask) if ask is not None else None
        if self.mac.get("tabs") is not None:
            out["refresh"] = self._refresh
        return out

    def _laya(self, laya):
        agent = getattr(laya, "agent", None)
        if laya is None or agent is None:
            return laya
        clone = copy.copy(laya)
        clone.agent = _RecordingAgent(agent, self.laya_log, lambda: self._read)
        temps = getattr(agent, "temperature_by_options", None)
        self.laya_temperatures = {str(k): float(v) for k, v in temps.items()} if isinstance(temps, dict) else None
        if callable(getattr(laya, "embed", None)):
            clone.embed = _recording_embed(laya.embed, self.embeds)
        return clone

    def _brain(self, brain):
        if brain is None:
            return None
        from karta.harness.brain import HarnessBrain

        if isinstance(brain, HarnessBrain):
            return HarnessBrain(_RecordingBrain(brain.brain, self.brain_log))
        return _RecordingBrain(brain, self.brain_log)

    def _confirm(self, confirm):
        def asked(question: str) -> bool:
            answer = bool(confirm(question))
            self.confirms.append({"question": _cut(question), "answer": answer})
            return answer

        return asked

    def _ask(self, ask):
        def asked(question: str) -> str:
            reply = ask(question)
            self.asks.append({"question": _cut(question), "reply": _cut(reply)})
            return reply

        return asked

    def _refresh(self, mac_map):
        from karta.harness import facts as facts_mod

        out = facts_mod.refresh_tabs(mac_map)
        self.tabs_refreshed = [tab_json(t) for t in getattr(out or mac_map, "tabs", []) or []]
        return out

    # ---------------------------------------------------------------- the screen and the actions

    def _screen_id(self, scr: Screen) -> int:
        data = screen_json(scr)
        key = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if key not in self._screen_ids:
            self._screen_ids[key] = len(self.screens)
            self.screens.append(data)
        return self._screen_ids[key]

    def screen(self) -> Screen:
        scr = self._env.screen()
        self._last, self._fresh = scr, True
        n = self._read = self._screen_id(scr)
        if not self.events or self.events[-1] != {"read": n}:
            self.events.append({"read": n})
        return scr

    def _note(self, tool: str, item: Item | None = None, text: str = "", detail: str = "") -> None:
        entry: dict[str, Any] = {"tool": tool, "t": round(self._clock() - self._t0, 3), "fresh": self._fresh}
        if item is not None:
            entry["target"] = item_json(item)
            index = next((n for n, i in enumerate(self._last.items) if i is item), None) if self._last else None
            if index is None and self._last is not None:
                index = next((n for n, i in enumerate(self._last.items) if i == item), None)
            entry["target_index"] = index
        if text:
            entry["text"] = _cut(text)
        if detail:
            entry["detail"] = _cut(detail)
        entry["screen"] = screen_json(self._last) if self._last is not None else None
        self.events.append({"act": len(self.actions)})
        self.actions.append(entry)
        self._fresh = False

    def click(self, item):
        self._note("click", item)
        return self._env.click(item)

    def set_text(self, item, text):
        self._note("set_text", item, text)
        return self._env.set_text(item, text)

    def type_at_focus(self, text):
        self._note("type_at_focus", text=text)
        return self._env.type_at_focus(text)

    def type_keys(self, text, item=None):
        self._note("type_keys", text=text)
        return self._env.type_keys(text) if item is None else self._env.type_keys(text, item)

    def press(self, keys):
        self._note("press", text=keys)
        return self._env.press(keys)

    def click_once(self, item):  # recorded as the click it is: a replay serves it as one
        self._note("click", item)
        return getattr(self._env, "click_once", self._env.click)(item)

    def confirm_or_return(self):  # recorded as the return it is
        self._note("press", text="return")
        either = getattr(self._env, "confirm_or_return", None)
        return either() if callable(either) else self._env.press("return")

    def open_app(self, app):
        self._note("open_app", detail=app)
        return self._env.open_app(app)

    def open_url(self, url, browser, profile=None):
        self._note("open_url", detail=f"{host(url)} in {browser}")
        return self._env.open_url(url, browser, profile)

    def focus_tab(self, browser, window, tab):
        self._note("focus_tab", detail=browser)
        return self._env.focus_tab(browser, window, tab)

    def scroll(self, direction):
        self._note("scroll", text=direction)
        return self._env.scroll(direction)

    def back(self):
        self._note("back")
        return self._env.back()

    def wait(self, seconds):
        self._note("wait", detail=f"{seconds:g}")
        return self._env.wait(seconds)

    # ---------------------------------------------------------------- the file

    def record(self, result=None, reason: str | None = None) -> dict:
        """The file's content: `result` is loop.TaskResult (or None when the run was stopped)."""
        steps = [
            {"tool": s.get("tool"), "outcome": str(s.get("outcome", "")), "detail": _cut(s.get("detail", ""))}
            for s in (getattr(result, "steps", None) or [])
            if isinstance(s, dict)
        ]
        from karta.harness.brain import prompt_version

        return {
            "version": VERSION,
            "prompt": prompt_version(),  # the plan prompt the run used: a replay plans in the same format
            "task": self.task,
            "done": bool(getattr(result, "done", False)),
            "reason": reason if reason is not None else _cut(getattr(result, "reason", "")),
            "ledger": list(getattr(result, "ledger", None) or []),
            "steps": steps,
            "actions": self.actions,
            "final_screen": screen_json(self._last) if self._last is not None and self._fresh else None,
            "screens": self.screens,
            "events": self.events,
            "brain": self.brain_log,
            "laya": self.laya_log,
            "laya_temperatures": self.laya_temperatures,
            "embeds": self.embeds,
            "memory": self.memory_log,
            "confirms": self.confirms,
            "asks": self.asks,
            "mac": self.mac,
            "tabs_refreshed": self.tabs_refreshed,
            "waits": sorted(n for n in ("changed_since", "wait_change") if callable(getattr(self._env, n, None))),
            "brain_calls": getattr(result, "brain_calls", None),
            "answer": getattr(result, "answer", None),
        }

    def finish(self, result=None, reason: str | None = None, now: datetime | None = None) -> Path | None:
        """Write the task's file; a failure to write is reported, never raised (the run itself is what matters)."""
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
            path = self.folder / f"{stamp}-{_slug(self.task)}.json"
            n = 2
            while path.exists():
                path, n = self.folder / f"{stamp}-{_slug(self.task)}-{n}.json", n + 1
            path.write_text(json.dumps(self.record(result, reason), indent=1, ensure_ascii=False) + "\n")
            return path
        except OSError as exc:
            print(f"  (snapshot not saved: {exc})")
            return None
