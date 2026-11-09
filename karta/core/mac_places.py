"""Read-only questions to this Mac for the Mac map: the running apps (NSWorkspace) and an AppleScript runner the tab
reader uses (it only reads)."""

from __future__ import annotations

import subprocess

from AppKit import NSWorkspace


def _osascript(script: str) -> str:
    done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    return done.stdout.strip() if done.returncode == 0 else ""


def running_apps() -> list[str]:
    return [a.localizedName() for a in NSWorkspace.sharedWorkspace().runningApplications() if a.activationPolicy() == 0]
