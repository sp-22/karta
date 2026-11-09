"""Giving up (harness.md, "Ending a task"): each give-up rule ends the task with one honest line built by code (no
brain call to word it), the line is said aloud in voice mode and only with --say when typed. Made-up apps and sites
only; `say` is never run."""

from __future__ import annotations

from test_harness_loop import PLAN, TASK, chat_env, yes
from test_harness_tools import BTN, FACTS, FakeBrain, FakeEnv, FakeLaya, ch, page

from karta import app
from karta.harness import loop
from karta.harness.decide import NONE
from karta.harness.loop import TaskResult, run_task
from karta.harness.tell import done_line, failure_line, reason_words, step_words
from karta.harness.types import Item


class Clock:
    """A clock that moves only when told: the brain's time and the user's time are put on it by hand."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowBrain(FakeBrain):
    """A FakeBrain whose every reply takes `seconds` on `clock`."""

    def __init__(self, clock: Clock, seconds: float, *replies):
        super().__init__(*replies)
        self.clock, self.seconds = clock, seconds

    def json(self, system: str, user: str) -> dict:
        self.clock.now += self.seconds
        return super().json(system, user)


# ---------------------------------------------------------------- each rule ends the task with its line


def test_the_wall_clock_counts_brain_time_and_stops_after_the_budget():
    clock = Clock()
    brain = SlowBrain(clock, 125.0, PLAN)  # the plan alone takes longer than the whole budget
    env = chat_env()
    result = run_task(TASK, env, FakeLaya(), brain, FACTS, confirm=yes, clock=clock)
    assert loop.BUDGET_S == 120.0
    # the first step ran as its line came in; the clock was out before the next one
    assert not result.done and result.gave_up == loop.GAVE_UP_TIME and "click Send" not in env.log
    assert result.said == "I stopped after 120 seconds: I got to Acme chat, but got no further."
    assert brain.calls == 1  # no call to word it


def test_the_users_own_answer_time_is_not_the_tasks():
    clock = Clock()

    def slow_yes(_question: str) -> bool:
        clock.now += 200.0  # the user thought about it for a while
        return True

    result = run_task(TASK, chat_env(), FakeLaya(), FakeBrain(PLAN), FACTS, confirm=slow_yes, clock=clock)
    assert result.done, result.trace
    assert result.said == "Done: build is green."


def test_a_step_retried_past_its_cap_stops_with_what_was_done_and_why():
    env = chat_env()
    env.stubborn = {"Message #general"}  # the message box never takes the text
    fix = {"steps": [{"tool": "fill", "fields": {"Message #general": "build is green"}}], "done_shows": "build is green"}
    again = {"steps": [{"tool": "write", "text": "build is green", "where": "Message #general"}], "done_shows": "build is green"}
    brain = FakeBrain(PLAN, fix, again)
    result = run_task(TASK, env, FakeLaya(), brain, FACTS, confirm=yes)
    assert not result.done and result.rescues == 2  # each rescue's step counted its own retries: no rescue left
    assert result.said == "I couldn't finish: I got to Acme chat but the Message #general field wouldn't take the text."
    assert result.failed_step.startswith("write build is green") and "holds" in result.failed_why
    assert "click Send" not in env.log  # nothing was sent
    assert brain.calls == result.brain_calls == 1 + result.rescues  # the plan and its rescues: none to word the line


def test_four_steps_tried_with_none_passing_stops_the_task():
    # one step after another misses and a rescue waves each through (↷ is no progress); the user's reply re-runs the
    # last: no one step is out of retries, but nothing has moved on in four tries
    env = FakeEnv({"a": page("Gridly", *(Item(n, BTN) for n in ("North", "South", "East", "West")))}, "a")
    steps = [{"tool": "click", "target": n, "then": f"{n} view open"} for n in ("North", "South", "East", "West")]
    skip = {"steps": [], "skip": "not needed"}
    brain = FakeBrain({"steps": steps, "done_shows": "Map ready"}, skip, skip)
    asked: list[str] = []
    result = run_task("open the map", env, FakeLaya(), brain, FACTS, ask=lambda q: asked.append(q) or "go on")
    assert not result.done and result.gave_up == loop.GAVE_UP_IDLE, result.trace
    assert len(asked) == 1 and "click West" not in env.log and result.rescues == 2
    assert result.said == "I couldn't do it: clicking East got me nowhere."


def test_a_rescues_step_counts_its_own_retries_but_a_capped_chain_never_asks_the_user():
    asked: list[str] = []
    env = FakeEnv({"a": page("Postbox", Item("Inbox", "heading"))}, "a")
    brain = FakeBrain(
        {"steps": [{"tool": "click", "target": "Archive everything"}], "done_shows": "Archived"},
        {"steps": [{"tool": "click", "target": "Archive all"}], "done_shows": "Archived"},
        {"steps": [{"tool": "click", "target": "Archive the lot"}], "done_shows": "Archived"},
    )
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.9)))
    result = run_task("archive my inbox", env, laya, brain, FACTS, ask=lambda q: asked.append(q) or "try harder")
    assert result.rescues == 2  # the second fix was tried, not cut short by the first step's count
    assert asked == [] and not result.done and "Archive" in result.said  # the chain is past its cap: not asked


def test_the_brains_own_stop_words_are_the_reason():
    env = FakeEnv(
        {"a": page("Notely", Item("Folders", "heading"), Item("New Note", BTN)), "b": page("Notely", Item("Body", "text"))},
        "a",
        {("a", "click New Note"): "b"},
    )
    brain = FakeBrain(
        {"steps": [{"tool": "click", "target": "New Note"}], "done_shows": "groceries list"},
        {"steps": [], "stop": "the note has no title field"},
    )
    result = run_task("make a groceries list note", env, FakeLaya(), brain, FACTS)
    assert not result.done and brain.calls == 2
    assert result.said == "I couldn't finish: I clicked New Note but the note has no title field."


def test_a_locked_mac_says_so():
    env = FakeEnv({"a": page("loginwindow")}, "a")
    result = run_task("open my notes", env, FakeLaya(), FakeBrain(), FACTS)
    assert result.said == "I stopped: the Mac is locked."


# ---------------------------------------------------------------- the words


def test_the_lines_are_short_and_built_from_the_steps():
    got_there = [{"tool": "open_place", "what": "Postbox"}]
    write = {"tool": "write", "text": "ana@example.com", "where": "To"}
    assert failure_line(
        got_there, reason_words(write, "done, but the screen does not show what was expected", "typed x but 'To' holds ''")
    ) == ("I couldn't finish: I got to Postbox but the To field wouldn't take the address.")
    assert failure_line([], reason_words({"tool": "find", "text": "lamps"}, "no field to type into")) == (
        "I couldn't find a search box on that page."
    )
    assert failure_line([], seconds=90) == "I stopped after 90 seconds."
    assert failure_line(got_there, seconds=90) == "I stopped after 90 seconds: I got to Postbox, but got no further."
    assert step_words({"tool": "write", "text": "hi", "where": "message box"}) == "I typed into the message box"
    assert len(failure_line(got_there, "x " * 80).split()) <= 30
    assert (
        done_line("") == "Done."
        and done_line("Saved") == "Done."
        and done_line("Event saved to calendar") == ("Done: Event saved to calendar.")
    )


# ---------------------------------------------------------------- saying it


class Events:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def emit(self, kind: str, **data) -> None:
        self.sent.append((kind, data))


def failed() -> TaskResult:
    return TaskResult(task="t", done=False, reason="gave up", said="I couldn't find a search box on that page.")


def test_voice_mode_says_the_line_and_the_hud_gets_it(capsys):
    said, events = [], Events()
    speech = app.end_of_task(failed(), events, aloud=True, speak=lambda text: said.append(text) or "speech")
    assert said == ["I couldn't find a search box on that page."] and speech == "speech"
    assert events.sent == [("done", {"ok": False, "reason": "gave up", "said": said[0]})]
    assert "KARTA: I couldn't find a search box" in capsys.readouterr().out


def test_typed_mode_prints_the_line_and_says_nothing_without_say(capsys, monkeypatch):
    said = []
    monkeypatch.setattr(app, "say_aloud", lambda text: said.append(text))
    app.end_of_task(failed(), Events(), aloud=False)
    assert said == [] and "KARTA: I couldn't find" in capsys.readouterr().out
    app.end_of_task(failed(), Events(), aloud=True)  # --say
    assert said == ["I couldn't find a search box on that page."]


def test_a_success_says_done_and_an_answer_is_not_said_twice():
    said: list[str] = []
    ok = TaskResult(task="t", done=True, reason="done", said="Done.")
    app.end_of_task(ok, Events(), aloud=True, speak=said.append)
    answered = TaskResult(task="t", done=True, reason="answered", answer="Two things.", said="", speech="talking")
    assert app.end_of_task(answered, Events(), aloud=True, speak=said.append) == "talking"
    assert said == ["Done."]


def test_the_speech_is_stopped_by_the_next_press_of_the_talk_key():
    class Speech:
        stopped = False

        def stop(self):
            self.stopped = True

    karta = app.Karta.__new__(app.Karta)
    karta.speech = Speech()
    talking = karta.speech
    karta.hush()  # what a press of the talk key (and a new task) runs
    assert talking.stopped and karta.speech is None


def test_say_is_a_flag_of_the_typed_cli(monkeypatch):
    runs = []

    class FakeKarta:
        def __init__(self, **_):
            pass

        def run(self, goal, act, budget, **kw):
            runs.append((goal, budget, kw))

    monkeypatch.setattr(app, "Karta", FakeKarta)
    monkeypatch.setattr("sys.argv", ["karta", "tidy the desk", "--say"])
    app.main()
    assert runs == [("tidy the desk", loop.BUDGET_S, {"trace": False, "aloud": True})]
