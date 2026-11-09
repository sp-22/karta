"""Delegate: start Claude Code in a new Terminal window on one of the user's repos, and hear back from it by its own
hooks (harness.md, "Delegate").

`delegate <request> | repo: <path or name>` is a plan step. Code resolves the repo (a path, the folder the session
delegated to last, or a folder of the Mac map's `notable_folders` by name) and it must be a directory holding `.git`.
It is always a key action ("run"), asked up front with the exact folder and request. The launch opens a NEW Terminal
window (`do script` with no `in`: never an existing window or tab) running

    cd <repo> && claude --settings <settings.json> <request>

each part one POSIX-quoted word (`shlex.quote`), the request refused when it holds a control character, is over
MAX_REQUEST characters or starts with "-" (an option, never a prompt). No permission flag is ever passed: the user's
own Claude Code permissions apply. The settings file adds Claude Code's documented `Stop` and `Notification` command
hooks, each appending its JSON input (stdin) to `events.jsonl` in the job's own folder; a background job reads that
file (`watch`), with the Terminal window's existence as the fallback. Karta never types into, closes or kills it.

Everything that reaches the Mac (`osascript`, `ps`) goes through callables a test replaces.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support" / "karta" / "delegate"
MAX_REQUEST = 500  # characters of a request, at most
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")  # a newline, a tab, an escape: never in a request
START_WAIT = 10.0  # seconds for `claude` to show among the new window's processes
POLL_S = 2.0  # seconds between reads of the events file
WINDOW_EVERY_S = 15.0  # seconds between checks that the window is still open
WATCH_S = 8 * 3600.0  # a watch ends after this long
LINE_WORDS = 20  # words of Claude Code's last message said aloud, at most
# Notification types (the hook input's `notification_type`) that mean Claude Code waits for the user; any other
# (a sign-in that worked, a quota note) is not said. A notification with no type counts as waiting.
NEEDS_INPUT = frozenset(["permission_prompt", "idle_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"])


class Refused(ValueError):
    """A request or repo Karta will not start Claude Code with."""


@dataclass
class Launched:
    """One Claude Code session Karta started: where, with what, and how to hear from it."""

    repo: Path
    request: str
    folder: Path  # its own folder: settings.json, events.jsonl
    window: int = 0  # the Terminal window's id
    tty: str = ""

    @property
    def events(self) -> Path:
        return self.folder / "events.jsonl"

    @property
    def name(self) -> str:
        return self.repo.name


# ---------------------------------------------------------------- what is asked and run


def check_request(request: str) -> str | None:
    """Why `request` cannot be started with, or None."""
    text = str(request or "")
    if CONTROL.search(text):
        return "the request holds a control character (a line break, a tab or an escape)"
    text = text.strip()
    if not text:
        return "no request to give Claude Code"
    if len(text) > MAX_REQUEST:
        return f"the request is over {MAX_REQUEST} characters"
    if text.startswith("-"):
        return "the request starts with '-', which Claude Code would read as an option"
    return None


def shown(path: Path, home: Path | None = None) -> str:
    """A path as the user knows it: "~/Desktop/Projects/folio"."""
    home = home or Path.home()
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)


def _key(name: str) -> str:
    return re.sub(r"[\s_.-]+", "", name.lower())


def resolve_repo(
    name: str, folders: list[str] | tuple[str, ...] = (), recent: str = "", home: Path | None = None
) -> tuple[Path | None, str]:
    """The repo a delegate step names: (its absolute path, "") or (None, why not). `name`: a path ("~/x/folio",
    "/Users/a/folio") or a folder's name ("folio"); empty: `recent`, the repo the session delegated to last.
    `folders`: the Mac map's notable folders, home-relative. Only an existing directory holding `.git` is a repo; two
    folders of that name is no answer."""
    home = home or Path.home()
    name = str(name or "").strip().strip("\"'").rstrip("/")
    if not name:
        if not recent:
            return None, "no repo was named"
        name = recent
    if name.startswith("~/"):
        found = [home / name[2:]]
    elif name.startswith("/"):
        found = [Path(name)]
    elif name.startswith("~"):
        return None, f"{name} is not a folder I can read"
    else:
        want = _key(name.split("/")[-1])
        found = [home / f[2:] if f.startswith("~/") else Path(f) for f in folders]
        found = list(dict.fromkeys(p for p in found if _key(p.name) == want))
        if not found:
            return None, f"I don't know a folder called {name}"
    try:
        found = [p.resolve() for p in found]
    except OSError:
        return None, f"I couldn't read {name}"
    repos = [p for p in dict.fromkeys(found) if p.is_dir() and (p / ".git").exists()]
    if len(repos) > 1:
        return None, f"two folders are called {name}: {', '.join(shown(p, home) for p in repos[:2])}"
    if not repos:
        there = [p for p in found if p.is_dir()]
        return None, (f"{shown(there[0], home)} is not a git repository" if there else f"{name} is not a folder")
    return repos[0], ""


def question(repo: Path, request: str) -> str:
    """The up-front question, with the exact folder and request."""
    return f"I'll start Claude Code in {shown(repo)} with: '{request.strip()}'. Go ahead?"


def ahead_part(repo: Path | str, request: str) -> str:
    """Its part of a longer up-front question (tell.ahead_line)."""
    where = shown(repo) if isinstance(repo, Path) else str(repo)
    return f"start Claude Code in {where} with: '{request.strip()}'"


def hooks_settings(events: Path) -> dict:
    """The settings Karta passes with --settings: Claude Code's Stop and Notification hooks, each a command hook that
    appends its JSON input (read on stdin) and a line break to `events` (Claude Code's hooks reference; harness.md, "Delegate")."""
    target = shlex.quote(str(events))
    hook = {"type": "command", "command": f"cat >> {target}; echo >> {target}"}
    return {"hooks": {"Stop": [{"hooks": [hook]}], "Notification": [{"hooks": [dict(hook)]}]}}


def command(repo: Path, settings: Path, request: str) -> str:
    """The one shell line the new window runs: every part one quoted word, so nothing in the request is shell."""
    why = check_request(request)
    if why:
        raise Refused(why)
    return f"cd {shlex.quote(str(repo))} && claude --settings {shlex.quote(str(settings))} {shlex.quote(request.strip())}"


def prepare(repo: Path, request: str, root: Path | None = None) -> tuple[Launched, str]:
    """The job's own folder (mode 700) with its settings file, and the command line to run."""
    root = root or SUPPORT
    folder = root / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True, mode=0o700)
    settings = folder / "settings.json"
    launched = Launched(repo, request.strip(), folder)
    settings.write_text(json.dumps(hooks_settings(launched.events), indent=2))
    launched.events.touch(mode=0o600)
    return launched, command(repo, settings, request)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def applescript(line: str) -> str:
    """Terminal's `do script` with no `in`: always a new window, never one that is open. Returns "windows before,
    windows after, the new tab's window id, its tty"."""
    if CONTROL.search(line):
        raise Refused("a control character in the command")
    return "\n".join(
        [
            'tell application "Terminal"',
            "set before to count of windows",
            f"set t to do script {_applescript_string(line)}",
            "set tt to tty of t",
            "set wid to 0",
            "repeat with w in windows",
            "repeat with x in tabs of w",
            "if tty of x is tt then set wid to id of w",
            "end repeat",
            "end repeat",
            'return (before as text) & "," & ((count of windows) as text) & "," & (wid as text) & "," & tt',
            "end tell",
        ]
    )


def _osascript(script: str) -> str:
    from karta.base import macos

    return macos.osascript(script)


def _ps(tty: str) -> str:
    """The command lines running on a terminal device."""
    device = tty.removeprefix("/dev/")
    return subprocess.run(["ps", "-t", device, "-o", "command="], capture_output=True, text=True, check=False).stdout


def runs_claude(listing: str) -> bool:
    """`claude` among the commands (the native binary, or node running its script)."""
    for line in listing.splitlines():
        words = line.split()
        if any(Path(w).name == "claude" for w in words[:2]):
            return True
    return False


def launch(
    line: str,
    osascript: Callable[[str], str] | None = None,
    ps: Callable[[str], str] | None = None,
    wait: float = START_WAIT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[int, str]:
    """Open the new window running `line` and check it: one more Terminal window than before, and `claude` among its
    processes within `wait` seconds. (window id, tty), else raises Refused with what went wrong."""
    osascript, ps = osascript or _osascript, ps or _ps
    reply = osascript(applescript(line))
    parts = [p.strip() for p in str(reply).split(",")]
    if len(parts) != 4 or not parts[0].isdigit() or not parts[1].isdigit():
        raise Refused(f"Terminal gave no window ({reply!r})")
    before, after, wid, tty = int(parts[0]), int(parts[1]), parts[2], parts[3]
    if after <= before or not wid.isdigit() or int(wid) == 0 or not tty:
        raise Refused("Terminal opened no new window")
    end = clock() + wait
    while True:
        if runs_claude(ps(tty)):
            return int(wid), tty
        if clock() >= end:
            raise Refused("the new Terminal window is open but Claude Code did not start in it (is `claude` installed?)")
        sleep(0.5)


def window_open(window: int, osascript: Callable[[str], str] | None = None) -> bool:
    """The Terminal window is still there (Terminal is never started by asking)."""
    script = (
        'if application "Terminal" is running then\n'
        f'tell application "Terminal" to return (exists window id {int(window)})\n'
        "end if\nreturn false"
    )
    return (osascript or _osascript)(script).strip().lower() == "true"


# ---------------------------------------------------------------- hearing back


def read_events(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """The hook inputs appended to `path` since `offset` (characters), and the new offset. An object still being
    written is left for the next read; a line that is no JSON object is skipped."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], offset
    decoder, out, i = json.JSONDecoder(), [], offset
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            return out, i
        try:
            value, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            nl = text.find("\n", i)
            if nl < 0:
                return out, i  # still being written
            i = nl + 1  # not JSON: skipped
            continue
        if isinstance(value, dict):
            out.append(value)
        i = end


def first_line(message: str, words: int = LINE_WORDS) -> str:
    """The first non-empty line of a message, without markdown marks, at most `words` words."""
    for line in str(message or "").splitlines():
        line = re.sub(r"[*_`#>]+", "", line).strip(" -•\t")
        if line:
            cut = line.split()
            return " ".join(cut[:words]) + (" …" if len(cut) > words else "")
    return ""


def heard(event: dict, name: str) -> tuple[str, str]:
    """A hook input as what Karta says: ("needs", line), ("stop", line), or ("", "") when it says nothing."""
    kind = event.get("hook_event_name")
    if kind == "Notification":
        if event.get("notification_type") not in (None, "", *NEEDS_INPUT):
            return "", ""
        return "needs", f"Claude Code in {name} needs you."
    if kind == "Stop":
        said = first_line(str(event.get("last_assistant_message") or ""))
        return "stop", f"Claude Code finished in {name}" + (f": {said}" if said else ".")
    return "", ""


def watch(
    launched: Launched,
    job,
    say: Callable[[str], None],
    keep: Callable[[Launched, str], None] | None = None,
    is_open: Callable[[int], bool] = window_open,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """A background job's work (jobs.Jobs.background): read the hook events until Claude Code stops. A Notification
    that waits for the user is said (`say`); the Stop is the job's end line, and its last message is kept (`keep`) as a
    session ref. The window closed, the job cancelled or WATCH_S passed also end it. Never acts on the session."""
    offset, started, checked = 0, clock(), clock()
    while not job.cancel.is_set():
        events, offset = read_events(launched.events, offset)
        for event in events:
            kind, line = heard(event, launched.name)
            if kind == "needs":
                say(line)
            elif kind == "stop":
                if keep is not None:
                    keep(launched, str(event.get("last_assistant_message") or ""))
                return line
        now = clock()
        if now - checked >= WINDOW_EVERY_S:
            checked = now
            try:
                still = is_open(launched.window)
            except Exception:  # a failed check is no news: keep watching
                still = True
            if not still:
                return f"The Claude Code window in {launched.name} was closed."
        if now - started >= WATCH_S:
            return f"I stopped listening for Claude Code in {launched.name}."
        sleep(POLL_S)
    return ""


def keeper(session) -> Callable[[Launched, str], None]:
    """`watch`'s keep: the session gets the last message as a `text` ref of its own (values in RAM only)."""

    def keep(launched: Launched, message: str) -> None:
        text = message.strip() or "It finished and gave no message."
        session.keep(
            f"Claude Code in {launched.name}: {launched.request}",
            "text",
            f"Claude Code's last message in {launched.name}, {len(text):,} chars",
            shown(launched.repo),
            text,
        )

    return keep
