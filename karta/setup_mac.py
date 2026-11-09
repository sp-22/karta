"""`karta setup`: everything Karta needs on this Mac, checked and fixed one step at a time. Run it once after
installing; running it again only repeats what is still missing.

    1. this Mac: Apple silicon, macOS
    2. permissions for the app Karta runs in (the terminal): Accessibility, Screen Recording, Input Monitoring
       (the talk key) and the Microphone; each missing one is asked for, and its Settings pane opened. Calendar
       access is optional and shown only: the first question about a day's events asks for it
    3. whisper.cpp's server (Homebrew) and its speech model, downloaded into Application Support
    4. Laya, the local decision model (downloaded from Hugging Face on first use, then cached)
    5. the brain: the Claude Code CLI
    6. a first map of this Mac and a short self-test

Nothing here clicks, types or opens apps (only System Settings, when a permission is missing). The result is
written to Application Support/karta/setup.json; `karta start` checks it.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

SUPPORT = Path.home() / "Library" / "Application Support" / "karta"
DONE_FILE = SUPPORT / "setup.json"
SETTINGS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_{}"


@dataclass
class Report:
    ok: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)

    def good(self, what: str) -> None:
        self.ok.append(what)
        print(f"  ✓ {what}", flush=True)

    def bad(self, what: str, fix: str) -> None:
        self.todo.append(f"{what}: {fix}")
        print(f"  ✗ {what}\n      → {fix}", flush=True)

    def optional(self, what: str, note: str) -> None:
        """Not needed to run: shown, never a thing left to do."""
        print(f"  ○ {what} (optional): {note}", flush=True)


def step(title: str) -> None:
    print(f"\n{title}", flush=True)


def open_pane(name: str) -> None:
    subprocess.run(["open", SETTINGS_PANE.format(name)], check=False)


# ---------------------------------------------------------------- the steps


def check_mac(r: Report) -> bool:
    step("1. This Mac")
    if sys.platform != "darwin":
        r.bad("macOS", "Karta runs on macOS only")
        return False
    if platform.machine() != "arm64":
        r.bad("Apple silicon", "Laya runs on MLX, which needs an Apple silicon Mac (M1 or later)")
        return False
    r.good(f"macOS {platform.mac_ver()[0]} on Apple silicon")
    return True


def check_permissions(r: Report) -> None:
    step("2. Permissions (for the app Karta runs in)")
    import ApplicationServices as AS
    import Quartz

    if AS.AXIsProcessTrusted():
        r.good("Accessibility: Karta can read and press controls")
    else:
        AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
        open_pane("Accessibility")
        r.bad("Accessibility", "turn it on for your terminal in the Settings pane just opened, then run `karta setup` again")
    if Quartz.CGPreflightScreenCaptureAccess():
        r.good("Screen Recording: Karta can read the screen")
    else:
        Quartz.CGRequestScreenCaptureAccess()
        open_pane("ScreenCapture")
        r.bad("Screen Recording", "turn it on for your terminal, restart the terminal, then run `karta setup` again")
    if Quartz.CGPreflightListenEventAccess():
        r.good("Input Monitoring: the talk key (Right Option) works")
    else:
        Quartz.CGRequestListenEventAccess()
        open_pane("ListenEvent")
        r.bad("Input Monitoring", "turn it on for your terminal (needed only for the talk key), then run `karta setup` again")
    try:  # opening the mic asks for it the first time; a denied mic records silence rather than failing
        import sounddevice as sd

        with sd.InputStream(samplerate=16000, channels=1, dtype="float32"):
            time.sleep(0.2)
        r.good("Microphone: opened (if macOS asked, allow it for your terminal)")
    except Exception as error:
        open_pane("Microphone")
        r.bad("Microphone", f"could not open it ({error}); allow it for your terminal")
    check_calendar(r)


def check_calendar(r: Report) -> None:
    """Optional, never asked for here: the first question about a day's events asks (the system's own prompt)."""
    try:
        import EventKit as EK

        status = EK.EKEventStore.authorizationStatusForEntityType_(EK.EKEntityTypeEvent)
        full, unasked = EK.EKAuthorizationStatusFullAccess, EK.EKAuthorizationStatusNotDetermined
    except Exception:
        status, full, unasked = None, 3, 0
    if status == full:
        r.good("Calendar (optional): Karta answers what's on your calendar from your calendars, read-only")
    elif status == unasked:
        r.optional("Calendar", "Karta asks the first time you ask what's on your calendar; without it, it reads the screen")
    else:
        r.optional("Calendar", "off: Karta reads the Calendar app's screen instead (allow your terminal under Calendars)")


def check_whisper(r: Report) -> None:
    step("3. Voice: whisper.cpp and its speech model")
    from karta.voice import config

    if shutil.which("whisper-server"):
        r.good("whisper-server installed")
    elif shutil.which("brew"):
        print("  installing whisper-cpp with Homebrew…", flush=True)
        done = subprocess.run(["brew", "install", "whisper-cpp"], check=False)
        if done.returncode == 0 and shutil.which("whisper-server"):
            r.good("whisper-server installed (Homebrew)")
        else:
            r.bad("whisper-server", "`brew install whisper-cpp` failed; run it yourself and check its output")
    else:
        r.bad("whisper-server", "install Homebrew (https://brew.sh), then run `karta setup` again")
    model = config.WHISPER_MODEL
    if model.exists():
        r.good(f"speech model ready ({model.stat().st_size // 1_000_000} MB, {model})")
        return
    model.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading the speech model to {model} …", flush=True)
    try:
        download(config.WHISPER_URL, model)
        r.good(f"speech model downloaded ({model.stat().st_size // 1_000_000} MB)")
    except Exception as error:
        r.bad("speech model", f"download failed ({error}); check the connection and run `karta setup` again")


def download(url: str, to: Path) -> None:
    part = to.with_suffix(to.suffix + ".part")
    with urllib.request.urlopen(url, timeout=30) as response, open(part, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        got, shown = 0, -1
        while chunk := response.read(1 << 20):
            out.write(chunk)
            got += len(chunk)
            pct = got * 100 // total if total else -1
            if pct != shown and pct % 10 == 0:
                shown = pct
                print(f"    {pct}%", flush=True)
    part.rename(to)  # only a complete file takes the model's name


def check_laya(r: Report) -> None:
    step("4. Laya, the local decision model")
    print("  loading (the first time downloads it from Hugging Face, about 850 MB)…", flush=True)
    try:
        started = time.perf_counter()
        from karta.core.matcher import LayaMatcher
        from karta.harness.decide import Laya

        matcher = LayaMatcher()
        Laya(matcher.agent, matcher.embed)
        r.good(f"Laya loaded in {time.perf_counter() - started:.1f}s")
    except Exception as error:
        r.bad("Laya", f"could not load it ({error}); check the connection and run `karta setup` again")


def check_brain(r: Report) -> None:
    step("5. The brain: Claude Code")
    exe = shutil.which("claude")
    if not exe:
        r.bad(
            "Claude Code CLI",
            "install it (https://docs.claude.com/en/docs/claude-code), sign in once with `claude`, then run `karta setup` again",
        )
        return
    version = subprocess.run([exe, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    r.good(f"Claude Code {version or 'installed'} (Karta uses your Claude sign-in; run `claude` once if you never signed in)")


def check_map(r: Report) -> None:
    step("6. A first map of this Mac")
    try:
        from karta.harness import facts
        from karta.harness.memory import HarnessMemory, SessionNotes

        SUPPORT.mkdir(parents=True, exist_ok=True)
        mac = facts.mac_map(HarnessMemory(SUPPORT / "harness-memory.sqlite", notes=SessionNotes()))
        r.good(
            f"{len(mac.facts.installed_apps)} apps, {len(mac.facts.browsers)} browsers, "
            f"{len(mac.facts.folders)} folders (the map stays on this Mac)"
        )
    except Exception as error:
        r.bad("Mac map", f"could not build it ({error})")


def run_setup() -> int:
    print("Karta setup: checking this Mac. Nothing is clicked or typed.")
    r = Report()
    if not check_mac(r):
        return 1
    check_permissions(r)
    check_whisper(r)
    check_laya(r)
    check_brain(r)
    check_map(r)
    SUPPORT.mkdir(parents=True, exist_ok=True)
    DONE_FILE.write_text(json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "ok": r.ok, "todo": r.todo}, indent=1))
    if r.todo:
        print(f"\n{len(r.todo)} thing(s) left: fix them as shown above, then run `karta setup` again.")
        return 1
    print("\nAll set. Start Karta with:  karta start   (hold Right Option, say a task, let go)")
    return 0


def setup_state() -> dict | None:
    """What the last `karta setup` found, or None if it never ran."""
    try:
        return json.loads(DONE_FILE.read_text())
    except (OSError, ValueError):
        return None
