"""What Karta is doing, as events, for anything that wants to show it (the HUD). Never affects a run.

heard(text)                   the command, as Karta took it
planning()                    asking the planner
plan(steps)                   the plan it will run
step(text)                    a step is starting
step_done(text, outcome)      ok, no_match, no_change, unexpected, refused
replan(why)                   a step or the objective failed; asking again
pick(label, how, p, instead_of?, frame?)  harness v2: the item a click picked, and how (Laya's p)
retry(wrong, why, attempt, of)   harness v2: that pick was wrong; undone, trying the next-best
move(text)                    open_place left a place for another, with its one-line reason
done(ok, reason)              the run is over
"""

from __future__ import annotations

import contextlib
import json
import socket
from collections.abc import Callable

Listener = Callable[[dict], None]
_listeners: list[Listener] = []

HUD_PORT = 47811


def subscribe(listener: Listener) -> None:
    _listeners.append(listener)


def emit(kind: str, **data) -> None:
    event = {"kind": kind, **data}
    for listener in list(_listeners):
        with contextlib.suppress(Exception):  # a display must never break a run
            listener(event)


def to_hud(port: int = HUD_PORT) -> None:
    """Send every event to the HUD process over localhost UDP. Fire and forget: no HUD, no problem."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(event: dict) -> None:
        sock.sendto(json.dumps(event).encode(), ("127.0.0.1", port))

    subscribe(send)
