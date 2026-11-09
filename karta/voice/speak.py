"""Speaking answers out loud with the built-in macOS `say`. Non-blocking; never goes through a shell."""

from __future__ import annotations

import contextlib
import os
import re
import subprocess

DEFAULT_VOICE = "Tara"

_VOICE_LINE = re.compile(r"^(.+?)\s+([a-z]{2,3}[_-][A-Za-z0-9]+)\s*(?:#.*)?$")


class Speech:
    """One utterance in flight. A no-op Speech (no process) is never speaking."""

    def __init__(self, proc: subprocess.Popen | None = None):
        self.proc = proc

    @property
    def speaking(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass

    def wait(self, timeout: float | None = None) -> None:
        if self.proc is None:
            return
        with contextlib.suppress(subprocess.TimeoutExpired):
            self.proc.wait(timeout=timeout)


def voice_names() -> list[str]:
    """Installed voice names from `say -v '?'` (empty when `say` cannot be run)."""
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        found = _VOICE_LINE.match(line.split("#")[0].rstrip())
        if found:
            names.append(found.group(1).strip())
    return names


def voice_installed(name: str) -> bool:
    want = (name or "").strip().lower()
    if not want:
        return False
    for have in voice_names():
        low = have.lower()
        if want == low or want == low.split(" (")[0]:
            return True
    return False


def speak(text: str, voice: str | None = None) -> Speech:
    """Start saying `text` and return at once. The voice is `voice`, else KARTA_VOICE, else Tara; one that is not
    installed falls back to the system default. Empty text, or no `say`, gives a no-op Speech."""
    text = (text or "").strip()
    if not text:
        return Speech()
    name = (voice or os.environ.get("KARTA_VOICE") or DEFAULT_VOICE).strip()
    args = ["say"]
    if name and voice_installed(name):
        args += ["-v", name]
    args += ["--", text]
    try:
        return Speech(subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except (OSError, subprocess.SubprocessError):
        return Speech()
