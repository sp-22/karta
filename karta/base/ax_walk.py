"""The bounded accessibility-tree walk, platform-free.

Takes children/attrs/actions as plain callables, so macos.py supplies the bridge to AXUIElement and
the pruning, de-duplication, and label-recovery rules stay testable without it. Role strings follow
the "AX*" vocabulary macOS's accessibility API uses.
"""

from __future__ import annotations

import re
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import NamedTuple

from .models import AxNode

AX_PRESS = "AXPress"  # sentinel an actions() callable emits when a node accepts a press/invoke

AX_ACTIONABLE_ROLES = {
    "AXButton",
    "AXCell",
    "AXCheckBox",
    "AXComboBox",
    "AXDisclosureTriangle",
    "AXImage",
    "AXIncrementor",
    "AXLink",
    "AXMenuBarItem",
    "AXMenuButton",
    "AXPopUpButton",
    "AXRadioButton",
    "AXRow",
    "AXSearchField",
    "AXSlider",
    "AXTab",
    "AXTextArea",
    "AXTextField",
}
# A bare child, usually a decorative AXImage, borrows the label of a parent that is itself a control.
AX_LABEL_PARENT_ROLES = {
    "AXButton",
    "AXCell",
    "AXCheckBox",
    "AXLink",
    "AXMenuButton",
    "AXPopUpButton",
    "AXRadioButton",
    "AXRow",
    "AXTab",
}
# List containers keep their label in a shallow AXStaticText rather than on themselves.
AX_LABEL_DESCENDANT_ROLES = {"AXCell", "AXRow"}
AX_SKIP_SUBTREE_ROLES = {"AXMenu"}  # a closed menu: thousands of zero-sized items, none on screen
AX_NODE_CAP = 4000
AX_TIME_CAP = 0.6
AX_OFFSCREEN_CAP = 120  # off-screen controls collected before the walk stops looking for more
AX_MIN_SIDE_PT = 4.0  # anything thinner is a Chromium sliver for a scrolled-out node
AX_FANOUT = 8  # children scanned per level when recovering a label
# Containers that show only what lies inside their own frame: a node of theirs outside it is scrolled out of view (a
# small window's page runs on below its edge, over other apps), not on screen, though on the display. Not a window:
# some apps hang controls outside their window's frame under it (a menu bar under every window).
AX_CLIP_ROLES = {"AXScrollArea", "AXWebArea"}
AX_FLOAT_ROLES = {"AXPopover"}  # drawn over everything, past its window's edge
AX_STRIP_PT = 160.0  # a window this short beside the front one is a strip of it (a full-screen browser's tab bar)
AX_STRIP_GAP_PT = 8.0
AX_STRIP_SPAN = 0.8  # share of the front window's width a strip covers

Frame = tuple[float, float, float, float]  # x, y, w, h in points


class AxAttrs(NamedTuple):
    role: str
    label: str
    frame: Frame | None


def off_display(frame: Frame | None, display_w_pt: float, display_h_pt: float) -> bool:
    """True when a real frame lies wholly outside the display: a note list thousands of screens down,
    or a web node the browser parked above the viewport. A zero-size frame claims nothing, which is
    what an application element and a closed menu report, so their subtrees are still worth a look.
    """
    if frame is None:
        return False
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return False
    return x >= display_w_pt or y >= display_h_pt or x + w <= 0 or y + h <= 0


def center_on_display(frame: Frame | None, display_w_pt: float, display_h_pt: float) -> bool:
    """Whether a frame's centre falls on the display. Chromium pages report a giant `shell` frame
    that starts tens of thousands of points above the viewport yet crosses it, so `off_display`
    alone lets it through as partially visible, and clicking it would aim far off the screen.
    A frameless or zero-size frame claims nothing (an application element, a closed menu), and its
    centre is meaningless, so those pass.
    """
    if frame is None:
        return True
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return True
    return 0 <= x + w / 2 < display_w_pt and 0 <= y + h / 2 < display_h_pt


def outside(frame: Frame | None, clip: Frame) -> bool:
    """`off_display` against any rectangle: a real frame wholly outside `clip` (x, y, w, h)."""
    if frame is None:
        return False
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return False
    cx, cy, cw, ch = clip
    if cw <= 0 or ch <= 0:
        return True  # nothing of the container shows
    return x >= cx + cw or y >= cy + ch or x + w <= cx or y + h <= cy


def centred_in(frame: Frame | None, clip: Frame) -> bool:
    """`center_on_display` against any rectangle: a frame's centre inside `clip`; frameless and zero-size pass."""
    if frame is None:
        return True
    x, y, w, h = frame
    if w <= 0 or h <= 0:
        return True
    cx, cy, cw, ch = clip
    return cx <= x + w / 2 < cx + cw and cy <= y + h / 2 < cy + ch


def clipped(clip: Frame, frame: Frame | None) -> Frame:
    """`clip` narrowed to `frame` (a window, scroll area or web page): what of it shows. A frameless or zero-size
    container narrows nothing."""
    if frame is None or frame[2] <= 0 or frame[3] <= 0:
        return clip
    x1, y1 = max(clip[0], frame[0]), max(clip[1], frame[1])
    x2, y2 = min(clip[0] + clip[2], frame[0] + frame[2]), min(clip[1] + clip[3], frame[1] + frame[3])
    return (x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1))


def _strip_of(a: Frame, b: Frame) -> bool:
    """`a` and `b` are one window in pieces: stacked edge to edge, sharing most of their width, one of them short (a
    full-screen browser shows its tab bar and toolbar as windows of their own above the page's)."""
    if min(a[3], b[3]) > AX_STRIP_PT:
        return False
    span = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    if span < AX_STRIP_SPAN * max(a[2], b[2]):
        return False
    gap = min(abs(a[1] + a[3] - b[1]), abs(b[1] + b[3] - a[1]))
    return gap <= AX_STRIP_GAP_PT


def front_windows(
    kids: list,
    focused,
    attrs: Callable[..., AxAttrs],
    subrole: Callable[..., object] = lambda node: None,
) -> list:
    """An application's children worth walking: the focused window and the strips it is made of, every window that
    is no standard window (a dialog, a floating panel, a popup: they sit over it), and everything that is no window
    (the menu bar). Another standard window of the app is behind the front one or beside it (split view), not what
    the user looks at, and its controls would take the walk's budget and pass for the front window's. With no focused
    window known, all of them."""
    if focused is None:
        return list(kids)
    front = node_identity(focused)
    frames: dict[int, Frame | None] = {}
    windows = []
    for n, kid in enumerate(kids):
        role, _label, frame = attrs(kid)
        if role == "AXWindow":
            windows.append(n)
            frames[n] = frame
    keep = {n for n in windows if node_identity(kids[n]) == front}
    if not keep:
        return list(kids)  # the focused window is not among them (a sheet's own window): read as before
    grown = True
    while grown:  # strips of strips: tab bar above toolbar above page
        grown = False
        for n in windows:
            if n in keep or frames[n] is None:
                continue
            if any(frames[k] is not None and _strip_of(frames[n], frames[k]) for k in keep):
                keep.add(n)
                grown = True
    out = []
    for n, kid in enumerate(kids):
        if n in frames and n not in keep and str(subrole(kid) or "") == "AXStandardWindow":
            continue
        out.append(kid)
    return out


def app_children(
    root, focused, children: Callable[..., Iterable], attrs: Callable[..., AxAttrs], subrole: Callable[..., object]
) -> Callable[..., list]:
    """`children` for a walk from an application element: at the root, only `front_windows`."""

    def kids(node) -> list:
        got = list(children(node))
        return front_windows(got, focused, attrs, subrole) if node is root else got

    return kids


def node_identity(node) -> object:
    """Accessibility elements hash by the element they wrap, so two fetches of one control compare
    equal; anything unhashable (a fake node in a test) falls back to object identity."""
    try:
        hash(node)
    except TypeError:
        return ("id", id(node))
    return node


def subtree_key(role: str, label: str, frame: Frame | None) -> tuple | None:
    """Identity of a node for de-duplication: same role, label and frame is the same control, whatever
    object the bridge wrapped it in. Frameless and zero-size nodes are containers and are never keyed."""
    if frame is None or frame[2] <= 0 or frame[3] <= 0:
        return None
    return (role, label, round(frame[0]), round(frame[1]), round(frame[2]), round(frame[3]))


def clickable(frame: Frame | None) -> bool:
    return frame is not None and min(frame[2], frame[3]) >= AX_MIN_SIDE_PT


def descendant_label(kids: list, children: Callable, attrs: Callable[..., AxAttrs]) -> str:
    """The first static text within two levels, which is where list rows hide their label."""
    for kid in kids[:AX_FANOUT]:
        role, label, _ = attrs(kid)
        if role == "AXStaticText" and label:
            return label
    for kid in kids[:AX_FANOUT]:
        for grandkid in list(children(kid))[:AX_FANOUT]:
            role, label, _ = attrs(grandkid)
            if role == "AXStaticText" and label:
                return label
    return ""


def walk_actionable(
    root,
    children: Callable[..., Iterable],
    attrs: Callable[..., AxAttrs],
    actions: Callable[..., Iterable[str]],
    display_w_pt: float,
    display_h_pt: float,
    node_cap: int = AX_NODE_CAP,
    time_cap: float = AX_TIME_CAP,
    offscreen_cap: int = AX_OFFSCREEN_CAP,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[list[AxNode], list[AxNode], bool]:
    """Breadth-first hunt for labelled controls: the on-screen ones, the reachable off-screen ones,
    and whether a cap cut the walk short.

    The four callables are the only way into the tree, so the pruning rules are platform-free
    and testable against a plain dict. The caps are the point: an unbounded walk of a note list
    or a long web page costs seconds and finds nothing on screen.

    A node that misses the display, or that the app clamped to a sliver, is not on screen and is
    not offered as one: its subtree stays pruned from `found`. But AXPress does not need a node to
    be visible, so a labelled one that accepts the action is collected separately, down to
    `offscreen_cap`, after which those subtrees are dropped again and the walk is the old one.
    """
    found: list[AxNode] = []
    offscreen: list[AxNode] = []
    deadline = clock() + time_cap
    display: Frame = (0.0, 0.0, display_w_pt, display_h_pt)
    queue = deque([(root, "", False, False, None, display, ())])
    seen = 0
    visited: set = set()  # elements compare by identity across fetches, so a self-listing app is walked once
    visited_keys: set[tuple] = set()  # and a control handed over as several distinct objects is kept once
    while queue:
        if seen >= node_cap or clock() >= deadline:
            return found, offscreen, True
        node, parent_label, parent_emitted, hidden, parent_key, clip, path = queue.popleft()
        identity = node_identity(node)
        if identity in visited:
            continue
        visited.add(identity)
        seen += 1
        role, own_label, frame = attrs(node)
        # where it sits, each ancestor with its role: two items of one row or card share a deeper ancestor, and a
        # field inside a list row or a control inside a toolbar is known by structure
        path = (*path, (role, hash(identity)))
        if role in AX_SKIP_SUBTREE_ROLES:
            continue
        key = subtree_key(role, own_label, frame)
        # A node keyed like its own parent wraps it, as AT-SPI nests nameless panels of one frame:
        # it is no copy from elsewhere, so its subtree is walked, but it is never a second item.
        wrapper = key is not None and key == parent_key
        # Only a labelled control is a duplicate by its key: nameless layout boxes share frames (a full-screen
        # browser's page group has its window's frame), and skipping one would drop the whole page under it.
        if key is not None and not wrapper and own_label:
            if key in visited_keys:
                continue
            visited_keys.add(key)
        if role in AX_FLOAT_ROLES:
            clip = display
        hidden = hidden or outside(frame, clip)
        if hidden and len(offscreen) >= offscreen_cap:
            continue  # nothing left to collect down there, and it never counted on screen
        kids = list(children(node))
        label, inherited = own_label, False
        if not label and role in AX_LABEL_DESCENDANT_ROLES:
            label = descendant_label(kids, children, attrs)
        if not label and parent_label:
            label, inherited = parent_label, True
        emitted = False
        duplicate = inherited and parent_emitted  # the parent already stands for this label
        nameless_group = role == "AXGroup" and not own_label  # a Chromium layout box, not a control
        # A click lands on the centre, so a node centred off the display is no item, though it
        # crosses the display. It stays reachable by AXPress, and its children are judged as their own.
        visible = not hidden and clickable(frame) and centred_in(frame, clip)
        if label and not duplicate and not nameless_group and not wrapper:
            if visible:
                pressable = AX_PRESS in actions(node)
                if pressable or role in AX_ACTIONABLE_ROLES:
                    x, y, w, h = frame
                    found.append(AxNode(role=role, label=label, x=x, y=y, w=w, h=h, pressable=pressable, ref=node, path=path))
                    emitted = True
            elif frame is not None and len(offscreen) < offscreen_cap and AX_PRESS in actions(node):
                x, y, w, h = frame
                offscreen.append(AxNode(role=role, label=label, x=x, y=y, w=w, h=h, pressable=True, ref=node, path=path))
        child_label = own_label if role in AX_LABEL_PARENT_ROLES else ""
        inner = clipped(clip, frame) if role in AX_CLIP_ROLES else clip
        queue.extend((kid, child_label, emitted, hidden, key, inner, path) for kid in kids)
    return found, offscreen, False


# ---------------------------------------------------------------- a page's main text

TEXT_ATTRS = ("AXRole", "AXSubrole", "AXValue", "AXChildren")  # what the text reader reads of each node, in one call
MAIN_TEXT_CHARS = 7000  # what the brain gets of a page's text: an article's worth
TEXT_NODE_CAP = 8000
TEXT_TIME_CAP = 1.5
LINK_SHARE = 0.6  # a block whose text is mostly links (and holds a few of them) is a link list: nav, related, tags
LINK_MIN = 3


def page_text(
    root,
    get: Callable[..., dict],
    node_class: Callable[[str, str], str],
    keep: Callable[[str], bool] = lambda text: True,
    cap: int = MAIN_TEXT_CHARS,
    node_cap: int = TEXT_NODE_CAP,
    time_cap: float = TEXT_TIME_CAP,
    clock: Callable[[], float] = time.monotonic,
) -> str:
    """A web page's main text, in document order, off-screen paragraphs included: every static text under `root` (an
    AXWebArea), with headings on lines of their own. What is interface is the content model's (karta.harness.content,
    `node_class(role, subrole)`): a node it calls "interface" (a control, a bar, a landmark around the page) is left
    out with all under it, a "field" too, and an "editor" (a popover, sheet, dialog or form) that holds a field; only
    the "main" landmark when there is one. As prose, a block that is mostly links goes too, and a text `keep` says is
    the site's own (its learned boilerplate). At most `cap` characters, cut at a word. `get(node)` gives a node's
    TEXT_ATTRS (one batched read on the Mac)."""
    deadline = clock() + time_cap
    seen = [0]

    def read(node, depth: int, heading: bool) -> tuple[list[tuple[str, bool, bool]], bool, bool]:
        """(pieces: text, in a link, in a heading; whether the subtree holds the main landmark's text; whether it holds
        a field)."""
        if seen[0] >= node_cap or clock() >= deadline or depth > 200:
            return [], False, False
        seen[0] += 1
        a = get(node)
        role, subrole = str(a.get("AXRole") or ""), str(a.get("AXSubrole") or "")
        kind = node_class(role, subrole)
        if kind in ("interface", "field"):
            return [], False, kind == "field"
        if role == "AXStaticText":
            value = a.get("AXValue")
            text = " ".join(value.split()) if isinstance(value, str) else ""
            return ([(text, False, heading)] if text and keep(text) else []), False, False
        heading = heading or role == "AXHeading"
        pieces: list[tuple[str, bool, bool]] = []
        main_pieces: list[tuple[str, bool, bool]] = []
        field = False
        for kid in a.get("AXChildren") or ():
            got, in_main, has_field = read(kid, depth + 1, heading)
            (main_pieces if in_main else pieces).extend(got)
            field = field or has_field
        if kind == "editor" and field:
            return [], False, True  # a form: its labels and buttons, never the page's prose
        if kind == "main":
            return pieces + main_pieces, True, field
        if main_pieces:
            return main_pieces, True, field  # the main landmark below: only its text counts
        if role == "AXLink":
            return [(t, True, h) for t, _, h in pieces], False, field
        links = [t for t, linked, _ in pieces if linked]
        total = sum(len(t) for t, _, _ in pieces)
        if len(links) >= LINK_MIN and sum(len(t) for t in links) > LINK_SHARE * total:
            return [], False, field
        return pieces, False, field

    pieces, _, _ = read(root, 0, False)
    lines: list[str] = []
    was_heading = True
    for text, _, heading in pieces:
        if heading or was_heading or not lines:
            lines.append(text)
        else:
            lines[-1] += " " + text
        was_heading = heading
    out = "\n".join(re.sub(r"\s+([,.;:!?)])", r"\1", line) for line in lines).strip()
    if len(out) <= cap:
        return out
    return out[:cap].rsplit(" ", 1)[0].rstrip(" ,;:") + " …"
