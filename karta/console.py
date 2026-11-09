"""What Karta shows in the terminal: the banner at start, then one timestamped line per event as it happens.

The same lines (without colour) go to Application Support/karta/logs/karta-<date>.log. Colour only on a terminal,
and never with NO_COLOR set.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

LOGS = Path.home() / "Library" / "Application Support" / "karta" / "logs"
TINT = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
BLUE, DIM, GREEN, RED, AMBER, BOLD, RESET = (
    ("\033[38;5;33m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m") if TINT else ("",) * 7
)

LETTERS = {  # block capitals, six rows each
    "K": ["██╗  ██╗", "██║ ██╔╝", "█████╔╝ ", "██╔═██╗ ", "██║  ██╗", "╚═╝  ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
}
TAGS = {  # event kind -> (tag, colour)
    "planning": ("plan", DIM),
    "plan": ("plan", DIM),
    "step": ("step", ""),
    "pick": ("pick", DIM),
    "retry": ("retry", AMBER),
    "ledger": ("check", ""),
    "rescue": ("rescue", AMBER),
    "move": ("place", AMBER),
    "tell": ("says", BLUE),
    "replay": ("pin", BLUE),
    "pinned": ("pin", GREEN),
    "listening": ("mic", BLUE),
    "transcribing": ("mic", DIM),
    "done": ("done", GREEN),
    "jobs": ("jobs", DIM),  # the live jobs, as the HUD's pills show them
}


def version() -> str:
    try:
        from importlib.metadata import version as installed

        return installed("karta")
    except Exception:  # running from a clone whose environment predates the package name: read pyproject
        import re

        found = re.search(r'^version = "([^"]+)"', (Path(__file__).parents[1] / "pyproject.toml").read_text(), re.M)
        return found[1] if found else "dev"


def banner(mode: str = "") -> None:
    rows = ["  " + "".join(LETTERS[c][r] for c in "KARTA") for r in range(6)]
    print(f"\n{BLUE}{BOLD}" + "\n".join(rows) + RESET)
    line = f"  say a task, and it's done on this Mac · v{version()}" + (f" · {mode}" if mode else "")
    print(f"{DIM}{line}{RESET}\n", flush=True)


def log(tag: str, text: str, colour: str = "") -> None:
    """One line: time, tag, text. Also appended to today's log file."""
    stamp = time.strftime("%H:%M:%S")
    print(f"{DIM}{stamp}{RESET}  {colour}{tag:<7}{RESET}{text}", flush=True)
    write(f"{stamp}  {tag:<7}{text}")


def write(line: str) -> None:
    try:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / f"karta-{time.strftime('%Y-%m-%d')}.log", "a") as out:
            out.write(line + "\n")
    except OSError:
        pass  # a log file must never stop a run


def on_event(event: dict) -> None:
    """The events.subscribe listener: each event worth reading becomes one log line."""
    kind = event.get("kind", "")
    if kind not in TAGS:
        return  # mic levels, idle and the HUD's own events stay off the terminal
    tag, colour = TAGS[kind]
    if kind == "done":
        ok = event.get("ok")
        log(tag, ("✓ " if ok else "✗ ") + str(event.get("reason", "")), GREEN if ok else RED)
    elif kind == "pick":
        p = event.get("p")
        log(
            tag,
            f"{event.get('label', '')!r} ({event.get('how', '')}" + (f", {p:.0%}" if isinstance(p, float) else "") + ")",
            colour,
        )
    elif kind == "retry":
        log(
            tag,
            f"✗ {event.get('wrong', '')!r} was wrong, trying the next ({event.get('attempt', '')}/{event.get('of', '')})",
            colour,
        )
    elif kind == "ledger":
        text = str(event.get("text", ""))
        log(tag, text, GREEN if text.startswith("✓") else RED if text.startswith("✗") else DIM)
    elif kind == "planning":
        log(tag, "asking the brain…", colour)
    elif kind == "listening":
        log(tag, "listening…", colour)
    elif kind == "transcribing":
        log(tag, "transcribing…", colour)
    else:
        log(tag, str(event.get("text") or event.get("why") or ""), colour)
