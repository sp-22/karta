"""Jobs: what Karta is doing while it keeps listening (`karta start`). Above the harness, beside app.py.

Two lanes. One screen worker thread owns the mouse and keyboard: screen jobs run strictly one at a time, in the order
they were asked for. A small background pool runs jobs that never touch the screen: a background job's work gets its
Job and nothing else, and the screen env a screen job runs on (`Guard`) refuses any thread but the screen worker's.

Voice is the control plane, with no brain call: a spoken input goes first to a job waiting for an answer
(`answer`), then to the control grammar (`control`: stop, pause, resume, cancel <which>, what are you doing), and
otherwise becomes a new screen job (`submit`). With `watches` (karta/watches.py) the grammar also lists watches and
schedules ("what are you watching?") and ends one ("stop watching the build", "cancel the 9 o'clock brief"). Cancel
is a clean stop: `run_task(stop=job.stopping)` sees it between steps, ends with GAVE_UP_STOPPED and closes a dialog
the task opened. `Guard` is the backstop: after a cancel the
screen may still be read, but an action the harness has not acknowledged the stop for raises `Stopped` (a
BaseException, so it leaves `run_task` the way Ctrl-C does): nothing is clicked or typed after a stop but the
harness's own clean-up. Pause holds a paused job at its next screen read, with the job's `clock` stopped meanwhile. A
job's question resolves "" (no) when it is cancelled.

`Speaker` is the one speech queue: lines are said in order, one at a time, never while a question waits for its
answer, while the talk key is held, or while something else is being said.
"""

from __future__ import annotations

import contextlib
import queue
import re
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import StrEnum


class State(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    ASKING = "asking"
    PAUSED = "paused"
    WATCHING = "watching"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


LIVE = (State.QUEUED, State.RUNNING, State.ASKING, State.PAUSED, State.WATCHING)
PILL = {State.RUNNING: "▶", State.ASKING: "?", State.PAUSED: "⏸", State.QUEUED: "…", State.WATCHING: "◷"}
STATUS = {
    State.RUNNING: "is running",
    State.ASKING: "is waiting for your answer",
    State.PAUSED: "is paused",
    State.QUEUED: "is queued",
    State.WATCHING: "is watching",
}
TITLE_WORDS = 5
BACKGROUND_WORKERS = 4


class Stopped(BaseException):  # a stop, like KeyboardInterrupt, not an error
    """The job was cancelled: raised at its next screen read or action, out through `run_task` like Ctrl-C."""


class ScreenDenied(RuntimeError):
    """A thread other than the screen worker reached for the screen."""


def title_of(task: str) -> str:
    """The job's short name, by code: the task's first words."""
    words = task.split()
    return " ".join(words[:TITLE_WORDS]) + (" …" if len(words) > TITLE_WORDS else "")


@dataclass
class Job:
    n: int  # spoken number, 1.., per process
    task: str
    lane: str = "screen"  # "screen" | "background"
    kind: str = "task"  # task | think | delegate (later: watch, schedule)
    title: str = ""
    state: State = State.QUEUED
    said: str = ""
    question: str = ""  # what it asks, while ASKING
    shared: bool = False  # another job was live alongside it: its spoken lines carry its number
    started: float = 0.0
    ended: float = 0.0
    paused_s: float = 0.0  # time held paused: off the task's budget clock
    halting: bool = False  # the harness saw the cancel (stopping): its clean-up may act
    cancel: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)  # set: may go on; clear: pause at the next read
    jobs: Jobs | None = field(default=None, repr=False)  # the table it is in (its `guard`)

    def __post_init__(self):
        self.resume.set()
        self.title = self.title or title_of(self.task)

    def stopping(self) -> bool:
        """`run_task`'s stop: True once the job is cancelled, and from then on its clean-up may act (Guard)."""
        if self.cancel.is_set():
            self.halting = True
            return True
        return False

    def clock(self) -> float:
        """`run_task`'s clock: the wall clock less the time the job was held paused."""
        return time.perf_counter() - self.paused_s


class Guard:
    """The screen env a screen job runs on: only the screen worker may use it; after a cancel the screen may still
    be read (the harness stops at its next step boundary, `Job.stopping`), but an action raises `Stopped` unless the
    harness has seen the stop (its clean-up: closing a dialog it opened); at a screen read a paused job waits for
    resume (`on_pause`, then `fresh()` so the read after it is new, then `on_resume`). Never between a press and its
    check: one action is one call."""

    ACTIONS = frozenset(
        [
            "click",
            "click_once",
            "set_text",
            "type_at_focus",
            "type_keys",
            "press",
            "open_app",
            "open_url",
            "focus_tab",
            "scroll",
            "scroll_top",
            "back",
            "wait",
            "open_terminal",
        ]
    )

    def __init__(
        self, env, job: Job, owner: threading.Thread, fresh: Callable[[], object] | None = None, on_pause=None, on_resume=None
    ):
        self._env, self._job, self._owner, self._fresh = env, job, owner, fresh
        self._on_pause, self._on_resume = on_pause, on_resume

    def _check(self, reading: bool) -> None:
        job = self._job
        if threading.current_thread() is not self._owner:
            raise ScreenDenied(f"job {job.n}: only the screen worker uses the screen")
        if job.cancel.is_set():
            if reading or job.halting:
                return
            raise Stopped  # the backstop: nothing acts after a stop the harness has not seen
        if reading and not job.resume.is_set():
            if self._on_pause:
                self._on_pause()
            t = time.perf_counter()
            while not job.resume.wait(0.05) and not job.cancel.is_set():
                pass
            job.paused_s += time.perf_counter() - t
            if job.cancel.is_set():  # stopped while paused: the read goes on, the harness stops at its boundary
                return
            if self._fresh:
                self._fresh()  # the user may have used the Mac meanwhile: read it anew
            if self._on_resume:
                self._on_resume()

    def screen(self):
        self._check(True)
        return self._env.screen()

    def __getattr__(self, name):
        attr = getattr(self._env, name)
        if name not in self.ACTIONS or not callable(attr):
            return attr

        def act(*args, **kwargs):
            self._check(False)
            return attr(*args, **kwargs)

        return act


@dataclass
class _Question:
    job: Job
    text: str
    reply: str = ""
    answered: threading.Event = field(default_factory=threading.Event)


class Speaker:
    """The one speech queue: each line in order, never over another line, a question waiting for its answer
    (`quiet()`), the talk key held, or a Speech said outside the queue (`hold`: a tell's answer)."""

    def __init__(self, speak: Callable[[str], object], quiet: Callable[[], bool] = lambda: False, poll: float = 0.05):
        self._speak, self._quiet, self._poll = speak, quiet, poll
        self._q: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._speeches: list = []  # what is or may be being said
        self.holding = False  # the talk key is down
        threading.Thread(target=self._run, daemon=True, name="karta-speaker").start()

    def say(self, text: str) -> None:
        if text and text.strip():
            self._q.put(text.strip())

    def hold(self, speech) -> None:
        """Something said outside the queue (a tell step's answer): the next line waits for it to end."""
        if speech is not None:
            with self._lock:
                self._speeches.append(speech)

    def hush(self) -> None:
        """Stop what is being said now (a new press of the talk key); lines still queued wait their turn."""
        with self._lock:
            speeches, self._speeches = self._speeches, []
        for speech in speeches:
            with contextlib.suppress(Exception):  # it had finished already
                speech.stop()

    def busy(self) -> bool:
        with self._lock:
            self._speeches = [s for s in self._speeches if getattr(s, "speaking", False)]
            talking = bool(self._speeches)
        return talking or self.holding or bool(self._quiet())

    def join(self) -> None:
        """Wait until every queued line has been started (tests)."""
        self._q.join()

    def _run(self) -> None:
        while True:
            text = self._q.get()
            try:
                while self.busy():
                    time.sleep(self._poll)
                with contextlib.suppress(Exception):  # a line that cannot be said is still shown and logged
                    self.hold(self._speak(text))
            finally:
                self._q.task_done()


# the control grammar: whole utterances only, so a task that merely starts with one of these words stays a task
_LEAD = r"^(?:(?:hey |ok |okay )?karta,? )?(?:please )?"
_TAIL = r"(?: please)?$"
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": -1}
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
STOP = re.compile(_LEAD + r"(?:stop|stop it|stop that|halt)" + _TAIL)
STOP_ALL = re.compile(_LEAD + r"(?:stop|cancel) (?:everything|all|all of (?:it|them)|all jobs)" + _TAIL)
PAUSE = re.compile(_LEAD + r"(?:pause|pause it|pause that|hold on)" + _TAIL)
RESUME = re.compile(_LEAD + r"(?:resume|go on|carry on|continue|keep going|unpause)" + _TAIL)
STATUS_ASK = re.compile(
    _LEAD + r"(?:what are you doing|what are you working on|what'?s running|what is running|status|what'?s going on)" + _TAIL
)
WATCH_LIST = re.compile(
    _LEAD
    + r"(?:what are you watching(?: for)?|what(?:'s| is| are) (?:scheduled|my schedules|my reminders|your watches)"
    + r"|what have you (?:got )?scheduled|list (?:your |my )?(?:watches|schedules|reminders))"
    + _TAIL
)
STOP_WATCH = re.compile(_LEAD + r"(?:stop|quit) watching(?: (?:for )?(?P<which>.+?))?" + _TAIL)
CANCEL = re.compile(_LEAD + r"(?:cancel|stop|drop|forget) (?P<which>.+?)" + _TAIL)
THAT = {"that", "it", "this", "that one", "this one", "the last one"}
_ORDINAL = re.compile(r"(?:the )?(?P<o>" + "|".join(ORDINALS) + r") (?:one|job|task)")
_NUMBER = re.compile(r"(?:job|task|number) (?:number )?(?P<n>\d+|" + "|".join(NUMBERS) + r")")
_FILLER = {"the", "a", "an", "one", "job", "task", "my", "about", "that", "this", "for"}


BROKE = "Something went wrong on my side, so I stopped. The details are in Karta's log."


def _broke(error: BaseException) -> str:
    """A job that raised: the traceback goes to the log, and the user hears one plain sentence, never Python's text."""
    from karta import console

    console.log("error", "".join(traceback.format_exception(error)).rstrip(), console.RED)
    return BROKE


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _normal(text: str) -> str:
    return " ".join(_words(text))


class Jobs:
    """The job table and its two lanes. `run_screen(job) -> (ok, said)` carries out a screen job on the screen
    worker; `speaker` says what jobs and control say; `emit(kind, **data)` shows the table (the HUD's pills)."""

    def __init__(
        self,
        run_screen: Callable[[Job], tuple[bool, str]],
        speaker: Speaker | None = None,
        emit=None,
        workers: int = BACKGROUND_WORKERS,
        watches=None,
    ):
        if emit is None:
            from karta.core.events import emit
        self._run_screen, self._speaker, self._emit = run_screen, speaker, emit
        self.watches = watches  # karta.watches.Watches: its listing and cancel join the control grammar
        self._lock = threading.RLock()
        self._jobs: list[Job] = []
        self._questions: list[_Question] = []
        self._screen_q: queue.Queue[Job] = queue.Queue()
        self.screen_thread = threading.Thread(target=self._screen_worker, daemon=True, name="karta-screen")
        self.screen_thread.start()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="karta-bg")

    # ------------------------------------------------------------ the table

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def live(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs if j.state in LIVE]

    def get(self, n: int) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs if j.n == n), None)

    def screen_job(self) -> Job | None:
        """The screen job under way (running, asking or paused), if any."""
        with self._lock:
            return next((j for j in self._jobs if j.lane == "screen" and j.state in LIVE and j.state != State.QUEUED), None)

    def _new(self, task: str, lane: str, kind: str, title: str = "") -> Job:
        with self._lock:
            job = Job(n=len(self._jobs) + 1, task=task, lane=lane, kind=kind, title=title, jobs=self)
            others = [j for j in self._jobs if j.state in LIVE]
            if others:
                job.shared = True
                for other in others:
                    other.shared = True
            self._jobs.append(job)
        return job

    def _set(self, job: Job, state: State) -> None:
        with self._lock:
            job.state = state
        self.show()

    def show(self) -> None:
        live = self.live()
        self._emit("jobs", text=pills(live) or "none", jobs=[{"n": j.n, "title": j.title, "state": j.state.value} for j in live])

    # ------------------------------------------------------------ new jobs

    def submit(self, task: str) -> tuple[Job, str]:
        """A new screen job: it runs now when the screen is free, else after the ones ahead of it (and says so)."""
        with self._lock:
            ahead = [j for j in self._jobs if j.lane == "screen" and j.state in LIVE]
            job = self._new(task, "screen", "task")
        self._screen_q.put(job)
        self.show()
        if not ahead:
            return job, ""
        if len(ahead) == 1:
            return job, "I'll do that after the current task."
        return job, f"I'll do that after the {len(ahead)} tasks ahead of it."

    def background(self, title: str, work: Callable[[Job], str], kind: str = "think") -> Job:
        """A job that never touches the screen: `work(job) -> said` runs in the pool and gets its Job only (it
        checks `job.cancel` itself)."""
        job = self._new(title, "background", kind, title=title)
        self.show()
        self._pool.submit(self._run_background, job, work)
        return job

    def _run_background(self, job: Job, work: Callable[[Job], str]) -> None:
        if job.cancel.is_set():
            return self._finish(job, State.CANCELLED, "")
        job.started = time.time()
        self._set(job, State.WATCHING if job.kind == "delegate" else State.RUNNING)
        try:
            said = work(job) or ""
        except Stopped:
            return self._finish(job, State.CANCELLED, "")
        except Exception as error:
            return self._finish(job, State.FAILED, _broke(error))
        self._finish(job, State.CANCELLED if job.cancel.is_set() else State.DONE, "" if job.cancel.is_set() else said)

    def _screen_worker(self) -> None:
        while True:
            job = self._screen_q.get()
            if job is None:
                return
            if job.cancel.is_set():  # cancelled while it waited its turn
                continue
            job.started = time.time()
            self._set(job, State.RUNNING)
            try:
                ok, said = self._run_screen(job)
            except Stopped:
                ok, said = False, ""
            except Exception as error:  # the worker stays up for the next job
                ok, said = False, _broke(error)
            if job.cancel.is_set():
                self._finish(job, State.CANCELLED, "")
            else:
                self._finish(job, State.DONE if ok else State.FAILED, said)

    def _finish(self, job: Job, state: State, said: str) -> None:
        with self._lock:
            if job.state not in LIVE:  # ended already (cancelled while it waited)
                return
            job.state, job.said, job.ended = state, said, time.time()
            self._questions = [q for q in self._questions if q.job is not job]
        self.show()
        if said and self._speaker is not None:
            self._speaker.say(self.line(job, said))

    def say(self, job: Job, text: str) -> None:
        """A line a live job says before it ends (a delegated session needs the user), through the speech queue."""
        if text and self._speaker is not None and job.state in LIVE:
            self._speaker.say(self.line(job, text))

    def line(self, job: Job, said: str) -> str:
        """What is said for a job: its number first when other jobs were live alongside it."""
        return f"Job {job.n}: {said}" if job.shared else said

    def guard(self, env, job: Job, fresh: Callable[[], object] | None = None) -> Guard:
        """`env` for `job`: the screen worker's only, stopping on cancel and holding on pause (PAUSED shown)."""

        def held(state: State) -> Callable[[], None]:
            return lambda: self._set(job, state)

        return Guard(env, job, self.screen_thread, fresh, on_pause=held(State.PAUSED), on_resume=held(State.RUNNING))

    # ------------------------------------------------------------ questions

    def ask(self, job: Job, question: str) -> str:
        """A job's question (its confirm or ask): blocks the job's thread until the next spoken input answers it,
        or the job is cancelled (""). The caller shows and logs the question."""
        q = _Question(job, question)
        with self._lock:
            self._questions.append(q)
            job.question = question
        self._set(job, State.ASKING)
        while not q.answered.wait(0.05):
            if job.cancel.is_set():
                break
        with self._lock:
            if q in self._questions:
                self._questions.remove(q)
            job.question = ""
            if job.state == State.ASKING:
                job.state = State.RUNNING
        self.show()
        return q.reply

    def asking(self) -> bool:
        with self._lock:
            return bool(self._questions)

    def answer(self, text: str) -> Job | None:
        """Give a spoken input to the job that has waited longest for an answer. The job, or None when none asks."""
        with self._lock:
            if not self._questions:
                return None
            q = self._questions.pop(0)
        q.reply = text
        q.answered.set()
        return q.job

    # ------------------------------------------------------------ control

    def cancel(self, job: Job) -> None:
        job.cancel.set()
        job.resume.set()  # a paused job wakes to stop
        if job.state == State.QUEUED:  # never started: it ends now, and its worker skips it
            self._finish(job, State.CANCELLED, "")

    def control(self, text: str) -> str | None:
        """The control grammar, by code: the reply to say, or None when `text` is not a control phrase."""
        said = _normal(text)
        if not said:
            return None
        if STATUS_ASK.fullmatch(said):
            more = self.watches.summary() if self.watches is not None else ""
            return f"{self.status()} {more}".strip()
        if self.watches is not None and WATCH_LIST.fullmatch(said):
            return self.watches.listing()
        found = STOP_WATCH.fullmatch(said)
        if self.watches is not None and found:
            return self.watches.cancel(found["which"] or "", "watch")
        if STOP_ALL.fullmatch(said):
            live = self.live()
            for job in live:
                self.cancel(job)
            return "Stopping everything." if live else "Nothing is running."
        if STOP.fullmatch(said):
            job = self.screen_job() or next(iter(self.live()), None)
            if job is None:
                return "Nothing is running."
            self.cancel(job)
            return f"Stopping job {job.n}."
        if PAUSE.fullmatch(said):
            job = self.screen_job()
            if job is None:
                return "Nothing to pause."
            job.resume.clear()
            return f"Pausing job {job.n}."
        if RESUME.fullmatch(said):
            paused = [j for j in self.live() if not j.resume.is_set()]
            for job in paused:
                job.resume.set()
            return ("Resuming job " + ", ".join(str(j.n) for j in paused) + ".") if paused else "Nothing is paused."
        found = CANCEL.fullmatch(said)
        if found:
            job = self._which(found["which"])
            if job is None and self.watches is not None:
                return self.watches.cancel(found["which"])  # "cancel the 9 o'clock brief"; None: a task
            if job is None:
                return None  # "cancel my meeting" with no such job: a task, not a control
            if job.state not in LIVE:
                return f"Job {job.n} has already ended."
            self.cancel(job)
            return f"Cancelling job {job.n}."
        return None

    def _which(self, words: str) -> Job | None:
        """The job a cancel names: that / it (the newest live one), an ordinal (its place among the live jobs, as
        "what are you doing" lists them), a number (job 2), or words of its title."""
        live = self.live()
        if words in THAT:
            return live[-1] if live else None
        found = _ORDINAL.fullmatch(words)
        if found:
            k = ORDINALS[found["o"]]
            if k == -1:
                return live[-1] if live else None
            return live[k - 1] if 0 < k <= len(live) else None
        found = _NUMBER.fullmatch(words)
        if found:
            n = found["n"]
            return self.get(int(n) if n.isdigit() else NUMBERS[n])
        want = {w for w in _words(words) if w not in _FILLER}
        if not want:
            return None
        hits = [j for j in live if want <= set(_words(j.title)) | set(_words(j.task))]
        return hits[0] if len(hits) == 1 else None

    def status(self) -> str:
        """'What are you doing?', read from the table by code."""
        live = self.live()
        if not live:
            return "Nothing right now."
        parts = []
        for job in live:
            if job.state == State.ASKING and job.question:
                parts.append(f"Job {job.n} {STATUS[job.state]}: {job.question}")
            else:
                parts.append(f"Job {job.n} {STATUS[job.state]}: {job.title}.")
        return " ".join(parts)

    def close(self, wait: float = 2.0) -> None:
        """Cancel everything live and let the workers go."""
        for job in self.live():
            self.cancel(job)
        self._screen_q.put(None)  # type: ignore[arg-type]
        self.screen_thread.join(wait)
        self._pool.shutdown(wait=False, cancel_futures=True)


def pills(jobs: list[Job]) -> str:
    """The HUD's row of job pills: "▶ 1 summarise this page · … 2 open my mail"."""
    return "  ·  ".join(f"{PILL.get(j.state, '·')} {j.n} {' '.join(j.title.split()[:3])}" for j in jobs)
