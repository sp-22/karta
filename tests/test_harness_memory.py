"""Harness v2 memory: small facts with good/bad counts, dropped when they keep failing, capped per app."""

import pytest

from karta.harness import memory as memory_module
from karta.harness.memory import HarnessMemory


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        self.t += 1
        return self.t


@pytest.fixture
def mem(tmp_path):
    return HarnessMemory(tmp_path / "sub" / "harness.sqlite", clock=Clock())


def test_a_place_that_worked_is_remembered_across_opens(tmp_path):
    path = tmp_path / "harness.sqlite"
    HarnessMemory(path).place_result("WhatsApp chats", "web.whatsapp.com in Brave Browser, profile Personal", True)
    assert HarnessMemory(path).place_for("whatsapp  Chats") == "web.whatsapp.com in Brave Browser, profile Personal"
    assert HarnessMemory(path).place_for("Linear issues") is None


def test_a_place_that_keeps_failing_is_dropped(mem):
    mem.place_result("chats", "A", True)
    mem.place_result("chats", "A", False)
    assert mem.place_for("chats") is None  # as often wrong as right: not offered
    mem.place_result("chats", "A", False)
    mem.place_result("chats", "A", False)  # two more failures than successes: forgotten
    assert mem.db.execute("select count(*) from harness_facts").fetchone() == (0,)
    mem.place_result("chats", "A", True)  # counted afresh: the old row was dropped
    assert mem.place_for("chats") == "A"
    rows = mem.db.execute("select good, bad from harness_facts").fetchall()
    assert rows == [(1, 0)]


def test_the_better_place_wins(mem):
    mem.place_result("chats", "A", True)
    for _ in range(3):
        mem.place_result("chats", "B", True)
    assert mem.place_for("chats") == "B"


def test_aliases_are_learned_and_unlearned(mem):
    mem.remember_alias("Hemansu", "Hemanshu Patel")
    assert mem.alias_for("hemansu") == "Hemanshu Patel"
    mem.alias_result("Hemansu", "Hemanshu Patel", False)
    assert mem.alias_for("Hemansu") is None
    mem.remember_alias("Rahul", "Rahul Shah", app="WhatsApp")
    assert mem.alias_for("Rahul", app="WhatsApp") == "Rahul Shah" and mem.alias_for("Rahul") is None


def test_know_how_is_capped_per_app_and_a_new_fact_gets_its_chance(mem, monkeypatch):
    monkeypatch.setitem(memory_module.CAPS, "know-how", 3)
    for fact in ("a", "b", "c"):
        mem.add_know_how("Reminders", fact)
        mem.add_know_how("Reminders", fact)
    mem.add_know_how("Reminders", "new")
    mem.add_know_how("Notes", "select a folder before New Note")
    tips = mem.know_how("Reminders")
    assert len(tips) == 3 and "new" in tips and "a" not in tips  # the oldest of the least useful went
    assert mem.know_how("Notes") == ["select a folder before New Note"]
    assert mem.know_how("Reminders", limit=1) == ["c"]


def test_misleading_know_how_is_forgotten(mem):
    mem.add_know_how("Reminders", "set the date in the title")
    mem.know_how_result("Reminders", "set the date in the title", False)
    mem.know_how_result("Reminders", "set the date in the title", False)
    mem.know_how_result("Reminders", "set the date in the title", False)
    assert mem.know_how("Reminders") == []


def test_places_are_capped_per_intent(mem, monkeypatch):
    monkeypatch.setitem(memory_module.CAPS, "place", 2)
    for place in ("A", "B", "C"):
        mem.place_result("chats", place, True)
    mem.place_result("mail", "Gmail", True)
    kept = {r[0] for r in mem.db.execute("select value from harness_facts where key = 'chats'")}
    assert len(kept) == 2 and "C" in kept
    assert mem.place_for("mail") == "Gmail"


def test_wrong_elements_stay_remembered_as_wrong(mem):
    for _ in range(3):
        mem.element_result("chatter.example", "the search box", "Find people", False)
    mem.element_result("chatter.example", "the search box", "Search or start a new chat", True)
    assert mem.bad_elements("chatter.example", "the search box") == {"Find people"}
    assert mem.element_for("chatter.example", "The search box") == "Search or start a new chat"


def test_in_memory_database_works():
    mem = HarnessMemory(":memory:")
    mem.place_result("x", "y", True)
    assert mem.place_for("x") == "y"


# ------------------------------------------------------------------ only what worked on disk, ageing, caps

import sqlite3  # noqa: E402

from karta.harness.memory import DAY, ELEMENT, KNOW_HOW, PLACE, SessionNotes  # noqa: E402


class SetClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def rows(mem):
    return mem.db.execute("select kind, app, key, value, good, bad from harness_facts order by kind, key, value").fetchall()


def test_a_wrong_fact_is_deleted_at_once_not_recorded_as_a_failure(mem):
    for _ in range(4):
        mem.place_result("team chat", "app.example.com in Safari", True)
    mem.failed(PLACE, "", "Team Chat", "app.example.com in Safari", reason="a login page")
    assert rows(mem) == []  # however often it worked: one failure and it is gone
    mem.failed(PLACE, "", "team chat", "never remembered")
    assert rows(mem) == []  # a failure of something not remembered writes nothing
    assert mem.notes.lines() == [
        "place 'team chat' -> 'never remembered' failed",
        "place 'team chat' -> 'app.example.com in Safari' failed: a login page",
    ]


def test_failures_live_in_the_session_only(tmp_path):
    path = tmp_path / "m.sqlite"
    notes = SessionNotes()
    mem = HarnessMemory(path, notes=notes)
    mem.element_result("Figma", "the share button", "Share to Community", False)
    assert mem.bad_elements("Figma", "The share button") == {"Share to Community"}
    assert HarnessMemory(path, notes=notes).bad_elements("Figma", "the share button") == {"Share to Community"}
    assert HarnessMemory(path).bad_elements("Figma", "the share button") == set()  # a new process knows nothing
    assert sqlite3.connect(path).execute("select count(*) from harness_facts").fetchone() == (0,)


def test_old_failure_rows_are_dropped_on_open(tmp_path):
    path = tmp_path / "m.sqlite"
    HarnessMemory(path).element_result("Zoom", "mute", "Mute", True)
    db = sqlite3.connect(path)
    db.execute(
        "insert into harness_facts (kind, app, key, value, good, bad, updated) values ('element', 'Zoom', 'mute', 'Leave', 0, 3, 5)"
    )
    db.commit()
    assert [f.value for f in HarnessMemory(path).list()] == ["Mute"]


def test_stale_facts_rank_below_fresh_ones_and_expire(tmp_path):
    clock = SetClock()
    mem = HarnessMemory(tmp_path / "m.sqlite", clock=clock)
    for _ in range(5):
        mem.place_result("music", "Spotify app", True)
    clock.t += 61 * DAY
    mem.place_result("music", "open.spotify.com in Safari", True)
    assert mem.place_for("music") == "open.spotify.com in Safari"  # used 5 times, but not for 61 days
    assert {f.value: f.stale for f in mem.list(PLACE)} == {"Spotify app": True, "open.spotify.com in Safari": False}
    clock.t += 120 * DAY  # the app's fact is now 181 days unused
    mem.add_know_how("Spotify", "the queue is under the now-playing bar")
    assert [f.value for f in mem.list(PLACE)] == ["open.spotify.com in Safari"]
    clock.t += 200 * DAY
    assert HarnessMemory(tmp_path / "m.sqlite", clock=clock).list() == []  # deleted on open


def test_stale_know_how_ranks_last(tmp_path):
    clock = SetClock()
    mem = HarnessMemory(":memory:", clock=clock)
    for _ in range(3):
        mem.add_know_how("Figma", "old tip")
    clock.t += 70 * DAY
    mem.add_know_how("Figma", "new tip")
    assert mem.know_how("Figma") == ["new tip", "old tip"]


def test_the_whole_file_is_capped(mem, monkeypatch):
    monkeypatch.setattr(memory_module, "MAX_ROWS", 3)
    for n in range(3):
        mem.element_result(f"App{n}", "go", "Go", True)
    mem.element_result("App0", "go", "Go", True)  # the most useful stays
    mem.element_result("App9", "go", "Go", True)  # the new fact stays
    kept = {f.app for f in mem.list()}
    assert kept == {"App0", "App2", "App9"}  # the least useful, oldest went


def test_list_forget_clear(mem):
    mem.remember_alias("Kav", "Kavya Iyer", app="Signal")
    mem.remember_alias("Kav", "Kavin Raj", app="Signal")
    mem.add_know_how("Signal", "note to self is at the top")
    mem.place_result("chat", "Signal app", True)
    assert [f.kind for f in mem.list()] == ["alias", "alias", "know-how", "place"]
    assert [f.value for f in mem.list(app="Signal", kind="alias")] == ["Kavin Raj", "Kavya Iyer"]
    assert mem.forget("alias", "Signal", "KAV", value="Kavin Raj") == 1
    assert mem.alias_for("kav", app="Signal") == "Kavya Iyer"
    assert mem.forget(KNOW_HOW, "Signal", " note to self is at the top ") == 1
    assert mem.places() == ["Signal app"]
    assert mem.clear() == 2 and mem.list() == []


def test_session_notes_are_bounded_and_per_app():
    notes = SessionNotes(limit=3)
    notes.add(ELEMENT, "Figma", "The share button", "Share to Community", "opened the wrong dialog")
    notes.add(PLACE, "", "design files", "figma.com in Safari", "a login page")
    notes.add(ELEMENT, "Zoom", "mute", "Leave")
    assert notes.tried(ELEMENT, "Figma", "the share button") == {"Share to Community"}
    assert [n.app for n in notes.for_app("Figma")] == ["", "Figma"]
    assert (
        notes.lines(app="Figma")[1] == "Figma: element 'the share button' -> 'Share to Community' failed: opened the wrong dialog"
    )
    notes.add(ELEMENT, "Zoom", "video", "Stop Video")
    assert len(notes) == 3 and notes.tried(ELEMENT, "Figma", "the share button") == set()
    notes.clear()
    assert notes.lines() == []


def test_readonly_opens_without_writing(tmp_path):
    path = tmp_path / "m.sqlite"
    clock = SetClock()
    HarnessMemory(path, clock=clock).place_result("x", "y", True)
    clock.t += 365 * DAY
    ro = HarnessMemory(path, clock=clock, readonly=True)
    assert [f.stale for f in ro.list()] == [True]  # not expired: nothing is written
    with pytest.raises(sqlite3.OperationalError):
        ro.place_result("a", "b", True)


# ------------------------------------------------------------------ scripts/karta_memory.py


def cli(*args):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("karta_memory", Path(__file__).parents[1] / "scripts" / "karta_memory.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.main(list(args))


def test_the_cli_shows_forgets_and_clears(tmp_path, capsys):
    path = tmp_path / "m.sqlite"
    assert cli("--path", str(path), "show") == 0
    assert "No memory file" in capsys.readouterr().out and not path.exists()  # show never creates the file
    mem = HarnessMemory(path)
    mem.remember_alias("Kav", "Kavya Iyer", app="Signal")
    mem.add_know_how("Signal", "note to self is at the top")
    cli("--path", str(path), "show", "--kind", "alias")
    out = capsys.readouterr().out
    assert "Signal: kav -> Kavya Iyer" in out and "1 fact(s)" in out
    cli("--path", str(path), "forget", "alias", "kav", "--app", "Signal")
    assert "Forgot 1" in capsys.readouterr().out
    cli("--path", str(path), "clear")
    assert "Cleared 1" in capsys.readouterr().out


def test_the_cli_shows_and_unpins_pinned_tasks(tmp_path, capsys):
    import time

    from karta.harness.recipes import FILE, Recipe, RecipeBook

    now = time.time()
    path = tmp_path / "m.sqlite"
    cli("--path", str(path), "pins")
    assert "No pinned tasks" in capsys.readouterr().out
    book = RecipeBook(tmp_path / FILE)
    steps = [{"tool": "open_place", "what": "Listly"}, {"tool": "write", "text": "{p1}", "where": "new item"}]
    book.recipes += [
        Recipe("r1", "add {p1} to my list in Listly", ["p1"], {"p1": "oat milk"}, steps, [{"kind": "ran"}] * 2, used=now),
        Recipe("r2", "water the ferns note", [], {}, steps[:1], [{"kind": "ran"}], name="ferns", used=now),
    ]
    book.save()
    cli("--path", str(path), "pins")
    out = capsys.readouterr().out
    assert "r1 'add {p1} to my list in Listly'" in out and "r2 [ferns]" in out and "2 pinned task(s)" in out
    assert "oat milk" not in out  # templates only, never their values
    cli("--path", str(path), "unpin", "ferns")
    assert "Unpinned r2" in capsys.readouterr().out
    cli("--path", str(path), "unpin", "r1")
    cli("--path", str(path), "unpin", "r9")
    assert "No pinned task 'r9'" in capsys.readouterr().out
    assert RecipeBook(tmp_path / FILE).recipes == []


def test_memory_opened_on_one_thread_is_used_by_another(tmp_path):
    """Voice mode opens memory on the main thread and runs tasks on the screen worker."""
    import threading

    memory = HarnessMemory(tmp_path / "harness.sqlite", clock=Clock())
    memory.place_result("WhatsApp chats", "web.whatsapp.com in Brave Browser, profile Personal", True)
    seen = {}
    worker = threading.Thread(
        target=lambda: seen.update(place=memory.place_for("whatsapp chats"), named=memory.places_named("whatsapp"))
    )
    worker.start()
    worker.join()
    assert seen["place"] == "web.whatsapp.com in Brave Browser, profile Personal"
