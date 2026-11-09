"""The fast paths under Karta's screen reading. Measured on an M4 (Brave, 2940x1912 capture):

    screenshot       screencapture CLI + PNG round trip 154 ms  ->  in-process CGDisplayCreateImage 33 ms
    front app        AppleScript 127 ms                          ->  NSWorkspace ~0 ms
    browser URL      AppleScript 93 ms on every read             ->  skipped (nothing in the loop reads it)
    settle           fixed 600 ms after every action             ->  0.1 s beat, then poll for a change (not
                                                                     stillness: video never stops), and retry
                                                                     a step whose target is not there yet
    typing           one key event per character (1.5 to 3 s)    ->  paste

OCR itself stays on "accurate" at full resolution: "fast" kept 31 of 68 lines on a video site's page and
half resolution 54 of 68, so speed comes from the perception cache re-reading only changed tiles instead.
"""

from __future__ import annotations

import time

import numpy as np
import Quartz
from AppKit import NSPasteboard, NSPasteboardTypeString, NSWorkspace
from Foundation import NSDate, NSRunLoop
from PIL import Image

from karta.base.platform_adapter import current

STABLE_DIFF = 1.5  # mean 8-bit difference on the thumbnail below which two frames count as the same
POLL = 0.05  # seconds between settle checks
MIN_SETTLE = 0.08  # give the app a moment to start reacting before judging it stable
MAX_SETTLE = 2.0  # a page still animating after this is as settled as it gets


def screenshot() -> Image.Image:
    image = Quartz.CGDisplayCreateImage(Quartz.CGMainDisplayID())
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
    return Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", Quartz.CGImageGetBytesPerRow(image), 1).convert("RGB")


def frontmost_app_and_pid() -> tuple[str, int]:
    # NSWorkspace only learns that the front app changed while the process's run loop runs; a long-lived
    # command-line process never runs it, so without this it keeps reporting the app that was in front at start.
    NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()), int(app.processIdentifier())


class FastDesktop:
    """karta.base's desktop adapter with the slow calls replaced; everything else is forwarded.

    `page` narrows the window to a browser's web page (its AXWebArea): OCR then skips the tab strip,
    address bar and toolbar, which web tasks never act on.
    """

    def __init__(self, base=None):
        self.base = base or current()
        self.page: tuple[float, float, float, float] | None = None

    def __getattr__(self, name):
        return getattr(self.base, name)

    def screenshot(self) -> Image.Image:
        return screenshot()

    def frontmost_app_and_pid(self) -> tuple[str, int]:
        return frontmost_app_and_pid()

    def browser_url(self, browser: str) -> str | None:
        return None

    def frontmost_window_bounds(self, pid: int | None = None) -> tuple[float, float, float, float] | None:
        return self.page or self.base.frontmost_window_bounds(pid)


def ocr_region(screen) -> tuple[float, float, float, float]:
    """Perception reads the window joined with a strip from the top of the display (menu bar, and on a
    browser the whole toolbar). Karta takes menus from Accessibility, so only the window (or page) is read."""
    width, height = float(screen.image.width), float(screen.image.height)
    if screen.window is None:
        return (0.0, 0.0, width, height)
    x, y, w, h = screen.window
    scale, margin = screen.scale, 4.0
    box = (
        max(0.0, (x - margin) * scale),
        max(0.0, (y - margin) * scale),
        min(width, (x + w + margin) * scale),
        min(height, (y + h + margin) * scale),
    )
    return box if box[2] > box[0] and box[3] > box[1] else (0.0, 0.0, width, height)


def _thumb(rect: tuple[float, float, float, float] | None) -> np.ndarray:
    region = Quartz.CGRectMake(*rect) if rect else Quartz.CGRectInfinite
    image = Quartz.CGWindowListCreateImage(
        region, Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID, Quartz.kCGWindowImageNominalResolution
    )
    if image is None:
        return np.zeros((1, 1))
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
    pil = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", Quartz.CGImageGetBytesPerRow(image), 1)
    return np.asarray(pil.convert("L").resize((max(1, width // 8), max(1, height // 8))), dtype=np.float32)


def settle(rect: tuple[float, float, float, float] | None, min_wait: float = MIN_SETTLE, max_wait: float = MAX_SETTLE) -> float:
    """Wait until the region stops changing; seconds waited. Replaces a fixed sleep after actions."""
    started = time.perf_counter()
    time.sleep(min_wait)
    previous = _thumb(rect)
    while time.perf_counter() - started < max_wait:
        time.sleep(POLL)
        frame = _thumb(rect)
        if frame.shape == previous.shape and float(np.abs(frame - previous).mean()) < STABLE_DIFF:
            break
        previous = frame
    return time.perf_counter() - started


PASTE_MIN = 0.1  # the app reads the clipboard as it handles the paste: never put the user's back sooner
PASTE_WAIT = 0.5  # at most this long for the paste to show in the field before the user's clipboard goes back
PASTE_POLL = 0.03
# the clipboard managers' own markers: they neither keep nor show what Karta pasted (an address, a message)
QUIET_TYPES = ("org.nspasteboard.TransientType", "org.nspasteboard.ConcealedType")


def pasteboard_item(contents: dict):
    """An NSPasteboardItem holding each type's data (bytes-like) or string."""
    from AppKit import NSPasteboardItem

    item = NSPasteboardItem.alloc().init()
    for kind, value in contents.items():
        if isinstance(value, str):
            item.setString_forType_(value, kind)
        elif value is not None:
            item.setData_forType_(value, kind)
    return item


def saved_items(board) -> list[dict]:
    """Everything the clipboard holds: each item, each type with its data (text, rich text, an image, a file)."""
    return [{str(kind): item.dataForType_(kind) for kind in item.types() or []} for item in board.pasteboardItems() or []]


def paste(text: str, press_paste, landed=None) -> None:
    """Put text on the clipboard (marked transient and concealed, QUIET_TYPES), paste it, and put back what the user had there:
    every item in every type, not only its text. It goes back once the paste landed (`landed()`: the field's value
    changed, so the app has read the clipboard) or PASTE_WAIT passed, never sooner than PASTE_MIN (too soon and the
    app would paste the user's clipboard instead), also when the paste raised, and never over something written to
    the clipboard meanwhile (the user copied something)."""
    board = NSPasteboard.generalPasteboard()
    saved = saved_items(board)
    board.clearContents()
    board.writeObjects_([pasteboard_item({NSPasteboardTypeString: text, **dict.fromkeys(QUIET_TYPES, "")})])
    ours = board.changeCount()
    try:
        press_paste()
        started = time.perf_counter()
        time.sleep(PASTE_MIN)
        while landed is not None and not landed() and time.perf_counter() - started < PASTE_WAIT:
            time.sleep(PASTE_POLL)
    finally:
        if board.changeCount() == ours:
            board.clearContents()
            if saved:
                board.writeObjects_([pasteboard_item(contents) for contents in saved])
