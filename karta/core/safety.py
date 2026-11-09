"""What the loop may never do on its own: clicks that quit, close, delete, send or pay need a yes."""

from __future__ import annotations

import re
from collections.abc import Callable

RISKY = re.compile(
    r"\b(quit|close|hide|delete|remove|erase|trash|discard|empty|send|pay|buy|purchase|order|checkout|submit|"
    r"transfer|sign out|log ?out|unsubscribe|uninstall|format|restart|shut ?down|force)\b",
    re.IGNORECASE,
)
# Menu commands that end or hide the app the loop is driving: never offered at all.
NEVER = re.compile(r"(^|> )(quit|hide|services|close all|force quit)\b", re.IGNORECASE)

Confirm = Callable[[str], bool]


def deny(_label: str) -> bool:
    return False


def risky(label: str) -> bool:
    return bool(RISKY.search(label))


def never(label: str) -> bool:
    return bool(NEVER.search(label))


# Shortcuts that quit, close, delete or log out: same rule as risky clicks.
RISKY_KEYS = {"cmd+q", "cmd+w", "cmd+shift+w", "cmd+delete", "cmd+backspace", "cmd+option+escape", "cmd+shift+q"}


def risky_keys(keys: str) -> bool:
    return keys.lower().replace("command", "cmd") in RISKY_KEYS
