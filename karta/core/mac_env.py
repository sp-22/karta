"""The real Mac under karta.harness.mac.MacHarnessEnv, which reads and acts itself: the front app and its page frame,
the OCR cache, menu commands with their key equivalents, the HUD's frame, the keymap, a settle beat and open()."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import ApplicationServices as AS
import Quartz

from karta.base import macos
from karta.base import perception as _perception
from karta.base.perception import OcrCache
from karta.core import fast

_perception.ocr_region = fast.ocr_region  # Karta processes only: read the window or page, not a top strip

AX_ENOUGH = 20  # a native app listing this many labelled controls is read without OCR (a miss re-reads with it)
BROWSERS = {"Google Chrome", "Brave Browser", "Microsoft Edge", "Arc", "Safari", "Firefox"}
# macOS virtual key codes (US layout).
KEYS = {
    **{c: k for c, k in zip("asdfhgzxcv", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), strict=True)},
    **{c: k for c, k in zip("bqweryt", (11, 12, 13, 14, 15, 16, 17), strict=True)},
    **{c: k for c, k in zip("oui", (31, 32, 34), strict=True)},
    "p": 35,
    "l": 37,
    "j": 38,
    "k": 40,
    "n": 45,
    "m": 46,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    "0": 29,
    "return": 36,
    "enter": 36,
    "tab": 48,
    "space": 49,
    "delete": 51,
    "backspace": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
    ",": 43,
    ".": 47,
    "/": 44,
}
MODIFIERS = {
    "cmd": Quartz.kCGEventFlagMaskCommand,
    "command": Quartz.kCGEventFlagMaskCommand,
    "shift": Quartz.kCGEventFlagMaskShift,
    "option": Quartz.kCGEventFlagMaskAlternate,
    "alt": Quartz.kCGEventFlagMaskAlternate,
    "ctrl": Quartz.kCGEventFlagMaskControl,
    "control": Quartz.kCGEventFlagMaskControl,
}


def _attr(element, name: str):
    err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    return value if err == 0 else None


def menu_items(pid: int, enabled: bool = True, shortcuts: bool = False) -> list[tuple]:
    """Enabled menu bar items as ("File > New Note", element), with their key equivalent ("cmd+n", "" for none) when
    `shortcuts`. Pressing one needs no open menu, and the step loop cannot see an open menu anyway. Disabled items are
    left out on purpose: they tell the planner what the app cannot do right now (e.g. no window, so no New Note). Each
    item is one batched read."""
    from karta.base.macos import ax_batch
    from karta.harness.mac import key_equivalent

    out = []
    bar = _attr(AS.AXUIElementCreateApplication(pid), "AXMenuBar")
    for top in (_attr(bar, "AXChildren") or [])[1:]:  # [0] is the Apple menu
        title = _attr(top, "AXTitle")
        for menu in _attr(top, "AXChildren") or []:
            for item in _attr(menu, "AXChildren") or []:
                got = ax_batch(item, MENU_ATTRS)
                name = got.get("AXTitle")
                if name and bool(got.get("AXEnabled")) == enabled:
                    label = f"{title} > {name}"
                    keys = key_equivalent(got.get("AXMenuItemCmdChar"), got.get("AXMenuItemCmdModifiers"))
                    out.append((label, item, keys) if shortcuts else (label, item))
    return out


MENU_ATTRS = ("AXTitle", "AXEnabled", "AXMenuItemCmdChar", "AXMenuItemCmdModifiers")


HUD_FRAME = Path.home() / "Library" / "Application Support" / "karta" / "hud-frame.json"


def _hud_frame() -> tuple[float, float, float, float] | None:
    """Where karta-hud is on screen (top-left origin, points), when it is running."""
    try:
        f = json.loads(HUD_FRAME.read_text())
        return f["x"], f["y"], f["w"], f["h"]
    except (OSError, ValueError, KeyError):
        return None


def _wait_front(app: str, timeout: float = 1.5) -> None:
    deadline = time.perf_counter() + timeout
    while fast.frontmost_app_and_pid()[0] != app and time.perf_counter() < deadline:
        time.sleep(0.05)


def _web_area(element, depth: int = 0):
    """The first AXWebArea under an element: a browser's page."""
    if depth > 25:
        return None
    if _attr(element, "AXRole") == "AXWebArea":
        return element
    for child in _attr(element, "AXChildren") or []:
        found = _web_area(child, depth + 1)
        if found is not None:
            return found
    return None


class _Focused:
    """The focused element, shaped like an AxNode so click/type treat it the same way."""

    def __init__(self, ref):
        self.ref, self.pressable = ref, False
        pos, size = _attr(ref, "AXPosition"), _attr(ref, "AXSize")
        p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)[1] if pos else None
        z = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)[1] if size else None
        self.x, self.y = (p.x, p.y) if p else (0.0, 0.0)
        self.w, self.h = (z.width, z.height) if z else (0.0, 0.0)


class MacEnv:
    def __init__(self, app: str | None = None):
        self._ocr_cache = OcrCache()  # only screen tiles that changed are read again
        self._desktop = fast.FastDesktop()  # in-process screenshot, no AppleScript per read
        self.settled: list[float] = []  # seconds waited after each action, for timing reports
        if app:
            macos.activate(app)
            _wait_front(app)  # a fullscreen app lives in its own Space: switching there takes a moment
        self.app, self.pid = fast.frontmost_app_and_pid()
        # Chromium browsers expose web page content to Accessibility only when asked.
        AS.AXUIElementSetAttributeValue(AS.AXUIElementCreateApplication(self.pid), "AXManualAccessibility", True)
        if app and not _attr(AS.AXUIElementCreateApplication(self.pid), "AXWindows"):
            # No window (Notes after its last one closed): reopen like a Dock click, so the app's
            # window commands (New Note and friends) are enabled before planning starts.
            subprocess.run(["open", "-a", app], check=False)
            self._settle(min_wait=0.5, max_wait=3.0)
        self._find_page()

    def _find_page(self) -> None:
        """A browser's web page rectangle, looked up once per page (37 ms), so OCR skips the toolbar."""
        self._desktop.page = None
        if self.app not in BROWSERS:
            return
        window = _attr(AS.AXUIElementCreateApplication(self.pid), "AXFocusedWindow")
        area = _web_area(window) if window is not None else None
        frame = macos._ax_frame(area) if area is not None else None
        if frame and frame[2] > 100 and frame[3] > 100:
            self._desktop.page = frame

    # ---------------------------------------------------------------- acting

    def _settle(self, min_wait: float = 0.1, max_wait: float = 0.0) -> None:
        """A beat for the app to take the input. No waiting for the screen
        to go still (video and animations never do): the loop checks for a change and retries instead."""
        started = time.perf_counter()
        time.sleep(min_wait)
        if max_wait > min_wait:  # opening an app or a page: wait for it to show something first
            fast.settle(macos.frontmost_window_bounds(self.pid), 0.0, max_wait - min_wait)
        self.settled.append(time.perf_counter() - started)

    def open(self, target: str) -> None:
        """An app (`open -a`), or a site with no browser in front (`open`: the default browser). A site in the browser
        in front goes through its address bar, by MacHarnessEnv (each step checked)."""
        if "." in target and " " not in target:
            subprocess.run(["open", target if target.startswith("http") else f"https://{target}"], check=False)
        else:
            subprocess.run(["open", "-a", target], check=False)
        self.followed(min_wait=0.5)

    def followed(self, min_wait: float = 0.5) -> None:
        """After opening something: a beat, then the new front app, its page and a fresh OCR cache."""
        self._settle(min_wait=min_wait)  # then the loop waits for a change, and the next step retries while it loads
        self.app, self.pid = fast.frontmost_app_and_pid()
        self._ocr_cache = OcrCache()
        self._find_page()
