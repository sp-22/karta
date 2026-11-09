"""The next-step option builder (karta/harness/next_step.py), its seed cases and the recorded-run labelling, with fakes."""

import numpy as np

from karta.harness.decide import HEAD_TOKENS, MAX_OPTIONS, Laya
from karta.harness.next_step import (
    BLOCKED,
    DONE,
    FIXED,
    SCROLL,
    Action,
    ask,
    build,
    head_tokens,
    label_all,
    label_recorded,
    load_recorded,
    recorded_cases,
)
from karta.harness.next_step_cases import cases, resolve
from karta.harness.snapshots import screen_json
from karta.harness.types import Item, Screen


def grid(n: int = 4) -> Screen:
    items = [Item("Search products", "field", region="top", search=True), Item("Cart", "button", region="top-right")]
    for k in range(n):
        name = f"Product number {k} snack"
        items += [Item(name, "text"), Item("₹20", "text"), Item("ADD", "button", region="middle", row=(name, "₹20"))]
    items += [
        Item("File > Close", "menu", menu=True),
        Item("Reload", "button", chrome=True),
        Item("Gone", "button", enabled=False),
    ]
    return Screen("Browser", items, url="https://shop.example/s?q=x")


class FakeAgent:
    def __init__(self, favour=None):
        self.calls, self.favour = [], favour

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        ((name, q),) = questions.items()
        options = list(q["criteria"])
        pick = self.favour if self.favour in options else options[0]
        rest = 0.3 / max(1, len(options) - 1)
        return {"answers": {name: {"probabilities": {o: (0.7 if o == pick else rest) for o in options}}}}


def fake_embed(texts):
    return np.array([[len(t), sum(map(ord, t)) % 97, 1.0] for t in texts], dtype=float)


def test_options_are_clicks_types_and_the_fixed_three():
    labels = label_all(grid(2))
    assert "type into Search products" in labels and "click Cart" in labels
    assert list(labels)[-3:] == list(FIXED)
    assert labels[DONE] == Action(DONE) and labels[SCROLL].index == -1
    # menus, chrome, disabled and plain text are never options
    assert not any(k in labels for k in ("click File > Close", "click Reload", "click Gone", "click ₹20"))


def test_a_repeated_label_carries_its_row_and_bare_labels_stay_unique():
    with_context = label_all(grid(3))
    assert "click ADD (row: Product number 1 snack)" in with_context
    bare = label_all(grid(3), context=False)
    assert [k for k in bare if k.startswith("click ADD")] == ["click ADD", "click ADD #2", "click ADD #3"]
    # both map to the same items
    assert {a.index for k, a in with_context.items() if "ADD" in k} == {a.index for k, a in bare.items() if "ADD" in k}


def test_region_stands_in_when_a_repeated_label_has_no_row():
    screen = Screen("App", [Item("Delete", "button", region="top"), Item("Delete", "button", region="bottom")])
    assert {"click Delete (top)", "click Delete (bottom)"} <= set(label_all(screen))


def test_build_fits_laya_keeps_the_fixed_options_and_offers_screen_order():
    screen = grid(30)
    opts = build("add Product number 7 snack", screen)
    labels = list(opts.options)
    assert len(labels) <= MAX_OPTIONS and labels[-3:] == list(FIXED)
    assert head_tokens("Task: `task`. What should be done next on this screen?", labels) <= HEAD_TOKENS
    indexes = [a.index for a in opts.options.values() if a.index >= 0]
    assert indexes == sorted(indexes)  # screen order, never by relevance or answer
    assert opts.dropped  # 30 ADD buttons do not fit
    assert "click ADD (row: Product number 7 snack)" in labels  # the task's words keep the named row


def test_order_does_not_depend_on_the_answer():
    screen = grid(4)
    first = list(build("add Product number 2 snack", screen).options)
    assert list(build("add Product number 2 snack", screen).options) == first


def test_a_popup_and_the_filled_fields_neighbours_rank_up_when_the_list_is_cut():
    items = [Item(f"Link {k}", "link", region="left") for k in range(20)]
    items += [Item("Message", "field", value="hi", region="bottom", focused=True), Item("Send", "button", region="bottom-right")]
    opts = build("tell someone hi", Screen("Chat", items))
    assert "click Send" in opts.options
    popup = [*items, Item("Close", "button", region="middle", dialog=True)]
    assert "click Close" in build("tell someone hi", Screen("Chat", popup)).options


def test_ask_is_one_question_with_the_task_and_maps_back():
    agent = FakeAgent(favour="click ADD (row: Product number 1 snack)")
    laya = Laya(agent, fake_embed, gates={})
    choice, opts = ask(laya, "add Product number 1 snack", grid(3))
    assert len(agent.calls) == 1
    state, questions = agent.calls[0]
    assert state["task"] == "add Product number 1 snack" and "page" in state
    assert list(questions["q"]["criteria"]) == list(opts.options)
    picked = grid(3).items[opts.options[choice.best].index]
    assert picked.name == "ADD" and picked.row[0] == "Product number 1 snack"
    _, bare = ask(laya, "x", grid(3), context=False, with_page=False)
    assert "page" not in agent.calls[-1][0] and "click ADD #2" in bare.options


def test_shuffle_changes_order_but_not_the_set():
    laya = Laya(FakeAgent(), fake_embed, gates={})
    _, plain = ask(laya, "add Product number 1 snack", grid(5))
    _, mixed = ask(laya, "add Product number 1 snack", grid(5), shuffle=3)
    assert set(plain.options) == set(mixed.options) and list(mixed.options)[-3:] == list(FIXED)


def test_seed_cases_resolve_and_cover_the_families():
    seed = cases()
    assert len(seed) >= 25
    assert {c.family for c in seed} >= {"shop", "chat", "notes", "calendar", "terminal", "browser"}
    for case in seed:
        offered = {a.key for a in label_all(case.screen).values()}
        assert case.answers and case.answers <= offered, case.name
    assert any(BLOCKED in {k for k, _ in c.answers} for c in seed) and any(DONE in {k for k, _ in c.answers} for c in seed)


def test_resolve_fails_loudly_on_an_ambiguous_answer():
    try:
        resolve(grid(2), ("click", "ADD", None))
    except ValueError:
        return
    raise AssertionError("an ambiguous ADD must not resolve")


def recorded() -> dict:
    screen = grid(2)
    return {
        "version": 1,
        "task": "add Product number 1 snack",
        "done": True,
        "ledger": ["✓ type Product | into: Search products", "✗ click Cart", "✓ click ADD | near: Product number 1 snack"],
        "actions": [
            {
                "tool": "set_text",
                "target": {"name": "Search products"},
                "target_index": 0,
                "text": "Product",
                "fresh": True,
                "screen": screen_json(screen),
            },
            {"tool": "click", "target": {"name": "Cart"}, "target_index": 1, "fresh": True, "screen": screen_json(screen)},
            {"tool": "click", "target": {"name": "ADD"}, "target_index": 7, "fresh": True, "screen": screen_json(screen)},
            {"tool": "press", "text": "return", "fresh": False, "screen": screen_json(screen)},
        ],
        "final_screen": screen_json(screen),
    }


def test_recorded_actions_are_labelled_by_the_ledger():
    marks = [right for _, right in label_recorded(recorded())]
    assert marks == [True, False, True, True]  # the press is named by no line: the task's done decides


def test_recorded_cases_keep_right_actions_on_fresh_screens_and_the_end():
    got = recorded_cases(recorded(), "run")
    assert [(c.name, c.answers) for c in got] == [
        ("run#0", frozenset({("type", 0)})),
        ("run#2", frozenset({("click", 7)})),
        ("run#end", frozenset({(DONE, -1)})),
    ]
    assert all(c.source == "recorded" for c in got)


def test_load_recorded_reads_a_folder_and_skips_bad_files(tmp_path):
    import json

    (tmp_path / "a.json").write_text(json.dumps(recorded()))
    (tmp_path / "b.json").write_text("{not json")
    assert len(load_recorded(tmp_path)) == 3
