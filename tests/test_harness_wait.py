"""Waiting for the screen: the real env's cheap change detector (thumbnails, then one full read) on a fake pixel
source and a fake clock, the re-reads it replaces fixed sleeps with, the fallback for envs without it, and the
time counters the loop reports."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, ch, page, toolbox

from karta.harness import mac
from karta.harness.decide import NONE
from karta.harness.loop import run_task
from karta.harness.mac import MacHarnessEnv, await_pixels, moved
from karta.harness.types import Item, Screen

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)

A, B, C = np.zeros((4, 4)), np.full((4, 4), 200.0), np.full((4, 4), 90.0)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def frames(clock: Clock, *timeline):
    """A pixel source: `timeline` is (from_second, frame) pairs, the last one started showing."""

    def grab():
        shown = timeline[0][1]
        for at, frame in timeline:
            if clock.t >= at:
                shown = frame
        return shown

    return grab


class Page:
    def _find_page(self) -> None:
        self.found = True


def fake_env(clock: Clock, grab, screens) -> MacHarnessEnv:
    """A MacHarnessEnv without the Mac: `screens(t)` is what a full read at time t says; each read takes 0.5 s."""
    env = MacHarnessEnv.__new__(MacHarnessEnv)
    env._last, env._env = None, Page()
    env._watch(grab, clock, clock.sleep)

    def read() -> Screen:
        clock.t += 0.5
        return screens(clock.t)

    env._read = read
    return env


def said(text: str) -> Screen:
    return Screen("App", [Item(text, "text")])


def test_a_thumbnail_differs_by_one_cell_but_not_by_noise():
    assert not moved(A, A + 3) and moved(A, A + np.pad(np.full((1, 1), 50.0), ((0, 3), (0, 3))))
    assert moved(A, np.zeros((2, 2))) and moved(None, A)


def test_pixels_that_never_move_wait_out_the_deadline_and_say_so():
    clock = Clock()
    assert not await_pixels(frames(clock, (0, A)), A, 1.0, clock, clock.sleep)
    assert 1.0 <= clock.t < 1.1


def test_pixels_that_move_are_waited_on_until_they_hold_still():
    clock = Clock()
    assert await_pixels(frames(clock, (0, A), (0.3, B), (0.38, C)), A, 3.0, clock, clock.sleep)  # C before B held still
    assert 0.38 + mac.STILL <= clock.t < 0.38 + mac.STILL + 0.1


def test_pixels_that_never_hold_still_stop_at_the_cap():
    clock = Clock()
    flicker = lambda: B if int(clock.t / mac.THUMB_POLL) % 2 else C  # noqa: E731
    assert await_pixels(flicker, A, 3.0, clock, clock.sleep)
    assert clock.t <= mac.STILL_CAP + 0.1


def test_nothing_changes_one_full_read_at_the_end():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A)), lambda t: said("same"))
    before = env.screen().fingerprint()
    assert env.reads == 1
    clock.t = 0.0
    assert not env.changed_since(before, 1.0)
    assert env.reads == 2 and 1.0 <= clock.t < 1.7  # the timeout, then one read
    assert 1.0 <= env.wait_seconds < 1.1 and env.read_seconds == 1.0


def test_a_change_is_read_once_after_the_pixels_settle():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A), (0.8, B)), lambda t: said("after" if t > 0.8 else "before"))
    before = env.screen().fingerprint()  # t = 0.5
    env._mark()  # the action, at t = 0.5
    assert env.changed_since(before, 3.5)
    assert env.reads == 2 and clock.t < 0.8 + mac.STILL + 0.1 + 0.5


def test_pixels_that_move_without_a_new_screen_keep_it_waiting():
    clock = Clock()  # a caret blinks at 0.2 s; the real change comes at 1.5 s
    env = fake_env(clock, frames(clock, (0, A), (0.7, B), (1.5, C)), lambda t: said("after" if t > 1.5 else "before"))
    before = env.screen().fingerprint()
    env._mark()
    assert env.changed_since(before, 3.5)
    assert env.reads == 3 and clock.t < 3.0


def test_a_read_since_the_action_that_shows_the_change_answers_at_once():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A)), lambda t: said("after"))
    env.screen()
    assert env.changed_since(said("before").fingerprint(), 3.5) and env.reads == 1 and clock.t == 0.5


def test_wait_change_returns_as_soon_as_the_screen_settles():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A), (0.7, B)), lambda t: said("x"))
    env.screen()  # t = 0.5, its thumbnail A
    assert env.wait_change(2.0) and clock.t < 0.7 + mac.STILL + 0.1
    assert env._last is None and env._env.found  # the next read is fresh, the page frame looked up again


def test_wait_change_without_a_change_waits_out_its_timeout():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A)), lambda t: said("x"))
    env.screen()
    assert not env.wait_change(0.3) and 0.8 <= clock.t < 0.9 and env._last is None


def test_actions_are_timed_without_their_reads_and_waits():
    clock = Clock()
    env = fake_env(clock, frames(clock, (0, A)), lambda t: said("x"))
    with env._acts():
        clock.t += 0.2
        env.screen()
        with env._acts():  # an action inside an action counts once
            clock.t += 0.1
    assert round(env.act_seconds, 2) == 0.3 and round(env.read_seconds, 2) == 0.5


# ---------------------------------------------------------------- the tools on an env that waits for changes


class WatchingEnv(FakeEnv):
    """FakeEnv with a change wait: `changes` says, per call, which page the screen settles on (None: no change)."""

    def __init__(self, *args, changes=(), **kw):
        super().__init__(*args, **kw)
        self.changes, self.waited = list(changes), []

    def wait_change(self, timeout: float) -> bool:
        self.waited.append(timeout)
        nxt = self.changes.pop(0) if self.changes else None
        if nxt:
            self.at = nxt
        return nxt is not None


def test_rechecks_re_read_as_soon_as_the_screen_changes_and_never_sleep():
    env = WatchingEnv(
        {
            "a": page("Docs", Item("Loading…", "text")),
            "b": page("Docs", Item("Blank document", BTN)),
            "c": page("Docs", Item("Untitled document", "heading")),
        },
        "a",
        {("b", "click Blank document"): "c"},
        changes=["b"],
    )
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = toolbox(env, laya).tool_click("Blank document")
    assert result.ok and env.log == ["click Blank document"]  # no fixed wait
    assert env.waited[0] == 0.3


def test_rechecks_stop_at_the_first_unchanged_screen_that_is_not_loading():
    env = WatchingEnv({"a": page("App", Item("Hello", "text"))}, "a")
    tb = toolbox(env)
    assert list(tb.rechecks(env.screen())) == [] and len(env.waited) == 1 and "wait" not in env.log


def test_rechecks_keep_waiting_while_the_page_says_it_is_loading():
    env = WatchingEnv({"a": replace(page("App", Item("Results", "text")), busy=True)}, "a")  # aria-busy, not a word
    tb = toolbox(env)
    seen = list(tb.rechecks(env.screen()))
    assert seen and all(t <= 2.0 for t in env.waited) and "wait" not in env.log


def test_an_env_without_a_change_wait_keeps_the_fixed_backoff():
    env = FakeEnv({"a": replace(page("App", Item("Results", "text")), busy=True)}, "a")
    tb = toolbox(env)
    assert len(list(tb.rechecks(env.screen()))) == 5 and env.log.count("wait") == 5
    assert tb.hold(0.3) is None and env.log.count("wait") == 6


def test_settle_without_a_change_poll_uses_the_change_wait():
    env = WatchingEnv({"a": page("App", Item("Go", BTN)), "b": page("App", Item("There", "text"))}, "a", changes=[None, "b"])
    tb = toolbox(env)
    scr = tb.settle(env.screen().fingerprint())
    assert scr is not None and scr.items[0].name == "There" and env.waited == [0.3, 0.5] and "wait" not in env.log


# ---------------------------------------------------------------- the timings


class CountingEnv(FakeEnv):
    """FakeEnv keeping the real env's counters: each read 0.25 s, each action 0.1 s acting and 0.5 s waiting."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.reads, self.read_seconds, self.wait_seconds, self.act_seconds = 0, 0.0, 0.0, 0.0

    def screen(self):
        self.reads += 1
        self.read_seconds += 0.25
        return super().screen()

    def _go(self, action: str) -> None:
        self.act_seconds += 0.1
        self.wait_seconds += 0.5
        super()._go(action)


def test_timings_count_the_real_envs_reads_waits_and_actions_per_step_and_in_all():
    env = CountingEnv(
        {"a": page("App", Item("Go", BTN)), "b": page("App", Item("Arrived", "text"))}, "a", {("a", "click Go"): "b"}
    )
    brain = FakeBrain({"steps": [{"tool": "click", "target": "Go"}], "done_shows": "Arrived"})
    result = run_task("go", env, FakeLaya(), brain, FACTS)
    assert result.done, result.trace
    t = result.timings
    assert t["reads"] == env.reads and t["read_s"] == round(env.read_seconds, 2)
    assert t["wait_s"] == 0.5 and t["act_s"] == 0.1 and t["env_s"] == round(t["read_s"] + t["wait_s"], 2)
    step = result.steps[0]
    assert step["reads"] >= 1 and step["wait_s"] == 0.5 and step["act_s"] == 0.1 and "laya_s" in step and "s" in step


def test_an_env_without_counters_keeps_the_role_players_time():
    env = FakeEnv({"a": page("App", Item("Go", BTN)), "b": page("App", Item("Arrived", "text"))}, "a", {("a", "click Go"): "b"})
    result = run_task(
        "go", env, FakeLaya(), FakeBrain({"steps": [{"tool": "click", "target": "Go"}], "done_shows": "Arrived"}), FACTS
    )
    assert result.done and "reads" not in result.timings and "reads" not in result.steps[0] and result.timings["env_s"] == 0.0
