"""Typed checks (docs/harness.md, "Typed checks"): a v2 plan's click, answer or submit says the KIND of
change it causes (`expect: new_item`), and code verifies that kind from what changed between the screen just before
the step and the screen after it, by structure the platform guarantees: the address (its site and page), the app and
window, the count of dialogs (facts.dialogs), the focused item, fields' values, the items new or gone since
(facts.new_items), the count of a list's rows, a checkbox's or switch's checked state. Words take part only as a
type's argument, which comes from the task, the plan's own typing, a host or the screen, never as a guess at what the
app will show.

A verdict is one of three: met (with what proved it), nothing changed, or a different change, named exactly as the
kinds that did happen ("expected new_item, but dialog_opened"). A small table lets close kinds pass (COMPATIBLE): the
opening kinds prove each other, except that a dialog or window with no empty field never proves a new item; on a
submit, the form closing and the item it sent appearing prove each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote_plus

from karta.harness import facts as facts_mod
from karta.harness.types import Item, Screen

MET, NOTHING, OTHER = "met", "nothing changed", "a different change"
UNNAMED = "something else changed"  # a change of no kind on the list: maybe still on its way

# the closed list: type -> its argument (True: required, None: optional, False: none)
TYPES: dict[str, bool | None] = {
    "page_on": True,  # another site or app is in front: its host or app
    "item_opened": None,  # another page of the same site, or another view of the same app (optionally holding words)
    "results_for": True,  # a search for these words shows its results
    "new_item": None,  # one more row in the list, or a new item holding the words
    "item_gone": None,  # one row fewer, or the item holding the words is gone
    "field_holds": True,  # a document's own text holds the words (done only)
    "editor_opened": False,  # an empty field to type into appeared or took the focus
    "dialog_opened": False,
    "dialog_closed": False,
    "window_opened": False,
    "list_changed": False,
    "toggled": None,  # a checkbox or switch flipped (optionally the one with that name)
    "focus_moved": False,
    "none": False,  # proves nothing: never a done, never a skip
}
ARG_CHARS = 80
EXPECT = re.compile(r"^([a-z_]+)\s*(?:\((.*)\))?$", re.S)
LIST_KINDS = ("row", "image")  # the items a list or a grid of files is made of
CONTENT_KINDS = ("row", "link", "text", "heading", "image", "button", "other")
MANY = 3  # items new and gone at least this many each: the content was replaced, not a line that flickered


@dataclass(frozen=True)
class Expect:
    kind: str
    arg: str = ""

    def __str__(self) -> str:
        return f"{self.kind}({self.arg})" if self.arg else self.kind


def parse(text: str) -> tuple[Expect | None, str | None]:
    """`type` or `type(arg)`: (Expect, None), or (None, why it is not one)."""
    m = EXPECT.match(str(text or "").strip().strip("`").strip())
    if not m or m[1].lower() not in TYPES:
        return None, f"expect: {str(text).strip()[:60]!r} is not one of {', '.join(TYPES)}"
    kind, arg = m[1].lower(), (m[2] or "").strip().strip("\"'").strip()
    takes = TYPES[kind]
    if takes is True and not arg:
        return None, f"expect: {kind} needs ( ): {kind}(<{'host or app' if kind == 'page_on' else 'text'}>)"
    if takes is False and arg:
        return None, f"expect: {kind} takes nothing in ( )"
    if len(arg) > ARG_CHARS:
        return None, f"expect: keep the words in ( ) under {ARG_CHARS} characters"
    return Expect(kind, arg), None


def of(step: dict) -> Expect | None:
    """A step's typed expectation, or None (a step that checks itself: open_place, find, write, fill, collect)."""
    return parse(step.get("expects") or "")[0] if step.get("expects") else None


# ---------------------------------------------------------------- what changed


def norm(text: str) -> str:
    return " ".join(str(text or "").casefold().replace("\u2019", "'").split()).strip(" .…:")


def _address(url: str | None) -> str:
    """host/path, without the scheme, www, the query or a closing slash."""
    bare = re.sub(r"^[a-z]+://(www\.)?", "", str(url or "").strip().lower())
    return re.split(r"[?#]", bare, maxsplit=1)[0].rstrip("/")


def _full(url: str | None) -> str:
    """The address with its query (a search, an item's id), without the fragment."""
    return str(url or "").strip().split("#", 1)[0].rstrip("/")


def _page(scr: Screen) -> Screen:
    """What the app shows, the browser's own dialogs included; never menu commands or the browser's chrome."""
    return Screen(scr.app, [i for i in scr.items if not i.menu and not i.chrome], scr.url, scr.window, scr.profile)


@dataclass
class Changes:
    """What changed between two screens of one step, by structure."""

    before: Screen
    after: Screen
    new: list[Item]  # page items on `after` that were not on `before`
    gone: list[Item]  # page items on `before` that are not on `after`
    tool: str = ""  # the step's tool ("done" for the end state): a notice going is no proof a form was sent
    link: bool = False  # the step clicked a link (a content link may lead to another site)

    @property
    def same_place(self) -> bool:
        """The same app and profile and, on the web, the same site."""
        b, a = self.before, self.after
        return (b.app, b.profile) == (a.app, a.profile) and facts_mod.site_of(b.url or "") == facts_mod.site_of(a.url or "")

    @property
    def same_view(self) -> bool:
        """The same place and address, and the view stayed: no more went than half of what it showed (or a few), and
        no more came than it showed (a page that loaded or was read afresh is no new item in it)."""
        was = len(_page(self.before).items)
        return (
            self.same_place
            and _address(self.before.url) == _address(self.after.url)
            and len(self.gone) <= max(was // 2, MANY)
            and len(self.new) <= max(was, MANY)
        )

    @property
    def moved(self) -> bool:
        """Another page or view: the address (with its query) or the window title changed."""
        return _full(self.before.url) != _full(self.after.url) or (self.before.window != self.after.window)

    @property
    def dialogs(self) -> tuple[int, int]:
        """The page's own dialogs before and after (a browser's own prompt is the browser asking, not the step)."""
        return facts_mod.dialogs(_own(self.before)), facts_mod.dialogs(_own(self.after))

    @property
    def empty(self) -> bool:
        b, a = self.before, self.after
        return (
            not self.new
            and not self.gone
            and (b.app, b.profile, _full(b.url), b.window, b.windows, b.window_at)
            == (a.app, a.profile, _full(a.url), a.window, a.windows, a.window_at)
        )


def _own(scr: Screen) -> Screen:
    return Screen(scr.app, scr.page(), scr.url, scr.window, scr.profile)


def diff(before: Screen, after: Screen, tool: str = "", link: bool = False) -> Changes:
    pb, pa = _page(before), _page(after)
    return Changes(before, after, facts_mod.new_items(pa, pb), facts_mod.new_items(pb, pa), tool, link)


def _frame(item: Item) -> tuple | None:
    ref = item.ref
    box = tuple(round(float(getattr(ref, k, 0) or 0)) for k in ("x", "y", "w", "h")) if ref is not None else ()
    return box if box and (box[2] or box[3]) else None


def _renamed(c: Changes, item: Item) -> bool:
    """`item` is a list's row that was there already under another name (a row renamed in place): a row went from the
    very spot it is drawn at; with no frames, the count of rows did not change while one went."""
    if item.kind not in LIST_KINDS:
        return False
    spot = _frame(item)
    same = [g for g in c.gone if g.kind == item.kind]
    if spot is not None and any(_frame(g) is not None for g in same):
        return any(_frame(g) == spot for g in same)
    return bool(same) and _count(c.after, (item.kind,)) == _count(c.before, (item.kind,))


def _count(scr: Screen, kinds: tuple[str, ...]) -> int:
    return sum(i.kind in kinds and not i.dialog for i in _page(scr).items)


def _content(items: list[Item]) -> list[Item]:
    return [i for i in items if i.kind in CONTENT_KINDS and not i.dialog and i.name.strip()]


def _shows(items: list[Item], words: str, typed: set[str]) -> bool:
    """`words` in these items (never a field: what a field holds may be our own typing)."""
    held = [i for i in items if i.kind != "field"]
    return bool(held) and facts_mod.shows(Screen("", held), words, typed)


def _typed(words: str, typed: set[str]) -> bool:
    """`words` are text the plan typed this task (a title, a subject): a row holding them is the thing it made."""
    w = norm(words)
    return bool(w) and any(w in norm(t) for t in typed)


def on(scr: Screen, where: str) -> bool:
    """`scr` is on the site (a host: any subdomain) or the app `where` names."""
    w = norm(where)
    if not w:
        return False
    if facts_mod.HOST.fullmatch(w):  # that host or a subdomain of it: mail.example.com is not www.example.com
        want, host = facts_mod.host_of(w), facts_mod.host_of(scr.url or "")
        return bool(host) and (host == want or host.endswith("." + want))
    app = norm(scr.app)
    if app and (app == w or app in w.split() or w in app):
        return True
    host = re.sub(r"\W", "", facts_mod.host_of(scr.url or ""))
    return bool(host) and re.sub(r"\W", "", w) in host


# ---------------------------------------------------------------- each type, by structure: the proof, or None


def page_on(c: Changes, arg: str, typed: set[str]) -> str | None:
    if arg:
        return f"on {arg} now" if on(c.after, arg) and not on(c.before, arg) else None
    if c.same_place or (not c.after.url and c.after.app == c.before.app):
        return None
    return f"page_on({facts_mod.site_of(c.after.url) if c.after.url else c.after.app})"


def item_opened(c: Changes, arg: str, typed: set[str]) -> str | None:
    """Another page of the same site (its address changed) or another view of the same app (its window title
    changed), not a dialog over it; after a click on a link, a page on another site too (a content link leads off)."""
    b, a = c.before, c.after
    off_site = c.link and (a.app, a.profile) == (b.app, b.profile) and bool(re.match(r"^https?://", a.url or "", re.I))
    if not (c.same_place or off_site) or dialog_opened(c, "", typed):
        return None
    if a.url and b.url:
        if _full(a.url) == _full(b.url):
            return None
        how = f"opened {_address(a.url)}"
    elif not a.url and not b.url and a.window and a.window != b.window:
        how = f"the window shows {a.window!r}"
    else:
        return None
    if arg and not (norm(arg) in norm(a.window) or _shows(c.new, arg, typed)):
        return None
    return how


def results_for(c: Changes, arg: str, typed: set[str]) -> str | None:
    """The page moved on (another address or window title) and the search text is in its address, its title or a
    field."""
    a, q = c.after, norm(arg)
    held = (
        q in norm(unquote_plus(a.url or ""))
        or q in norm(a.window)
        or any(i.kind == "field" and q in norm(i.value) for i in a.page())
    )
    if not q or not held or not c.moved:
        return None
    return f"results for {arg!r}" + (f" at {_address(a.url)}" if a.url else "")


def new_item(c: Changes, arg: str, typed: set[str]) -> str | None:
    """With words: a window newly named so (a new document or file), or in the same view a new item (not a field:
    what a field holds may be our own typing) holding them. Without: the same view lists one more row (or file)."""
    a, b = c.after, c.before
    named = norm(arg) in norm(a.window) and norm(arg) not in norm(b.window)
    if arg and a.app == b.app and named and norm(arg) not in norm(unquote_plus(a.url or "")):
        return f"a window named {arg!r}"  # a new document or file named so (a search carries its words in its address)
    if arg:  # never a field (our own typing), never a row that was there already under another name
        hit = next((i for i in c.new if i.kind != "field" and _shows([i], arg, typed) and not _renamed(c, i)), None)
        if hit is not None and c.same_view:
            return f"a new {hit.kind} holding {arg!r}"
        if hit is not None and hit.kind in LIST_KINDS and _same_list(c) and not _shows(c.before.page(), arg, typed):
            return f"a new {hit.kind} holding {arg!r} (the list redrawn)"
        if c.same_place and c.moved and _typed(arg, typed):  # a saved draft or form, in the list that keeps it
            row = next((i for i in c.new if i.kind in LIST_KINDS and _shows([i], arg, typed) and not _renamed(c, i)), None)
            if row is not None:
                return f"a {row.kind} holding {arg!r} where it is listed"
        return None
    if not c.same_view:
        return None
    for kind in LIST_KINDS:  # a list that was there grew (a list that loads in from nothing is a page, not an item)
        was, now = _count(c.before, (kind,)), _count(c.after, (kind,))
        if was and now > was:
            return f"{now - was} more {kind}{'s' if now - was > 1 else ''}"
    return None


def item_gone(c: Changes, arg: str, typed: set[str]) -> str | None:
    """With words: what held them is gone from the view. Without: the view lists a row (or file) fewer."""
    if not c.same_view:
        return None
    if arg:
        b, a = [i for i in c.before.page() if not i.dialog], [i for i in c.after.page() if not i.dialog]
        return f"nothing holds {arg!r} any more" if _shows(b, arg, typed) and not _shows(a, arg, typed) else None
    for kind in LIST_KINDS:
        fewer = _count(c.before, (kind,)) - _count(c.after, (kind,))
        if fewer > 0:
            return f"{fewer} {kind}{'s' if fewer > 1 else ''} fewer"
    return None


def editor_opened(c: Changes, arg: str, typed: set[str]) -> str | None:
    """A field to type into opened: one of a name not there before (an editor, a compose form, a new row's title),
    empty or focused; or an empty one that took the focus."""
    if c.after.app != c.before.app:
        return None
    names = {norm(i.name) for i in c.before.items if i.kind == "field"}
    ready = {norm(i.name) for i in c.before.items if i.kind == "field" and i.focused and not (i.value or "").strip()}
    fresh = [
        i
        for i in c.new
        if i.kind == "field"
        and i.enabled
        and not i.secret
        and (
            (norm(i.name) not in names and (i.focused or not (i.value or "").strip()))
            or (i.focused and not (i.value or "").strip() and norm(i.name) not in ready)
        )
    ]
    return f"a field {fresh[0].name!r} to type into" if fresh else None


def _dialog_keys(scr: Screen, own: bool = True) -> set[tuple[str, str]]:
    """The dialogs' items by name and kind: the page's own, or with the browser's own prompts too."""
    return {(norm(i.name), i.kind) for i in (_own(scr) if own else _page(scr)).items if i.dialog}


def dialog_opened(c: Changes, arg: str, typed: set[str]) -> str | None:
    """One more dialog, sheet, popup or panel: by count, or items in a dialog that none was showing before."""
    was, now = c.dialogs
    if now > was:
        return "a dialog opened"
    old = _dialog_keys(c.before)
    fresh = [i for i in c.new if i.dialog and not i.browser_ui and (norm(i.name), i.kind) not in old]
    return "a dialog opened" if fresh and len(fresh) >= min(2, len([i for i in c.after.page() if i.dialog])) else None


CONTROLS = ("button", "link", "field", "checkbox", "tab")


def dialog_closed(c: Changes, arg: str, typed: set[str]) -> str | None:
    """One dialog fewer, or every item of the dialogs before is gone; a notice that went (its control and more gone,
    no control new: only what it covered shows); or the form that held text is gone (a compose window that closed:
    none of its fields is there any more)."""
    was, now = c.dialogs
    if now < was:
        return "a dialog closed"
    old = _dialog_keys(c.before, own=False)  # the browser's own notice closing counts too
    if old and not old & _dialog_keys(c.after, own=False):
        return "a dialog closed"
    went = len(c.gone) >= 2 and any(i.kind in CONTROLS for i in c.gone) and not any(i.kind in CONTROLS for i in c.new)
    if went and c.tool not in ("submit", "done"):  # a notice dismissed; never proof that a form was sent
        return "a notice closed"
    filled = {norm(f.name) for f in c.before.page() if f.kind == "field" and (f.value or "").strip() and not f.search}
    left = {norm(f.name) for f in c.after.page() if f.kind == "field"}
    if filled and not filled & left and c.after.app == c.before.app:
        return "the form that held the text closed"
    return None


def window_opened(c: Changes, arg: str, typed: set[str]) -> str | None:
    """A new window or tab of the same app: one more of its windows open, or another of its windows in front (the
    focused window drawn elsewhere: a new one opens cascaded, under the same title); an app's window of another title;
    or the browser's own new page (an address that is no web address)."""
    b, a = c.before, c.after
    if (b.app, b.profile) != (a.app, a.profile) or c.dialogs[1] > c.dialogs[0]:
        return None
    if b.windows and a.windows > b.windows:
        return f"a new window ({b.windows} open, now {a.windows})"
    if b.window_at and a.window_at and a.window_at != b.window_at and _full(a.url) == _full(b.url):
        return "another window in front"
    if a.url and a.url != b.url and not re.match(r"^https?://", a.url, re.I):
        return "the browser's own new page"
    if a.url or b.url or not a.window or a.window == b.window:
        return None
    return f"a window {a.window!r}"


def _flips(c: Changes) -> list[Item]:
    """Checkboxes and switches whose checked state flipped (the same name and kind, the other state)."""
    gone = {(norm(i.name), i.kind, i.checked) for i in c.gone if i.checked is not None}
    return [i for i in c.new if i.checked is not None and (norm(i.name), i.kind, not i.checked) in gone]


def _named(arg: str, *names: str) -> bool:
    return not arg or any(norm(arg) in norm(n) or norm(n) in norm(arg) for n in names)


def toggled(c: Changes, arg: str, typed: set[str]) -> str | None:
    """A checkbox or switch flipped; or a toggle button renamed itself in place ("Pin" became "Unpin"), nothing
    opening: one control of its kind gone and one new where it was drawn, the only controls that changed."""
    flips = [i for i in _flips(c) if _named(arg, i.name)]
    if flips:
        return f"{flips[0].name!r} turned {'on' if flips[0].checked else 'off'}"
    if not c.same_view or dialog_opened(c, "", typed):
        return None
    went = [g for g in c.gone if g.kind in ("button", "checkbox")]
    came = [i for i in c.new if i.kind in ("button", "checkbox")]
    if len(went) != 1 or len(came) != 1 or went[0].kind != came[0].kind or not _named(arg, went[0].name, came[0].name):
        return None
    old, new = went[0], came[0]
    spot_old, spot_new = _frame(old), _frame(new)
    in_place = (
        spot_old == spot_new if spot_old and spot_new else norm(old.name) in norm(new.name) or norm(new.name) in norm(old.name)
    )
    return f"{old.name!r} became {new.name!r}" if in_place and norm(old.name) != norm(new.name) else None


def _same_list(c: Changes) -> bool:
    """The same place, address and window: the list may have been redrawn (a view refreshed), not another page."""
    b, a = c.before, c.after
    return c.same_place and _address(b.url) == _address(a.url) and b.window == a.window


def list_changed(c: Changes, arg: str, typed: set[str]) -> str | None:
    """The same place shows other items: rows added or removed, a selection flipped (a tab, a view), or its content
    replaced."""
    if not c.same_place:
        return None
    rows = _count(c.after, LIST_KINDS) - _count(c.before, LIST_KINDS)
    if rows:
        return f"{abs(rows)} {'more' if rows > 0 else 'fewer'} rows"
    if _flips(c):
        return f"{_flips(c)[0].name!r} selected"
    changed = len(_content(c.new)) + len(_content(c.gone))
    return f"{changed} items changed" if changed >= MANY else None


def _focused(scr: Screen) -> set[str]:
    return {norm(i.name) for i in scr.page() if i.focused and i.kind == "field"}


def focus_moved(c: Changes, arg: str, typed: set[str]) -> str | None:
    """A field to type into took the keyboard focus (another control taking it is no step that worked)."""
    now = _focused(c.after) - _focused(c.before)
    return f"the focus is on {sorted(now)[0]!r}" if now else None


def _document(scr: Screen, field: Item) -> bool:
    """The field is a document's own text (a note, a page, a terminal), not one field of a form: a field of several
    lines, in no dialog, and no other field (but a search box) holds text."""
    others = [f for f in scr.page() if f.kind == "field" and (f.value or "").strip() and not f.search and f is not field]
    return field.multiline and not field.dialog and not others


def field_holds(c: Changes, arg: str, typed: set[str]) -> str | None:
    """A document's own text holds the words, newly. A one-line field, or one field of a form, is no proof that the
    thing exists: a form is not saved yet (done: new_item of what it saves)."""
    if not norm(arg):
        return None
    held_before = {norm(f.name) for f in c.before.page() if f.kind == "field" and facts_mod.holds(f.value or "", arg)}
    for f in c.after.page():
        if f.kind != "field" or norm(f.name) in held_before or not _document(c.after, f):
            continue
        shown = facts_mod.text_landed(f, arg, c.before, c.after)  # the one check of typed text, as the write's
        if shown is not None:
            return f"{f.name!r} holds {arg!r}" if shown is f else f"{shown.name!r} in {f.name!r} holds {arg!r}"
    return None


def none(c: Changes, arg: str, typed: set[str]) -> str | None:
    return "none: proves nothing"


CHECKS = {
    "page_on": page_on,
    "item_opened": item_opened,
    "results_for": results_for,
    "new_item": new_item,
    "item_gone": item_gone,
    "field_holds": field_holds,
    "editor_opened": editor_opened,
    "dialog_opened": dialog_opened,
    "dialog_closed": dialog_closed,
    "window_opened": window_opened,
    "list_changed": list_changed,
    "toggled": toggled,
    "focus_moved": focus_moved,
    "none": none,
}
# close kinds that prove each other (only for an expectation without words, which must still show)
COMPATIBLE: dict[str, tuple[str, ...]] = {
    "editor_opened": ("new_item", "dialog_opened", "window_opened"),
    "new_item": ("editor_opened",),  # a dialog or window with no empty field is no new item
    "dialog_opened": ("editor_opened", "window_opened"),
    "window_opened": ("editor_opened", "dialog_opened"),
    "list_changed": ("results_for", "new_item", "item_gone", "item_opened"),
}
SENT = ("dialog_closed", "new_item")  # on a submit: the form went, or what it sent shows
SEEN = (  # how a different change is named, most telling first
    "page_on",
    "item_opened",
    "window_opened",
    "dialog_opened",
    "dialog_closed",
    "editor_opened",
    "new_item",
    "item_gone",
    "toggled",
    "list_changed",
    "focus_moved",
)


@dataclass(frozen=True)
class Verdict:
    status: str  # MET, NOTHING or OTHER
    expect: Expect
    proof: str = ""  # what proved it (met)
    seen: tuple[str, ...] = ()  # the kinds that did happen (a different change)

    @property
    def ok(self) -> bool:
        return self.status == MET

    def why(self) -> str:
        if self.ok:
            return f"{self.expect}: {self.proof}"
        if self.status == NOTHING:
            return f"nothing changed (expected {self.expect})"
        return f"expected {self.expect}, but {', '.join(self.seen)}"


def seen(c: Changes, typed: set[str] = frozenset()) -> tuple[str, ...]:
    """The kinds of change that did happen, named; something always, once anything changed."""
    out = []
    for kind in SEEN:
        proof = CHECKS[kind](c, "", typed)
        if proof:
            out.append(proof if kind == "page_on" else kind + (f" ({proof})" if kind == "toggled" else ""))
    if not out and not c.empty:
        out.append(f"{UNNAMED} ({len(c.new)} new, {len(c.gone)} gone)")
    return tuple(out)


def verify(
    expect: Expect,
    before: Screen,
    after: Screen,
    typed: set[str] = frozenset(),
    tool: str = "",
    sent: str = "",
    link: bool = False,
) -> Verdict:
    """Did the step cause the kind of change it declared? Met, nothing changed, or a different change (named).
    `typed`: text the plan typed (in a field it is ours, never proof); `sent`: on a submit, the text it sends;
    `link`: the step clicked a link."""
    c = diff(before, after, tool, link)
    proof = CHECKS[expect.kind](c, expect.arg, typed)
    if proof:
        return Verdict(MET, expect, proof)
    close = COMPATIBLE.get(expect.kind, ()) if not expect.arg else ()
    if tool == "submit" and expect.kind in SENT:
        close = (*close, *(k for k in SENT if k != expect.kind))
    for kind in close:
        proof = CHECKS[kind](c, sent if kind == "new_item" and tool == "submit" else "", typed)
        if proof:
            return Verdict(MET, expect, f"{proof} ({kind}, close to {expect.kind})")
    if c.empty:
        return Verdict(NOTHING, expect)
    return Verdict(OTHER, expect, seen=seen(c, typed))
