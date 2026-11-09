"""The job queue (karta/jobs.py) and voice mode's routing (app.take): two lanes, one screen job at a time, the control
grammar, cancel and pause between steps, status wording, the speech queue. Fake jobs and envs only: no Mac."""

from __future__ import annotations

import threading
import time

import pytest
from test_harness_loop import PLAN, TASK, chat_env, yes
from test_harness_tools import FACTS, FakeBrain, FakeLaya

from karta import app
from karta.harness.loop import run_task
from karta.jobs import Job, Jobs, ScreenDenied, Speaker, State, Stopped, pills


def until(cond, timeout: float = 3.0) -> None:
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


class Gate:
    """A fake screen job's body: each job blocks until released, and overlapping runs are counted."""

    def __init__(self):
        self.lock = threading.Lock()
        self.inside, self.most, self.order = 0, 0, []
        self.release: dict[int, threading.Event] = {}

    def __call__(self, job: Job):
        with self.lock:
            self.inside += 1
            self.most = max(self.most, self.inside)
            self.order.append(job.n)
        self.release.setdefault(job.n, threading.Event()).wait(5)
        with self.lock:
            self.inside -= 1
        return True, "Done."

    def let(self, n: int) -> None:
        self.release.setdefault(n, threading.Event()).set()


class Said:
    """A fake `speak`: records lines; each Speech is said at once."""

    def __init__(self):
        self.lines = []

    def __call__(self, text):
        self.lines.append(text)


def make(run=None, quiet=None):
    said, events = Said(), []
    jobs_ref = {}
    speaker = Speaker(said, quiet=quiet or (lambda: jobs_ref["jobs"].asking()), poll=0.01)
    jobs = Jobs(run or Gate(), speaker, emit=lambda kind, **data: events.append((kind, data)))
    jobs_ref["jobs"] = jobs
    return jobs, speaker, said, events


def test_two_screen_jobs_never_overlap_and_a_queued_one_runs_after_the_current():
    gate = Gate()
    jobs, speaker, said, _ = make(gate)
    first, reply1 = jobs.submit("open my notes app")
    second, reply2 = jobs.submit("summarise this page")
    assert reply1 == "" and reply2 == "I'll do that after the current task."
    assert jobs.submit("check the weather")[1] == "I'll do that after the 2 tasks ahead of it."
    until(lambda: first.state == State.RUNNING)
    time.sleep(0.05)
    assert second.state == State.QUEUED and gate.order == [1]
    gate.let(1)
    until(lambda: second.state == State.RUNNING)
    gate.let(2)
    gate.let(3)
    until(lambda: all(j.state == State.DONE for j in jobs.list()))
    assert gate.most == 1 and gate.order == [1, 2, 3]
    speaker.join()
    assert said.lines == ["Job 1: Done.", "Job 2: Done.", "Job 3: Done."]  # several were live: each says its number
    jobs.close()


def test_a_lone_job_says_its_line_without_a_number():
    gate = Gate()
    jobs, speaker, said, _ = make(gate)
    gate.let(1)
    job, _ = jobs.submit("open my notes app")
    until(lambda: job.state == State.DONE)
    speaker.join()
    assert said.lines == ["Done."]
    jobs.close()


def test_a_background_job_runs_beside_the_screen_job_and_never_gets_the_screen():
    gate = Gate()
    jobs, speaker, said, _ = make(gate)
    screen_job, _ = jobs.submit("open my notes app")
    until(lambda: screen_job.state == State.RUNNING)
    guarded = jobs.guard(object(), screen_job)
    seen = {}

    def work(job):
        seen["args"] = job
        with pytest.raises(ScreenDenied):
            guarded.screen()  # the screen env refuses every thread but the screen worker's
        return "three items."

    bg = jobs.background("count the list", work)
    until(lambda: bg.state == State.DONE)
    assert screen_job.state == State.RUNNING  # it ran alongside, not after
    assert seen["args"] is bg and bg.lane == "background"
    gate.let(1)
    until(lambda: screen_job.state == State.DONE)
    speaker.join()
    assert said.lines[0] == "Job 2: three items."
    jobs.close()


@pytest.mark.parametrize(
    "text, reply",
    [
        ("stop", "Stopping job 1."),
        ("Karta, stop.", "Stopping job 1."),
        ("pause", "Pausing job 1."),
        ("resume", "Nothing is paused."),
        ("cancel the second one", "Cancelling job 2."),
        ("cancel job 3", "Cancelling job 3."),
        ("cancel job three", "Cancelling job 3."),
        ("cancel that", "Cancelling job 3."),
        ("cancel the weather", "Cancelling job 3."),
        ("stop everything", "Stopping everything."),
        (
            "what are you doing?",
            "Job 1 is running: open my notes app. Job 2 is queued: summarise this page. Job 3 is queued: check the weather.",
        ),
        ("stop the timer in the clock app", None),  # a task that starts with a control word stays a task
        ("cancel my dentist appointment", None),
        ("continue the story in my notes", None),
    ],
)
def test_control_phrases(text, reply):
    gate = Gate()
    jobs, *_ = make(gate)
    first, _ = jobs.submit("open my notes app")
    jobs.submit("summarise this page")
    jobs.submit("check the weather")
    until(lambda: first.state == State.RUNNING)
    assert jobs.control(text) == reply
    for n in (1, 2, 3):
        gate.let(n)
    jobs.close()


def test_cancel_a_queued_job_it_never_runs_and_the_rest_go_on():
    gate = Gate()
    jobs, *_ = make(gate)
    first, _ = jobs.submit("open my notes app")
    second, _ = jobs.submit("summarise this page")
    third, _ = jobs.submit("check the weather")
    until(lambda: first.state == State.RUNNING)
    assert jobs.control("cancel the second one") == "Cancelling job 2."
    assert second.state == State.CANCELLED
    gate.let(1)
    gate.let(3)
    until(lambda: third.state == State.DONE)
    assert gate.order == [1, 3] and first.state == State.DONE
    assert jobs.control("cancel job 2") == "Job 2 has already ended."
    jobs.close()


class StepEnv:
    """A fake screen: each step reads the screen, then clicks."""

    def __init__(self):
        self.log = []

    def screen(self):
        self.log.append("read")
        return "screen"

    def click(self, item):
        self.log.append(f"click {item}")


def stepping(env_of, steps=3, after=None):
    """A fake screen job: `steps` steps on its guarded env, `after(job, i)` after step i."""

    def run(job):
        env = env_of(job)
        for i in range(steps):
            env.screen()
            env.click(i)
            if after:
                after(job, i)
        return True, "Done."

    return run


def test_cancel_acts_between_steps():
    env = StepEnv()
    holder = {}

    def after(job, i):
        if i == 0:
            holder["jobs"].control("stop")

    jobs, *_ = make(stepping(lambda job: job.jobs.guard(env, job), after=after))
    holder["jobs"] = jobs
    job, _ = jobs.submit("open my notes app")
    until(lambda: job.state == State.CANCELLED)
    assert env.log == ["read", "click 0", "read"]  # the backstop: step 2 may read the screen, never click
    jobs.close()


def test_pause_holds_at_the_next_read_stops_the_clock_and_resume_reads_afresh():
    env, fresh = StepEnv(), []
    holder, reached = {}, threading.Event()

    def after(job, i):
        if i == 0:
            holder["jobs"].control("pause")
            reached.set()

    jobs, *_ = make(stepping(lambda job: job.jobs.guard(env, job, fresh=lambda: fresh.append(1)), steps=2, after=after))
    holder["jobs"] = jobs
    job, _ = jobs.submit("open my notes app")
    reached.wait(2)
    until(lambda: job.state == State.PAUSED)
    assert jobs.status() == "Job 1 is paused: open my notes app."
    time.sleep(0.2)
    assert env.log == ["read", "click 0"]
    assert jobs.control("resume") == "Resuming job 1."
    until(lambda: job.state == State.DONE)
    assert env.log == ["read", "click 0", "read", "click 1"] and fresh == [1]
    assert job.paused_s >= 0.2 and job.clock() <= time.perf_counter() - 0.2  # paused time is off the budget clock
    jobs.close()


@pytest.mark.v1  # the fakes' plan is in the v1 format
def test_a_cancel_inside_the_harness_stops_before_the_send():
    """The real run_task on the fakes: a stop after the message is typed ends the task before Send is clicked."""
    env = chat_env()
    holder = {}

    def typed(e):
        holder["jobs"].control("stop")

    env.on[("ready", "set Message #general=build is green")] = typed

    def run(job):
        result = run_task(TASK, job.jobs.guard(env, job), FakeLaya(), FakeBrain(PLAN), FACTS, confirm=yes, clock=job.clock)
        return result.done, result.said

    jobs, *_ = make(run)
    holder["jobs"] = jobs
    job, _ = jobs.submit(TASK)
    until(lambda: job.state not in (State.QUEUED, State.RUNNING))
    assert job.state == State.CANCELLED
    assert "set Message #general=build is green" in env.log and "click Send" not in env.log
    jobs.close()


def test_a_question_takes_the_next_spoken_input_not_a_new_job():
    asked = {}

    def run(job):
        asked["reply"] = job.jobs.ask(job, "Send it?")
        return True, "Sent."

    jobs, *_ = make(run)
    job, _ = jobs.submit("send the note")
    until(lambda: job.state == State.ASKING)
    assert jobs.status() == "Job 1 is waiting for your answer: Send it?"
    assert app.take(jobs, "stop", lambda *a, **k: None) == ""  # the answer, even a control word
    until(lambda: job.state == State.DONE)
    assert asked["reply"] == "stop" and len(jobs.list()) == 1
    jobs.close()


def test_take_routes_control_then_new_jobs():
    gate = Gate()
    jobs, *_ = make(gate)
    emitted = []
    emit = type("E", (), {"emit": staticmethod(lambda kind, **data: emitted.append(kind))})
    assert app.take(jobs, "what are you doing", emit) == "Nothing right now."
    assert app.take(jobs, "uh", emit) == "" and emitted == ["done"]  # too short to be a task
    assert app.take(jobs, "open my notes app", emit) == ""
    assert app.take(jobs, "summarise this page", emit) == "I'll do that after the current task."
    assert [j.task for j in jobs.list()] == ["open my notes app", "summarise this page"]
    gate.let(1)
    gate.let(2)
    jobs.close()


def test_cancel_while_asking_answers_no():
    asked = {}

    def run(job):
        asked["reply"] = job.jobs.ask(job, "Send it?")
        if job.cancel.is_set():
            raise Stopped
        return True, "Sent."

    jobs, speaker, said, _ = make(run)
    job, _ = jobs.submit("send the note")
    until(lambda: job.state == State.ASKING)
    jobs.cancel(job)
    until(lambda: job.state == State.CANCELLED)
    assert asked["reply"] == ""
    speaker.join()
    assert said.lines == []  # a cancelled job says nothing more: "Stopping job 1." was its line
    jobs.close()


def test_the_speech_queue_keeps_order_and_never_talks_over_a_question():
    question, said = threading.Event(), Said()
    speaker = Speaker(said, quiet=question.is_set, poll=0.01)
    speaker.say("Job 1: Done.")
    speaker.join()
    question.set()  # a job asks: what follows waits for the answer
    speaker.say("Job 2: three items.")
    speaker.say("Job 3: Done.")
    time.sleep(0.1)
    assert said.lines == ["Job 1: Done."]
    question.clear()
    speaker.join()
    assert said.lines == ["Job 1: Done.", "Job 2: three items.", "Job 3: Done."]


def test_the_speech_queue_waits_for_a_held_speech_and_the_talk_key():
    class Speaking:
        speaking = True

        def stop(self):
            self.speaking = False

    said = Said()
    speaker = Speaker(said, poll=0.01)
    answer = Speaking()
    speaker.hold(answer)  # a tell's answer still being said
    speaker.say("Job 2: Done.")
    time.sleep(0.1)
    assert said.lines == []
    speaker.holding = True  # the talk key went down
    answer.speaking = False
    time.sleep(0.1)
    assert said.lines == []
    speaker.holding = False
    speaker.join()
    assert said.lines == ["Job 2: Done."]
    held = Speaking()
    speaker.hold(held)
    speaker.hush()
    assert not held.speaking


def test_pills_and_the_jobs_event():
    gate = Gate()
    jobs, _, _, events = make(gate)
    first, _ = jobs.submit("summarise this page for me now")
    jobs.submit("open my mail")
    until(lambda: first.state == State.RUNNING)
    assert pills(jobs.live()) == "▶ 1 summarise this page  ·  … 2 open my mail"
    kind, data = events[-1]
    assert kind == "jobs" and [j["state"] for j in data["jobs"]] == ["running", "queued"]
    gate.let(1)
    gate.let(2)
    until(lambda: not jobs.live())
    assert events[-1][1]["jobs"] == [] and events[-1][1]["text"] == "none"
    jobs.close()
