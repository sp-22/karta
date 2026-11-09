"""ClaudeCodeBrain offline: a fake `claude` process that speaks stream-json stands in for the CLI."""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import pytest

from karta.brain.claude_code import ClaudeCodeBrain, LLMError, claude_args, extract_json, map_usage


def delta(text: str) -> dict:
    return {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}}


def result(text: str, usage: dict | None = None, **extra) -> dict:
    return {"type": "result", "subtype": "success", "result": text, "usage": usage or {}, **extra}


class _Stdin:
    def __init__(self, proc: FakeProc):
        self.proc = proc

    def write(self, data: str) -> None:
        if self.proc.killed:
            raise BrokenPipeError("dead")
        self.proc.sent.append(json.loads(data)["message"]["content"])
        threading.Thread(target=self.proc.reply, args=(self.proc.sent[-1],), daemon=True).start()

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass


class _Stdout:
    def __init__(self):
        self.lines: queue.Queue[str | None] = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line


class FakeProc:
    """Replies to each user line with the script's (delay_s, event) pairs."""

    def __init__(self, args: list[str], cwd: Path, script):
        self.args, self.cwd, self.script = args, cwd, script
        self.sent: list[str] = []
        self.killed = False
        self.stdin = _Stdin(self)
        self.stdout = _Stdout()

    def reply(self, text: str) -> None:
        for wait, event in self.script(self, text):
            time.sleep(wait)
            if self.killed:
                return
            self.stdout.lines.put(json.dumps(event) + "\n")

    def poll(self):
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True
        self.stdout.lines.put(None)

    def wait(self, timeout=None) -> int:
        return -9


def factory(script):
    procs: list[FakeProc] = []

    def spawn(args, cwd):
        procs.append(FakeProc(args, cwd, script))
        return procs[-1]

    return spawn, procs


def answer(text: str, usage: dict | None = None):
    def script(proc, user):
        return [(0, delta(text)), (0, result(text, usage))]

    return script


@pytest.mark.parametrize(
    "text",
    [
        '{"steps": ["click Send"], "done_when": "sent"}',
        '```json\n{"steps": ["click Send"], "done_when": "sent"}\n```',
        'Here is the plan:\n{"steps": ["click Send"], "done_when": "sent"}\nDone.',
        'Note {not json} then {"steps": ["click Send"], "done_when": "sent"}',
    ],
)
def test_json_is_found_past_fences_and_prose(text):
    assert extract_json(text) == {"steps": ["click Send"], "done_when": "sent"}


def test_no_json_raises_an_llm_error():
    with pytest.raises(LLMError):
        extract_json("I cannot help with that.")


def test_usage_maps_to_the_openai_shape():
    usage = {"input_tokens": 10, "cache_read_input_tokens": 400, "cache_creation_input_tokens": 50, "output_tokens": 70}
    assert map_usage(usage) == {"prompt_tokens": 460, "completion_tokens": 70}


def test_the_command_line_is_stripped_and_thinking_is_off():
    args = claude_args("SYS", "sonnet")
    assert args[:2] == ["claude", "-p"] and "--bare" not in args
    assert args[args.index("--tools") + 1] == ""
    assert args[args.index("--system-prompt") + 1] == "SYS"
    assert json.loads(args[args.index("--settings") + 1]) == {"alwaysThinkingEnabled": False, "autoMemoryEnabled": False}
    assert args[args.index("--setting-sources") + 1] == ""  # no user or project settings, CLAUDE.md or AGENTS.md


def test_the_brain_runs_without_claude_codes_own_memory(monkeypatch, tmp_path):
    seen = {}

    class Fake:
        def __init__(self, args, cwd, env, **_):
            seen["env"] = env

    monkeypatch.setattr("karta.brain.claude_code.subprocess.Popen", Fake)
    from karta.brain.claude_code import _spawn

    _spawn(["claude"], tmp_path)
    assert seen["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"


def test_json_returns_the_dict_and_records_usage(tmp_path):
    spawn, procs = factory(answer('{"steps": []}', {"input_tokens": 5, "output_tokens": 3}))
    brain = ClaudeCodeBrain(model="sonnet", spawn=spawn, workdir=tmp_path)
    assert brain.json("SYS", "Goal: x") == {"steps": []}
    assert brain.calls == 1 and brain.last_usage == {"prompt_tokens": 5, "completion_tokens": 3} and brain.last_ms > 0
    assert procs[0].sent == ["Goal: x"] and procs[0].cwd == tmp_path
    brain.close()


def test_a_spare_is_started_after_each_take_and_the_taken_one_is_closed(tmp_path):
    spawn, procs = factory(answer('{"steps": []}'))
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path)
    brain.warm("SYS")
    assert len(procs) == 1
    brain.json("SYS", "one")
    assert len(procs) == 2 and procs[0].killed and not procs[1].killed  # the warm one answered; a new spare waits
    brain.json("SYS", "two")
    assert len(procs) == 3 and procs[1].sent == ["two"] and procs[1].killed and not procs[2].killed
    brain.close()


def test_a_timeout_kills_the_process_and_retries_once_on_a_fresh_one(tmp_path):
    def script(proc, user):
        if proc is procs[0]:
            return []  # the first process never answers
        return [(0, result('{"steps": ["press return"]}'))]

    spawn, procs = factory(script)
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path, timeout=0.3)
    assert brain.json("SYS", "Goal") == {"steps": ["press return"]}
    assert procs[0].killed and procs[0].sent == ["Goal"] and procs[1].sent == ["Goal"]
    brain.close()


def test_two_timeouts_raise_an_llm_error(tmp_path):
    spawn, _ = factory(lambda proc, user: [])
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path, timeout=0.2)
    with pytest.raises(LLMError, match="no reply"):
        brain.json("SYS", "Goal")
    brain.close()


def test_not_logged_in_is_a_clear_error(tmp_path):
    spawn, _ = factory(lambda proc, user: [(0, result("Not logged in · Please run /login", is_error=True))])
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path)
    with pytest.raises(LLMError, match="not logged in"):
        brain.json("SYS", "Goal")
    brain.close()


def test_a_missing_claude_binary_is_a_clear_error_at_first_use(monkeypatch, tmp_path):
    monkeypatch.setattr("karta.brain.claude_code.shutil.which", lambda name: None)
    brain = ClaudeCodeBrain(workdir=tmp_path)
    with pytest.raises(LLMError, match="Claude Code CLI"):
        brain.json("SYS", "Goal")


def test_stream_lines_yields_each_line_as_it_arrives(tmp_path):
    gate = threading.Event()

    def script(proc, user):
        yield 0, delta("click Compose\ntype {t")
        gate.wait(2)
        yield 0, delta("o} into To\npress return")
        yield 0, result("…", {"output_tokens": 9})

    spawn, _ = factory(script)
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path)
    lines = brain.stream_lines("SYS", "Goal")
    assert next(lines) == "click Compose"  # before the rest of the reply exists
    gate.set()
    assert list(lines) == ["type {to} into To", "press return"]
    assert brain.last_usage["completion_tokens"] == 9
    brain.close()


def test_close_kills_every_process(tmp_path):
    spawn, procs = factory(answer("{}"))
    brain = ClaudeCodeBrain(spawn=spawn, workdir=tmp_path)
    brain.warm("A")
    brain.warm("B")
    brain.close()
    assert len(procs) == 2 and all(p.killed for p in procs)
