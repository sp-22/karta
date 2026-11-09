"""The page view (docs/harness.md, "Per-page planning"): one screen read as numbered items, for a page call.

`build(screen, task, typed)` -> View. The items of that one read, by structure only (no site or app words):
- dropped: off-screen items, the browser's chrome but its address field, nameless items and nameless layout rows,
  exact repeats (the same kind, name, region and row);
- taken in tiers until CAP: the focused field, a dialog's items, fields, controls, rows, menu commands (the app's own
  menu and the last menu, Help, after the rest), then text, then other interface text; rows and text are the content
  model's content only (content.py: `Item.role_class`), and an item it calls interface that is no control, field or
  menu command (a banner's text, a form's label, a sidebar's row) is listed last, as "interface", still a target; an item read only off the pixels
  (OCR) sits in the row it is drawn in, right after that row's item, and takes its tier;
- shown grouped in that order, numbered in display order: `#n kind 'name' = value [flags] @region (host)`;
- privacy, by code: a field's value shows only when it is the task's text or text Karta typed (else its length); on a
  personal surface (a native app, or a mail, accounts, calendar or messages host) content longer than SHAPE_WORDS
  words shows only as `[N words]` (on such a site its controls too); a run of such rows folds into one line; names
  are cut to NAME_CHARS characters; e-mail addresses are masked unless the task holds them;
- a withheld screen (Screen.withheld: mail or messages without the user's yes) shows its structure only (Structure).

`View.items[n - 1]` is the Item #n of that read. A step keeps `identity(item, screen)` (name, kind, region, its AX
roles, which of its namesakes), never the number; `resolve(at, screen)` finds it again on a later read, or None when
it is gone or no longer one item (then the page is looked at again, never guessed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from karta.harness import content as content_mod
from karta.harness import facts as facts_mod
from karta.harness.types import Item, Screen

CAP = 150  # items shown at most: 150 never dropped a right target on the recordings, 80 sometimes did
NAME_CHARS = 48
SHAPE_WORDS = 4  # personal content longer than this shows as its shape only
FOLD_RUN = 3  # this many shape-only rows in a row fold into one line
ROW_GAP_LINES = 6  # an OCR item sits in the row drawn at most this many of its lines above it
GROUPS = ("focused", "dialog", "fields", "controls", "rows", "menu commands", "text", "interface")
CONTROLS = frozenset({"button", "link", "checkbox", "tab"})
CONTENT = frozenset({"row", "text", "heading", "image", "other"})
PERSONAL_HOSTS = frozenset({"mail", "accounts", "calendar", "messages"})  # a host's first label: a personal surface
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
WITHHELD_NOTE = (
    "Structure only: the user has not let this app's text go to Claude. Controls, fields and labels are named; rows "
    "are numbered from the top (row 1 is the first); their text, senders and subjects are not shown."
)
_CELLS = re.compile(r"\s*(?:,|·|\||\n|\s[-\u2013\u2014]\s)\s*")  # a row's or a window title's parts


@dataclass
class View:
    """One read as the brain sees it: `text`, and `items[n - 1]` the Item shown as #n (`labels[n - 1]` its name as
    shown: the step's target words, never hidden content)."""

    screen: Screen
    text: str
    items: list[Item] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    total: int = 0  # items kept before the cap

    def item(self, n: int) -> Item | None:
        return self.items[n - 1] if 1 <= n <= len(self.items) else None


def personal(screen: Screen) -> bool:
    """A surface whose content is the user's own: a native app, or a mail, accounts, calendar or messages host."""
    host = facts_mod.host_of(screen.url or "")
    return not host or host.split(".")[0] in PERSONAL_HOSTS


class Structure:
    """The one rule for a withheld screen (Screen.withheld), used by every prompt that shows a screen (the plan's
    digest, its v1 lines, the end check, a look's or a page call's numbered view): its structure only.

    - a row: `row N`, numbered from the top among the screen's visible named rows (row 1 the first);
    - a field, a control (button, link, tab, checkbox), a menu command and a heading: its name when it reads as a UI
      label (`label`), else `[N words]`;
    - any other text (a message's body, a sender, a time, an image's name): `[N words]`.
    A field's text is never shown (its length only), nor a window title, a row's neighbours or a link's host."""

    def __init__(self, screen: Screen):
        rows = [i for i in screen.items if i.kind == "row" and _keep(i)]
        self.rows = {id(i): n for n, i in enumerate(rows, 1)}
        self.count = len(rows)
        # the content a UI label never is: a row's parts (a sender, a subject), and the window title's parts of two words
        # or more (an open message's subject; a one-word part, "Inbox", is the folder the window shows)
        window = {_low(c) for c in _CELLS.split(screen.window) if len(c.split()) > 1}
        self.content = {_low(c) for i in rows for c in _CELLS.split(i.name)} | window
        self.content.discard("")

    def label(self, name: str) -> bool:
        """A UI label: at most SHAPE_WORDS words, no e-mail address, and none of a row's or the window title's parts."""
        leaf = name.split(" > ")[-1]
        return 0 < len(leaf.split()) <= SHAPE_WORDS and not EMAIL.search(leaf) and _low(leaf) not in self.content

    def name(self, item: Item) -> str:
        """What a prompt shows for `item`, by the rule above."""
        if id(item) in self.rows:
            return f"row {self.rows[id(item)]}"
        if (item.kind in ("field", "heading", "menu", *CONTROLS) or item.menu) and self.label(item.name):
            return item.name
        return f"[{len(item.name.split())} words]"

    def value(self, item: Item) -> str:
        """A field's text, as its length only; a control's state word as it is."""
        if not item.value:
            return ""
        return f"holds {len(item.value)} chars" if item.kind == "field" else item.value if self.label(item.value) else ""


def _low(text: str) -> str:
    return " ".join(text.split()).lower()


def mask(text: str, task: str) -> str:
    return EMAIL.sub(lambda m: m[0] if m[0].lower() in task.lower() else "<email>", text)


def trim(text: str, n: int = NAME_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def roles_of(item: Item) -> str:
    """The item's AX roles from the window down (`Ref.path`), "" when the env gives none (OCR, a recording)."""
    path = getattr(item.ref, "path", ()) or ()
    return ">".join(str(p[0]) if isinstance(p, tuple) and p else str(p) for p in path)


def _frame(item: Item) -> tuple[float, float, float, float] | None:
    try:
        x, y, w, h = (float(getattr(item.ref, k)) for k in "xywh")
    except (AttributeError, TypeError, ValueError):
        return None
    return (x, y, w, h) if w > 0 and h > 0 else None


def _owner(ocr: Item, pool: list[Item]) -> Item | None:
    """The item read off the accessibility tree whose row an OCR item is drawn in: on its line to its left, else the
    nearest above it in its column within ROW_GAP_LINES of its lines. None without frames."""
    v = _frame(ocr)
    if v is None:
        return None
    best: tuple[float, Item] | None = None
    for item in pool:
        t = _frame(item)
        if t is None or item.source == "ocr" or item.kind == "field":
            continue
        on_line = abs((t[1] + t[3] / 2) - (v[1] + v[3] / 2)) < max(t[3], v[3]) / 2 and t[0] < v[0]
        overlap = min(t[0] + t[2], v[0] + v[2]) - max(t[0], v[0])
        above = t[1] + t[3] / 2 < v[1] and overlap >= 0.5 * min(t[2], v[2])
        gap = 0.0 if on_line else v[1] - (t[1] + t[3])
        if (on_line or above) and gap <= ROW_GAP_LINES * v[3] and (best is None or gap < best[0]):
            best = (gap, item)
    return best[1] if best else None


def _tier(item: Item, ends: set[str]) -> float:
    if item.focused and item.kind == "field":
        return 0
    if item.dialog or item.browser_ui:
        return 1
    if item.kind == "field":
        return 2
    if item.menu or item.kind == "menu":
        return 5.5 if item.name.split(" > ")[0] in ends else 5
    if item.kind in CONTROLS:
        return 3
    if content_mod.interface(item):  # the site's frame, a form's label: no row or text of the page, still a target
        return 7
    if item.kind == "row":
        return 4
    return 6


def _keep(item: Item) -> bool:
    if item.offscreen or not item.name.strip():
        return False
    if item.chrome and item.kind != "field":  # the browser's own controls; its address field stays
        return False
    return not (item.hint and item.kind == "row")  # a nameless layout row


def _shape(item: Item, mine: bool, web: bool) -> str | None:
    """`[N words]` for personal content (and, on a personal site, its controls), else None."""
    words = len(item.name.split())
    if mine and words > SHAPE_WORDS and not item.menu and (item.kind in CONTENT or (web and item.kind in CONTROLS)):
        return f"[{words} words]"
    return None


def _value(item: Item, task: str, typed: set[str]) -> str:
    value = item.value or ""
    if not value or item.kind not in ("field", "checkbox", "button"):
        return ""
    if item.kind == "field" and item.chrome and not item.secret:  # the address field: its host only, never a path
        return f" = {facts_mod.host_of(value)!r}"
    if item.kind == "field" and (item.secret or not _said(value, task, typed)):
        return f" (holds {len(value)} chars)"
    return f" = {trim(mask(value, task), 40)!r}"


def _said(value: str, task: str, typed: set[str]) -> bool:
    v = " ".join(value.split()).lower()
    return bool(v) and (v in task.lower() or any(v in " ".join(t.split()).lower() for t in typed if t))


def build(screen: Screen, task: str = "", typed=(), cap: int = CAP) -> View:
    """The numbered view of `screen` (module doc)."""
    typed = {str(t) for t in typed}
    content_mod.classify(screen)
    tops = [i.name.split(" > ")[0] for i in screen.items if (i.menu or i.kind == "menu") and " > " in i.name]
    tops = list(dict.fromkeys(tops))
    ends = {tops[0], tops[-1]} if tops else set()  # the app's own menu and the last, Help: after the other commands
    seen: set[tuple] = set()
    pool: list[Item] = []
    for item in screen.items:
        if not _keep(item):
            continue
        key = (item.kind, item.name, item.region, item.row)
        if key in seen:
            continue
        seen.add(key)
        pool.append(item)
    rank: dict[int, tuple[float, float]] = {}
    for n, item in enumerate(pool):
        rank[id(item)] = (_tier(item, ends), float(n))
    for n, item in enumerate(pool):  # an OCR item goes with the row it is drawn in, right after that row's item
        if item.source == "ocr":
            owner = _owner(item, pool)
            if owner is not None:
                tier, at = rank[id(owner)]
                rank[id(item)] = (tier, at + 0.001 * (n + 1) / (len(pool) + 1))
    chosen = sorted(pool, key=lambda i: rank[id(i)])[:cap]
    mine, web = personal(screen), bool(screen.url)
    hide = Structure(screen) if screen.withheld else None
    names = [i.name for i in chosen]
    head = [f"app: {screen.app}"]
    if screen.window:
        head.append(f"window: {trim(mask(screen.window, task), 60)}" if not mine and not hide else "window: [its title]")
    if screen.url:
        head.append(f"site: {facts_mod.host_of(screen.url)}")
    head.append(f"{len(chosen)} of {len(pool)} items shown")
    lines, labels, last_group = [" · ".join(head)], [], None
    if hide is not None:
        lines.append(f"{WITHHELD_NOTE} {hide.count} rows.")
    fold: list[tuple[int, str, str]] = []  # a run of shape-only rows: (number, shape, region)

    def flush() -> None:
        if hide is not None and len(fold) >= FOLD_RUN:  # withheld: a row's shape is its number from the top
            lines.append(f"#{fold[0][0]}..#{fold[-1][0]} {len(fold)} rows (row {fold[0][1]} to row {fold[-1][1]})"
                         + (f" @{fold[0][2]}" if fold[0][2] else ""))  # fmt: skip
        elif len(fold) >= FOLD_RUN:
            shapes = sorted({s for _, s, _ in fold}, key=lambda s: int(re.sub(r"\D", "", s) or 0))
            lines.append(f"#{fold[0][0]}..#{fold[-1][0]} {len(fold)} rows, {shapes[0]}" + (f" to {shapes[-1]}" if len(shapes) > 1 else "")
                         + (f" @{fold[0][2]}" if fold[0][2] else ""))  # fmt: skip
        else:
            lines.extend(f"#{n} row {s}" + (f" @{r}" if r else "") for n, s, r in fold)
        fold.clear()

    for n, item in enumerate(chosen, 1):
        group = GROUPS[int(rank[id(item)][0])]
        if hide is not None:  # withheld: structure only (Structure); a row's shape is its number from the top
            label = hide.name(item)
            shape = label.removeprefix("row ") if item.kind == "row" else None if label == item.name else label
            label = trim(label)
        else:
            shape = _shape(item, mine, web)
            label = shape or trim(mask(item.name, task))
        labels.append(label)
        if group != last_group:
            flush()
            lines.append(f"{group}:")
            last_group = group
        plain = item.kind == "row" and shape is not None and item.enabled and not item.focused and item.checked is None
        if plain and item.source != "ocr":
            fold.append((n, shape, item.region))
            continue
        flush()
        lines.append(_line(n, item, label, shape, screen, task, typed, mine, web, names, hide))
    flush()
    return View(screen, "\n".join(lines), list(chosen), labels, len(pool))


def _line(n, item, label, shape, screen, task, typed, mine, web, names, hide=None) -> str:
    folded = "  " if item.source == "ocr" and not item.menu else ""
    kind = "[ocr]" if item.source == "ocr" else item.kind
    value = _value(item, task, typed) if hide is None or item.kind == "field" else ""
    if hide is not None and item.kind != "field" and hide.value(item):
        value = f" = {hide.value(item)!r}"
    out = f"{folded}#{n} {kind} {shape or repr(label)}{value}"
    flags = [f for f, on in (("focused", item.focused), ("disabled", not item.enabled), ("on", item.checked is True),
                             ("off", item.checked is False)) if on]  # fmt: skip
    if flags:
        out += f" [{', '.join(flags)}]"
    if item.region and not item.menu:
        out += f" @{item.region}"
    if hide is not None:  # withheld: no row's neighbours, no link's host
        return out
    if item.kind in CONTROLS and item.row and names.count(item.name) > 1:  # one of several alike: the row it is in
        mate = item.row[0]
        words = len(mate.split())
        out += f" (row: {f'[{words} words]' if mine and words > SHAPE_WORDS else repr(trim(mask(mate, task), 30))})"
    if item.url and screen.url and facts_mod.site_of(item.url) != facts_mod.site_of(screen.url):
        out += f" ({facts_mod.host_of(item.url)})"  # a link to another site: where it leads, beside its cut name
    elif item.url and not screen.url:
        out += f" ({facts_mod.host_of(item.url)})"
    return out


# ---------------------------------------------------------------- what a step keeps of an item, and finding it again


def identity(item: Item, screen: Screen) -> dict:
    """What a step keeps of item #n: its name, kind, region, AX roles and, among items of its name and kind on that
    read, which one (`nth` of `of`). Never the number: it means nothing on another read."""
    same = [i for i in screen.items if i.name == item.name and i.kind == item.kind]
    out: dict = {"name": item.name, "kind": item.kind}
    if item.region:
        out["region"] = item.region
    roles = roles_of(item)
    if roles:
        out["roles"] = roles
    if len(same) > 1:
        out["nth"] = next((k for k, i in enumerate(same) if i is item), 0)
        out["of"] = len(same)
    return out


def resolve(at: dict, screen: Screen) -> Item | None:
    """The item `at` (identity) names on `screen`: the one of that name and kind; of several, the one with its AX roles,
    then its region, then the same one of its namesakes when there are as many as before. None when it is gone or
    could be more than one: the caller looks at the page again, never guesses."""
    if not isinstance(at, dict) or not at.get("name"):
        return None
    same = [i for i in screen.items if i.name == at["name"] and i.kind == at.get("kind", i.kind)]
    if len(same) <= 1:
        return same[0] if same else None
    pool = same
    for key, get in (("roles", roles_of), ("region", lambda i: i.region)):
        if at.get(key):
            narrowed = [i for i in pool if get(i) == at[key]]
            if len(narrowed) == 1:
                return narrowed[0]
            pool = narrowed or pool
    nth, of = at.get("nth"), at.get("of")
    if isinstance(nth, int) and of == len(same) and 0 <= nth < len(same) and any(same[nth] is i for i in pool):
        return same[nth]
    return None


def page_key(screen: Screen) -> tuple:
    """Which page a read is of: the app and its web address (no fragment), else the app and its window's title."""
    if screen.url:
        return (screen.app, screen.profile, screen.url.split("#", 1)[0].rstrip("/"))
    return (screen.app, screen.window)
