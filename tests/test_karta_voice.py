import pytest

from karta.app import clean_goal, said_yes


@pytest.mark.parametrize(
    ("heard", "goal"),
    [
        ("  Open the Downloads folder.  ", "Open the Downloads folder"),
        ("start a new email!", "start a new email"),
        ("", None),
        ("uh", None),
    ],
)
def test_clean_goal(heard, goal):
    assert clean_goal(heard) == goal


@pytest.mark.parametrize(
    ("heard", "yes"),
    [("Yes.", True), ("haan", True), ("Okay, go ahead", True), ("No.", False), ("yes... no wait", False), ("hello", False)],
)
def test_only_a_clear_spoken_yes_counts(heard, yes):
    assert said_yes(heard) is yes
