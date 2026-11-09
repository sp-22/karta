import subprocess

from karta.voice import speak as sp

VOICES = "Alex                en_US    # Most people recognize me\nEddy (English (UK)) en_GB    # Hello\nTara                en_IN    # Hi\n"


class FakeProc:
    def __init__(self):
        self.code = None
        self.kills = 0

    def poll(self):
        return self.code

    def kill(self):
        self.kills += 1
        self.code = -9

    def wait(self, timeout=None):
        return self.code


def patch(monkeypatch, voices=VOICES):
    calls = []

    def popen(args, **kw):
        calls.append(args)
        return FakeProc()

    monkeypatch.setattr(sp.subprocess, "Popen", popen)
    monkeypatch.setattr(sp.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=voices, stderr=""))
    return calls


def test_voice_installed(monkeypatch):
    patch(monkeypatch)
    assert sp.voice_installed("Tara") and sp.voice_installed("tara") and sp.voice_installed("Eddy")
    assert not sp.voice_installed("Nobody") and not sp.voice_installed("")


def test_speak_args_no_shell(monkeypatch):
    calls = patch(monkeypatch)
    monkeypatch.delenv("KARTA_VOICE", raising=False)
    s = sp.speak("hi; rm -rf / $(x)")
    assert calls == [["say", "-v", "Tara", "--", "hi; rm -rf / $(x)"]]
    assert s.speaking
    s.stop()
    s.stop()
    assert not s.speaking and s.proc.kills == 1


def test_env_and_missing_voice_fallback(monkeypatch):
    calls = patch(monkeypatch)
    monkeypatch.setenv("KARTA_VOICE", "Alex")
    sp.speak("a")
    monkeypatch.setenv("KARTA_VOICE", "Ghost")
    sp.speak("b")
    sp.speak("c", voice="Eddy")
    assert calls == [["say", "-v", "Alex", "--", "a"], ["say", "--", "b"], ["say", "-v", "Eddy", "--", "c"]]


def test_empty_is_noop(monkeypatch):
    calls = patch(monkeypatch)
    s = sp.speak("  ")
    assert calls == [] and not s.speaking
    s.stop()
    s.wait()


def test_say_missing_never_crashes(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(sp.subprocess, "run", boom)
    monkeypatch.setattr(sp.subprocess, "Popen", boom)
    assert not sp.speak("hello").speaking
