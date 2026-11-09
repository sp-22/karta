"""Dialogs read by structure: a permission prompt is an allow/block choice (or the browser's own permission UI), never
a form window that happens to sit over a page with a banner; one more dialog than before counts as one opened even
when it shares its title with one open already; a fill finds "To recipients" for "To" and types into the dialog in use.
Made-up apps and sites only."""

from __future__ import annotations

from dataclasses import dataclass

from karta.harness import expect, facts
from karta.harness.tools import Toolbox, names_toggle
from karta.harness.types import Item, Outcome, Screen
from tests.test_harness_tools import FACTS, FakeEnv, FakeLaya, ch, toolbox

BRAVE = "Brave Browser"


@dataclass
class At:
    x: float
    y: float
    w: float
    h: float

    @property
    def centre(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2


def compose(x: float, focused: bool = False) -> list[Item]:
    """One in-page message window: window controls, recipients, subject, body, send."""
    return [
        Item("Draft", "text", region="middle", dialog=True, source="ocr", ref=At(x, 300, 200, 20)),
        Item("Minimize", "button", region="middle", dialog=True, ref=At(x + 300, 300, 20, 20)),
        Item("Full screen", "button", region="middle", dialog=True, ref=At(x + 330, 300, 20, 20)),
        Item("Save & close", "button", region="middle", dialog=True, ref=At(x + 360, 300, 20, 20)),
        Item("To recipients", "field", region="middle", dialog=True, focused=focused, ref=At(x, 340, 400, 30)),
        Item("Subject", "field", region="middle", dialog=True, ref=At(x, 380, 400, 30)),
        Item("Message Body", "field", region="bottom", dialog=True, multiline=True, ref=At(x, 420, 400, 200)),
        Item("Send", "button", region="bottom", dialog=True, ref=At(x, 640, 60, 30)),
    ]


BANNER = [  # the page behind asks, in a banner of its own, for desktop alerts
    Item("Enable desktop notifications for Mailbox.", "text", region="bottom-left", source="ocr"),
    Item("OK", "link", region="bottom-left"),
    Item("No thanks", "link", region="bottom-left"),
]
INBOX = [Item("Write", "button", region="top-left"), Item("Inbox", "link", region="left")]


def mail(*windows: list[Item]) -> Screen:
    return Screen(BRAVE, [*INBOX, *BANNER, *(i for w in windows for i in w)], url="https://mailbox.example/inbox")


def test_a_form_window_over_a_page_with_an_alerts_banner_is_no_permission_prompt():
    scr = mail(compose(600, focused=True))
    assert facts.page_fact(scr) is Outcome.BLOCKED_BY_DIALOG  # the page's own window: no permission prompt
    asked: list[str] = []
    tb = toolbox(FakeEnv({"a": scr}, "a"), confirm=lambda q: asked.append(q) or False)
    result = tb.clear_dialog({"tool": "click", "target": "Write"})
    assert result is None or result.outcome is not Outcome.NEEDS_PERMISSION
    assert not any(q.startswith("permission") for q in asked)


def test_a_real_permission_prompt_still_is_one():
    bubble = [
        Item("clips.example wants to show notifications", "text", dialog=True, browser_ui=True),
        Item("Allow", "button", dialog=True, browser_ui=True),
        Item("Block", "button", dialog=True, browser_ui=True),
    ]
    assert facts.page_fact(Screen(BRAVE, bubble)) is Outcome.NEEDS_PERMISSION  # the browser's own: by who owns it
    ask = [Item("Tasklet would like to access your calendar.", "text", dialog=True),
           Item("Don't Allow", "button", dialog=True), Item("OK", "button", dialog=True)]  # fmt: skip
    assert facts.page_fact(Screen("UserNotificationCenter", ask)) is Outcome.NEEDS_PERMISSION  # the system's prompter
    assert facts.page_fact(Screen("Tasklet", ask)) is Outcome.BLOCKED_BY_DIALOG  # an app's own sheet: its words aside


def test_one_more_dialog_of_the_same_name_counts_as_one_opened():
    one = mail(compose(600))
    two = mail(compose(100, focused=True), compose(600))
    # OCR read the two titles as one line: the title count did not rise, the window count did
    two.items = [i for i in two.items if not (i.name == "Draft" and i.ref.x == 100)]
    assert facts.dialogs(one) == 1 and facts.dialogs(two) == 2
    assert facts.shows_new(two, one, "Draft")
    assert expect.verify(expect.Expect("dialog_opened"), one, two).ok
    assert not facts.shows_new(one, one, "Draft")  # the same window, still open: nothing new


def test_fill_to_finds_to_recipients_in_the_window_in_use():
    typed: list[Item] = []

    class Env(FakeEnv):
        def set_text(self, item: Item, text: str) -> None:
            typed.append(item)
            self.pages[self.at].items = [replace_value(i, item, text) for i in self.pages[self.at].items]
            self._go(f"set {item.name}={text}")

    scr = mail(compose(100, focused=True), compose(600))
    tb = toolbox(Env({"a": scr}, "a"))
    result = tb.tool_fill({"To": "mira@example.com", "Subject": "Weekend plans"})
    assert result.ok, result
    assert [i.name for i in typed] == ["To recipients", "Subject"]
    assert all(i.ref.x == 100 for i in typed)  # the focused window's fields, never the other one's
    assert tb.last_write == ("Subject", "Weekend plans")


def replace_value(i: Item, target: Item, text: str) -> Item:
    from dataclasses import replace

    return replace(i, value=text) if i.ref is target.ref else i


def test_a_list_rows_checkbox_holding_the_word_is_not_the_field():
    row = Item("unread, Garden club, A note to members about Sunday, Oct 3", "checkbox", region="middle")
    assert not names_toggle("To", row)
    assert names_toggle("Time", Item("Time zone", "checkbox"))
    assert names_toggle("signed in", Item("Keep me signed in", "checkbox"))
    env = FakeEnv({"a": Screen(BRAVE, [*INBOX, row])}, "a")
    result = toolbox(env).tool_fill({"To": "mira@example.com"})
    assert result.outcome is Outcome.NO_FIELD and env.log == []  # nothing clicked


def test_copies_without_frames_take_the_focused_windows_region():
    left = [Item("To recipients", "field", region="left", dialog=True), Item("Subject", "field", region="left", dialog=True)]
    right = [Item("To recipients", "field", region="right", dialog=True, focused=True),
             Item("Subject", "field", region="middle", dialog=True)]  # fmt: skip
    scr = Screen(BRAVE, [*left, *right])
    tb = Toolbox("t", FakeEnv({"a": scr}, "a"), FakeLaya(), FACTS)
    assert tb.named_field(scr, "Subject").region == "middle"


# ---------------------------------------------------------------- only real dialogs are answered or cleared

SIDE_CART = [  # a page's side cart: a web dialog role without aria-modal, so a panel (mac.blocks), never in the way
    Item("Your basket", "heading", region="right", dialog=True, panel=True),
    Item("Decrease quantity by one", "button", region="right", dialog=True, panel=True),
    Item("Increase quantity by one", "button", region="right", dialog=True, panel=True),
    Item("Go to basket", "link", region="right", dialog=True, panel=True),
    Item("Under 20", "checkbox", region="right", dialog=True, panel=True),
    Item("Apply filter", "button", region="right", dialog=True, panel=True),
    Item("Close", "button", region="right", dialog=True, panel=True),
]


class AnsweringLaya(FakeLaya):
    """Would gladly answer: its dialog role says "part of the step" and its answer is the first option, sure."""

    def dialog_role(self, intent, items):
        from karta.harness.decide import PART_OF_STEP
        from tests.test_harness_tools import ch

        self.asked.append("dialog_role")
        return ch("D6 dialog role", (PART_OF_STEP, 0.95))


def test_a_side_panel_with_quantity_and_filters_is_never_answered_and_nothing_in_it_is_clicked():
    shop = Screen(BRAVE, [Item("Pebble lamp", "heading"), Item("Add to basket", "button"), *SIDE_CART], "https://lamps.example/")
    assert facts.blocking_dialog(shop) is None and facts.in_the_way(shop) == []
    assert facts.page_fact(shop) is None
    env = FakeEnv({"a": shop}, "a")
    laya = AnsweringLaya()
    tb = toolbox(env, laya, task="add the pebble lamp to the basket")
    assert tb.clear_dialog({"tool": "click", "target": "Add to basket"}) is None
    assert tb.clear_dialog(None) is None  # the end of a plan: nothing to clear either
    assert tb.tool_answer("add the pebble lamp to the basket").outcome is Outcome.NOT_FOUND
    assert env.log == [] and "dialog_role" not in laya.asked and "dialog_answer" not in laya.asked


def test_a_real_modal_not_part_of_the_task_is_cleared_by_its_own_cancel_control():
    modal = [
        Item("Leave this page?", "text", dialog=True),
        Item("Leave", "button", dialog=True),
        Item("Stay", "button", dialog=True, cancels=True),  # the dialog's AXCancelButton: no word says it
    ]
    env = FakeEnv(
        {"a": Screen("Quillnote", [Item("Notes", "heading"), *modal]), "b": Screen("Quillnote", [Item("Notes", "heading")])},
        "a",
        {("a", "click Stay"): "b"},
    )
    laya = FakeLaya()
    result = toolbox(env, laya, task="open my notes").clear_dialog({"tool": "click", "target": "Notes"})
    assert result.ok and env.log == ["click Stay"] and "dialog_answer" not in laya.asked


def test_a_real_modal_with_no_way_out_by_name_is_closed_with_escape_never_answered():
    modal = [
        Item("Rate this app", "heading", dialog=True),
        Item("★★★★★", "button", dialog=True),
        Item("★", "button", dialog=True),
    ]
    env = FakeEnv(
        {"a": Screen("Quillnote", [Item("Notes", "heading"), *modal]), "b": Screen("Quillnote", [Item("Notes", "heading")])},
        "a",
        {("a", "press escape"): "b"},
    )
    unsure = FakeLaya(dialog_answer=lambda t, o: ch("D6 dialog answer", ("★", 0.4), ("★★★★★", 0.35)))
    result = toolbox(env, unsure).clear_dialog({"tool": "click", "target": "Notes"})
    assert result.ok and env.log == ["press escape"]  # Laya not sure which closes it: escape, never a guess


def test_laya_never_answers_a_real_dialog_with_a_quantity_a_filter_or_a_setting_on_a_guess():
    modal = [
        Item("Added to your basket", "heading", dialog=True),
        Item("Increase quantity by one", "button", dialog=True),
        Item("Gift wrap", "checkbox", dialog=True),
        Item("Under 20", "link", dialog=True),
    ]
    env = FakeEnv({"a": Screen(BRAVE, [Item("Pebble lamp", "heading"), *modal], "https://lamps.example/")}, "a")
    offered: list[list[str]] = []

    def answer(task, options):
        from karta.harness.decide import labels
        from tests.test_harness_tools import ch

        offered.append([o.name for o in options])
        return ch("D6 dialog answer", (next(iter(labels(options))), 0.8))

    class StrictLaya(FakeLaya):  # as decide.Laya: the risky tier needs more than a sure answer
        def acts(self, choice, level="reversible"):
            return choice.ranked[0][1] >= (0.95 if level == "risky" else choice.gate)

    tb = toolbox(env, StrictLaya(dialog_answer=answer), task="add the pebble lamp to the basket")
    result = tb.tool_answer("add the pebble lamp to the basket")
    assert result.outcome is Outcome.UNSURE and env.log == []
    assert offered == [["Increase quantity by one"]]  # the checkbox and the link are never offered to Laya
    toolbox(env, StrictLaya(dialog_answer=answer)).tool_answer("Gift wrap")  # the step names it: taken
    assert [a for a in env.log if a != "wait"] == ["click Gift wrap"]  # and after it, still no guessed quantity


# ---------------------------------------------------------------- permission prompts by who owns them

ZUGRIFF = [  # a system privacy prompt in German, put up by the system for another app
    Item("„Quillcam“ möchte auf die Kamera zugreifen.", "text", dialog=True),
    Item("Nicht erlauben", "button", dialog=True, cancels=True),
    Item("OK", "button", dialog=True),
]


def test_a_non_english_system_permission_prompt_always_takes_the_users_answer():
    prompt = Screen("UserNotificationCenter", ZUGRIFF)
    assert facts.system_prompt(prompt) and facts.page_fact(prompt) is Outcome.NEEDS_PERMISSION
    for yes, clicked in ((False, "click Nicht erlauben"), (True, "click OK")):
        env = FakeEnv({"a": prompt, "b": Screen("Quillcam", [Item("Record", "button")])}, "a", {("a", clicked): "b"})
        laya = AnsweringLaya()
        tb = toolbox(env, laya, task="record a clip in quillcam", confirm=lambda q, yes=yes: yes)
        result = tb.clear_dialog({"tool": "click", "target": "Record"})
        assert env.log == [clicked] and result.ok and len(tb.confirmations) == 1
        assert "dialog_role" not in laya.asked and "dialog_answer" not in laya.asked
    # a plan's own answer step never grants it either: the user is asked
    env = FakeEnv({"a": prompt}, "a")
    tb = toolbox(env, AnsweringLaya(), confirm=lambda q: False)
    tb.tool_answer("OK")
    assert [a for a in env.log if a != "wait"] == ["click Nicht erlauben"] and tb.confirmations


def test_a_system_prompt_in_front_is_not_drift():
    from karta.harness.facts import app_place

    env = FakeEnv({"a": Screen("UserNotificationCenter", ZUGRIFF)}, "a")
    tb = toolbox(env)
    tb.last_place = app_place("Quillcam")
    assert tb.drift() is None
