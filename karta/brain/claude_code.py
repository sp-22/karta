"""Claude Code as Karta's planner brain: the `claude` CLI in headless stream-json mode, text in, text out.

Karta's harness stays in charge; the brain only writes text. Each `claude` process is stripped
(no tools, no MCP, no slash commands, no session file, low effort, thinking off) and runs from an
empty directory, so no CLAUDE.md or project settings load. It uses the user's own Claude Code
login through the CLI itself; no token is read or passed around here.

Warm-spare pool: one fresh process per request (a long session gets slower as its history grows).
One spare per system prompt and model is always booting in the background; a request takes it, the next
spare starts at once, and the taken process is closed when its reply is in. A request may name its own model (the
page calls' KARTA_PAGE_MODEL); by default it is the brain's.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Protocol

DEFAULT_MODEL = "sonnet"
TIMEOUT_S = 30.0
WORKDIR = Path.home() / "Library" / "Caches" / "karta" / "brain"  # empty on purpose: no CLAUDE.md, no project settings
WARMUP = "Warm-up, no task yet. Reply with the single word: ready"


class LLMError(RuntimeError):
    """The brain failed to answer (the CLI missing, timed out, or an unreadable reply)."""


class BrainUnavailable(LLMError):
    """The `claude` CLI is missing or not logged in."""


class Popen(Protocol):
    stdin: IO[str] | None
    stdout: IO[str] | None

    def poll(self) -> int | None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


Spawn = Callable[[list[str], Path], Popen]


def _spawn(args: list[str], cwd: Path) -> Popen:
    env = {k: v for k, v in os.environ.items() if k not in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"  # Claude Code's own memory is not Karta's: never in the brain's context
    return subprocess.Popen(
        args, cwd=cwd, env=env, text=True, bufsize=1,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )  # fmt: skip


def claude_args(system: str, model: str) -> list[str]:
    """The measured, stripped headless command line."""
    return [
        "claude", "-p", "--model", model,
        "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--disable-slash-commands", "--no-session-persistence", "--effort", "low",
        # isolated from the user's Claude Code: no user or project settings, CLAUDE.md, AGENTS.md or auto-memory; the
        # brain knows only what Karta puts in its prompt (Haiku thinks by default, ~33 s, without the thinking flag)
        "--setting-sources", "", "--settings", '{"alwaysThinkingEnabled": false, "autoMemoryEnabled": false}',
        "--exclude-dynamic-system-prompt-sections", "--system-prompt", system,
        "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
    ]  # fmt: skip


def text_delta(event: dict) -> str | None:
    """The text a stream event carries, if it is a text delta."""
    if event.get("type") != "stream_event":
        return None
    inner = event.get("event") or {}
    delta = inner.get("delta") or {}
    if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
        return delta.get("text", "")
    return None


def extract_json(text: str) -> dict:
    """The JSON object in a reply, past any code fence or leading/trailing prose."""
    start = text.find("{")
    if start < 0:
        raise LLMError(f"no JSON object in the brain's reply: {text[:300]!r}")
    decoder = json.JSONDecoder()
    while start >= 0:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    raise LLMError(f"the brain's reply is not valid JSON: {text[:300]!r}")


def map_usage(usage: dict) -> dict[str, int]:
    """Claude's usage in the OpenAI shape the planner eval reads."""
    prompt = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    return {"prompt_tokens": prompt, "completion_tokens": int(usage.get("output_tokens") or 0)}


class _Proc:
    """One `claude` process and a thread that turns its stdout into parsed events."""

    def __init__(self, popen: Popen):
        self.popen = popen
        self.events: queue.Queue[dict | None] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            for line in self.popen.stdout or ():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    self.events.put(event)
        except (OSError, ValueError):
            pass
        self.events.put(None)  # end of output: the process is gone

    def send(self, text: str) -> None:
        msg = {"type": "user", "message": {"role": "user", "content": text}}
        try:
            assert self.popen.stdin is not None
            self.popen.stdin.write(json.dumps(msg) + "\n")
            self.popen.stdin.flush()
        except (OSError, ValueError) as error:
            raise LLMError(f"the claude process is gone: {error}") from error

    def turn(self, timeout: float) -> Iterator[dict]:
        """Every event of one turn, ending with its result event; LLMError on timeout or death."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"no reply from claude within {timeout:.0f} s")
            try:
                event = self.events.get(timeout=left)
            except queue.Empty:
                continue
            if event is None:
                raise LLMError("the claude process exited before replying")
            yield event
            if event.get("type") == "result":
                return

    def close(self) -> None:
        try:
            if self.popen.stdin:
                self.popen.stdin.close()
        except (OSError, ValueError):
            pass
        if self.popen.poll() is None:
            self.popen.kill()
        with contextlib.suppress(Exception):
            self.popen.wait(timeout=2)


class ClaudeCodeBrain:
    """The brain behind Karta's planner, backed by warm `claude` processes."""

    def __init__(
        self,
        model: str | None = None,
        timeout: float = TIMEOUT_S,
        prewarm: bool = False,
        spawn: Spawn | None = None,
        workdir: Path | None = None,
    ):
        self.model = model or os.environ.get("KARTA_BRAIN_MODEL", DEFAULT_MODEL)
        self.timeout = timeout
        self.prewarm = prewarm  # a warm-up turn on each spare: measured steadier, not faster
        self._spawn = spawn
        self.workdir = workdir or WORKDIR
        self.calls = 0
        self.last_ms = 0.0
        self.last_usage: dict[str, int] = {}
        self._spares: dict[tuple[str, str], _Proc] = {}  # (system prompt, model) -> its warm spare
        self._live: set[_Proc] = set()
        self._lock = threading.Lock()
        self._checked = spawn is not None  # an injected factory needs no `claude` on PATH
        atexit.register(self.close)

    # -- pool

    def _check(self) -> None:
        if self._checked:
            return
        if shutil.which("claude") is None:
            raise BrainUnavailable(
                "KARTA_BRAIN=claude-code needs the Claude Code CLI: install `claude` and run `claude` once to log in"
            )
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._checked = True

    def _start(self, system: str, model: str | None = None) -> _Proc:
        proc = _Proc((self._spawn or _spawn)(claude_args(system, model or self.model), self.workdir))
        with self._lock:
            self._live.add(proc)
        if self.prewarm:
            proc.send(WARMUP)
        return proc

    def warm(self, system: str, model: str | None = None) -> None:
        """Start the spare for this system prompt (and model) now, so the first request does not pay the boot."""
        self._check()
        key = (system, model or self.model)
        with self._lock:
            have = key in self._spares
        if not have:
            spare = self._start(*key)
            with self._lock:
                self._spares[key] = spare

    def _take(self, system: str, model: str | None = None) -> _Proc:
        """The warm spare for this prompt and model (or a fresh one), and the next spare started at once."""
        self._check()
        key = (system, model or self.model)
        with self._lock:
            proc = self._spares.pop(key, None)
        if proc is None or proc.popen.poll() is not None:
            if proc is not None:
                self._drop(proc)
            proc = self._start(*key)
        spare = self._start(*key)
        with self._lock:
            self._spares[key] = spare
        if self.prewarm:
            for _ in proc.turn(self.timeout):  # the warm-up reply, discarded
                pass
        return proc

    def _drop(self, proc: _Proc) -> None:
        proc.close()
        with self._lock:
            self._live.discard(proc)

    # -- requests

    def _finish(self, result: dict, started: float) -> str:
        self.calls += 1
        self.last_ms = (time.perf_counter() - started) * 1000
        self.last_usage = map_usage(result.get("usage") or {})
        text = result.get("result") or ""
        if result.get("is_error") or result.get("subtype", "success") != "success":
            lowered = text.lower()
            if "login" in lowered or "logged in" in lowered or "api key" in lowered or "auth" in lowered:
                raise BrainUnavailable(f"Claude Code is not logged in (run `claude` once and /login): {text[:200]}")
            raise LLMError(f"claude returned an error: {text[:300]}")
        return text

    def _ask(self, system: str, user: str) -> str:
        """The full reply text; one retry on a fresh process after a timeout or a dead process."""
        for attempt in range(2):
            started = time.perf_counter()
            proc = self._take(system)
            try:
                proc.send(user)
                result: dict = {}
                for event in proc.turn(self.timeout):
                    result = event
                return self._finish(result, started)
            except BrainUnavailable:
                raise
            except (TimeoutError, LLMError) as error:
                if attempt == 1 or _is_reply_error(error):
                    raise LLMError(str(error)) from error
            finally:
                self._drop(proc)
        raise LLMError("unreachable")

    def json(self, system: str, user: str) -> dict:
        return extract_json(self._ask(system, user))

    def stream_lines(self, system: str, user: str, model: str | None = None) -> Iterator[str]:
        """Each complete line of the reply as soon as it arrives (the last one when the turn ends). `model`: this
        request's model (default: the brain's)."""
        started = time.perf_counter()
        proc = self._take(system, model)
        try:
            proc.send(user)
            buffer = ""
            for event in proc.turn(self.timeout):
                piece = text_delta(event)
                if piece:
                    buffer += piece
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.strip():
                            yield line
                elif event.get("type") == "result":
                    self._finish(event, started)
            if buffer.strip():
                yield buffer
        except TimeoutError as error:
            raise LLMError(str(error)) from error
        finally:
            self._drop(proc)

    def close(self) -> None:
        """Kill every process this brain started."""
        with self._lock:
            procs = list(self._live)
            self._live.clear()
            self._spares.clear()
        for proc in procs:
            proc.close()


def _is_reply_error(error: Exception) -> bool:
    """An error claude itself reported (not worth a retry), as opposed to a hang or a dead process."""
    return str(error).startswith("claude returned an error")
