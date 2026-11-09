"""macOS adapter: synthetic input, app control, screen capture, and the focused accessibility element.

This is the only module that touches Quartz, ApplicationServices, AppleScript, or Vision OCR.
Everything else reaches it through platform_adapter.desktop. The bounded tree walk itself lives in
ax_walk.py.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

import ApplicationServices as AS
import Quartz
from ocrmac import ocrmac
from PIL import Image

from .ax_walk import AX_PRESS, AxAttrs, Frame, app_children, walk_actionable
from .config import ABORT_CORNER_PX
from .models import Abort, AxNode, Field

KEYCODES = {"return": 36, "tab": 48, "escape": 53, "a": 0, "delete": 51, "[": 33}
MIN_WINDOW_SIDE_PT = 50.0  # anything smaller is a palette or a shadow, not the window being worked in

# ------------------------------------------------------------------ escape hatch


def mouse_location() -> tuple[float, float]:
    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    return loc.x, loc.y


def check_abort() -> None:
    x, y = mouse_location()
    if x <= ABORT_CORNER_PX and y <= ABORT_CORNER_PX:
        raise Abort("mouse in top-left corner")


def sleep_watching(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check_abort()
        time.sleep(0.1)


def accessibility_trusted() -> bool:
    return bool(AS.AXIsProcessTrusted())


# ------------------------------------------------------------------ input


def _post(event) -> None:
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.04)


def _flagged(event, flags=0):
    """Every posted event carries its modifier flags explicitly, zero when none: an event created with no flags
    takes the session's last ones, so a click or a typed letter right after a chord (cmd+a, a paste's cmd+v) acted
    as if Command were still held (a link opened in a background tab, a "t" opened a new tab)."""
    Quartz.CGEventSetFlags(event, flags)
    return event


def _mouse(kind, point):
    return _flagged(Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft))


def _down_then_up(event: Callable[[bool], object]) -> None:
    """Post the down event, then the up event even when the down is interrupted, so nothing stays held."""
    try:
        _post(event(True))
    finally:
        _post(event(False))


def click_at(point: tuple[float, float]) -> None:
    # Check before moving: the synthetic move would otherwise take the pointer out of the abort corner.
    check_abort()
    _post(_mouse(Quartz.kCGEventMouseMoved, point))
    check_abort()
    kinds = {True: Quartz.kCGEventLeftMouseDown, False: Quartz.kCGEventLeftMouseUp}
    _down_then_up(lambda down: _mouse(kinds[down], point))


def press(key: str, command: bool = False) -> None:
    check_abort()
    code = KEYCODES[key]

    def event(down: bool):
        return _flagged(Quartz.CGEventCreateKeyboardEvent(None, code, down), Quartz.kCGEventFlagMaskCommand if command else 0)

    _down_then_up(event)


def _unicode_key(ch: str, down: bool):
    event = _flagged(Quartz.CGEventCreateKeyboardEvent(None, 0, down))
    Quartz.CGEventKeyboardSetUnicodeString(event, len(ch), ch)
    return event


def type_text(text: str) -> None:
    """One character at a time, checking the abort corner before each."""
    for ch in text:
        check_abort()
        _down_then_up(partial(_unicode_key, ch))


def clear_field() -> bool:
    """Empty the focused text element through Accessibility, never a select all (in a combo box cmd+a moves the focus
    to its suggestion list): its AXValue set to "" and read back, else its whole text selected (AXSelectedTextRange,
    read back) and one delete pressed while that element still holds the focus. False when neither could be done."""
    element = ax_focused_element()
    if element is None:
        return False
    old = ax_value(element)
    if not old:
        return old == ""
    if ax_set_value(element, "") and not ax_value(element):
        return True
    if not (ax_select_range(element, 0, len(old)) and ax_selected_range(element) == (0, len(old))):
        return False
    if ax_focused_element() != element:
        return False
    press("delete")
    return not ax_value(element)


def scroll(lines: int) -> None:
    """Scroll events go to the view under the cursor, so park it over the frontmost window first."""
    center = frontmost_window_center()
    check_abort()
    if center is not None:
        _post(_mouse(Quartz.kCGEventMouseMoved, center))
    _post(_flagged(Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)))


# ------------------------------------------------------------------ apps and windows


def osascript(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, check=True).stdout.strip()


def frontmost_app() -> str:
    return osascript('tell application "System Events" to get name of first application process whose frontmost is true')


def frontmost_app_and_pid() -> tuple[str, int]:
    """Name and pid of the frontmost process in one AppleScript round trip."""
    name, _, pid = osascript(
        'tell application "System Events" to tell (first application process whose frontmost is true) to get {name, unix id}'
    ).rpartition(", ")
    return name, int(pid)


def frontmost_pid() -> int:
    return int(osascript('tell application "System Events" to get unix id of first application process whose frontmost is true'))


def activate(app: str, timeout: float = 3.0) -> bool:
    """Bring an app to the front and confirm it got there."""
    check_abort()
    osascript(f'tell application "{app}" to activate')
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        check_abort()
        if frontmost_app() == app:
            return True
        time.sleep(0.1)
    check_abort()
    osascript(f'tell application "System Events" to set frontmost of process "{app}" to true')
    time.sleep(0.3)
    return frontmost_app() == app


def open_url(browser: str, url: str) -> bool:
    check_abort()
    osascript(f'tell application "{browser}" to open location "{url}"')
    return activate(browser)


def browser_url(browser: str) -> str | None:
    try:
        return osascript(f'tell application "{browser}" to get URL of active tab of front window') or None
    except subprocess.CalledProcessError:
        return None


def open_path(path: Path, as_text: bool = False) -> None:
    """Show a file to the user; `as_text` opens it in the default text editor."""
    subprocess.run(["open", *(["-t"] if as_text else []), str(path)], check=False)


def frontmost_window_bounds(pid: int | None = None) -> tuple[float, float, float, float] | None:
    """The frontmost app's topmost on-screen window as x, y, w, h in points. Pure Quartz, no AX needed.

    Pass the pid when the caller already has it; looking it up costs an AppleScript round trip.
    """
    pid = frontmost_pid() if pid is None else pid
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    best = None
    for window in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        if window.get("kCGWindowOwnerPID") == pid and window.get("kCGWindowLayer") == 0:
            b = window["kCGWindowBounds"]
            big = b["Width"] > MIN_WINDOW_SIDE_PT and b["Height"] > MIN_WINDOW_SIDE_PT
            # The largest, not the first: a fullscreen browser is toolbar strips plus the page.
            if big and (best is None or b["Width"] * b["Height"] > best[2] * best[3]):
                best = (float(b["X"]), float(b["Y"]), float(b["Width"]), float(b["Height"]))
    return best


def frontmost_window_center(pid: int | None = None) -> tuple[float, float] | None:
    """Center of the frontmost app's topmost on-screen window, in points."""
    bounds = frontmost_window_bounds(pid)
    if bounds is None:
        return None
    x, y, w, h = bounds
    return x + w / 2, y + h / 2


# ------------------------------------------------------------------ capture and accessibility


def screenshot() -> Image.Image:
    path = Path(tempfile.mkdtemp()) / "screen.png"
    subprocess.run(["screencapture", "-x", "-D", "1", str(path)], check=True, capture_output=True)
    return Image.open(path).convert("RGB")


def display_scale(image: Image.Image) -> float:
    points_wide = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID()).size.width
    return image.width / points_wide


def recognize_text(image: Image.Image) -> list[tuple[str, float, tuple[float, float, float, float]]]:
    """Vision OCR lines as text, confidence, and a box in the image's own pixels: accurate, with language correction
    off (on interface text, names, prices and codes, it doubles the time and corrects nothing). ocrmac's own request
    leaves it on, so the request is made here with its helpers."""
    import objc
    import Vision

    with objc.autorelease_pool():
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        request.setUsesLanguageCorrection_(False)
        handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(ocrmac.pil2buf(image), None)
        done = handler.performRequests_error_([request], None)
        if not (done[0] if isinstance(done, tuple) else done):
            return []
        out = []
        for result in request.results() or []:
            box = result.boundingBox()
            frame = (box.origin.x, box.origin.y, box.size.width, box.size.height)
            out.append((str(result.text()), float(result.confidence()), ocrmac.convert_coordinates_pil(frame, *image.size)))
        return out


def _ax_attr(element, name: str):
    """One attribute, or None. A dead or hostile element raises from the bridge; that is a miss, not a crash."""
    try:
        err, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:
        return None
    return value if err == 0 else None


def ax_batch(element, names: tuple[str, ...]) -> dict:
    """Several attributes of one element in one round trip (AXUIElementCopyMultipleAttributeValues): {name: value},
    None where the element has none. A walk reads a handful per node, and each read is a call into the app, so batching
    them is most of a walk's time. One attribute at a time when the batch call fails."""
    try:
        err, values = AS.AXUIElementCopyMultipleAttributeValues(element, list(names), 0, None)
    except Exception:
        err, values = -1, None
    if err != 0 or values is None or len(values) != len(names):
        return {name: _ax_attr(element, name) for name in names}
    return {name: (None if _ax_error(value) else value) for name, value in zip(names, values, strict=True)}


def _ax_error(value) -> bool:
    """An entry of a batched read that is the attribute's error (unsupported, no value), not its value."""
    if type(value).__name__ != "AXValueRef":
        return False
    try:
        return AS.AXValueGetType(value) == AS.kAXValueAXErrorType
    except Exception:
        return True


def focused_field() -> Field | None:
    system = AS.AXUIElementCreateSystemWide()
    element = _ax_attr(system, AS.kAXFocusedUIElementAttribute)
    if element is None:
        return None
    x = y = w = h = 0.0
    pos = _ax_attr(element, AS.kAXPositionAttribute)
    size = _ax_attr(element, AS.kAXSizeAttribute)
    if pos is not None and size is not None:
        _, pt = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
        _, sz = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
        x, y, w, h = pt.x, pt.y, sz.width, sz.height
    value = _ax_attr(element, AS.kAXValueAttribute)
    label = _ax_attr(element, AS.kAXTitleAttribute) or _ax_attr(element, AS.kAXDescriptionAttribute) or ""
    return Field(
        role=str(_ax_attr(element, AS.kAXRoleAttribute) or ""),
        label=str(label),
        placeholder=str(_ax_attr(element, AS.kAXPlaceholderValueAttribute) or ""),
        value=value if isinstance(value, str) else "",
        x=x,
        y=y,
        w=w,
        h=h,
        ref=element,
    )


# ------------------------------------------------------------------ acting on an element

# An element accepts these directly, so a press lands on the control the app declared rather than
# on whatever pixel happens to sit at its center. Every one of them is best effort: the element may
# be dead, the app may refuse, and the bridge raises on both. False means "use synthetic input".


def ax_press(ref) -> bool:
    """Send AXPress to an element."""
    return ax_perform(ref, AX_PRESS)


def ax_perform(ref, action: str) -> bool:
    """Perform one of an element's AX actions (AXPress, AXConfirm, AXCancel, AXScrollToVisible)."""
    check_abort()
    try:
        return AS.AXUIElementPerformAction(ref, action) == 0
    except Exception:
        return False


def ax_set(ref, name: str, value) -> bool:
    """Write one attribute of an element (a scroll bar's AXValue). A read-only or unwilling element reports an error."""
    check_abort()
    try:
        return AS.AXUIElementSetAttributeValue(ref, name, value) == 0
    except Exception:
        return False


def ax_focus(ref) -> bool:
    """Give an element the keyboard focus."""
    check_abort()
    try:
        return AS.AXUIElementSetAttributeValue(ref, AS.kAXFocusedAttribute, True) == 0
    except Exception:
        return False


def ax_set_value(ref, text: str) -> bool:
    """Write an element's value. A read-only or unwilling element reports an error."""
    check_abort()
    try:
        return AS.AXUIElementSetAttributeValue(ref, AS.kAXValueAttribute, text) == 0
    except Exception:
        return False


def ax_value(ref) -> str | None:
    """An element's value, when it has a textual one."""
    value = _ax_attr(ref, AS.kAXValueAttribute)
    return value if isinstance(value, str) else None


def ax_focused_element():
    """The element that has the keyboard focus, system-wide (whatever app it is in), or None. Read-only."""
    return _ax_attr(AS.AXUIElementCreateSystemWide(), AS.kAXFocusedUIElementAttribute)


def ax_pid(ref) -> int | None:
    """The process an element belongs to, or None."""
    try:
        err, pid = AS.AXUIElementGetPid(ref, None)
    except Exception:
        return None
    return int(pid) if err == 0 else None


def ax_selected_range(ref) -> tuple[int, int] | None:
    """A text element's selection as (start, length), or None when it has none."""
    value = _ax_attr(ref, "AXSelectedTextRange")
    if value is None:
        return None
    try:
        ok, cf_range = AS.AXValueGetValue(value, AS.kAXValueCFRangeType, None)
    except Exception:
        return None
    if not ok or cf_range is None:
        return None
    # PyObjC hands a CFRange back as a struct (location, length) or as a plain 2-tuple, depending on the bridge
    if hasattr(cf_range, "location"):
        return int(cf_range.location), int(cf_range.length)
    try:
        start, length = cf_range
        return int(start), int(length)
    except (TypeError, ValueError):
        return None


def ax_select_range(ref, start: int, length: int) -> bool:
    """Select `length` characters from `start` in a text element (AXSelectedTextRange), with no key pressed."""
    check_abort()
    try:
        value = AS.AXValueCreate(AS.kAXValueCFRangeType, AS.CFRange(start, length))
        return AS.AXUIElementSetAttributeValue(ref, "AXSelectedTextRange", value) == 0
    except Exception:
        return False


# ------------------------------------------------------------------ actionable elements

AX_MESSAGE_TIMEOUT = 0.2
AX_VALUE_CHARS = 120


def _ax_children(element) -> list:
    return list(_ax_attr(element, AS.kAXChildrenAttribute) or [])


def _ax_label(element) -> str:
    """AXTitle on AppKit, AXDescription on web and Electron, a short AXValue as a last resort."""
    for name in (AS.kAXTitleAttribute, AS.kAXDescriptionAttribute):
        text = _ax_attr(element, name)
        if isinstance(text, str) and text.strip():
            return " ".join(text.split())
    value = _ax_attr(element, AS.kAXValueAttribute)
    if isinstance(value, str) and 0 < len(value.strip()) <= AX_VALUE_CHARS:
        return " ".join(value.split())
    return ""


def _ax_frame(element) -> Frame | None:
    return ax_frame_of(_ax_attr(element, AS.kAXPositionAttribute), _ax_attr(element, AS.kAXSizeAttribute))


def ax_frame_of(pos, size) -> Frame | None:
    """A frame from an element's AXPosition and AXSize values (read singly or in a batch)."""
    if pos is None or size is None:
        return None
    ok_pos, pt = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok_size, sz = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok_pos and ok_size):
        return None
    return float(pt.x), float(pt.y), float(sz.width), float(sz.height)


def _ax_attrs(element) -> AxAttrs:
    return AxAttrs(str(_ax_attr(element, AS.kAXRoleAttribute) or ""), _ax_label(element), _ax_frame(element))


def _ax_actions(element) -> list[str]:
    try:
        err, names = AS.AXUIElementCopyActionNames(element, None)
    except Exception:
        return []
    return [str(n) for n in names] if err == 0 and names else []


def actionable_elements(pid: int, display_w_pt: float, display_h_pt: float) -> tuple[list[AxNode], list[AxNode], bool]:
    """Labelled controls of one process: the on-screen ones in points, the pressable off-screen ones,
    and whether a cap cut the walk short."""
    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app, AX_MESSAGE_TIMEOUT)
    focused = _ax_attr(app, "AXFocusedWindow")  # its other standard windows are behind it: not walked
    kids = app_children(app, focused, _ax_children, _ax_attrs, lambda n: _ax_attr(n, "AXSubrole"))
    return walk_actionable(app, kids, _ax_attrs, _ax_actions, display_w_pt, display_h_pt)


# ------------------------------------------------------------------ the calendar (EventKit, read-only)

CALENDAR_ANSWER_S = 60.0  # how long the first read waits for the user's answer to the access prompt
_calendar: dict = {"store": None, "asked": False}


def _event_store():
    """An EKEventStore with full access to events, or None. Full access is asked for once a process (the system's own
    prompt), and the answer waited for while the run loop turns, at most CALENDAR_ANSWER_S; denied, restricted,
    write-only or no answer in time is None."""
    import EventKit as EK
    from Foundation import NSDate, NSRunLoop

    status = EK.EKEventStore.authorizationStatusForEntityType_(EK.EKEntityTypeEvent)
    if status != EK.EKAuthorizationStatusFullAccess and (status != EK.EKAuthorizationStatusNotDetermined or _calendar["asked"]):
        return None
    store = _calendar["store"] or EK.EKEventStore.alloc().init()
    if status != EK.EKAuthorizationStatusFullAccess:
        _calendar["asked"] = True
        answer: list[bool] = []
        store.requestFullAccessToEventsWithCompletion_(lambda granted, _error: answer.append(bool(granted)))
        end = time.monotonic() + CALENDAR_ANSWER_S
        while not answer and time.monotonic() < end:
            NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        if not (answer and answer[0]):
            return None
    _calendar["store"] = store
    return store


def calendar_events(day) -> list[str] | None:
    """The events of one day (a datetime.date) across all calendars, as "HH:MM Title" lines and "all day: Title",
    all-day first, then by time (an event that began the day before counts from 00:00). None when Karta has no
    access to the calendar (the caller reads the screen instead). Read-only: nothing is written."""
    from datetime import datetime, timedelta

    from Foundation import NSDate

    store = _event_store()
    if store is None:
        return None
    start = datetime(day.year, day.month, day.day)
    end = datetime.combine(day + timedelta(days=1), start.time())
    query = store.predicateForEventsWithStartDate_endDate_calendars_(
        NSDate.dateWithTimeIntervalSince1970_(start.timestamp()), NSDate.dateWithTimeIntervalSince1970_(end.timestamp()), None
    )
    rows = []
    for event in store.eventsMatchingPredicate_(query) or []:
        title = " ".join(str(event.title() or "").split()) or "untitled"
        if event.isAllDay():
            rows.append(("", f"all day: {title}"))
        else:
            at = max(start, datetime.fromtimestamp(event.startDate().timeIntervalSince1970()))
            rows.append((f"{at:%H:%M}", f"{at:%H:%M} {title}"))
    return [line for _, line in sorted(rows)]
