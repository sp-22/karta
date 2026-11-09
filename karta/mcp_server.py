"""`karta-mcp`: Karta as the hands, eyes and verifier of a coding agent (Claude Code, Cursor, Codex), over MCP (stdio).

The calling agent is the brain: it reads the screen, decides the next action and calls one tool. Karta carries the
action out with its Toolbox (code first, Laya for narrow choices, the same safety gates as a task) and says what
happened and what changed on screen. There is no `task` tool: a whole task needs a planning brain, and the caller
already is one, so it composes the primitives instead of nesting a second LLM inside Karta.

Anything hard to undo (send, pay, delete, ...: karta.harness.risk) never runs on the first call: the tool answers
"needs confirmation" with the exact action, and runs only when called again with confirm=true.
KARTA_MCP_DRY=1: every action says what it would do and does nothing.

    claude mcp add karta -- uv run --project ~/Desktop/Projects/karta --extra mcp karta-mcp
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from urllib.parse import urlparse

from karta.harness import facts as facts_mod
from karta.harness import risk
from karta.harness.decide import SHOWN
from karta.harness.tools import Toolbox, affirms, norm, typed_lines
from karta.harness.types import Item, MacFacts, Outcome, Screen, ToolResult

MAX_ITEMS = 120  # screen(): items listed, so a read stays cheap for the caller
VALUE_CHARS = 60
NAME_CHARS = 80
CHANGES = 12  # items listed as new or gone after an action


def host(url: str | None) -> str:
    """The site's host only, never the path or query."""
    if not url:
        return ""
    return (urlparse(url if "://" in url else "https://" + url).hostname or "").removeprefix("www.")


def _trim(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def item_line(item: Item) -> str:
    """One compact line: kind 'name' = 'value' @region [flags]."""
    line = f"{item.kind} {_trim(item.name, NAME_CHARS)!r}"
    if item.value:
        line += f" = {_trim(item.value, VALUE_CHARS)!r}"
    if item.region:
        line += f" @{item.region}"
    flags = [
        flag
        for flag, on in (
            ("focused", item.focused),
            ("disabled", not item.enabled),
            ("checked", item.checked is True),
            ("unchecked", item.checked is False),
            ("in dialog", item.dialog),
            ("browser ui", item.browser_ui),
            ("chrome", item.chrome),
            ("menu", item.menu),
            ("offscreen", item.offscreen),
        )
        if on
    ]
    return line + (f" [{', '.join(flags)}]" if flags else "")


def screen_text(scr: Screen, limit: int = MAX_ITEMS) -> str:
    """The screen for the caller: a header, then dialogs, the page, chrome and menu commands last, capped."""

    def rank(item: Item) -> int:
        return 0 if item.dialog or item.browser_ui else 3 if item.menu else 2 if item.chrome else 1

    items = sorted(scr.items, key=rank)  # stable: screen order within each group
    head = [f"app: {scr.app}"]
    if scr.window:
        head.append(f"window: {_trim(scr.window, NAME_CHARS)}")
    if host(scr.url):
        head.append(f"site: {host(scr.url)}")
    if scr.profile:
        head.append(f"profile: {scr.profile}")
    shown = items[:limit]
    head.append(f"{len(scr.items)} items" + (f" (first {limit} shown)" if len(items) > limit else ""))
    return "\n".join([" · ".join(head), *(f"{n}. {item_line(i)}" for n, i in enumerate(shown, 1))])


def changes(before: Screen, after: Screen) -> str:
    """What an action changed, in one line: the app, site, window, items new and gone; or "nothing changed"."""
    if before.fingerprint() == after.fingerprint():
        return "nothing changed"
    parts = []
    if before.app != after.app:
        parts.append(f"app {before.app} -> {after.app}")
    if host(before.url) != host(after.url):
        parts.append(f"site {host(before.url) or '-'} -> {host(after.url) or '-'}")
    elif before.url != after.url:
        parts.append(f"a new page on {host(after.url)}")
    if before.window != after.window:
        parts.append("window title changed")
    was = {item_line(i) for i in before.items}
    now = {item_line(i) for i in after.items}
    new = [item_line(i) for i in after.items if item_line(i) not in was]
    gone = [item_line(i) for i in before.items if item_line(i) not in now]
    for word, lines in (("new", new), ("gone", gone)):
        if lines:
            more = f" (+{len(lines) - CHANGES} more)" if len(lines) > CHANGES else ""
            parts.append(f"{word}: " + "; ".join(dict.fromkeys(lines[:CHANGES])) + more)
    return "; ".join(parts) or "the screen changed (focus or layout only)"


def dry() -> bool:
    return os.environ.get("KARTA_MCP_DRY", "").strip().lower() in {"1", "true", "yes"}


class Session:
    """One caller's session: the env, Laya, the Mac map and one Toolbox kept across calls (so a submit knows what was
    typed before it). Every action goes through the Toolbox, so the harness's safety gates apply."""

    def __init__(self, env, laya, facts: MacFacts, memory=None, mac=None, dry_run: bool | None = None):
        self.env, self.laya, self.mac = env, laya, mac
        self.dry = dry() if dry_run is None else dry_run
        self.asked: list[str] = []
        self.allow = False
        self.before = None  # the screen just before the last action: what `check` calls new is new since then
        self.tb = Toolbox("", env, laya, facts, memory, self._confirm, tabs=getattr(mac, "tabs", None))

    def _confirm(self, question: str) -> bool:
        self.asked.append(question)
        return self.allow

    # ---------------------------------------------------------------- reading

    def screen(self) -> str:
        return screen_text(self.env.screen())

    def check(self, expect: str) -> dict:
        """Does the screen show `expect`? Code first (the page's words, a field's name or text), then Laya."""
        scr = self.env.screen()
        if facts_mod.shows(scr, expect, typed_lines(self.tb.echo)):
            if self.before is None:
                return {"shows": True, "why": f"code: {expect!r} is on the page"}
            # words that were there before the last action are no proof it worked: say whether they are new
            fresh = facts_mod.shows_new(scr, self.before, expect, typed_lines(self.tb.echo))
            why = "new since the last action" if fresh else "but it was already there before the last action"
            return {"shows": True, "new": fresh, "why": f"code: {expect!r} is on the page, {why}"}
        want = norm(expect)
        field = next((f for f in scr.fields() if want and (norm(f.name) == want or (f.value and want in norm(f.value)))), None)
        if field is not None:
            return {"shows": True, "why": f"code: the field {field.name!r}" + (f" holds {field.value!r}" if field.value else "")}
        choice = self.laya.outcome(expect, self.tb.without_typing(scr))
        p = choice.ranked[0][1] if choice.ranked else 0.0
        if affirms(choice, SHOWN):
            return {"shows": True, "why": f"Laya: {choice.best} (p={p:.2f})"}
        why = f"Laya: {choice.best} (p={p:.2f})" if choice.ranked else "Laya had no answer"
        return {"shows": False, "why": f"not on the page by code; {why}" + ("" if choice.sure else ", unsure")}

    # ---------------------------------------------------------------- acting

    def act(self, tool: str, args: dict, risk_of: tuple[str, str] | None = None, confirm: bool = False) -> dict:
        """Run one Toolbox tool and report it. `risk_of` = (action, target) checked before anything runs; the Toolbox's
        own gate catches what only shows once the target is found (a vague name that turns out to be "Send")."""
        label = f"{tool} " + json.dumps(args, ensure_ascii=False)
        self.before = self.env.screen()
        kind = risk.risky(*risk_of) if risk_of else None
        if self.dry:
            out = {"outcome": "dry run", "would": label, "detail": self._would(tool, args)}
            return out | ({"risk": kind, "needs": "confirm=true"} if kind and not confirm else {})
        if kind and not confirm:
            return self._needs(kind, f"{risk_of[0]} {risk_of[1]!r}".strip(), label)
        self.tb.task = label
        self.asked, self.allow = [], confirm
        start = len(self.tb.trace)
        before = self.env.screen()
        try:
            result: ToolResult = getattr(self.tb, "tool_" + tool)(**args)
        finally:
            self.allow = False
        if result.outcome is Outcome.REFUSED and self.asked and not confirm:
            kind, _, action = self.asked[-1].rsplit(" (task:", 1)[0].partition(": ")
            return self._needs(kind, action, label)
        after = self.env.screen()
        done = [line.strip().removeprefix("do: ") for line in self.tb.trace[start:] if line.strip().startswith("do: ")]
        return {
            "outcome": result.outcome.value,
            "ok": result.ok,
            "detail": result.detail,
            "did": done,
            "changed": changes(before, after),
        }

    def _needs(self, kind: str, action: str, label: str) -> dict:
        return {
            "outcome": "needs confirmation",
            "risk": kind,
            "action": action,
            "detail": f"{action} is hard to undo ({kind}). Nothing was done. Ask the user, then call again with confirm=true.",
            "retry": label,
        }

    def _would(self, tool: str, args: dict) -> str:
        """Dry run: what the action would act on, read from the screen, never acted on."""
        if tool != "click":
            return "nothing was done (KARTA_MCP_DRY=1)"
        try:
            item, why = self.tb.find_target(args["target"], self.env.screen(), args.get("near", ""))
        except Exception as e:  # a dry run never fails the call
            return f"nothing was done; could not resolve the target: {e}"
        if item is None:
            return f"nothing was done; no target: {why.outcome.value} ({why.detail})"
        kind = risk.risky("click", item.name)
        return f"nothing was done; would click {item_line(item)} ({why.detail})" + (
            f"; needs confirmation: {kind}" if kind else ""
        )

    # ---------------------------------------------------------------- the tools

    def click(self, target: str, near: str = "", confirm: bool = False) -> dict:
        return self.act("click", {"target": target, "near": near}, ("click", target), confirm)

    def type_text(self, text: str, into: str = "") -> dict:
        if into:
            return self.act("write", {"text": text, "where": into})
        return self.act("type", {"text": text})

    def press_submit(self, expect: str = "", confirm: bool = False) -> dict:
        box = self.tb.last_write[0] if self.tb.last_write else ""
        return self.act("submit", {"expect": expect}, ("submit", box), confirm)

    def press(self, keys: str, confirm: bool = False) -> dict:
        box = self.tb.last_write[0] if self.tb.last_write else ""
        return self.act("press", {"keys": keys}, (f"press {keys}", box), confirm)

    def open(self, app_or_url: str) -> dict:
        what = app_or_url.strip()
        site = facts_mod.site_url(what) if "." in what else None
        if self.mac is not None and not self.dry:
            with contextlib.suppress(Exception):  # the tabs change between calls; a failed re-read keeps the old ones
                self.mac = facts_mod.refresh_tabs(self.mac)
                self.tb.tabs = self.mac.tabs
        if site:
            return self.act("open_place", {"what": host(site), "site": site})
        return self.act("open_place", {"what": what})

    def scroll(self, direction: str = "down") -> dict:
        return self.act("scroll", {"direction": "up" if direction.strip().lower() == "up" else "down"})

    def back(self) -> dict:
        return self.act("back", {})


# ---------------------------------------------------------------- the server


def build(session: Session):
    """The MCP server over `session`; every tool answers plain text or JSON."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(
        "karta",
        instructions=(
            "Karta is your hands and eyes on this Mac. You decide; Karta acts, one step at a time, and verifies. "
            "Call screen() to see what is in front, then one action (open, click, type_text, press_submit, scroll, "
            "back, press), and check(expect) to verify. Name controls by their visible words. An action that is hard "
            "to undo (send, pay, delete, ...) answers 'needs confirmation': ask the user, then repeat it with "
            "confirm=true."
        ),
    )

    def run(fn, *args, **kwargs) -> str:
        with contextlib.redirect_stdout(sys.stderr):  # stdout is the MCP channel: no stray print may reach it
            try:
                out = fn(*args, **kwargs)
            except Exception as e:  # a failed action is an answer, never a dead server
                out = {"outcome": "error", "detail": f"{type(e).__name__}: {e}"}
        return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)

    @server.tool()
    def screen() -> str:
        """What is on screen now: the front app, window, site (host only) and up to 120 items, one per line:
        kind 'name' = 'value' @region [flags]. Dialog items come first, menu commands last."""
        return run(session.screen)

    @server.tool()
    def click(target: str, near: str = "", confirm: bool = False) -> str:
        """Click the control named `target` (its visible words; `near`: an item in the same row or card). Returns
        the outcome, what was done and what changed. confirm=true only after the user agreed to a risky click."""
        return run(session.click, target, near, confirm)

    @server.tool()
    def type_text(text: str, into: str = "") -> str:
        """Type `text` into the field named `into` (replacing its text; "address bar" for a browser's), or at the
        keyboard focus when `into` is empty. Does not submit."""
        return run(session.type_text, text, into)

    @server.tool()
    def press_submit(expect: str = "", confirm: bool = False) -> str:
        """Submit what was typed last (its send/save button, else return in that field). `expect`: words that show
        once it worked. A send needs confirm=true after the user agreed."""
        return run(session.press_submit, expect, confirm)

    @server.tool()
    def press(keys: str, confirm: bool = False) -> str:
        """Press a key or shortcut: "escape", "tab", "down", "cmd+k". Quitting or closing shortcuts need confirm=true."""
        return run(session.press, keys, confirm)

    @server.tool()
    def open(app_or_url: str) -> str:
        """Open an app ("Notes") or a site ("github.com", "https://example.com/x"); an open tab on the site is reused."""
        return run(session.open, app_or_url)

    @server.tool()
    def scroll(direction: str = "down") -> str:
        """Scroll the front window "down" or "up"."""
        return run(session.scroll, direction)

    @server.tool()
    def back() -> str:
        """Go back (the browser's or app's back)."""
        return run(session.back)

    @server.tool()
    def check(expect: str) -> str:
        """Does the screen show `expect` (words, a field's name or its text)? {"shows": bool, "why": ...}."""
        return run(session.check, expect)

    return server


def main() -> None:
    """Load the Mac map and Laya once, then serve MCP on stdin/stdout."""
    with contextlib.redirect_stdout(sys.stderr):
        from karta.base import macos

        if not macos.accessibility_trusted():
            raise SystemExit(
                "karta-mcp: the app running it lacks Accessibility permission (System Settings > Privacy & Security)"
            )
        from karta.app import MEMORY
        from karta.core.matcher import LayaMatcher
        from karta.harness import content
        from karta.harness.decide import Laya
        from karta.harness.mac import MacHarnessEnv
        from karta.harness.memory import HarnessMemory, SessionNotes

        memory = HarnessMemory(MEMORY, notes=SessionNotes())
        content.BOOK.store = memory  # a site's learned boilerplate (content.py) is kept with the rest
        mac = facts_mod.mac_map(memory)
        matcher = LayaMatcher()
        session = Session(MacHarnessEnv(), Laya(matcher.agent, matcher.embed), mac.facts, memory, mac)
        print(f"karta-mcp: ready{' (dry run: KARTA_MCP_DRY=1)' if session.dry else ''}", file=sys.stderr)
    build(session).run("stdio")


if __name__ == "__main__":
    main()
