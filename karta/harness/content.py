"""The content model (docs/harness.md, "Interface and content"): every item of a read is interface or
content, decided once per read, and every reader of content (a collect's candidates, a page's or window's text, the
facts a tell or compose gets, the page view's rows and text) reads that one label.

`classify(screen)` sets `Item.role_class` on each item, by structure only (no site or app words), first rule that
says something wins:
1. interface: a menu command, the browser's chrome or its own popups, a control by kind (field, checkbox, tab, menu),
   an item named only by its tooltip or developer id (`hint`), anything in the page's search landmark;
2. interface: inside a toolbar or menu bar (`Ref.bar`), inside a navigation, banner, footer, sidebar or search
   landmark (`Ref.landmarks`), or, when the page has a main landmark, a web item outside it;
3. interface: inside an editor or form sheet: a popover, sheet, dialog or web form (`Ref.editor`) that holds an
   editable field (the field's own labels, its buttons, its summary);
4. interface: every item of a web read that is mostly the read of the page of its site just before it (`SAME_PAGE`
   of its texts, under a new address): that page still showing, none of it the new page's (`Boilerplate.learn`);
5. content: inside the main list (`main_list`: the biggest container of alike rows in the main region, by the AX
   tree), but a button inside a row that is not the row itself (its "more options");
6. interface: any other button;
7. interface: text seen identically on two different pages of the site (`Boilerplate`, learned from reads);
8. interface: text that is only a control's name (its label, or the control read again off the pixels);
9. interface: a link of the page's own site named in a word or two that is no result's own main link (`main_links`):
   the site's navigation;
10. interface: beside longer content (a text, link or row of three words or more), a text named in a word or two with
   no number, outside the main list: a label, a tab, a category (a link so named is a result's or rule 9's);
11. content: the rest (rows, links, headings, text and images of what the page or app shows, the values in them).

Boilerplate (`Boilerplate`, one per process: `BOOK`): text that shows identically on two or more different pages of
the same site (a web page's exact host, pages told apart by their path; a native app by its window title, numbers
aside) is the site's interface. Kept per site as a small capped set of digests of short normalised strings (never the
text), in RAM and, given a store (HarnessMemory, kind `boilerplate`), on disk; learned by code from each read, with no
brain call. Never learned: the read's own title (its window title's parts), a string of more than BOILER_WORDS words,
a link to the other page (a result named on its list and on its own page), or a pair of reads that are mostly one
page. In RAM only, the digests of the latest two pages' texts of each site are kept, to tell the two apart.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from karta.harness.facts import _path, host_of, site_of
from karta.harness.types import Item, Screen

INTERFACE, CONTENT = "interface", "content"
CONTROL_KINDS = frozenset({"field", "checkbox", "tab", "menu"})  # a control by its kind: never content
# The AX vocabulary the model reads (platform names). A control's subtree carries labels and typed text, never content.
CONTROL_ROLES = frozenset({
    "AXButton", "AXMenuButton", "AXPopUpButton", "AXTextField", "AXTextArea", "AXSearchField", "AXComboBox",
    "AXSecureTextField", "AXCheckBox", "AXRadioButton", "AXSlider", "AXIncrementor",
})  # fmt: skip
FIELD_ROLES = frozenset({"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox", "AXSecureTextField"})
BAR_ROLES = frozenset({"AXToolbar", "AXMenuBar", "AXMenu"})  # the bars around what an app or page shows
EDITOR_ROLES = frozenset({"AXPopover", "AXSheet", "AXDialog", "AXForm"})  # with an editable field: an editor or a form
EDITOR_SUBROLES = frozenset({"AXLandmarkForm", "AXApplicationDialog", "AXApplicationAlertDialog"})
# Landmarks around what a page shows, never in it: the site's navigation, header, footer, sidebar and search.
FRAME_LANDMARKS = frozenset({
    "AXLandmarkNavigation", "AXLandmarkBanner", "AXLandmarkContentInfo", "AXLandmarkComplementary", "AXLandmarkSearch",
})  # fmt: skip
MAIN_LANDMARK = "AXLandmarkMain"
# A main list's container is no page, window or scroll area: those hold the page's header, body and footer as rows.
NOT_A_LIST = frozenset({"AXApplication", "AXWindow", "AXWebArea", "AXScrollArea", "AXSplitGroup"})
MIN_ROWS = 3  # alike rows, at least, that make a list
ROW_WORDS = 12  # a row counts its words up to this: a list of titled results outweighs a sidebar of one-word links
SHORT_WORDS = 2  # named in this few words: a label, a tab, a site's own navigation link
LABEL_KINDS = frozenset({"text", "other"})  # what a label is read as: never a link (a result named in a word) or a row
BOILER_WORDS = 8  # a string of more words is no site's repeated frame: never learned
BOILER_CHARS = 80
SAME_PAGE = 0.8  # a read sharing this share of its strings with the page before still shows that page (tools.OLD_PAGE)
SITE_CAP = 120  # learned strings kept per site (memory.CAPS keeps as many on disk)
SITES = 64  # sites kept in RAM
_CELLS = re.compile("\\s*(?:,|\u00b7|\\||\\n|\\s[-\u2013\u2014]\\s)\\s*")  # a window title's parts


# ---------------------------------------------------------------- strings, sites and pages


def normal(text: str) -> str:
    """A string as the model compares it: case, width, spacing and the punctuation around it aside."""
    s = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("\u2019", "'")
    return " ".join(s.split()).strip(" .,:;!?\u2026\u00b7\u2022|-\u2013\u2014()[]")


def digest(text: str) -> str:
    """What the model keeps of a string: a short digest of its normal form, never the text."""
    return hashlib.sha1(normal(text).encode()).hexdigest()[:16]


def site_key(screen: Screen) -> str:
    """Whose frame a read shows: a web page's exact host (no "www."), else the app."""
    if screen.url and re.match(r"^https?://", screen.url, re.I):
        return host_of(screen.url).split(":")[0]
    return screen.app or ""


def page_key(screen: Screen) -> str:
    """Which page of its site a read shows: a web page's path (no query or fragment), else its window's title with its
    numbers left out (a mailbox's count changes, the window does not)."""
    if screen.url and re.match(r"^https?://", screen.url, re.I):
        return urlsplit(screen.url).path.rstrip("/") or "/"
    return normal(re.sub(r"\d+", "", screen.window))


def _link_page(url: str, site: str) -> str:
    """The page of `site` a link leads to (its path), "" when it leads elsewhere."""
    if not url or host_of(url).split(":")[0] != site:
        return ""
    return urlsplit(url).path.rstrip("/") or "/"


def short(item: Item) -> bool:
    """Named in a word or two, value included: a label, a tab, a menu of the site ("Upcoming", "Images")."""
    return len(f"{item.name} {item.value}".split()) <= SHORT_WORDS


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url or "").path.split("/") if s]


def main_links(links: list[Item], host: str) -> list[Item]:
    """The results' own main links on a list of results, by their addresses: a link of the site whose sub-pages are
    listed beside it (its stars, forks or comments: links below it, each named in a word or two; a titled link below
    it is the result, and it only a way there), never one whose address is above another such link (an owner or a
    category), and only when at least two of them are alike (as deep): a list. A result named in one word ("notes")
    is still a result; one such link alone is the site's own nav."""
    same = [(i, _segments(i.url)) for i in links if i.url and site_of(i.url) == site_of(host)]

    def below(a: list[str], b: list[str]) -> bool:
        return len(a) > len(b) and a[: len(b)] == b

    subs = [[j for j, q in same if below(q, p)] for _, p in same]
    heads = [(i, p) for (i, p), under in zip(same, subs, strict=True) if under and all(short(j) for j in under)]
    mains = [(i, p) for i, p in heads if not any(below(q, p) for _, q in heads)]
    depths = [len(p) for _, p in mains]
    return [i for i, p in mains if depths.count(len(p)) >= 2]


def _strings(screen: Screen) -> dict[str, tuple[frozenset[str], bool]]:
    """A read's texts, as digests, each with the pages of its site its links lead to and whether it may be learned (a
    short string, no part of the read's own title: its window title's parts, a result's name on its own page)."""
    site = site_key(screen)
    title = {normal(p) for p in _CELLS.split(screen.window or "")}
    out: dict[str, tuple[set[str], bool]] = {}
    for item in screen.items:
        if item.menu or item.chrome or item.browser_ui or item.offscreen or item.kind == "field":
            continue
        text = normal(item.name)
        if not text or not any(c.isalpha() for c in text):
            continue
        learnable = text not in title and len(text.split()) <= BOILER_WORDS and len(text) <= BOILER_CHARS
        leads, was = out.get(digest(text), (set(), False))
        out[digest(text)] = (leads | {_link_page(item.url, site)}, was or learnable)
    return {k: (frozenset(v - {""}), ok) for k, (v, ok) in out.items()}


# ---------------------------------------------------------------- boilerplate, learned from reads


@dataclass
class _Read:
    page: str
    names: dict[str, tuple[frozenset[str], bool]] = field(default_factory=dict)


class Boilerplate:
    """Per site: the short strings seen identically on two or more of its pages (module doc). `store`: anything with
    `boilerplate(site) -> set[str]` and `add_boilerplate(site, digests)` (HarnessMemory); None keeps them in RAM."""

    def __init__(self, store: Any = None, cap: int = SITE_CAP, sites: int = SITES):
        self.store = store
        self.cap, self.sites = cap, sites
        self.known: OrderedDict[str, OrderedDict[str, None]] = OrderedDict()
        self.latest: dict[str, _Read] = {}  # the latest page read of each site (the reads of one page merged)
        self.before: dict[str, _Read] = {}  # the page of each site read before that one

    def _site(self, site: str) -> OrderedDict[str, None]:
        if site not in self.known:
            kept: Iterable[str] = ()
            if self.store is not None:
                try:
                    kept = self.store.boilerplate(site)
                except Exception:
                    kept = ()
            self.known[site] = OrderedDict.fromkeys(list(kept)[: self.cap])
            while len(self.known) > self.sites:
                self.known.popitem(last=False)
        self.known.move_to_end(site)
        return self.known[site]

    def knows(self, site: str, text: str) -> bool:
        """`text` is part of `site`'s frame: seen identically on two of its pages."""
        return bool(site) and digest(text) in self._site(site)

    def _keep(self, site: str, digests: set[str]) -> None:
        known = self._site(site)
        fresh = [d for d in digests if d not in known]
        for d in digests:
            known[d] = None
            known.move_to_end(d)
        while len(known) > self.cap:
            known.popitem(last=False)
        if fresh and self.store is not None:
            with contextlib.suppress(Exception):
                self.store.add_boilerplate(site, fresh)

    def learn(self, screen: Screen) -> tuple[set[str], bool]:
        """Learn from one read: the digests it shares with the read of another page of its site just before it, and
        whether the read is mostly that page's (`SAME_PAGE` of its texts: the page before still showing; a web read
        only). The shared short strings are kept for the site, unless the read is mostly that page's or one is a link
        to the other page."""
        site, page = site_key(screen), page_key(screen)
        if not site:
            return set(), False
        names = _strings(screen)
        latest = self.latest.get(site)
        if latest is not None and latest.page != page:
            self.before[site] = latest
            latest = None
        before = self.before.get(site)
        shared: set[str] = set()
        stale = False
        if before is not None and before.page != page and names:
            shared = {d for d in names if d in before.names}
            stale = bool(screen.url) and len(names) >= MIN_ROWS and len(shared) >= SAME_PAGE * len(names)
            if not stale:
                self._keep(site, {
                    d for d in shared
                    if names[d][1] and before.names[d][1] and page not in before.names[d][0] and before.page not in names[d][0]
                })  # fmt: skip
        if stale:
            return shared, True  # the page before still showing: its texts are not this page's
        if latest is None:
            self.latest[site] = _Read(page, names)
        else:
            for d, (leads, ok) in names.items():
                was, ok_was = latest.names.get(d, (frozenset(), False))
                latest.names[d] = (was | leads, ok or ok_was)
        return shared, False


BOOK = Boilerplate()  # one per process: the env's reads learn into it (karta/harness/mac.py); app.py gives it a store


# ---------------------------------------------------------------- structure


def _landmarks(item: Item) -> tuple[str, ...]:
    return tuple(getattr(item.ref, "landmarks", ()) or ())


def _editor(item: Item) -> Any:
    return getattr(item.ref, "editor", None)


def _own_role(item: Item) -> str:
    path = _path(item)
    return str(path[-1][0]) if path and isinstance(path[-1], tuple) and path[-1] else ""


def main_list(items: list[Item]) -> tuple[int, Any] | None:
    """The main list's container, by the AX tree: among the ancestors of `items` (no page, window or scroll area), the
    one whose children of one role (its rows) are at least MIN_ROWS and hold the most words, each row counted up to
    ROW_WORDS; of two as heavy, the deeper. (depth, its key in `Ref.path`), None when no container has such rows."""
    rows: dict[tuple[int, Any], dict[str, dict[Any, int]]] = {}
    for item in items:
        path = _path(item)
        words = len(item.name.split())
        for d in range(len(path) - 1):
            node, child = path[d], path[d + 1]
            if not (isinstance(node, tuple) and isinstance(child, tuple)) or str(node[0]) in NOT_A_LIST:
                continue
            by_role = rows.setdefault((d, node[1]), {}).setdefault(str(child[0]), {})
            by_role[child[1]] = by_role.get(child[1], 0) + words
    best: tuple[int, int] | None = None
    found = None
    for (d, key), by_role in rows.items():
        for children in by_role.values():
            if len(children) < MIN_ROWS:
                continue
            score = (sum(min(w, ROW_WORDS) for w in children.values()), d)
            if best is None or score > best:
                best, found = score, (d, key)
    return found


def _in_list(item: Item, at: tuple[int, Any]) -> tuple[bool, bool]:
    """(the item is inside the main list, the item is one of its rows itself)."""
    path = _path(item)
    d, key = at
    if len(path) <= d + 1 or not isinstance(path[d], tuple) or path[d][1] != key:
        return False, False
    return True, len(path) == d + 2


def node_class(role: str, subrole: str) -> str:
    """One AX node by its own role and subrole, for a reader that walks the tree (ax_walk.page_text), by the same
    rules as `classify`: "field" (an editable field), "interface" (another control, a bar, a landmark around the
    page), "editor" (a popover, sheet, dialog or form: interface when it holds a field), "main" (the page's main
    landmark), else ""."""
    if role in FIELD_ROLES:
        return "field"
    if role in CONTROL_ROLES or role in BAR_ROLES or subrole in FRAME_LANDMARKS:
        return INTERFACE
    if role in EDITOR_ROLES or subrole in EDITOR_SUBROLES:
        return "editor"
    if subrole == MAIN_LANDMARK:
        return "main"
    return ""


def classify(screen: Screen, book: Boilerplate | None = None, learn: bool = False) -> Screen:
    """Set `Item.role_class` on every item of `screen` (module doc), once: a read already classified is returned as it
    is. Set in place (the field takes no part in an item's equality or hash), so every item keeps its identity.
    `learn`: the read also teaches `book` (the env's own reads only, once each)."""
    if screen.items and all(i.role_class for i in screen.items):
        return screen
    book = BOOK if book is None else book
    shared, stale = book.learn(screen) if learn else (set(), False)
    site = site_key(screen)
    items = screen.items
    has_main = any(MAIN_LANDMARK in _landmarks(i) for i in items)
    controls = {normal(i.name) for i in items if i.kind in CONTROL_KINDS | {"button"} and i.source != "ocr" and not i.menu}
    editors = {
        _editor(i) for i in items if i.kind == "field" and i.enabled and not i.search and not i.chrome and _editor(i) is not None
    }
    labels = [_structure(item, has_main, editors, stale) for item in items]
    rest = [i for i, lab in zip(items, labels, strict=True) if not lab]
    at = main_list(rest)
    links = [i for i in rest if i.kind == "link" and i.url]
    mains = {id(i) for i in main_links(links, screen.url or "")} if screen.url else set()
    listed: set[int] = set()
    for n, item in enumerate(items):
        if not labels[n]:
            labels[n] = _by_place(item, at, site, book, shared, controls, mains, screen.url or "", listed)
    if any(
        lab == CONTENT and i.kind in LABEL_KINDS | {"row", "link"} and not short(i) for i, lab in zip(items, labels, strict=True)
    ):
        for n, item in enumerate(items):  # rule 10: beside longer content, a word or two of its own is a label
            if labels[n] == CONTENT and _label(item) and id(item) not in listed and id(item) not in mains:
                labels[n] = INTERFACE
    for item, label in zip(items, labels, strict=True):
        object.__setattr__(item, "role_class", label)
    return screen


def _label(item: Item) -> bool:
    """A word or two of its own and no number: a label, a tab, a category, a footer's link ("Featured", "all-day")."""
    return item.kind in LABEL_KINDS and short(item) and not any(c.isdigit() for c in f"{item.name} {item.value}")


def _structure(item: Item, has_main: bool, editors: set, stale: bool) -> str:
    """Rules 1 to 4 (module doc): interface by what the item is and where it sits; "" when they say nothing."""
    if item.menu or item.chrome or item.browser_ui or item.kind in CONTROL_KINDS or item.hint or item.search:
        return INTERFACE
    marks = _landmarks(item)
    if getattr(item.ref, "bar", False) or any(m in FRAME_LANDMARKS for m in marks):
        return INTERFACE
    if has_main and getattr(item.ref, "web", False) and MAIN_LANDMARK not in marks:
        return INTERFACE
    if _editor(item) is not None and _editor(item) in editors:
        return INTERFACE
    if stale:  # the page before still showing under the new address
        return INTERFACE
    return ""


def _by_place(item: Item, at, site: str, book: Boilerplate, shared: set[str], controls: set[str], mains: set,
              url: str, listed: set[int]) -> str:  # fmt: skip
    """Rules 5 to 9 and 11 (module doc); `listed` gets the items inside the main list."""
    if at is not None:
        inside, row = _in_list(item, at)
        if inside:
            listed.add(id(item))
            return CONTENT if item.kind != "button" or row else INTERFACE
    if item.kind == "button":
        return INTERFACE
    if digest(item.name) in shared or book.knows(site, item.name):
        return INTERFACE
    if normal(item.name) in controls and not item.url:
        return INTERFACE
    if item.kind == "link" and item.url and url and id(item) not in mains and short(item) and site_of(item.url) == site_of(url):
        return INTERFACE
    return CONTENT


# ---------------------------------------------------------------- what readers take


def items(screen: Screen) -> list[Item]:
    """What the page or app shows, content only, in screen order: the one list every reader of content takes."""
    classify(screen)
    return [i for i in screen.page() if i.role_class == CONTENT]


def interface(item: Item) -> bool:
    return item.role_class == INTERFACE


# ---------------------------------------------------------------- a result's own title, among the content

TITLE, CONTROL, EVENT, TEXT = "title", "control", "event", "text"  # a collected item's role (with CONTENT, INTERFACE)
COUNTER = re.compile(r"^\W*\d[\d.,:]*\s*(?:[^\W\d_]+\W*){0,2}$")  # a number and a word or two: "87 editions", "12:03"
MARKED = re.compile(r"^\W*([^\W\d_]+(?:\s[^\W\d_]+){0,2})\s*:\s+(?=\S)")  # "Cover of: X": a short lead and a colon
CONTROLS = frozenset({"button", "checkbox", "tab", "menu", "field"})


def _address(url: str) -> str:
    """A link's page: host and path, no query's order, fragment or closing slash."""
    parts = urlsplit(url or "")
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}?{parts.query}"


def title_ids(screen: Screen) -> set[int]:
    """The content items of `screen` that are a result's own title, by structure (never a word of the site): a
    heading, a native list's row, or a link that is a result's main link. On a list of results with pages of their own
    (at least two addresses each linked under two names or more: the title, its cover, "87 editions"), only the main
    link of each such address is a title (its longest name that is no counter, no image's and no "Lead: X"), never a
    link to any other address (an author, a category). Without such a list, a content link is a title unless it is a
    counter, an image's name, or the same address and name repeated on MIN_ROWS results (an owner, an author). Text
    is a title only in a native app's list (a web result's title is its link or heading); a button never is."""
    classify(screen)
    shown = [i for i in screen.page() if i.role_class == CONTENT and not i.offscreen]
    images = {normal(i.name) for i in screen.page() if i.kind == "image"}

    def plain(i: Item) -> bool:
        return bool(normal(i.name)) and not COUNTER.match(i.name) and normal(i.name) not in images and not MARKED.match(i.name)

    links = [i for i in shown if i.kind == "link" and i.url]
    by: dict[str, list[Item]] = {}
    for i in links:
        by.setdefault(_address(i.url), []).append(i)
    mains: dict[str, Item] = {}
    for address, group in by.items():
        named = [i for i in group if plain(i)]
        if named and len({normal(i.name) for i in group}) >= 2:
            mains[address] = max(named, key=lambda i: len(i.name))
    results = len(mains) >= 2
    repeated = {
        key for key in {(_address(i.url), normal(i.name)) for i in links}
        if sum((_address(i.url), normal(i.name)) == key for i in links) >= MIN_ROWS
    }  # fmt: skip
    out: set[int] = set()
    for i in shown:
        if i.kind == "heading" or (i.kind == "row" and not COUNTER.match(i.name)):
            out.add(id(i))
        elif i.kind == "link" and i.url:
            key = _address(i.url)
            if (mains.get(key) is i) if results else (plain(i) and (key, normal(i.name)) not in repeated):
                out.add(id(i))
        elif i.kind == "link":
            if plain(i):
                out.add(id(i))
        elif i.kind in LABEL_KINDS and not screen.url and not COUNTER.match(i.name) and not _label(i):
            out.add(id(i))
    return out


def roles(screen: Screen) -> dict[int, str]:
    """Each item's role for a collect to keep with it (`tools.Collected.role`): a result's own title (TITLE), other
    content (CONTENT), a control that holds a value (CONTROL: an option's price on its button), or the rest of the
    interface (INTERFACE: a banner, a label, a form's text)."""
    titled = title_ids(screen)
    out = {}
    for i in screen.items:
        if id(i) in titled:
            out[id(i)] = TITLE
        elif i.role_class == CONTENT:
            out[id(i)] = CONTENT
        else:
            out[id(i)] = CONTROL if i.kind in CONTROLS else INTERFACE
    return out


def kept_text(screen: Screen, book: Boilerplate | None = None) -> Callable[[str], bool]:
    """For a reader of a page's text (ax_walk.page_text): whether a piece of text is the page's, not its site's
    learned frame."""
    book = BOOK if book is None else book
    site = site_key(screen)
    return lambda text: not book.knows(site, text)
