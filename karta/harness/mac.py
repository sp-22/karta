"""The real Mac as a HarnessEnv: karta.base perception and Karta's MacEnv underneath, Items on top.

Reading: the bounded AX walk (ax_walk.walk_actionable) with a richer label for icon-only buttons,
the OCR (ocr_lines, merge_blocks, merge_with_origins: AX wins over OCR), off-screen controls
(offscreen_controls), regions (models.Screen.region), rows (screen.with_rows), MacEnv's menu commands,
HUD exclusion and browser page frame (chrome). Acting: MacEnv's keymap, paste, settle and open().

The pure helpers at the top are what the tests cover; karta.core.mac_env is imported only when a
MacHarnessEnv is made, since importing it points the OCR at the window for the whole process.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from karta.harness import content
from karta.harness.screen import clean_ocr, with_rows
from karta.harness.types import Item, Screen

# AX role (or subrole) -> one of types.KINDS. Subroles win: a radio button in a tab bar is a tab.
AX_KINDS = {
    "AXButton": "button",
    "AXMenuButton": "button",
    "AXPopUpButton": "button",
    "AXDisclosureTriangle": "button",
    "AXIncrementor": "button",
    "AXTextField": "field",
    "AXTextArea": "field",
    "AXSearchField": "field",
    "AXComboBox": "field",
    "AXSecureTextField": "field",
    "AXLink": "link",
    "AXRow": "row",
    "AXCell": "row",
    "AXOutlineRow": "row",
    "AXTableRow": "row",
    "AXStaticText": "text",
    "AXHeading": "heading",
    "AXMenuItem": "menu",
    "AXMenuBarItem": "menu",
    "AXMenu": "menu",
    "AXCheckBox": "checkbox",
    "AXRadioButton": "checkbox",
    "AXSwitch": "checkbox",
    "AXToggle": "checkbox",
    "AXTab": "tab",
    "AXTabButton": "tab",
    "AXImage": "image",
}
OPEN_TIMEOUT = 6.0  # seconds an app gets to come to the front after `open -a` (a cold start takes seconds)
THUMB_POLL = 0.05  # seconds between thumbnails while waiting for the screen to change (as karta.core.fast's POLL)
PIXEL_DIFF = 12.0  # largest 8-bit difference on the thumbnail above which two frames differ (a caret blink does)
STILL = 0.12  # seconds the thumbnail holds still before a changed screen counts as settled
STILL_CAP = 1.5  # at most this long waiting for stillness: video never stops
NO_PIXELS_POLL = 0.15  # no thumbnail to watch: re-read this often instead
FIELD_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
PLAIN_FIELDS = {"AXTextField", "AXTextArea", "AXSearchField"}  # a native field whose AXValue can be emptied directly
FOCUS_DEPTH = 8  # ancestors climbed from the focused element to the field it types into (a combo box's own editor)
CHECK_ROLES = {"AXCheckBox", "AXRadioButton", "AXSwitch", "AXToggle"}
# Where a name comes from, best first. AXValue is the text in it, never its name; the last three
# name icon-only buttons: a tooltip, a developer id ("composeButton"), a subrole's description ("close button").
NAME_ATTRS = ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp")
SHORT_VALUE = 120  # as ax_walk's AX_VALUE_CHARS: a longer AXValue is content, never a name
REGION_WORDS = {
    "middle-center": "middle",
    "top-center": "top",
    "bottom-center": "bottom",
    "middle-left": "left",
    "middle-right": "right",
}
PAGE_LISTS = ("Tab >", "Window >", "History >", "Bookmarks >")  # page titles, not commands (wordlists: tailored)
SCROLL_LINES = 10  # lines per scroll step, when the wheel is the fallback
SCROLL_PAGE = 0.8  # of the visible height per scroll step through the scroll bar's AXValue
SCROLL_DEPTH = 8  # levels searched under the front window for its scroll area
SCROLL_NODES = 400  # elements looked at, at most, for it
CONFIRM_WAIT = 1.0  # after AXConfirm, how long the screen gets to change before Return is pressed instead
CANCEL_WAIT = 0.6  # after an AX cancel, as after an AXPress: no change by then and the key is pressed
CANCEL_DEPTH = 30  # ancestors climbed from the focused element to the menu, popover, sheet or dialog it is in
CANCEL_ROLES = {"AXMenu", "AXPopover", "AXSheet"}  # what AXCancel may close (never a field: it clears a search box)
ADDRESS_KEYS = "cmd+l"  # the browser's address bar
AT_FOCUS_KEYS = ("return", "tab")  # act on the focused element: pressed only while the focus is in the app in front
EXTRA_KEYS = {"[": 33, "]": 30, "-": 27, "=": 24, "home": 115, "end": 119, "pageup": 116, "pagedown": 121, "forwarddelete": 117}


def moved(frame, base) -> bool:
    """Two thumbnails (fast._thumb) differ somewhere: a new size, or one cell by more than PIXEL_DIFF."""
    if frame is None or base is None:
        return True
    return frame.shape != base.shape or float(abs(frame - base).max()) > PIXEL_DIFF


def await_pixels(
    grab: Callable,
    base,
    deadline: float,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Thumbnails from `grab` every THUMB_POLL s until one differs from `base` (False once `deadline` passes), then
    until they hold still for STILL s (at most STILL_CAP). True when the pixels moved. No thumbnail to watch (grab
    gives None): True after NO_PIXELS_POLL, so the caller re-reads as the old poll did."""
    while True:
        frame = grab()
        if frame is None or base is None:
            sleep(min(NO_PIXELS_POLL, max(0.0, deadline - clock())))
            return True
        if moved(frame, base):
            break
        if clock() >= deadline:
            return False
        sleep(THUMB_POLL)
    since = clock()
    cap = since + STILL_CAP
    while clock() < cap:
        sleep(THUMB_POLL)
        now = grab()
        if now is None:
            break
        if moved(now, frame):
            frame, since = now, clock()
        elif clock() - since >= STILL:
            break
    return True


def walk_app(
    app_el: Any,
    focused: Any,
    children: Callable[[Any], Any],
    attrs: Callable[[Any], Any],
    actions: Callable[[Any], Any],
    subrole: Callable[[Any], object],
    w_pt: float,
    h_pt: float,
) -> tuple[list, list]:
    """The AX walk of one app as a read sees it: its focused window (with the strips it is made of in full screen),
    its dialogs and panels and its menu bar, never its other windows behind; each control on screen inside the page or
    list that shows it. (on-screen nodes, off-screen pressable nodes)"""
    from karta.base.ax_walk import app_children, walk_actionable

    found, hidden, _ = walk_actionable(
        app_el, app_children(app_el, focused, children, attrs, subrole), attrs, actions, w_pt, h_pt
    )
    return found, hidden


WALK_ATTRS = ("AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXValue", "AXPosition", "AXSize", "AXChildren")


def ax_reader(
    batch: Callable[[Any, tuple], dict], single: Callable[[Any, str], object], frame_of: Callable[[Any, Any], Any]
) -> tuple[Callable, Callable, Callable]:
    """The walk's (attrs, children, subrole) over one batched read per node: `batch(node, WALK_ATTRS)` once, cached
    for the read; what else a label needs (placeholder, help, identifier) is read singly, only for a control without
    a title. Each read is a call into the app, so one call instead of six is most of a walk's time."""
    from karta.base.ax_walk import AxAttrs, node_identity

    cache: dict = {}

    def got(node) -> dict:
        key = node_identity(node)
        if key not in cache:
            cache[key] = batch(node, WALK_ATTRS)
        return cache[key]

    def attrs(node) -> AxAttrs:
        a = got(node)
        role = str(a.get("AXRole") or "")
        label = rich_label(role, lambda key: a[key] if key in a else single(node, key))
        return AxAttrs(role, label, frame_of(a.get("AXPosition"), a.get("AXSize")))

    def children(node) -> list:
        return list(got(node).get("AXChildren") or [])

    def subrole(node) -> object:
        return got(node).get("AXSubrole")

    return attrs, children, subrole


def kind_of(role: str, subrole: str = "") -> str:
    """An AX role as one plain word from types.KINDS."""
    return AX_KINDS.get(subrole) or AX_KINDS.get(role) or "other"


def identifier_words(identifier: str) -> str:
    """A developer id as words ("composeButton" -> "compose button"); "" for machine ids ("_NS:12", uuids)."""
    if not identifier or identifier.startswith("_") or any(c.isdigit() for c in identifier) or len(identifier) > 40:
        return ""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", identifier)
    words = re.sub(r"[-_.]+", " ", spaced).lower().split()
    return " ".join(words) if words and all(w.isalpha() for w in words) else ""


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def name_of(attrs: Mapping[str, object]) -> str:
    """What an element is called, without its value: title, description, placeholder, help, then the
    icon-only fallbacks: a readable identifier, and the role description only when the element has a subrole (it then
    describes the subrole: "close button"). A role description alone only repeats the role, in the app's language."""
    for key in NAME_ATTRS:
        if text := _text(attrs.get(key)):
            return text
    if text := identifier_words(_text(attrs.get("AXIdentifier"))):
        return text
    return _text(attrs.get("AXRoleDescription")) if _text(attrs.get("AXSubrole")) not in ("", "AXUnknown") else ""


def name_and_value(attrs: Mapping[str, object]) -> tuple[str, str, bool | None]:
    """(name, value, checked) from an element's AX attributes.

    A field keeps its text as the value and is named by its label ("text field" when it has none); a
    checkbox's AXValue (0/1) is its checked state; anything else with no name is named by a short value.
    """
    role = _text(attrs.get("AXRole"))
    name, raw = name_of(attrs), attrs.get("AXValue")
    if role in CHECK_ROLES or _text(attrs.get("AXSubrole")) in CHECK_ROLES:
        checked = bool(raw) if isinstance(raw, int | float | bool) else None
        return name or _text(raw)[:SHORT_VALUE], "", checked
    value = _text(raw) if isinstance(raw, str) else ""
    if role in FIELD_ROLES:
        return name or "text field", value[:300], None
    if not name and 0 < len(value) <= SHORT_VALUE:
        return value, "", None
    return name, value[:SHORT_VALUE] if value != name else "", None


def named_by_hint(attrs: Mapping[str, object], name: str) -> bool:
    """The name is only a tooltip, a developer id or a role description (an icon with no label of its own), never
    what the element shows: its title, description, placeholder or value."""
    if not name or name in {_text(attrs.get(k)) for k in ("AXTitle", "AXDescription", "AXPlaceholderValue", "AXValue")}:
        return False
    return name in {
        _text(attrs.get("AXHelp")),
        identifier_words(_text(attrs.get("AXIdentifier"))),
        _text(attrs.get("AXRoleDescription")),
    }


def url_text(value: object) -> str:
    """A link's AXURL as text: an NSURL's absoluteString, or the string itself; "" for anything else."""
    if value is None:
        return ""
    absolute = getattr(value, "absoluteString", None)
    text = str(absolute()) if callable(absolute) else value if isinstance(value, str) else ""
    return text.strip() if re.match(r"^[a-z][a-z0-9+.-]*:", text.strip(), re.I) else ""


def region_word(cell: str) -> str:
    """Perception's "middle-center" cell as one of types.REGIONS ("middle")."""
    return REGION_WORDS.get(cell, cell)


def profile_from_title(title: str, profiles: list[str]) -> str | None:
    """The browser profile a Chromium window title ends with: one of the browser's known profile names (its Local
    State) after the last " - " ("Inbox - Brave - Work" -> "Work"); else None. Never the browser's name, in any
    language or brand."""
    tail = [p for p in profiles if p and title.endswith(f" - {p}")]
    return max(tail, key=len) if tail else None


def rich_label(role: str, get: Callable[[str], object]) -> str:
    """The walk's label: ax_walk's (title, description, a short value) plus placeholder, help, identifier
    and role description for controls, so an icon-only button is kept and named."""
    for key in ("AXTitle", "AXDescription"):
        if text := _text(get(key)):
            return text
    from karta.base.ax_walk import AX_ACTIONABLE_ROLES

    if role not in AX_ACTIONABLE_ROLES:
        value = _text(get("AXValue"))
        return value if len(value) <= SHORT_VALUE else ""
    if role in FIELD_ROLES:  # a field's value is its text: name it by its label, else say it is a field
        return _text(get("AXPlaceholderValue")) or _text(get("AXHelp")) or _title_element(get) or "text field"
    value = get("AXValue")
    if isinstance(value, str) and 0 < len(value.strip()) <= SHORT_VALUE:
        return _text(value)
    return name_of({k: get(k) for k in ("AXPlaceholderValue", "AXHelp", "AXIdentifier", "AXRoleDescription", "AXSubrole")})


def _title_element(get: Callable[[str], object]) -> str:
    """A field's visible label, linked through AXTitleUIElement (native forms)."""
    label = get("AXTitleUIElement")
    if label is None or isinstance(label, str):
        return ""
    from karta.base.macos import _ax_attr

    return _text(_ax_attr(label, "AXValue")) or _text(_ax_attr(label, "AXTitle"))


@dataclass
class Ref:
    """How to act on an item: its frame in screen points, and its AX element when it has one."""

    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0
    element: Any = None
    pressable: bool = False
    path: tuple = ()  # the element's ancestors, outermost first (ax_walk): items of one row or card share a deep one
    # where it sits, for the content model (content.classify), by its ancestors (`mark_tree`; text read off the pixels
    # by the frames it lies in): the landmarks around it (AX subroles, outermost first), the editor it is in (a
    # popover, sheet, dialog or web form: its key), inside a toolbar or menu bar, inside the web page
    landmarks: tuple = ()
    editor: Any = None
    bar: bool = False
    web: bool = False

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


DETAIL_ATTRS = (
    "AXRole", "AXSubrole", "AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp", "AXIdentifier",
    "AXRoleDescription", "AXValue", "AXEnabled", "AXFocused",
)  # fmt: skip


LOCK_FLAG = "CGSSessionScreenIsLocked"  # in the login session's dictionary while the lock screen is up
LOCK_APP = "loginwindow"


def screen_locked(session: Callable[[], object] | None = None, front: Callable[[], str] | None = None) -> bool:
    """Whether the Mac's screen is locked: Quartz's session dictionary says so (CGSessionCopyCurrentDictionary,
    CGSSessionScreenIsLocked), or the lock screen's app, loginwindow, is in front. Read-only; False when it cannot
    tell. `session` and `front` are for tests."""
    try:
        if session is None:
            import Quartz

            session = Quartz.CGSessionCopyCurrentDictionary
        info = session() or {}
        if bool(info.get(LOCK_FLAG, False)):
            return True
    except Exception:
        pass
    try:
        if front is None:
            from karta.core.fast import frontmost_app_and_pid

            def front() -> str:
                return frontmost_app_and_pid()[0]

        return str(front() or "") == LOCK_APP
    except Exception:
        return False


class MacHarnessEnv:
    """types.HarnessEnv on the real Mac. Reads never act; every action replaces, never appends.

    Waiting for the screen watches a thumbnail of the front window (or page), which costs milliseconds, and reads
    the screen in full (Accessibility and OCR, up to half a second) only once the pixels have settled. The time
    spent is counted: `reads` and `read_seconds` (full reads), `wait_seconds` (waiting for a change, reads left
    out) and `act_seconds` (inside the actions, reads and waits left out)."""

    def __init__(self, app: str | None = None, ocr: bool | None = None, thumb: Callable | None = None):
        from karta.core import mac_env

        self._m = mac_env
        self._env = mac_env.MacEnv(app)  # activates `app` when given; None reads whatever is in front
        self._ocr = ocr  # None: OCR browsers and AX-poor apps (MacEnv's rule); True/False: always / never
        self._last: Screen | None = None
        self._web_ax: set[int] = set()  # apps already asked to expose web content
        self._profile_names: dict[str, list[str]] = {}
        self._langs: dict[int, str] = {}
        self.refused = ""  # why the last action did nothing or went wrong (the focus was not where it should be), else ""
        self.pressed = ""  # how the last press was done: by Accessibility, a menu command, or "key"
        self.scrolled = ""  # how the last scroll was done: "scroll bar" (its AXValue) or "wheel"
        self._watch(thumb)

    def _watch(
        self,
        thumb: Callable | None = None,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """The pixel source for change detection (tests pass their own) and the time counters."""
        self._thumb = thumb or self._front_thumb
        self._clock, self._sleep = clock, sleep
        self._read_thumb = None  # the thumbnail as the last full read began
        self._act_thumb = None  # the thumbnail just before the last action, until a wait uses it
        self._acting = 0
        self.reads, self.read_seconds, self.wait_seconds, self.act_seconds = 0, 0.0, 0.0, 0.0

    @property
    def app(self) -> str:
        return self._env.app

    def locked(self) -> bool:
        """The screen is locked (the session's own flag, read-only): nothing can be read or done until it is not."""
        return screen_locked()

    # ---------------------------------------------------------------- reading

    def screen(self) -> Screen:
        if self._last is None:
            started = self._clock()
            self._read_thumb = self._grab()  # before the read: a change during it shows as a change after it
            self._last = self._read()
            self.reads += 1
            self.read_seconds += self._clock() - started
        return self._last

    def _read(self) -> Screen:
        import ApplicationServices as AS

        from karta.base import macos
        from karta.base import perception as p
        from karta.base.config import MAX_OPTIONS, MIN_OCR_CONFIDENCE
        from karta.base.models import Item as UpItem
        from karta.base.platform_adapter import using

        env, m = self._env, self._m
        prompt = self._system_prompt()
        if prompt is not None:
            return prompt
        browser = env.app in m.BROWSERS
        if browser and env._desktop.page is None:
            env._find_page()
        with using(env._desktop):
            cap = p.capture(browser=env.app if browser else "")
        app_el = AS.AXUIElementCreateApplication(env.pid)
        AS.AXUIElementSetMessagingTimeout(app_el, macos.AX_MESSAGE_TIMEOUT)
        if env.pid not in self._web_ax:
            # Chromium browsers expose a page's content only once asked, per process: ask for every app Karta
            # reads, not just the one in front at start (a restarted browser forgets it).
            AS.AXUIElementSetAttributeValue(app_el, "AXManualAccessibility", True)
            self._web_ax.add(env.pid)

        attrs, children, subrole = ax_reader(macos.ax_batch, macos._ax_attr, macos.ax_frame_of)
        w_pt, h_pt = cap.size_pt
        window = m._attr(app_el, "AXFocusedWindow")
        try:
            found, hidden = walk_app(app_el, window, children, attrs, macos._ax_actions, subrole, w_pt, h_pt)
        except Exception:
            found, hidden = [], []
        # The menu bar's own titles ("File") are no commands: the commands come in whole from menu_items, below.
        nodes = [n for n in found[:MAX_OPTIONS] if n.label and n.role != "AXMenuBarItem"]
        controls = p.to_ax_items(nodes, cap.scale)
        want_ocr = self._ocr if self._ocr is not None else (browser or len(nodes) < m.AX_ENOUGH)
        blocks: list[UpItem] = []
        if want_ocr:
            lines, _, _ = p.ocr_lines(cap, env._ocr_cache)
            kept = [(t, c, b) for t, c, b in ((clean_ocr(t), c, b) for t, c, b in lines) if t and c >= MIN_OCR_CONFIDENCE]
            blocks = p.to_items(p.merge_blocks(kept), MAX_OPTIONS)
        merged = p.merge_with_origins(blocks, controls, MAX_OPTIONS)
        merged = drop_field_echoes(merged, nodes, lambda n: macos.ax_value(n.ref) or "", cap.scale)

        items: list[Item] = []
        centres: list[float] = []
        halves: list[float] = []
        for up, origin in merged:
            x, y = up.x1 / cap.scale, up.y1 / cap.scale
            w, h = (up.x2 - up.x1) / cap.scale, (up.y2 - up.y1) / cap.scale
            region = region_word(cap.region(up))
            if origin is None:
                item = Item(name=up.text, kind="text", region=region, source="ocr", ref=Ref(x, y, w, h))
            else:
                node = nodes[origin]
                item = self._ax_item(node, region, up.source, Ref(x, y, w, h, node.ref, node.pressable, node.path))
            items.append(item)
            centres.append(y + h / 2)
            halves.append(max(1.0, h) / 2)
        items = with_rows(items, centres, halves)
        items = self._without_hud(items)
        if browser and cap.window:
            top = cap.window[1]  # the page's top edge (FastDesktop reports the AXWebArea)
            items = [mark_chrome(i, top) for i in items]
        items += self._focused_extra(items, AS)
        items, busy_around = mark_tree(items, lambda el: macos._ax_attr(el, "AXParent"), self._ancestor_attrs, browser=browser)
        items += [
            self._ax_item(n, "", "ax", Ref(n.x, n.y, n.w, n.h, n.ref, True, n.path), offscreen=True)
            for n in p.offscreen_controls(hidden, [UpItem(0, i.name, 1.0, 0, 0, 0, 0) for i in items])
        ]
        items += [
            Item(name=label, kind="menu", menu=True, shortcut=keys, ref=Ref(element=ref, pressable=True))
            for label, ref, keys in m.menu_items(env.pid, shortcuts=True)
            if not (browser and label.startswith(PAGE_LISTS))
        ]
        title = _text(m._attr(window, "AXTitle")) if window is not None else ""
        url, busy, lang = self._page_state(window) if browser else (None, False, "")
        windows, window_at = self._windows(app_el, window)
        scr = Screen(
            app=env.app,
            items=items,
            url=url,
            busy=busy or busy_around,
            window=title,
            profile=profile_from_title(title, self._profiles(env.app)) if browser else None,
            lang=lang or self._app_lang(env.pid),
            windows=windows,
            window_at=window_at,
        )
        return content.classify(scr, learn=True)  # interface or content, once per read; the read teaches boilerplate

    def _windows(self, app_el, window) -> tuple[int, str]:
        """The app's standard windows open (AXWindows of subrole AXStandardWindow) and where its focused window is
        drawn: a new window shows as one more, or as another frame in front. (0, "") when they cannot be read."""
        from karta.base import macos

        try:
            count = sum(
                _text(macos._ax_attr(w, "AXSubrole")) == "AXStandardWindow" for w in macos._ax_attr(app_el, "AXWindows") or []
            )
            frame = macos._ax_frame(window) if window is not None else None
        except Exception:
            return 0, ""
        return int(count), ",".join(str(round(v)) for v in frame) if frame else ""

    def _system_prompt(self) -> Screen | None:
        """A prompt the system put up in front of the app (facts.SYSTEM_PROMPTERS: a privacy, security or file-access
        alert in a process of its own), read from that process: every item of its window is the prompt. None when no
        such process is in front."""
        import ApplicationServices as AS

        from karta.base import macos
        from karta.core import fast
        from karta.harness.facts import SYSTEM_PROMPTERS

        try:
            name, pid = fast.frontmost_app_and_pid()
        except Exception:
            return None
        if name not in SYSTEM_PROMPTERS or pid == self._env.pid:
            return None
        app_el = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(app_el, macos.AX_MESSAGE_TIMEOUT)
        attrs, children, subrole = ax_reader(macos.ax_batch, macos._ax_attr, macos.ax_frame_of)
        window = self._m._attr(app_el, "AXFocusedWindow")
        try:
            found, _hidden = walk_app(app_el, window, children, attrs, macos._ax_actions, subrole, PROMPT_BOUNDS, PROMPT_BOUNDS)
        except Exception:
            return None
        items = [self._ax_item(n, "", "ax", Ref(n.x, n.y, n.w, n.h, n.ref, n.pressable, n.path)) for n in found if n.label]
        items, _busy = mark_tree(items, lambda el: macos._ax_attr(el, "AXParent"), self._ancestor_attrs)
        title = _text(self._m._attr(window, "AXTitle")) if window is not None else ""
        return content.classify(
            Screen(app=name, items=[replace(i, dialog=True, panel=False) for i in items], window=title, lang=self._app_lang(pid))
        )

    @staticmethod
    def _ancestor_attrs(element) -> dict:
        from karta.base.macos import ax_batch, ax_frame_of

        got = ax_batch(element, (*ANCESTOR_ATTRS, "AXPosition", "AXSize"))
        return {**{k: got.get(k) for k in ANCESTOR_ATTRS}, "frame": ax_frame_of(got.get("AXPosition"), got.get("AXSize"))}

    def _profiles(self, browser: str) -> list[str]:
        """The browser's profile names, from its Local State (read once per process; [] for a single-profile one)."""
        if browser not in self._profile_names:
            from pathlib import Path

            from karta.harness.facts import browser_profiles

            self._profile_names[browser] = browser_profiles([browser], Path.home()).get(browser, [])
        return self._profile_names[browser]

    def _app_lang(self, pid: int) -> str:
        """The language the app shows its interface in: its bundle's preferred localization (NSBundle, per pid, read
        once), else the user's first preferred language; "" when neither can be read."""
        if pid not in self._langs:
            self._langs[pid] = app_language(pid)
        return self._langs[pid]

    def _ax_item(self, node, region: str, source: str, ref: Ref, offscreen: bool = False) -> Item:
        from karta.base.macos import _ax_attr, ax_batch

        attrs = ax_batch(node.ref, DETAIL_ATTRS) if node.ref is not None else {"AXRole": node.role}
        attrs["AXRole"] = attrs.get("AXRole") or node.role
        name, value, checked = name_and_value(attrs)
        if not name or (name == "text field" and node.label):
            name = node.label  # the walk found more: a parent's label, or a field's AXTitleUIElement
        kind = kind_of(node.role, _text(attrs.get("AXSubrole")))
        # a link's own address: one more attribute, read for links only (collect carries it to later steps)
        url = url_text(_ax_attr(node.ref, "AXURL")) if kind == "link" and node.ref is not None else ""
        return Item(
            name=name,
            kind=kind,
            url=url,
            multiline=str(attrs.get("AXRole") or "") == "AXTextArea",
            value=value,
            region=region,
            focused=bool(attrs.get("AXFocused")),
            enabled=attrs.get("AXEnabled") is not False,
            checked=checked,
            source=source,
            offscreen=offscreen,
            hint=kind != "field" and named_by_hint(attrs, name),
            secret="AXSecureTextField" in (_text(attrs.get("AXRole")), _text(attrs.get("AXSubrole"))),
            search="AXSearchField" in (_text(attrs.get("AXRole")), _text(attrs.get("AXSubrole"))),
            cancels=_text(attrs.get("AXSubrole")) == "AXCloseButton",
            ref=ref,
        )

    def _focused_extra(self, items: list[Item], AS) -> list[Item]:
        """The focused text area when the walk missed it (a note body, a chat box): where typing goes."""
        if any(i.focused for i in items):
            return []
        m = self._m
        focused = m._attr(AS.AXUIElementCreateApplication(self._env.pid), "AXFocusedUIElement")
        role = str(m._attr(focused, "AXRole") or "") if focused is not None else ""
        if role not in FIELD_ROLES:
            return []
        spot = m._Focused(focused)
        attrs = {k: m._attr(focused, k) for k in DETAIL_ATTRS}
        name, value, _ = name_and_value(attrs)
        if name == "text field":
            name = f"focused {role.removeprefix('AX').lower()}"
        return [
            Item(
                name=name,
                kind="field",
                value=value,
                focused=True,
                multiline=role == "AXTextArea",
                ref=Ref(spot.x, spot.y, spot.w, spot.h, focused),
            )
        ]

    def _without_hud(self, items: list[Item]) -> list[Item]:
        """Karta's own HUD shows the running command: its text must never become a target."""
        hud = self._m._hud_frame()
        if not hud:
            return items
        hx, hy, hw, hh = hud
        # only text read off the pixels: the HUD is not in any app's Accessibility tree, so an app's own control under
        # it (a chat's message box at the bottom of the screen) stays
        return [
            i for i in items if i.source != "ocr" or not (hx <= i.ref.centre[0] <= hx + hw and hy <= i.ref.centre[1] <= hy + hh)
        ]

    def page_text(self) -> str:
        """The front page's main text from its accessibility tree, off-screen paragraphs included (ax_walk.page_text,
        one batched read per node), by the content model: interface nodes (content.node_class) and the site's learned
        boilerplate (content.kept_text) left out; "" when the front window shows no web page."""
        import ApplicationServices as AS

        from karta.base import macos
        from karta.base.ax_walk import TEXT_ATTRS, page_text

        app_el = AS.AXUIElementCreateApplication(self._env.pid)
        AS.AXUIElementSetMessagingTimeout(app_el, macos.AX_MESSAGE_TIMEOUT)
        window = self._m._attr(app_el, "AXFocusedWindow")
        area = self._m._web_area(window) if window is not None else None
        if area is None:
            return ""
        started = self._clock()
        try:
            keep = content.kept_text(self.screen())
            return page_text(area, lambda node: macos.ax_batch(node, TEXT_ATTRS), content.node_class, keep)
        except Exception:
            return ""
        finally:
            self.read_seconds += self._clock() - started

    def calendar_events(self, day) -> list[str] | None:
        """The day's events across all calendars through EventKit (macos.calendar_events), read-only; None without
        access to the calendar."""
        from karta.base import macos

        try:
            return macos.calendar_events(day)
        except Exception:
            return None

    def _page_state(self, window) -> tuple[str | None, bool, str]:
        """The page's AXURL (no AppleScript), else the browser's front tab by AppleScript; whether the page says it is
        still loading (`page_busy`) and its language (AXLanguage), read off its web area in the same round trip."""
        from karta.base import macos

        area = self._m._web_area(window) if window is not None else None
        got = macos.ax_batch(area, PAGE_ATTRS) if area is not None else {}
        url, lang = got.get("AXURL"), language_tag(got.get("AXLanguage"))
        if url is not None:
            return (str(url.absoluteString()) if hasattr(url, "absoluteString") else str(url)), page_busy(got), lang
        try:
            return macos.browser_url(self._env.app), page_busy(got), lang
        except Exception:
            return None, page_busy(got), lang

    # ---------------------------------------------------------------- acting

    def _stale(self, min_wait: float = 0.1, max_wait: float = 0.0) -> None:
        self._env._settle(min_wait, max_wait)
        self._last = None

    # ---------------------------------------------------------------- waiting for the screen

    def _front_thumb(self):
        from karta.base import macos
        from karta.core import fast

        return fast._thumb(self._env._desktop.page or macos.frontmost_window_bounds(self._env.pid))

    def _grab(self):
        try:
            return self._thumb()
        except Exception:
            return None

    def _mark(self) -> None:
        """The picture just before an action: what the next wait compares against."""
        self._act_thumb = self._grab()

    def _base(self):
        """What a wait compares against: the picture before the last action, else as the last read began."""
        base, self._act_thumb = self._act_thumb, None
        return base

    @contextmanager
    def _acts(self):
        """Times an action (the outermost one only), its own reads and waits left out. An action starts with nothing
        refused: `refused` speaks of the last action only."""
        if not self._acting:
            self.refused = ""
        self._acting += 1
        started, spent = self._clock(), self.read_seconds + self.wait_seconds
        try:
            yield
        finally:
            self._acting -= 1
            if not self._acting:
                self.act_seconds += max(0.0, self._clock() - started - (self.read_seconds + self.wait_seconds - spent))

    def changed_since(self, before: str, timeout: float) -> bool:
        """After an action: True once the screen's fingerprint differs from `before`, False when `timeout` passes.
        Watches the thumbnail until it moves and settles, then reads once; pixels that never move get one read at
        the end (a focus or value change may not show in them)."""
        started, spent = self._clock(), self.read_seconds
        deadline = started + timeout
        try:
            base = self._base()
            if base is None and self._last is not None:
                if self._last.fingerprint() != before:
                    return True  # read since the last action, and it shows the change already
                base = self._read_thumb
            while True:
                if base is None:  # no picture from before: read now, as the old poll did
                    self._last = None
                    if self.screen().fingerprint() != before:
                        return True
                    base = self._read_thumb
                    if self._clock() >= deadline:
                        return False
                shifted = await_pixels(self._grab, base, deadline, self._clock, self._sleep)
                self._last = None
                if self.screen().fingerprint() != before:
                    return True
                if not shifted or self._clock() >= deadline:
                    return False
                base = self._read_thumb  # moved without changing what the screen says (a caret, a video): again
        finally:
            self.wait_seconds += max(0.0, self._clock() - started - (self.read_seconds - spent))

    _changed_since = changed_since  # an alias some callers still use

    def wait_change(self, timeout: float) -> bool:
        """Until the pixels move and settle (True) or `timeout` passes (False); the next read is a fresh one."""
        started = self._clock()
        try:
            base = self._base()
            if base is None:
                base = self._read_thumb if self._last is not None else self._grab()
            shifted = await_pixels(self._grab, base, started + timeout, self._clock, self._sleep)
            if shifted:
                self._env._find_page()  # a new page may have a new frame
            self._last = None
            return shifted
        finally:
            self.wait_seconds += max(0.0, self._clock() - started)

    # ---------------------------------------------------------------- acting

    def click(self, item: Item) -> None:
        with self._acts():
            self._click(item)

    def _click(self, item: Item) -> None:
        ref: Ref = item.ref
        if ref.element is not None and (item.menu or item.offscreen):
            self._mark()
            pressed = self._ax_press(ref.element)  # nothing on screen to aim at; AXPress needs no visibility
            self._stale()
            if pressed or item.menu:
                return
            item = self._into_view(item)  # an off-screen control that refused AXPress: scrolled to by AX, then clicked
            if not item.offscreen:  # never a click at a spot that is not the control
                self._mark()
                self._click_at(item.ref.centre)
                self._stale()
            return
        if ref.element is not None and ref.pressable:
            before = self.screen().fingerprint()
            self._mark()
            pressed = self._ax_press(ref.element)
            self._stale()
            if pressed and self.changed_since(before, timeout=0.6):
                return
        # Not pressable, or AXPress said yes and did nothing (Notes' toolbar): a real click at the centre.
        self._mark()
        self._click_at(ref.centre)
        self._stale()

    def _into_view(self, item: Item) -> Item:
        """An off-screen item scrolled into view by its own AXScrollToVisible, with its frame read again; the item as
        it was when it has no element, refuses, or has no frame after it."""
        ref: Ref = item.ref
        if not item.offscreen or ref.element is None or not self._ax_perform(ref.element, "AXScrollToVisible"):
            return item
        frame = self._ax_frame(ref.element)
        if not frame:
            return item
        self._last = None
        x, y, w, h = frame
        return replace(item, offscreen=False, ref=replace(ref, x=x, y=y, w=w, h=h))

    def set_text(self, item: Item, text: str) -> None:
        """Replace the field's text (empty text deletes it), never blind: the field must hold the keyboard focus first
        (`focus_holds`, re-taken by a click once), and its old text is selected through Accessibility, never by a select
        all whose keys may move the focus out of the field (a suggestion box takes it). An empty field is pasted into
        directly. Nothing is typed when the focus cannot be made sure (`refused` says why)."""
        with self._acts():
            self._set_text(item, text)

    def _set_text(self, item: Item, text: str) -> None:
        self._mark()
        self.refused = ""
        item = self._into_view(item)  # a field scrolled out of view is brought back before any click on it
        if not self._take_focus(item):
            self.refused = f"the keyboard focus is not in {item.name!r}: nothing typed"
            self._stale()
            return
        old = self._old_text(item)
        if old and not self._select_old(item, old):
            self.refused = f"could not select the text in {item.name!r} without losing its focus: nothing typed"
            self._stale()
            return
        if text:
            self._paste(text)
        elif old and self._old_text(item):  # selected, with the focus held (an AX-emptied field needs no key)
            self._keys("delete")
            if item.ref.element is not None and self._ax_value(item.ref.element):
                self.refused = f"{item.name!r} still holds text after delete"
            self._stale()

    def type_at_focus(self, text: str) -> None:
        """Type where the keyboard focus is, only when it is in something editable of the app in front (a terminal, a
        document's body, the address bar); else nothing (`refused`)."""
        with self._acts():
            self._mark()
            self.refused = ""
            if not self._focus_editable():
                self.refused = "the keyboard focus is in nothing editable of the app in front: nothing typed"
                return
            self._paste(text)

    def type_keys(self, text: str, item: Item | None = None) -> None:
        """Type `text` key by key, never a paste: a field that ignores a paste (some token and recipient fields) takes
        keystrokes. With `item`, into that field only: its focus made sure and its old text selected through
        Accessibility first (so the keys replace it); without, at the focus when it is editable. The caller keeps it
        short (repair.MAX_KEYS) and never sends a line break this way."""
        with self._acts():
            self._mark()
            self.refused = ""
            if item is not None:
                item = self._into_view(item)
                if not self._take_focus(item):
                    self.refused = f"the keyboard focus is not in {item.name!r}: nothing typed"
                    return
                old = self._old_text(item)
                if old and not self._select_old(item, old):
                    self.refused = f"could not select the text in {item.name!r} without losing its focus: nothing typed"
                    return
            elif not self._focus_editable():
                self.refused = "the keyboard focus is in nothing editable of the app in front: nothing typed"
                return
            self._type(text)
            self._stale()

    # ---------------------------------------------------------------- the keyboard focus, before any typing

    def _take_focus(self, item: Item) -> bool:
        """Give the field the keyboard focus and make sure it has it: the AX focus, and a real click too in a browser
        (its AX focus can leave the keyboard elsewhere) or for a field read off the pixels; then the system's focused
        element must be the field, inside it or its editor (`focus_holds`). One more click when it is not."""
        ref: Ref = item.ref
        focused = ref.element is not None and self._ax_focus(ref.element)
        if not focused or self._env.app in self._m.BROWSERS:
            self._click_at(ref.centre)
        for attempt in range(2):
            self._pause(0.15)
            if self._holds_focus(item):
                return True
            if not attempt:
                self._click_at(ref.centre)  # the focus went elsewhere: back by a click, and checked again
        return False

    def _holds_focus(self, item: Item) -> bool:
        ref: Ref = item.ref
        focused = self._ax_focused()
        frame = self._ax_frame(focused) if focused is not None and ref.element is None else None
        return focus_holds(ref.element, focused, self._ax_parent, self._ax_editable, frame, ref.centre)

    def _focus_editable(self) -> bool:
        """The keyboard focus is in something editable of the app in front."""
        focused = self._ax_focused()
        return focused is not None and self._ax_editable(focused) and self._ax_pid(focused) in (None, self._front_pid())

    def _old_text(self, item: Item) -> str:
        """What the field holds now: its AX value, else (read off the pixels) what the read saw."""
        element = item.ref.element
        return (self._ax_value(element) or "") if element is not None else (item.value or "")

    def _select_old(self, item: Item, old: str) -> bool:
        """Select the field's text so what comes next replaces it: its AXSelectedTextRange set to the whole text and
        read back; else, for a native app's plain field, its AXValue emptied and read back; select all (cmd+a) only as
        the last resort, and only while the field still holds the focus after it (in a combo box it can move the focus
        to the suggestion list: then the field is clicked again and nothing is typed)."""
        element = item.ref.element
        if element is not None:
            if self._ax_select(element, len(old)):
                return True
            if self._env.app not in self._m.BROWSERS and self._ax_clear(element):
                return True
        self._shortcut("cmd+a")  # Edit > Select All by AX when the app has it, else the key
        self._pause(0.05)
        if self._holds_focus(item):
            return True
        self._click_at(item.ref.centre)
        return False

    # the Mac calls the text entry makes, one each (tests stand in for them)

    def _ax_focused(self):
        from karta.base import macos

        return macos.ax_focused_element()

    def _ax_parent(self, element):
        from karta.base import macos

        return macos._ax_attr(element, "AXParent")

    def _ax_editable(self, element) -> bool:
        """A text element: a field's role, or a text selection of its own (a web editor, a terminal's text)."""
        from karta.base import macos

        return _text(macos._ax_attr(element, "AXRole")) in FIELD_ROLES or macos.ax_selected_range(element) is not None

    def _ax_frame(self, element):
        from karta.base import macos

        return macos._ax_frame(element)

    def _ax_pid(self, element) -> int | None:
        from karta.base import macos

        return macos.ax_pid(element)

    def _front_pid(self) -> int:
        from karta.core import fast

        return fast.frontmost_app_and_pid()[1]

    def _ax_value(self, element) -> str | None:
        from karta.base import macos

        return macos.ax_value(element)

    def _ax_focus(self, element) -> bool:
        from karta.base import macos

        return macos.ax_focus(element) and bool(macos._ax_attr(element, "AXFocused"))

    def _ax_select(self, element, length: int) -> bool:
        from karta.base import macos

        return macos.ax_select_range(element, 0, length) and macos.ax_selected_range(element) == (0, length)

    def _ax_clear(self, element) -> bool:
        from karta.base import macos

        role = _text(macos._ax_attr(element, "AXRole"))
        return role in PLAIN_FIELDS and macos.ax_set_value(element, "") and not (macos.ax_value(element) or "")

    def _click_at(self, point: tuple[float, float]) -> None:
        from karta.base import macos

        macos.click_at(point)

    def _ax_attr(self, element, name: str):
        from karta.base import macos

        return macos._ax_attr(element, name)

    def _ax_actions(self, element) -> list[str]:
        from karta.base import macos

        return macos._ax_actions(element)

    def _ax_perform(self, element, action: str) -> bool:
        from karta.base import macos

        return macos.ax_perform(element, action)

    def _ax_press(self, element) -> bool:
        return self._ax_perform(element, "AXPress")

    def _ax_set(self, element, name: str, value) -> bool:
        from karta.base import macos

        return macos.ax_set(element, name, value)

    def _front_window(self):
        import ApplicationServices as AS

        return self._ax_attr(AS.AXUIElementCreateApplication(self._env.pid), "AXFocusedWindow")

    def _menu_items(self) -> list[tuple]:
        """The app's enabled menu commands as (name, element, key equivalent), all of them (a browser's History menu
        included, which a screen read leaves out)."""
        return self._m.menu_items(self._env.pid, shortcuts=True)

    def _wheel(self, lines: int) -> None:
        from karta.base import macos

        macos.scroll(lines)

    def _pause(self, seconds: float) -> None:
        time.sleep(seconds)

    def _type(self, text: str) -> None:
        from karta.base import macos

        macos.type_text(text)

    def _paste(self, text: str) -> None:
        """Paste (cmd+v stays the way text goes in), the user's clipboard put back once the focused element's value
        changed (the app has read the clipboard) or fast.PASTE_WAIT passed."""
        from karta.base import macos
        from karta.core import fast

        macos.check_abort()
        focused = self._ax_focused()
        was = self._ax_value(focused) if focused is not None else None
        landed = None if was is None else (lambda: self._ax_value(focused) not in (None, was))
        fast.paste(text, lambda: self._keys("cmd+v"), landed)
        self._stale()

    def _keys(self, keys: str) -> None:
        """One key or chord as events (mac_env's key map, with the few keys it lacks: "[" for back, page keys), posted
        through macos (the abort corner checked, its modifier flags explicit, the key always let go)."""
        import Quartz

        from karta.base import macos

        mods, key = parse_keys(keys, self._m.MODIFIERS)
        codes = {**self._m.KEYS, **EXTRA_KEYS}
        if key not in codes:
            raise ValueError(f"unknown key {key!r} in {keys!r}")
        flags = 0
        for mod in mods:
            flags |= self._m.MODIFIERS[mod]
        macos.check_abort()
        macos._down_then_up(lambda down: macos._flagged(Quartz.CGEventCreateKeyboardEvent(None, codes[key], down), flags))

    def press(self, keys: str) -> None:
        """Do what the key is for, through Accessibility where the app exposes it; the key is the fallback, and after
        it the focus or the state is checked (`pressed` says how it was done, `refused` why nothing was or what went
        wrong):
        - cmd+l in a browser: its address field (by structure) given the focus and its text selected by AX, else the
          app's menu command of that key equivalent, else the key; then the focus must be in something editable;
        - escape: AXCancel of the menu, popover, sheet or dialog the focus is in, or its cancel button
          (AXCancelButton), when that changes the screen; else the key;
        - return: one key, never with a second way after it (it may send: a message, a payment). A search's or the
          address bar's return, which sends nothing, is `confirm_or_return` (AXConfirm first, the key as fallback);
        - any other ⌘ shortcut: the app's own enabled menu command with that key equivalent, pressed by AX; else the
          key (cmd+[ is History > Back in a browser, Go > Back in a file browser);
        - return and tab go only to a focus in the app in front: none there, nothing is pressed."""
        from karta.base import macos

        macos.check_abort()
        with self._acts():
            self._mark()
            self.refused = ""
            self.pressed = self._press(keys)
            self._stale()

    def _press(self, keys: str) -> str:
        mods, key = parse_keys(keys, self._m.MODIFIERS)
        combo = "+".join([*mods, key])
        if combo == ADDRESS_KEYS and self._env.app in self._m.BROWSERS and self._focus_address():
            return "the address field, focused by Accessibility"
        if combo == "escape":
            how = self._ax_cancel()
            if how:
                return how
        if "cmd" not in mods and key in AT_FOCUS_KEYS and not self._focus_in_front():
            self.refused = f"{key} not pressed: the keyboard focus is in nothing of the app in front"
            return ""
        how = self._shortcut(keys)
        if combo == ADDRESS_KEYS:
            self._pause(0.15)
            if not self._focus_editable():
                self.refused = f"{keys} left the keyboard focus in nothing editable: nothing more is typed"
        return how

    def _shortcut(self, keys: str) -> str:
        """A ⌘ shortcut as the app's own menu command (the one enabled command with that key equivalent, pressed by
        AX: no key event reaches the focused field); any other key, or no such command, as the key."""
        mods, key = parse_keys(keys, self._m.MODIFIERS)
        if "cmd" in mods:
            want = (frozenset(mods), key)
            hits = [(name, el) for name, el, eq in self._menu_items() if el is not None and self._combo(eq) == want]
            if len(hits) == 1 and self._ax_press(hits[0][1]):
                return f"the menu command {hits[0][0]!r}"
        self._keys(keys)
        return "key"

    def _combo(self, keys: str) -> tuple[frozenset, str] | None:
        try:
            mods, key = parse_keys(keys, self._m.MODIFIERS)
        except ValueError:
            return None
        return frozenset(mods), key

    def _focus_in_front(self) -> bool:
        """Something of the app in front holds the keyboard focus."""
        focused = self._ax_focused()
        return focused is not None and self._ax_pid(focused) in (None, self._front_pid())

    def _focus_address(self) -> bool:
        """The browser's address field (`address_field`, by structure) holds the keyboard focus (`_take_focus`: the AX
        focus, a click, `focus_holds`) with its text selected by AX, so what is typed next replaces it."""
        item = address_field(self.screen())
        if item is None or not self._take_focus(item):
            return False
        old = self._old_text(item)
        return not old or self._ax_select(item.ref.element, len(old))

    def _ax_cancel(self) -> str:
        """Close what the focus is in by Accessibility: the nearest menu, popover, sheet or dialog that offers AXCancel,
        else its cancel button (AXCancelButton). With no focused element, the front window's sheet or the dialog window
        itself. "" when there is none, or it changed nothing (then the key is pressed)."""
        chain, element = [], self._ax_focused()
        while element is not None and len(chain) < CANCEL_DEPTH:
            chain.append(element)
            element = self._ax_parent(element)
        if not chain:
            window = self._front_window()
            chain = [] if window is None else [window]
        for element in chain:
            attrs = {name: self._ax_attr(element, name) for name in ("AXRole", "AXSubrole", "AXModal")}
            role = _text(attrs["AXRole"])
            if role in CANCEL_ROLES or is_dialog(attrs):
                targets = [element]
            elif role == "AXWindow":
                targets = self._sheets(element)  # a sheet over the window the focus is not in
            else:
                continue
            for target in targets:
                how = self._cancel(target)
                if how:
                    return how
            if role == "AXWindow":
                break  # nothing above a window to close
        return ""

    def _sheets(self, window) -> list:
        return [c for c in self._ax_attr(window, "AXChildren") or [] if _text(self._ax_attr(c, "AXRole")) == "AXSheet"]

    def _cancel(self, element) -> str:
        """AXCancel on `element`, else a press of its cancel button; the way it went when the screen changed, else ""."""
        if "AXCancel" in self._ax_actions(element):
            way, target, action = "AXCancel", element, "AXCancel"
        else:
            target = self._ax_attr(element, "AXCancelButton")
            if target is None:
                return ""
            way, action = "its cancel button, by AXPress", "AXPress"
        before = self.screen().fingerprint()
        self._mark()
        if not self._ax_perform(target, action):
            return ""
        self._stale()
        return way if self.changed_since(before, timeout=CANCEL_WAIT) else ""

    def confirm_or_return(self) -> None:
        """Return for what sends nothing (a search box, the address bar): AXConfirm on the focused element when it
        offers it and the screen changed within CONFIRM_WAIT; else the key (an element that says yes to AXConfirm and
        does nothing still gets its return). Never for a step that may send: that is `press("return")`, one way."""
        from karta.base import macos

        macos.check_abort()
        with self._acts():
            self._mark()
            self.pressed = "AXConfirm" if self._ax_confirm() else self._press("return")
            self._stale()

    def click_once(self, item: Item) -> None:
        """A click that may send, pay or commit, done exactly one way, never a second after it: AXPress when the
        element takes it (a press it refuses may still have gone through: an app past the messaging timeout), and
        whatever the screen does next is the step's check, with the usual wait; a real click only for an item with no
        pressable element. Never the AXPress-then-click fallback `click` has for controls that ignore AXPress."""
        with self._acts():
            ref: Ref = item.ref
            self._mark()
            if ref.element is not None and (ref.pressable or item.menu or item.offscreen):
                self.pressed = "AXPress" if self._ax_press(ref.element) else "AXPress refused: not clicked after it"
            elif item.offscreen:
                self.pressed = "off screen with no element: not clicked"
                self.refused = f"{item.name!r} is off screen and cannot be pressed: not clicked"
            else:
                self._click_at(ref.centre)
                self.pressed = "a click"
            self._stale()

    def _ax_confirm(self) -> bool:
        """AXConfirm on the focused element when it offers it, and the screen changed after it (a field's value or what
        it sent): a native field's own action, with no key. False otherwise: then Return is pressed, so an element that
        says yes to AXConfirm and does nothing is still sent."""
        focused = self._ax_focused()
        if focused is None or "AXConfirm" not in self._ax_actions(focused):
            return False
        before = self.screen().fingerprint()
        self._mark()
        if not self._ax_perform(focused, "AXConfirm"):
            return False
        self._stale()
        return self.changed_since(before, timeout=CONFIRM_WAIT)

    def open_app(self, app: str, timeout: float = OPEN_TIMEOUT) -> None:
        """`open -a`, then wait for the app to be in front: a cold start takes seconds, and an app launched from a
        terminal may not take focus by itself, so it is activated again until it is (or the timeout passes)."""
        with self._acts():
            self._open_app(app, timeout)

    def _open_app(self, app: str, timeout: float) -> None:
        import subprocess

        from karta.core import fast

        self._mark()
        self._env.open(app)  # `open -a`, then MacEnv follows the new front app, its page and OCR cache
        deadline = time.perf_counter() + timeout
        while fast.frontmost_app_and_pid()[0] != app and time.perf_counter() < deadline:
            name = app.replace('"', "")
            subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'], capture_output=True, check=False)
            self._m._wait_front(app, timeout=0.5)
        self._stale(min_wait=0.3, max_wait=1.5)  # its window draws after it comes to the front
        self._refresh_front()

    def open_url(self, url: str, browser: str, profile: str | None = None) -> None:
        """The browser first, then the profile's window (its Profiles menu), then the address bar (MacEnv.open)."""
        with self._acts():
            self._open_url(url, browser, profile)

    def _open_url(self, url: str, browser: str, profile: str | None) -> None:
        if self._env.app != browser:
            self.open_app(browser)
        if profile and self.screen().profile != profile:
            entry = profile_menu_item(self.screen().items, profile, self._profiles(browser))
            if entry is not None and not _ticked(entry):  # a ticked profile is the one in front already
                self.click(entry)
                self._stale(min_wait=0.5, max_wait=2.0)  # a new window in that profile
                self._refresh_front()
        self._mark()
        if self._env.app in self._m.BROWSERS and "." in url and " " not in url:
            self._address_open(url if url.startswith("http") else f"https://{url}")
        else:
            self._env.open(url)  # no browser in front: `open`, the default browser
        self._last = None

    def _address_open(self, url: str) -> None:
        """A site in the browser in front, through the address bar of the window on screen (`open -a` let the browser
        pick the window, and a page once replaced a tab in another one), each step checked before the next: the
        address field holds the focus (press cmd+l: by AX first), the paste goes only to an editable focus, the field
        holds the address, then return (`confirm_or_return`: AXConfirm first). A step that fails stops it (`refused` says which)."""
        self.press(ADDRESS_KEYS)
        if not self.refused:
            self.type_at_focus(url)
        if not self.refused and not self._address_holds(url):
            self.refused = "the address field does not hold the address: return not pressed"
        if not self.refused:
            self.confirm_or_return()  # an address sends nothing: AXConfirm, then the key
        self._env.followed(min_wait=0.5)  # then the loop waits for a change, and the next step retries while it loads

    def _address_holds(self, url: str) -> bool:
        """The focused element holds the address's host (a browser may show it without the scheme); True when its
        value cannot be read (the focus itself was checked before the paste)."""
        focused = self._ax_focused()
        value = self._ax_value(focused) if focused is not None else None
        host = re.sub(r"^\w+://", "", url).split("/")[0].lower()
        return value is None or host in value.lower()

    def focus_tab(self, browser: str, window: int, tab: int) -> None:
        """An already-open tab to the front: its window raised, the tab made active (AppleScript, 1-based indexes)."""
        import subprocess

        name = browser.replace('"', "")
        if browser == "Safari":
            script = (
                f'tell application "{name}" to set current tab of window {int(window)} to tab {int(tab)} of window {int(window)}'
            )
        else:
            script = f'tell application "{name}" to set active tab index of window {int(window)} to {int(tab)}'
        with self._acts():
            self._mark()
            subprocess.run(["osascript", "-e", script, "-e", f'tell application "{name}" to set index of window {int(window)} to 1',
                            "-e", f'tell application "{name}" to activate'], capture_output=True, check=False)  # fmt: skip
            self._stale(min_wait=0.3, max_wait=1.5)
            self._refresh_front()

    def _refresh_front(self) -> None:
        from karta.core import fast

        self._env.app, self._env.pid = fast.frontmost_app_and_pid()
        self._env._find_page()
        self._last = None

    def scroll(self, direction: str) -> None:
        """The front window's scroll area moved by its vertical scroll bar's AXValue (no pointer moved, no event to the
        view under it), read back; else wheel events over the window's centre."""
        with self._acts():
            self._mark()
            self.scrolled = "scroll bar" if self._ax_scroll(direction) else "wheel"
            if self.scrolled == "wheel":
                self._wheel(-SCROLL_LINES if direction == "down" else SCROLL_LINES)
            self._stale()

    def _ax_scroll(self, direction: str) -> bool:
        """One step (SCROLL_PAGE of what shows) through the scroll bar: its AXValue (0 top, 1 bottom) moved by the
        visible height over the hidden height (the area's frame against its contents'), set and read back."""
        area = self._scroll_area()
        bar = self._ax_attr(area, "AXVerticalScrollBar") if area is not None else None
        value = self._ax_attr(bar, "AXValue") if bar is not None else None
        if not isinstance(value, int | float) or isinstance(value, bool):
            return False
        view = self._ax_frame(area)
        content = next((f for f in map(self._ax_frame, self._ax_attr(area, "AXContents") or []) if f), None)
        if not view or not content or content[3] <= view[3] + 1:
            return False
        step = SCROLL_PAGE * view[3] / (content[3] - view[3])
        new = min(1.0, max(0.0, value + (step if direction == "down" else -step)))
        if abs(new - value) < 1e-6 or not self._ax_set(bar, "AXValue", new):
            return False
        now = self._ax_attr(bar, "AXValue")
        return isinstance(now, int | float) and abs(now - value) > 1e-6

    def scroll_top(self) -> bool:
        """The front window's scroll area moved to its top (its vertical scroll bar's AXValue set to 0), when it is
        scrolled down: True when it moved. A list's first rows are its top's, never the viewport's."""
        area = self._scroll_area()
        bar = self._ax_attr(area, "AXVerticalScrollBar") if area is not None else None
        value = self._ax_attr(bar, "AXValue") if bar is not None else None
        if not isinstance(value, int | float) or isinstance(value, bool) or value <= 1e-6:
            return False
        with self._acts():
            self._mark()
            moved = self._ax_set(bar, "AXValue", 0.0)
            self._stale()
        return bool(moved)

    def _scroll_area(self):
        """The front window's largest scroll area (the page, the list, the document), within SCROLL_DEPTH levels."""
        window = self._front_window()
        best, size, level, seen = None, 0.0, [window] if window is not None else [], 0
        for _ in range(SCROLL_DEPTH):
            below = []
            for element in level:
                seen += 1
                if seen > SCROLL_NODES:
                    return best
                if _text(self._ax_attr(element, "AXRole")) == "AXScrollArea":
                    frame = self._ax_frame(element)
                    if frame and frame[2] * frame[3] > size:
                        best, size = element, frame[2] * frame[3]
                below.extend(self._ax_attr(element, "AXChildren") or [])
            level = below
        return best

    def back(self) -> None:
        self.press("cmd+[")  # the app's Back command by AX (History > Back, Go > Back), else the key

    def open_terminal(self, line: str) -> tuple[int, str]:
        """A NEW Terminal window running `line` (delegate.launch: `do script` with no `in`), checked: (its window id,
        its tty), else delegate.Refused."""
        from karta.harness.delegate import launch

        return launch(line)

    def wait(self, seconds: float) -> None:
        from karta.base import macos

        started = self._clock()
        self.refused = ""
        macos.sleep_watching(seconds)
        self._env._find_page()
        self._last = None
        self.wait_seconds += self._clock() - started


MOD_WORDS = {
    "command": "cmd",
    "cmd": "cmd",
    "⌘": "cmd",
    "control": "ctrl",
    "ctrl": "ctrl",
    "⌃": "ctrl",
    "option": "alt",
    "opt": "alt",
    "alt": "alt",
    "⌥": "alt",
    "shift": "shift",
    "⇧": "shift",
}
KEY_WORDS = {"enter": "return", "esc": "escape", "spacebar": "space", "del": "delete", "backspace": "delete"}


def parse_keys(keys: str, modifiers: Mapping[str, int]) -> tuple[list[str], str]:
    """ "cmd+space", "command space", "⌘ Space", "Cmd-Shift-N" -> (["cmd"], "space"): modifiers by name or symbol,
    joined by +, -, or spaces; the last word is the key."""
    words = [w for w in re.split(r"[\s+]+|(?<=\w)-(?=\w)", keys.strip().lower()) if w]
    if not words:
        raise ValueError("no key given")
    *mods, key = words
    mods = [MOD_WORDS.get(m, m) for m in mods]
    unknown = [m for m in mods if m not in modifiers]
    if unknown:
        raise ValueError(f"unknown modifier(s) {unknown} in {keys!r}")
    return mods, KEY_WORDS.get(key, key)


DIALOG_ROLES = {"AXSheet", "AXDialog", "AXSystemDialog"}
DIALOG_SUBROLES = {"AXDialog", "AXSystemDialog", "AXApplicationDialog", "AXApplicationAlertDialog"}  # a window or web group
BLOCKING_SUBROLES = {"AXDialog", "AXSystemDialog"}  # a dialog window: a web group's dialog roles block only with aria-modal
# what each ancestor of an item is asked, in one batched read: is it a dialog, a web page, the search landmark, a window
# or sheet with a default button, something still loading
ANCESTOR_ATTRS = ("AXRole", "AXSubrole", "AXModal", "AXCancelButton", "AXCloseButton", "AXDefaultButton", "AXElementBusy")
ANCESTRY_DEPTH = 200  # a loop guard: every ancestor is read once per screen read (shared through the cache)


def is_dialog(attrs: Mapping[str, object]) -> bool:
    """A sheet, dialog or modal: its AX role, a dialog subrole (a window's, or a web group's dialog and alertdialog
    roles), or aria-modal (AXModal). Never its role description: that is localized."""
    return (
        _text(attrs.get("AXRole")) in DIALOG_ROLES
        or _text(attrs.get("AXSubrole")) in DIALOG_SUBROLES
        or attrs.get("AXModal") is True
    )


def blocks(attrs: Mapping[str, object]) -> bool:
    """A dialog that blocks the page: a sheet, a dialog window (AXDialog, AXSystemDialog), or anything modal
    (AXModal: a web dialog or alertdialog with aria-modal). A web dialog role without aria-modal is a panel (a side
    cart, a drawer, a flyout): grouped as a dialog, never answered or cleared."""
    return (
        _text(attrs.get("AXRole")) in DIALOG_ROLES
        or _text(attrs.get("AXSubrole")) in BLOCKING_SUBROLES
        or attrs.get("AXModal") is True
    )


def browser_blocks(attrs: Mapping[str, object]) -> bool:
    """The browser's own dialog blocks the page only as a sheet, a system dialog or anything modal (AXModal). A bubble
    the browser floats over the page (a translate offer, a profile or settings popover: a dialog window that is none
    of those) leaves the page's own controls working: a panel, never in the way and never a permission prompt."""
    return (
        _text(attrs.get("AXRole")) == "AXSheet"
        or _text(attrs.get("AXSubrole")) == "AXSystemDialog"
        or attrs.get("AXModal") is True
    )


def _same(element: Any, other: Any) -> bool:
    try:
        return other is not None and element == other
    except Exception:
        return False


def address_field(scr: Screen) -> Item | None:
    """The browser's own address field, by structure: an enabled text field of its chrome (outside the page, in no
    bubble of its own) with an element to act on; of several, the widest (a tab search box is narrower)."""
    fields = [
        i
        for i in scr.items
        if i.kind == "field"
        and i.chrome
        and not i.browser_ui
        and i.enabled
        and isinstance(i.ref, Ref)
        and i.ref.element is not None
    ]
    return max(fields, key=lambda i: i.ref.w, default=None)


def focus_holds(
    target: Any,
    focused: Any,
    parent: Callable[[Any], Any],
    editable: Callable[[Any], bool],
    frame: tuple[float, float, float, float] | None = None,
    point: tuple[float, float] | None = None,
) -> bool:
    """Typing would land in the field: the system's focused element is the field itself, inside it (its editor, a
    descendant: FOCUS_DEPTH ancestors up), or an editable element the field sits directly in (a combo box around its
    text). A field read off the pixels (no element): an editable focused element whose frame holds the field's centre.
    False when nothing has the focus: never typed blind."""
    if focused is None:
        return False
    if target is None:
        if frame is None or point is None or not editable(focused):
            return False
        x, y, w, h = frame
        return x <= point[0] <= x + w and y <= point[1] <= y + h
    element = focused
    for _ in range(FOCUS_DEPTH):
        if element is None:
            break
        if _same(element, target):
            return True
        element = parent(element)
    return _same(parent(target), focused) and editable(focused)


@dataclass(frozen=True)
class Around:
    """What an element's ancestors (itself included) say about it, by structure."""

    dialog: Mapping | None = None  # the nearest dialog's attributes (with its "frame")
    web: bool = False  # inside a web page: an AXWebArea ancestor (a site's own popup, not the browser's)
    search: bool = False  # inside the page's search landmark (AXLandmarkSearch: role=search)
    default: Any = None  # the nearest window's or sheet's default button (AXDefaultButton)
    busy: bool = False  # an ancestor says it is still loading (AXElementBusy: aria-busy)
    # for the content model (content.py): each landmark around it, outermost first, with its frame; the nearest editor
    # (a popover, sheet, dialog or web form: its key and frame); the nearest toolbar or menu bar's frame
    landmarks: tuple = ()
    editor: tuple | None = None
    bar: tuple | None = None


def around(element: Any, parent: Callable[[Any], Any], attrs_of: Callable[[Any], Mapping], cache: dict) -> Around:
    """`Around` for `element`, folded from the outermost ancestor in; `cache` shares each ancestor's answer across the
    read, so every ancestor is read once."""
    from karta.base.ax_walk import node_identity

    chain, node, out = [], element, Around()
    for _ in range(ANCESTRY_DEPTH):
        if node is None:
            break
        key = node_identity(node)
        if key in cache:
            out = cache[key]
            break
        chain.append((key, attrs_of(node)))
        node = parent(node)
    for key, attrs in reversed(chain):
        role, subrole, frame = _text(attrs.get("AXRole")), _text(attrs.get("AXSubrole")), attrs.get("frame")
        editor = role in content.EDITOR_ROLES or subrole in content.EDITOR_SUBROLES or is_dialog(attrs)
        out = Around(
            dialog=attrs if is_dialog(attrs) else out.dialog,
            web=out.web or role == "AXWebArea",
            search=out.search or subrole == "AXLandmarkSearch",
            default=attrs.get("AXDefaultButton") or out.default,
            busy=out.busy or attrs.get("AXElementBusy") is True,
            landmarks=(*out.landmarks, (subrole, frame)) if subrole.startswith("AXLandmark") else out.landmarks,
            editor=(hash(key), frame) if editor else out.editor,
            bar=(frame,) if role in content.BAR_ROLES else out.bar,
        )
        cache[key] = out
    return out


def _inside(frame, point: tuple[float, float]) -> bool:
    return (
        bool(frame)
        and frame[2] > 0
        and frame[3] > 0
        and frame[0] <= point[0] <= frame[0] + frame[2]
        and (frame[1] <= point[1] <= frame[1] + frame[3])
    )


def place_text(item: Item, landmarks: set, editors: set, bars: set, browser: bool) -> Item:
    """Text read off the pixels takes the place of the AX containers its centre lies in (`mark_tree`): the landmarks
    (outermost, the largest, first), the smallest editor, a toolbar or menu bar; in a browser, the web page unless it
    is the browser's chrome."""
    ref = item.ref
    if not isinstance(ref, Ref) or ref.element is not None or item.menu or not ref.w:
        return item
    at = ref.centre
    marks = sorted(((s, f) for s, f in landmarks if _inside(f, at)), key=lambda sf: -(sf[1][2] * sf[1][3]))
    editor = min((e for e in editors if _inside(e[1], at)), key=lambda e: e[1][2] * e[1][3], default=None)
    placed = replace(
        ref,
        landmarks=tuple(s for s, _ in marks),
        editor=editor[0] if editor else None,
        bar=any(_inside(f, at) for f in bars),
        web=browser and not item.chrome,
    )
    return replace(item, ref=placed)


def mark_tree(
    items: list[Item], parent: Callable[[Any], Any], attrs_of: Callable[[Any], Mapping], browser: bool = False
) -> tuple[list[Item], bool]:
    """Each AX item marked by what its ancestors are (`around`), and whether anything on screen says it is still
    loading. dialog=True inside a sheet, dialog or modal, and for OCR text inside such a dialog's frame; a dialog that
    does not block (`blocks`) marks its items `panel` too; the dialog's own AXCancelButton or AXCloseButton is marked
    `cancels`; a window's or sheet's AXDefaultButton `default`; anything inside the search landmark `search`. In a
    browser, an item outside the web page is the browser's own: chrome, or browser_ui in a dialog (a profile,
    permission or settings popup), a panel when that dialog does not block the page (`browser_blocks`: a bubble).
    For the content model, each AX item's Ref gets the landmarks around it, its editor, whether it is in a toolbar or
    menu bar and whether it is in the web page; text read off the pixels gets them by the frames it lies in
    (`place_text`)."""
    cache: dict = {}
    frames = []
    out = []
    busy = False
    landmarks: set = set()  # the content model's containers seen, for the text read off the pixels (place_text)
    editors: set = set()
    bars: set = set()

    def framed(frame) -> tuple | None:
        return tuple(float(v) for v in frame) if frame and len(frame) == 4 else None

    for item in items:
        ref = item.ref
        if not (isinstance(ref, Ref) and ref.element is not None and not item.menu):
            out.append(item)
            continue
        here = around(ref.element, parent, attrs_of, cache)
        busy = busy or here.busy
        found = here.dialog
        own = browser and not here.web
        landmarks |= {(s, f) for s, f in ((s, framed(f)) for s, f in here.landmarks) if f}
        if here.editor and framed(here.editor[1]):
            editors.add((here.editor[0], framed(here.editor[1])))
        if here.bar and framed(here.bar[0]):
            bars.add(framed(here.bar[0]))
        ref = replace(
            ref,
            landmarks=tuple(s for s, _ in here.landmarks),
            editor=here.editor[0] if here.editor else None,
            bar=here.bar is not None,
            web=here.web,
        )
        item = replace(item, ref=ref)
        marks = {"search": item.search or here.search, "default": _same(ref.element, here.default)}
        if found is not None:
            panel = not browser_blocks(found) if own else not blocks(found)
            if found.get("frame"):
                frames.append((found["frame"], own, panel))
            cancels = item.cancels or any(_same(ref.element, found.get(k)) for k in ("AXCancelButton", "AXCloseButton"))
            marks |= {"dialog": True, "browser_ui": own, "panel": panel, "cancels": cancels}
        elif own:
            marks["chrome"] = True  # the browser's toolbar, tab strip, address bar, tab search
        out.append(replace(item, **marks))
    out = [place_text(i, landmarks, editors, bars, browser) for i in out]
    if not frames:
        return out, busy

    def frame_of(ref: Ref):
        cx, cy = ref.centre
        return next(
            (hit for hit in frames if hit[0][0] <= cx <= hit[0][0] + hit[0][2] and hit[0][1] <= cy <= hit[0][1] + hit[0][3]), None
        )

    marked = []
    for i in out:
        hit = frame_of(i.ref) if not i.dialog and i.source == "ocr" and isinstance(i.ref, Ref) else None
        marked.append(replace(i, dialog=True, browser_ui=hit[1], panel=hit[2]) if hit else i)
    return marked, busy


PAGE_ATTRS = ("AXURL", "AXLoaded", "AXElementBusy", "AXLanguage")
PROMPT_BOUNDS = 100_000.0  # a system prompt's walk is not clipped to a page: its window is the whole of it


def key_equivalent(char: object, modifiers: object) -> str:
    """A menu item's key equivalent as the harness writes keys: "cmd+shift+f". AXMenuItemCmdModifiers is a bit set:
    1 shift, 2 option, 4 control, 8 no command key. "" when it has none."""
    key = str(char or "").strip().lower()
    if not key:
        return ""
    bits = int(modifiers) if isinstance(modifiers, int | float) else 0
    mods = ([] if bits & 8 else ["cmd"]) + ["shift"] * bool(bits & 1) + ["alt"] * bool(bits & 2) + ["ctrl"] * bool(bits & 4)
    return "+".join([*mods, key])


def language_tag(value: object) -> str:
    """A language as its primary subtag, lower case ("de-DE", "de_CH" -> "de"); "" for none or "Base"."""
    text = _text(value).replace("_", "-").split("-")[0].lower()
    return "" if text in ("", "base", "und") else text


def app_language(pid: int) -> str:
    """The app's interface language: its bundle's preferred localization (what macOS picked from the user's languages
    and the app's own), else the user's first preferred language; "" when neither can be read."""
    try:
        from AppKit import NSRunningApplication
        from Foundation import NSBundle, NSLocale

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        bundle = NSBundle.bundleWithURL_(app.bundleURL()) if app is not None and app.bundleURL() is not None else None
        own = list(bundle.preferredLocalizations() or []) if bundle is not None else []
        return language_tag(own[0] if own else None) or language_tag((list(NSLocale.preferredLanguages()) or [None])[0])
    except Exception:
        return ""


def page_busy(attrs: Mapping[str, object]) -> bool:
    """A web area still loading by its own attributes: AXElementBusy set, or AXLoaded false (absent says nothing)."""
    return attrs.get("AXElementBusy") is True or attrs.get("AXLoaded") is False


def mark_chrome(item: Item, page_top: float) -> Item:
    """Text read off the pixels above the page's top edge is browser chrome (tab strip, address bar, toolbar). An AX
    item is placed by structure instead (`mark_tree`: outside the web page)."""
    ref = item.ref
    if isinstance(ref, Ref) and ref.element is None and not item.menu and ref.w and ref.centre[1] < page_top:
        return replace(item, chrome=True)
    return item


def profile_menu_item(items: list[Item], profile: str, profiles: list[str]) -> Item | None:
    """The menu command that opens `profile`'s window: in the menu that lists most of the browser's known profile
    names (`profiles`, from its Local State), the entry named exactly so, else the one naming it ("Sam (Work)").
    Found by the names, never by the menu's title, which is localized."""
    menus: dict[str, list[Item]] = {}
    for i in items:
        if i.menu and " > " in i.name:
            menus.setdefault(i.name.rsplit(" > ", 1)[0], []).append(i)
    known = {p.lower() for p in profiles}

    def listed(entries: list[Item]) -> int:
        return sum(any(k in e.name.rsplit(" > ", 1)[1].lower() for k in known) for e in entries)

    best = max(menus.values(), key=listed, default=[])
    if not best or listed(best) < min(2, len(known)):
        return None
    leaf = {i: i.name.rsplit(" > ", 1)[1] for i in best}
    exact = [i for i in best if leaf[i] == profile]
    loose = [i for i in best if profile.lower() in leaf[i].lower()]
    return (exact or loose or [None])[0]


def _ticked(entry: Item) -> bool:
    from karta.base.macos import _ax_attr

    return bool(_text(_ax_attr(entry.ref.element, "AXMenuItemMarkChar"))) if isinstance(entry.ref, Ref) else False


def drop_field_echoes(merged: list, nodes: list, value_of: Callable[[Any], str], scale: float) -> list:
    """OCR text that is only the value of the AX field it sits in (our typing in a search box) goes: the field carries it.

    `merged` is perception's merge_with_origins output (capture pixels), `nodes` the AxNodes (points).
    """
    from karta.base.perception import texts_match

    boxes = [
        (n.x * scale, n.y * scale, (n.x + n.w) * scale, (n.y + n.h) * scale, v)
        for n in nodes
        if n.role in FIELD_ROLES and (v := value_of(n))
    ]
    if not boxes:
        return merged
    kept = []
    for up, origin in merged:
        cx, cy = up.center
        if origin is None and any(x1 <= cx <= x2 and y1 <= cy <= y2 and texts_match(up.text, v) for x1, y1, x2, y2, v in boxes):
            continue
        kept.append((up, origin))
    return kept
