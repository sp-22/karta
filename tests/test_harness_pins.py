"""Pinning (karta/harness/recipes.py): the path that worked is saved with its values as parameters, replayed exactly
or by name with no plan call, reused for the same shape through a `use:` line in the one plan call, and healed by a
rescue into a new version. Fakes only: nothing touches the Mac, nothing is said aloud."""

from __future__ import annotations

import json
import os
import re

import pytest
from test_harness_tools import FakeLaya, FakeLineBrain, ch

from karta.harness import loop, pin_cases, recipes
from karta.harness.brain import HarnessBrain
from karta.harness.decide import NOT_SHOWN, SHOWN
from karta.harness.memory import HarnessMemory
from karta.harness.recipes import Recipe, RecipeBook, extract
from karta.harness.types import Screen


def run(book, command, reply="", moved=False, laya=None, brain=None, confirm=None):
    mac = pin_cases.SearchMail(moved=moved)
    brain = brain or pin_cases.ScriptBrain(reply)
    result = loop.run_task(
        command, mac, laya or FakeLaya(), brain, pin_cases.FACTS, confirm=confirm or (lambda _q: True), tabs=[], pins=book
    )
    return result, mac, brain


def pinned_book() -> RecipeBook:
    book = RecipeBook()
    result, _, _ = run(book, pin_cases.FIRST, pin_cases.PLAN)
    assert result.done and result.pinned.startswith("pinned r1")
    return book


# ---------------------------------------------------------------- the template: values typed from the task


def steps(*lines: str) -> list[dict]:
    from karta.harness.brain import parse_line

    return [parse_line(line)[1] for line in lines]


def test_a_search_a_count_and_an_address_become_parameters():
    template, params, values = extract(
        pin_cases.FIRST,
        steps(*[line for line in pin_cases.PLAN.splitlines() if not line.startswith(("done:", "contract:"))]),
    )
    assert template == "email {p1} {p2} links from findit to {p3}"
    assert values == {"p1": "2", "p2": "sourdough starter", "p3": "mira@example.com"}
    assert [n for n, _ in params] == ["p1", "p2", "p3"]


def test_two_items_typed_one_by_one_are_two_parameters():
    task = "add oat milk and eggs to my groceries list in Listly"
    template, _, values = extract(
        task, steps("write oat milk | where: new item", "submit | expect: oat milk", "write eggs | where: new item")
    )
    assert template == "add {p1} and {p2} to my groceries list in Listly"
    assert values == {"p1": "oat milk", "p2": "eggs"}


def test_a_message_is_one_parameter_and_its_echo_follows_it():
    task = "post standup moved to 11 in the team channel on Chatterbox"
    ran = steps(
        "click team channel | then: team channel",
        "write standup moved to 11 | where: message box",
        "submit | expect: standup moved to 11",
    )
    template, params, values = extract(task, ran)
    assert template == "post {p1} in the team channel on Chatterbox" and values == {"p1": "standup moved to 11"}
    abstracted = [recipes.abstract(s, params) for s in ran]
    assert abstracted[2]["expect"] == "{p1}" and abstracted[0]["target"] == "team channel"


def test_a_count_said_as_a_word_binds_back_to_a_number():
    task = "collect three links on rye bread"
    ran = steps("write rye bread | where: search box", "collect links on rye bread | count: 3 | as: links")
    template, params, values = extract(task, ran)
    assert template == "collect {p1} links on {p2}" and values == {"p1": "three", "p2": "rye bread"}
    recipe = Recipe("r1", template, ["p1", "p2"], values, [recipes.abstract(s, params) for s in ran], [{}])
    hit, error = recipes.bind(recipe, {"p1": "five", "p2": "spelt"}, "same shape")
    assert error is None and hit.steps[1]["count"] == 5 and hit.steps[1]["what"] == "links on spelt"


def test_words_the_steps_never_typed_stay_in_the_template():
    template, _, values = extract(
        "open the weekly report in Docsy", steps("open_place Docsy | ready: Documents", "click weekly report")
    )
    assert template == "open the weekly report in Docsy" and values == {}


def test_a_spelling_variant_is_dropped_once_the_name_is_a_parameter():
    _, params, _ = extract("message Rohan on Chatterbox", steps("find Rohan | meaning: the chat with Rohan | alt: Rohn, Roh"))
    step = recipes.abstract(steps("find Rohan | meaning: the chat with Rohan | alt: Rohn, Roh")[0], params)
    assert step["text"] == "{p1}" and step["meaning"] == "the chat with {p1}" and step["alternatives"] == []


def test_a_password_or_code_typed_from_the_task_is_never_pinned():
    book = RecipeBook()
    ran = steps("write hunter22 | where: Password", "submit | expect: Welcome")
    trail = [(s, recipes.proof(s)) for s in ran]
    assert book.after("sign in to Ledgerly with hunter22", None, True, True, trail, "Welcome", contract="opened(Ledgerly)") == (
        "",
        "",
    )
    collected = steps("collect the sign-in code | count: 1 | as: code", "write {code} | where: Verification code")
    assert recipes.secret(collected) is False
    trail = [(s, recipes.proof(s)) for s in collected]
    assert book.after("sign in with the code", None, True, True, trail, "", contract="opened(the account)")[1] == "pinned"


# ---------------------------------------------------------------- replays and their brain calls


@pytest.mark.parametrize("prompt", ["v1", "v2"])
def test_the_bench_runs_within_its_brain_call_gates(monkeypatch, prompt):
    monkeypatch.setenv("KARTA_PROMPT", prompt)
    rows = pin_cases.bench(FakeLaya())
    bad = [r for r in rows if not r["ok"]]
    assert not bad, bad
    calls = {(r["case"], r["run"]): r["brain_calls"] for r in rows}
    assert calls[("exact", 2)] == 0 and calls[("bound", 2)] == 0 and calls[("same shape", 2)] == 1
    assert calls[("named", 3)] == 0
    assert calls[("site change", 2)] == 1 and calls[("site change", 3)] == 0


def test_an_exact_replay_makes_no_brain_call_and_still_asks_before_sending():
    book = pinned_book()
    asked = []
    result, mac, brain = run(
        book, "  Email 2 sourdough starter links from FindIt to mira@example.com. ", confirm=lambda q: asked.append(q) or True
    )
    assert result.done and result.replayed == "exact" and result.brain_calls == 0 and brain.calls == 0
    assert asked and "send it" in asked[0]  # asked once, before the replay acts
    assert mac.sent_values["To"] == "mira@example.com"


def test_a_no_to_the_send_on_a_replay_is_not_held_against_the_recipe():
    book = pinned_book()
    result, mac, _ = run(book, pin_cases.FIRST, confirm=lambda _q: False)
    assert not result.done and mac.at != "sent"
    assert book.get("r1").fails == 0


def test_the_plan_prompt_lists_templates_never_values():
    book = pinned_book()
    _, _, brain = run(book, pin_cases.SAME_SHAPE, pin_cases.USE)
    prompt = brain.asked[0]
    assert "- r1: email {p1} {p2} links from findit to {p3}" in prompt
    assert "mira" not in prompt and "sourdough" not in prompt
    assert "use: <id> | p1: <value>" in prompt


def test_a_same_shape_replay_types_the_new_values():
    book = pinned_book()
    result, mac, brain = run(book, pin_cases.SAME_SHAPE, pin_cases.USE)
    assert result.done and result.replayed == "same shape" and brain.calls == 1
    assert mac.sent_values["To"] == "arjun@example.com" and mac.sent_values["Subject"] == "3 kombucha reads"
    assert mac.sent_values["Message body"].count("https://") == 3
    assert book.get("r1").values == {"p1": "3", "p2": "kombucha", "p3": "arjun@example.com"} and book.get("r1").version == 1


def test_the_same_command_with_new_values_replays_with_no_brain_call_and_still_asks_before_sending():
    book = pinned_book()
    asked = []
    result, mac, brain = run(book, pin_cases.BOUND, confirm=lambda q: asked.append(q) or True)
    assert result.done and result.replayed == "bound" and brain.calls == 0 and result.brain_calls == 0
    assert mac.sent_values["To"] == "arjun@example.com" and mac.sent_values["Subject"] == "3 kombucha reads"
    assert mac.sent_values["Message body"].count("https://") == 3
    assert any("send" in q.lower() for q in asked)  # the up-front yes, as on every replay
    assert book.get("r1").values == {"p1": "3", "p2": "kombucha", "p3": "arjun@example.com"}


def test_code_binds_only_what_it_can_bind_alone(monkeypatch):
    book = pinned_book()
    recipe = book.get("r1")
    monkeypatch.setattr(recipes, "date_spans", lambda text: [])
    assert recipes.shaped(recipe, "Email three green tea links from FindIt to bo@example.org!") == {
        "p1": "three",
        "p2": "green tea",
        "p3": "bo@example.org",
    }  # a number word is a number; the kinds choose among the ways to align ("three green" is no number)
    assert recipes.shaped(recipe, "email some kombucha links from findit to bo@example.org") is None  # not a number
    assert recipes.shaped(recipe, pin_cases.FIRST) is None  # the same values: the exact command
    assert recipes.shaped(recipe, "email 3 kombucha links to bo@example.org from findit") is None  # reworded
    assert book.bound("email 3 kombucha links from findit to my bank password") is None  # never near a secret
    assert book.match("email 3 kombucha links from findit to arjun@example.com and sam@example.com").how == "bound"


def test_a_date_binds_only_as_a_date(monkeypatch):
    def spans(text: str):  # the platform's date detector, for the words of this test
        return [(m.start(), m.end()) for m in re.finditer(r"(?:on )?(?:friday|monday)(?: at \d+ ?(?:am|pm))?", text, re.I)]

    monkeypatch.setattr(recipes, "date_spans", spans)
    recipe = recipes.Recipe(
        "r1", "remind me to {p1} on {p2} in Lists", ["p1", "p2"], {"p1": "buy stamps", "p2": "Friday"}, [], []
    )
    assert recipes.shaped(recipe, "remind me to call the plumber on Monday at 9 am in Lists") == {
        "p1": "call the plumber",
        "p2": "Monday at 9 am",
    }
    assert recipes.shaped(recipe, "remind me to water the plants on the balcony in Lists") is None  # no date where one was
    assert recipes.shaped(recipes.Recipe("r3", "play {p1}", ["p1"], {"p1": "lofi"}, [], []), "play jazz") is None  # too loose
    one = recipes.Recipe("r2", "add a reminder to {p1} in Lists", ["p1"], {"p1": "buy stamps"}, [], [])
    assert recipes.shaped(one, "add a reminder to call Bo on Friday in Lists") is None  # a date the recipe never set
    assert recipes.shaped(one, "add a reminder to call Bo in Lists") == {"p1": "call Bo"}


@pytest.mark.v1  # a v1 plan: words for the done line
def test_a_bad_use_line_falls_back_to_a_plan_with_one_repair_ask():
    book = pinned_book()
    plan = pin_cases.PLAN.replace("sourdough starter", "kombucha").replace("mira@", "arjun@")
    brain = FakeLineBrain("use: r1 | p1: 3 | p2: kombucha", plan)
    result, _, _ = run(book, pin_cases.SAME_SHAPE, brain=brain)
    assert brain.calls == 2 and "needs a value for p3" in brain.asked[1][1]
    assert result.done and result.replayed == ""


def test_use_lines_are_checked():
    book = pinned_book()
    assert "no pinned task 'r9'" in book.bind_use("r9 | p1: 3")[1]
    assert "takes p1, p2, p3" in book.bind_use("r1 | p1: 3 | p2: tea | p3: a@b.example | p4: x")[1]
    assert "given twice" in book.bind_use("r1 | p1: 3 | p1: 4 | p2: tea | p3: a@b.example")[1]
    assert "one short value" in book.bind_use("r1 | p1: {x} | p2: tea | p3: a@b.example")[1]
    assert "count" in book.bind_use("r1 | p1: lots | p2: tea | p3: a@b.example")[1]  # a count must be a number
    hit, error = book.bind_use("r1 | p1: 4 | p2: green tea | p3: a@b.example")
    assert error is None and hit.how == "same shape" and hit.steps[1]["text"] == "green tea"


@pytest.mark.v1  # a v1 plan: words for the done line
def test_a_json_brain_can_answer_with_use_too():
    class JsonBrain:
        def __init__(self, reply):
            self.reply = reply

        def json(self, system, user):
            return self.reply

    book = pinned_book()
    hb = HarnessBrain(JsonBrain({"use": "r1 | p1: 3 | p2: kombucha | p3: arjun@example.com"}))
    stream = hb.stream_plan(
        pin_cases.SAME_SHAPE, "", Screen("Finder", []), pinned=book.offer(pin_cases.SAME_SHAPE), use=book.bind_use
    )
    got = list(stream)
    assert stream.plan.pinned is not None and got[1]["text"] == "kombucha" and stream.plan.done_shows == "Message sent"


def test_a_use_line_without_offers_is_rejected():
    hb = HarnessBrain(FakeLineBrain("use: r1 | p1: 3", "click New | then: Draft"))
    stream = hb.stream_plan("make a draft", "", Screen("Finder", []))
    assert [s["tool"] for s in stream] == ["click"] and hb.calls == 2


def test_a_site_change_heals_the_recipe_and_keeps_the_old_version():
    book = pinned_book()
    result, _, brain = run(book, pin_cases.FIRST, pin_cases.FIX, moved=True)
    assert result.done and result.rescues == 1 and brain.calls == 1 and result.pinned.startswith("r1 updated to v2")
    recipe = book.get("r1")
    assert recipe.version == 2 and recipe.history[0]["version"] == 1
    assert any(s.get("target") == "Message > New Message" for s in recipe.steps)
    assert any(s.get("target") == "Compose" for s in recipe.history[0]["steps"])
    assert not any(s.get("target") == "Compose" for s in recipe.steps)  # the failed step is left out
    assert book.rollback("r1") and book.get("r1").version == 1


@pytest.mark.v1  # a v1 plan: words for the done line
def test_a_done_only_the_brain_saw_is_not_pinned():
    unsure = FakeLaya(outcome=lambda e, s: ch("D7 outcome", (NOT_SHOWN, 0.5), (SHOWN, 0.45), gate=0.6))
    plan = pin_cases.PLAN.replace("done: Message sent", "done: Delivered to the recipient")
    plan = "\n".join(line for line in plan.splitlines() if not line.startswith("contract:"))  # a contract is code's
    brain = FakeLineBrain(plan, "yes: the mail shows as sent")
    book = RecipeBook()
    result, _, _ = run(book, pin_cases.FIRST, laya=unsure, brain=brain)
    assert result.done and "brain check" in result.reason
    assert not book.recipes and result.pinned == ""


def test_a_recipe_that_fails_twice_is_benched_until_a_planned_run_works():
    book = pinned_book()
    for n in (1, 2):
        result, _, _ = run(book, pin_cases.FIRST, "stop: the compose button is gone", moved=True)
        assert not result.done and book.get("r1").fails == n
    assert book.get("r1").benched and book.offer(pin_cases.SAME_SHAPE) == []
    assert "not replayed until it works again" in result.pinned
    result, _, brain = run(book, pin_cases.FIRST, pin_cases.PLAN)
    assert brain.calls == 1 and result.replayed == "" and "planned afresh" in "\n".join(result.trace)
    assert result.done and book.get("r1").fails == 0 and result.pinned == "r1 works again"


# ---------------------------------------------------------------- naming and forgetting


def test_naming_and_saying_the_name():
    for naming, saying in (
        ("call that my morning thing", "do my morning thing"),
        ("Save this as morning.", "morning thing"),
        ("name it weekly links", "run weekly links again"),
    ):
        book = pinned_book()
        result, _, brain = run(book, naming)
        assert result.done and brain.calls == 0 and result.brain_calls == 0
        result, mac, brain = run(book, saying)
        assert result.done and result.replayed == "named" and brain.calls == 0
        assert result.task == pin_cases.FIRST and mac.sent_values["To"] == "mira@example.com"


def test_a_name_moves_from_one_routine_to_the_next():
    book = pinned_book()
    book.command("call that my digest")
    book.recipes.append(Recipe("r7", "tidy {p1}", ["p1"], {"p1": "desk"}, [], [], used=book.clock()))
    book.last = "r7"
    book.command("call that my digest")
    assert book.named("digest").id == "r7" and book.get("r1").name == ""


def test_forgetting():
    book = pinned_book()
    assert book.command("forget that") == (True, "forgot the pinned task 'email {p1} {p2} links from findit to {p3}'")
    assert book.recipes == [] and book.command("forget that") == (False, "nothing is pinned to forget")
    book = pinned_book()
    book.command("call that my reading digest")
    assert book.command("forget this wifi network") is None  # an ordinary task
    assert book.command("forget my reading digest")[0] and book.recipes == []
    assert book.command("call that my morning thing") == (False, "nothing is pinned yet to name: do the task once first")


def test_a_pin_command_never_touches_the_mac():
    book = pinned_book()
    result, mac, brain = run(book, "forget that")
    assert result.done and mac.log == [] and brain.calls == 0


# ---------------------------------------------------------------- the file


def test_the_file_is_private_capped_and_ages_out(tmp_path):
    path = tmp_path / "pins.json"
    now = [1_000_000.0]
    book = RecipeBook(path, clock=lambda: now[0])
    run(book, pin_cases.FIRST, pin_cases.PLAN)
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    data = json.loads(path.read_text())
    assert data["last"] == "r1" and data["recipes"][0]["template"] == "email {p1} {p2} links from findit to {p3}"
    assert "breadnotes" not in path.read_text()  # collected items are never stored
    again = RecipeBook(path, clock=lambda: now[0])
    assert again.get("r1").steps == book.get("r1").steps and again.next == 2
    for n in range(recipes.MAX_RECIPES + 5):
        again.recipes.append(Recipe(f"r{n + 10}", f"tidy {{p1}} {n}", ["p1"], {"p1": "desk"}, [], [], used=now[0] + n))
    again.save()
    assert len(again.recipes) == recipes.MAX_RECIPES
    now[0] += (recipes.EXPIRE_DAYS + 1) * recipes.DAY
    assert RecipeBook(path, clock=lambda: now[0]).recipes == []


def test_only_the_last_versions_are_kept():
    book = pinned_book()
    recipe = book.get("r1")
    for n in range(5):
        trail = [
            ({"tool": "click", "target": f"Step {n}", "expects": "list_changed"}, {"kind": "expect", "type": "list_changed"})
        ]
        book.after(
            pin_cases.FIRST, recipes.bind(recipe, recipe.values, "exact")[0], True, True, trail, "", contract=recipe.contract
        )
    assert recipe.version == 6 and [h["version"] for h in recipe.history] == [5, 4, 3]


def test_memory_keeps_its_pins_beside_its_file(tmp_path):
    memory = HarnessMemory(tmp_path / "harness-memory.sqlite")
    assert isinstance(memory.pins, RecipeBook) and memory.pins.path == tmp_path / "pins.json"
    assert HarnessMemory(":memory:").pins is None
    assert recipes.book_of(None, memory) is memory.pins and recipes.book_of(None, object()) is None


def test_a_run_whose_submit_had_nothing_written_before_it_is_never_pinned():
    from karta.harness.recipes import whole

    assert whole([{"tool": "open_place"}, {"tool": "write", "text": "x"}, {"tool": "submit"}])
    assert not whole([{"tool": "open_place"}, {"tool": "submit"}])
    assert not whole([{"tool": "write", "text": "x"}, {"tool": "open_place"}, {"tool": "submit"}])
