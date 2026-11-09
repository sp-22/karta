"""The contract every harness v2 module builds on. Change it only with every module's owner in mind.

An Item is one thing on screen, described in plain words for Laya (never raw AX role names); a Screen is
what the harness reads at one moment; a HarnessEnv is anything that shows a Screen and can be acted on:
the real Mac (karta/harness/mac.py) or the role-played one (karta/harness/sim.py).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

KINDS = ("button", "field", "link", "row", "text", "heading", "menu", "checkbox", "tab", "image", "other")
REGIONS = ("top-left", "top", "top-right", "left", "middle", "right", "bottom-left", "bottom", "bottom-right")


@dataclass(frozen=True)
class Item:
    name: str  # what it is called, without its current text: "Search or start a new chat"
    kind: str = "other"  # one of KINDS: a plain word, not an AX role
    value: str = ""  # the text in a field, a checkbox's state word, a menu path's leaf, ...
    region: str = ""  # one of REGIONS, "" when unknown
    row: tuple[str, ...] = ()  # names of items sharing its row, set only when another item has the same name
    focused: bool = False
    enabled: bool = True
    checked: bool | None = None
    source: str = "ax"  # "ax", "ocr" or "ax+ocr"
    offscreen: bool = False  # labelled and pressable, but not visible now
    menu: bool = False  # a menu-bar command ("File > New Note")
    chrome: bool = False  # browser or window chrome: tab strip, address bar, toolbar
    dialog: bool = False  # inside a dialog, sheet, popup or modal (AXSheet, AXDialog, a web dialog role)
    panel: bool = False  # ... whose dialog does not block the page (a web dialog without aria-modal: a side cart, a drawer)
    cancels: bool = False  # its dialog's own way out by structure: the dialog's AXCancelButton, a close button (AXCloseButton)
    secret: bool = False  # a password field (AXSecureTextField)
    search: bool = False  # a search field (AXSearchField: native, or a web type=search / role=searchbox), or anything
    # inside the page's search landmark (role=search)
    default: bool = False  # its window's or sheet's default button (AXDefaultButton: the one Return presses)
    shortcut: str = ""  # a menu command's key equivalent ("cmd+f"), from AXMenuItemCmdChar and its modifiers
    browser_ui: bool = False  # a dialog of the browser itself (a profile, permission or settings popup), not the page's
    url: str = ""  # a link's own web address (AXURL), "" when it has none or it was read by OCR
    multiline: bool = False  # a text area (a message body, a note): a line break adds a line instead of sending
    hint: bool = False  # named only by its tooltip, developer id or role description: an icon, no label it shows
    # "interface" (controls, navigation, banners, form labels) or "content" (what the page or app shows): set once per
    # read by karta.harness.content.classify, "" until then; every reader of content reads it (content.items)
    role_class: str = field(default="", compare=False, hash=False)
    ref: Any = field(default=None, compare=False, hash=False, repr=False)  # the env's handle for acting; never shown to Laya

    def describe(self) -> str:
        """One short line for Laya: "'ADD' button (middle-right; row: Lay's Magic Masala 115g, ₹50)"."""
        facts = [self.kind] if self.kind != "other" else []
        if self.value:
            facts.append(f"has {self.value!r}" if self.kind == "field" else self.value)
        if self.focused:
            facts.append("focused")
        if not self.enabled:
            facts.append("disabled")
        if self.checked is not None:
            facts.append("checked" if self.checked else "unchecked")
        if self.offscreen:
            facts.append("not visible")
        if self.dialog:
            facts.append("in a popup")
        where = [self.region] if self.region else []
        if self.row:
            where.append("row: " + ", ".join(self.row[:3]))
        text = repr(self.name) + (" " + " ".join(facts[:1]) if facts else "")
        extra = facts[1:] + where
        return text + (f" ({'; '.join(extra)})" if extra else "")


@dataclass
class Screen:
    app: str
    items: list[Item]
    url: str | None = None
    window: str = ""  # the front window's title
    profile: str | None = None  # the browser profile in front, when known
    busy: bool = False  # it says it is still loading: the web area's AXLoaded false, or AXElementBusy (aria-busy) on it
    # or around anything on screen
    lang: str = ""  # the language of what it shows ("de"): the page's AXLanguage, else the app's localization; "" unknown
    windows: int = 0  # the app's standard windows open (AXWindows), 0 when unknown
    window_at: str = ""  # where the focused window is drawn ("x,y,w,h" in points): another window took the front when it
    # changes; "" when unknown
    withheld: bool = False  # a mail or messaging screen whose text may not go to the brain (no yes): every prompt shows
    # its structure only (set by Toolbox.for_brain, rendered by view.Structure)

    def page(self) -> list[Item]:
        """What the app or site shows: no menu commands, no browser chrome."""
        return [i for i in self.items if not i.menu and not i.chrome and not i.browser_ui]

    def fields(self) -> list[Item]:
        return [i for i in self.page() if i.kind == "field" and i.enabled]

    def fingerprint(self) -> str:
        key = [self.app, self.url, self.window, [(i.name, i.kind, i.value, i.enabled, i.checked, i.focused) for i in self.items]]
        if self.windows or self.window_at:  # a new window under the same title, showing the same, is still a change
            key.append([self.windows, self.window_at])
        return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()


class HarnessEnv(Protocol):
    """What the harness needs from a Mac, real or simulated. Every action replaces, never appends."""

    def screen(self) -> Screen: ...

    def click(self, item: Item) -> None: ...

    def set_text(self, item: Item, text: str) -> None:
        """Replace the field's text with `text` (select all, then type)."""
        ...

    def type_at_focus(self, text: str) -> None:
        """Type where the keyboard focus is (a terminal prompt, a note body)."""
        ...

    def press(self, keys: str) -> None:
        """ "return", "escape", "cmd+k", "down" ..."""
        ...

    def open_app(self, app: str) -> None: ...

    def open_url(self, url: str, browser: str, profile: str | None = None) -> None:
        """Open `url` in `browser`, in `profile`'s window when given (the harness knows the profile names)."""
        ...

    def focus_tab(self, browser: str, window: int, tab: int) -> None:
        """Bring an already-open tab to the front: `window` and `tab` are the browser's own 1-based indexes."""
        ...

    def scroll(self, direction: str) -> None:
        """ "down" or "up"."""
        ...

    def back(self) -> None: ...

    def wait(self, seconds: float) -> None: ...


@dataclass
class MacFacts:
    """What code knows about this Mac without asking any model."""

    installed_apps: list[str]
    browsers: dict[str, list[str]]  # browser app -> its profile names ("Brave Browser" -> ["Work", "Personal"])
    folders: list[str]  # notable folders, home-relative: "~/Desktop/Projects/karta"
    now: datetime

    def summary(self) -> str:
        """For the brain: a few short lines."""
        return (
            f"Now: {self.now:%A %d %B %Y, %H:%M}\nInstalled apps: {', '.join(self.installed_apps)}\n"
            + "\n".join(f"{b} profiles: {', '.join(p)}" for b, p in self.browsers.items())
            + f"\nFolders: {', '.join(self.folders)}"
        )


class Outcome(StrEnum):
    """Why a tool call ended the way it did: honest reasons, given to the brain word for word."""

    OK = "ok"
    NOT_INSTALLED = "not installed"
    WRONG_PLACE = "not the place asked for"
    LOGIN_PAGE = "a login page: not signed in here"
    BLOCKED_BY_DIALOG = "a dialog or popup is in the way"
    NEEDS_PERMISSION = "asks for a permission"
    NO_RESULTS = "no results"
    NOT_FOUND = "nothing on screen matches"
    NO_FIELD = "no field to type into"
    DISABLED = "the control is disabled: something must be chosen or filled first"
    NOT_SET_UP = "open, but not set up or empty here (no account, nothing listed)"
    NO_CHANGE = "done, but the screen did not change"
    UNEXPECTED = "done, but the screen does not show what was expected"
    UNSURE = "not sure enough to act"
    REFUSED = "the user said no"
    STUCK = "stuck: repeating without progress"
    NO_DATA = "nothing was collected under that name"


@dataclass
class ToolResult:
    outcome: Outcome
    detail: str = ""  # one line: which place, which item, what was on screen

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.OK


@dataclass
class Choice:
    """One Laya answer, kept whole: the harness takes the next-best option without asking again."""

    decision: str  # "D1 place", "D2 page state", ...
    ranked: list[tuple[str, float]]  # (option, probability), best first
    gate: float  # the confidence below which the answer is "unsure"
    ms: float = 0.0

    @property
    def best(self) -> str:
        return self.ranked[0][0]

    @property
    def sure(self) -> bool:
        return bool(self.ranked) and self.ranked[0][1] >= self.gate
