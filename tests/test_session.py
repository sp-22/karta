"""Session memory (karta/harness/session.py) and the clean stop (run_task's `stop`): a later task points at an earlier
one's result by `{@n}`, which code binds and types; the block reaches the plan only when the task points back; values
never reach a prompt or the disk; a stop between steps closes the dialog the task opened. Made-up apps, fake brain."""

from __future__ import annotations

import threading

from test_harness_carry import told
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, page
from test_jobs import make, until

from karta.harness.brain import HarnessBrain
from karta.harness.loop import GAVE_UP_STOPPED, STOPPED_SAID, run_task
from karta.harness.session import FRESH_S, KEEP_TASKS, Session
from karta.harness.types import Item
from karta.jobs import State


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


READS = page(
    "Readwell",
    Item("Reading list", "heading"),
    Item("Keep a starter alive", "row"),
    Item("Hooch on top means it is hungry", "row"),
)


def compose() -> FakeEnv:
    """Mailroom's new message: To, a body of several lines, Send (a fresh page each time: typing changes it)."""
    to, body = Item("To", "field"), Item("Body", "field", multiline=True)
    return FakeEnv({"c": page("Mailroom", Item("New message", "heading"), to, body, Item("Send", BTN))}, "c")


ARTICLE = "Tides rise and fall twice a day as the moon pulls the oceans. " * 8  # a page's main text, 500 characters


def plan_prompt(brain: FakeLineBrain, n: int = -1) -> str:
    plans = [user for system, user in brain.asked if user.startswith("Task:")]
    return plans[n]


def reading_list_task(session: Session, monkeypatch) -> None:
    told(monkeypatch, [])
    brain = FakeLineBrain("collect the items | as: reads\ntell what is on my reading list | from: reads | as: list")
    result = run_task("what is on my reading list?", FakeEnv({"r": READS}, "r"), FakeLaya(), brain, FACTS, session=session)
    assert result.done, result.trace


def test_email_that_to_riya_types_the_remembered_items(monkeypatch):
    session = Session(clock=Clock())
    reading_list_task(session, monkeypatch)
    assert [(r.id, r.kind) for r in session.events[-1].refs] == [(1, "items"), (2, "answer")]
    env = compose()
    brain = FakeLineBrain("write Riya | where: To\nwrite {@1} | where: Body")
    result = run_task("email that to Riya", env, FakeLaya(), HarnessBrain(brain), FACTS, session=session)
    prompt = plan_prompt(brain, 0)
    assert "Recent (this session" in prompt and "@1 items 2 items" in prompt and "@2 answer" in prompt
    assert "set Body=Keep a starter alive\nHooch on top means it is hungry" in env.log, result.trace  # code typed the value
    assert "set Body={@1}" not in env.log


def test_summarise_it_reads_the_last_page_from_the_session(monkeypatch):
    told(monkeypatch, [])
    session = Session(clock=Clock())
    article = page("Brave Browser", Item("How tides work", "heading"), url="https://tides.example/how")
    article.window = "How tides work"
    env = FakeEnv({"a": article}, "a")
    env.page_text = lambda: ARTICLE
    brain = FakeLineBrain("collect the main text of the article | as: article\ndone: new_item(How tides work)")
    run_task("read the article on tides", env, FakeLaya(), brain, FACTS, session=session)
    refs = session.events[-1].refs
    assert [r.kind for r in refs] == ["text"] and refs[0].title.startswith('"How tides work", 496 chars')

    other = FakeEnv({"n": page("Notes", Item("Groceries", "row"))}, "n")  # another app in front now
    brain = FakeLineBrain("tell summarise the article | from: @1 | as: answer", "Tides follow the moon, twice a day [1].")
    result = run_task("summarise it", other, FakeLaya(), brain, FACTS, session=session)
    assert result.done and result.answer.startswith("Tides follow the moon"), result.trace
    prompt = plan_prompt(brain, 0)
    assert '@1 text "How tides work", 496 chars (tides.example)' in prompt and "Ended on: Brave Browser, tides.example" in prompt
    assert "moon pulls" not in prompt  # the text went to the tell's own call, never the plan
    assert other.log == []  # nothing on screen was needed


def test_no_block_when_the_task_points_at_nothing(monkeypatch):
    session = Session(clock=Clock())
    reading_list_task(session, monkeypatch)
    env = lambda: FakeEnv({"n": page("Notes", Item("New Note", BTN))}, "n")  # noqa: E731
    plan = "click New Note | expect: new_item\ndone: new_item"
    with_session, without = FakeLineBrain(plan), FakeLineBrain(plan)
    run_task("make a new note", env(), FakeLaya(), with_session, FACTS, session=session)
    run_task("make a new note", env(), FakeLaya(), without, FACTS)
    assert "Recent (" not in plan_prompt(with_session, 0)
    assert plan_prompt(with_session, 0) == plan_prompt(without, 0)  # byte for byte: the warm cache holds
    assert session.lines("starter dough tips") and session.lines("make a new note") == []  # a shared word lists it


def test_entries_expire_after_thirty_minutes_and_eight_tasks(monkeypatch):
    clock = Clock()
    session = Session(clock=clock)
    reading_list_task(session, monkeypatch)
    assert session.lines("send that to Riya")
    clock.t += FRESH_S + 60
    assert session.lines("send that to Riya") == [] and session.addresses() == ""
    for _ in range(KEEP_TASKS + 1):
        reading_list_task(session, monkeypatch)
    assert len(session.events) == KEEP_TASKS


def test_values_never_reach_the_prompt_or_the_disk(monkeypatch, tmp_path):
    told(monkeypatch, [])
    card = page("Contactly", Item("Riya Sen riya.sen@mailroom.example", "row"), Item("Edit", BTN))
    session = Session(tmp_path / "session.sqlite", clock=Clock())
    brain = FakeLineBrain("collect Riya's email address | as: addr\ntell what is Riya's address | from: addr | as: list")
    run_task("what is Riya's email address?", FakeEnv({"c": card}, "c"), FakeLaya(), brain, FACTS, session=session)
    env = compose()
    brain = FakeLineBrain("write {@1} | where: To\ndone: field_holds(Riya)")
    run_task("send her the same note again", env, FakeLaya(), brain, FACTS, session=session)
    prompt = plan_prompt(brain, 0)
    assert "@1 items" in prompt and "riya.sen" not in prompt and "mailroom.example" not in prompt
    assert "set To=riya.sen@mailroom.example" in env.log  # the value reached the screen by code only
    disk = (tmp_path / "session.sqlite").read_bytes()
    assert b"riya.sen" not in disk and b"what is Riya" in disk  # task text and titles, never a value
    assert session.recall("Riya address")[0][1] == "what is Riya's email address?"
    assert (tmp_path / "session.sqlite").stat().st_mode & 0o777 == 0o600


def test_a_code_collected_is_never_kept(monkeypatch):
    told(monkeypatch, [])
    session = Session(clock=Clock())
    sms = page("Textly", Item("Your sign-in code is 482913", "row"))
    brain = FakeLineBrain("collect the sign-in code | as: code\ntell what is the code | from: code | as: list")
    run_task("what is my sign-in code?", FakeEnv({"s": sms}, "s"), FakeLaya(), brain, FACTS, session=session)
    kept = [c.value for r in session.events[-1].refs if r.kind != "answer" for c in r.items]
    assert "482913" not in " ".join(kept)


def test_an_address_the_session_gave_passes_foreign_detail(monkeypatch):
    told(monkeypatch, [])
    card = page("Contactly", Item("Riya Sen riya.sen@mailroom.example", "row"))
    session = Session(clock=Clock())
    brain = FakeLineBrain("collect Riya's email address | as: addr\ntell what is Riya's address | from: addr | as: list")
    run_task("what is Riya's email address?", FakeEnv({"c": card}, "c"), FakeLaya(), brain, FACTS, session=session)
    plan = "write riya.sen@mailroom.example | where: To\ndone: field_holds(riya.sen@mailroom.example)"
    given, stranger = FakeLineBrain(plan), FakeLineBrain(plan)
    env = compose()
    run_task("write to her", env, FakeLaya(), given, FACTS, session=session)
    assert "set To=riya.sen@mailroom.example" in env.log  # Karta held it: no "address nobody gave"
    env = compose()
    run_task("write to her", env, FakeLaya(), stranger, FACTS)
    assert not any(line.startswith("set To=") for line in env.log)  # without the session it is foreign


# ---------------------------------------------------------------- the clean stop


def notely(flag) -> FakeEnv:
    """Notely: Delete note opens a confirmation; the stop lands just after that click."""
    note = page("Notely", Item("Shopping", "row"), Item("Delete note", BTN))
    alert = page(
        "Notely", Item("Shopping", "row"), Item("Delete this note?", "text", dialog=True),
        Item("Delete", BTN, dialog=True), Item("Cancel", BTN, dialog=True, cancels=True),
    )  # fmt: skip

    def opened(env):
        env.at = "alert"
        flag()

    return FakeEnv(
        {"note": note, "alert": alert}, "note", {("note", "click Delete note"): opened, ("alert", "click Cancel"): "note"}
    )


PLAN = "click Delete note | expect: dialog_opened\nanswer Delete | expect: item_gone\ndone: item_gone(Shopping)"


def test_stop_between_steps_closes_the_opened_dialog_and_says_so():
    stop = threading.Event()
    env = notely(stop.set)
    brain = FakeLineBrain(PLAN)
    result = run_task("delete the shopping note in notely", env, FakeLaya(), brain, FACTS, confirm=lambda q: True,
                      stop=stop.is_set)  # fmt: skip
    assert env.log == ["click Delete note", "click Cancel"] and env.at == "note", result.trace
    assert not result.done and result.gave_up == GAVE_UP_STOPPED and result.said == STOPPED_SAID
    assert brain.calls == 1 and result.rescues == 0 and result.pinned == ""


def test_a_job_stopped_mid_task_ends_cleanly_through_the_guard():
    holder = {}
    env = notely(lambda: holder["jobs"].control("stop"))
    results = []

    def run(job):
        result = run_task("delete the shopping note in notely", job.jobs.guard(env, job), FakeLaya(), FakeLineBrain(PLAN),
                          FACTS, confirm=lambda q: True, clock=job.clock, stop=job.stopping)  # fmt: skip
        results.append(result)
        return result.done, result.said

    jobs, *_ = make(run)
    holder["jobs"] = jobs
    job, _ = jobs.submit("delete the shopping note in notely")
    until(lambda: job.state not in (State.QUEUED, State.RUNNING))
    assert job.state == State.CANCELLED and results[0].gave_up == GAVE_UP_STOPPED
    assert env.log == ["click Delete note", "click Cancel"]  # the clean-up acted; nothing else did
    jobs.close()
