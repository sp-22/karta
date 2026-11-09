"""Nothing unproven is kept (harness.md, "Memory", "Pinning", "Carrying data"): an answer that is not found is no
done, never pinned or remembered; a done line of kind none proves nothing; a recipe in the old words format is never
replayed, offered or pinned by a typed run; a same-shape bind that would type a stray word of the run it was pinned
from is refused. Made-up apps only."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from test_harness_carry import told
from test_harness_tools import BTN, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness import loop, recipes, tell
from karta.harness.memory import HarnessMemory
from karta.harness.recipes import Recipe, RecipeBook
from karta.harness.types import Item, MacFacts

FACTS = MacFacts(["Tasklane", "Finder"], {}, [], datetime(2026, 10, 6, 9, 0))
TASKS = page("Tasklane", Item("Due today", "heading"), Item("Pay the electricity bill", "row"), Item("Add task", BTN))


def test_the_brains_not_found_is_the_not_found_line_and_is_no_answer():
    class Brain:
        def stream_lines(self, system, user):
            assert tell.UNANSWERED in system
            return iter(["NOT FOUND."])

    hb = SimpleNamespace(lines=True, calls=0, requests=0, seconds=0.0, stats=[], brain=Brain())
    text, used = tell.word_answer("what is the first definition?", ["Site logo"], hb)
    assert text == tell.NOT_FOUND and used and not tell.answered(text)
    assert tell.answered("The bill is due today.") and not tell.answered("")


def test_a_question_whose_answer_is_not_found_is_not_done_pinned_or_remembered(monkeypatch, tmp_path):
    told(monkeypatch, [])
    memory = HarnessMemory(tmp_path / "m.sqlite")
    book = RecipeBook()
    env = FakeEnv({"t": TASKS}, "t")
    brain = FakeLineBrain(
        "open_place Tasklane\ncollect the first definition of quorp | count: 1 | as: meaning\n"
        "tell the first definition of quorp | from: meaning | as: answer",
        "NOT FOUND",
        "stop: the page has no definition",
    )
    result = loop.run_task("what is the first definition of quorp in tasklane?", env, FakeLaya(), brain, FACTS, memory, pins=book)
    assert not result.done and result.answer is None, result.trace
    assert book.recipes == [] and memory.place_for("Tasklane") is None


def test_a_done_line_of_kind_none_or_a_last_step_of_kind_none_proves_nothing():
    env = FakeEnv(
        {"a": page("Tasklane", Item("Add task", BTN)), "b": page("Tasklane", Item("Add task", BTN), Item("x", "row"))}, "a"
    )
    env.on[("a", "click Add task")] = "b"
    brain = FakeLineBrain("click Add task | expect: none")
    result = loop.run_task("add a task in tasklane", env, FakeLaya(), brain, FACTS)
    assert not result.done


def _v1_recipe() -> Recipe:
    return Recipe(
        "r1", "make a note called {p1} that says {p2}", ["p1", "p2"], {"p1": "Groceries", "p2": "buy oats"},
        [{"tool": "click", "target": "New Note", "then": "New note"}, {"tool": "type", "text": "{p1}\n{p2}"}],
        [{"kind": "ran"}, {"kind": "ran"}], "Groceries", "created(note named {p1})",
    )  # fmt: skip


def test_a_recipe_in_the_old_words_format_is_never_replayed_offered_or_pinned_by_a_typed_run():
    book = RecipeBook()
    book.recipes.append(_v1_recipe())
    assert not recipes.is_typed(book.recipes[0].steps, book.recipes[0].done_shows)
    assert book.match("make a note called Groceries that says buy oats") is None
    assert book.offer("make a note called Chores that says sweep") == []
    assert "no pinned task" in book.bind_use("r1 | p1: Chores | p2: sweep")[1]
    trail = [({"tool": "click", "target": "New Note"}, {"kind": "ran"})]
    assert book.after("make a note in Pebble", None, True, True, trail, "", contract="created(note)") == ("", "")
    book.typed = False  # a v1 run still replays its own format
    assert book.match("make a note called Groceries that says buy oats") is not None


def test_a_same_shape_bind_that_would_type_a_stray_word_of_its_first_run_is_refused():
    book = RecipeBook()
    stray = Recipe(
        "r1", "make a note called {p1} that says {p2} all", ["p1", "p2"], {"p1": "Groceries", "p2": "buy oats"},
        [{"tool": "click", "target": "New Note", "expects": "new_item"}, {"tool": "type", "text": "{p1}\nall {p2}"}],
        [{"kind": "expect", "type": "new_item"}, {"kind": "ran"}], "new_item({p1})", "created(note named {p1})",
    )  # fmt: skip
    book.recipes.append(stray)
    hit, error = book.bind_use("r1 | p1: Chores | p2: sweep", "make a note called Chores that says sweep")
    assert hit is None and "'all'" in error
    hit, error = book.bind_use("r1 | p1: Chores | p2: sweep", "make a note called Chores that says sweep all")
    assert hit is not None and error is None
