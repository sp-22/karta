"""`karta-hud`: a floating glass panel that shows what Karta heard and what it is doing, and while a task runs an
soft blue glow fading in from the screen's edges (as screen sharing shows it), so nobody takes the mouse from Karta mid-task.

Run it once (`uv run karta-hud &`); any Karta run started with `--hud` sends it events over
localhost UDP. It never takes focus, shows on every Space (fullscreen apps too), and writes its
own frame to FRAME_FILE so Karta's screen reader ignores the panel's text.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import socket
import threading
import time
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSColor,
    NSFont,
    NSMakeRect,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSTextField,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialHUDWindow,
    NSVisualEffectStateActive,
    NSVisualEffectView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper
from Quartz import CAGradientLayer, CALayer

from karta.core.events import HUD_PORT

WIDTH, HEIGHT, BOTTOM = 520.0, 58.0, 96.0  # points; BOTTOM is the gap above the screen's bottom edge (clears the Dock)
PILLS_H = 20.0  # points: the row of job pills above the status lines, there only while jobs are live
FRAME_FILE = Path.home() / "Library" / "Application Support" / "karta" / "hud-frame.json"
MARKS = {"ok": "✓", "no_match": "✗", "no_change": "✗", "unexpected": "✗", "refused": "⊘", "running": "▸"}
HIDE_AFTER = 4.0  # seconds after a run ends
ANSWER_WPS = 2.5  # an answer stays up as long as it takes to read at this many words a second
BARS, BAR_W, BAR_GAP, BAR_MIN, BAR_MAX = 7, 4.0, 4.0, 4.0, 34.0  # the voice bars, in points
EDGE = 34.0  # points: how far the glow fades into the screen from each edge while Karta is in control
EDGE_ALPHA = 0.45  # the glow's strength right at the edge
IN_CONTROL = "Karta is controlling this Mac"
PICK_SHOWN = 2.0  # seconds a pick's "how" (Laya 82%, 'Y' was wrong) stays beside the step it clicks
MARK_S, MARK_PAD = 0.3, 4.0  # follow mode: the highlight round a pick, gone before the click lands (points)


def pick_text(event: dict) -> tuple[str, str]:
    """A "pick" event as the status line shows it, and the short how kept beside the click that follows."""
    p = event.get("p")
    how = f"Laya {p:.0%}" if isinstance(p, (int, float)) else str(event.get("how", ""))
    wrong = event.get("instead_of")
    short = f"{how} · {wrong!r} was wrong" if wrong else how
    return f"◎ {event.get('label', '')!r} ({short})", short


class PickMark(NSObject):
    """Follow mode: a click-through blue frame round the item just picked, shown briefly before it is clicked."""

    def init(self):
        self = objc.super(PickMark, self).init()
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 10, 10),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        view = panel.contentView()
        view.setWantsLayer_(True)
        view.layer().setBorderWidth_(3.0)
        view.layer().setCornerRadius_(6.0)
        view.layer().setBorderColor_(NSColor.systemBlueColor().CGColor())
        self.panel, self.timer = panel, None
        return self

    @objc.python_method
    def flash(self, frame: dict) -> None:
        x, y, w, h = (float(frame[k]) for k in ("x", "y", "w", "h"))
        top = NSScreen.mainScreen().frame().size.height  # the frame is top-left origin; AppKit counts from the bottom
        self.panel.setFrame_display_(NSMakeRect(x - MARK_PAD, top - y - h - MARK_PAD, w + 2 * MARK_PAD, h + 2 * MARK_PAD), True)
        self.panel.orderFrontRegardless()
        if self.timer:
            self.timer.invalidate()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(MARK_S, self, "gone:", None, False)

    def gone_(self, _timer):
        self.panel.orderOut_(None)  # at once, no fade: the click's own change must be the only thing that moves


class Edge(NSObject):
    """A click-through, full-screen panel that draws only a blue glow fading in from the edges: no text, so the screen reader has nothing
    of it to read, and every click goes through to the app under it."""

    def init(self):
        self = objc.super(Edge, self).init()
        screen = NSScreen.mainScreen().frame()
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            screen, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setAlphaValue_(0.0)
        view = panel.contentView()
        view.setWantsLayer_(True)
        blue = NSColor.systemBlueColor().colorWithAlphaComponent_(EDGE_ALPHA).CGColor()
        clear = NSColor.systemBlueColor().colorWithAlphaComponent_(0.0).CGColor()
        w, h = screen.size.width, screen.size.height
        for frame, start, end in (  # (the edge's strip, where the glow starts, where it has faded)
            (((0, h - EDGE), (w, EDGE)), (0.5, 1.0), (0.5, 0.0)),  # top
            (((0, 0), (w, EDGE)), (0.5, 0.0), (0.5, 1.0)),  # bottom
            (((0, 0), (EDGE, h)), (0.0, 0.5), (1.0, 0.5)),  # left
            (((w - EDGE, 0), (EDGE, h)), (1.0, 0.5), (0.0, 0.5)),  # right
        ):
            glow = CAGradientLayer.layer()
            glow.setFrame_(frame)
            glow.setColors_([blue, clear])
            glow.setStartPoint_(start)
            glow.setEndPoint_(end)
            view.layer().addSublayer_(glow)
        self.panel = panel
        return self

    def show(self):
        self.panel.orderFrontRegardless()
        self.panel.animator().setAlphaValue_(1.0)

    def hide(self):
        self.panel.animator().setAlphaValue_(0.0)


def _label(frame, size: float, weight: float, color) -> NSTextField:
    field = NSTextField.labelWithString_("")
    field.setFrame_(frame)
    field.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    field.setTextColor_(color)
    field.setLineBreakMode_(4)  # truncate tail
    return field


class Hud(NSObject):
    def init(self):
        self = objc.super(Hud, self).init()
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - WIDTH) / 2
        y = BOTTOM  # AppKit counts y from the bottom
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, WIDTH, HEIGHT), style, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setIgnoresMouseEvents_(True)
        panel.setAlphaValue_(0.0)

        glass = NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
        glass.setMaterial_(NSVisualEffectMaterialHUDWindow)
        glass.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(NSVisualEffectStateActive)
        glass.setWantsLayer_(True)
        glass.layer().setCornerRadius_(14.0)
        glass.layer().setMasksToBounds_(True)
        panel.setContentView_(glass)
        self.heard = _label(NSMakeRect(18, 30, WIDTH - 36, 20), 14, 0.23, NSColor.labelColor())
        self.pills = _label(NSMakeRect(18, HEIGHT - 4, WIDTH - 36, 16), 11, 0.3, NSColor.systemBlueColor())
        self.status = _label(NSMakeRect(18, 10, WIDTH - 36, 18), 12, 0.0, NSColor.secondaryLabelColor())
        glass.addSubview_(self.heard)
        glass.addSubview_(self.status)
        glass.addSubview_(self.pills)
        # the voice bars (like a dictation pill): they follow the mic while the talk key is held
        glass.setWantsLayer_(True)
        self.bars = []
        for _ in range(BARS):
            bar = CALayer.layer()
            bar.setBackgroundColor_(NSColor.systemBlueColor().CGColor())
            bar.setCornerRadius_(BAR_W / 2)
            bar.setHidden_(True)
            glass.layer().addSublayer_(bar)
            self.bars.append(bar)
        self.level = 0.0
        self._place_bars(0.0)

        self.panel = panel
        self.edge = Edge.alloc().init()
        self.mark = PickMark.alloc().init()
        self.hide_timer = None
        self.picked, self.picked_until = "", 0.0  # the last pick's how, shown beside its click
        self.pinned = ""  # "pinned" or "pinned v2" once this run's path is pinned: shown with Done
        self.live = False  # jobs are live (voice mode): the pills row shows and the panel stays up
        self._fit(HEIGHT)
        return self

    @objc.python_method
    def _fit(self, height: float) -> None:
        """The panel's height (it grows upwards for the pills row), and its frame for Karta's screen reader."""
        screen = NSScreen.mainScreen().frame()
        x = (screen.size.width - WIDTH) / 2
        self.panel.setFrame_display_(NSMakeRect(x, BOTTOM, WIDTH, height), True)
        FRAME_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Top-left origin, points: what Karta's screen reader uses to skip the panel's own text.
        FRAME_FILE.write_text(json.dumps({"x": x, "y": screen.size.height - BOTTOM - height, "w": WIDTH, "h": height}))

    # ------------------------------------------------------------ showing

    def show(self):
        if self.hide_timer:
            self.hide_timer.invalidate()
            self.hide_timer = None
        self.panel.orderFrontRegardless()
        self.panel.animator().setAlphaValue_(1.0)

    def hideSoon(self):
        self.hide_after(HIDE_AFTER)

    @objc.python_method
    def hide_after(self, seconds: float) -> None:
        self.hide_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            seconds, self, "hide:", None, False
        )

    def hide_(self, _timer):
        if self.live:  # jobs are live: their pills stay up
            return
        self.panel.animator().setAlphaValue_(0.0)

    @objc.python_method
    def _place_bars(self, level: float) -> None:
        self.level = 0.55 * self.level + 0.45 * level  # smooth, so the bars breathe rather than flicker
        x0 = WIDTH - 18 - BARS * (BAR_W + BAR_GAP)
        for n, bar in enumerate(self.bars):
            shape = 0.55 + 0.45 * math.sin((n + 1) * 1.7 + time.monotonic() * 9.0)  # each bar its own sway
            h = BAR_MIN + (BAR_MAX - BAR_MIN) * min(1.0, self.level * shape * 1.4)
            bar.setFrame_(((x0 + n * (BAR_W + BAR_GAP), (HEIGHT - h) / 2), (BAR_W, h)))

    @objc.python_method
    def show_bars(self, on: bool) -> None:
        for bar in self.bars:
            bar.setHidden_(not on)
        width = WIDTH - 36 - (BARS * (BAR_W + BAR_GAP) + 12 if on else 0)
        for label in (self.heard, self.status):
            frame = label.frame()
            label.setFrame_(((frame.origin.x, frame.origin.y), (width, frame.size.height)))

    @objc.python_method
    def say(self, text: str) -> None:
        self.status.setStringValue_(text)

    # ------------------------------------------------------------ events

    def handle_(self, event):
        kind = event.get("kind")
        if kind == "listening":  # the talk key went down: show it at once
            self.heard.setStringValue_("Listening…")
            self.say("Hold Right Option while you speak; let go when you're done")
            self.show_bars(True)
            self._place_bars(0.0)
            self.show()
            return
        if kind == "jobs":  # a pill per live job: "▶ 1 summarise this page  ·  … 2 open my mail"
            live = bool(event.get("jobs"))
            self.pills.setStringValue_(str(event.get("text", "")) if live else "")
            if live != self.live:
                self.live = live
                self._fit(HEIGHT + PILLS_H if live else HEIGHT)
            if live:
                self.show()
            return
        if kind == "level":
            self._place_bars(float(event.get("v", 0.0)))
            return
        if kind == "transcribing":
            self.show_bars(False)
            self.heard.setStringValue_("Transcribing…")
            self.say("")
            return
        if kind == "idle":
            self.show_bars(False)
            self.hide_(None)
            return
        self.show_bars(False)
        if kind in ("planning", "step"):
            self.edge.show()  # Karta is in control until the run ends (not while it only asks or reads a task back)
        if kind == "heard":
            self.pinned = ""
            self.heard.setStringValue_(event.get("text", ""))
            self.say(f"{IN_CONTROL} · Listening…")
            self.show()
        elif kind == "planning":
            self.say(f"{IN_CONTROL} · Planning…")
        elif kind == "step":
            aside = f"  ·  {self.picked}" if self.picked and time.monotonic() < self.picked_until else ""
            self.say(f"{MARKS['running']} {event.get('text', '')}{aside}")
        elif kind == "pick":
            text, self.picked = pick_text(event)
            self.picked_until = time.monotonic() + PICK_SHOWN
            self.say(text)
            if isinstance(event.get("frame"), dict):
                with contextlib.suppress(Exception):  # a highlight must never break the HUD
                    self.mark.flash(event["frame"])
        elif kind == "retry":
            self.say(f"✗ {event.get('wrong', '')!r} was wrong — trying the next ({event.get('attempt', 2)}/{event.get('of', 3)})")
        elif kind == "step_done" and event.get("outcome") != "ok":
            self.say(f"{MARKS.get(event.get('outcome', ''), '·')} {event.get('text', '')}")
        elif kind == "replan":
            self.say("↻ Trying another way…")
        elif kind == "replay":  # a pinned path: no planning, every step still checked
            self.say(f"{IN_CONTROL} · Pinned · replaying")
        elif kind == "pinned":
            self.pinned = str(event.get("short") or "")
        elif kind == "tell":  # the answer to a question: shown while it is said
            self.heard.setStringValue_(" ".join(str(event.get("text", "")).split()))
            self.say("Answering aloud · press Right Option to stop")
            self.show()
        elif kind == "done":
            self.edge.hide()  # the Mac is the user's again
            answer = str(event.get("answer") or "")
            if answer:
                self.heard.setStringValue_(" ".join(answer.split()))
                self.say("✓ Answered")
                self.hide_after(max(HIDE_AFTER, len(answer.split()) / ANSWER_WPS))  # long enough to read it
                return
            said = " ".join(str(event.get("said") or "").split())
            if said and not event.get("ok"):  # why it stopped, in the words being said aloud
                self.heard.setStringValue_(said)
                self.say("⊘ Couldn't finish · press Right Option to stop")
                self.hide_after(max(HIDE_AFTER, len(said.split()) / ANSWER_WPS))
                return
            pinned, self.pinned = self.pinned, ""
            self.say((f"✓ Done · {pinned}" if pinned else "✓ Done") if event.get("ok") else f"⊘ {event.get('reason', 'Stopped')}")
            self.hideSoon()


def _listen(hud: Hud) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", HUD_PORT))
    while True:
        data, _ = sock.recvfrom(65536)
        try:
            event = json.loads(data.decode())
        except ValueError:
            continue
        AppHelper.callAfter(hud.handle_, event)


def _follow(parent: int) -> None:
    while True:
        time.sleep(1.0)
        try:
            os.kill(parent, 0)
        except OSError:
            FRAME_FILE.unlink(missing_ok=True)
            os._exit(0)


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, never takes focus
    hud = Hud.alloc().init()
    threading.Thread(target=_listen, args=(hud,), daemon=True).start()
    parent = int(os.environ.get("KARTA_PARENT_PID") or 0)
    if parent:  # started by `karta start`: go when it goes, however it was stopped
        threading.Thread(target=_follow, args=(parent,), daemon=True).start()
    print(f"karta-hud: listening on 127.0.0.1:{HUD_PORT} (Ctrl-C to quit)")
    try:
        AppHelper.runEventLoop(installInterrupt=True)
    finally:
        FRAME_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
