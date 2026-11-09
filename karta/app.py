"""`karta`: the Karta program. On start it maps this Mac, loads Laya and warms the brain; then it runs tasks.

    uv run karta                                   # start Karta, then type tasks at the prompt
    uv run karta "create a note called Karta test" # one task (dry run: plans, never clicks)
    uv run karta "..." --act --hud                 # does it, showing each step in karta-hud (started if not running)
    uv run karta --voice --act --hud               # hold Right Option, say the task, let go: it runs
    uv run karta "..." --act --say                 # typed, and how it ended is also said aloud

The Mac map (installed apps, browser profiles, folders, open tabs, remembered places) is built once at start; the
tabs and running apps are re-read before every task. The harness keeps the whole map; the brain gets only the few
lines a task needs. Stop a live task with Ctrl-C. Anything hard to undo asks first.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

MEMORY = Path.home() / "Library" / "Application Support" / "karta" / "harness-memory.sqlite"
SESSION = MEMORY.with_name("session.sqlite")  # recent tasks' text and titles, 14 days, never a value (harness/session.py)


def hud_running(port: int | None = None) -> bool:
    """A karta-hud is listening already: its UDP port on 127.0.0.1 is taken (binding it fails)."""
    import socket

    from karta.core.events import HUD_PORT

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", HUD_PORT if port is None else port))
    except OSError:
        return True
    finally:
        sock.close()
    return False


def start_hud(running=None, popen=None):
    """Launch karta-hud when none is running (`hud_running`), as a child that goes when this process goes
    (KARTA_PARENT_PID). Returns the process, or None when a HUD was running already. `running` and `popen` are for
    tests."""
    import os
    import subprocess
    import sys

    if (running or hud_running)():
        return None
    return (popen or subprocess.Popen)(
        [sys.executable, "-m", "karta.hud"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "KARTA_PARENT_PID": str(os.getpid())},
    )


class Shown:
    """Wraps the real env: every action is printed and sent to the HUD before it happens."""

    def __init__(self, env, say):
        self._env, self._say = env, say

    def __getattr__(self, name):
        return getattr(self._env, name)

    def _act(self, name, text, *args):
        self._say(text)
        return getattr(self._env, name)(*args)

    def click(self, item):
        return self._act("click", f"click {item.name!r}", item)

    def set_text(self, item, text):
        return self._act("set_text", f"type {text[:40]!r} into {item.name!r}", item, text)

    def type_at_focus(self, text):
        return self._act("type_at_focus", f"type {text[:40]!r}", text)

    def press(self, keys):
        return self._act("press", f"press {keys}", keys)

    def open_app(self, app):
        return self._act("open_app", f"open {app}", app)

    def open_url(self, url, browser, profile=None):
        return self._act("open_url", f"open {url} in {browser}" + (f" ({profile})" if profile else ""), url, browser, profile)

    def focus_tab(self, browser, window, tab):
        return self._act("focus_tab", f"switch to the open tab in {browser}", browser, window, tab)

    def scroll(self, direction):
        return self._act("scroll", f"scroll {direction}", direction)

    def back(self):
        return self._act("back", "go back")

    def wait(self, seconds):
        return self._act("wait", f"wait {seconds:g}s", seconds)

    def open_terminal(self, line):
        return self._act("open_terminal", f"open a new Terminal window: {line}", line)


def show_map(mac) -> None:
    """One line of counts: the map itself (apps, profiles, tabs, places) stays in the harness and off the screen."""
    from karta import console

    f = mac.facts
    profiles = sum(len(p) for p in f.browsers.values())
    hosts = {t.url.split("/")[2] if "://" in t.url else t.url for t in mac.tabs}
    console.log(
        "map",
        f"{len(f.installed_apps)} apps · {len(f.browsers)} browsers, {profiles} profiles · {len(mac.tabs)} tabs on "
        f"{len(hosts)} sites · {len(mac.signed_in)} places remembered · {len(f.folders)} folders",
        console.DIM,
    )


class Karta:
    """The running program: the map, Laya, the brain and memory, made once and kept for every task."""

    def __init__(self, hud: bool = False, record: str | None = None):
        from karta.base import macos

        self.record = record  # a folder: save each task's screens and actions there (karta/harness/snapshots.py)
        self.speech = None  # the answer being said aloud (karta.voice.speak's Speech), until hushed
        self.hud = None  # the karta-hud this process started (None: none, or one was running already)

        if not macos.accessibility_trusted():
            raise SystemExit("this terminal lacks Accessibility permission (System Settings > Privacy & Security)")
        from karta import console
        from karta.brain.claude_code import ClaudeCodeBrain
        from karta.core import events
        from karta.core.matcher import LayaMatcher
        from karta.harness import content
        from karta.harness import facts as facts_mod
        from karta.harness.brain import HarnessBrain
        from karta.harness.decide import Laya
        from karta.harness.memory import HarnessMemory, SessionNotes
        from karta.harness.session import Session

        self.events, self.facts_mod, self.console = events, facts_mod, console
        events.subscribe(console.on_event)  # every event becomes a live log line (and goes to the log file)
        if hud:
            self.hud = start_hud()  # the HUD is started when it is not running, as `karta start` does
            events.to_hud()
        t0 = time.perf_counter()
        self.memory = HarnessMemory(MEMORY, notes=SessionNotes())
        content.BOOK.store = self.memory  # a site's learned boilerplate (content.py) is kept with the rest
        self.session = Session(SESSION)  # what recent tasks produced, so "that" and "it" resolve (values in RAM only)
        self.mac = facts_mod.mac_map(self.memory)
        show_map(self.mac)
        console.log("ready", f"Mac mapped in {time.perf_counter() - t0:.2f}s", console.DIM)
        t1 = time.perf_counter()
        matcher = LayaMatcher()
        self.laya = Laya(matcher.agent, matcher.embed)
        self.brain = HarnessBrain(ClaudeCodeBrain())
        if self.brain.page or self.brain.auto:  # the page or look calls' warm spare boots now: no cold start later
            from karta.harness.brain import LOOK_SYS, PAGE_SYS

            self.brain.brain.warm(PAGE_SYS if self.brain.page else LOOK_SYS, self.brain.page_model)
        console.log("ready", f"Laya loaded in {time.perf_counter() - t1:.1f}s · brain: Claude Code", console.DIM)

    def close(self) -> None:
        """Stop the HUD this process started (one that was running already is left alone)."""
        hud, self.hud = self.hud, None
        if hud is not None:
            hud.terminate()

    def hush(self) -> None:
        """Stop the answer being said aloud, if one is: a new press of the talk key, or a new task."""
        speech, self.speech = self.speech, None
        if speech is not None:
            import contextlib

            with contextlib.suppress(Exception):  # it had finished already
                speech.stop()

    def run(
        self, goal: str, act: bool, budget: float, confirm=None, ask=None, trace: bool = False, aloud: bool = False, job=None
    ):
        """One task, recorded in the session (typed or a job alike). `job` (voice mode): the jobs.Job it runs as, on
        the screen worker: its env is guarded (cancel and pause, `Jobs.guard`), its budget clock stops while paused, and
        a stop ends it at the next step boundary (`Job.stopping`: GAVE_UP_STOPPED; the guard's `Stopped` is the
        backstop, ending it as Ctrl-C does). Returns the TaskResult (None for a dry run or a backstop stop)."""
        from karta.harness.brain import brain_facts
        from karta.harness.loop import run_task
        from karta.harness.mac import MacHarnessEnv

        events = self.events
        if job is None:  # in voice mode the speech queue keeps the last answer going while the next job starts
            self.hush()
        self.mac = self.facts_mod.refresh_tabs(self.mac)  # tabs and running apps change between tasks
        events.emit("heard", text=goal)
        env = MacHarnessEnv()
        console = self.console
        slice_ = brain_facts(goal, self.mac.facts, self.mac.tabs)
        console.write("what the brain gets of the map:\n  " + slice_.replace("\n", "\n  "))
        if not act:
            print("\nWHAT THE BRAIN GETS OF THE MAP:\n  " + slice_.replace("\n", "\n  "))
            screen = env.screen()
            print(f"\nSCREEN NOW: {screen.app} · {len(screen.items)} items ({len(screen.page())} on the page)")
            events.emit("planning")
            t = time.perf_counter()
            if self.brain.page:  # the first page call, on the screen now
                from karta.harness.view import build

                stream = self.brain.page_plan(goal, slice_, build(screen, goal))
                list(stream)
                plan = stream.plan
            else:
                plan = self.brain.plan(goal, slice_, screen)
            print(f"\nPLAN ({time.perf_counter() - t:.1f}s, 1 brain call):")
            for step in plan.steps:
                print(f"  {step}")
            if plan.done_shows:
                print(f"  done when the screen shows: {plan.done_shows!r}")
            print("\n(dry run: nothing was clicked or typed; add --act to run it)")
            events.emit("done", ok=True, reason="dry run")
            return None

        def say(text: str) -> None:
            events.emit("step", text=text)  # the terminal log and the HUD both show it

        def typed_yes(question: str) -> bool:
            return input(f"\nKARTA ASKS: {question} [y/N] ").strip().lower() in {"y", "yes"}

        def typed_reply(question: str) -> str:
            return input(f"\nKARTA ASKS: {question}\n> ").strip()

        confirm, ask = confirm or typed_yes, ask or typed_reply

        console.log("task", f"{goal}  {console.DIM}(Ctrl-C stops it){console.RESET}", console.BOLD)
        events.emit("planning")
        shown = Shown(env, say)
        if job is not None:
            shown = job.jobs.guard(shown, job, fresh=lambda: env.wait(0))  # wait(0): the next read is a new one
        parts = {"laya": self.laya, "brain": self.brain, "facts": self.mac, "memory": self.memory, "confirm": confirm, "ask": ask}
        if self.record:
            from karta.harness.snapshots import Recorder

            shown = recorder = Recorder(shown, goal, self.record)
            parts = recorder.wrap(**parts)  # the brain's replies, Laya's answers, memory and the user's answers too
        from karta.jobs import Stopped

        try:
            by_job = {"clock": job.clock, "stop": job.stopping} if job is not None else {}
            result = run_task(goal, shown, budget_s=budget, session=self.session, **by_job, **parts)
        except (KeyboardInterrupt, Stopped):
            if self.record:
                recorder.finish(None, reason="stopped by you")
            events.emit("done", ok=False, reason="stopped by you")
            return None
        if self.record:
            saved = recorder.finish(result)
            if saved:
                console.log("saved", str(saved), console.DIM)
        self.speech = end_of_task(result, events, aloud)  # still talking: the next press of the talk key stops it
        if result.delegated and job is None:
            console.log("says", "Karta hears back from Claude Code in voice mode only (karta start).", console.DIM)
        console.write("TRACE:\n" + "\n".join(f"  {line}" for line in result.trace))  # the full trace is in the log file
        if trace:
            print("\nTRACE:\n" + "\n".join(f"  {line}" for line in result.trace))
        t = result.timings
        summary = [
            f"RESULT: done={result.done} · {result.reason}",
            f"  brain calls {result.brain_calls} · Laya decisions {result.laya_decisions} · actions {result.actions} · rescues {result.rescues} · local repairs {result.repairs}",
            f"  time {result.seconds:.1f}s (brain {t.get('brain_s', 0):.1f}s, Laya {t.get('laya_s', 0):.2f}s, screen and waiting {t.get('env_s', 0):.1f}s)",
        ]
        if "reads" in t:
            summary.append(
                f"  screen: {t['reads']:.0f} full reads {t['read_s']:.1f}s · waiting for changes {t['wait_s']:.1f}s · acting {t['act_s']:.1f}s"
            )
        print("\n" + "\n".join(summary), flush=True)
        console.write("\n".join(summary))
        return result


def say_aloud(text: str):
    """karta.voice.speak.speak (Tara): starts saying `text`, returns its Speech (.stop())."""
    from karta.voice.speak import speak

    return speak(text)


def end_of_task(result, events, aloud: bool, speak=None):
    """How a run ended, for the user: the HUD and the log get the done event (with the line said), the terminal
    prints the line, and `aloud` says it (voice mode, or --say). A question's answer was said by its tell step
    already. Returns the Speech, so the next press of the talk key can stop it (Karta.hush)."""
    said = (result.said or "").strip()
    events.emit(
        "done",
        ok=result.done,
        reason=result.reason,
        **({"answer": result.answer} if result.answer else {}),
        **({"said": said} if said else {}),
    )
    if not said:
        return result.speech
    print(f"\nKARTA: {said}", flush=True)
    if not aloud:
        return result.speech
    try:
        return (speak or say_aloud)(said)
    except Exception:  # shown and printed all the same
        return result.speech


MIN_GOAL_CHARS = 4


def clean_goal(text: str) -> str | None:
    """A heard task, trimmed; None when too short to be one (a cough, a stray word)."""
    goal = text.strip().rstrip(".!?").strip()
    return goal if len(goal) >= MIN_GOAL_CHARS else None


YES = re.compile(r"\b(yes|yeah|yep|yup|haan|han|ha|go|go ahead|do it|okay|ok|sure|run it|send it)\b", re.I)
NO = re.compile(r"\b(no|nope|nahi|na|stop|cancel|don'?t|wait)\b", re.I)


def said_yes(text: str) -> bool:
    """A spoken yes: a yes word and no no-word ("no, don't" and "yes... no wait" are both no)."""
    return bool(YES.search(text)) and not NO.search(text)


def take(jobs, text: str, events, watches=None) -> str:
    """One spoken input in voice mode, in order: the answer to a job's question; else a control phrase (code, no
    brain call); else a schedule (code) or a watch (one setup call, in the background) when `watches` hears one;
    else a new screen job, run now when the screen is free, else queued. The reply to say ("")."""
    if jobs.answer(text) is not None:
        return ""
    reply = jobs.control(text)
    if reply is not None:
        return reply
    goal = clean_goal(text)
    if goal is None:
        events.emit("done", ok=False, reason="didn't catch that")
        return ""
    if watches is not None:
        heard = watches.hear(goal, background=lambda title, work: jobs.background(title, work, kind="think"))
        if heard is not None:
            return heard
    return jobs.submit(goal)[1]


def voice_loop(karta: Karta, act: bool, budget: float) -> None:
    """Push to talk: Karta waits in the background, never listening, until Right Option is held. Say the task while
    holding it; on release it becomes a job (karta/jobs.py) and Karta keeps listening while it runs: a task said
    meanwhile is queued after it, a job's question takes the next thing said, and stop / pause / resume / cancel
    <which> / what are you doing are handled by code. Anything risky mid-task (a send, a payment) asks by voice, and
    only a clear yes counts. The HUD shows Listening with live bars while the key is held and a pill per live job.
    Speech to text is local; everything said goes through one speech queue (jobs.Speaker)."""
    from karta.jobs import Jobs, Speaker
    from karta.voice.hotkey import HoldToTalk
    from karta.voice.stt import WhisperServer

    events = karta.events
    server = WhisperServer()
    server.start()
    key = HoldToTalk()

    console = karta.console
    speaker = Speaker(say_aloud, quiet=lambda: jobs.asking())

    def listening() -> None:
        speaker.holding = True  # nothing is said while the key is held
        speaker.hush()  # a new press of the talk key stops what is being said
        karta.hush()
        events.emit("listening")

    def hear(prompt: str) -> str:
        console.log("idle", f"hold Right Option and {prompt}", console.DIM)
        while True:
            try:
                audio = key.record(on_start=listening, on_level=lambda v: events.emit("level", v=round(v, 3)))
            finally:
                speaker.holding = False
            if audio is not None:
                break
            events.emit("idle")  # a slip of the key: the HUD goes away again
        events.emit("transcribing")
        t = time.perf_counter()
        text = server.transcribe(audio).strip()
        console.log("speech", f"{text!r}  {console.DIM}({time.perf_counter() - t:.1f}s){console.RESET}", console.BLUE)
        return text

    def asks(job, question: str, how: str) -> str:
        who = f"Job {job.n} asks: " if job.shared else ""
        console.log("asks", f"{who}{question} (hold Right Option, {how})", console.AMBER)
        events.emit("heard", text=f"{who}{question} Hold Right Option: {how}.")
        reply = jobs.ask(job, question)  # the next thing said answers it (`take`)
        console.log("answer", reply or "(none)", console.AMBER)
        return reply

    def run_job(job) -> tuple[bool, str]:
        result = karta.run(
            job.task,
            act,
            budget,
            confirm=lambda q: said_yes(asks(job, q, "yes or no")),
            ask=lambda q: asks(job, q, "answer"),
            aloud=False,  # the speech queue says how it ended
            job=job,
        )
        if result is None:  # a dry run, or stopped (the job's cancel says which)
            return True, ""
        speaker.hold(result.speech)  # a tell's answer still being said: the next line waits for it
        for launched in result.delegated:  # a Claude Code session it started: heard back from in the background
            jobs.background(f"Claude Code in {launched.name}", watcher(launched), kind="delegate")
        return result.done, result.said

    def watcher(launched):
        from karta.harness import delegate

        keep = delegate.keeper(karta.session)
        return lambda job: delegate.watch(launched, job, lambda line: jobs.say(job, line), keep)

    watches = make_watches(karta, speaker.say, lambda task: jobs.submit(task)[1])
    jobs = Jobs(run_job, speaker, emit=events.emit, watches=watches)
    for line in watches.arm():  # watches and schedules from the last `karta start`; a missed schedule is said
        console.log("says", line, console.BLUE)
        speaker.say(line)
    watches.start()
    console.log("idle", "Karta is idle. Hold Right Option and say a task; let go and it runs. Ctrl-C quits.", console.DIM)
    try:
        while True:
            reply = take(jobs, hear("say a task"), events, watches)
            if reply:
                console.log("says", reply, console.BLUE)
                speaker.say(reply)
    except KeyboardInterrupt:
        print()
    finally:
        watches.close()
        jobs.close()
        server.stop()


def make_watches(karta: Karta, say, submit):
    """Voice mode's watches and schedules (karta/watches.py): kept in jobs.sqlite, read by code while `karta start`
    runs; the brain is reached only through their own small calls (a HarnessBrain of their own, so a task's count
    of calls stays its own)."""
    from karta import watches as w
    from karta.harness.brain import HarnessBrain

    plan, judge = w.brain_calls(HarnessBrain(karta.brain.brain))

    def tab_titles(host: str) -> list[str]:
        from karta.core.mac_places import running_apps

        running = set(running_apps())
        browsers = [b for b in karta.mac.facts.browsers if b in running]
        return [t.title for t in karta.facts_mod.open_tabs(browsers, {host})]

    def keep(task: str, title: str, source: str, value: str) -> None:
        karta.session.keep(task, "watch", title, source, value)

    return w.Watches(
        w.Store(),
        say=say,
        submit=submit,
        probes=w.Probes(tabs=tab_titles),
        plan=plan,
        judge=judge,
        keep=keep,
        consent=w.kept_consent(MEMORY),
    )


def start() -> None:
    """`karta start`: the HUD and push-to-talk together, acting for real; Ctrl-C stops both."""
    from karta.setup_mac import setup_state

    state = setup_state()
    if state is None or state.get("todo"):
        print(
            "Karta isn't set up yet" + (f" ({len(state['todo'])} thing(s) left)" if state else "") + ": run `karta setup` first."
        )
        raise SystemExit(1)
    import signal

    def stop(*_):
        raise KeyboardInterrupt  # a terminate is a Ctrl-C: the voice loop and the HUD shut down cleanly

    signal.signal(signal.SIGTERM, stop)
    karta = None
    try:
        karta = Karta(hud=True)  # starts the HUD unless one is running
        from karta.harness.loop import BUDGET_S

        voice_loop(karta, act=True, budget=BUDGET_S)
    finally:
        if karta is not None:
            karta.close()


COMMANDS = {"setup": "check and install what Karta needs (run once)", "start": "the HUD and push-to-talk, ready to act"}


def main() -> None:
    import sys

    from karta import console

    if len(sys.argv) > 1 and sys.argv[1] in COMMANDS:
        console.banner(sys.argv[1])
        if sys.argv[1] == "setup":
            from karta.setup_mac import run_setup

            raise SystemExit(run_setup())
        start()
        return
    parser = argparse.ArgumentParser(
        prog="karta",
        description="Karta: say it, and it's done on this Mac.",
        epilog="commands: " + "; ".join(f"karta {c}: {h}" for c, h in COMMANDS.items()),
    )
    parser.add_argument("goal", nargs="?", help="one task; without it Karta waits for tasks at a prompt")
    parser.add_argument("--act", action="store_true", help="actually click and type (default: dry run)")
    parser.add_argument("--hud", action="store_true", help="show each step in karta-hud")
    from karta.harness.loop import BUDGET_S

    parser.add_argument("--budget", type=float, default=BUDGET_S, help="seconds per task, brain time included")
    parser.add_argument("--say", action="store_true", help="also say aloud how each typed task ended (voice mode always does)")
    parser.add_argument("--voice", action="store_true", help="push to talk: hold Right Option, say the task, let go and it runs")
    parser.add_argument("--trace", action="store_true", help="also print each task's full trace (it is always in the log file)")
    parser.add_argument(
        "--record",
        metavar="DIR",
        nargs="?",
        const="~/Library/Application Support/karta/snapshots",
        help="save each task's screens and actions as JSON in DIR (local only; default ~/Library/Application Support/karta/snapshots)",
    )
    args = parser.parse_args()
    console.banner("push-to-talk" if args.voice else "acting" if args.act else "dry run")
    karta = Karta(hud=args.hud, record=args.record)
    if args.voice:
        voice_loop(karta, args.act, args.budget)
        return
    if args.goal:
        karta.run(args.goal, args.act, args.budget, trace=args.trace, aloud=args.say)
        return
    print("\nKarta is ready. Type a task (prefix with ! to run it for real), or 'quit'.")
    while True:
        try:
            line = input("karta> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if line in {"quit", "exit"}:
            return
        if line:
            act = line.startswith("!")
            karta.run(line.lstrip("! "), act or args.act, args.budget, trace=args.trace, aloud=args.say)


if __name__ == "__main__":
    main()
