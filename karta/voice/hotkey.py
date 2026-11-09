"""Push to talk: Karta hears only while a key is held (Right Option by default), so it never listens or acts on its
own. A listen-only Quartz event tap watches the key from a background thread; it needs the terminal's Accessibility
or Input Monitoring permission, and sees only that key's up and down and whether another key was pressed during the
hold (never which key)."""

from __future__ import annotations

import queue
import threading
import time

import numpy as np
import Quartz
import sounddevice as sd

from . import config

RIGHT_OPTION = 61  # the Right Option key's keycode
MIN_HOLD_S = 0.6  # a shorter press is a slip, not speech
MIN_SPEECH_RMS = 0.006  # quieter than this the whole hold is silence: nothing was said (speech is about 0.02 to 0.08)
MAX_HOLD_S = 30.0
LEVEL_EVERY_S = 0.05  # how often the HUD's bars hear the mic's loudness
LEVEL_GAIN = 12.0  # speech RMS (about 0.02 to 0.08) to 0..1


class HoldToTalk:
    """`record()` waits for the key to go down, records the mic until it comes up, and returns the audio (float32,
    16 kHz mono)."""

    def __init__(self, keycode: int = RIGHT_OPTION):
        self.keycode = keycode
        self.changes: queue.Queue[bool] = queue.Queue()  # True: pressed, False: released
        self._down = False
        self.chorded = False  # another key went down while the talk key was held: a shortcut (Option+key), not speech
        ready = threading.Event()
        self._error: str | None = None
        threading.Thread(target=self._watch, args=(ready,), daemon=True).start()
        ready.wait(5.0)
        if self._error:
            raise SystemExit(self._error)

    def _watch(self, ready: threading.Event) -> None:
        def seen(_proxy, kind, event, _refcon):
            if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                Quartz.CGEventTapEnable(self._tap, True)  # macOS switched the watch off: straight back on
                return event
            if kind == Quartz.kCGEventKeyDown:
                if self._down:
                    self.chorded = True
                return event
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode) == self.keycode:
                down = bool(Quartz.CGEventGetFlags(event) & Quartz.kCGEventFlagMaskAlternate)
                if down != self._down:
                    self._down = down
                    self.changes.put(down)
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),
            seen,
            None,
        )
        if tap is None:
            self._error = "cannot watch the talk key: give this terminal Input Monitoring (System Settings > Privacy & Security)"
            ready.set()
            return
        self._tap = tap
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
        ready.set()
        Quartz.CFRunLoopRun()

    def _wait(self, down: bool, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            left = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                if self.changes.get(timeout=left) == down:
                    return True
            except queue.Empty:
                return False

    def record(self, on_start=None, on_level=None) -> np.ndarray | None:
        """The speech said while the key was held; None for a slip (held under MIN_HOLD_S). `on_level(0..1)` gets the
        mic's loudness about 20 times a second while recording (for the HUD's bars)."""
        while not self.changes.empty():  # presses made while Karta was busy do not count
            self.changes.get_nowait()
        self._wait(True)
        self.chorded = False
        blocks: list[np.ndarray] = []
        last = [0.0]

        def took(data, *_):
            blocks.append(data[:, 0].copy())
            now = time.monotonic()
            if on_level and now - last[0] >= LEVEL_EVERY_S:
                last[0] = now
                on_level(min(1.0, float(np.sqrt(np.mean(data[:, 0] ** 2))) * LEVEL_GAIN))

        stream = sd.InputStream(samplerate=config.SAMPLE_RATE, channels=1, dtype="float32", callback=took)
        stream.start()
        if on_start:
            on_start()
        started = time.monotonic()
        self._wait(False, MAX_HOLD_S)
        time.sleep(0.15)  # the last syllable is still coming in as the key comes up
        stream.stop()
        stream.close()
        if time.monotonic() - started < MIN_HOLD_S or not blocks or self.chorded:
            return None  # a slip, or Option used as a modifier for another key
        audio = np.concatenate(blocks)
        if float(np.sqrt(np.mean(audio**2))) < MIN_SPEECH_RMS:
            return None  # held, but nothing was said: whisper would make words up from the silence
        return audio
