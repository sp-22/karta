"""Menu commands by their last part, and the pick check never vetoing an exact name (harness.md, Tools, "click"). Made-up
apps only."""

from __future__ import annotations

from test_harness_tools import BTN, FakeEnv, FakeLaya, ch, page, toolbox

from karta.harness.decide import DOES_IT, DOES_OTHER, NONE, labels
from karta.harness.tools import exact_name
from karta.harness.types import Item

UNSURE_CHECK = dict(pick_ok=lambda i, it: ch("D4 pick check", (DOES_OTHER, 0.53), (DOES_IT, 0.47)))


def menu(name: str) -> Item:
    return Item(name, "menu", menu=True)


def test_a_step_that_names_a_menu_commands_last_part_names_it_unless_it_is_risky():
    assert exact_name("New Folder", menu("File > New Folder"))
    assert not exact_name("New Folder", menu("File > New Folder with Selection"))
    assert not exact_name("Delete", menu("Edit > Delete"))  # a risky command is named only in full
    assert not exact_name("New Folder", Item("File > New Folder", "row"))  # only a menu command has a path


def test_the_menu_command_the_step_names_by_its_last_part_is_clicked_by_code_not_vetoed():
    """Laya would pick the command (D3 0.75) and its pick check is a coin toss (D4 0.53): the command was eliminated
    though it was named exactly."""
    scr = page("Filebox", Item("Desktop", "row"), Item("Documents", "row"), menu("File > New Folder"),
               menu("File > New Folder with Selection"), menu("Window > Minimize"))  # fmt: skip
    env = FakeEnv({"a": scr, "b": page("Filebox", Item("Desktop", "row"), Item("untitled folder", "field", focused=True))}, "a")
    env.on[("a", "click File > New Folder")] = "b"
    laya = FakeLaya(target=lambda i, items: ch("D3 target", (NONE, 0.6)), **UNSURE_CHECK)
    result = toolbox(env, laya).tool_click("New Folder")
    assert result.ok and env.log == ["click File > New Folder"], result


def test_the_pick_check_never_vetoes_one_of_several_copies_of_the_exact_name():
    scr = page("Postbox", Item("Archive", BTN, row="Mira lunch"), Item("Archive", BTN, row="Sam invoice"))
    env = FakeEnv({"a": scr, "b": page("Postbox", Item("Archived", "text"))}, "a", {("a", "click Archive"): "b"})

    def target(intent, items):
        first = next(iter(labels(items)))
        return ch("D3 target", (first, 0.55), (NONE, 0.3))

    laya = FakeLaya(target=target, **UNSURE_CHECK)
    result = toolbox(env, laya).tool_click("Archive")
    assert result.ok and env.log == ["click Archive"], result
    assert "pick_ok" not in laya.asked and DOES_IT
