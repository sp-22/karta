"""Delegate (karta/harness/delegate.py): Claude Code started on a repo in a new Terminal window, heard back from by its
own hooks. osascript and ps are fakes: nothing here opens a window or runs Claude Code."""

from __future__ import annotations

import json
import shlex
import threading
from pathlib import Path

import pytest
from test_harness_tools import FACTS, FakeEnv, FakeLaya, FakeLineBrain, page
from test_jobs import make, until

from karta.harness import delegate as dg
from karta.harness import risk
from karta.harness.brain import HarnessBrain, parse_line
from karta.harness.loop import run_task
from karta.harness.session import Session
from karta.harness.tell import ahead_line
from karta.harness.types import Item
from karta.jobs import State

# ---------------------------------------------------------------- quoting


NASTY = [
    "add a cancel button'; rm -rf ~; echo '",
    'fix $(whoami) and `id` in the "login" form',
    "rename $HOME && ls > /tmp/x | cat",
    "add a café menu — with ünïcode 名前",
    "don't break the build\\n please",
]


@pytest.mark.parametrize("request_", NASTY)
def test_the_request_is_one_quoted_word_whatever_it_holds(tmp_path, request_):
    repo, settings = tmp_path / "my repo", tmp_path / "Application Support" / "settings.json"
    line = dg.command(repo, settings, request_)
    assert shlex.split(line) == ["cd", str(repo), "&&", "claude", "--settings", str(settings), request_]
    script = dg.applescript(line)
    quoted = next(s for s in script.splitlines() if s.startswith("set t to do script "))[len("set t to do script ") :]
    assert quoted[0] == quoted[-1] == '"'
    unescaped = quoted[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    assert unescaped == line


@pytest.mark.parametrize(
    "request_,why",
    [
        ("add a button\nrm -rf ~", "control character"),
        ("add a button\r", "control character"),
        ("add\ta button", "control character"),
        ("colour \x1b[31m it", "control character"),
        ("--dangerously-skip-permissions", "starts with '-'"),
        ("-p do it", "starts with '-'"),
        ("x" * (dg.MAX_REQUEST + 1), "over"),
        ("   ", "no request"),
    ],
)
def test_a_request_with_control_characters_or_an_option_is_refused(tmp_path, request_, why):
    assert why in (dg.check_request(request_) or "")
    with pytest.raises(dg.Refused):
        dg.command(tmp_path, tmp_path / "s.json", request_)
    if request_.strip() == request_:  # a plan line is stripped; inside it, the same rule
        step = parse_line(f"delegate {request_} | repo: folio")
        assert step[0] != "step", step  # never a plan step either


def test_never_a_permission_flag_and_never_an_existing_window():
    line = dg.command(Path("/r/folio"), Path("/s/settings.json"), "add a cancel button")
    assert shlex.split(line)[3:6] == ["claude", "--settings", "/s/settings.json"] and len(shlex.split(line)) == 7
    script = dg.applescript(line)
    do = [s for s in script.splitlines() if "do script" in s]
    assert len(do) == 1 and do[0].startswith('set t to do script "') and do[0].endswith('"')  # no `in`: a new window
    for never in ("keystroke", "System Events", "key code", "activate", "close", "quit", "kill"):
        assert never not in script  # nothing typed, focused, closed or killed: only the one new window


# ---------------------------------------------------------------- repo resolution


def repos(home: Path) -> list[str]:
    (home / "Projects" / "folio" / ".git").mkdir(parents=True)
    (home / "Projects" / "notes").mkdir(parents=True)  # no .git
    (home / "Work" / "karta-web").mkdir(parents=True)
    (home / "Work" / "karta-web" / ".git").write_text("gitdir: elsewhere")  # a worktree's .git file
    return ["~/Projects", "~/Work", "~/Projects/folio", "~/Projects/notes", "~/Work/karta-web"]


def test_a_repo_resolves_by_name_path_or_the_session(tmp_path):
    folders = repos(tmp_path)
    folio = (tmp_path / "Projects" / "folio").resolve()
    assert dg.resolve_repo("folio", folders, home=tmp_path) == (folio, "")
    assert dg.resolve_repo("Folio", folders, home=tmp_path)[0] == folio
    assert dg.resolve_repo("karta web", folders, home=tmp_path)[0] == (tmp_path / "Work" / "karta-web").resolve()
    assert dg.resolve_repo("~/Projects/folio", [], home=tmp_path)[0] == folio
    assert dg.resolve_repo(str(folio), [], home=tmp_path)[0] == folio
    assert dg.resolve_repo("", folders, recent="~/Projects/folio", home=tmp_path)[0] == folio


def test_a_folder_that_is_no_git_repo_is_refused(tmp_path):
    folders = repos(tmp_path)
    assert dg.resolve_repo("notes", folders, home=tmp_path) == (None, "~/Projects/notes is not a git repository")
    assert dg.resolve_repo("ledger", folders, home=tmp_path) == (None, "I don't know a folder called ledger")
    assert dg.resolve_repo("~/Projects/gone", [], home=tmp_path) == (None, "~/Projects/gone is not a folder")
    assert dg.resolve_repo("", folders, home=tmp_path) == (None, "no repo was named")
    (tmp_path / "Old" / "folio" / ".git").mkdir(parents=True)
    path, why = dg.resolve_repo("folio", [*folders, "~/Old/folio"], home=tmp_path)
    assert path is None and why.startswith("two folders are called folio")


# ---------------------------------------------------------------- the up-front question


def test_delegate_is_always_a_run_key_action_asked_with_folder_and_request(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    step = {"tool": "delegate", "request": "add a cancel button", "repo": "folio"}
    assert risk.key_action(step) == "run"
    folio = tmp_path / "Desktop" / "Projects" / "folio"
    assert dg.question(folio, "add a cancel button") == (
        "I'll start Claude Code in ~/Desktop/Projects/folio with: 'add a cancel button'. Go ahead?"
    )
    assert ahead_line([step], {id(step): "run"}, {id(step): folio}) == (
        "I'll start Claude Code in ~/Desktop/Projects/folio with: 'add a cancel button'. Go ahead?"
    )


class Terminal:
    """A fake env with open_terminal: records every call; every other action is FakeEnv's (logged)."""

    def __init__(self, fail: str = ""):
        self.env = FakeEnv({"t": page("Finder", Item("Documents", "row"))}, "t")
        self.opened: list[str] = []
        self.fail = fail

    def __getattr__(self, name):
        return getattr(self.env, name)

    def open_terminal(self, line: str):
        self.opened.append(line)
        if self.fail:
            raise dg.Refused(self.fail)
        return 4242, "/dev/ttys009"


def delegate_task(
    tmp_path, monkeypatch, answer: bool, env=None, session=None, reply="delegate add a cancel button | repo: folio"
):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop" / "Projects" / "folio" / ".git").mkdir(parents=True)
    facts = type(FACTS)(FACTS.installed_apps, FACTS.browsers, ["~/Desktop/Projects/folio"], FACTS.now)
    env = env or Terminal()
    asked: list[str] = []

    def confirm(q):
        asked.append(q)
        return answer

    result = run_task(
        "start a Claude Code session in folio to add a cancel button",
        env,
        FakeLaya(),
        HarnessBrain(FakeLineBrain(reply)),
        facts,
        confirm=confirm,
        session=session,
    )
    return result, env, asked


def test_a_yes_starts_it_once_in_a_new_window(tmp_path, monkeypatch):
    session = Session()
    result, env, asked = delegate_task(tmp_path, monkeypatch, True, session=session)
    assert asked == ["I'll start Claude Code in ~/Desktop/Projects/folio with: 'add a cancel button'. Go ahead?"]
    assert result.done, result.trace
    assert len(env.opened) == 1 and shlex.split(env.opened[0])[-1] == "add a cancel button"
    assert env.env.log == []  # nothing clicked, typed or pressed: the only action is the new window
    assert result.said == "Claude Code is starting in folio."
    [launched] = result.delegated
    assert launched.window == 4242 and launched.repo.name == "folio"
    settings = json.loads((launched.folder / "settings.json").read_text())
    assert set(settings["hooks"]) == {"Stop", "Notification"}
    assert session.last_repo() == "~/Desktop/Projects/folio"


def test_a_no_stops_and_starts_nothing(tmp_path, monkeypatch):
    result, env, asked = delegate_task(tmp_path, monkeypatch, False)
    assert len(asked) == 1 and not result.done and env.opened == [] and result.delegated == []
    assert "you said no" in result.said


def test_a_non_git_folder_fails_before_anything_is_asked(tmp_path, monkeypatch):
    (tmp_path / "Desktop" / "Projects" / "notes").mkdir(parents=True)
    result, env, asked = delegate_task(tmp_path, monkeypatch, True, reply="delegate tidy up | repo: ~/Desktop/Projects/notes")
    assert asked == [] and env.opened == [] and not result.done
    assert "~/Desktop/Projects/notes is not a git repository" in result.said


def test_claude_code_that_did_not_start_is_reported_honestly(tmp_path, monkeypatch):
    env = Terminal(fail="the new Terminal window is open but Claude Code did not start in it (is `claude` installed?)")
    result, env, _ = delegate_task(tmp_path, monkeypatch, True, env=env)
    assert not result.done and len(env.opened) == 1
    assert "Claude Code did not start" in result.said


# ---------------------------------------------------------------- the launch check


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_launch_needs_one_more_window_and_claude_running_in_it():
    scripts: list[str] = []

    def osa(reply):
        return lambda script: scripts.append(script) or reply

    clock = Clock()
    ps = iter(["-zsh\n", "-zsh\n/usr/local/bin/claude --settings x 'add it'\n"])
    got = dg.launch("cd x && claude y", osa("2, 3, 77, /dev/ttys004"), lambda tty: next(ps), sleep=clock.sleep, clock=clock)
    assert got == (77, "/dev/ttys004") and "do script" in scripts[0]
    node = dg.launch("l", osa("0,1,5,/dev/ttys001"), lambda tty: "node /opt/bin/claude add\n", sleep=clock.sleep, clock=clock)
    assert node == (5, "/dev/ttys001")
    with pytest.raises(dg.Refused, match="no new window"):
        dg.launch("l", osa("3,3,77,/dev/ttys004"), lambda tty: "claude", sleep=clock.sleep, clock=clock)
    with pytest.raises(dg.Refused, match="did not start"):
        dg.launch(
            "l", osa("1,2,8,/dev/ttys002"), lambda tty: "-zsh\nzsh: command not found: claude\n", sleep=clock.sleep, clock=clock
        )


def test_the_real_launch_is_blocked_in_tests():
    with pytest.raises(RuntimeError, match="real machine"):
        dg.launch("cd x && claude y")


# ---------------------------------------------------------------- hook events into spoken lines


def test_hook_inputs_become_spoken_lines():
    needs = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "message": "may I run rm?"}
    assert dg.heard(needs, "folio") == ("needs", "Claude Code in folio needs you.")
    assert dg.heard({"hook_event_name": "Notification", "message": "?"}, "folio")[0] == "needs"
    assert dg.heard({"hook_event_name": "Notification", "notification_type": "auth_success"}, "folio") == ("", "")
    stop = {
        "hook_event_name": "Stop",
        "stop_hook_active": False,
        "last_assistant_message": "\n## **Added** the cancel button.\nDetails…",
    }
    assert dg.heard(stop, "folio") == ("stop", "Claude Code finished in folio: Added the cancel button.")
    assert dg.heard({"hook_event_name": "Stop"}, "folio") == ("stop", "Claude Code finished in folio.")
    assert dg.heard({"hook_event_name": "PreToolUse"}, "folio") == ("", "")


def test_the_events_file_is_read_in_pieces(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"hook_event_name": "Notification"}\nnot json\n{"hook_event_name":\n "Stop"}\n{"hook_ev')
    events, offset = dg.read_events(path)
    assert [e["hook_event_name"] for e in events] == ["Notification", "Stop"]
    with path.open("a") as f:
        f.write('ent_name": "Stop", "last_assistant_message": "x"}\n')
    more, _ = dg.read_events(path, offset)
    assert more == [{"hook_event_name": "Stop", "last_assistant_message": "x"}]


def test_the_hook_commands_append_to_the_job_file():
    events = Path("/Users/a/Library/Application Support/karta/delegate/1/events.jsonl")
    settings = dg.hooks_settings(events)
    for name in ("Stop", "Notification"):
        [group] = settings["hooks"][name]
        [hook] = group["hooks"]
        assert hook["type"] == "command"
        assert shlex.split(hook["command"]) == ["cat", ">>", str(events) + ";", "echo", ">>", str(events)]


# ---------------------------------------------------------------- the watching job


def launched(tmp_path) -> dg.Launched:
    folder = tmp_path / "job"
    folder.mkdir()
    out = dg.Launched(tmp_path / "folio", "add a cancel button", folder, window=4242)
    out.events.touch()
    return out


@pytest.fixture
def table():
    jobs, _speaker, said, _events = make()
    yield jobs, said
    jobs.close()  # a watch still running stops here: its job is cancelled


def test_the_watch_says_needs_you_then_finished_and_keeps_the_result(tmp_path, monkeypatch, table):
    monkeypatch.setattr(dg, "POLL_S", 0.01)
    jobs, said = table
    session, one = Session(), launched(tmp_path)

    def work(job):
        return dg.watch(one, job, lambda t: jobs.say(job, t), dg.keeper(session), lambda w: True)

    job = jobs.background("Claude Code in folio", work, kind="delegate")
    until(lambda: job.state == State.WATCHING)
    with one.events.open("a") as f:
        f.write(json.dumps({"hook_event_name": "Notification", "notification_type": "permission_prompt"}) + "\n")
    until(lambda: said.lines == ["Claude Code in folio needs you."])
    with one.events.open("a") as f:
        f.write(json.dumps({"hook_event_name": "Stop", "last_assistant_message": "Added the button.\nMore."}) + "\n")
    until(lambda: job.state == State.DONE)
    assert job.said == "Claude Code finished in folio: Added the button."
    until(lambda: said.lines[-1] == "Claude Code finished in folio: Added the button.")
    [ref] = session.events[-1].refs
    assert ref.kind == "text" and ref.items[0].value == "Added the button.\nMore." and "Added" not in ref.title
    assert session.listed("what did it do?") == [ref]  # "it" points at the result: a tell from @n follows up


class Halt:
    def __init__(self):
        self.cancel = threading.Event()


def test_the_watch_ends_when_the_window_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "POLL_S", 0.01)
    monkeypatch.setattr(dg, "WINDOW_EVERY_S", 0.0)
    one = launched(tmp_path)
    assert dg.watch(one, Halt(), print, None, lambda w: False) == "The Claude Code window in folio was closed."

    def broken(window):
        one.events.write_text(json.dumps({"hook_event_name": "Stop"}) + "\n")  # it ends by its own Stop
        raise RuntimeError("osascript failed")

    assert dg.watch(one, Halt(), print, None, broken) == "Claude Code finished in folio."  # a failed check: no news


def test_cancelling_the_job_only_stops_listening(tmp_path, monkeypatch, table):
    monkeypatch.setattr(dg, "POLL_S", 0.01)
    jobs, said = table
    one = launched(tmp_path)
    job = jobs.background("Claude Code in folio", lambda job: dg.watch(one, job, print, None, lambda w: True), kind="delegate")
    until(lambda: job.state == State.WATCHING)
    assert jobs.control("cancel job 1") == "Cancelling job 1."
    until(lambda: job.state == State.CANCELLED)
    assert said.lines == []
