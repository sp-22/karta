"""Watches and schedules (karta/watches.py, karta/when.py): each probe on fakes, the conditions, the cost cap, backoff,
expiry, the recurrence grammar, re-arming after a restart and missed runs, voice control, and a scheduled task's key
action still asking. No osascript, network or Mac: every reader is a fake."""

from __future__ import annotations

import os
import stat
from datetime import datetime

import pytest
from test_harness_loop import PLAN, TASK, chat_env
from test_harness_tools import FACTS, FakeBrain, FakeLaya
from test_jobs import make, until

from karta import app, when
from karta import watches as w
from karta.harness.loop import run_task
from karta.jobs import State

WED_10 = datetime(2026, 11, 4, 10, 0).timestamp()  # a Wednesday


class Clock:
    def __init__(self, t: float = WED_10):
        self.t = t

    def __call__(self) -> float:
        return self.t


class Term:
    """A fake Terminal window: busy and contents, or gone."""

    def __init__(self, busy=True, text="$ make build\ncompiling…"):
        self.busy, self.text, self.gone = busy, text, False

    def __call__(self, window: int):
        return None if self.gone else (self.busy, self.text)


class Judge:
    def __init__(self, reply="Your build passed."):
        self.reply, self.asked = reply, []

    def __call__(self, question, what, text):
        self.asked.append((question, what, text))
        return self.reply


def rig(line: str, *, probes=None, judge=None, consent=None, store=None, clock=None, submit=None):
    said, kept = [], []
    clock = clock or Clock()
    watches = w.Watches(
        store or w.Store(None),
        say=said.append,
        submit=submit,
        probes=probes or w.Probes(),
        plan=lambda request: line,
        judge=judge,
        keep=lambda *a: kept.append(a),
        consent=consent,
        clock=clock,
        detect=None,
    )
    return watches, said, kept, clock


def run_for(watches, clock, seconds: float, step: float = 15.0) -> None:
    end = clock.t + seconds
    while clock.t < end:
        clock.t += step
        watches.tick()


# ---------------------------------------------------------------- the watch line


def test_the_watch_line_is_checked_by_code():
    spec = w.parse_watch("watch terminal | on: front | until: idle | ask: did the build pass or fail?")
    assert (spec.probe, spec.on, spec.until, spec.ask, spec.repeat, spec.hours) == (
        "terminal", "front", "idle", "did the build pass or fail?", False, None)  # fmt: skip
    spec = w.parse_watch("watch mail | on: Riya | until: new(invoice) | repeat: yes | for: 2h")
    assert (spec.arg, spec.repeat, spec.hours) == ("invoice", True, 2.0)
    assert w.parse_watch("watch file | on: ~/Downloads | until: appears(*.pdf) | for: none").hours == 0
    assert w.parse_watch("watch page | on: https://example.com | until: contains(sold out) | then: run open it").then == "open it"
    for bad in ("watch screen | on: front | until: changed", "watch mail | on: any | until: idle",
                "watch page | on: x | until: contains", "open the app", "stop: I can't read Slack"):  # fmt: skip
        with pytest.raises(w.Refused):
            w.parse_watch(bad)
    assert w.parse_watch("watch calendar | on: 10 | until: soon | ask: is it important").ask == ""  # code says it


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "https://10.0.0.4/x", "http://192.168.1.1", "file:///etc/hosts", "https://a:b@example.com",
            "http://intranet/wiki", "https://printer.local", "http://[::1]/", "ftp://example.com"],
)  # fmt: skip
def test_only_public_pages_are_fetched(url):
    with pytest.raises(w.Refused):
        w.public_url(url)


def test_a_public_page_is_fetched_without_cookies_and_a_redirect_inside_is_refused():
    assert w.public_url("example.com/status") == "https://example.com/status"
    handler = w._Redirect()
    with pytest.raises(w.Refused):
        handler.redirect_request(None, None, 302, "", {}, "http://127.0.0.1/admin")
    title, text = w.page_text("<html><title>Tickets &amp; more</title><script>x()</script><p>Sold <b>out</b></p></html>")
    assert title == "Tickets & more" and text == "Tickets & more Sold out"


def test_the_readers_parse_applescript_replies():
    scripts = []

    def osa(reply):
        return lambda script: scripts.append(script) or reply

    assert w.terminal_read(7, osa("true\nline 1\nline 2")) == (True, "line 1\nline 2")
    assert w.terminal_read(7, osa("gone")) is None
    assert w.terminal_front(osa("42"), own="/dev/ttys001") == 42 and "/dev/ttys001" in scripts[-1]
    assert w.mail_read(osa("off")) is None
    assert w.mail_read(osa("101\tRiya Shah <riya@example.com>\tQ3 numbers\n")) == [
        ("101", "Riya Shah <riya@example.com>", "Q3 numbers")
    ]
    assert all('" is running' in s or '" is not running' in s for s in scripts)  # Terminal and Mail are never started by a read


# ---------------------------------------------------------------- each probe


def test_terminal_idle_fires_once_the_window_stops_and_the_brain_reads_the_last_lines():
    term, judge = Term(busy=True), Judge()
    watches, said, kept, clock = rig(
        "watch terminal | on: front | until: idle | ask: did the build pass or fail?",
        probes=w.Probes(terminal_front=lambda: 7, terminal=term), judge=judge)  # fmt: skip
    assert watches.hear("tell me when my build passes") == (
        "OK, I'm watching that Terminal window for 8 hours: I'll tell you when your build passes.")  # fmt: skip
    entry = watches.listed()[0]
    assert entry.spec["on"] == "7"  # "front" bound to the window in front now
    run_for(watches, clock, 30)
    assert said == [] and judge.asked == []
    term.busy, term.text = False, "$ make build\nall 212 tests passed\nuser@mac ~ %"
    run_for(watches, clock, 60)  # backed off to a minute meanwhile
    assert said == ["Your build passed."]
    question, what, text = judge.asked[0]
    assert question == "did the build pass or fail?" and "all 212 tests passed" in text
    assert what == "The Terminal window went quiet: all 212 tests passed"  # code's own line, a prompt skipped
    assert kept[0][1] == "Your build passed." and kept[0][2] == "Terminal"  # a session ref
    assert watches.listed() == []  # once: it ended


def test_terminal_contains_and_a_closed_window():
    term = Term(busy=True, text="starting")
    watches, said, _, clock = rig("watch terminal | on: front | until: contains(listening on) | repeat: yes",
                                  probes=w.Probes(terminal_front=lambda: 3, terminal=term))  # fmt: skip
    watches.hear("tell me when the server is up")
    term.text = "starting\nlistening on :8080"
    run_for(watches, clock, 15)
    assert said == ['The Terminal window shows "listening on".']
    term.gone = True
    run_for(watches, clock, 60)
    assert said[-1] == "The Terminal window I was watching is gone." and watches.listed() == []


def test_no_terminal_window_is_refused():
    watches, *_ = rig("watch terminal | on: front | until: idle", probes=w.Probes(terminal_front=lambda: 0))
    assert watches.hear("tell me when the build is done") == "I can't watch that: I don't see a Terminal window."


def test_a_new_file_in_a_folder(tmp_path):
    (tmp_path / "old.pdf").write_text("x")
    watches, said, _, clock = rig(f"watch file | on: {tmp_path} | until: appears(*.pdf)")
    watches.hear("tell me when a pdf lands in that folder")
    (tmp_path / "notes.txt").write_text("x")
    run_for(watches, clock, 15)
    assert said == []  # not a pdf
    (tmp_path / "invoice.pdf").write_text("x")
    run_for(watches, clock, 60)
    assert said == [f"New in {tmp_path.name}: invoice.pdf."]


def test_a_file_that_appears_and_one_that_changes(tmp_path):
    target = tmp_path / "report.csv"
    watches, said, _, clock = rig(f"watch file | on: {target} | until: appears")
    watches.hear("tell me when the report is written")
    run_for(watches, clock, 15)
    target.write_text("a,b")
    run_for(watches, clock, 60)
    assert said == ["report.csv is there now."]
    watches, said, _, clock = rig(f"watch file | on: {target} | until: changed")
    watches.hear("tell me when the report changes")
    os.utime(target, (1, 1))
    run_for(watches, clock, 15)
    assert said == ["report.csv changed."]


class Mail:
    def __init__(self):
        self.rows = [("1", "Boss <b@example.com>", "Hello"), ("2", "Riya Shah <riya@example.com>", "Old thread")]

    def __call__(self):
        return list(self.rows)


def test_new_mail_from_a_sender_is_said_by_code_with_no_brain_call():
    mail, judge = Mail(), Judge()
    watches, said, kept, clock = rig("watch mail | on: Riya | until: new", probes=w.Probes(mail=mail), judge=judge)
    watches.hear("tell me when Riya replies")
    mail.rows.insert(0, ("3", "Someone <s@example.com>", "Lunch"))
    run_for(watches, clock, 15)
    assert said == []  # another sender
    mail.rows.insert(0, ("4", "Riya Shah <riya@example.com>", "Q3 numbers"))
    run_for(watches, clock, 60)
    assert said == ["New mail from Riya Shah: Q3 numbers."] and judge.asked == []
    assert kept[0][2] == "Mail"


def test_mail_text_goes_to_the_brain_only_with_the_users_yes():
    for consent, calls in ((lambda app: None, 0), (lambda app: False, 0), (lambda app: app == "Mail", 1)):
        mail, judge = Mail(), Judge("Riya says the numbers are late.")
        watches, said, _, clock = rig("watch mail | on: Riya | until: new | ask: is it urgent?",
                                      probes=w.Probes(mail=mail), judge=judge, consent=consent)  # fmt: skip
        watches.hear("tell me when Riya replies and whether it's urgent")
        mail.rows.insert(0, ("9", "Riya Shah <riya@example.com>", "Q3 numbers"))
        run_for(watches, clock, 15)
        assert len(judge.asked) == calls
        assert said == (["Riya says the numbers are late."] if calls else ["New mail from Riya Shah: Q3 numbers."])


def test_an_event_starting_soon_is_said_once():
    clock = Clock(datetime(2026, 11, 4, 9, 40).timestamp())
    events = lambda day: ["all day: Holiday", "09:55 Standup", "14:00 Review"]  # noqa: E731
    watches, said, _, clock = rig("watch calendar | on: 10 | until: soon | repeat: yes",
                                  probes=w.Probes(events=events), clock=clock)  # fmt: skip
    watches.hear("tell me before each meeting")
    run_for(watches, clock, 300)
    assert said == []
    run_for(watches, clock, 600)
    assert said == ["Standup starts in 9 minutes."]  # the 9:46 check: backed off to 5 minutes before it
    run_for(watches, clock, 600)
    assert len(said) == 1  # once per event


def test_a_public_page_that_changes_and_one_that_shows_a_text():
    page = {"text": "Tickets: sold out"}
    probes = w.Probes(fetch=lambda url: ("Gig", page["text"]))
    watches, said, _, clock = rig("watch page | on: https://example.com/gig | until: contains(on sale)", probes=probes)
    watches.hear("tell me when tickets are on sale on example.com/gig")
    run_for(watches, clock, 60)
    page["text"] = "Tickets: on sale now"
    run_for(watches, clock, 300)
    assert said == ['Example.com now shows "on sale".']
    watches, said, _, clock = rig("watch page | on: https://example.com/gig | until: changed", probes=probes)
    watches.hear("tell me when the gig page changes")
    page["text"] = "Tickets: gone"
    run_for(watches, clock, 15)
    assert said == ["example.com changed."]


def test_an_open_tabs_title():
    titles = {"t": ["Build #41 running"]}
    probes = w.Probes(tabs=lambda host: titles["t"] if host == "ci.example.com" else [])
    watches, said, _, clock = rig("watch tab | on: ci.example.com | until: changed", probes=probes)
    watches.hear("tell me when the ci tab changes")
    titles["t"] = ["Build #41 passed"]
    run_for(watches, clock, 15)
    assert said == ["The ci.example.com tab now reads: Build #41 passed."]


# ---------------------------------------------------------------- cost, backoff, expiry


def test_the_brain_reads_at_most_three_changes_per_watch_then_code_words():
    term, judge = Term(busy=False, text="a"), Judge("It changed.")
    watches, said, _, clock = rig("watch terminal | on: front | until: changed | ask: what happened? | repeat: yes",
                                  probes=w.Probes(terminal_front=lambda: 1, terminal=term), judge=judge)  # fmt: skip
    watches.hear("keep watching the terminal")
    for n in range(5):
        term.text = f"line {n}"
        run_for(watches, clock, 15)
    assert len(judge.asked) == w.MAX_WATCH_CALLS == 3
    assert said[:3] == ["It changed."] * 3 and said[3:] == [
        "The Terminal window changed: line 3",
        "The Terminal window changed: line 4",
    ]


def test_twenty_brain_calls_a_day_across_all_watches_and_the_count_survives_a_restart(tmp_path):
    store = w.Store(tmp_path / "jobs.sqlite")
    judge, terms = Judge("Read."), []
    clock = Clock()
    watches = None
    for _ in range(8):
        term = Term(busy=False, text="a")
        terms.append(term)
        watches, _said, _, clock = rig("watch terminal | on: front | until: changed | ask: what? | repeat: yes",
                                      probes=w.Probes(terminal_front=lambda: 1, terminal=term), judge=judge,
                                      store=store, clock=clock)  # fmt: skip
        watches.hear("keep watching the terminal")
        for n in range(3):
            term.text = f"x{n}"
            run_for(watches, clock, 15)
    assert len(judge.asked) == w.WATCH_CALLS_DAY == 20
    day = datetime.fromtimestamp(clock.t).date().isoformat()
    assert w.Store(tmp_path / "jobs.sqlite").calls_on(day) == 20


def test_an_unchanged_probe_backs_off_and_a_change_resets_it():
    term = Term(busy=True, text="same")
    watches, _, _, clock = rig("watch terminal | on: front | until: idle",
                               probes=w.Probes(terminal_front=lambda: 1, terminal=term))  # fmt: skip
    watches.hear("tell me when it finishes")
    entry = watches.listed()[0]
    gaps = []
    for _ in range(5):
        clock.t = entry.next_at
        watches.tick()
        gaps.append(entry.next_at - clock.t)
    assert gaps == [60.0, 300.0, 300.0, 300.0, 300.0]  # 15 s to the first check, then 1 min, then 5 min
    term.text = "more output"
    clock.t = entry.next_at
    watches.tick()
    assert entry.next_at - clock.t == 15.0


def test_a_watch_expires_after_eight_hours_unless_it_says_otherwise():
    term = Term(busy=True)
    watches, said, _, clock = rig("watch terminal | on: front | until: idle",
                                  probes=w.Probes(terminal_front=lambda: 1, terminal=term))  # fmt: skip
    watches.hear("tell me when my build passes")
    clock.t += 8 * 3600 + 1
    watches.tick()
    assert said == ["I stopped watching for your build passes: 8 hours passed."] and watches.listed() == []
    watches, said, _, clock = rig("watch terminal | on: front | until: idle | for: none",
                                  probes=w.Probes(terminal_front=lambda: 1, terminal=term))  # fmt: skip
    assert "until you stop me" in watches.hear("keep watching the build until I say stop")
    clock.t += 48 * 3600
    watches.tick()
    assert said == [] and len(watches.listed()) == 1


# ---------------------------------------------------------------- schedules: the time phrase, by code


@pytest.mark.parametrize(
    "text, words, task, remind",
    [
        ("every weekday at 9, give me a brief", "every weekday at 9 AM", "give me a brief", False),
        ("Every weekday at 9 am give me a brief.", "every weekday at 9 AM", "give me a brief", False),
        ("on weekdays at 9 give me a brief", "every weekday at 9 AM", "give me a brief", False),
        ("every day at noon open the news", "every day at noon", "open the news", False),
        ("every morning give me a brief", "every day at 9 AM", "give me a brief", False),
        ("every monday at 8:30 open the standup notes", "every Monday at 8:30 AM", "open the standup notes", False),
        (
            "every monday and thursday at 6:30 pm summarise my inbox",
            "every Monday and Thursday at 6:30 PM",
            "summarise my inbox",
            False,
        ),
        ("mondays and fridays at 8, open the gym app", "every Monday and Friday at 8 AM", "open the gym app", False),
        ("every weekend at 10 am check the weather", "every weekend day at 10 AM", "check the weather", False),
        (
            "every friday at 5 pm, email the team the weekly notes",
            "every Friday at 5 PM",
            "email the team the weekly notes",
            False,
        ),
        ("hey karta, every sunday at 7 in the evening plan my week", "every Sunday at 7 PM", "plan my week", False),
        ("summarise my inbox every day at 6", "every day at 6 PM", "summarise my inbox", False),
        ("check my mail every 30 minutes", "every 30 minutes", "check my mail", False),
        ("every hour, check the build", "every hour", "check the build", False),
        ("every 2 hours check the deploy page", "every 2 hours", "check the deploy page", False),
        ("at 6 pm today remind me to call my mom", "today at 6 PM", "call your mom", True),
        ("remind me at 6 pm to call my mom", "today at 6 PM", "call your mom", True),
        ("remind me tomorrow at 8 to take the bins out", "tomorrow at 8 AM", "take the bins out", True),
        ("tonight at 9 remind me to read", "today at 9 PM", "read", True),
        ("in 20 minutes, check my mail", "today at 10:20 AM", "check my mail", False),
        ("remind me in an hour to stretch", "today at 11 AM", "stretch", True),
        ("tomorrow at 9, give me a brief", "tomorrow at 9 AM", "give me a brief", False),
        ("remind me on friday at 3 pm to pay rent", "on Friday at 3 PM", "pay rent", True),
    ],
)
def test_recurrence_and_time_phrases(text, words, task, remind):
    got = when.parse(text, WED_10)
    assert got is not None and not got.why, got
    assert (got.when.words(WED_10), got.task, got.remind) == (words, task, remind)


@pytest.mark.parametrize(
    "text",
    ["make an event tomorrow at 3 pm", "delete every monday meeting", "open the 9:30 deck", "remind me to call mom",
     "send the report to Riya at 5 pm", "what's on my calendar tomorrow", "open every tab about flights"],
)  # fmt: skip
def test_a_task_that_names_a_time_stays_a_task(text):
    assert when.parse(text, WED_10) is None


def test_a_schedule_with_no_time_too_often_or_past_is_said_back():
    assert "At what time" in when.parse("every weekday give me a brief", WED_10).why
    assert when.parse("every 5 minutes check mail", WED_10).why == "I run a schedule at most every 15 minutes."
    assert when.parse("at 8 am today remind me to stretch", WED_10).why == "today at 8 AM has passed."
    late = when.parse("remind me at 9 to stretch", WED_10)  # 9 has passed today: tomorrow's
    assert late.when.words(WED_10) == "tomorrow at 9 AM"


def test_the_platform_date_detector_reads_a_date_code_does_not():
    found = when.parse("at 3 pm remind me to renew the passport", WED_10, detect=lambda text: datetime(2026, 11, 12, 15, 0))
    assert found.when.words(WED_10) == "on 12 November at 3 PM"


def test_next_runs():
    weekday9 = when.When(days=when.WEEKDAYS, at=(9, 0))
    fri = datetime(2026, 11, 6, 10, 0).timestamp()
    assert datetime.fromtimestamp(weekday9.next_after(fri)) == datetime(2026, 11, 9, 9, 0)  # Friday after 9: Monday
    every = when.When(every_s=1800, anchor=WED_10)
    assert every.next_after(WED_10 + 10) == WED_10 + 1800 and every.next_after(WED_10 + 1800) == WED_10 + 3600
    assert when.When(once=WED_10).next_after(WED_10) is None


# ---------------------------------------------------------------- restarts and missed runs


def schedules(path, clock, submit=None):
    return w.Watches(w.Store(path), say=lambda line: None, submit=submit, clock=clock, detect=None)


def test_restart_rearms_watches_and_schedules_from_the_file(tmp_path):
    path = tmp_path / "jobs.sqlite"
    term = Term(busy=True)
    first, _, _, clock = rig("watch terminal | on: front | until: idle", store=w.Store(path),
                             probes=w.Probes(terminal_front=lambda: 5, terminal=term))  # fmt: skip
    first.hear("tell me when my build passes")
    assert first.hear("every weekday at 9, give me a brief") == "OK: every weekday at 9 AM, give me a brief."
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    said = []
    again = w.Watches(w.Store(path), say=said.append, probes=w.Probes(terminal=term), clock=clock, detect=None)
    assert again.arm() == []
    assert [e.kind for e in again.listed()] == ["watch", "schedule"] and again.listed()[0].spec["on"] == "5"
    term.busy = False
    clock.t += 1
    again.tick()
    assert said == ["The Terminal window went quiet: compiling…"]  # the baseline came back from the file


def test_a_terminal_window_closed_while_karta_was_off(tmp_path):
    path = tmp_path / "jobs.sqlite"
    term = Term(busy=True)
    first, _, _, clock = rig("watch terminal | on: front | until: idle", store=w.Store(path),
                             probes=w.Probes(terminal_front=lambda: 5, terminal=term))  # fmt: skip
    first.hear("tell me when my build passes")
    term.gone = True
    said = []
    again = w.Watches(w.Store(path), say=said.append, probes=w.Probes(terminal=term), clock=clock, detect=None)
    again.arm()
    again.tick()
    assert said == ["The Terminal window I was watching is gone."] and again.listed() == []


def test_a_schedule_missed_by_under_an_hour_runs_once_and_an_older_miss_is_skipped_and_said(tmp_path):
    path = tmp_path / "jobs.sqlite"
    nine = datetime(2026, 11, 4, 9, 0).timestamp()
    clock = Clock(nine - 3600)
    schedules(path, clock).hear("every weekday at 9, give me a brief")
    ran = []
    clock.t = nine + 30 * 60  # Karta was off at 9; started at 9:30
    later = schedules(path, clock, submit=lambda task: ran.append(task) or "")
    assert later.arm() == ["Starting your scheduled task: give me a brief."] and ran == ["give me a brief"]
    later.tick()
    assert ran == ["give me a brief"]  # never twice: the next time was written before the run
    assert datetime.fromtimestamp(later.listed()[0].next_at) == datetime(2026, 11, 5, 9, 0)
    clock.t = datetime(2026, 11, 5, 12, 0).timestamp()  # off again over Thursday's 9:00, back at noon
    third = schedules(path, clock, submit=lambda task: ran.append(task) or "")
    assert third.arm() == ["I missed your 9 AM task while I was off: give me a brief."] and ran == ["give me a brief"]
    assert datetime.fromtimestamp(third.listed()[0].next_at) == datetime(2026, 11, 6, 9, 0)


def test_a_reminder_is_said_and_then_gone(tmp_path):
    said = []
    clock = Clock()
    reminders = w.Watches(w.Store(tmp_path / "jobs.sqlite"), say=said.append, clock=clock, detect=None)
    assert reminders.hear("remind me in 20 minutes to stretch") == "OK, I'll remind you today at 10:20 AM: stretch."
    clock.t += 20 * 60
    reminders.tick()
    assert said == ["Reminder: stretch."] and reminders.listed() == []


# ---------------------------------------------------------------- voice control


def control_rig():
    jobs, *_ = make()
    term = Term(busy=True)
    watches, *_ = rig("watch terminal | on: front | until: idle", probes=w.Probes(terminal_front=lambda: 1, terminal=term))
    jobs.watches = watches
    return jobs, watches


@pytest.mark.parametrize(
    "steps",
    [
        [("what are you watching?", "I'm not watching anything, and nothing is scheduled.")],
        [("tell me when my build passes", None), ("every weekday at 9, give me a brief", None),
         ("remind me at 6 pm to call my mom", None),
         ("what are you watching?", "I'm watching for your build passes. Scheduled: every weekday at 9 AM, give me a brief; "
                                    "today at 6 PM, remind you: call your mom."),
         ("what are you doing?", "Nothing right now. I also have 1 watch and 2 schedules."),
         ("stop watching the build", "I've stopped watching for your build passes."),
         ("stop watching the build", "I'm not watching anything."),
         ("cancel the 9 o'clock brief", "Cancelled: every weekday at 9 AM, give me a brief."),
         ("forget the reminder to call mom", "Cancelled: today at 6 PM, remind you: call your mom."),
         ("cancel my dentist appointment", None)],  # names nothing here: a task
    ],
)  # fmt: skip
def test_voice_control_of_watches_and_schedules(steps):
    jobs, watches = control_rig()
    for text, reply in steps:
        got = jobs.control(text)
        if got is None and reply is None and watches.hear(text) is not None:
            continue
        assert got == reply, text
    jobs.close()


def test_take_routes_schedules_and_watches_before_new_jobs():
    jobs, watches = control_rig()
    emit = type("E", (), {"emit": staticmethod(lambda kind, **data: None)})
    assert app.take(jobs, "every weekday at 9, give me a brief", emit, watches) == "OK: every weekday at 9 AM, give me a brief."
    assert app.take(jobs, "tell me when my build passes", emit, watches) == ""  # set up in the background
    until(lambda: any(j.state == State.DONE for j in jobs.list()))
    setup = jobs.list()[0]
    assert setup.lane == "background" and setup.said.startswith("OK, I'm watching that Terminal window")
    assert app.take(jobs, "tell me the weather", emit, watches) == ""  # a question about now is a task
    assert [j.task for j in jobs.list() if j.lane == "screen"] == ["tell me the weather"]
    jobs.close()


@pytest.mark.v1  # the fakes' plan is in the v1 format
def test_a_scheduled_tasks_key_action_still_asks_by_voice():
    """A due schedule is a normal screen job: the real run_task on the fakes asks before the send, and a no sends
    nothing."""
    env = chat_env()

    def run(job):
        result = run_task(TASK, job.jobs.guard(env, job), FakeLaya(), FakeBrain(PLAN), FACTS,
                          confirm=lambda q: app.said_yes(job.jobs.ask(job, q)), clock=job.clock)  # fmt: skip
        return result.done, result.said

    jobs, *_ = make(run)
    clock = Clock(datetime(2026, 11, 4, 8, 59).timestamp())
    sched = w.Watches(w.Store(None), say=lambda line: None, submit=lambda task: jobs.submit(task)[1], clock=clock, detect=None)
    sched.hear(f"every weekday at 9, {TASK}")
    clock.t += 60
    sched.tick()
    job = jobs.list()[0]
    until(lambda: job.state == State.ASKING)
    assert "Go ahead?" in job.question or "?" in job.question
    jobs.answer("no")
    until(lambda: job.state not in (State.RUNNING, State.ASKING))
    assert "click Send" not in env.log
    jobs.close()
