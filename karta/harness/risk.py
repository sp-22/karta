"""Risk: which actions need the user's yes. Code rules first (karta.core.safety).

The target is what makes an action risky: clicking "Send" sends, clicking "Sent" opens a folder; pressing return in
a message box sends, in a search box it searches. Laya is never asked what code can tell from the words.
"""

from __future__ import annotations

import re

from karta.core import safety

KINDS = (  # (kind, the words on a control that do it), the first match wins: "Proceed to pay" is a payment
    ("log out", r"log ?out|sign ?out"),
    ("pay", r"pay|pay now|proceed to pay(ment)?|make (a )?payment|confirm payment|transfer|send money"),
    (
        "purchase",
        r"buy|buy now|purchase|place (your )?order|submit order|confirm order|order now|check ?out|proceed to check ?out",
    ),
    ("delete", r"delete|remove|erase|trash|move to (the )?trash|empty (the )?trash|discard|uninstall|delete (for|forever)"),
    ("send", r"send|send now|send message|send reply"),
    ("post", r"post|publish|tweet|share post|post comment"),
    ("comment", r"comment|add comment"),
    ("submit", r"submit"),
    ("quit", r"quit|force quit|shut ?down|restart|format|unsubscribe"),
    ("run", r"run|re-?run|run again|execute|with same command"),  # runs (or runs again) a command, a script, a job
    ("cancel", r"cancel (order|subscription|booking|reservation|membership|plan|account|payment|trip|ride)"),
)
RULES = [(kind, re.compile(rf"\b({words})\b", re.IGNORECASE)) for kind, words in KINDS]
# a control whose WHOLE label is one of these is a way out, not a commit ("Cancel" is safe; "Cancel order", "Cancel
# subscription" and "Preview and publish" are not: a safe word inside a longer label never exempts it)
SAFE = re.compile(
    r"^\W*(don'?t( \w+)?|do not( \w+)?|cancel|undo|save (as )?draft|preview|sent|drafts|not now|close)\W*$", re.IGNORECASE
)
NAVIGATION = re.compile(r"^(trash|bin|deleted( items)?|orders?|your orders|posts|comments|payments?)$", re.IGNORECASE)
ENTER = re.compile(r"\b(return|enter)\b", re.IGNORECASE)
BOXES = (  # a text box where return does something: (kind, the words that name the box)
    ("send", r"message|chat|reply|compose|dm|send|text message|write a message|type a message"),
    ("comment", r"comment|leave a comment|add a comment"),
    ("post", r"post|tweet|what'?s happening|what'?s on your mind|status"),
)
BOX_RULES = [(kind, re.compile(rf"\b({words})\b", re.IGNORECASE)) for kind, words in BOXES]
COMMITS = re.compile(
    r"^\W*(send|post|submit|save|done|reply|publish|ok|okay|confirm|apply|comment|allow|accept|agree"
    r"|continue|next|sign ?in|log ?in)\b",
    re.IGNORECASE,
)  # a control that commits a form or a form's step, or answers, whatever `risky` says: never a guess, never retried

KEY_KINDS = {"cmd+q": "quit", "cmd+shift+q": "log out", "cmd+w": "close", "cmd+shift+w": "close"}
ACTING = ("click", "press", "submit", "answer", "choose", "select", "tap")
QUIET = ("write", "type", "fill", "find", "open_place", "open", "scroll", "wait", "back", "read")


def risky(action: str, target: str = "") -> str | None:
    """The kind of risk ("send", "post", "pay", "delete", "purchase") when doing `action` on `target` would be
    hard to undo, else None.

    `action` is a tool name or a short command ("click", "press cmd+return", "submit"); `target` the item acted on
    ("Proceed to pay", "Type a message"). Typing, finding, opening and scrolling are never risky by themselves; the
    click or key that sends is. Unknown risky words from karta.core.safety still count ("risky")."""
    verb, _, rest = action.strip().partition(" ")
    verb = verb.lower()
    if verb in QUIET:
        return None
    keys = rest if verb == "press" else ""
    if verb == "press" and not keys:
        keys, target = target, ""
    if keys:
        return _key_risk(keys, target)
    if verb == "submit":  # submit = return in the field it names: a message box sends, a search box searches
        return _box_risk(target or rest)
    label = (target or rest).strip().strip("'\"")
    if verb not in ACTING and not label:
        label = action
    if not label or SAFE.fullmatch(label) or NAVIGATION.fullmatch(label):
        return None
    for kind, rule in RULES:
        if rule.search(label):
            return kind
    return "risky" if safety.risky(label) and not re.search(r"\b(close|hide)\b", label, re.IGNORECASE) else None


def _key_risk(keys: str, target: str) -> str | None:
    """A shortcut that quits, closes, deletes or logs out (karta.core.safety), or return in a box that sends."""
    combo = keys.strip().lower().replace("command", "cmd")
    if combo in KEY_KINDS:
        return KEY_KINDS[combo]
    if safety.risky_keys(combo):
        return "delete" if "delete" in combo or "backspace" in combo else "quit"
    return _box_risk(target) if ENTER.search(combo) else None


GENERIC_BOX = re.compile(r"^\W*(focused )?(text ?(field|area|box|view)|edit ?text|input|)\W*$", re.IGNORECASE)


def _box_risk(box: str) -> str | None:
    """Return in a box: a message, comment or post box sends. A box with no real name (unnamed, "text field") could be
    any of them, so it counts as a send and asks: failing closed, not open."""
    if re.search(r"\bsearch\b|\baddress\b|\burl\b|\bfilter\b|\bfind\b", box or "", re.IGNORECASE):
        return None
    if GENERIC_BOX.match(box or ""):
        return "send"
    for kind, rule in BOX_RULES:
        if rule.search(box):
            return kind
    return None


def commits(item) -> bool:
    """A control that commits a form or a form's step, or answers: its window's or sheet's default button (the one
    Return presses, in any language), or one of COMMITS' words on a control that is not the view's own (a toolbar's
    or a date control's next and previous move the view: facts.view_control, by role). Never clicked on a guess, never
    retried."""
    from karta.harness.facts import view_control

    if getattr(item, "default", False):
        return True
    return bool(COMMITS.match(item.name)) and not view_control(item)


# ---------------------------------------------------------------- the key actions a task is asked about up front

KEY_ACTIONS = (  # (kind, the words on a control that do it): asked about before the task starts, never risky by itself
    ("add to cart", r"add to (cart|bag|basket|trolley)"),
    ("star", r"star|unstar|favou?rite"),
    ("follow", r"follow|unfollow|subscribe"),
)
KEY_RULES = [(kind, re.compile(rf"\b({words})\b", re.IGNORECASE)) for kind, words in KEY_ACTIONS]
KEY_RISKS = ("send", "post", "comment", "pay", "purchase", "delete", "run")  # risk kinds that are key actions


def key_action(step: dict, wrote_into: str = "") -> str | None:
    """The key action a plan step takes, the kind the user is asked about before the task starts: send, post, pay or
    buy, add to cart, delete, star or follow, run a command (a delegate step always); None for any other step. `wrote_into`: the field the plan
    wrote into last (a submit sends what is in it: a message box sends, a search box searches)."""
    tool = step.get("tool")
    at = step.get("at")
    named = str((at.get("name") if isinstance(at, dict) else "") or "")
    if tool == "submit":  # a box named as a message, comment or post box; an unnamed one still asks when it sends
        kind = None if GENERIC_BOX.match(wrote_into or "") else risky("submit", wrote_into)
    elif tool in ("click", "answer"):
        label = named or str(step.get("target") or step.get("choice_meaning") or "")
        kind = risky("click", label) or next((k for k, rule in KEY_RULES if rule.search(label)), None)
    elif tool == "reach":
        return "add to cart"
    elif tool == "delegate":  # starts a coding agent on the user's code: always asked
        return "run"
    elif tool == "type":
        text = str(step.get("text") or "")
        lines = [line for line in text.splitlines() if line.strip()]
        return "run" if len(lines) == 1 and text.endswith("\n") else None  # one line and its Return: a command runs
    else:
        return None
    return kind if kind in KEY_RISKS or kind in dict(KEY_ACTIONS) else None
