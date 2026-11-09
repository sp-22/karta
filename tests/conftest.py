"""Shared fixtures and a guard that keeps every test off the real machine."""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import types

import pytest
from PIL import Image

from karta.base import macos
from karta.base.models import Screen
from karta.core import fast
from karta.harness import delegate as delegate_mod
from karta.harness import facts as facts_mod


def pytest_configure(config):
    config.addinivalue_line("markers", "v1: the test writes v1 plans (then: words): it runs with KARTA_PROMPT=v1")
    config.addinivalue_line("markers", "blind: the test is about the blind plan and its rescue: it runs with KARTA_PLAN=blind")


@pytest.fixture(autouse=True)
def plan_prompt(request, monkeypatch):
    """Tests marked v1 plan in the v1 format (kept behind KARTA_PROMPT=v1 for one release); the rest in the default."""
    if request.node.get_closest_marker("v1"):
        monkeypatch.setenv("KARTA_PROMPT", "v1")
    if request.node.get_closest_marker("blind"):
        monkeypatch.setenv("KARTA_PLAN", "blind")


@pytest.fixture(autouse=True)
def no_real_machine(monkeypatch, tmp_path_factory):
    """No test reaches the computer it runs on, whoever wrote it.

    The suite runs on the developer's own Mac, often while they use it. Every call that would
    move the pointer, press a key, run AppleScript (which opens apps and URLs), capture the
    screen, open a file, touch the clipboard, or act on another app's accessibility element refuses here, so a test
    that forgot to patch one fails instead of taking over the machine. A test that needs one
    patches it itself, after this. The pointer reads as mid-screen, never the abort corner.
    """

    def refuse(what: str):
        def call(*args, **kwargs):
            raise RuntimeError(f"a test reached the real machine through {what}; patch it in the test")

        return call

    for name in ("_post", "osascript", "screenshot", "open_path"):
        monkeypatch.setattr(macos, name, refuse(f"macos.{name}"))
    monkeypatch.setattr(macos, "mouse_location", lambda: (500.0, 500.0))
    # the user's clipboard: a test that pastes gives fast.paste a pasteboard of its own
    monkeypatch.setattr(fast, "NSPasteboard", _Refused("NSPasteboard", refuse))
    # EventKit (the calendar and its access prompt): a test that reads events puts its own fake in sys.modules
    monkeypatch.setitem(sys.modules, "EventKit", _Refused("EventKit", refuse))
    for name in ("AXUIElementPerformAction", "AXUIElementSetAttributeValue"):
        monkeypatch.setattr(macos.AS, name, refuse(f"ApplicationServices.{name}"))
    # No process of any kind, and no connection except to a server on this machine that the test
    # started itself.
    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    for name in ("system", "posix_spawn", "posix_spawnp"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, refuse(f"os.{name}"))
    _loopback_only(monkeypatch, refuse)
    # LaunchServices' mail and message handlers are this Mac's: none here, unless a test names its own
    monkeypatch.setattr(facts_mod, "message_apps", lambda: frozenset())
    # a delegated Claude Code session's folder (settings, hook events): never the user's support folder
    monkeypatch.setattr(delegate_mod, "SUPPORT", tmp_path_factory.mktemp("delegate"))


class _Refused(types.ModuleType):
    """A module every attribute of which refuses."""

    def __init__(self, name: str, refuse):
        super().__init__(name)
        self._refuse = refuse

    def __getattr__(self, attr: str):
        self._refuse(f"{self.__name__}.{attr}")()


def _is_loopback(host: object) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _loopback_only(monkeypatch, refuse) -> None:
    """Sockets reach this machine's loopback address and nothing else, so a test can talk to a
    fake server it started (the `endpoint` fixture) but never to the network. A name other than
    localhost is not even looked up."""
    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def remote(sock: socket.socket, address: object) -> bool:
        return sock.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address[0])

    def connect(sock, address):
        if remote(sock, address):
            refuse(f"a connection to {address!r}")()
        return real_connect(sock, address)

    def connect_ex(sock, address):
        if remote(sock, address):
            refuse(f"a connection to {address!r}")()
        return real_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None and not _is_loopback(host.decode() if isinstance(host, bytes) else host):
            refuse(f"a lookup of {host!r}")()
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture
def screen() -> Screen:
    return Screen(image=Image.new("RGB", (2000, 1200)), scale=2.0, app="Google Chrome", field=None, url=None)
