"""Code-first matching: settle by text what needs no model.

Every tool asks here before it asks Laya: the brain named "the 'Got it' button" and a button "Got it" is on screen;
the search for "Pri" returned exactly one result containing it. Laya is only asked when these return None.

Stdlib only (difflib). Limits: text only, so an icon-only control without an accessible name is never matched; a
near-spelling is one or two letters in a longer word (ratio >= NEAR), so short words ("Save" / "Same") are never taken
as the same; two items with the same name are never told apart here (Laya or the row does that).
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from karta.harness.types import Item

NEAR = 0.85  # "Marisal" / "Marisol" is 0.857; "Close" / "Clone" is 0.8
CLEAR = 0.12  # a fuzzy winner must beat the second-best by this much
KIND_WORDS = {  # words the brain adds around a name: dropped from both sides; the ones mapped to a kind hint at it
    "button": "button", "btn": "button", "field": "field", "box": "field", "input": "field", "textbox": "field",
    "bar": "field", "link": "link", "tab": "tab", "checkbox": "checkbox", "row": "row", "item": "", "icon": "",
    "option": "", "control": "", "the": "", "a": "", "an": "", "labelled": "", "labeled": "", "called": "",
    "named": "",
}  # fmt: skip
CROSSES = str.maketrans(dict.fromkeys("\u00d7\u2715\u2716\u2573\u2a2f", "x"))  # a close button drawn as a cross
APOSTROPHES = re.compile("['\u2019\u02bc`]")
QUOTED = re.compile("(?<!\\w)[\"'\u2018\u201c\u00ab](.{1,80}?)[\"'\u2019\u201d\u00bb](?!\\w)")


def normalise(text: str) -> str:
    """Lower case, accents off, apostrophes joined ("Don't" -> "dont"), every other mark a space, spaces collapsed."""
    text = unicodedata.normalize("NFKD", text.translate(CROSSES))
    text = APOSTROPHES.sub("", "".join(c for c in text if not unicodedata.combining(c)).lower())
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def _target(text: str) -> tuple[str, set[str]]:
    """The name the brain's words point at (the quoted part when there is one), without kind words, and the kinds
    those words hint at."""
    quoted = QUOTED.search(text)
    words = normalise(text).split()
    kinds = {KIND_WORDS[w] for w in words if KIND_WORDS.get(w)}
    core = normalise(quoted[1]) if quoted else " ".join(w for w in words if w not in KIND_WORDS)
    return (core or normalise(text)), kinds


def _bare(name: str) -> str:
    """An item's name, normalised, kind words off ("Search field" -> "search"); a name of kind words only stays."""
    words = normalise(name).split()
    return " ".join(w for w in words if w not in KIND_WORDS) or " ".join(words)


def similarity(a: str, b: str) -> float:
    """0 to 1: how alike two names are after normalising (for near-spellings like Marisal / Marisol).

    The better of the plain ratio and the ratio with words sorted ("Okafor Marisol" / "Marisol Okafor" is 1.0)."""
    na, nb = normalise(a), normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    plain = SequenceMatcher(None, na, nb, autojunk=False).ratio()
    sa, sb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
    return max(plain, SequenceMatcher(None, sa, sb, autojunk=False).ratio())


def _narrow(found: list[Item], kinds: set[str]) -> list[Item]:
    """Several items fit: keep the kind the brain named, then the enabled, then the visible ones, while any are left."""
    for keep in (lambda i: i.kind in kinds, lambda i: i.enabled, lambda i: not i.offscreen):
        narrowed = [i for i in found if keep(i)]
        if narrowed:
            found = narrowed
    return found


def named(target: str, items: list[Item]) -> Item | None:
    """The one item the brain's words name by text ("the 'Got it' button", "Not now", "Search field"): quotes,
    case, punctuation and kind words (button, field, link, tab, the) ignored; a near-spelling allowed when exactly
    one item is that close. None when zero or several items fit.

    Order: the same name (several: the kind the brain named, then enabled, then visible, and still exactly one); else
    the one item within a near-spelling; else, only when the brain named a field ("the search box"), the one field
    whose name starts with those words ("Search mail"). A name that merely contains the words ("Send later" for
    "Send") is never taken: that is Laya's question."""
    core, kinds = _target(target)
    if not core:
        return None
    usable = [i for i in items if normalise(i.name)]
    same = [i for i in usable if core in (_bare(i.name), normalise(i.name))]
    if same:
        same = _narrow(same, kinds)
        return same[0] if len(same) == 1 else None
    near = [i for i in usable if similarity(core, _bare(i.name)) >= NEAR]
    if near:
        near = _narrow(near, kinds)
        return near[0] if len(near) == 1 else None
    if "field" in kinds:
        fields = [i for i in usable if i.kind == "field" and (normalise(i.name) + " ").startswith(core + " ")]
        return fields[0] if len(fields) == 1 else None
    return None


def _contains(name: str, searched: str) -> bool:
    """Every searched word begins a word of the name ("mar" in "marisol okafor"; "okafor mar" too)."""
    words = name.split()
    return all(any(w.startswith(s) for w in words) for s in searched.split())


def _closeness(searched: str, name: str) -> float:
    """How near `searched` is to the name or to any run of as many words in it ("marisal" ~ "marisol okafor")."""
    words = name.split()
    n = len(searched.split())
    runs = [" ".join(words[k : k + n]) for k in range(max(1, len(words) - n + 1))]
    return max(similarity(searched, r) for r in [name, *runs])


def one_result(searched: str, results: list[Item]) -> Item | None:
    """The single result that contains `searched` (or its words) by text, or the one clear fuzzy winner; None
    when several results fit or none does.

    Contains: each searched word begins a word of the result's name ("Mar" -> "Marisol Okafor"); among several, the
    one whose name is exactly the searched text, else None. Fuzzy (nothing contains it): the best result at NEAR or
    more and CLEAR ahead of the second ("Marisal" -> "Marisol Okafor" when no other Marisol is listed)."""
    wanted = normalise(searched)
    rows = [(r, normalise(r.name)) for r in results if normalise(r.name)]
    if not wanted or not rows:
        return None
    containing = [(r, n) for r, n in rows if _contains(n, wanted)]
    if len(containing) == 1:
        return containing[0][0]
    if containing:
        exact = [r for r, n in containing if n == wanted]
        return exact[0] if len(exact) == 1 else None
    scored = sorted(((_closeness(wanted, n), r) for r, n in rows), key=lambda s: -s[0])
    best, second = scored[0][0], (scored[1][0] if len(scored) > 1 else 0.0)
    return scored[0][1] if best >= NEAR and best - second >= CLEAR else None
