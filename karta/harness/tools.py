"""The tools the brain plans with (docs/harness.md); code and narrow Laya questions carry each one out.

The tools: open_place, find, click, write, type at the focus, fill, answer a dialog or prompt, submit, reach a total,
and the fixed actions press, back, scroll and wait. Each tool returns a ToolResult with an honest Outcome.

Code decides whatever it can, before any Laya question (karta.harness.match): the item the brain named by text,
the one search result containing the searched text, a login page, a dialog, whether a field holds the text. Laya
only the rest, by option elimination: its pick is checked (a pick check, then the step's own outcome), and a pick that
is wrong or unsure is removed and Laya asked again over what is left, a few rounds, each rejected option noted in the
session notes, before the ladder climbs (next search text, next place, the brain). A reversible action acts on the
reversible verdict (Choice.sure) and is verified at once; one that cannot be undone (risk.risky) needs Laya's risky
verdict (Laya.acts) and the user's yes.

Dialogs are cleared only when unrelated to the step (clear_dialog); typing goes only into a field re-found on the
current screen, and return only into that field; places are tried in the order the evidence gives (an open tab of the
site first) and left only on proof (a login page, a page that failed to load, an app not set up); a disabled
target says so with the enabled options near it; failures go to the session notes, and a remembered fact that fails
is demoted.
"""

from __future__ import annotations

import math
import os
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from urllib.parse import unquote_plus, urljoin, urlsplit

from karta.base.models import Abort
from karta.core import events
from karta.harness import content as content_mod
from karta.harness import facts as facts_mod
from karta.harness import match, risk
from karta.harness import screen as screen_mod
from karta.harness import view as view_mod
from karta.harness.brain import MAX_COLLECT, SLOT_REF, step_line
from karta.harness.decide import (
    DOES_IT,
    DOES_OTHER,
    FITS,
    LOADING,
    LOGIN,
    NONE,
    NOT_PRIVATE,
    OTHER,
    PRIVATE,
    READY,
    SHOWN,
    labels,
)
from karta.harness.facts import Place, _path, _shared, beside, frame_of, holds, norm
from karta.harness.types import Choice, HarnessEnv, Item, MacFacts, Outcome, Screen, ToolResult

try:  # the session notes (in RAM): kept with memory; a small stand-in until then
    from karta.harness.memory import SessionNotes
except ImportError:  # pragma: no cover

    class SessionNotes:  # type: ignore[no-redef]
        def __init__(self):
            self.rows: list[tuple[str, str, str, str, str]] = []

        def add(self, kind: str, app: str, key: str, value: str = "", reason: str = "") -> None:
            self.rows.append((kind, app, " ".join(key.lower().split()), value, reason))

        def tried(self, kind: str, app: str, key: str) -> set[str]:
            key = " ".join(key.lower().split())
            return {v for k, a, kk, v, _ in self.rows if (k, a, kk) == (kind, app, key)}

        def lines(self, limit: int = 8, app: str | None = None) -> list[str]:
            return [f"{a + ': ' if a else ''}{k} {kk!r} -> {v!r} failed: {r}" for k, a, kk, v, r in reversed(self.rows)][:limit]


SEARCH_WORD = re.compile(r"\bsearch\b", re.I)  # the plan's own word for a search box ("where: search box"), never the UI's
ADDRESS_WORDS = re.compile(r"\b(address bar|address field|url bar|location bar|web address|omnibox)\b", re.I)
MENU_TITLE = re.compile(r"(?:the\s+)?([A-Za-z][\w ]{0,20}?)\s+menu", re.I)
FOCUS_KEYS = {"cmd+l", "cmd+a", "tab", "shift+tab"}
BROWSER_APPS = {*facts_mod.CHROMIUM, "Safari"}
URLISH = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)
NO_RESULT_LINES = 3  # a search that found nothing brings up a line or two; Laya reads only those
NUMBERED = re.compile(r"^\W*(\d+)[.)]\s+\S")
ARTICLE = re.compile(r"^(the|a|an)\s+", re.I)
CONTROL_WORDS = re.compile(r"\s+(button|link|field|box|icon|tab|folder|checkbox|menu item)$", re.I)
FIND_KEYS = "cmd+f"  # the platform's find command: its menu command is clicked when nothing on the page opens search
CHANGE_TIMEOUT = 3.5  # seconds an action gets to change the screen before it counts as no change
RETRY_DELAYS = (0.3, 0.5, 0.8, 1.2, 2.0)  # backoff before each re-read when a target or result is not there yet
RECHECKS = 12  # at most this many re-reads while a target or result is not there yet (an env that waits for changes)


def _money_pattern() -> re.Pattern:
    """A number beside a currency mark, as the platform knows them: any Unicode currency symbol ($, \u20ac, \u00a3,
    \u00a5, \u20b9 ...) beside any amount, or an ISO code in common use (Foundation's list: "EUR", "INR") beside an
    amount with a decimal or grouping mark ("EUR 30.00": "TOP 10" is no price). Never a word of ours ("rupees")."""
    symbols = f"[{re.escape(''.join(ch for ch in map(chr, range(0x10000)) if unicodedata.category(ch) == 'Sc'))}]"
    try:
        from Foundation import NSLocale

        codes = "|".join(sorted({str(c) for c in NSLocale.commonISOCurrencyCodes()}, key=len, reverse=True))
    except Exception:
        codes = ""
    marked = r"\d[\d'\u00a0\u202f]*[.,]\d[\d.,'\u00a0\u202f]*"
    alts = [rf"{symbols}\s?(?P<a>{AMOUNT})", rf"(?P<b>{AMOUNT})\s?{symbols}"]
    if codes:
        alts += [rf"\b(?:{codes})\s?(?P<c>{marked})", rf"(?P<d>{marked})\s?(?:{codes})\b"]
    return re.compile("|".join(alts))


AMOUNT = r"\d(?:[\d.,'\u00a0\u202f]*\d)?"  # digits with grouping or decimal marks of any locale
MONEY = _money_pattern()
TYPING_TOOLS = ("write", "find", "type", "fill")
REACH_ROUNDS = 16
TARGETS = 16  # Laya's options per question
# Laya's question for the button that submits a form (D3). Its words are what the model reads best across send, save,
# post, reply, create, add, done and OK buttons ("send or save what was written" lost Create, Add and Reply to "none")
SUBMIT_INTENT = "submit what was written"
ROUNDS = 4  # option elimination: asks over a shrinking pool before the ladder climbs
LOOKS = 3  # looks at a page that may still be loading
MAX_MOVES = 2  # places left for another per task (each on proof); then the user decides
LOAD_WAIT = 1.5
NEAR_OPTIONS = 8
OPTION_KINDS = ("button", "checkbox", "row", "tab", "link", "field", "image", "other")
# a permission prompt's answers, in order of preference (the narrowest grant first); names are match.normalise()d
ALLOW = (
    "allow once",
    "allow while using app",
    "allow while using the app",
    "only this time",
    "allow",
    "ok",
    "okay",
    "enable",
    "turn on",
    "continue",
    "yes",
)
DENY = ("dont allow", "do not allow", "deny", "block", "never", "not now", "no thanks", "ask app not to track", "cancel", "no")


def words(text: str, n: int = 6) -> str:
    """What the brain wrote, kept short for Laya's options."""
    return " ".join(str(text).split()[:n])


def bare(intent: str) -> str:
    """ "the Search button" -> "search": how a control's own name would read."""
    return norm(CONTROL_WORDS.sub("", ARTICLE.sub("", intent.strip())))


def affirms(choice: Choice | None, yes: str) -> bool:
    """A sure answer that is the semantic yes of its question (decide.DOES_IT, decide.SHOWN, ...): never true/false."""
    return bool(choice) and choice.sure and choice.best == yes


def item_for(label: str, items: list[Item]) -> Item | None:
    """The item a Laya option names: decide.labels() is deterministic, so its label maps back; a bare name also does."""
    if label == NONE:
        return None
    hit = labels(items).get(label)
    if hit is not None:
        return hit
    return next((i for i in items if i.name == label), None) or next((i for i in items if norm(i.name) == norm(label)), None)


def code_named(target: str, items: list[Item]) -> Item | None:
    """The item the brain's words name by text (karta.harness.match), or None."""
    try:
        return match.named(target, items)
    except NotImplementedError:  # pragma: no cover - the skeleton before it was built
        return None


def code_one_result(searched: str, results: list[Item]) -> Item | None:
    try:
        return match.one_result(searched, results)
    except NotImplementedError:  # pragma: no cover
        return None


def step_targets(step: dict | None) -> list[str]:
    """What a step acts on, in the brain's words: a control, a field, the fields of a form."""
    if not step:
        return []
    tool = step.get("tool")
    if tool == "click":
        return [str(step.get("target", ""))]
    if tool == "write":
        return [str(step.get("where", ""))]
    if tool == "fill":
        return [str(k) for k in step.get("fields", {})]
    return []


def step_ats(step: dict | None) -> list[dict]:
    """The items a page plan's step names by number, as identities (view.identity): a click's, a write's or an
    answer's one, a fill's per field."""
    at = (step or {}).get("at")
    return [a for a in (at if isinstance(at, list) else [at]) if isinstance(a, dict) and a]


STALE = "no longer on screen as the page showed it"  # a #n item gone or no longer one: the page is looked at again


_host_of = facts_mod.host_of
_site_of = facts_mod.site_of


def on_site(scr: Screen, url: str) -> bool:
    """The page is on that web address's host or a subdomain of it, never a sibling subdomain (facts.on_host): a
    search page of a domain is not that domain's mail host. Words on a page are no proof of where it is."""
    return bool(scr.url) and facts_mod.on_host(scr.url, url)


OLD_PAGE = 0.8  # a read whose page items are this share of the last page's still shows the last page


def redirected(before: Screen, now: Screen) -> bool:
    """A page opened just now landed on another site than the one asked for: its address moved off the page it was
    on before opening (a stale tab that never moved is not a redirect)."""
    host = _host_of(now.url or "")
    return bool(host) and host != _host_of(before.url or "")


def page_keys(scr: Screen) -> set[tuple[str, str]]:
    return {(norm(i.name), i.kind) for i in scr.page() if norm(i.name)}


def still_old(scr: Screen, old: Screen | None) -> bool:
    """A read right after opening a page (a web address, a tab) that still shows the page before it: the address bar
    and the document's own address change first, the page's content last. Old by content: most of what the page lists
    is what the same app's last read listed. With no last read of that app (the browser came from behind another app),
    by structure: page text read only off the pixels while the page exposes no element of its own yet."""
    page = [i for i in scr.page() if i.name.strip()]
    if old is not None and old.app == scr.app:
        now, was = page_keys(scr), page_keys(old)
        return bool(now) and bool(was) and len(now & was) >= OLD_PAGE * len(now)
    return bool(page) and all(i.source == "ocr" for i in page)


def memory_key(scr: Screen, browsers=()) -> str:
    """Where a remembered fact belongs (memory.py): the page's exact host for a web page ("mail.example.com", so a site's
    mail and search never share facts), the app for a native app, and nothing ("") for a browser showing no web page:
    never the browser, whose pages are every site."""
    if scr.url and re.match(r"^https?://", scr.url, re.I):
        from karta.harness.memory import host_key

        return host_key(scr.url)
    return "" if scr.app in browsers else scr.app


def titled(target: str, pool: list[Item]) -> Item | None:
    """The one item whose name starts with the target's whole name: a list row read as its title and then its preview
    ("Asha Rao Studio see you at five"). Two words at least, and only when exactly one item starts so; never a menu
    command, open or in the menu bar."""
    want = norm(target)
    if len(want.split()) < 2:
        return None
    # never a menu command: one is named only by its exact last part ("New Window" is never "New Window with Same Command")
    hits = [
        i for i in pool if i.enabled and i.kind not in ("field", "menu") and not i.menu and norm(i.name).startswith(want + " ")
    ]
    return hits[0] if len(hits) == 1 else None


def most_like(text: str, pool: list[Item]) -> Item | None:
    """The item whose name holds most of `text`'s words (at least 60% of them, and more than any other item)."""
    want = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 1}
    if not want:
        return None
    scored = sorted(
        ((len(want & set(re.findall(r"[a-z0-9]+", i.name.lower()))) / len(want), n) for n, i in enumerate(pool)), reverse=True
    )
    if not scored or scored[0][0] < 0.6 or (len(scored) > 1 and scored[1][0] == scored[0][0]):
        return None
    return pool[scored[0][1]]


def nearest_named(target: str, anchor: Item, pool: list[Item]) -> Item | None:
    """Among the enabled controls named `target`, the one nearest to `anchor` on screen, when it is clearly the
    nearest (under 0.6 of the next one's distance). None when positions are unknown or it is a close call."""
    at = getattr(anchor.ref, "centre", None)
    if at is None:
        return None
    same = [
        i
        for i in pool
        if i is not anchor
        and i.enabled
        and i.kind != "field"
        and code_named(target, [i])
        and getattr(i.ref, "centre", None) is not None
    ]
    ranked = sorted(same, key=lambda i: math.dist(at, i.ref.centre))
    if not ranked:
        return None
    if len(ranked) > 1 and math.dist(at, ranked[0].ref.centre) >= 0.6 * math.dist(at, ranked[1].ref.centre):
        return None
    return ranked[0]


MENU_FILLER = {"the", "a", "an", "s", "menu", "item", "command", "option", "click", "in", "from", "under"}


def exact_name(intent: str, item: Item) -> bool:
    """The item is named exactly what the step says: its own name, or a menu command whose last part it is ("New
    Folder" names "File > New Folder"), unless that command is risky (it is then named only in full)."""
    said = (norm(intent), bare(intent))
    if norm(item.name) in said:
        return True
    last = item.name.rsplit(" > ", 1)[-1]
    return item.menu and " > " in item.name and norm(last) in said and risk.risky("click", last) is None


def names_command(target: str, command: str) -> bool:
    """`target` names the menu command `command` ("File > New Private Window"): every word of the command itself is in
    the target, and every word of the target is in the command's path."""

    def ws(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in MENU_FILLER}

    own, path, want = ws(command.split(" > ")[-1]), ws(command), ws(target)
    return bool(own) and own <= want and want <= path


def typed_lines(texts) -> set[str]:
    """What we typed, line by line (a command typed at a prompt echoes one line at a time)."""
    return {line.strip() for t in texts for line in str(t).splitlines() if line.strip()}


def _never(_question: str) -> bool:
    return False


def native(scr: Screen) -> bool:
    """A native app's screen, not a web page: no web address and not a browser."""
    return not scr.url and scr.app not in BROWSER_APPS


def loading(scr: Screen) -> bool:
    """A page still loading, by structure: it says so (`Screen.busy`: AXLoaded false, aria-busy on it or around an
    item), or it shows nothing yet. Never a failure: wait for it. A spinner's words are never read (the pixel waits
    cover what still moves)."""
    return scr.busy or not any(i.name.strip() for i in scr.page())


def plain_answer(item: Item) -> bool:
    """A dialog's plain answer, by structure: a numbered choice, or the dialog's own default or cancel button (the ones
    Return and Escape press). Anything else in a dialog (a quantity, a filter, a setting) may change more."""
    return bool(NUMBERED.match(item.name)) or (item.kind == "button" and (item.default or item.cancels))


def mentions(query: str, name: str) -> bool:
    """Every searched word begins a word of the name, or nearly spells one ("Mar" in "Marisol Okafor", "Marisal"
    in "Marisol Iyer")."""
    wanted, have = match.normalise(query).split(), match.normalise(name).split()
    return bool(wanted) and all(any(w.startswith(q) or match.similarity(q, w) >= 0.75 for w in have) for q in wanted)


def amount(raw: str) -> float | None:
    """A written amount in any locale's format: the last "." or "," followed by one or two digits is the decimal mark,
    every other mark groups ("12,99" -> 12.99, "1.234,50" -> 1234.5, "1,200" -> 1200, "4.50" -> 4.5)."""
    digits = re.sub(r"['\u00a0\u202f]", "", raw)
    marks = [m.start() for m in re.finditer(r"[.,]", digits)]
    whole, frac = digits, ""
    if marks and len(digits) - marks[-1] - 1 in (1, 2):
        whole, frac = digits[: marks[-1]], digits[marks[-1] + 1 :]
    whole = re.sub(r"[.,]", "", whole)
    try:
        return float(f"{whole}.{frac}" if frac else whole)
    except ValueError:
        return None


def money(text: str) -> list[float]:
    """The amounts of money written in `text`, by format: a number beside a currency mark ("\u20b9120", "$4.50",
    "12,99 \u20ac", "EUR 30")."""
    found = (amount(m["a"] or m["b"] or m["c"] or m["d"]) for m in MONEY.finditer(str(text)))
    return [a for a in found if a is not None]


def amount_of(text: str) -> float | None:
    """The amount a step names: "\u20b9120", "$30", "12,99 \u20ac", or a bare number."""
    found = money(text)
    if found:
        return found[0]
    bare_number = re.search(AMOUNT, str(text))
    return amount(bare_number[0]) if bare_number else None


# ---------------------------------------------------------------- collect: what a step reads off the screen

LINK_WORDS = re.compile(r"\b(links?|urls?|web ?address(es)?)\b", re.I)
COLLECT_KINDS = ("link", "text", "row", "other", "button")  # never a heading: it names a list, it is not in it
READ_MORE = 2  # scrolls to read more when fewer than asked show (as reveal's)
CONTROL_WORDS_MAX = 2  # a button named in this few words is a control, not an item to collect
COLLECT_FILLER = {
    "the", "a", "an", "of", "on", "in", "to", "for", "from", "my", "all", "every", "each", "link", "links", "url",
    "urls", "item", "items", "list", "row", "rows", "good", "best", "top", "first", "some", "few", "any", "new",
    "latest", "recent", "this", "that", "these", "those", "with", "and", "or", "about", "today", "tomorrow", "page",
    "pages", "article", "articles", "post", "posts", "video", "videos", "result", "results", "entries", "entry",
    "title", "titles", "headline", "headlines", "name", "names", "listing", "listings", "product", "products", "one",
    "ones", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "their", "its", "search",
}  # fmt: skip
COUNT_LINE = re.compile(r"^\W*[\d,.]+\s+[^\W\d_]+\W*$")  # a number and one word: "3 tasks", "1.204 Treffer"
CODE_WANTED = re.compile(r"\b(code|otp|pin|passcode|one[- ]time)\b", re.I)
CODES = (
    re.compile(r"\b[A-Z]{1,3}-\d{3,8}\b"),  # G-482913
    re.compile(r"(?<![\d.,:/])\d{4,8}(?![\d.,:/])"),  # 482913
    re.compile(r"\b(?=[A-Z]*\d)(?=\d*[A-Z])[A-Z0-9]{5,10}\b"),  # K7Q2XZ
)
MONEY_WANTED = re.compile(r"\b(prices?|costs?|amounts?|totals?|fares?|fees?|balances?|cheap\w*|expensive|pricier)\b", re.I)
EMAIL_WANTED = re.compile(r"\be-?mail address(es)?\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
TITLE_WORDS = re.compile(r"\b(titles?|headlines?|names?)\b", re.I)  # a result's title or name is its link's name
# A page's main text: a text word with an article word ("the main text of the article", "the post's content").
TEXT_WORDS = re.compile(r"\b(text|content|contents|body|wording)\b", re.I)
ARTICLE_WORDS = re.compile(r"\b(main|full|whole|article|story|post|page|blog|essay|readme|it|this)\b", re.I)
MIN_PAGE_TEXT = 200  # characters: less is no article, so the screen's items are read as before
TEXT_CAP = 7000  # a native window's text, at most (as ax_walk.MAIN_TEXT_CHARS for a page)
# The collect prompt's "the link of this page": the page's own address, never a link on it.
OWN_LINK = re.compile(r"\b(link|url|address)\b.*\b(this|current)\b|\b(this|current)\b.*\b(link|url|address)\b", re.I)
ROW_MAX = 4  # a row or card holds its title and a few details; more title-like texts as close is no row of its own
ROW_GAP_LINES = 6  # read off the pixels: a row's title is at most this many of the value's lines above it
# The user's words for what is in front now ("this page", "this email", "what's on my screen"): the plan acts on it
# and opens no place for it (loop: an open_place of the place in front is dropped).
FRONT_WORDS = re.compile(
    r"\b(this|current)\s+(page|tab|site|website|article|post|email|e-mail|mail|message|thread|conversation|chat|window"
    r"|document|doc|note|file|pdf|screen)\b|\bwhat'?s on (my|the) screen\b|\b(in front of me|on my screen)\b",
    re.I,
)
WINDOW_TEXT_KINDS = ("heading", "text")  # a native window's text, read in order when it has no web page
TITLES_ONLY = "You said no to sending {apps}'s text to Claude, so I used the titles only."
CONSENT_ASK = "May I send text from {app} to Claude to summarise it?"
# The collect prompt's calendar events: read through EventKit for the day asked (calendar_day), else off the screen.
CALENDAR_WORDS = re.compile(r"\b(calendar|events?|meetings?|appointments?)\b", re.I)
AD_ASK = "an advertisement or a paid placement"  # what D9 is asked of an item that carries a short label of its own


@dataclass(frozen=True)
class Collected:
    """One thing a collect step took: its title, its web address (links), and the value it was taken for (a code, a
    price), when the step asked for one. `titled`: a value read off a row (a price beside its product), which goes
    with the row's title."""

    title: str
    url: str = ""
    value: str = ""
    titled: bool = False
    role: str = field(default="", compare=False)  # what it was on screen (content.roles): a result's own title, other
    # content, a control holding a value, interface; an event read from the calendar; a page's text. The contract's
    # check reads it (contract.py)

    def text(self) -> str:
        """As it goes into a later step's text: the value alone ("title — value" for a row's), else "title — address",
        else the title."""
        if self.value:
            return f"{self.title} \u2014 {self.value}" if self.titled else self.value
        return f"{self.title} \u2014 {self.url}" if self.url else self.title

    def shown(self) -> str:
        """For the trace and the ledger: titles and hosts only (never an address's path, never a code or a page's
        text: a value is shown as its length)."""
        if self.value:
            size = f"a value of {len(self.value)} characters"
            return f"{words(self.title, 8)}: {size}" if self.titled else size
        title = words(self.title, 8)
        return f"{title} ({_host_of(self.url)})" if self.url else title


def wants_text(what: str) -> bool:
    """The collect asks for a page's main text ("the main text of the article"), not items on it."""
    return bool(TEXT_WORDS.search(what) and ARTICLE_WORDS.search(what)) and not LINK_WORDS.search(what) and not asks_value(what)


def window_text(scr: Screen, cap: int = TEXT_CAP) -> str:
    """A native window's text (a message open in a mail app, a note), its content's headings and static texts in
    screen order (`content.items`), one per line; never interface (a control, a field's typing, a menu, a toolbar's or
    an editor's labels). At most `cap` characters, cut at a word."""
    shown = content_mod.items(scr)
    text = "\n".join(" ".join(i.name.split()) for i in shown if i.kind in WINDOW_TEXT_KINDS and not i.offscreen)
    return text if len(text) <= cap else text[:cap].rsplit(" ", 1)[0]


def asks_value(what: str) -> bool:
    """The collect names a kind of value (a code, a price, an email address), not whole items."""
    return any(p.search(what) for p in (CODE_WANTED, MONEY_WANTED, EMAIL_WANTED))


def wanted_value(what: str, text: str) -> str | None:
    """The part of `text` a collect asks for when it names a kind of value (a code, a price, an email address):
    that part, or None when `text` has none (so it is not one of them). "" when no kind is asked for."""
    if CODE_WANTED.search(what):
        for pattern in CODES:
            found = [m[0] for m in pattern.finditer(text) if not re.fullmatch(r"(19|20)\d\d", m[0])]
            if found:
                return next((f for f in found if len(f) == 6), found[0])
        return None
    if MONEY_WANTED.search(what):
        hit = MONEY.search(text)
        return hit[0].strip() if hit else None
    if EMAIL_WANTED.search(what):
        hit = EMAIL.search(text)
        return hit[0] if hit else None
    return ""


def _same_line(a: Item, b: Item) -> bool:
    if (a.row and b.name in a.row) or (b.row and a.name in b.row):
        return True
    fa, fb = frame_of(a), frame_of(b)
    if fa is None or fb is None:
        return False
    ya, yb = fa["y"] + fa["h"] / 2, fb["y"] + fb["h"] / 2
    return abs(ya - yb) < max(fa["h"], fb["h"]) / 2


LEAD = re.compile(r"^\W*([^\W\d_]+(?:\s[^\W\d_]+)?)\s*[\u00b7\u2022|:\u2013\u2014]\s+\S")  # "Xy · rest": a short lead


def ad_label(item: Item, page: list[Item]) -> Item | None:
    """A short label of the item's own, by structure: a one- or two-word text (no link, no price) on its line, or
    just before it (a label over a card), or a one- or two-word lead set off by a mark in its own name ("Xy · rest").
    Whether it marks an ad is Laya's (Toolbox.is_ad); never its words."""
    lead = LEAD.match(item.name)
    if lead:
        return Item(lead[1], "text")
    k = next((n for n, i in enumerate(page) if i == item), -1)
    for n, other in enumerate(page):
        if other == item or other.kind not in ("text", "other") or other.url or money(other.name):
            continue
        if 0 < len(other.name.split()) <= CONTROL_WORDS_MAX and (_same_line(other, item) or n == k - 1):
            return other
    return None


def same_page(url: str, here: str) -> bool:
    """`url` is the page itself, or its own path with other query parameters (a search refinement, a sort, a filter,
    a link back to the same search): no new page to collect. Relative addresses are read against `here`."""
    if not here:
        return False
    a, b = urlsplit(urljoin(here, url.split("#")[0])), urlsplit(here.split("#")[0])
    host = lambda u: u.netloc.lower().removeprefix("www.")  # noqa: E731
    return host(a) == host(b) and (a.path.rstrip("/") or "/") == (b.path.rstrip("/") or "/")


def own_title(scr: Screen, what: str) -> str:
    """The page's own title (its window's, else a heading's) when it names the thing `what` asks about and the page is
    not a search for it (its words in the address's query): the thing's own page, whose values are its own."""
    query = unquote_plus(urlsplit(scr.url or "").query).lower()
    want = topic_words(what)
    if not want or all(w in query for w in want):
        return ""
    names = (scr.window, *(i.name for i in scr.page() if i.kind == "heading"))
    return next((t for t in names if t and named_fit(what, Item(t))), "")


def _page_of(url: str) -> str:
    """A web address as the page it opens: its host and path, no query or fragment."""
    parts = urlsplit(url or "")
    return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"


MARKED = content_mod.MARKED  # "Cover of: X": a short lead and a colon


def _core(title: str) -> tuple[str, bool]:
    """A title's own words, lower case, without a short lead set off by a colon ("Cover of: X" is X); and whether
    one was there."""
    lead = MARKED.match(title or "")
    rest = title[lead.end() :] if lead else title
    return " ".join(re.findall(r"\w+", (rest or "").lower())), bool(lead)


def same_thing(a: Collected, b: Collected) -> bool:
    """Two collected items are one result: the same address, the same words, or one is the other's own title under a
    short lead ("Cover of: X" beside "X ...", a thumbnail's name). Two values that differ are never one."""
    if a.value and b.value and a.value != b.value:
        return False
    if a.url and b.url and a.url.split("#")[0].rstrip("/") == b.url.split("#")[0].rstrip("/"):
        return True
    (ca, la), (cb, lb) = _core(a.title), _core(b.title)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    short_one, long_one, marked = (ca, cb, la) if len(ca) <= len(cb) else (cb, ca, lb)
    return marked and len(short_one.split()) >= 2 and (long_one + " ").startswith(short_one + " ")


def collect_candidates(scr: Screen, what: str, own: str = "") -> list[Item]:
    """What a collect could take from this screen, in screen order: the page's content (`content.items`: the content
    model's one label, never interface: controls, navigation, banners, form labels, the site's learned boilerplate),
    never the list's own title or count, an off-screen item, or a calendar's own date and hour labels. Links asked
    for: only items with their own web address that leads off the page, one per address under its longest name (a
    result's title, not its thumbnail), never a bare host. A value asked for: only items holding one, the content's
    first, else any other item that holds one (an option's price on a control), never a field, a menu or the browser's
    chrome. Never an item of a dialog that blocks the page while the page has content of its own, nor of a system
    prompt."""
    if facts_mod.system_prompt(scr):  # the system asking for a permission: never anything a task collects
        return []
    shown = content_mod.items(scr)  # classified first: the items below are the read's classified ones
    page = scr.page()
    titles = {norm(scr.window)} | {norm(i.name) for i in page if i.kind == "heading"}  # what names the list itself
    numbers_wanted = asks_value(what)  # a price or a code is all digits: then a number alone is the item
    counts = [i for i in page if COUNT_LINE.match(i.name)]  # one alone is the list's count; several are its rows

    def usable(i: Item) -> bool:
        return (
            not (len(counts) == 1 and i is counts[0])
            and norm(i.name) not in titles
            and not i.offscreen
            and len(i.name.strip()) > 1
            and (numbers_wanted or not bare_date(i.name, scr.lang))
        )

    pool = [i for i in shown if usable(i) and (i.kind in COLLECT_KINDS or (numbers_wanted and i.kind != "heading"))]
    behind = [i for i in pool if not (i.dialog and not i.panel)]
    if behind:  # a dialog over the page (a prompt, an alert) is no content of the page it covers
        pool = behind
    if LINK_WORDS.search(what) or (TITLE_WORDS.search(what) and scr.url):
        here = (scr.url or "").split("#")[0].rstrip("/")
        linked = [i for i in pool if i.url and not i.url.startswith("#") and not same_page(i.url, here)]
        linked = [i for i in linked if not URLISH.match(i.name.strip())]  # a cite
        if linked:
            # a link named as an image on the page is that image's: a result's thumbnail or cover, never its title
            thumbs = {norm(i.name) for i in page if i.kind == "image"}
            # titles or names: a result's own title (content.title_ids), never its author's link, a cover or a counter
            titled = content_mod.title_ids(scr) if TITLE_WORDS.search(what) else set()

            def rank(i: Item) -> tuple:
                return id(i) in titled, norm(i.name) not in thumbs, len(i.name)

            best: dict[str, Item] = {}
            for i in linked:  # one per address, at its first place on screen, under its longest name (a title's)
                key = i.url.split("#")[0].rstrip("/")
                if key not in best or rank(i) > rank(best[key]):
                    best[key] = i
            order = [best[k] for k in dict.fromkeys(i.url.split("#")[0].rstrip("/") for i in linked)]
            order = [i for i in order if norm(i.name) not in thumbs] or order  # thumbnails only when nothing is titled
            return [i for i in order if id(i) in titled] or order
        pool = [i for i in pool if i.kind == "link"] or pool
    if numbers_wanted:  # a kind of value is asked for: only the items holding one, the content's first
        holding = [i for i in pool if wanted_value(what, f"{i.name} {i.value}")]
        if not holding:  # a value the page shows on a control of its own (an option's price): any item but a field
            others = [i for i in page if i.role_class != content_mod.CONTENT and i.kind not in ("field", "menu", "heading")]
            holding = [i for i in others if usable(i) and wanted_value(what, f"{i.name} {i.value}")]
        if not MONEY_WANTED.search(what):
            return holding
        return priced_rows(page, holding, what, own or own_title(scr, what))
    seen: set[str] = set()
    out = []
    for i in pool:  # the same words twice (AX and OCR, a title and its tooltip) are one item
        key = norm(f"{i.name} {i.value}")
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out


def topic_words(what: str) -> set[str]:
    """The distinctive words of what a collect asks for ("overdue invoices" -> overdue, invoice), singular; never the
    kind of value it asks for ("the price of the Lumen desk lamp" -> lumen, desk, lamp)."""
    want = {
        w.rstrip("s")
        for w in re.findall(r"[a-z0-9']+", what.lower())
        if len(w) > 2 and w not in COLLECT_FILLER and not any(p.fullmatch(w) for p in (MONEY_WANTED, CODE_WANTED))
    }
    return want - {w.rstrip("s") for w in COLLECT_FILLER}


def _names(word: str, have: set[str]) -> bool:
    """`word` is one of `have`, or one with a model number after it ("bud" in "buds4")."""
    return word in have or any(re.fullmatch(rf"{re.escape(word)}s?\d+[a-z]?", h) for h in have)


def named_fit(what: str, item: Item) -> bool:
    """Code's own yes: every distinctive word of `what` is in the item ("overdue invoices" in "Invoice 214 overdue")."""
    want = topic_words(what)
    have = {w.rstrip("s") for w in re.findall(r"[a-z0-9']+", f"{item.name} {item.value}".lower())}
    return bool(want) and all(_names(w, have) for w in want)


def bare_date(name: str, lang: str = "") -> bool:
    """A date, a time or numbers alone ("28 October 2026 Wednesday", "Mittwoch, 28. Oktober", "M 28 5", "8 AM"): a
    calendar's own day and hour labels and a mini-month's cells, never an item of a list. Every word is a number, a
    word of one or two letters ("de", "am") or one of the platform's own month, weekday and day-period names
    (`date_names`) in the screen's language or the user's."""
    parts = re.findall(r"[^\W\d_]+|\d+", name.lower())
    names = date_names(lang)
    return bool(parts) and all(p.isdigit() or len(p) <= 2 or p in names for p in parts)


_DATE_NAMES: dict[str, frozenset[str]] = {}


def date_names(lang: str = "") -> frozenset[str]:
    """Month and weekday names (full and short) and the AM/PM symbols, lower case, as Foundation's date formatter
    gives them for `lang` and for the user's preferred languages: the platform's own, never a list of ours. Empty off
    a Mac."""
    if lang not in _DATE_NAMES:
        names: set[str] = set()
        try:
            from Foundation import NSDateFormatter, NSLocale

            for code in dict.fromkeys([lang, *list(NSLocale.preferredLanguages())]):
                if not code:
                    continue
                fmt = NSDateFormatter.alloc().init()
                fmt.setLocale_(NSLocale.localeWithLocaleIdentifier_(code))
                for symbols in (fmt.monthSymbols(), fmt.shortMonthSymbols(), fmt.weekdaySymbols(), fmt.shortWeekdaySymbols()):
                    names |= {w for s in symbols or [] for w in re.findall(r"[^\W\d_]+", str(s).lower())}
                names |= {w for s in (fmt.AMSymbol(), fmt.PMSymbol()) for w in re.findall(r"[^\W\d_]+", str(s).lower())}
        except Exception:
            pass
        _DATE_NAMES[lang] = frozenset(names)
    return _DATE_NAMES[lang]


def asked_day(text: str) -> date | None:
    """The first day `text` names ("today", "tomorrow", a weekday, "30 October"), resolved by the platform's date
    detector (NSDataDetector, in the user's calendar and time zone); None when it names none, or off a Mac."""
    try:
        from Foundation import NSDataDetector, NSTextCheckingTypeDate

        detector, _ = NSDataDetector.dataDetectorWithTypes_error_(NSTextCheckingTypeDate, None)
        found = detector.firstMatchInString_options_range_(text, 0, (0, len(text)))
    except Exception:
        return None
    return datetime.fromtimestamp(found.date().timeIntervalSince1970()).date() if found and found.date() else None


def calendar_day(what: str, task: str, url: str | None) -> date | None:
    """The day a collect of calendar events asks for: `what` names events (CALENDAR_WORDS) and, with a web page in
    front, the calendar itself (a site's own events are the page's); the day named in `what`, else in the task."""
    if not CALENDAR_WORDS.search(what) or (url and not re.search(r"\bcalendar\b", what, re.I)):
        return None
    return asked_day(what) or asked_day(task or "")


def day_words(day: date, today: date | None = None) -> str:
    """ "today", "tomorrow", "on" and a weekday within the week, else "on 30 October"."""
    ahead = (day - (today or date.today())).days
    if ahead in (0, 1):
        return ("today", "tomorrow")[ahead]
    return f"on {day:%A}" if 1 < ahead < 7 else f"on {day.day} {day:%B}"


def _alpha_words(text: str) -> list[str]:
    return re.findall(r"[a-z]{3,}", MONEY.sub(" ", text).lower())


def row_title_like(item: Item) -> bool:
    """Could title a row: words of its own (two or more), not a control, a value or a tooltip."""
    return (
        item.kind not in ("button", "field", "checkbox", "menu", "tab")
        and not item.hint
        and not MONEY.search(f"{item.name} {item.value}")
        and len(_alpha_words(item.name)) >= 2
    )


def ax_owner(value: Item, titles: list[Item]) -> Item | None:
    """The title of the row or card a value sits in, by the accessibility tree: the title-like item sharing its
    deepest ancestor (several as close: the longest, which is the title, not a detail). None when it is read off the
    pixels, or when that ancestor holds more than a row's few texts (a price alone in a sidebar)."""
    mine = _path(value)
    if not mine:
        return None
    best, tied = 0, []
    for title in titles:
        depth = _shared(mine, _path(title))
        if depth > best:
            best, tied = depth, [title]
        elif depth == best and depth:
            tied.append(title)
    return max(tied, key=lambda t: len(t.name)) if 0 < len(tied) <= ROW_MAX else None


def form_mates(controls: list[Item], box: Item) -> list[Item] | None:
    """The controls of `box`'s own form by the accessibility tree: those inside the deepest container they share with
    the field. None when no control shares more than the page itself with it (or none has a path): then the tree says
    nothing, and the caller judges by where they are drawn."""
    mine = _path(box)
    depths = [(_shared(mine, _path(c)), c) for c in controls if _path(c)]
    if not depths:
        return None
    deepest = max(d for d, _ in depths)
    page = min(_shared(mine, _path(c)) for _, c in depths)  # what every control shares with it: the page or window
    if deepest <= page and len(depths) > 1:
        return None
    return [c for d, c in depths if d == deepest]


def seen_owner(value: Item, titles: list[Item]) -> Item | None:
    """The title of a value's row by where they are drawn: on its line to its left, or above it in its column within
    ROW_GAP_LINES of its lines; the nearest."""
    v = frame_of(value)
    if v is None:
        return None
    best: tuple[float, Item] | None = None
    for title in titles:
        t = frame_of(title)
        if t is None:
            continue
        on_line = abs((t["y"] + t["h"] / 2) - (v["y"] + v["h"] / 2)) < max(t["h"], v["h"]) / 2 and t["x"] < v["x"]
        above = t["y"] + t["h"] / 2 < v["y"] and same_column(title, value)
        gap = 0.0 if on_line else v["y"] - (t["y"] + t["h"])
        if (on_line or above) and gap <= ROW_GAP_LINES * v["h"] and (best is None or gap < best[0]):
            best = (gap, title)
    return best[1] if best else None


def priced_rows(page: list[Item], prices: list[Item], what: str, own: str = "") -> list[Item]:
    """Each price with its row's title, as one item (the title's, its value the price): the row by the accessibility
    tree (its card or table row), else the price's own words, else where it is drawn. A thing named in `what` must be
    the row's title, and the price comes from that same row: a price with no row of its own is never taken for it.
    With nothing named, a price with no row is kept alone. `own`: the page's own title when it names the thing (the
    item's own page): with no row that names it, the page's prices are the thing's, under that title. In screen order
    (an accessory for the thing goes after it by Laya's word, in the collect: Toolbox.accessory)."""
    titles = [t for t in page if row_title_like(t)]
    named = bool(topic_words(what))
    rows: list[Item] = []
    if named and own:
        rows = priced_rows(page, prices, what)
        return rows or [Item(own, "text", value=f"{p.name} {p.value}".strip()) for p in prices]
    for price in prices:
        text = f"{price.name} {price.value}".strip()
        owner = ax_owner(price, titles)
        if owner is None and len(_alpha_words(price.name)) >= 3:
            owner = replace(price, name=" ".join(MONEY.sub(" ", price.name).split()).strip(" ,:;-|"))
        owner = owner or seen_owner(price, titles)
        if owner is None:
            if not named:
                rows.append(price)
            continue
        row = replace(owner, value=text)
        if not named or named_fit(what, row):
            rows.append(row)
    return rows


def same_column(item: Item, other: Item) -> bool:
    """Two items in one column of the page: their frames overlap sideways by half the narrower one; without frames,
    the same region."""
    a, b = frame_of(item), frame_of(other)
    if a is None or b is None:
        return bool(item.region) and item.region == other.region
    overlap = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    return overlap >= 0.5 * min(a["w"], b["w"])


def column_first(others: list[tuple[tuple[int, int], Item]], fitting: list[Item]) -> list[tuple[tuple[int, int], Item]]:
    """Fill-in candidates for a collect, in screen order, those in the column or region of an item that fits first:
    a link beside the list beats one in a sidebar."""
    if not fitting:
        return list(others)
    return sorted(others, key=lambda pi: (not any(same_column(pi[1], f) for f in fitting), pi[0]))


def _plain_option(name: str) -> str:
    return match.normalise(NUMBERED.sub("", name) if NUMBERED.match(name) else name)


EXACT = "by name"  # how an item whose own name is the step's words was picked: never a picking problem
FOLLOW_S = 0.4  # KARTA_FOLLOW=1: the pause before a click that was not an exact match, so a person can follow it


def spot(item: Item) -> tuple:
    """One item among several of the same name (a grid of "Add" buttons): its name, kind, place and row, and its
    frame when the env gives one."""
    return (item.name, item.kind, item.region, item.row, getattr(item.ref, "centre", None))


REGION_GRID = ("top-left", "top", "top-right", "left", "middle", "right", "bottom-left", "bottom", "bottom-right")


def belongs(control: Item, box: Item) -> bool:
    """`control` is part of the form `box` is in: both in a dialog (a send button of the page behind never sends a
    dialog's text, nor a dialog's the page's), in one row, in one region, or beside it."""
    if box.dialog or control.dialog:
        return box.dialog and control.dialog
    if (box.row and control.name in box.row) or (control.row and box.name in control.row):
        return True
    if not (box.region and control.region) and (frame_of(control) is None or frame_of(box) is None):
        return True  # where they are is unknown: nothing says it is elsewhere
    return next_region(control.region, box.region) or beside(control, box)


def next_region(a: str, b: str) -> bool:
    """The same region of the screen, or the one next to it across an edge ("top" and "top-right"; never corners
    apart): a form's control may sit just over a region's line from its field."""
    if not a or not b:
        return False
    cell = {r: (n // 3, n % 3) for n, r in enumerate(REGION_GRID)}
    (ra, ca), (rb, cb) = cell.get(a, (-9, -9)), cell.get(b, (9, 9))
    return abs(ra - rb) + abs(ca - cb) <= 1


TOGGLE_WORDS = 6  # a toggle's own label is short; a list row's checkbox carries the row's whole text


def names_toggle(name: str, item: Item) -> bool:
    """A checkbox or toggle a fill names: its name read by match.named, or holding the asked name as whole words in a
    short label ("Time" -> "Time zone", "signed in" -> "Keep me signed in"); never a list row's checkbox that merely
    holds the word ("to" in a message's title)."""
    want = match.normalise(bare(name))
    have = match.normalise(item.name)
    if not want:
        return False
    if code_named(name, [item]) is not None or (have + " ").startswith(want + " "):
        return True
    return len(have.split()) <= TOGGLE_WORDS and f" {want} " in f" {have} "


def following() -> bool:
    return os.environ.get("KARTA_FOLLOW", "").strip().lower() in ("1", "true", "yes", "on")


def one_way(item: object) -> bool:
    """A control pressed exactly one way, never a second after it: one that sends, posts, pays, deletes or runs
    (`risk.risky`), commits a form or answers (`risk.commits`: a default button, Next, Save ...), or is a key action
    (add to cart, star, follow)."""
    name = getattr(item, "name", "")
    if not isinstance(name, str) or not name:
        return False
    return bool(risk.risky("click", name) or risk.commits(item) or any(rule.search(name) for _kind, rule in risk.KEY_RULES))


@dataclass
class Press:
    """The last item a click picked and pressed: what it was for, how it was chosen, and the screen just before."""

    target: str
    near: str
    item: Item
    how: str
    before: Screen
    p: float | None = None  # Laya's probability for it, when Laya chose it


@dataclass
class Toolbox:
    """The tools, bound to one run: the env, Laya, this Mac's facts, memory, and what the run learned so far."""

    task: str
    env: HarnessEnv
    laya: object  # karta.harness.decide.Laya
    facts: MacFacts
    memory: object | None = None  # karta.harness.memory.HarnessMemory
    confirm: Callable[[str], bool] = _never
    notes: object = field(default_factory=SessionNotes)  # karta.harness.memory.SessionNotes: failures, in RAM
    tabs: list | None = None  # facts.Tab: the browsers' open tabs at task start (hosts compared only)
    upcoming: Callable[[], list[dict]] = lambda: []  # the plan's next steps, as far as the brain has written them
    trace: list[str] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    places: dict[str, Place] = field(default_factory=dict)  # what -> the place that opened it, reused all run
    last_place: Place | None = None  # the place that worked last: where a drifted run returns
    aliases: dict[str, tuple[str, str]] = field(default_factory=dict)  # a name said -> (app, the name it turned out to be)
    pending: dict[str, str] = field(default_factory=dict)  # text written into a field, not submitted yet -> the field
    echo: set[str] = field(default_factory=set)  # text typed at the focus: it echoes as plain text
    answered: set[str] = field(default_factory=set)  # dialog answers the brain planned, already given
    actions: int = 0
    laya_seconds: float = 0.0
    last_write: tuple[str, str] | None = None  # (field name, text) of the last write
    answered_at: int = -1  # the action count when a dialog was last answered
    front: str = ""  # the app in front at the last look
    here: str = ""  # where the last look was, as memory keys it (memory_key): the site, else the native app
    elements: list[tuple[str, str, str, bool, bool]] = field(
        default_factory=list
    )  # (where, intent, name, accessible, chrome) picked
    used: list[tuple[str, str, str, str]] = field(default_factory=list)  # remembered facts the current step used
    read_tabs: Callable[[], list] | None = None  # re-reads the browsers' open tabs now (None: `tabs` is the latest)
    last_choice: Choice | None = None  # the Laya choice behind the last pick (None: code picked it)
    submitted: str | None = None  # the control that sent the last write (cleared by the next write)
    by_type: bool = False  # the step running declares its kind of change (v2): the loop verifies it (expect.py)
    last_disabled: Item | None = None  # the disabled control the last pick named
    boxes: dict[str, str] = field(default_factory=dict)  # app -> the search box found there this run
    signed_in: list[str] = field(default_factory=list)  # place names the Mac map knows worked (so signed in then)
    last_press: Press | None = None  # the item the current step's click pressed (retry.py undoes and replaces it)
    wrong: dict[tuple[str, str], set] = field(default_factory=dict)  # (target, near) -> spots clicked and found wrong
    instead_of: str = ""  # the item found wrong just before this pick (shown with the next pick)
    slots: dict[str, list[Collected]] = field(default_factory=dict)  # what each collect step took, by its name
    planned: set[str] = field(default_factory=set)  # the names collect steps were run for (kept or not)
    last_slot: str = ""  # the name the latest collect kept its items under
    slot_pages: dict[str, str] = field(default_factory=dict)  # name -> the page (address) its items were read on
    redo: set[str] = field(default_factory=set)  # names whose items did not answer a tell: collected again, replaced
    moved_on_pages: set[str] = field(default_factory=set)  # web pages this task scrolled: read where they are now
    seen: dict[str, Screen] = field(default_factory=dict)  # app -> its last read: the page before opening another
    left_behind: dict[str, Screen] = field(default_factory=dict)  # app -> the page it showed before the last opening
    visited: dict[str, list[str]] = field(default_factory=dict)  # app -> the web addresses it showed, in turn (go_back)
    pages_told: set[tuple[str, int]] = field(default_factory=set)  # (question, page text) a tell asked once already
    contract: object = None  # what the task must achieve (contract.Contract), the run's: a tell checks its answer by it
    unmet: str = ""  # why the contract's last check did not hold ("" once it does)
    reads: int = 0  # screens read
    first_read: dict[tuple, int] = field(default_factory=dict)  # a field or dialog item at its place -> when first read
    # what the current step left for a local repair (repair.py): the field it typed into last and how, the pick Laya
    # was unsure of, and a fill's progress. Cleared as each step starts.
    last_put: dict | None = None  # {"box", "text", "shown", "ahead", "tried"}: the last typing into a field
    last_unsure: tuple[str, list[Item], Choice] | None = None  # (intent, the options, Laya's ranking) of a pick it did not make
    filled: list[str] = field(default_factory=list)  # the fields the current fill has filled so far
    fill_at: str = ""  # the field (as the step names it) where the current fill stopped
    # places by evidence (open_place): the browser the user is using and the default one, the moves made this task,
    # the question for the user once MAX_MOVES are spent, and whether the step running came from a brain rescue
    using: str | None = None  # the browser in front when the task started (None: another app was)
    default_browser: str | None = None  # LaunchServices' (facts.MacMap.default_browser); None: the map's first
    moves: int = 0
    must_ask: str = ""
    from_rescue: bool = False
    place_proof: str = ""  # what try_place's last result rests on (see try_place)
    sent_on: Place | None = None  # the host the last place tried was sent on to (a redirect), now the place
    approved: dict[int, str] = field(default_factory=dict)  # id(step) -> the key action the user said yes to up front
    step: dict | None = None  # the step running
    # compose and consent (harness.md, "Compose", "Consent"): the brain that composes, the texts composed by name, the
    # composed names typed and not sent yet (a send then reads them out and asks, whatever was said up front), the
    # screens each name's text was read on, the consent answers given this task, and what the end line says first
    brain: object | None = None  # karta.harness.brain.HarnessBrain
    composed: dict[str, str] = field(default_factory=dict)
    unsent: dict[str, str] = field(default_factory=dict)
    heard_for: int | None = None  # id(step) of the send whose composed text the user heard and said yes to
    slot_screens: dict[str, list[Screen]] = field(default_factory=dict)
    consents: dict[str, bool] = field(default_factory=dict)
    sources: dict[str, tuple[str, bool | None]] = field(default_factory=dict)  # a host or app -> source(), asked once
    can_ask: bool = True  # the user can be asked (False: a run with no one to answer: a no, never kept)
    notices: list[str] = field(default_factory=list)
    structure_only: set[str] = field(default_factory=set)  # the sources whose screens went to the brain as structure
    # delegate (harness.md, "Delegate"): the repo the session delegated to last, each delegate step's resolved repo
    # (asked up front by it), and the Claude Code sessions started (watched by a background job: app.py)
    recent_repo: str = ""
    repos: dict = field(default_factory=dict)  # id(step) -> Path
    launched: list = field(default_factory=list)  # delegate.Launched

    # ---------------------------------------------------------------- plumbing

    def run(self, step: dict) -> ToolResult:
        args = {k: v for k, v in step.items() if k not in ("tool", "then", "expects")}  # the expectation is the loop's
        self.step = step
        self.last_press = None
        self.used = []
        self.last_put, self.last_unsure, self.filled, self.fill_at = None, None, [], ""
        self.by_type = bool(step.get("expects"))
        tool = getattr(self, "tool_" + str(step.get("tool")), None)
        if tool is None:
            return ToolResult(Outcome.NOT_FOUND, f"unknown tool {step.get('tool')!r}")
        try:
            return tool(**args)
        except (ValueError, KeyError, TypeError) as e:  # a bad key name or argument fails the step, never the run
            self.trace.append(f"    {step.get('tool')} could not be done: {e}")
            return ToolResult(Outcome.UNEXPECTED, f"could not do it: {e}")
        except Abort:
            raise  # the user's abort corner stops the run, always
        except Exception as e:  # any other failure in a tool fails this step honestly, never the whole run
            import traceback

            self.trace.append(f"    {step.get('tool')} raised {type(e).__name__}: {e}")
            self.trace.extend("      " + line for line in traceback.format_exc().splitlines()[-6:])
            return ToolResult(Outcome.UNEXPECTED, f"an internal error in {step.get('tool')}: {type(e).__name__}")

    def look(self) -> Screen:
        scr = self.env.screen()
        self.front = scr.app
        self.here = memory_key(scr, self.facts.browsers)
        self.seen[scr.app] = scr
        trail = self.visited.setdefault(scr.app, [])
        if scr.url and (not trail or trail[-1] != scr.url):
            trail.append(scr.url)
        self.reads += 1
        for i in scr.items:
            if i.kind == "field" or i.dialog:
                self.first_read.setdefault(self._spot_key(i), self.reads)
        return scr

    def do(self, what: str, fn: Callable, *args) -> bool:
        """Run one action; False when the env refused it or saw it go wrong (`env.refused`: the focus was not where
        the text or key should go), so a chain of keys stops there instead of going on blind. A click on a control
        that may send, pay or commit (`one_way`) goes to the env's `click_once`, when it has one: exactly one way of
        pressing it, never a second after it."""
        self.actions += 1
        self.trace.append(f"    do: {what}")
        if fn == self.env.scroll and (seen := self.seen.get(self.front)) is not None and seen.url:
            self.moved_on_pages.add(_page_of(seen.url))  # read where the task scrolled it, never from its top again
        once = getattr(self.env, "click_once", None)
        if fn == self.env.click and callable(once) and args and one_way(args[0]):
            fn = once
        if isinstance(getattr(self.env, "pressed", None), str):
            self.env.pressed = ""  # how this action was done, never the last key's
        fn(*args)
        pressed = getattr(self.env, "pressed", "")
        if isinstance(pressed, str) and pressed and pressed != "key" and fn != self.env.click:
            self.trace.append(f"    by: {pressed}")
        refused = getattr(self.env, "refused", "")
        if isinstance(refused, str) and refused:
            self.trace.append(f"    not done: {refused}")
            return False
        return True

    def ask(self, method: str, *args) -> Choice:
        started = time.perf_counter()
        choice: Choice = getattr(self.laya, method)(*args)
        self.laya_seconds += time.perf_counter() - started
        self.decisions.append(
            {
                "decision": choice.decision,
                "ranked": [(o, round(p, 2)) for o, p in choice.ranked[:4]],
                "gate": choice.gate,
                "sure": choice.sure,
                "ms": round(choice.ms),
            }
        )
        best = f"{choice.best!r} p={choice.ranked[0][1]:.2f}" if choice.ranked else "(no options)"
        self.trace.append(f"  {choice.decision} -> {best}{'' if choice.sure else ' UNSURE'}")
        return choice

    def acts(self, choice: Choice | None, level: str = "reversible") -> bool:
        """Laya's verdict for an action of this risk (decide.Laya.acts); Choice.sure where it has none."""
        if choice is None:
            return True  # code decided it
        fn = getattr(self.laya, "acts", None)
        if callable(fn):
            try:
                return bool(fn(choice, level))
            except Exception:
                pass
        return choice.sure

    # ---------------------------------------------------------------- waiting for the screen

    def pause(self, seconds: float) -> None:
        """A wait for the screen: the real env sleeps and re-reads; the role-played one advances its own time."""
        self.do(f"wait {seconds:g} s for the screen", self.env.wait, seconds)

    def hold(self, seconds: float) -> bool | None:
        """Up to `seconds` for the screen to change: the env's own change wait when it has one (the real Mac returns
        as soon as the pixels move and settle), else a fixed pause. True/False: it changed or not; None: paused."""
        watch = getattr(self.env, "wait_change", None)
        if not callable(watch):
            self.pause(seconds)
            return None
        started = time.perf_counter()
        changed = bool(watch(seconds))
        self.trace.append(
            f"    waited {time.perf_counter() - started:.2f} s for the screen"
            + ("" if changed else f" (no change in {seconds:g} s)")
        )
        return changed

    def settle(self, before: str, timeout: float = CHANGE_TIMEOUT) -> Screen | None:
        """After an action: the screen once it differs from `before`, waiting up to `timeout` (the env's own poll
        when it has one (MacHarnessEnv.changed_since); else re-reads with backoff); None when it never did."""
        poll = getattr(self.env, "changed_since", None) or getattr(self.env, "_changed_since", None)
        if callable(poll):
            try:
                return self.look() if poll(before, timeout) else None
            except Exception:
                pass
        scr = self.look()
        if scr.fingerprint() != before:
            return scr
        spent = 0.0
        for delay in RETRY_DELAYS:
            if spent + delay > timeout:
                break
            self.hold(delay)
            spent += delay
            scr = self.look()
            if scr.fingerprint() != before:
                return scr
        return None

    def rechecks(self, scr: Screen):
        """Re-reads while a target or result is not there yet: each new screen, for as long as the screen keeps
        changing or shows it is loading. An env that can wait for a change re-reads
        as soon as the screen changes, within the same total time as the backoff (RETRY_DELAYS) it replaces; any
        other waits out the backoff."""
        last = scr.fingerprint()
        waiting = loading(scr)
        for _ in self._rewaits():
            now = self.look()
            moved = now.fingerprint() != last
            if not moved and not waiting and not loading(now):
                return
            last, waiting = now.fingerprint(), loading(now)
            yield now

    def _rewaits(self):
        """The waits behind rechecks: RETRY_DELAYS as fixed pauses, or with a change wait, each delay at most and
        until their sum has passed."""
        if not callable(getattr(self.env, "wait_change", None)):
            for delay in RETRY_DELAYS:
                self.pause(delay)
                yield
            return
        deadline = time.perf_counter() + sum(RETRY_DELAYS)
        for n in range(RECHECKS):
            left = deadline - time.perf_counter()
            if left <= 0:
                return
            self.hold(min(RETRY_DELAYS[min(n, len(RETRY_DELAYS) - 1)], left))
            yield

    def floor(self, choice: Choice) -> float:
        """The reversible floor of the decision (decide.Laya.gate): an unsure answer above it is worth a check."""
        fn = getattr(self.laya, "gate", None)
        if callable(fn):
            try:
                return float(fn(choice.decision))
            except Exception:
                pass
        return choice.gate

    def gate(self, action: str, target: str, item: Item | None = None) -> bool:
        """An action that is hard to undo needs the user's yes. True when it may go ahead: the user said yes now, or
        before the task started to this very step taking this kind of action (`approved`). The risk floor's words are
        English, so on a screen in another language it fails closed (`foreign_risk`)."""
        kind = risk.risky(action, target)
        if kind is None:
            kind = self.foreign_risk(action, target, item)
        if kind is None:
            return True
        if self.unsent and kind.split(" ")[0] in ("send", "post", "comment", "submit", "risky"):
            return self.heard()  # composed text goes out only once it was read out: never on the up-front yes
        if self.step is not None and self.heard_for == id(self.step):
            self.trace.append(f"  {kind}: {action} {target!r}: the user heard the composed text and said yes")
            return True
        if self.step is not None and self.approved.get(id(self.step)) == kind:
            self.trace.append(f"  {kind}: {action} {target!r}: the user said yes to it before the task started")
            return True
        question = f"{kind}: {action} {target!r} (task: {self.task})"
        self.confirmations.append(question)
        yes = bool(self.confirm(question))
        self.trace.append(f"  confirm {question} -> {'yes' if yes else 'no'}")
        return yes

    def heard(self) -> bool:
        """Before a send holding composed text: the text itself, whole, then "Send it?" (said aloud in voice mode,
        printed when typed), whatever the user said up front. A yes covers this send step only."""
        from karta.harness.tell import composed_line

        question = composed_line("\n\n".join(self.unsent.values()))
        self.confirmations.append(question)
        yes = bool(self.confirm(question))
        self.trace.append(
            f"  confirm the composed text ({', '.join(f'{{{k}}}' for k in self.unsent)}) -> {'yes' if yes else 'no'}"
        )
        if yes:
            self.unsent.clear()
            self.heard_for = id(self.step) if self.step is not None else None
        return yes

    # ---------------------------------------------------------------- consent: mail and messages to the brain

    def screens_of(self, slots) -> list[Screen]:
        """The screens the text under these names was read on (a composed name: its sources')."""
        return [scr for slot in dict.fromkeys(slots) for scr in self.slot_screens.get(slot, [])]

    def source(self, scr: Screen) -> tuple[str, bool | None]:
        """Where a screen's text comes from, as consent keys it (a web page's host, else the app), and whether it is a
        mail or messaging app's: a native app LaunchServices registered for mailto:, sms: or imessage:
        (facts.message_apps), or a web page Laya is sure is a mailbox or a conversation (D2's private kind, under
        the risky tier either way); None when Laya is unsure (it asks)."""
        host = _host_of(scr.url or "")
        if not host:
            return scr.app, scr.app in facts_mod.message_apps()
        if host in self.sources:
            return self.sources[host]
        if not callable(getattr(self.laya, "private_screen", None)):  # a Laya without the question cannot tell
            return host, False
        choice = self.ask("private_screen", scr)
        private = None
        if choice.ranked and choice.best == PRIVATE and self.acts(choice):
            private = True
        elif choice.ranked and choice.best == NOT_PRIVATE and self.acts(choice, "risky"):
            private = False
        self.sources[host] = (host, private)
        return host, private

    def withheld(self, screens: list[Screen]) -> list[str]:
        """The sources among `screens` whose text may not go to the brain: a mail or messaging app (or a page Laya is
        unsure of) the user said no to. The user is asked once per source ("May I send text from <app> to Claude to
        summarise it?", by voice in voice mode); the answer is kept in memory (kind consent) and asked again only
        once forgotten."""
        out: list[str] = []
        for scr in screens:
            label, private = self.source(scr)
            if private is False or label in out or not label:
                continue
            if not self.consented(label):
                out.append(label)
        return out

    def for_brain(self, scr: Screen) -> Screen:
        """The screen as a prompt may show it (the plan's start, a look, a page call, a rescue, the end check): as it
        is, or flagged `withheld` when it is a mail or messaging app's or page's (`source`) and the user has not said
        yes. Asked once first, the same question and the same kept row as a tell's (`withheld`); a no, or no one to
        ask, sends its structure only (view.Structure)."""
        refused = self.withheld([scr])
        if not refused:
            return scr
        if refused[0] not in self.structure_only:
            self.structure_only.add(refused[0])
            self.trace.append(f"  {refused[0]}: no yes to send its text to Claude: its structure only")
        return replace(scr, withheld=True)

    def consented(self, label: str) -> bool:
        if label in self.consents:
            return self.consents[label]
        kept = getattr(self.memory, "consent", None)
        answer = kept(label) if callable(kept) else None
        if answer is None:
            question = CONSENT_ASK.format(app=label)
            self.confirmations.append(question)
            answer = bool(self.confirm(question)) if self.can_ask else False
            self.trace.append(f"  consent: {question} -> {'yes' if answer else 'no'}")
            keep = getattr(self.memory, "set_consent", None)
            if callable(keep) and self.can_ask:
                keep(label, answer)
        self.consents[label] = answer
        return answer

    def titles(self, slot: str) -> list[str]:
        """What a name holds, titles only (consent said no): a row's title, a text's window title, never a value."""
        screens = self.slot_screens.get(slot) or []
        out = []
        for c in self.slots.get(slot) or []:
            text = len(c.value) >= MIN_PAGE_TEXT and not c.titled
            out.append(next((s.window for s in screens if s.window), c.title) if text else c.title)
        return [t for t in dict.fromkeys(out) if t]

    def facts_for(self, slots, refused: list[str]) -> list[str]:
        """The facts the brain gets from these names: each item's text, or its title only when its source is one
        the user said no to."""
        out: list[str] = []
        for slot in slots:
            labels_of = {self.source(s)[0] for s in self.slot_screens.get(slot, [])} if refused else set()
            out.extend(self.titles(slot) if labels_of & set(refused) else [c.text() for c in self.slots.get(slot) or []])
        return out

    def foreign(self) -> str:
        """The language of the screen in front when it is not English ("de"), else "": the risk floor reads English."""
        scr = self.seen.get(self.front)
        lang = scr.lang if scr is not None else ""
        return "" if not lang or lang == "en" else lang

    def foreign_risk(self, action: str, target: str, item: Item | None) -> str | None:
        """On a screen in another language the risk floor's words cannot tell, so it fails closed: Return in any field
        but a search field or the browser's own sends ("send"); a click or an answer asks unless Laya is sure, under the
        risky tier, that it does not send, pay, buy, delete or post (D4); a search control never asks."""
        lang = self.foreign()
        verb = action.strip().split(" ")[0].lower()
        if not lang or verb not in risk.ACTING:
            return None
        if item is not None and (item.search or item.chrome):
            return None
        if verb in ("press", "submit"):
            return "send" if risk.ENTER.search(action) or verb == "submit" else None
        if callable(getattr(self.laya, "pick_ok", None)):
            shown = item if item is not None else Item(target, "button")
            choice = self.ask("pick_ok", "send, pay, buy, delete or post something", shown)
            if choice.ranked and choice.best == DOES_OTHER and self.acts(choice, "risky"):
                return None
        return f"risky (the screen is in {lang}: its words are not read)"

    def note(self, kind: str, key: str, value: str, reason: str, app: str | None = None) -> None:
        """A failure of this task, kept in RAM for the rescue prompt and to steer Laya away from it."""
        add = getattr(self.notes, "add", None)
        if callable(add):
            add(kind, self.front if app is None else app, key, value, reason)

    def tried(self, kind: str, key: str, app: str | None = None) -> set[str]:
        fn = getattr(self.notes, "tried", None)
        return set(fn(kind, self.front if app is None else app, key)) if callable(fn) else set()

    def demote(self, kind: str, app: str, key: str, value: str, reason: str) -> None:
        """A remembered fact failed: deleted on disk at once (memory.failed) and noted in RAM; the note alone without
        memory. It is no longer one the current step used."""
        self.used = [u for u in self.used if u != (kind, app, key, value)]
        if kind == "place" and value in self.signed_in:
            self.signed_in = [p for p in self.signed_in if p != value]
        failed = getattr(self.memory, "failed", None)
        if callable(failed):
            failed(kind, app, key, value, reason)
            self.trace.append(f"  memory: {kind} {key!r} -> {value!r} forgotten ({reason})")
        else:
            self.note(kind, key, value, reason, app)

    def remembered(self, kind: str, app: str, key: str, value: str) -> None:
        """The current step uses a fact memory gave: forgotten if the step then fails (forget_used)."""
        if (kind, app, key, value) not in self.used:
            self.used.append((kind, app, key, value))

    def forget_used(self, reason: str) -> None:
        """The step failed: every remembered fact it used (an element, an alias, a place) is deleted at once."""
        for kind, app, key, value in list(self.used):
            self.demote(kind, app, key, value, f"its step failed: {reason}" if reason else "its step failed")
        self.used = []

    def shortlist(self, query: str, items: list[Item], k: int = TARGETS) -> list[Item]:
        return items if len(items) <= k else screen_mod.shortlist(query, items, self.laya.embed, k)

    def page_fields(self, scr: Screen) -> list[Item]:
        """Fields on the page, by name; never the address bar or other chrome (Screen.fields)."""
        return [f for f in scr.fields() if not URLISH.match(f.name.strip())]

    def field_named(self, scr: Screen, name: str, near: Item | None = None) -> Item | None:
        """The field called `name` on this screen (never merely the focused one); among several, the one at `near`'s
        place (its frame, else its region), then the copy the focus is in or nearest to (`one_of`)."""
        fields = [f for f in scr.items if f.kind == "field" and not f.menu and norm(f.name) == norm(name)]
        if len(fields) > 1 and near is not None:
            at = frame_of(near)
            same = [f for f in fields if at is not None and frame_of(f) == at]
            fields = same or ([f for f in fields if f.region == near.region] if near.region else []) or fields
        return self.one_of(fields, scr) if fields else None

    def one_of(self, copies: list[Item], scr: Screen) -> Item:
        """Among copies of one control (two compose windows open), the one to act in: the focused one, else the one
        nearest to the focused item (the same window), else the newest (first read last: the window just opened),
        else the first."""
        if len(copies) == 1:
            return copies[0]
        focused = [c for c in copies if c.focused]
        if focused:
            return focused[0]
        focus = next((i for i in scr.page() if i.focused), None)
        at = getattr(focus.ref, "centre", None) if focus is not None else None
        if at is not None and all(getattr(c.ref, "centre", None) is not None for c in copies):
            return min(copies, key=lambda c: math.dist(at, c.ref.centre))
        if focus is not None and focus.region:
            here = [c for c in copies if c.region == focus.region] or [c for c in copies if next_region(c.region, focus.region)]
            if len(here) == 1 or (here and len(here) < len(copies)):
                return here[0]
        ages = [self.first_read.get(self._spot_key(c), 0) for c in copies]
        return copies[ages.index(max(ages))] if len(set(ages)) > 1 else copies[0]

    @staticmethod
    def _spot_key(item: Item) -> tuple:
        f = frame_of(item)
        return (item.name, item.kind, tuple(round(v) for v in f.values()) if f else None)

    def one_each(self, items: list[Item], scr: Screen) -> list[Item]:
        """`items` with copies of one name and kind reduced to the one to act in (one_of), in screen order."""
        groups: dict[tuple, list[Item]] = {}
        for i in items:
            groups.setdefault((norm(i.name), i.kind), []).append(i)
        keep = {id(self.one_of(g, scr)) for g in groups.values()}
        return [i for i in items if id(i) in keep]

    def same_field(self, scr: Screen, name: str) -> Item | None:
        return self.field_named(scr, name)

    # ---------------------------------------------------------------- choosing: code first, then option elimination

    def eliminate(
        self,
        kind: str,
        intent: str,
        pool: list[Item],
        question: Callable[[list[Item]], Choice],
        check_first: bool,
        sure_stands: bool = False,
    ) -> tuple[Item | None, Choice | None]:
        """Laya over `pool`; its pick is checked (the pick check; a sure first answer of a question that needs no pre-
        check stands, the step's outcome checks it); a pick that is unsure or fails the check is removed, noted, and
        Laya asked again over what is left, ROUNDS at most. Options that failed earlier this session go unasked."""
        failed = self.tried(kind, intent)
        pool = [i for i in pool if i.name not in failed] or list(pool)
        last: Choice | None = None
        for n in range(ROUNDS):
            if not pool:
                break
            choice = question(pool)
            last = choice
            if not choice.ranked or (choice.sure and choice.best == NONE):  # a sure "none of these"
                break
            ranked = [(item_for(label, pool), p) for label, p in choice.ranked if label != NONE]
            top, p1 = next(((i, p) for i, p in ranked if i is not None), (None, 0.0))
            if top is None:
                break
            if n == 0 and choice.sure and (sure_stands or not check_first):
                return top, choice
            if exact_name(intent, top):  # Laya chose among copies of the exact name: its pick check never vetoes it
                return top, choice
            worth = choice.sure or p1 >= self.floor(choice) or not check_first  # a result or an answer: always checked
            if worth and affirms(self.ask("pick_ok", intent, top), DOES_IT):
                return top, choice
            why = (
                "its pick check failed"
                if choice.sure
                else "unsure, and its pick check failed"
                if worth
                else "unsure, below the floor"
            )
            self.note(kind, intent, top.name, why)
            self.trace.append(f"  eliminated {top.name!r} ({why}); asking again over {len(pool) - 1}")
            pool = [i for i in pool if i is not top]
        return None, last

    def near_options(self, item: Item, items: list[Item]) -> list[str]:
        """The enabled options beside a disabled control: same popup, same row or same region."""

        def close(i: Item) -> bool:
            if item.dialog and i.dialog:
                return True
            if (item.row and i.name in item.row) or (i.row and item.name in i.row):
                return True
            return bool(item.region) and i.region == item.region

        pool = [i for i in items if i is not item and i.enabled and not i.menu and not i.chrome and i.kind in OPTION_KINDS]
        near = [i for i in pool if close(i)] or pool
        return list(dict.fromkeys(i.name for i in near))[:NEAR_OPTIONS]

    def disabled(self, item: Item, items: list[Item]) -> ToolResult:
        near = self.near_options(item, items)
        return ToolResult(Outcome.DISABLED, f"{item.name!r} is disabled" + (f"; enabled options near it: {near}" if near else ""))

    def pick(self, intent: str, items: list[Item], sure_stands: bool = False) -> tuple[Item | None, ToolResult]:
        """The item `intent` means: Laya's target question, then its pick check. Code first (its exact name, then match.named), memory, then Laya by
        option elimination. `sure_stands`: a sure first answer stands with no pick check (an unsure one is still
        checked), for a pick whose own step verifies it."""
        self.last_choice = None
        self.last_disabled = None
        if not items:
            return None, ToolResult(Outcome.NOT_FOUND, f"nothing on screen for {intent!r}")
        named = [i for i in items if exact_name(intent, i)]
        if not named:
            hit = code_named(intent, items)
            named = [hit] if hit is not None else []
        usable = [i for i in named if i.enabled]
        if len(usable) == 1:  # its own name (exact), or match.named's reading of the words (close)
            return self.picked(intent, usable[0], EXACT if exact_name(intent, usable[0]) else "code: named alike")
        if named and not usable:
            self.last_disabled = named[0]
            return None, self.disabled(named[0], items)
        known = self.memory.element_for(self.here, intent) if self.memory and self.here else None
        remembered = [i for i in items if known and i.enabled and i.name == known]
        if len(remembered) == 1:  # the item this intent meant here before
            self.remembered("element", self.here, intent, known)
            return self.picked(intent, remembered[0], "remembered")
        pool = [i for i in (usable or items) if i.enabled]
        if not pool:
            return None, ToolResult(Outcome.NOT_FOUND, f"everything like {intent!r} is disabled")
        cands = self.shortlist(intent, pool)
        item, choice = self.eliminate("element", intent, cands, lambda p: self.ask("target", intent, p), True, sure_stands)
        if item is None:
            why = Outcome.NOT_FOUND if choice is None or choice.sure else Outcome.UNSURE
            if choice is not None:
                self.last_unsure = (intent, cands, choice)
            return None, ToolResult(why, f"no sure pick for {intent!r}" + (f" (last best {choice.best!r})" if choice else ""))
        self.last_choice = choice
        return self.picked(intent, item, "Laya")

    def picked(self, intent: str, item: Item, how: str) -> tuple[Item, ToolResult]:
        if self.here:  # kept only once the task ends proven (loop.remember); a browser with no page keeps nothing
            self.elements.append((self.here, intent, item.name, "ax" in item.source, item.chrome or item.browser_ui))
        return item, ToolResult(Outcome.OK, f"{item.name!r} ({how})")

    def reject_element(self, intent: str, item: Item, how: str, reason: str) -> None:
        """The item picked for `intent` did nothing: never picked again this session; forgotten if memory gave it."""
        self.elements = [e for e in self.elements if e[1:3] != (intent, item.name)]
        if how == "remembered":
            self.demote("element", self.here, intent, item.name, reason)
        else:
            self.note("element", intent, item.name, reason)

    # ---------------------------------------------------------------- typing safely

    def put(self, box: Item, text: str, shown: str | None = None) -> ToolResult:
        """Replace a field's text, only in that field re-found on the CURRENT screen by its name, then check that
        this same field holds the text; never type when it is gone. `shown`: how the trace and the result name the
        text (a {name} the text was filled from: what was collected stays out of the logs). What it tried is kept in
        `last_put` for a local repair (repair.py)."""
        said = repr(shown if shown is not None else text)
        now: Item | None = None
        tried: list[str] = []
        for attempt in range(2):
            ahead = self.look()
            target = self.field_named(ahead, box.name, box)
            if target is None:
                return ToolResult(Outcome.NO_FIELD, f"{box.name!r} is gone from the screen: nothing typed")
            if attempt:
                self.do(f"click {target.name!r}", self.env.click, target)
            self.do(f"replace the text in {target.name!r} with {said}", self.env.set_text, target, text)
            tried.append("click, paste" if attempt else "paste")
            self.last_put = {"box": target, "text": text, "shown": shown, "ahead": ahead, "tried": list(tried)}
            now, token = self.landed(ahead, target, text)
            took = self.took(target, now, token, text, said)
            if took is not None:
                return took
            box = target
            if now is None:
                break
        held = ("holds other text" if shown is not None else f"holds {now.value!r}") if now is not None else "is gone"
        return ToolResult(Outcome.UNEXPECTED, f"typed {said} but {box.name!r} {held}")

    def landed(self, ahead: Screen, target: Item, text: str) -> tuple[Item | None, Item | None]:
        """After typing `text` into `target` (`ahead`: the screen just before): (the field now, re-found by name, as the
        focused one that renamed itself, or as the field drawn at its very spot; a token that took the text, or None),
        by the one shared check (`facts.text_landed`), re-read while the field may take a moment to show it."""

        def read(scr: Screen) -> tuple[Item | None, Item | None]:
            now = self.field_named(scr, target.name, target) or self.renamed(scr, text) or self.at_spot(scr, target)
            if now is None:
                return None, None
            shown = facts_mod.text_landed(now, text, ahead, scr)
            return now, (shown if shown is not None and shown is not now else None)

        def took(now: Item | None, token: Item | None) -> bool:
            return token is not None or (now is not None and holds(now.value, text))

        scr = self.look()
        now, token = read(scr)
        if not took(now, token):
            for later in self.rechecks(scr):
                now, token = read(later)
                if took(now, token):
                    break
        return now, token

    def took(self, target: Item, now: Item | None, token: Item | None, text: str, said: str) -> ToolResult | None:
        """OK when the field holds the text, or a token in or beside it took it (never typed again: that would make a
        second token); None when neither."""
        if token is not None:
            self.trace.append(f"  {target.name!r} emptied itself and {token.name!r} appeared in it: a token")
            return ToolResult(Outcome.OK, f"{target.name!r} took {said} as a token")
        if now is not None and holds(now.value, text):
            if norm(now.name) != norm(target.name):
                self.trace.append(f"  {target.name!r} renamed itself to {now.name!r} once typed into: the same box")
                if self.last_put is not None:
                    self.last_put["name"] = now.name  # what a submit finds the box by now
            return ToolResult(Outcome.OK, f"{now.name!r} holds {said}")
        return None

    def at_spot(self, scr: Screen, box: Item) -> Item | None:
        """The one field drawn where `box` was (its centre inside it): a field that renamed itself once typed into (a
        recipient box whose name lists its tokens). None without frames, or when several are there."""
        if frame_of(box) is None:
            return None
        there = [f for f in scr.items if f.kind == "field" and not f.menu and facts_mod.inside(box, f)]
        return there[0] if len(there) == 1 else None

    def renamed(self, scr: Screen, text: str) -> Item | None:
        """The field just typed into, when it renamed itself (a search box's placeholder name goes once it holds text):
        the one focused field holding exactly `text`."""
        mine = [f for f in scr.items if f.kind == "field" and not f.menu and f.focused and norm(f.value) == norm(text)]
        return mine[0] if len(mine) == 1 else None

    def not_exact(self, scr: Screen) -> str | None:
        """Before sending: the field written last must hold exactly the text written, nothing more (a message typed
        twice is never sent). Why not, or None."""
        if not self.last_write or self.last_write[0] == "address bar":
            return None
        box = self.field_named(scr, self.last_write[0])
        if box is None or not (box.value or "").strip():
            return None
        held, meant = norm(box.value), norm(self.last_write[1])
        if "\n" in box.value.strip():
            # a box of several lines may hold more by nature (a signature, a quoted reply): what was written (expanded
            # from what was collected, when it was) must be in it once, never twice
            if "\n" in self.last_write[1].strip() and held.count(meant) > 1:
                return f"not sending: {box.name!r} holds the message {held.count(meant)} times"
            return None
        if held != meant and meant in held:  # more than the message (typed twice, or added to); an app shortening it
            return f"not sending: {box.name!r} holds more than the message ({len(box.value)} characters for {len(self.last_write[1])})"
        return None

    def return_key(self, what: str, sends: bool = True) -> bool:
        """Return, one way when it `sends` (or may: a message, a form, a terminal's command): the key alone. What sends
        nothing (a search, a web address) may take the env's `confirm_or_return`: AXConfirm, then the key."""
        either = getattr(self.env, "confirm_or_return", None)
        if not sends and callable(either):
            return self.do(what, either)
        return self.do(what, self.env.press, "return")

    def return_into(self, box: Item, text: str, sends: bool = True) -> bool:
        """Press return only into `box`: it is still on screen, holds `text`, and has the focus (clicked once if
        another field has it). `sends`: as return_key."""
        scr = self.look()
        target = self.field_named(scr, box.name, box)
        if target is None or not holds(target.value, text):
            self.trace.append(
                f"  not pressing return: {box.name!r} " + ("is gone" if target is None else f"holds {target.value!r}")
            )
            return False
        if not target.focused and any(f.focused for f in scr.fields()):
            self.do(f"click {target.name!r}", self.env.click, target)
            target = self.field_named(self.look(), box.name, box)
            if target is None or not target.focused:
                self.trace.append(f"  not pressing return: {box.name!r} does not take the focus")
                return False
        return self.return_key(f"press return in {target.name!r}", sends)

    def address_holds(self, text: str) -> bool:
        """Before return in the address bar: the focused field of the browser's chrome, when the read shows one with a
        value, holds what was typed (its host: a browser may hide the scheme). Only a value that says otherwise
        stops it; the focus itself was checked as the text went in."""
        focus = next((i for i in self.look().items if i.kind == "field" and i.chrome and i.focused), None)
        if focus is None or not (focus.value or "").strip():
            return True
        want = re.sub(r"^\w+://", "", text.strip()).split("/")[0].lower()
        if want in focus.value.lower():
            return True
        self.trace.append("  not pressing return: the address bar holds something else")
        return False

    def without_typing(self, scr: Screen) -> Screen:
        """The page minus fields and our own typing (unsent text, lines typed at the focus), for end-state questions."""
        mine = [norm(t) for t in [*self.pending, *typed_lines(self.echo)] if norm(t)]
        keep = [i for i in scr.page() if i.kind != "field" and not any(t in norm(f"{i.name} {i.value}") for t in mine)]
        return Screen(scr.app, keep, scr.url, scr.window, scr.profile)

    def outcome_since(self, expected: str, scr: Screen, before: Screen | None) -> Choice | None:
        """Laya's outcome question over only what is new on `scr` since `before` (facts.since), our typing left out:
        a line that was on screen already never answers it. None when nothing outside the fields is new (then
        nothing shows it)."""
        view = self.without_typing(facts_mod.since(scr, before))
        if before is not None and not view.items and not view.window:
            return None
        return self.ask("outcome", expected, view)

    def as_found(self, text: str) -> str:
        """`text` with each name the user said replaced by the one it turned out to be ("Marisal" -> "Marisol Okafor"):
        the brain wrote the end state in the user's words, the screen shows the real name."""
        for said, (_app, meant) in self.aliases.items():
            if said.strip() and norm(said) not in norm(meant):
                text = re.sub(rf"(?<!\w){re.escape(said.strip())}(?!\w)", lambda _m, meant=meant: meant, text, flags=re.I)
        return text

    def typed(self) -> set[str]:
        """Everything we typed that may echo on screen: unsent field text and each line typed at the focus."""
        return set(self.pending) | typed_lines(self.echo)

    def dialog(self, scr: Screen | None = None) -> list[Item] | None:
        return facts_mod.blocking_dialog(scr or self.look())

    # ---------------------------------------------------------------- dialogs in the way

    def clear_dialog(self, step: dict | None) -> ToolResult | None:
        """A dialog that blocks the page, in the way of `step` (facts.blocking_dialog: a sheet, a dialog window, a
        modal web dialog; never a panel, which the step acts on as on the page). None when there is none, or when the
        step acts inside it (its target is in the dialog by text, or Laya says the dialog is part of the step). A
        permission prompt takes the user's word: it is known by who owns it (the system's prompter, the browser's own
        dialog), never by its words. A dialog that is not part of the step is closed by its own way out: its cancel or
        close control by structure, else the button Laya is sure closes it agreeing to nothing (way_out), else escape
        when it holds no field of its own. Laya's dialog answer is asked only for a dialog that is part of the task."""
        scr = self.look()
        if facts_mod.system_prompt(scr):  # the system asks for another app: the user answers, whatever the step
            return self.permission(facts_mod.in_the_way(scr), scr)
        own = [i for i in scr.items if i.dialog and i.browser_ui and not i.menu and not i.panel]
        if own and self.front in BROWSER_APPS:  # the browser's own dialog that blocks: hands off, the user answers it
            return self.permission(own, scr)
        dialog = self.dialog(scr)
        if not dialog:
            return None
        tool = (step or {}).get("tool")
        if tool == "answer":
            return None
        pool = list(dict.fromkeys([*dialog, *(facts_mod.modal_items(scr) or dialog)]))
        targets = step_targets(step)
        numbered = [view_mod.resolve(a, scr) for a in step_ats(step)]
        inside = any(i is not None and any(i is p for p in pool) for i in numbered)  # a #n target in the dialog
        if inside or any(t and code_named(t, pool) is not None for t in targets) or self.inside_for(tool, pool):
            self.trace.append("  the step's target is inside the dialog: acting inside it")
            return None
        intent = step_line(step) if step else self.task
        role = self.dialog_role(intent, pool)
        if role == "part":
            if targets or tool in ("find", "submit"):
                self.trace.append("  Laya: the dialog is part of the step: acting inside it")
                return None
            return self.tool_answer(self.task)
        return self.close_dialog(scr, pool)

    def close_dialog(self, scr: Screen, pool: list[Item]) -> ToolResult:
        """A blocking dialog that is not part of the task, closed by its own way out and nothing else: its cancel or
        close control (by structure, else a plain way out by name); its only button when that is a plain answer (an
        acknowledgement: OK, Continue, Got it); else escape, when it has controls but no field of its own (escape
        could drop what a form holds). A notice with no control is left to wait out (the local repair). Never an
        answer picked for the task."""
        controls = [i for i in pool if i.enabled and i.kind in facts_mod.ACTIONABLE]
        buttons = [i for i in controls if i.kind in ("button", "link")]
        names = [i.name for i in controls][:6]
        way_out = facts_mod.dismiss_option(pool) or self.way_out(buttons)
        if way_out is None and len(buttons) == 1 and not risk.risky("click", buttons[0].name):  # its only way on: a notice
            way_out = buttons[0]
        if way_out is not None:
            self.trace.append(f"  an interruption: closing it with {way_out.name!r}")
            self.do(f"click {way_out.name!r}", self.env.click, way_out)
            self.answered_at = self.actions
            if self.settle(scr.fingerprint()) is None:
                self.note("answer", "close the interruption", way_out.name, "the dialog did not close")
                return ToolResult(Outcome.NO_CHANGE, f"clicked {way_out.name!r} but the dialog stayed")
            return ToolResult(Outcome.OK, f"closed an interruption with {way_out.name!r}")
        if not controls or any(i.kind == "field" for i in controls) or not facts_mod.modal_items(scr):
            return ToolResult(Outcome.BLOCKED_BY_DIALOG, f"a dialog with no way out of its own ({', '.join(names)})")
        self.trace.append("  an interruption with no way out by name: escape")
        self.do("press escape (a dialog in the way)", self.env.press, "escape")
        self.answered_at = self.actions
        if self.settle(scr.fingerprint()) is None or self.dialog() is not None:
            return ToolResult(Outcome.BLOCKED_BY_DIALOG, f"escape did not close the dialog ({', '.join(names)})")
        return ToolResult(Outcome.OK, "closed an interruption with escape")

    def inside_for(self, tool: str | None, pool: list[Item]) -> bool:
        """A find whose search box is in the dialog (a command menu); a submit whose field or send button is."""
        if tool == "find":
            return any(i.kind == "field" and (i.search or i.focused) for i in pool)
        if tool in ("write", "fill", "type"):  # a compose window, a form, a detail panel: the typing goes on in it
            return any(i.kind == "field" and i.enabled for i in pool)
        if tool == "submit":  # the field written last is in it, or it has a default button (the one Return presses)
            written = self.last_write[0] if self.last_write else None
            return any((written and i.kind == "field" and norm(i.name) == norm(written)) or i.default for i in pool)
        return False

    def dialog_role(self, intent: str, items: list[Item]) -> str | None:
        """Laya's dialog-role question: "part" (the step goes on in it), "away" (an interruption), None when unsure or absent."""
        if not callable(getattr(self.laya, "dialog_role", None)):
            return None
        choice = self.ask("dialog_role", intent, items)
        if not choice.ranked or not choice.sure:
            return None
        from karta.harness import decide

        best = choice.best
        if best == getattr(decide, "PART_OF_STEP", None) or re.search(r"part of|needed|goes on", best, re.I):
            return "part"
        if best == getattr(decide, "INTERRUPTION", None) or re.search(r"interrupt|close|unasked|in the way", best, re.I):
            return "away"
        return None

    def way_out(self, buttons: list[Item]) -> Item | None:
        """The button that closes an interruption without agreeing to, buying or turning on anything: Laya's sure dialog
        answer (D6) for that, never one whose words agree, pay or grant (facts.NEVER_DISMISS, a safety veto) nor a
        risky one. None when Laya is not sure: then its only button, escape, or the local repair's wait."""
        if len(buttons) < 2 or not callable(getattr(self.laya, "dialog_answer", None)):
            return None
        choice = self.ask("dialog_answer", "close it, agreeing to nothing", buttons)
        item = item_for(choice.best, buttons) if choice.ranked and choice.sure else None
        if item is None or facts_mod.NEVER_DISMISS.search(item.name.lower()) or risk.risky("click", item.name):
            return None
        return item

    def permission(self, dialog: list[Item], scr: Screen) -> ToolResult:
        """A permission prompt (the system's prompter, the browser's own dialog: known by who owns it) always takes the
        user's yes or no, never the brain's planned answer nor Laya's; the user sees its own words. A no is given by
        the prompt's own cancel control (by structure), else the narrowest option that says no; a yes by the narrowest
        option that says yes, else the one other button beside the cancel control. With no such option, nothing is
        clicked: fail closed in any language."""
        options = [i for i in dialog if i.enabled and not i.menu]
        buttons = [i for i in options if i.kind in ("button", "link")]
        said = [i.name for i in dialog if i.kind in facts_mod.PROSE] or [i.name for i in options]
        what = words(" / ".join(said), 16)
        question = f"permission: {scr.app} asks: {what} (task: {self.task}). Allow it?"
        self.confirmations.append(question)
        yes = bool(self.confirm(question))
        self.trace.append(f"  asked the user: {question} -> {'yes' if yes else 'no'}")
        by_name = {_plain_option(i.name): i for i in options}
        cancel = next((i for i in buttons if i.cancels), None)
        if yes:
            item = next((by_name[o] for o in ALLOW if o in by_name), None)
            others = [i for i in buttons if i is not cancel]
            if item is None and cancel is not None and len(others) == 1:
                item = others[0]
        else:
            item = cancel or next((by_name[o] for o in DENY if o in by_name), None)
        if item is None:
            return ToolResult(
                Outcome.NEEDS_PERMISSION if yes else Outcome.REFUSED,
                f"a permission prompt: no option says {'allow' if yes else 'no'} among {[o.name for o in options][:6]}",
            )
        return self.choose_option(item, scr, f"the user's {'yes' if yes else 'no'} to a permission prompt")

    def choose_option(self, item: Item, scr: Screen, why: str) -> ToolResult:
        number = NUMBERED.match(item.name)
        if number and item.kind not in ("button", "link"):  # a terminal's numbered choice
            if not self.do(f"type {number.group(1)!r}", self.env.type_at_focus, number.group(1)):
                return ToolResult(Outcome.NO_FIELD, f"could not type {number.group(1)!r} at the focus ({why})")
            if not self.do("press return", self.env.press, "return"):
                return ToolResult(Outcome.NO_FIELD, f"typed {number.group(1)!r} but return was not pressed ({why})")
        else:
            self.do(f"click {item.name!r}", self.env.click, item)
        self.answered_at = self.actions
        if self.settle(scr.fingerprint()) is None:
            return ToolResult(Outcome.NO_CHANGE, f"chose {item.name!r} ({why})")
        return ToolResult(Outcome.OK, f"chose {item.name!r} ({why})")

    # ---------------------------------------------------------------- open_place

    def tool_open_place(self, what: str, ready_shows: str = "", site: str = "") -> ToolResult:
        """`site` is the website the brain names for it; code checks it is host-shaped (facts.site_url), the site
        catalog is only a hint, and memory keeps the place that worked. Places are tried in the order the evidence
        gives (facts.rank_by_evidence); a place that opened is left only on proof (a login page, a page that failed to
        load, an app with no account set up), at most MAX_MOVES times a task; anything else is settled in place."""
        what = words(what)
        if site and facts_mod.site_url(site) is None:
            self.trace.append(f"  {site!r} is not a web host: ignored")
            site = ""
        found = self.found_place(what, site)
        if found is not None:
            # found once: the place is done and locked for the rest of the task. Never the ladder again (no
            # other profile, browser or reload): back to that very place when the screen wandered, and work from there
            return self.bring_back(found, what)
        hint = self.memory.place_for(what) if self.memory else None
        places = list(facts_mod.places_for(what, self.task, self.facts, hint, site or None, self.tabs))
        locked = self.locked_web()
        if self.from_rescue and locked is not None:  # a rescue never moves to another browser or profile
            places = self.stay_in(locked, places, site)
            if not places:
                return ToolResult(
                    Outcome.WRONG_PLACE,
                    f"{locked.name} was found for this task: a rescue never moves to another browser or profile",
                )
            if places == [locked]:
                return self.bring_back(locked, what)
        if not places:
            return ToolResult(Outcome.NOT_INSTALLED, f"nothing installed serves {what!r}, and no website was named for it")
        places = self.rank_places(what, places, hint)
        misses: list[ToolResult] = []
        empty: Place | None = None  # an app open with nothing set up: better than a login page everywhere
        why = ""  # why the last place was left: the reason line of the next move
        for place in places:
            if misses:  # leaving the last place for this one: a move, only on proof, at most MAX_MOVES a task
                if self.moves >= MAX_MOVES:
                    self.must_ask = (
                        f"I moved {MAX_MOVES} times looking for a signed-in place for {what!r} ({why}). "
                        f"Where should I open it, or should I stop?"
                    )
                    self.trace.append(f"  {MAX_MOVES} moves made: not moving to {place.name}; the user decides")
                    break
                self.moves += 1
                self.moved(place, why)
            from_memory = place.tab is None and (place.name == hint or place.name in self.signed_in)
            self.sent_on = None
            result, verdict = self.place_verdict(what, ready_shows, place, reuse=not misses)
            place = self.sent_on or place  # sent on to another host that is no login page: that host is the place
            if verdict == "unsure":  # the place opened and shows no proof against it: settled here, never left
                result, verdict = self.settle_in(what, ready_shows, place, result)
            if result.ok:
                self.places[what] = self.last_place = place
                if from_memory:
                    self.remembered("place", "", what, place.name)
                return result
            if verdict == "stay":  # still not ready after a wait and the site's root in the same tab: reported, kept
                self.places[what] = self.last_place = place
                return result
            misses.append(result)
            if result.outcome is Outcome.NOT_SET_UP and empty is None:
                empty = place
            if from_memory:
                self.demote("place", "", what, place.name, result.outcome.value)
            else:
                self.note("place", what, place.name, result.outcome.value, app="")
            if verdict == "stop":
                break  # the right place with something in the way: the rescue can answer it
            why = verdict
        if not misses:
            return ToolResult(Outcome.NOT_INSTALLED, f"nothing installed serves {what!r}")
        others = {m.outcome for m in misses} - {Outcome.NOT_SET_UP}
        if empty is not None and not self.must_ask and others <= {Outcome.LOGIN_PAGE, Outcome.WRONG_PLACE, Outcome.NOT_INSTALLED}:
            if not self.showing(empty, self.look()):
                self.open(empty)
            self.trace.append(f"  every other place failed: back to {empty.name}, open though nothing is set up")
            self.places[what] = self.last_place = empty
            return ToolResult(Outcome.OK, f"{empty.name}: open (nothing set up; the only usable place)")
        outcomes = {m.outcome for m in misses}
        outcome = Outcome.LOGIN_PAGE if outcomes == {Outcome.LOGIN_PAGE} else misses[-1].outcome
        detail = "; ".join(m.detail for m in misses)
        return ToolResult(outcome, detail + (f"; {MAX_MOVES} moves made, the user decides" if self.must_ask else ""))

    def bring_back(self, found: Place, what: str) -> ToolResult:
        """The place found earlier in this task, brought back to the front (its tab, found by its address)."""
        if not self.showing(found, self.look()):
            self.trace.append(f"  {found.name} was found already: back to it, nowhere else")
            self.arrive(found, self.look())
        self.places[what] = self.last_place = found
        if self.showing(found, self.look()):
            return ToolResult(Outcome.OK, f"{found.name}: open already (found earlier in this task)")
        return ToolResult(Outcome.WRONG_PLACE, f"{found.name} was found earlier but could not be brought back to front")

    def locked_web(self) -> Place | None:
        """The browser place found in this task (the place lock), the last one found; None before any."""
        return next((p for p in reversed(list(self.places.values())) if p.app in self.facts.browsers), None)

    def stay_in(self, locked: Place, places: list[Place], site: str) -> list[Place]:
        """A rescue's places, kept to the locked browser and profile: an app that is no browser stays; the locked
        browser named as an app is the locked place itself; a site goes to the locked browser and profile."""
        kept: list[Place] = []
        for p in places:
            if p.app not in self.facts.browsers:
                kept.append(p)
            elif p.app == locked.app and p.url is None:
                kept.append(locked)
            elif p.app == locked.app and (p.tab is not None or p.profile == locked.profile):
                kept.append(p)
        url = facts_mod.site_url(site) if site else None
        if url and not any(p.url and facts_mod.on_host(p.url, url) for p in kept):
            kept.append(facts_mod.site_place(url, locked.app, locked.profile))
        dropped = [p.name for p in places if p not in kept]
        if dropped:
            self.trace.append(f"  a rescue stays in {locked.name}: not {', '.join(dropped[:4])}")
        return list(dict.fromkeys(kept))

    def moved(self, place: Place, why: str) -> None:
        """One reason line per move, in the trace, the terminal and the HUD."""
        where = place.app + (f" {place.profile}" if place.profile else "")
        line = f"moved to {where}: {why}"
        self.trace.append(f"  {line}")
        events.emit("move", text=line)

    def place_verdict(self, what: str, ready_shows: str, place: Place, reuse: bool = True) -> tuple[ToolResult, str]:
        """Try `place`, and say what the result proves: "ok"; "stop" (a dialog, a permission or the user's no in the
        way: the right place); a reason to leave it (it did not open, a login page, a page that failed to load, an app
        with no account set up); or "unsure" (it opened and nothing proves it is the wrong place)."""
        result = self.try_place(what, ready_shows, place, reuse)
        if result.ok:
            return result, "ok"
        if result.outcome in (Outcome.BLOCKED_BY_DIALOG, Outcome.NEEDS_PERMISSION, Outcome.REFUSED):
            return result, "stop"
        where = place.profile or place.app
        if self.place_proof == "unreached":
            return result, f"{place.name} did not open"
        if result.outcome is Outcome.LOGIN_PAGE:
            return result, f"login page in {where}"
        if result.outcome is Outcome.NOT_SET_UP:
            return result, f"nothing set up in {where}"
        return result, "unsure"

    def settle_in(self, what: str, ready_shows: str, place: Place, first: ToolResult) -> tuple[ToolResult, str]:
        """A place that opened, with no proof against it (not ready, "something else", an unsure Laya): wait and
        re-read; then the site's root address in the same tab (a leftover view of the right site: a compose or
        settings page). Left only when that shows a login page or a page that failed to load; else ("stay") the
        failure is reported and the place kept."""
        self.trace.append(f"  {place.name} opened ({first.detail}): settled here, not left without proof")
        self.hold(LOAD_WAIT)
        result, verdict = self.judge(what, ready_shows, place)
        if verdict == "unsure" and place.url:
            self.open_root(place)
            result, verdict = self.judge(what, ready_shows, place)
        if verdict == "unsure":
            return ToolResult(
                result.outcome, f"{result.detail}; still not ready after a wait and its root address (no login or error page)"
            ), "stay"
        return result, verdict

    def judge(self, what: str, ready_shows: str, place: Place) -> tuple[ToolResult, str]:
        """The place in front judged again, without opening it again (a login page is proof)."""
        where = place.profile or place.app
        state = self.ready(what, ready_shows, place)
        if state.ok:
            return ToolResult(Outcome.OK, f"{place.name}: {state.detail} (settled in place)"), "ok"
        result = ToolResult(state.outcome, f"{place.name}: {state.detail}")
        if state.outcome is Outcome.LOGIN_PAGE:
            return result, f"login page in {where}"
        if state.outcome in (Outcome.BLOCKED_BY_DIALOG, Outcome.NEEDS_PERMISSION, Outcome.REFUSED):
            return result, "stop"
        return result, "unsure"

    def open_root(self, place: Place) -> None:
        """The site's root address, typed into the address bar of the tab in front (never a new tab or window)."""
        parts = urlsplit(place.url or "")
        self.open_address(f"{parts.scheme or 'https'}://{parts.netloc}/")

    def open_address(self, url: str) -> Screen | None:
        """`url` typed into the address bar of the tab in front and opened there; the screen once the page is on its
        site (re-read while it loads), else None."""
        before = self.look()
        if not (
            self.do("focus the address bar", self.env.press, "cmd+l")
            and self.do(f"type {url} into the address bar", self.env.type_at_focus, url)
            and self.address_holds(url)
            and self.return_key("open it in this tab", sends=False)
        ):
            return None
        scr = self.settle(before.fingerprint()) or self.look()
        if on_site(scr, url):
            return scr
        return next((later for later in self.rechecks(scr) if on_site(later, url)), None)

    def go_back(self, what: str = "go back") -> Screen | None:
        """Back to the page before, proven by its address: the browser's back, and when the address did not move (a
        link that opened a new tab has no page before it) the address before typed into this tab. The screen there,
        or None. A native app (no address) is back once its screen changed."""
        before = self.look()
        trail = self.visited.get(before.app, [])
        here = (before.url or "").split("#")[0]
        prior = next((u for u in reversed(trail) if u.split("#")[0] != here), None) if here else None
        self.do(what, self.env.back)
        after = self.settle(before.fingerprint())
        if not here:
            return after
        now = after or self.look()
        if (now.url or "").split("#")[0] != here:
            return now
        if prior is None:
            return None
        self.trace.append(f"  back did not leave {_host_of(here)}: opening the page before by its address")
        return self.open_address(prior)

    def found_place(self, what: str, site: str = "") -> Place | None:
        """The place a done open_place found for the same app or site ("Pebble", "Pebble app", site pebble.example)."""
        apps = [] if site else self.named_apps(what)

        def other_app(place: Place) -> bool:  # the lock never stands in for another app the task itself names
            return bool(apps) and place.url is None and place.app not in apps

        if what in self.places and not other_app(self.places[what]):
            return self.places[what]
        host = _host_of(facts_mod.site_url(site) or "") if site else ""
        for key, place in self.places.items():
            if other_app(place):
                continue
            if (host and place.url and _host_of(place.url) == host) or norm(key) in norm(what) or norm(what) in norm(key):
                return place
        return None

    def named_apps(self, what: str) -> list[str]:
        """The installed apps (no browser) both `what` and the task name, the longest name winning (facts.named_apps)."""
        apps = [a for a in self.facts.installed_apps if a not in self.facts.browsers]
        in_task = set(facts_mod.named_apps(self.task.lower(), apps))
        return [a for a in facts_mod.named_apps(what.lower(), apps) if a in in_task]

    def try_place(self, what: str, ready_shows: str, place: Place, reuse: bool = True) -> ToolResult:
        """Open `place` and judge it. `place_proof` says what the result rests on: "unreached" (it never came to the
        front on its site: a page that failed to load is the browser's own address, never the site's), or "" (it opened). `reuse`: a place
        already in front is used as it is; after a move it is opened all the same (the page in front is the place
        just left, and a window's profile is not always known)."""
        self.place_proof = "unreached"
        scr = start = self.look()
        old = self.page_before(place)  # showing the site's address, a page may still be the one left behind
        if not (reuse and self.showing(place, scr)):  # a named app already up is used, never re-opened elsewhere
            scr = self.arrive(place, scr)
        if scr.app != place.app:
            return ToolResult(Outcome.WRONG_PLACE, f"{place.name}: {scr.app} is in front")
        if place.url and not on_site(scr, place.url) and not facts_mod.is_login_page(scr):  # a login elsewhere: ready says so
            if not redirected(start, scr):
                return ToolResult(Outcome.WRONG_PLACE, f"{place.name}: the page is still on {_host_of(scr.url or '')!r}")
            if place.tab is not None or not re.match(r"^https?://", scr.url or "", re.I):
                # a tab switch that landed on another site (tabs move), or the browser's own error page: never reached
                return ToolResult(Outcome.WRONG_PLACE, f"{place.name}: landed on {_host_of(scr.url or '')!r}, not the site")
            # sent on to another host right after opening: judged by the page it landed on, which is the place from
            # now on; a sign-in only when that page shows one (a password field, or Laya sure, in `ready`)
            landed = facts_mod.site_place(f"https://{_host_of(scr.url or '')}/", place.app, place.profile)
            self.trace.append(f"  {place.name}: sent on to {_host_of(scr.url or '')!r}: judged by the page it landed on")
            self.sent_on = place = landed
        if place.url and old is not None and still_old(scr, old):
            return ToolResult(Outcome.WRONG_PLACE, f"{place.name}: the address changed but the page still shows the last one")
        self.place_proof = ""
        state = self.ready(what, ready_shows, place)
        if not state.ok:
            return ToolResult(state.outcome, f"{place.name}: {state.detail}")
        if self.reads_content() and self.empty_place(self.look()):  # open, but nothing here to read
            if place.url:  # a site that opened with no login page is signed in: an empty view is still the place
                return ToolResult(Outcome.OK, f"{place.name}: {state.detail} (signed in; it looks empty)")
            return ToolResult(Outcome.NOT_SET_UP, f"{place.name}: open, but not set up or empty")
        return ToolResult(Outcome.OK, f"{place.name}: {state.detail}")

    def arrive(self, place: Place, before: Screen) -> Screen:
        """Open `place` and return the first read of it. A web page counts as arrived only once the page itself
        changed: the pixels moved from the page before (the env's change wait, bounded), the address is on the site
        (`on_site`), and the content is no longer the last page's (`still_old`): the address bar and the document's own
        address change before the page does. Re-reads (`rechecks`) while it has not; the caller judges what is left."""
        old = self.page_before(place)
        if old is not None:
            self.left_behind[place.app] = old
        self.open(place)
        scr = self.settle(before.fingerprint()) or self.look()
        if not place.url:
            return scr

        def there(s: Screen) -> bool:
            return on_site(s, place.url) and not still_old(s, old)

        if not there(scr):
            for later in self.rechecks(scr):
                scr = later
                if there(scr):
                    break
        return scr

    def page_before(self, place: Place) -> Screen | None:
        """The page the place's app showed last, when it was another site: what a stale read would still show."""
        old = self.seen.get(place.app)
        if old is None or not place.url:
            return None
        if not on_site(old, place.url):
            return old
        left = self.left_behind.get(place.app)  # the last read is on the site but still showed the page left behind
        return left if left is not None and still_old(old, left) else None

    def reads_content(self) -> bool:
        """The plan goes on to find something that should already be there (an empty place is fine for creating)."""
        return any(s.get("tool") == "find" for s in self.upcoming())

    def rank_places(self, what: str, places: list[Place], hint: str | None) -> list[Place]:
        """The places by evidence (facts.rank_by_evidence: an open tab, memory, an app, the browser with a tab on the
        site or the one in use, the default browser, the rest); places that failed this session go last."""
        failed = self.tried("place", what, app="")
        default = self.default_browser or next(iter(self.facts.browsers), None)
        ranked = facts_mod.rank_by_evidence(places, hint, self.signed_in, self.using, default)
        self.trace.append(f"  places by evidence: {'; '.join(p.name for p in ranked[:4])}" + (" ..." if len(ranked) > 4 else ""))
        fresh = [p for p in ranked if p.name not in failed]
        return fresh + [p for p in ranked if p.name in failed]

    def showing(self, place: Place, scr: Screen) -> bool:
        """The place is in front: its app, its profile, and for a site an address on that site (never words)."""
        if scr.app != place.app:
            return False
        if place.profile and scr.profile and scr.profile != place.profile:
            return False
        if place.url:
            return on_site(scr, place.url)
        return True

    def open_tab(self, place: Place):
        """The tab open on the place's site in its browser (and profile, when known) now: found by its address in the
        open tabs re-read at this moment (`read_tabs`), never by a position kept from before (tabs move). None when
        no tab is on the site any more."""
        if self.read_tabs is not None:
            try:
                self.tabs = list(self.read_tabs() or [])
            except Exception as error:  # the last tabs read stand
                self.trace.append(f"  open tabs not re-read: {error}")
        for tab in self.tabs or []:
            if tab.browser != place.app or (place.profile and tab.profile and tab.profile != place.profile):
                continue
            if place.url and facts_mod.on_host(tab.url, place.url):
                return tab
        return None

    def open(self, place: Place) -> None:
        tab = self.open_tab(place) if place.tab is not None and callable(getattr(self.env, "focus_tab", None)) else None
        if tab is not None:
            where = f"{place.app} window {tab.window_index}, tab {tab.tab_index}"
            self.do(f"switch to {where}", self.env.focus_tab, place.app, tab.window_index, tab.tab_index)
        elif place.url:
            where = place.app + (f", profile {place.profile}" if place.profile else "")
            self.do(f"open {place.url} in {where}", self.env.open_url, place.url, place.app, place.profile)
        else:
            self.do(f"open {place.app}", self.env.open_app, place.app)

    def ready(self, what: str, ready_shows: str, place: Place | None = None) -> ToolResult:
        """Facts first, all by code: a blocking dialog (cleared unless the next step acts in it), a login page, the
        ready words (on the page or a field's own name), a page still loading (waited out); then `place` in front
        (its app and profile, a site's address on the site: `showing`) is ready. Laya's page state only when code
        cannot tell (the place is not in front), and only its "ready" passes: any other answer fails the step in
        place, never a reason to leave it (`place_verdict` settles in place)."""
        for _ in range(LOOKS):
            scr = self.look()
            fact = facts_mod.page_fact(scr, ready_shows, set(self.pending))
            if fact not in (Outcome.OK, Outcome.LOGIN_PAGE, Outcome.BLOCKED_BY_DIALOG, Outcome.NEEDS_PERMISSION) and ready_shows:
                want = {w for w in re.findall(r"[a-z0-9]+", ready_shows.lower()) if len(w) > 2}
                if want and any(i.kind == "field" and want <= set(re.findall(r"[a-z0-9]+", i.name.lower())) for i in scr.page()):
                    return ToolResult(Outcome.OK, f"{ready_shows!r} on screen (a field's name)")
            if fact in (Outcome.BLOCKED_BY_DIALOG, Outcome.NEEDS_PERMISSION):  # a dialog or permission ask in the way
                nxt = self.upcoming()
                cleared = self.clear_dialog(nxt[0] if nxt else None)
                if cleared is None:
                    return ToolResult(Outcome.OK, "open, with a dialog the next step acts in")
                if not cleared.ok:
                    return ToolResult(cleared.outcome if cleared.outcome is Outcome.REFUSED else fact, cleared.detail)
                continue
            if fact is Outcome.LOGIN_PAGE:
                return ToolResult(Outcome.LOGIN_PAGE, "a login page")
            if fact is Outcome.OK:
                return ToolResult(Outcome.OK, f"{ready_shows!r} on screen")
            if loading(scr):  # a page still coming is never a failure
                for later in self.rechecks(scr):
                    if not loading(later):
                        break
                if loading(self.look()):
                    return ToolResult(Outcome.UNEXPECTED, "still loading after waiting")
                continue
            if place is not None and self.showing(place, scr):
                if self.asks_sign_in(scr):
                    if self.fills_here(scr):  # the plan signs in here (a code, an account): the page it needs
                        return ToolResult(Outcome.OK, "a sign-in page the next step types into: the place")
                    return ToolResult(Outcome.LOGIN_PAGE, "a login page (Laya, sure)")
                where = _host_of(scr.url or "") or scr.app
                return ToolResult(Outcome.OK, f"open on {where}: loaded, no login page, nothing in the way")
            choice = self.ask("page_state", what, scr)
            if not choice.sure:  # an unsure "ready" is not ready
                return ToolResult(Outcome.UNSURE, f"page state unsure ({choice.ranked[:1]})")
            if choice.best == LOADING:
                self.hold(LOAD_WAIT)
                continue
            if choice.best == LOGIN and native(scr):  # a native app is a login page only by its password field
                rest = [(k, p) for k, p in choice.ranked if k != LOGIN]
                if rest and rest[0][0] == READY:
                    return ToolResult(Outcome.OK, f"Laya: {READY} (its login verdict ignored: a native app, no password field)")
            if choice.best == READY:
                return ToolResult(Outcome.OK, f"Laya: {READY}")
            # a login page, a dialog or another place are code's facts, checked above: Laya's word is no proof of them
            return ToolResult(Outcome.UNEXPECTED, f"Laya: {choice.best} (no proof on the page: not left for it)")
        return ToolResult(Outcome.UNEXPECTED, "still not ready after waiting")

    def fills_here(self, scr: Screen) -> bool:
        """The plan's next step types into a field this page shows (by the step's words or its #n item): a page that
        asks to sign in is then the one the task needs (a one-time code, an account to sign in with), never proof
        to leave it. Laya's sure kind of screen only: a password field stays code's login fact (facts.page_fact)."""
        nxt = self.upcoming()
        step = nxt[0] if nxt else None
        if not step or step.get("tool") not in TYPING_TOOLS:
            return False
        fields = [i for i in scr.page() if i.kind == "field" and not i.search]
        if any(i is not None and any(i is f for f in fields) for i in (view_mod.resolve(a, scr) for a in step_ats(step))):
            return True
        return any(t and code_named(t, fields) is not None for t in step_targets(step))

    def asks_sign_in(self, scr: Screen) -> bool:
        """A page with no password field that still asks you to sign in before you can use it (an account field, sign-in
        buttons only, a code to scan): meaning, so Laya's kind of screen (D2), taken only when it is sure under the
        risky tier. A weak signal by design: a site's header that merely offers "Log in" must never move a place. Never
        for a native app (no web address): it asks to sign in only with a password field, read by structure."""
        if not callable(getattr(self.laya, "screen_kind", None)) or native(scr):
            return False  # a native app is a login page only by its password field (facts.is_login_page)
        choice = self.ask("screen_kind", scr)
        return bool(choice.ranked) and choice.best == LOGIN and self.acts(choice, "risky")

    def empty_place(self, scr: Screen) -> bool:
        """An app or site open with nothing to read: no content rows (the fact, `facts.content_rows`), and Laya sure the
        page says it has no account set up or nothing listed yet (D7, over the page). Any listed row means usable."""
        if facts_mod.content_rows(scr):
            return False
        choice = self.ask("outcome", "no account set up, or nothing listed yet", self.without_typing(scr))
        return affirms(choice, SHOWN)

    # ---------------------------------------------------------------- place drift

    def drift(self, scr: Screen | None = None) -> str | None:
        """Why the screen is no longer the place that worked last (a login page, another app, another profile),
        or None."""
        place = self.last_place
        if place is None:
            return None
        scr = scr or self.look()
        if facts_mod.system_prompt(scr):  # the system asks for the place's app: answered there, not left for
            return None
        if facts_mod.is_login_page(scr):
            return "a login page appeared"
        if scr.app != place.app:
            return f"{scr.app} is in front, not {place.app}"
        if place.profile and scr.profile and scr.profile != place.profile:
            return f"profile {scr.profile} is in front, not {place.profile}"
        return None

    def return_to_place(self, why: str) -> bool:
        """Back to the place that worked last (its app, profile and site, or its open tab)."""
        place = self.last_place
        if place is None:
            return False
        self.trace.append(f"  drifted ({why}): back to {place.name}")
        scr = self.arrive(place, self.look())
        back = self.showing(place, scr) and not facts_mod.is_login_page(scr)
        if not back:
            self.trace.append(f"  could not get back to {place.name}")
        return back

    # ---------------------------------------------------------------- find

    def tool_find(self, text: str, meaning: str, alternatives: list[str] = (), shortcuts: bool = True) -> ToolResult:
        said = str(text)
        if not self.reads:
            self.look()
        where = self.here  # aliases belong to the site or app the search starts on
        remembered = self.memory.alias_for(said, where) if self.memory and where else None
        texts = list(dict.fromkeys(t for t in [remembered, said, *alternatives] if t and str(t).strip()))
        empty = self.tried("search", meaning)  # searches that found nothing here this session go last
        texts = [t for t in texts if t not in empty] + [t for t in texts if t in empty]
        last = ToolResult(Outcome.NO_RESULTS, "")
        for query in texts:
            box, why = self.search_box(shortcuts)  # re-found for every search: the last one may have closed it
            if box is None:
                return why
            before = self.look()
            typed = self.put(box, query)
            if not typed.ok:
                return typed
            scr, results, said_none = self.await_results(before, query, box)
            if not results and not said_none:  # nothing came up yet: a search that needs return
                sent = self.look().fingerprint()
                if not self.return_into(box, query):
                    return ToolResult(Outcome.NO_FIELD, f"the search box {box.name!r} is gone: return not pressed")
                self.settle(sent)  # the page or its address changes once the search is sent: never judged before
                scr, results, said_none = self.await_results(before, query, box)
            if not results:
                self.trace.append(f"  find {query!r}: no results")
                self.searched_nothing(said, query, remembered, meaning, where=where)
                last = ToolResult(Outcome.NO_RESULTS, f"no results for {query!r}")
                continue
            opened = self.open_result(meaning, query, results, self.candidates(scr, query, box))
            if opened is None:
                self.searched_nothing(said, query, remembered, meaning, "nothing surely matched", where)
                last = ToolResult(Outcome.UNSURE, f"nothing surely {meaning!r} among results for {query!r}")
                continue
            if not opened.ok:
                return opened
            item_name = opened.detail
            self.pending.pop(query, None)  # a search text is not something to send
            if remembered and query == remembered:
                self.remembered("alias", where, said, remembered)
            if norm(said) not in norm(item_name) and where:
                self.aliases[said] = (where, item_name)
            return ToolResult(Outcome.OK, f"opened {item_name!r} (searched {query!r})")
        last.detail = f"nothing matched {meaning!r} for any of {texts}" + (f": {last.detail}" if last.detail else "")
        if last.outcome is Outcome.NO_RESULTS and self.empty_place(self.look()):  # nothing here to find
            return ToolResult(Outcome.NOT_SET_UP, f"{self.front} is open but empty or not set up: {last.detail}")
        return last

    def await_results(self, before: Screen, query: str, box: Item) -> tuple[Screen, list[Item], bool]:
        """The search's results, re-read with backoff while none are there yet: (the screen, the results, whether what
        came only says nothing matched). "No results" is what came saying so (no_results), or nothing ever coming."""
        scr = self.look()
        results = self.results(before, scr, query, box)
        if not results:
            for later in self.rechecks(scr):
                scr = later
                results = self.results(before, scr, query, box)
                if results:
                    break
        if self.no_results(results):
            return scr, [], True
        return scr, results, False

    def searched_nothing(
        self, said: str, query: str, remembered: str | None, meaning: str, why: str = "no results", where: str = ""
    ) -> None:
        if remembered and query == remembered:
            self.demote("alias", where or self.here, said, remembered, why)
        else:
            self.note("search", meaning, query, why)

    def open_result(self, meaning: str, query: str, results: list[Item], everything: list[Item] = ()) -> ToolResult | None:
        """Code first (the one result containing the searched text, among the new results and on the whole page alike),
        then Laya's name match by option elimination; the result is opened and its outcome checked (the screen changed), a dead one
        eliminated too. OK's detail is the name; None when nothing is surely it."""
        tried: set[str] = set()
        hit = code_one_result(query, results)
        if hit is not None and everything and code_one_result(query, list(everything)) is not hit:
            hit = None  # another item on the page contains it too: Laya tells them apart
        for _ in range(ROUNDS):
            pool = [r for r in results if r.name not in tried]
            if not pool:
                return None
            if hit is not None and hit.name not in tried:
                item, how = hit, "the one result containing it"
            else:
                cands = self.shortlist(meaning, pool, 15)
                item, _choice = self.eliminate(
                    "result", meaning, cands, lambda p: self.ask("name_match", meaning, query, p), check_first=False
                )
                how = "Laya"
            if item is None:
                return None
            if not self.gate("open", item.name):
                return ToolResult(Outcome.REFUSED, item.name)
            before = self.look().fingerprint()
            self.do(f"click {item.name!r} ({how})", self.env.click, item)
            if self.settle(before) is not None:
                return ToolResult(Outcome.OK, item.name)
            tried.add(item.name)
            self.note("result", meaning, item.name, "opening it changed nothing")
        return None

    def search_field(self, scr: Screen) -> Item | None:
        known = self.boxes.get(scr.app)
        if known:  # the box that searched here before, whatever it is called ("What do you want to play?")
            again = self.field_named(scr, known)
            if again is not None and again.enabled:
                return again
        fields = [f for f in self.page_fields(scr) if f.search]
        if len(fields) > 1:
            fields = [f for f in fields if f.focused] or fields
        return fields[0] if fields else None

    def search_box(self, shortcuts: bool = True) -> tuple[Item | None, ToolResult]:
        """A visible search box (Item.search); else click the visible search control (a control in the search
        landmark); else, with `shortcuts`, the app's own menu command for ⌘F."""
        box, why = self._search_box(shortcuts)
        if box is not None:
            self.boxes[self.front] = box.name
        return box, why

    def _search_box(self, shortcuts: bool) -> tuple[Item | None, ToolResult]:
        scr = self.look()
        box = self.search_field(scr)
        if box:
            return box, ToolResult(Outcome.OK)
        controls = [i for i in scr.page() if i.kind != "field" and i.enabled and i.search]
        if controls:
            control, _ = (controls[0], None) if len(controls) == 1 else self.pick("the Search button", controls)
            if control is not None:
                self.do(f"click {control.name!r}", self.env.click, control)
                scr = self.settle(scr.fingerprint()) or self.look()
                box = self.search_field(scr) or self.focused_search(scr)
                for later in [] if box else self.rechecks(scr):  # a Search button may load a search page first
                    box = self.search_field(later) or self.focused_search(later)
                    if box:
                        break
                if box:
                    return box, ToolResult(Outcome.OK)
                return None, ToolResult(Outcome.NO_FIELD, f"clicked {control.name!r} but no search box came up")
        if not any(i.kind in ("button", "link", "field", "row", "tab") for i in scr.page()):
            return None, ToolResult(Outcome.NO_FIELD, "a text-only app: no search box (type at the focus instead)")
        finder = next((i for i in scr.items if i.menu and i.enabled and i.shortcut == FIND_KEYS), None)
        if not shortcuts or finder is None:
            return None, ToolResult(Outcome.NO_FIELD, "no search box and no search control on screen")
        self.do(f"click {finder.name!r} (the app's {FIND_KEYS} command)", self.env.click, finder)
        scr = self.settle(scr.fingerprint(), timeout=1.0) or self.look()
        box = self.search_field(scr) or self.focused_search(scr)
        if box:
            return box, ToolResult(Outcome.OK)
        self.do("press escape", self.env.press, "escape")
        return None, ToolResult(Outcome.NO_FIELD, f"{finder.name!r} brought up no search box")

    def focused_search(self, scr: Screen) -> Item | None:
        """The focused field after clicking a search control or the find command, but only one that can be a search box:
        a search field by structure, or empty. A focused document body or message that already holds text is never
        typed over as a search (a note's body was once replaced this way)."""
        for f in self.page_fields(scr):
            if f.focused and (f.search or not f.value.strip()):
                return f
        return None

    def candidates(self, page: Screen, query: str, box: Item) -> list[Item]:
        """Everything on the page a search could have brought up (not fields, the search text, empty-search words)."""

        def echo(i: Item) -> bool:  # the typed query read back by OCR, not a result named like it
            return norm(i.name) == norm(query) and "ocr" in i.source

        return [
            i
            for i in page.page()
            if i.kind != "field"
            and i.enabled
            and norm(i.name) != norm(box.name)
            and not echo(i)
            and not (i.search and i.kind == "button")  # the search box's own controls (go, clear)
        ]

    def results(self, before: Screen, scr: Screen, query: str, box: Item) -> list[Item]:
        """What the search brought up, read from the page's structure: the items that are new since before typing, or,
        when the page only narrowed (a filtered list), what is left of it; nothing when only the search box changed."""
        was, now = self.candidates(before, query, box), self.candidates(scr, query, box)
        seen = {(norm(i.name), i.kind) for i in was}
        new = [i for i in now if (norm(i.name), i.kind) not in seen]
        if new:
            return new
        # the page narrowed (a filtered list), or shows the same list as before (a search already showing, the same
        # list after return): whatever their kind, the items that name what was searched are its results;
        # a sidebar or heading that merely stayed is not one
        return [i for i in now if mentions(query, i.name)]

    def no_results(self, results: list[Item]) -> bool:
        """What a search brought up only says nothing matched: a few new lines (NO_RESULT_LINES at most; real results
        are more, or are rows and links) that Laya is sure say so (D7 over those lines alone). Never by the page's
        words: a song called "Nothing Found" is a result."""
        if not results or len(results) > NO_RESULT_LINES or any(i.kind in ("row", "link") for i in results):
            return False
        choice = self.ask("outcome", "a message that nothing matched the search", Screen(self.front, list(results)))
        return affirms(choice, SHOWN)

    # ---------------------------------------------------------------- click, write, type, fill

    def numbered_item(self, at: dict) -> tuple[Item | None, Screen]:
        """The item a page plan named by number, found again by its identity (view.resolve) on the screen now, re-read
        while the page may still be coming; (None, screen) when it is gone or no longer one item: never a guess."""
        scr = self.look()
        item = view_mod.resolve(at, scr)
        if item is None:
            for later in self.rechecks(scr):
                scr, item = later, view_mod.resolve(at, later)
                if item is not None:
                    break
        return item, scr

    def picked_by_number(self, target: str, item: Item) -> None:
        """A #n pick is the brain's own, exact: memory keeps it as an element only when it is a control (never a
        row, link or text: content, and maybe the user's)."""
        if self.here and item.kind not in ("row", "link", "text", "heading", "image", "other"):
            self.elements.append((self.here, target, item.name, "ax" in item.source, item.chrome or item.browser_ui))

    def click_at(self, target: str, at: dict) -> ToolResult:
        """A click on the item a page plan named by number: that exact item, no Laya pick and no pick check; the risk
        gate as for any click (a send, a payment or a delete asks the user)."""
        self.last_choice, self.last_disabled = None, None
        item, scr = self.numbered_item(at)
        if item is None:
            return ToolResult(Outcome.NOT_FOUND, f"{target!r}: {STALE}")
        if not item.enabled:
            self.last_disabled = item
            return self.disabled(item, scr.page())
        if self.submitted and norm(item.name) == norm(self.submitted) and not self.pending:
            return ToolResult(Outcome.NO_CHANGE, f"{item.name!r} already sent what was written; nothing new to send")
        if not self.gate("click", item.name, item):
            return ToolResult(Outcome.REFUSED, item.name)
        self.picked_by_number(target, item)
        return self.press_item(target, item, EXACT, exact=True)

    def tool_click(self, target: str, near: str = "", at: dict | None = None) -> ToolResult:
        """Click the control `target` names (in the row of `near` when given). Before failing: re-read while the page
        may still be coming, scroll to reveal it, and for a disabled control the option it needs first. `at`: the item
        a page plan named by number (click_at)."""
        if at:
            return self.click_at(target, at)
        target = words(target)
        if ADDRESS_WORDS.search(target) and self.front in BROWSER_APPS:  # browser chrome: its own field (AX, else ⌘L)
            if not self.do("focus the address bar", self.env.press, "cmd+l"):
                return ToolResult(Outcome.NOT_FOUND, "the address bar did not take the focus")
            return ToolResult(Outcome.OK, "the address bar has the focus")
        scr = self.look()
        title = MENU_TITLE.fullmatch(target)
        if title and any(i.menu and norm(i.name).startswith(norm(title[1]) + " ") for i in scr.items):
            # a menu bar title: its commands are read whole ("File > New Window") and clicked directly
            return ToolResult(Outcome.OK, f"the {title[1]} menu's commands are ready to click")
        item, why = self.find_target(target, scr, near)
        if item is None and (why.outcome is Outcome.NOT_FOUND or loading(scr)):
            for later in self.rechecks(scr):
                item, why = self.find_target(target, later, near)
                if item is not None or why.outcome not in (Outcome.NOT_FOUND, Outcome.UNSURE):
                    break
        if item is None and why.outcome is Outcome.NOT_FOUND:
            item, found = self.reveal(target, near)
            if item is None and found is not None:
                return found
        if item is None and why.outcome is Outcome.DISABLED and self.last_disabled is not None:
            item = self.enable_first(target, self.last_disabled)
            if item is None:
                return why
            why = ToolResult(Outcome.OK, f"{item.name!r} (enabled)")
        if item is None:
            return why
        how = why.detail.rsplit("(", 1)[-1].rstrip(")")
        if self.submitted and norm(item.name) == norm(self.submitted) and not self.pending:
            return ToolResult(Outcome.NO_CHANGE, f"{item.name!r} already sent what was written; nothing new to send")
        if risk.risky("click", item.name) and not self.acts(self.last_choice, "risky"):
            return ToolResult(Outcome.UNSURE, f"{item.name!r} cannot be undone and Laya is not sure enough of it")
        if how == "Laya" and risk.commits(item) and not self.acts(self.last_choice, "risky"):
            # a control that commits a form or its step (Next, Continue, Save, Sign in) the step did not name: only
            # on Laya's risky verdict, never on a guess its pick check let through
            return ToolResult(Outcome.UNSURE, f"{item.name!r} commits a form and Laya is not sure enough it is {target!r}")
        if not self.gate("click", item.name, item):
            return ToolResult(Outcome.REFUSED, item.name)
        return self.press_item(target, item, how, near)

    def find_target(self, target: str, scr: Screen, near: str = "") -> tuple[Item | None, ToolResult]:
        wrong = self.wrong.get((norm(target), norm(near)), set())  # clicked for this step and found wrong
        pool = [i for i in scr.page() if (i.kind != "field" or i.enabled) and spot(i) not in wrong]
        if near:
            anchor = (
                code_named(near, pool) or next((i for i in pool if norm(near) in norm(i.name)), None) or most_like(near, pool)
            )
            if anchor is not None:
                mates = [i for i in pool if i is not anchor and ((anchor.name in i.row) or (i.name in anchor.row))]
                if not any(code_named(target, [m]) for m in mates):
                    # a grid of cards has no rows: the control of that name clearly nearest to the item is its own
                    close = nearest_named(target, anchor, pool)
                    if close is not None:
                        return close, ToolResult(Outcome.OK, f"the {target!r} nearest to {anchor.name!r}")
                pool = mates or pool
        head = titled(target, pool)
        if head is not None:
            return self.picked(target, head, "code: a row titled so")
        # an open menu's commands are menu commands too: named only by their exact name, never Laya's guess among them
        pool = [i for i in pool if i.kind != "menu" or exact_name(target, i) or code_named(target, [i]) is not None]
        item, why = self.pick(target, pool)
        if item is None and why.outcome is not Outcome.DISABLED and not near:
            # a menu command is a visible control too, but only one the step names in full or by its exact last part:
            # "Add" near a product is never "Profiles > Add Profile…"
            menus = [i for i in scr.items if i.menu and names_command(target, i.name)]
            if menus:
                item, why = self.pick(target, menus)
        return item, why

    def press_item(self, target: str, item: Item, how: str, near: str = "", exact: bool = False) -> ToolResult:
        """Click and wait for the screen to change (CHANGE_TIMEOUT). A field or search box that shows nothing new is
        fine when the next step types into it; a row that does nothing gets its own button tried (a list's
        Play or Open beside it). The pick is recorded (last_press) and shown (a "pick" event) first."""
        before = self.look()
        self.show_pick(target, item, how, before, near)
        self.do(f"click {item.name!r}", self.env.click, item)
        if self.settle(before.fingerprint()) is not None:
            return ToolResult(Outcome.OK, f"clicked {item.name!r}")
        nxt = self.upcoming()
        if (item.kind == "field" or item.search) and nxt and nxt[0].get("tool") in TYPING_TOOLS:
            return ToolResult(Outcome.OK, f"clicked {item.name!r} (a field: the next step types into it)")
        mates = (
            [
                i
                for i in before.page()
                if i is not item and i.enabled and i.kind in ("button", "link") and (item.name in i.row or i.name in item.row)
            ]
            if (item.kind == "row" or item.row) and not exact  # a #n pick is exactly that item: no other is picked
            else []
        )
        if mates:
            button, _choice = (
                (mates[0], None)
                if len(mates) == 1
                else self.eliminate("element", target, mates, lambda p: self.ask("target", target, p), check_first=True)
            )
            if button is not None and not risk.risky("click", button.name):
                self.do(f"click {button.name!r} (in the row of {item.name!r})", self.env.click, button)
                if self.settle(before.fingerprint()) is not None:
                    return ToolResult(Outcome.OK, f"clicked {button.name!r} in the row of {item.name!r}")
        self.reject_element(target, item, how, "clicking it changed nothing")
        return ToolResult(Outcome.NO_CHANGE, f"clicked {item.name!r}")

    def show_pick(self, target: str, item: Item, how: str, before: Screen, near: str = "") -> None:
        """Record the pick for verify-and-retry and tell the HUD what was picked and how; in follow mode
        (KARTA_FOLLOW=1) a pick that was not an exact match waits FOLLOW_S first, its frame sent for a highlight."""
        choice = self.last_choice if how == "Laya" else None
        p = round(choice.ranked[0][1], 2) if choice is not None and choice.ranked else None
        self.last_press = Press(target, near, item, how, before, p)
        self.trace.append(
            f"  pick: {item.name!r} for {target!r} ({how}"
            + (f", p={p:.2f}" if p is not None else "")
            + (f"; {self.instead_of!r} was wrong" if self.instead_of else "")
            + ")"
        )
        slow = following() and how != EXACT
        data = {"label": item.name, "how": how, "p": p}
        if self.instead_of:
            data["instead_of"] = self.instead_of
        if slow and frame_of(item):
            data["frame"] = frame_of(item)
        events.emit("pick", **data)
        self.instead_of = ""
        if slow:
            time.sleep(FOLLOW_S)

    def reveal(self, target: str, near: str = "") -> tuple[Item | None, ToolResult | None]:
        """Before the brain: scroll to reveal the target. (item, None) when it is on screen now; (None, None) when that
        did not help. Never the page's own search: a click's target is a control's name, and typing it into a search
        box runs a search for it (the find tool searches, when the plan asks)."""
        scrolled = 0
        for _ in range(2):
            before = self.look()
            self.do("scroll down to reveal it", self.env.scroll, "down")
            scr = self.settle(before.fingerprint(), timeout=1.0)
            if scr is None:
                break
            scrolled += 1
            item, _why = self.find_target(target, scr, near)
            if item is not None:
                return item, None
        for _ in range(scrolled):
            self.do("scroll back up", self.env.scroll, "up")
        return None, None

    def enable_first(self, target: str, disabled: Item) -> Item | None:
        """A disabled control needs a choice first: Laya picks the option beside it (then any list
        row) that would enable it, checked; the control is clicked only once code sees it enabled."""
        scr = self.look()
        pool = [
            i
            for i in scr.page()
            if i.enabled
            and not i.menu
            and not i.chrome
            and i.kind in OPTION_KINDS
            and i.kind != "field"
            and not i.search
            and not risk.risky("click", i.name)
        ]
        near = set(self.near_options(disabled, scr.page()))
        ordered = [i for i in pool if i.name in near] + [i for i in pool if i.name not in near and i.kind == "row"]
        intent = f"what to choose first so {words(disabled.name, 4)} works"
        tried: set[str] = set()
        for _ in range(2):
            options = [i for i in ordered if i.name not in tried]
            if not options:
                return None
            option, _choice = self.eliminate(
                "element", intent, self.shortlist(intent, options), lambda p: self.ask("target", intent, p), check_first=True
            )
            if option is None:
                return None
            tried.add(option.name)
            before = self.look()
            self.do(f"click {option.name!r} (so {disabled.name!r} is enabled)", self.env.click, option)
            now = self.settle(before.fingerprint()) or self.look()
            again = next((i for i in now.page() if norm(i.name) == norm(disabled.name) and i.kind == disabled.kind), None)
            if again is not None and again.enabled:
                self.trace.append(f"  {disabled.name!r} is enabled now")
                return again
        return None

    # ---------------------------------------------------------------- collect, and what it carries to later steps

    def keep(self, slot: str, kept: list[Collected], scr: Screen | None = None) -> list[Collected]:
        """Keep a collect's items under `slot`: a name already holding items read on another page, that answered no
        tell wrongly, gets these added after them (two articles read in turn), the same result once; else they
        replace what it held. The latest name collected is remembered (a question's answer is read from it)."""
        page = _page_of(scr.url) if scr is not None and scr.url else ""
        old = self.slots.get(slot) or []
        read_on = [scr if scr is not None else self.seen.get(self.front) or Screen(self.front, [])]
        if old and page and slot not in self.redo and self.slot_pages.get(slot) not in ("", page):
            kept = old + [c for c in kept if not any(same_thing(c, o) for o in old)]
            read_on = self.slot_screens.get(slot, []) + read_on
        self.slot_screens[slot] = read_on
        self.composed.pop(slot, None)
        self.slots[slot] = kept
        self.slot_pages[slot] = page
        self.redo.discard(slot)
        self.last_slot = slot
        return kept

    def collect_rows(self, what: str, rows: list[dict], slot: str) -> ToolResult | None:
        """A page plan's `collect #n, #m`: exactly those items, found again by identity, as they are (a value `what`
        asks for read from each); None when none is on screen any more (then the usual collect reads the page)."""
        scr = self.look()
        found = [i for i in (view_mod.resolve(r, scr) for r in rows if isinstance(r, dict)) if i is not None]
        if not found:
            self.trace.append(f"  collect: the {len(rows)} item(s) chosen by number are gone: reading the page instead")
            return None
        role = content_mod.roles(scr)
        kept = [
            Collected(" ".join(i.name.split()), i.url, wanted_value(what, f"{i.name} {i.value}") or "", role=role.get(id(i), ""))
            for i in dict.fromkeys(found)
        ]
        kept = self.keep(slot, kept, scr)
        shown = "; ".join(c.shown() for c in kept)
        self.trace.append(f"  collect {what!r}: {len(kept)} chosen by number as {{{slot}}}: {shown}")
        fewer = f" ({len(kept)} of the {len(rows)} chosen: the rest are gone)" if len(kept) < len(rows) else ""
        return ToolResult(Outcome.OK, f"{len(kept)} item{'s' if len(kept) != 1 else ''} collected as {{{slot}}}{fewer}: {shown}")

    def from_top(self, scr: Screen) -> Screen:
        """A web page this task did not scroll, scrolled down (a tab left as it was): moved to its top first, so the
        first items of a list are its first, never the viewport's (`env.scroll_top`)."""
        top = getattr(self.env, "scroll_top", None)
        if not scr.url or "#" in scr.url or _page_of(scr.url) in self.moved_on_pages or not callable(top):
            return scr
        if not top():
            return scr
        self.actions += 1
        self.trace.append("  collect: the page was scrolled down: read from its top")
        return self.settle(scr.fingerprint(), timeout=1.0) or self.look()

    def open_own_page(self, what: str, scr: Screen) -> tuple[Screen, Item] | None:
        """A value the results do not show beside the thing named (no price in its row): its own link (the first link
        whose name holds every distinctive word of `what`) opened, so its page can be read; None when there is none."""
        if not asks_value(what) or not topic_words(what) or not scr.url:  # a web page's link: no row of an app opened
            return None
        here = (scr.url or "").split("#")[0]
        link = next(
            (
                i
                for i in scr.page()
                if i.kind == "link" and i.url and not i.offscreen and not same_page(i.url, here) and named_fit(what, i)
            ),
            None,
        )
        if link is None or not self.gate("click", link.name, link):
            return None
        self.trace.append(f"  collect: no {what!r} beside it here: opening {words(link.name, 8)!r} to read it there")
        self.do(f"click {link.name!r}", self.env.click, link)
        after = self.settle(scr.fingerprint())
        return (after, link) if after is not None and after.url != scr.url else None

    def own_reads(self, page: Screen):
        """An item's own page, read as it loads (each re-read while it still changes or shows it is loading), then
        further down: READ_MORE scrolls at most, stopping at its end. The caller stops once it found what it needs."""
        yield page
        for later in self.rechecks(page):
            page = later
            yield page
        for _ in range(READ_MORE):
            before = page.fingerprint()
            self.do("scroll down to read more", self.env.scroll, "down")
            later = self.settle(before, timeout=1.0)
            if later is None:
                return
            page = later
            yield page

    def tool_collect(self, what: str, count: int = 0, slot: str = "items", rows: list | str | None = None) -> ToolResult:
        """Read what the screen shows now and keep the items that are `what` under `slot`, for a later step's {slot}.
        Code takes the candidates (collect_candidates: no ads, no controls; links with their own address). Those that
        fit the topic by code (named_fit) are taken first, in screen order; fewer than `count` of them showing, it
        scrolls to read more, at most READ_MORE times. Only when too few fit do the others fill in: those in the
        column or region of the fitting ones first (a sidebar last), each asked of Laya (D9 collect, under its gate:
        it vetoes). Without count: everything that fits on this screen, up to MAX_COLLECT. Kept in screen order, the
        same result once. A count asked on a web page this task left as it was reads it from its top. "The link of
        this page" is the page's own address. A value not shown beside the thing named is read on its own page (its
        link opened, then back). Nothing else is clicked."""
        slot = str(slot or "items").lower()
        self.planned.add(slot)
        if isinstance(rows, list) and rows:
            chosen = self.collect_rows(what, rows, slot)
            if chosen is not None:
                return chosen
        if callable(events_of := getattr(self.env, "calendar_events", None)) and (
            day := calendar_day(what, self.task, self.look().url)
        ):
            lines = events_of(day)  # None: no access to the calendar, so the screen is read as below
            if lines is not None:
                return self.calendar(lines[: int(count or 0) or None], day, slot)
        if OWN_LINK.search(what) and int(count or 0) <= 1 and (scr := self.look()).url:
            title = scr.window or next((i.name for i in scr.page() if i.kind == "heading"), "") or _host_of(scr.url)
            self.keep(slot, [Collected(" ".join(title.split()), scr.url, role=content_mod.TITLE)], scr)
            self.trace.append(f"  collect {what!r}: the page's own address as {{{slot}}}: {_host_of(scr.url)}")
            return ToolResult(Outcome.OK, f"the page's own address collected as {{{slot}}}: {_host_of(scr.url)}")
        if wants_text(what):
            reader = getattr(self.env, "page_text", None)
            text = (reader() or "") if callable(reader) else ""  # the whole article from the page's AX tree, off-screen too
            if len(text) >= MIN_PAGE_TEXT:
                self.keep(slot, [Collected("the page's text", "", text, role=content_mod.TEXT)], self.look())
                self.trace.append(f"  collect {what!r}: the page's main text, {len(text)} characters, as {{{slot}}}")
                return ToolResult(Outcome.OK, f"the page's main text collected as {{{slot}}}: {len(text)} characters")
            scr = self.look()
            text = window_text(scr) if not scr.url else ""
            if len(text) >= MIN_PAGE_TEXT:  # a native app's window (a message, a note): its text, in screen order
                self.keep(slot, [Collected("the window's text", "", text, role=content_mod.TEXT)], scr)
                self.trace.append(f"  collect {what!r}: the front window's text, {len(text)} characters, as {{{slot}}}")
                return ToolResult(Outcome.OK, f"the front window's text collected as {{{slot}}}: {len(text)} characters")
        want = int(count or 0)
        cap = want or MAX_COLLECT
        taken: list[tuple[tuple, Collected]] = []
        others: list[tuple[tuple[int, int], Item]] = []  # read, not fitting by code, not taken yet: the fill-ins
        fitting: list[Item] = []
        asked: set[str] = set()
        unsure = 0
        own_page: Item | None = None  # the thing's own link, opened to read its value there
        role: dict[int, str] = {}  # each read item's role (content.roles), kept with what is taken
        titles_wanted = bool(TITLE_WORDS.search(what))  # a result's own title fits by structure, as a name by code

        def filled() -> int:  # an accessory for the thing goes after it and takes no place of it
            return sum(1 for (later, *_), _ in taken if not later)

        def take(pos: tuple[int, int], item: Item) -> None:
            row = wanted_value(what, item.value) if MONEY_WANTED.search(what) else None  # a price beside its title
            value = row or wanted_value(what, f"{item.name} {item.value}") or ""
            got = Collected(" ".join(item.name.split()), item.url, value, titled=bool(row), role=role.get(id(item), ""))
            for n, (at, had) in enumerate(taken):  # the same result twice (a thumbnail and its title): once, titled
                if same_thing(got, had):
                    if len(_core(got.title)[0]) > len(_core(had.title)[0]) and not _core(got.title)[1]:
                        taken[n] = (at, got)
                    return
            later = bool(row) and bool(topic_words(what)) and self.accessory(what, item)  # a case for it, after it
            taken.append(((later, *pos), got))

        def fill_in() -> None:
            """The others in, column first, unless Laya is sure an item is something else (the page's structure
            stands): Laya alone said yes to a site's own nav link, so they wait until the reading is done."""
            nonlocal unsure
            ranked = column_first(others, fitting)
            keep = {id(i) for i in self.shortlist(what, [i for _, i in ranked])}
            for pos, item in ranked:
                if filled() >= cap:
                    break
                others.remove((pos, item))
                if id(item) not in keep or self.is_ad(item, scr.page()):
                    continue
                fit = self.fits(what, item)
                if fit is not False:
                    unsure += fit is None
                    take(pos, item)

        for n in range(READ_MORE + 1):
            scr = self.look() if n or not want else self.from_top(self.look())
            role.update(content_mod.roles(scr))
            fresh = [i for i in collect_candidates(scr, what) if self._collect_key(i) not in asked]
            for k, item in enumerate(fresh):
                asked.add(self._collect_key(item))
                if named_fit(what, item) or (titles_wanted and role.get(id(item)) == content_mod.TITLE):
                    fitting.append(item)
                    if filled() < cap and not self.is_ad(item, scr.page()):
                        take((n, k), item)
                else:
                    others.append(((n, k), item))
            if filled() < cap and not topic_words(what):  # code cannot judge a fit: the page's structure and Laya
                fill_in()
            if filled() >= cap or not want or n == READ_MORE:
                break
            before = scr.fingerprint()
            self.do("scroll down to read more", self.env.scroll, "down")
            if self.settle(before, timeout=1.0) is None:
                break  # the end of the page
        if filled() < cap and others:
            fill_in()
        if not taken and (opened := self.open_own_page(what, scr)) is not None:
            first, own_page = opened
            title = " ".join(own_page.name.split())
            for n, page in enumerate(self.own_reads(first)):  # its values are the thing's: read as it loads, then on
                role.update(content_mod.roles(page))
                for k, item in enumerate(collect_candidates(page, what, own=title)):
                    if filled() < cap:
                        take((READ_MORE + 1 + n, k), item if named_fit(what, item) else replace(item, name=title))
                if filled():
                    break
            self.go_back("back to the list")
        kept = [c for _, c in sorted(taken, key=lambda t: t[0])][:cap]
        shown = "; ".join(c.shown() for c in kept)
        self.trace.append(f"  collect {what!r}: {len(kept)} taken as {{{slot}}}" + (f": {shown}" if shown else ""))
        if kept:
            kept = self.keep(slot, kept, scr)
        if not kept:
            return ToolResult(Outcome.NOT_FOUND, f"nothing like {what!r} on screen ({len(asked)} read)")
        laya = f" ({unsure} Laya could not tell: taken by the page's structure)" if unsure else ""
        fewer = f" ({len(kept)} of the {want} asked for: all that fit)" if len(kept) < want else ""
        there = f" (read on its own page, {_host_of(own_page.url)})" if own_page is not None else ""
        # fewer than asked is what there is: the plan's next step or the tell decides what it is worth
        return ToolResult(
            Outcome.OK,
            f"{len(kept)} item{'s' if len(kept) != 1 else ''} collected as {{{slot}}}{fewer}{laya}{there}: {shown}",
        )

    def calendar(self, lines: list[str], day: date, slot: str) -> ToolResult:
        """A day's events read from the calendar itself, one "HH:MM Title" each; an empty day keeps one fact saying
        so, which a tell says as it is. The trace and the ledger show the count, never a title."""
        when = day_words(day)
        self.keep(
            slot,
            [Collected("calendar event", "", line, role=content_mod.EVENT) for line in lines]
            or [Collected("calendar", "", f"Nothing on your calendar {when}", role=content_mod.EVENT)],
        )
        self.trace.append(f"  collect: {len(lines)} calendar event(s) {when} from the calendar as {{{slot}}}")
        return ToolResult(Outcome.OK, f"{len(lines)} calendar event(s) {when} collected as {{{slot}}}")

    @staticmethod
    def _collect_key(item: Item) -> str:
        return item.url.split("#")[0].rstrip("/") if item.url else norm(f"{item.name} {item.value}")

    def is_ad(self, item: Item, page: list[Item]) -> bool:
        """An advertisement or a paid placement: asked of Laya (D9) only for an item that carries a short label of its
        own (`ad_label`, a structural trigger), with that label shown; taken out only when Laya is sure."""
        label = ad_label(item, page)
        if label is None or not callable(getattr(self.laya, "collect_fit", None)):
            return False
        shown = item if LEAD.match(item.name) else replace(item, name=f"{label.name}: {item.name}")
        return affirms(self.ask("collect_fit", AD_ASK, shown), FITS)

    def accessory(self, what: str, item: Item) -> bool:
        """A priced row that names the thing asked for but is something made for it (a case for a phone): Laya's sure
        "something else" (D9, under the risky tier). Its words ("for", "with") are never read."""
        if not callable(getattr(self.laya, "collect_fit", None)):
            return False
        choice = self.ask("collect_fit", what, item)
        return bool(choice.ranked) and choice.best == OTHER and self.acts(choice, "risky")

    def fits(self, what: str, item: Item) -> bool | None:
        """Is `item` one of `what`? Code first (all of its distinctive words), then Laya (D9 collect; the pick check
        where a Laya has no such question): True when it says so under the reversible gate, False only when it is
        sure, under the risky gate, that it is something else; None when it cannot tell (code's structure stands, so
        the item is taken). Laya alone is weak at saying an item belongs to a list (gates.json, D9): it vetoes."""
        if named_fit(what, item):
            return True
        ask = "collect_fit" if callable(getattr(self.laya, "collect_fit", None)) else "pick_ok"
        yes = FITS if ask == "collect_fit" else DOES_IT
        choice = self.ask(ask, what, item)
        if affirms(choice, yes):
            return True
        if choice.ranked and choice.best != yes and self.acts(choice, "risky"):
            return False
        return None

    def expand(self, text: str, multiline: bool) -> tuple[str, ToolResult | None]:
        """{name} in a step's text, filled with what a collect kept under that name: one item per line in a box of
        several lines ("title — address"), comma-separated in a one-line field. A name that collected nothing fails
        the step with that reason (the ladder takes it from there). In a task that collects nothing, braces are text
        (the plan check already refused a {name} in a write or fill without its collect)."""
        names = {m.lower() for m in SLOT_REF.findall(text)}
        if not names or (not self.slots and not self.planned):
            return text, None
        missing = sorted(n for n in names if not self.slots.get(n))
        if missing:
            kept = ", ".join(f"{{{k}}}" for k in self.slots) or "nothing"
            return text, ToolResult(Outcome.NO_DATA, f"nothing was collected as {{{missing[0]}}} (collected: {kept})")

        def fill(m: re.Match) -> str:
            items = self.slots.get(m[1].lower())
            if not items:
                return m[0]
            return ("\n" if multiline else ", ").join(c.text() for c in items)

        out = SLOT_REF.sub(fill, text)
        self.unsent.update({n: self.composed[n] for n in names if n in self.composed})  # read out before it is sent
        self.trace.append(f"  {{…}} filled from what was collected ({'one per line' if multiline else 'one line'})")
        return out, None

    def tool_compose(self, what: str, sources: list, slot: str, tone: str = "") -> ToolResult:
        """Text that does `what` (a reply, a message, notes, a subject), written by one brain call from what `sources`
        hold and nothing else (tell.compose: cited, no fact added, no email address), kept under `slot` for a later
        `write {slot}`; never a step. A mail or messaging app's text goes to the brain only with the user's yes
        (`withheld`); without it, its titles only, and the end line says so."""
        from karta.harness import tell as tell_mod

        names = [str(n).lower() for n in sources]
        missing = [n for n in names if not self.slots.get(n)]
        if missing:
            kept = ", ".join(f"{{{k}}}" for k in self.slots) or "nothing"
            return ToolResult(Outcome.NO_DATA, f"nothing was collected as {{{missing[0]}}} (collected: {kept})")
        if self.brain is None:
            return ToolResult(Outcome.UNEXPECTED, "no brain to compose with")
        screens = self.screens_of(names)
        refused = self.withheld(screens)
        if refused:
            self.notices.append(TITLES_ONLY.format(apps=" and ".join(refused)))
        cites: list[int] = []
        text = tell_mod.compose(what, self.facts_for(names, refused), self.brain, tone, cites)
        if not text:
            return ToolResult(Outcome.NOT_FOUND, f"nothing could be written from {', '.join(names)} alone")
        self.slots[slot] = [Collected("the composed text", "", text)]
        self.slot_pages[slot] = ""
        self.slot_screens[slot] = screens
        self.composed[slot] = text
        self.planned.add(slot)
        detail = f"{len(text)} characters composed as {{{slot}}} from {', '.join(names)}"
        self.trace.append(f"  compose: {detail}" + (f", citing fact(s) {', '.join(map(str, cites))}" if cites else ""))
        return ToolResult(Outcome.OK, detail)

    def resolve_repo(self, step: dict):
        """A delegate step's repo (delegate.resolve_repo over the Mac map's folders and the session's last one), kept
        for the step: (path, "") or (None, why not)."""
        from karta.harness import delegate as dg

        path, why = dg.resolve_repo(str(step.get("repo") or ""), self.facts.folders, self.recent_repo)
        if path is not None:
            self.repos[id(step)] = path
        return path, why

    def tool_delegate(self, request: str, repo: str = "") -> ToolResult:
        """Start Claude Code on a repo in a NEW Terminal window (the env's `open_terminal`, delegate.launch): asked
        first with the exact folder and request unless the user said yes to this step up front; never an existing
        window, never a permission flag. Checked: one more window, `claude` running in it."""
        from karta.harness import delegate as dg

        step = self.step if self.step is not None else {"repo": repo}
        why = dg.check_request(request)
        if why:
            return ToolResult(Outcome.UNEXPECTED, f"I won't start Claude Code with that: {why}")
        path, why = self.resolve_repo(step)
        if path is None:
            return ToolResult(Outcome.NOT_FOUND, why)
        if self.approved.get(id(step)) != "run":
            asked = dg.question(path, request)
            self.confirmations.append(asked)
            yes = bool(self.confirm(asked))
            self.trace.append(f"  confirm {asked!r} -> {'yes' if yes else 'no'}")
            if not yes:
                return ToolResult(Outcome.REFUSED, "Claude Code was not started")
        else:
            self.trace.append(f"  run: Claude Code in {dg.shown(path)}: the user said yes to it before the task started")
        opener = getattr(self.env, "open_terminal", None)
        if not callable(opener):
            return ToolResult(Outcome.UNEXPECTED, "this Mac cannot open a Terminal window from here")
        launched, line = dg.prepare(path, request)
        self.actions += 1
        self.trace.append(f"    do: open a new Terminal window: {line}")
        try:
            launched.window, launched.tty = opener(line)
        except dg.Refused as error:
            return ToolResult(Outcome.UNEXPECTED, str(error))
        self.launched.append(launched)
        return ToolResult(Outcome.OK, f"Claude Code started in {dg.shown(path)} (Terminal window {launched.window})")

    def tool_write(self, text: str, where: str, at: dict | None = None) -> ToolResult:
        if at:  # the field a page plan named by number: that field, no Laya pick
            box, _scr = self.numbered_item(at)
            if box is None or box.kind != "field":
                return ToolResult(Outcome.NO_FIELD, f"{where!r}: {STALE}" if box is None else f"{where!r} is not a field")
            if box.chrome and self.front in BROWSER_APPS:  # the browser's address field: its own key, as named
                return self.tool_write(text, "address bar")
            self.picked_by_number(where, box)
            return self.write_into(box, text)
        where = words(where)
        if ADDRESS_WORDS.search(where) and self.front in BROWSER_APPS:  # the browser's own address bar, not a page field
            said = text
            text, missing = self.expand(text, multiline=False)
            if missing is not None:
                return missing
            if not self.do("focus the address bar", self.env.press, "cmd+l"):
                return ToolResult(Outcome.NO_FIELD, "the address bar did not take the focus: nothing typed")
            if not self.do(f"type {said!r} into the address bar", self.env.type_at_focus, text):
                return ToolResult(Outcome.NO_FIELD, "the address bar lost the focus: nothing typed")
            self.echo.add(text)
            self.pending[text] = "address bar"
            self.last_write = ("address bar", text)
            self.submitted = None
            return ToolResult(Outcome.OK, f"typed {text!r} into the address bar (return opens it)")
        fields = self.page_fields(self.look())
        if not fields:
            return ToolResult(Outcome.NO_FIELD, f"no field on the page for {where!r} (type at the focus if it is text-only)")
        # a message never goes into the search box; the plan's "search box" goes there first
        asked = bool(SEARCH_WORD.search(where))
        fields = [f for f in fields if f.search == asked] or fields
        fields = self.one_each(fields, self.look())  # two forms alike open: the one in use
        box, why = self.pick(where, fields)
        if box is None:
            return ToolResult(why.outcome if why.outcome in (Outcome.UNSURE, Outcome.DISABLED) else Outcome.NO_FIELD, why.detail)
        return self.write_into(box, text)

    def write_into(self, box: Item, text: str) -> ToolResult:
        """`text` ({name} filled in) into `box`, verified there; what a submit then sends and checks."""
        said = text
        text, missing = self.expand(text, box.multiline)
        if missing is not None:
            return missing
        put = self.put(box, text, shown=said if said != text else None)
        if put.ok:
            name = (self.last_put or {}).get("name") or box.name  # a box that renamed itself once typed into
            self.pending[text] = name
            self.last_write = (name, text)
            self.submitted = None
        return put

    def tool_type(self, text: str) -> ToolResult:
        focus = next((f for f in self.page_fields(self.look()) if f.focused), None)
        said = text
        text, missing = self.expand(text, focus is not None and focus.multiline)
        if missing is not None:
            return missing
        before = self.look().fingerprint()
        self.do(f"type {said!r} at the focus", self.env.type_at_focus, text)
        self.echo.add(text)
        if self.settle(before) is None:
            return ToolResult(Outcome.NO_CHANGE, f"typed {said!r} but nothing changed (nothing focused?)")
        into = next((f for f in self.page_fields(self.look()) if f.focused and norm(text.strip()) in norm(f.value)), None)
        if into is not None and "\n" not in text:  # typed into a named box (a message box): a send checks that box
            self.pending[text] = into.name
            self.last_write = (into.name, text)
            self.submitted = None
        return ToolResult(Outcome.OK, f"typed {said!r}")

    def tool_fill(self, fields: dict, at: list | None = None) -> ToolResult:
        filled = []
        for k, (name, value) in enumerate(fields.items()):
            numbered = at[k] if at and k < len(at) and isinstance(at[k], dict) and at[k] else None
            if numbered:  # the field (or checkbox) a page plan named by number: that one, no Laya pick
                item, scr = self.numbered_item(numbered)
                if item is None:
                    self.fill_at = name
                    return ToolResult(Outcome.NO_FIELD, f"{name.strip()!r}: {STALE}; filled {filled}")
                boxes = [item] if item.kind == "checkbox" or item.checked is not None else []
            else:
                scr = self.look()
                boxes = [i for i in scr.page() if i.kind == "checkbox" and names_toggle(name, i)]
            if isinstance(value, bool) or norm(str(value)) in ("on", "off", "checked", "unchecked"):
                want = value if isinstance(value, bool) else norm(str(value)) in ("on", "checked")
                if not boxes:
                    return ToolResult(Outcome.NOT_FOUND, f"no checkbox {name!r}; filled {filled}")
                if not boxes[0].enabled:
                    return ToolResult(Outcome.DISABLED, self.disabled(boxes[0], scr.page()).detail + f"; filled {filled}")
                if bool(boxes[0].checked) != want:
                    self.do(f"click {boxes[0].name!r}", self.env.click, boxes[0])
                filled.append(name)
                self.filled = list(filled)
                continue
            box = (item if item.kind == "field" else None) if numbered else self.named_field(scr, name)
            if box is None and numbered:
                self.fill_at = name
                return ToolResult(Outcome.NO_FIELD, f"{name.strip()!r} is not a field; filled {filled}")
            if box is None:  # the control that reveals it, or a pop-up that holds the choice
                box, chose = self.reveal_field(name, str(value), boxes)
                if chose is not None:
                    filled.append(name)
                    self.filled = list(filled)
                    continue
            if box is None:
                box = self.fill_field(self.look(), name)
            if box is None:
                self.fill_at = name
                return ToolResult(Outcome.NO_FIELD, f"no field {name!r}; filled {filled}")
            said = str(value)
            value, missing = self.expand(said, box.multiline)
            if missing is not None:
                return ToolResult(missing.outcome, f"{missing.detail}; filled {filled}")
            put = self.put(box, str(value), shown=said if said != value else None)
            if not put.ok:
                self.fill_at = name
                return ToolResult(put.outcome, f"{put.detail}; filled {filled}")
            self.pending[str(value)] = box.name
            self.last_write = (box.name, str(value))  # as write: a submit checks, and returns into, this field
            self.submitted = None
            filled.append(name)
            self.filled = list(filled)
        return ToolResult(Outcome.OK, f"filled {filled}")

    def named_field(self, scr: Screen, name: str) -> Item | None:
        """The field a fill names: its exact name, match.named's reading, else the one field whose name starts with
        the asked name as whole words ("To" -> "To recipients"). Copies (two forms open): the one in use (one_of)."""
        fields = self.one_each(self.page_fields(scr), scr)
        exact = [f for f in fields if norm(f.name) == bare(name)]
        if exact:
            return exact[0]
        hit = code_named(name, fields)
        if hit is not None:
            return hit
        want = match.normalise(bare(name))
        starts = [f for f in fields if want and (match.normalise(f.name) + " ").startswith(want + " ")]
        return starts[0] if len(starts) == 1 else None

    def reveal_field(self, name: str, value: str, boxes: list[Item]) -> tuple[Item | None, Item | None]:
        """A field that is not on screen yet: its checkbox or toggle turned on ("Time" before the time
        field), or its pop-up button opened and the value's option chosen ("Schedule" -> "Sunset to Sunrise"), or
        Laya's pick of the toggle that shows it. (field to type into, None) or (None, the option chosen)."""
        scr = self.look()
        controls = [i for i in scr.page() if i.enabled and i.kind != "field" and not i.menu and not i.chrome]
        opener = next((b for b in boxes if b.enabled and not b.checked), None) or code_named(name, controls)
        toggles = [  # a setting's own toggle, never a list row's checkbox (it selects the row)
            i for i in controls if (i.kind == "checkbox" or i.checked is not None) and len(i.name.split()) <= TOGGLE_WORDS
        ]
        if opener is None and toggles:
            opener, _choice = self.eliminate(
                "element",
                f"the option that shows the {bare(name)} field",
                toggles,
                lambda p: self.ask("target", f"the option that shows the {bare(name)} field", p),
                check_first=True,
            )
        if opener is None or (opener.kind == "checkbox" and opener.checked):
            return None, None
        self.do(f"click {opener.name!r} (to show {name!r})", self.env.click, opener)
        now = self.settle(scr.fingerprint()) or self.look()
        field_item = self.named_field(now, name)
        if field_item is not None:
            return field_item, None
        new = [i for i in now.page() if i.enabled and i not in scr.page()]
        option = code_named(value, new or now.page())
        if option is not None and option.kind != "field":
            self.do(f"choose {option.name!r} for {name!r}", self.env.click, option)
            self.settle(now.fingerprint())
            return None, option
        return None, None

    def fill_field(self, scr: Screen, name: str) -> Item | None:
        fields = self.one_each(self.page_fields(scr), scr)
        exact = [f for f in fields if norm(f.name) == bare(name)]
        if exact:
            return exact[0]
        return self.pick(f"the {bare(name)} field", fields)[0] if fields else None

    # ---------------------------------------------------------------- answer

    def tool_answer(self, choice_meaning: str, at: dict | None = None) -> ToolResult:
        if at:  # an option a page plan named by number: a permission prompt is still the user's
            scr = self.look()
            own = facts_mod.in_the_way(scr)
            if facts_mod.system_prompt(scr) or any(i.browser_ui for i in own):
                return self.permission(own, scr)
            return self.click_at(choice_meaning, at)
        scr = self.look()
        dialog = self.dialog(scr)
        if not dialog:
            if self.answered_at == self.actions or norm(choice_meaning) in self.answered:  # answered already
                return ToolResult(Outcome.OK, "already answered")
            return ToolResult(Outcome.NOT_FOUND, "no dialog or prompt on screen")
        own = facts_mod.in_the_way(scr)
        if facts_mod.system_prompt(scr) or any(i.browser_ui for i in own):  # the user's to give, whatever the plan says
            return self.permission(own, scr)
        answers = [i for i in dialog if i.enabled and (i.kind in ("button", "link", "checkbox", "row") or NUMBERED.match(i.name))]
        options = answers or [i for i in dialog if i.kind != "field" and i.enabled] or list(dialog)
        exact = [
            i for i in options if norm(i.name) == norm(choice_meaning) or bare(choice_meaning) == norm(NUMBERED.sub("", i.name))
        ]
        if len(exact) != 1:
            hit = code_named(choice_meaning, options)
            exact = [hit] if hit is not None else exact
        key = f"{scr.app}: {' / '.join(sorted(o.name for o in options))[:120]}"
        # Laya answers with a button or a numbered choice only: a checkbox, a row or a link in a dialog changes a
        # setting, a filter or the page, and is taken only when the step names it
        laya_options = [o for o in options if o.kind == "button" or NUMBERED.match(o.name)]
        tried: set[str] = set()
        for _ in range(ROUNDS):
            choice = None
            if len(exact) == 1 and exact[0].name not in tried:
                item = exact[0]
            else:
                pool = [o for o in laya_options if o.name not in tried]
                if not pool:
                    if tried:
                        break
                    return ToolResult(Outcome.UNSURE, f"no plain answer to choose among {[o.name for o in options][:6]}")
                item, choice = self.eliminate(
                    "answer", key, pool, lambda p: self.ask("dialog_answer", choice_meaning, p), check_first=False
                )
                if item is None:  # never invent an answer; the rescue or the user decides
                    return ToolResult(
                        Outcome.UNSURE if choice is None or not choice.sure else Outcome.BLOCKED_BY_DIALOG,
                        f"no sure answer among {[o.name for o in options][:6]}",
                    )
            if risk.risky("answer", item.name) and not self.acts(choice, "risky"):
                return ToolResult(Outcome.UNSURE, f"{item.name!r} cannot be undone and Laya is not sure enough of it")
            if choice is not None and not plain_answer(item) and not self.acts(choice, "risky"):
                # not a dialog's plain answer (a quantity, a filter, a setting, a link): only on a risky-sure pick
                return ToolResult(Outcome.UNSURE, f"{item.name!r} is not a plain answer and Laya is not sure enough of it")
            if not self.gate("answer", item.name, item):
                return ToolResult(Outcome.REFUSED, item.name)
            result = self.choose_option(item, scr, "for " + words(choice_meaning, 8))
            if result.outcome is not Outcome.NO_CHANGE:
                self.answered.add(norm(choice_meaning))
                return result
            tried.add(item.name)
            self.note("answer", key, item.name, "choosing it changed nothing")
            scr = self.look()
        return ToolResult(Outcome.NO_CHANGE, f"no answer among {[o.name for o in options][:6]} changed the dialog")

    # ---------------------------------------------------------------- submit

    def tool_submit(self, expect: str) -> ToolResult:
        """Send what was written by its visible control first (a button named by a generic verb, else the one beside
        the field written last); return only into that field, and never into a multi-line one (a body, a comment, a
        text area: return adds a line there). The user's yes first when it cannot be undone."""
        before = self.look()
        if self.submitted and not self.pending and not self.last_write:  # never send the same thing twice
            return ToolResult(Outcome.NO_CHANGE, f"already sent by {self.submitted!r}; nothing new written since")
        if self.last_write and self.last_write[0] == "address bar":  # a web address: return opens it, no page button
            if not self.address_holds(self.last_write[1]):
                return ToolResult(Outcome.NO_FIELD, "the address bar no longer holds the address: return not pressed")
            if not self.return_key("press return", sends=False):
                return ToolResult(Outcome.NO_FIELD, "return was not pressed: the focus left the address bar")
            result = self.check_end(expect, before, None)
            if result.ok:
                self.submitted = "return"
            return result
        searched = self.search_written(before)
        if searched is not None:
            # a search runs from its own box: return there, never a page button that happens to be a verb ("Create")
            name, text = searched
            field_item = self.field_named(before, name)
            if field_item is None or not self.return_into(field_item, text, sends=False):
                return ToolResult(Outcome.NO_FIELD, f"{name!r} is gone or lost our text: return not pressed")
            result = self.check_end(expect, before, None)
            if result.ok:
                self.submitted = "return"
            return result
        extra = self.not_exact(before)
        if extra is not None:
            return ToolResult(Outcome.UNEXPECTED, extra)
        control = self.submit_control(before)
        written = self.written_box(before)
        box = self.last_write[0] if self.last_write else (written.name if written is not None else "")
        if control is None and written is not None and written.multiline:
            control = self.send_button(before, set(), written)
            if control is None:
                return ToolResult(
                    Outcome.NOT_FOUND, f"{box!r} takes several lines (return adds one) and no send button is visible"
                )
        if self.unsent and not self.heard():  # composed text: read out and asked, whatever the control is named
            return ToolResult(Outcome.REFUSED, "the composed text was not sent")
        if control is not None:
            if not self.gate("click", control.name, control):
                return ToolResult(Outcome.REFUSED, control.name)
            self.do(f"click {control.name!r}", self.env.click, control)
        else:
            editor = (  # return in the list row the plan just wrote in a native app commits that row: no send
                written is not None
                and not before.url
                and self.last_write is not None
                and norm(written.name) == norm(self.last_write[0])
                and facts_mod.in_list_row(written)
            )
            if not self.last_write and written is None:  # nothing to send: never a blind return, so never a question
                return ToolResult(Outcome.NO_FIELD, "nothing was written and no field holds the focus: return not pressed")
            if editor:
                self.trace.append(f"  {box!r} is a list row's editor: return commits the row, it sends nothing")
            elif not self.gate("press return", box, written):
                return ToolResult(Outcome.REFUSED, "return")
            if self.last_write:  # return only into the field written last, still holding our text
                field_item = self.field_named(before, self.last_write[0])
                if field_item is None or not self.return_into(field_item, self.last_write[1]):
                    return ToolResult(Outcome.NO_FIELD, f"{self.last_write[0]!r} is gone or lost our text: return not pressed")
            elif not self.do("press return", self.env.press, "return"):
                return ToolResult(Outcome.NO_FIELD, "return was not pressed: the keyboard focus is in nothing of the app")
        result = self.check_end(expect, before, control)
        if result.ok:
            self.submitted = control.name if control is not None else "return"
        return result

    def searched_into(self, scr: Screen, name: str) -> bool:
        """The field written last is a search box: a search field by structure (Item.search), or the box that searched
        in this app before."""
        box = self.field_named(scr, name)
        return (box is not None and box.search) or self.boxes.get(scr.app) == name

    def search_written(self, scr: Screen) -> tuple[str, str] | None:
        """(box, text) when what a submit sends is a search: the field written last is a search box, or, with nothing
        written, the focused field is one and holds text."""
        if self.last_write:
            return self.last_write if self.searched_into(scr, self.last_write[0]) else None
        focus = next((f for f in self.page_fields(scr) if f.focused and (f.value or "").strip()), None)
        return (focus.name, focus.value) if focus is not None and self.searched_into(scr, focus.name) else None

    def written_box(self, scr: Screen) -> Item | None:
        """The field a submit sends from: the one written last, else the focused one."""
        if self.last_write:
            return self.same_field(scr, self.last_write[0])
        return next((f for f in self.page_fields(scr) if f.focused), None)

    def owned(self, controls: list[Item], box: Item | None) -> list[Item]:
        """The controls that belong to `box`'s form, nearest first. By the accessibility tree when it is known
        (`form_mates`: the controls inside the deepest container they share with the field, below the page itself);
        else where they are drawn: in its dialog (never the page behind it, never another dialog's when frames tell
        them apart), its row, its region or beside it. All of them when no field is known."""
        if box is None:
            return controls
        mine = form_mates(controls, box) if _path(box) else None
        if mine is None:
            mine = [c for c in controls if belongs(c, box)]
        at = getattr(box.ref, "centre", None)
        if at is not None and all(getattr(c.ref, "centre", None) is not None for c in mine):
            mine.sort(key=lambda c: math.dist(at, c.ref.centre))
        return mine

    def submit_control(self, scr: Screen) -> Item | None:
        """The visible control that sends or saves what was written (send_button over the field written last)."""
        return self.send_button(scr, set(), self.written_box(scr))

    def send_button(self, scr: Screen, exclude: set[str], box: Item | None = None) -> Item | None:
        """The visible button that sends or saves what was written, by structure, never by its words: among the
        buttons of `box`'s own form (owned; never a link, which goes somewhere else), its default button (AXDefaultButton, the one Return presses); else Laya's
        pick of the one that would "submit what was written" (SUBMIT_INTENT; a sure first answer stands with no pick
        check; an unsure one only past its pick check, as any pick; none: then return goes into the field).
        With no field known, only a default button. For a search box only a control of its own search landmark: a
        page verb ("Create") never runs a search."""
        buttons = [i for i in scr.page() if i.kind == "button" and i.enabled and i.name not in exclude]  # a link goes away
        if box is not None and self.searched_into(scr, box.name):
            hits = [b for b in self.owned(buttons, box) if b.search and b.kind != "field"]
            return hits[0] if hits else None
        if box is None:
            return next((b for b in buttons if b.default), None)
        mine = [b for b in self.owned(buttons, box) if not b.search]
        default = [b for b in mine if b.default]
        if default or not mine:
            return default[0] if default else None
        # the pool is the form's own buttons (structure); a sure first answer stands with no pick check: the submit's
        # typed expectation verifies it, and a send that cannot be undone asks the user's yes first (gate, heard)
        item, _ = self.pick(SUBMIT_INTENT, mine, sure_stands=True)
        return item

    def form_button(self, scr: Screen, box: Item | None, exclude: set[str]) -> Item | None:
        """The form's own button for a submit that sends nothing (a search, a filter, a quick entry) when Return
        changed nothing: by structure, among the buttons of `box`'s form by the accessibility tree (form_mates: the
        deepest container they share with the field), its default button, else the one nearest the field; with no
        tree, only a default button drawn with it. Never a way out (its cancel or close control), never one the risk
        floor names."""
        if box is None:
            return None
        buttons = [i for i in scr.page() if i.kind == "button" and i.enabled and i.name not in exclude]
        buttons = [b for b in buttons if not b.cancels and risk.risky("click", b.name) is None]
        mates = form_mates(buttons, box) if _path(box) else None
        if mates is None:  # the tree says nothing: only a default button among those drawn with it (a verb beside
            return next((b for b in self.owned(buttons, box) if b.default), None)  # a box is no form's)
        mine = self.owned(mates, box)
        return next((b for b in mine if b.default), mine[0] if mine else None)

    def created(self, after: Screen, before: Screen, text: str) -> bool:
        """The written text now shows outside any field, where it did not before: a new item whose title stays in
        its field (a reminder, a task), not a message waiting to be sent."""

        def outside(scr: Screen) -> Screen:
            return Screen(scr.app, [i for i in scr.page() if i.kind != "field"], scr.url, scr.window, scr.profile)

        return facts_mod.shows(outside(after), text) and not facts_mod.shows(outside(before), text)

    @staticmethod
    def moved_on(before: Screen, after: Screen) -> bool:
        """The page itself changed (its address, or what it lists outside its fields), not only a field's text."""

        def listed(scr: Screen) -> set[str]:
            return {norm(i.name) for i in scr.page() if i.kind != "field"}

        return (after.url or "") != (before.url or "") or listed(after) != listed(before)

    def check_end(self, expect: str, before: Screen, control: Item | None = None) -> ToolResult:
        """Done only by what the page shows, once it had time to show it: never our own typing still in its field.
        Text still in the field after the wait: the visible send button is tried."""
        after = self.settle(before.fingerprint()) or self.look()
        if self.last_write:
            name, text = self.last_write

            def still(scr: Screen) -> bool:
                box = self.field_named(scr, name)
                return box is not None and holds(box.value, text)

            if still(after):
                for later in self.rechecks(after):
                    after = later
                    if not still(later):
                        break
            # a search box keeps its query once the results came: the page moving on is the proof, not an empty box
            searched = self.searched_into(before, name) and still(after) and self.moved_on(before, after)
            if searched:
                self.trace.append(f"  {name!r} keeps the search; the page moved on to its results")
            if still(after) and not searched and not self.created(after, before, text):
                box = self.field_named(after, name)
                exclude = {control.name} if control is not None else set()
                button = self.send_button(after, exclude, box)
                # the form's own button, by structure, only for a submit that sends nothing (a search, a quick entry):
                # one that sends, posts or commits is never pressed a way it did not name
                sends = risk.risky("submit", name) is not None or self.foreign_risk("submit", name, box) is not None
                if button is None and not sends:
                    button = self.form_button(after, box, exclude)
                if button is not None:
                    if not self.gate("click", button.name, button):
                        return ToolResult(Outcome.REFUSED, button.name)
                    self.trace.append(f"  our text is still in {name!r}: the visible {button.name!r} button")
                    self.do(f"click {button.name!r}", self.env.click, button)
                    after = self.settle(after.fingerprint()) or self.look()
                    control = button
                if still(after):
                    return ToolResult(Outcome.UNEXPECTED, f"{text!r} is still in {name!r}: not sent")
        if after.fingerprint() == before.fingerprint():
            return ToolResult(Outcome.NO_CHANGE, "the screen did not change")
        if self.by_type or not expect.strip():  # the kind of change it declared is the loop's (expect.py), never words
            self.pending.clear()
            self.last_write = None  # sent: never sent twice
            return ToolResult(Outcome.OK, "sent: the screen changed")
        # the expected words count only where they are new since before the send: a "sent" line left from an
        # earlier message is no proof this one went
        echo = typed_lines(self.echo)
        if not facts_mod.shows_new(after, before, expect, echo):
            for later in self.rechecks(after):  # a sent message may take a moment to be listed
                after = later
                if facts_mod.shows_new(after, before, expect, echo):
                    break
        if facts_mod.shows_new(after, before, expect, echo):
            self.pending.clear()
            self.last_write = None
            return ToolResult(Outcome.OK, f"{expect!r} on screen (new since the send)")
        choice = self.outcome_since(expect, after, before)
        if choice is None:
            return ToolResult(Outcome.UNEXPECTED, f"nothing new shows {expect!r}")
        if affirms(choice, SHOWN):
            self.pending.clear()
            self.last_write = None
            return ToolResult(Outcome.OK, f"Laya: {choice.best}")
        return ToolResult(Outcome.UNSURE if not choice.sure else Outcome.UNEXPECTED, f"{expect!r} is not on screen")

    # ---------------------------------------------------------------- reach a total

    def tool_reach(self, amount: str, of: str = "") -> ToolResult:
        """Add items until the page's running total is `amount`: code reads each offer's price from its row and the
        running total from the page, picks the quantities (a price that divides what is left, fewest clicks), adds or
        removes one at a time, and is done only when code reads the total. Which control in a row adds or removes one,
        and which priced line is the total when several could be, is Laya's (their words are never read). Honest: it
        counts every add and says what the page shows when it cannot get there."""
        target = amount_of(amount)
        if target is None or target <= 0:
            return ToolResult(Outcome.NOT_FOUND, f"{amount!r} is not an amount")
        product: tuple[str, float] | None = None
        adds = removes = 0
        for _ in range(REACH_ROUNDS):
            scr = self.look()
            total = self.running_total(scr)
            if adds and total is None:
                for later in self.rechecks(scr):
                    scr = later
                    if self.running_total(later) is not None:
                        break
                total = self.running_total(scr)
            now = total or 0.0
            if total is not None and abs(total - target) < 0.005:
                return ToolResult(Outcome.OK, f"the total on screen is {total:g} ({adds} added, {removes} removed)")
            offers = self.offers(scr, of)
            if now > target:
                name = product[0] if product else None
                control = self.row_control(scr, name, f"remove one {name}") if name else None
                if control is None:
                    return ToolResult(Outcome.UNEXPECTED, f"the total is {now:g}, over {target:g}, and nothing removes one")
                self.do(f"click {control.name!r} (remove one {name!r})", self.env.click, control)
                self.settle(scr.fingerprint())
                removes += 1
                continue
            left = target - now
            if product is None or product[1] > left + 0.005:
                product = self.choose_offer(offers, left)
                if product is None:
                    return ToolResult(
                        Outcome.NOT_FOUND, f"no offer priced to reach {target:g} from {now:g} ({len(offers)} offers read)"
                    )
                self.trace.append(f"  reach {target:g}: {product[0]!r} at {product[1]:g}, {left / product[1]:g} of them")
            control = self.row_control(scr, product[0], f"add one more {product[0]}")
            if control is None:
                return ToolResult(Outcome.NOT_FOUND, f"no add control in the row of {product[0]!r}")
            if not self.gate("click", control.name, control):
                return ToolResult(Outcome.REFUSED, control.name)
            self.do(f"click {control.name!r} (add one {product[0]!r})", self.env.click, control)
            if self.settle(scr.fingerprint()) is None:
                return ToolResult(Outcome.NO_CHANGE, f"adding {product[0]!r} changed nothing (total {now:g})")
            adds += 1
        return ToolResult(Outcome.UNEXPECTED, f"the total is {self.running_total(self.look())} after {adds} adds, not {target:g}")

    @staticmethod
    def offers(scr: Screen, of: str = "") -> list[tuple[str, float]]:
        """(product, price) of each row that has a button and a price: its row-mates hold the product's name (the first
        that is no price, number or control) and its price."""
        page = scr.page()
        controls = {i.name for i in page if i.kind in ("button", "link")}
        out: dict[str, float] = {}
        for control in page:
            if not (control.enabled and control.kind in ("button", "link") and control.row):
                continue
            prices = [p for name in control.row for p in money(name)]
            if not prices:
                continue
            names = [n for n in control.row if not money(n) and n not in controls and not re.fullmatch(r"\d+", n.strip())]
            priced = [n for n in control.row if money(n)]
            out.setdefault(names[0] if names else priced[0], prices[0])
        wanted = match.normalise(of).split()
        fitting = {
            n: p for n, p in out.items() if wanted and any(w.startswith(q) for q in wanted for w in match.normalise(n).split())
        }
        return list((fitting or out).items())

    @staticmethod
    def choose_offer(offers: list[tuple[str, float]], left: float) -> tuple[str, float] | None:
        """The offer that reaches what is left exactly in the fewest adds; else the dearest one that still fits."""
        fits = [(n, p) for n, p in offers if 0 < p <= left + 0.005]
        exact = [(n, p) for n, p in fits if abs(left / p - round(left / p)) < 0.001]
        if exact:
            return max(exact, key=lambda o: o[1])
        return max(fits, key=lambda o: o[1]) if fits else None

    def row_control(self, scr: Screen, product: str, intent: str) -> Item | None:
        """The control in `product`'s row that does `intent` ("add one more ...", "remove one ..."): the row's only
        control when it has one (adding the first), else Laya's sure pick among the row's controls (a stepper's two
        buttons, whatever they are named or drawn as)."""
        row = [
            i
            for i in scr.page()
            if i.enabled
            and i.kind in ("button", "link")
            and any(norm(product) == norm(n) or norm(product) in norm(n) for n in i.row)
        ]
        if len({norm(i.name) for i in row}) == 1 and intent.startswith("add"):
            return row[0]
        return self.pick(intent, row)[0] if row else None

    def running_total(self, scr: Screen) -> float | None:
        """The page's running total, read by code: the priced line that is no product's (in no control's row); when
        several could be, Laya's pick of the running total (D5). None when there is none."""
        page = scr.page()
        rows = {n for i in page if i.kind in ("button", "link") for n in i.row}
        priced = [i for i in page if i.kind != "field" and i.name not in rows and money(f"{i.name} {i.value}")]
        if len(priced) > 1:
            choice = self.ask("name_match", "the running total of what was added", "", priced)
            pick = item_for(choice.best, priced) if choice.ranked and choice.sure else None
            priced = [pick] if pick is not None else []
        return money(f"{priced[0].name} {priced[0].value}")[-1] if priced else None

    # ---------------------------------------------------------------- press, back, scroll, wait

    def tool_press(self, keys: str) -> ToolResult:
        keys = str(keys).strip().lower()
        if not self.gate(f"press {keys}", self.last_write[0] if self.last_write else ""):
            return ToolResult(Outcome.REFUSED, keys)
        if keys in FOCUS_KEYS:  # these only move the focus or the selection: often nothing visible changes
            self.do(f"press {keys}", self.env.press, keys)
            return ToolResult(Outcome.OK, f"press {keys}")
        return self._fixed(f"press {keys}", self.env.press, keys)

    def tool_back(self) -> ToolResult:
        """Back to the page before, proven by its address (`go_back`)."""
        if self.go_back() is None:
            return ToolResult(Outcome.NO_CHANGE, "go back")
        return ToolResult(Outcome.OK, "go back")

    def tool_scroll(self, direction: str = "down") -> ToolResult:
        return self._fixed(f"scroll {direction}", self.env.scroll, direction)

    def tool_wait(self, seconds: float = 2.0) -> ToolResult:
        self.do(f"wait {seconds:g} s", self.env.wait, seconds)
        return ToolResult(Outcome.OK, f"waited {seconds:g} s")

    def _fixed(self, what: str, fn: Callable, *args) -> ToolResult:
        before = self.look().fingerprint()
        self.do(what, fn, *args)
        if self.settle(before) is None:
            return ToolResult(Outcome.NO_CHANGE, what)
        return ToolResult(Outcome.OK, what)
