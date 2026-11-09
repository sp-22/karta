"""The outcome contract (docs/harness.md, "The contract"): what a task must achieve, declared once by the
brain on the plan's first line and checked by code at the end. "Done" means the result is right, not that the steps ran.

    contract: list(3, titles)          an answer: N distinct items of a kind, each a content item of that kind
    contract: value(price)             an answer: a value of that kind, found in a content item the answer cites
    contract: comparison(A vs B on price)    both values named, each from a content item of its own row or page, and
                                       the winner said
    contract: decision                 yes or no, and the reason, cited
    contract: summary(this page)       a cited summary of what was read
    contract: created(note named Buy stamps)   an outcome: a new item with that name, or one more in the list's count
    contract: changed(Wi-Fi to off)    the control's own state is the value asked for (its pane open is not enough)
    contract: sent(to a@example.com)   the send's own proof: the form closed, or what it sent is listed
    contract: opened(the latest email) the object's own view is in front (another view, titled as what was opened)
    contract: deleted(old draft)       the item is gone, or one fewer in the list's count

The brain only declares the contract; code checks it (`answer_check` at the tell, `outcome_check` at the end through
loop.py). It is fixed by the task's first plan: a look's or rescue's own `contract:` or `done:` line never changes it,
so nothing later can swap it for something easier. A run with no contract line gets one derived by code from its plan
(`derived`: a question's generic answer, else the plan's done line), which is checked the same way but never learned
from: memory and pins learn only from a run whose declared contract was met (loop.remember, recipes.RecipeBook.after).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote_plus, urlsplit

from karta.harness import content as content_mod
from karta.harness import facts as facts_mod
from karta.harness.types import Item, Screen

ANSWERS = ("list", "value", "comparison", "decision", "summary", "answer")  # "answer": derived, any cited answer
OUTCOMES = ("created", "changed", "sent", "opened", "deleted", "done")  # "done": derived, the plan's done line
DECLARED = ANSWERS[:-1] + OUTCOMES[:-1]  # what a plan may declare
LIST_OF = ("titles", "items", "links", "events", "prices", "names")
VALUE_OF = ("number", "time", "date", "price", "word")
ARG_CHARS = 120
CONTRACT = re.compile(r"^([a-z]+)\s*(?:\((.*)\))?$", re.S)
YES_NO = re.compile(r"^\W*(yes|no)\b", re.I)  # a decision's first word, as the tell is told to start it
TIME = re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?[ap]\.?m\b", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
COUNTED = re.compile(r"(?<![\d:.,/])(\d+)(?![\d:.,/])")  # one whole number, never a time, a date or a decimal
ON = ("on", "true", "yes", "enabled", "checked")  # a switch's state as the contract names it (Karta's own grammar)
OFF = ("off", "false", "no", "disabled", "unchecked")


@dataclass(frozen=True)
class Contract:
    kind: str
    count: int = 0  # list: how many (0: one or more)
    of: str = ""  # list and value: the kind of item or value; comparison: the measure; summary: the source; created:
    # the kind of thing made
    name: str = ""  # created, deleted, opened: the thing; changed: the setting; sent: the recipient
    value: str = ""  # changed: the state asked for
    a: str = ""  # comparison: the two things compared
    b: str = ""
    declared: bool = True  # False: derived by code from a plan with no contract line (never learned from)

    @property
    def answer(self) -> bool:
        return self.kind in ANSWERS

    def __str__(self) -> str:
        k = self.kind
        if k == "list":
            return f"list({self.count}, {self.of})" if self.count else f"list({self.of})"
        if k in ("value", "summary") and self.of:
            return f"{k}({self.of})"
        if k == "comparison":
            return f"comparison({self.a} vs {self.b}" + (f" on {self.of})" if self.of else ")")
        if k == "created":
            inner = f"{self.of} named {self.name}" if self.of and self.name else self.of or self.name
            return f"created({inner})" if inner else k
        if k == "changed":
            return f"changed({self.name}" + (f" to {self.value})" if self.value else ")")
        if k == "sent":
            return f"sent(to {self.name})" if self.name else k
        if k in ("opened", "deleted", "done") and self.name:
            return f"{k}({self.name})"
        return k


def parse(text: str) -> tuple[Contract | None, str | None]:
    """`kind` or `kind(<args>)` of the declared kinds: (Contract, None), or (None, why it is none)."""
    m = CONTRACT.match(" ".join(str(text or "").strip().strip("`").split()))
    if not m or m[1].lower() not in DECLARED:
        return None, f"contract: {str(text).strip()[:60]!r} is not one of {', '.join(DECLARED)}"
    kind, arg = m[1].lower(), (m[2] or "").strip().strip("\"'").strip()
    if len(arg) > ARG_CHARS:
        return None, f"contract: keep the words in ( ) under {ARG_CHARS} characters"
    if kind == "list":
        from karta.harness.recipes import NUMBERS  # "three" is 3: a pinned count is the task's own word

        got = re.fullmatch(r"(?:(\w+)\s*,\s*|(\d+)\s+)?(?:of:?\s*)?([a-z]+)", arg.lower())
        count = (got[1] or got[2] or "0") if got else ""
        count = NUMBERS.get(count, count)
        of = (got[3].rstrip("s") + "s") if got else ""
        if not got or of not in LIST_OF or not count.isdigit():
            return None, f"contract: list(<count>, <one of {', '.join(LIST_OF)}>), not list({arg})"
        return Contract(kind, int(count), of), None
    if kind == "value":
        of = arg.lower()
        if of not in VALUE_OF:
            return None, f"contract: value(<one of {', '.join(VALUE_OF)}>), not value({arg})"
        return Contract(kind, of=of), None
    if kind == "comparison":
        got = re.fullmatch(r"(.+?)\s+(?:vs\.?|versus)\s+(.+?)(?:\s+on\s+(.+))?", arg, re.I)
        if not got:
            return None, "contract: comparison(<A> vs <B> on <measure>)"
        return Contract(kind, of=(got[3] or "").strip(), a=got[1].strip(), b=got[2].strip()), None
    if kind == "decision":
        return Contract(kind, name=arg), None
    if kind == "summary":
        return Contract(kind, of=re.sub(r"^of\s+", "", arg, flags=re.I)), None
    if kind == "created":
        what, _, name = arg.partition(" named ")
        return Contract(kind, of=what.strip(), name=name.strip()), None
    if kind == "changed":
        setting, sep, value = arg.rpartition(" to ")
        if not arg:
            return None, "contract: changed(<the setting> to <the value>)"
        return Contract(kind, name=(setting if sep else arg).strip(), value=value.strip() if sep else ""), None
    if kind == "sent":
        return Contract(kind, name=re.sub(r"^to\s+", "", arg, flags=re.I)), None
    return Contract(kind, name=arg), None  # opened, deleted


def derived(tells: list[dict], task: str, done_shows: str) -> Contract:
    """A run with no contract line: a question (a tell in its plan or a look's, or words ending with a question mark)
    must say an answer (any cited answer); anything else must show its done line. Never learned from."""
    if tells or str(task or "").strip().endswith("?"):
        return Contract("answer", declared=False)
    return Contract("done", name=done_shows, declared=False)


def words_of(c: Contract) -> list[str]:
    """The words a contract carries that the plan may only take from the task, its typing or the screen."""
    return [w for w in (c.name, c.a, c.b) if w] + ([c.of] if c.kind == "created" and c.of else [])


# ---------------------------------------------------------------- answers


def _distinct(items: list) -> list:
    out = []
    for item in items:
        if not any(_same(item, o) for o in out):
            out.append(item)
    return out


def _same(a, b) -> bool:
    if a.url and b.url and a.url.split("#")[0].rstrip("/") == b.url.split("#")[0].rstrip("/"):
        return True
    return content_mod.normal(a.title) == content_mod.normal(b.title) and a.value == b.value


def fits(c: Contract, item) -> bool:
    """A collected item (tools.Collected) is of the kind a list contract asks for, by the role it had on screen
    (content.roles): titles and names a result's own title; events an event read from the calendar or a title; links
    an address of its own; prices a money value; items any content. Never interface (a banner, a label)."""
    from karta.harness.tools import amount_of

    role = getattr(item, "role", "")
    if role == content_mod.INTERFACE:
        return False
    if c.of in ("titles", "names"):
        return role == content_mod.TITLE
    if c.of == "events":
        return role in (content_mod.EVENT, content_mod.TITLE)
    if c.of == "links":
        return bool(item.url) and role != content_mod.CONTROL
    if c.of == "prices":
        return bool(item.value) and amount_of(item.value) is not None
    return role != content_mod.CONTROL


def listed(c: Contract, items: list) -> tuple[list, str]:
    """A list contract's items from what was collected: the distinct ones of its kind, the first `count` when it
    names one. ([], why) when fewer than asked fit."""
    good = _distinct([i for i in items if fits(c, i)])
    want = c.count or 1
    if len(good) < want:
        dropped = len(_distinct(items)) - len(good)
        why = f"only {len(good)} of the {want} {c.of} asked for" + (
            f" ({dropped} collected item(s) are no {c.of[:-1]}: {_kinds(c)})" if dropped else ""
        )
        return [], why
    return good[: c.count] if c.count else good, ""


def _kinds(c: Contract) -> str:
    return {
        "titles": "a result's own title, never an author's or category's link, a counter, a banner or a label",
        "names": "a result's own name, never an owner's link, a counter, a banner or a label",
        "events": "an event, never a form's label",
        "links": "an address of their own",
        "prices": "a price",
    }.get(c.of, "content, never the page's frame")


def _values(kind: str, text: str) -> list[str]:
    """The values of `kind` a text says."""
    from karta.harness.tools import MONEY

    if kind == "price":  # said aloud, a price may lose its symbol ("2,499 rupees"): then its numbers
        return [m[0].strip() for m in MONEY.finditer(text)] or [m[0] for m in NUMBER.finditer(text)]
    if kind == "time":
        return [m[0] for m in TIME.finditer(text)]
    if kind in ("number", "date"):
        return [m[0] for m in NUMBER.finditer(text)]
    return re.findall(r"[^\W\d_]{3,}", text)


def _held(kind: str, value: str, fact: str) -> bool:
    from karta.harness.tools import amount_of

    if kind == "price":
        want = amount_of(value) if amount_of(value) is not None else amount_of(f"${value}")
        return want is not None and any(
            (amount_of(v) if amount_of(v) is not None else amount_of(f"${v}")) == want for v in _values("price", fact)
        )
    if kind == "word":
        return value.casefold() in fact.casefold()
    digits = lambda t: int(re.sub(r"\D", "", t) or -1)  # noqa: E731  "2,430" holds "2430", "09:30" is "9:30"
    return digits(value) >= 0 and any(digits(v) == digits(value) for v in _values(kind, fact) or [fact])


def _side(name: str, items: list) -> list:
    """The collected items that name one side of a comparison by all of its distinctive words."""
    from karta.harness.tools import named_fit

    return [i for i in items if named_fit(name, Item(i.title, value=i.value))]


def answer_check(c: Contract, text: str, items: list, cites: list[int], facts: list[str], question: str = "") -> str:
    """Why the spoken answer does not meet the contract, or "" when it does. `items`: the collected items the facts
    came from (tools.Collected, with their roles); `facts`: the numbered facts the answer was worded from; `cites`: the
    ones it cites (1-based)."""
    from karta.harness.tell import NOT_FOUND

    said = str(text or "").strip()
    if not said or said == NOT_FOUND:
        return "no answer was found in what was read"
    if c.kind in ("answer", "list"):
        return ""  # a list is read out by code from the items that fit (listed)
    content = [i for i in items if getattr(i, "role", "") != content_mod.INTERFACE]
    if not content:
        return "what was read is the page's frame, not its content"
    framed = {n for n, i in enumerate(items, 1) if getattr(i, "role", "") == content_mod.INTERFACE}
    if len(facts) != len(items):  # code's comparison line first: the items' facts follow it
        framed = {n + len(facts) - len(items) for n in framed}
    cited = [facts[n - 1] for n in cites if 1 <= n <= len(facts) and n not in framed]
    cited = cited or [f for n, f in enumerate(facts, 1) if n not in framed]  # a value is never the page's frame
    if c.kind == "value":
        values = _values(c.of, said)
        if not values:
            return f"the answer says no {c.of}"
        if not any(_held(c.of, v, f) for v in values for f in cited):
            return f"the {c.of} it says is in none of the facts it cites"
        return ""
    if c.kind == "decision":
        return "" if YES_NO.match(said) else "the answer does not say yes or no"
    if c.kind == "comparison":
        kind = "price" if _values("price", " ".join(facts)) else "number"
        sides = []
        for name in (c.a, c.b):
            mine = [i for i in _side(name, content) if i.value or _values(kind, i.title)]
            if not mine:
                return f"nothing read is {name!r} with its {c.of or 'value'}"
            sides.append(mine)
        if len({id(x) for x in sides[0]} | {id(x) for x in sides[1]}) < 2:
            return "both sides come from one item"
        for name, mine in zip((c.a, c.b), sides, strict=True):
            own = [v for i in mine for v in _values(kind, f"{i.value} {i.title}")]
            if not any(_held(kind, v, said) for v in own):
                return f"the answer does not say {name!r}'s {c.of or 'value'}"
        from karta.harness.tools import named_fit

        if not any(named_fit(name, Item(said)) for name in (c.a, c.b)):
            return "the answer does not say which one wins"
        return ""
    if c.kind == "summary":
        return "" if cites or len(facts) == 1 else "the summary cites nothing it read"
    return ""


def comparison_line(c: Contract, items: list) -> str:
    """Code's own comparison for the tell's facts: each side's first priced item, the lowest and the highest; "" when
    either side holds no price."""
    from karta.harness.tools import amount_of

    firsts = []
    for name in (c.a, c.b):
        priced = [i for i in _side(name, items) if i.value and amount_of(i.value) is not None]
        if not priced:
            return ""
        firsts.append(priced[0])
    low = min(firsts, key=lambda i: amount_of(i.value))
    high = max(firsts, key=lambda i: amount_of(i.value))
    return f"Compared by code: lowest: {low.title} at {low.value}; highest: {high.title} at {high.value}"


def hint(c: Contract) -> str:
    """What the tell's one wording call is told about the answer's shape (the same call: no extra brain call)."""
    return {
        "value": f"Say the {c.of} itself.",
        "decision": "Start with Yes or No, then the reason.",
        "comparison": f"Say both {c.of or 'values'} ({c.a} and {c.b}) and which one is {'lower' if c.of else 'ahead'}.",
        "summary": "Sum it up.",
    }.get(c.kind, "")


# ---------------------------------------------------------------- outcomes, by structure


def _counters(scr: Screen) -> dict[tuple[str, str], int]:
    """The items whose name holds one whole number (a list's count: "Today, 4 reminders"), by their name with the
    number folded, never a time or a date."""
    out: dict[tuple[str, str], int] = {}
    for i in scr.page():
        nums = COUNTED.findall(i.name)
        if len(nums) == 1 and not TIME.search(i.name):
            out[(COUNTED.sub("#", content_mod.normal(i.name)), i.kind)] = int(nums[0])
    return out


def counted(before: Screen, after: Screen, up: bool = True) -> str:
    """A list's count rose (or fell) between two reads of the same app or site: "'All, 14 reminders' is 15 now"."""
    if (before.app, facts_mod.site_of(before.url or "")) != (after.app, facts_mod.site_of(after.url or "")):
        return ""
    was = _counters(before)
    for key, n in _counters(after).items():
        old = was.get(key)
        if old is not None and (n > old if up else n < old):
            return f"a count went from {old} to {n} ({key[0].replace('#', str(n))!r})"
    return ""


def appeared(refs: list[Screen], now: Screen, name: str) -> str:
    """An item holding `name` is on screen now (never a field: that may be our own typing; the window's title too: a
    new document) where no earlier read of the same app or site showed one: the thing made, wherever it is listed."""
    want = content_mod.normal(name)
    if not want:
        return ""

    def holders(scr: Screen) -> list[str]:
        found = [i.name for i in scr.page() if i.kind != "field" and want in content_mod.normal(i.name)]
        return found + ([scr.window] if want in content_mod.normal(scr.window) else [])

    place = (now.app, facts_mod.site_of(now.url or ""))
    same = [r for r in refs if (r.app, facts_mod.site_of(r.url or "")) == place]
    if not same or any(holders(r) for r in same):
        return ""
    return f"{name!r} is listed now" if holders(now) else ""


def _state_word(value: str) -> bool | None:
    low = value.strip().casefold()
    return True if low in ON else False if low in OFF else None


def _related(setting: str, name: str) -> bool:
    """A control's name shares a distinctive word with the setting the contract names ("Use dark appearance" for "dark
    mode"): the control a step of the plan proved it flipped, named in the app's own words."""
    from karta.harness.recipes import STOP

    words = lambda t: {w for w in re.findall(r"[^\W\d_]{3,}", t.casefold()) if w not in STOP}  # noqa: E731
    return bool(words(setting) & words(name))


def state(scr: Screen, setting: str, value: str, flipped: list[str] = ()) -> str:
    """The control named `setting` holds `value` now: a switch or checkbox in that state ("on", "off"), or a control
    showing the value (a popup's or field's value, a selected option named so). A control a step of the plan flipped
    (`flipped`: its toggled check proved it) and named in words of the setting counts as it; one that renamed itself in
    place ("Pin" became "Unpin": no checked state to read) holds the change. "" when not: the setting's pane open is
    not the setting changed."""
    want = content_mod.normal(setting)
    if not want:
        return ""
    related = {content_mod.normal(n) for n in flipped if _related(setting, n)}
    named = [
        i
        for i in scr.page()
        if i.kind != "text" and (want in content_mod.normal(i.name) or content_mod.normal(i.name) in related)
    ]
    on = _state_word(value) if value else True
    for i in named:
        if i.checked is not None and on is not None and i.checked == on:
            return f"{i.name!r} is {'on' if i.checked else 'off'}"
        if value and on is None and content_mod.normal(value) in content_mod.normal(i.value):
            return f"{i.name!r} shows {value!r}"
    if value and on is None:
        chosen = [i for i in scr.page() if i.checked and content_mod.normal(value) == content_mod.normal(i.name)]
        if chosen and named:
            return f"{chosen[0].name!r} is selected"
    shown = {content_mod.normal(i.name) for i in scr.page()}
    if on is not None and any(r not in shown for r in related) and not any(i.checked is not None for i in named):
        return "the toggle the plan flipped renamed itself in place"
    return ""


def _named(name: str, title: str, named_fit) -> bool:
    """Every distinctive word of `name` is in `title` (tools.named_fit); a name of no distinctive word ("search"), by
    its own words of three letters or more."""
    from karta.harness.recipes import STOP

    if named_fit(name, Item(title)):
        return True
    words = [w for w in re.findall(r"[^\W\d_]{3,}", name.casefold()) if w not in STOP]
    have = set(re.findall(r"[^\W\d_]+", title.casefold()))
    from karta.harness.tools import topic_words

    return bool(words) and not topic_words(name) and all(w in have or w.rstrip("s") in have for w in words)


def opened(start: Screen, now: Screen, name: str, pressed: list[str]) -> str:
    """The object's own view is in front: another view than the start (its address, fragment included, or its window
    title changed, a window or dialog more, or a heading new: a reading pane), titled as what was opened: its window's title, a heading or its address
    (a search's words in its query) that holds the contract's words, or a title that is part of the name of the item
    the task opened (a row's or link's name holds its subject)."""
    heads = {content_mod.normal(i.name) for i in start.page() if i.kind == "heading"}
    moved = (
        (now.app, now.url or "", now.window) != (start.app, start.url or "", start.window)
        or facts_mod.dialogs(now) > facts_mod.dialogs(start)
        or (now.windows or 0) > (start.windows or 0)
        or any(i.kind == "heading" and content_mod.normal(i.name) not in heads for i in now.page())  # a reading pane
    )
    if not moved:
        return ""
    address = " ".join(re.findall(r"[^\W_]+", unquote_plus(urlsplit(now.url or "")._replace(scheme="").geturl())))
    titles = [now.window, *(i.name for i in now.page() if i.kind == "heading" and not i.offscreen), address]
    from karta.harness.tools import named_fit

    for title in (t for t in titles if t):
        if name and _named(name, title, named_fit):
            return "its address shows it" if title is address else f"{title!r} is in front"
        part = content_mod.normal(title)
        if len(part.split()) >= 2 and any(part in content_mod.normal(p) for p in pressed):
            return f"the view of what was opened is in front ({len(part.split())} words of its name)"
    return ""
