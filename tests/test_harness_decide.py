import ast
import os
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from karta.harness import decide
from karta.harness.bench_cases import (
    ITEM_DECISIONS,
    METHODS,
    SCENARIO_APPS,
    all_cases,
    blinkit_popup,
    blinkit_results,
    whatsapp_chat,
)
from karta.harness.decide import (
    ABSENT,
    DECISIONS,
    DOES_IT,
    DOES_OTHER,
    ELSEWHERE,
    INTERRUPTION,
    NONE,
    NOT_SHOWN,
    PAGE_STATES,
    PART_OF_STEP,
    SHOWN,
    UNLISTED,
    Laya,
    labels,
    load_gates,
)
from karta.harness.types import Choice, Item, Screen

real_laya = pytest.mark.skipif(os.environ.get("KARTA_LAYA") != "1", reason="set KARTA_LAYA=1 to run the real model")


class FakeAgent:
    """Gives `favour` (a label, or a function of the labels) 0.6 and shares the rest; records every question."""

    def __init__(self, favour=None):
        self.calls = []
        self.favour = favour

    def system_one(self, state, questions):
        self.calls.append((state, questions))
        ((name, q),) = questions.items()
        options = list(q["criteria"])
        pick = self.favour(options) if callable(self.favour) else self.favour
        pick = pick if pick in options else options[0]
        rest = 0.4 / max(1, len(options) - 1)
        probs = {o: (0.6 if o == pick else rest) for o in options}
        return {"answers": {name: {"type": "choice", "choice": pick, "probabilities": probs}}}


def fake_embed(texts):
    return np.array([[len(t), sum(map(ord, t)) % 97, 1.0] for t in texts], dtype=float)


def laya(favour=None, gates=None, **kw):
    return Laya(FakeAgent(favour), fake_embed, gates={} if gates is None else gates, **kw)


def questions(agent):
    return [q for _, qs in agent.calls for q in qs.values()]


def test_every_question_is_one_narrow_choice_with_semantic_labels():
    lay = laya()
    screen = blinkit_results()
    lay.place("Blinkit grocery", ["Notes app", "blinkit.com in Brave profile Work"])
    lay.page_state("Blinkit grocery", screen)
    lay.target("the ADD button for Kurkure", screen.page())
    lay.pick_ok("add Kurkure", screen.page()[-1])
    lay.name_match("Kurkure", "kurkure", screen.page())
    lay.dialog_answer("order chips", [Item("Allow", "button"), Item("Block", "button")])
    for template in Laya.TEMPLATES["D6 dialog role"]:
        lay.templates["D6 dialog role"] = template
        lay.dialog_role(
            "order chips", [Item("Get 50% off today", "text"), Item("Claim offer", "button"), Item("Close", "button")]
        )
    lay.outcome("Kurkure in the cart", screen)
    for _, qs in lay.agent.calls:
        assert len(qs) == 1
    for q in questions(lay.agent):
        assert q["type"] == "choice"
        assert 2 <= len(q["criteria"]) <= decide.MAX_OPTIONS
        assert not {"true", "false", "yes", "no"} & {k.lower() for k in q["criteria"]}
        assert len(q["instructions"].split()) <= 20


def test_choice_keeps_the_whole_ranking_best_first_with_its_gate():
    lay = laya(favour="Notes app", gates={"D1 place": {"gate": 0.55}})
    choice = lay.place("a note", ["Notes app", "Reminders app", "Calendar app"])
    probs = [p for _, p in choice.ranked]
    assert probs == sorted(probs, reverse=True)
    assert choice.best == "Notes app"
    assert {o for o, _ in choice.ranked} == {"Notes app", "Reminders app", "Calendar app", NONE}
    assert choice.gate == 0.55 and choice.sure


def test_below_its_gate_an_answer_is_unsure():
    choice = laya(gates={"D1 place": {"gate": 0.7}}).place("a note", ["Notes app", "Reminders app"])
    assert not choice.sure


POLICY = {
    "D1 place": {
        "policy": {"floor": 0.5, "margin": 0.3, "high": 0.9},
        "risky_policy": {"floor": 0.8, "margin": 0.6, "high": 0.97},
    }
}


def ch(*ranked, decision="D1 place"):
    return Choice(decision, list(ranked), 0.5)


def test_policy_acts_when_the_top_answer_is_high_and_clearly_ahead():
    lay = laya(gates=POLICY)
    assert lay.acts(ch(("a", 0.6), ("b", 0.2)))  # above the floor, 0.4 ahead
    assert not lay.acts(ch(("a", 0.6), ("b", 0.35)))  # above the floor, only 0.25 ahead
    assert not lay.acts(ch(("a", 0.45), ("b", 0.05)))  # far ahead, below the floor
    assert lay.acts(ch(("a", 0.92), ("b", 0.08)))  # the high shortcut
    assert lay.acts(ch(("a", 1.0)))


def test_choice_sure_is_the_policy_verdict():
    """Choice.sure compares p1 with its gate, so the gate is what this ranking needs: min(high, max(floor, p2 +
    margin)). A right answer at 0.70 far ahead of the rest is acted on; one at 0.70 in a near tie is not."""
    lay = laya(gates=POLICY)
    ahead = lay.choice("D1 place", [("a", 0.7), ("b", 0.2), ("c", 0.1)])
    tied = lay.choice("D1 place", [("a", 0.7), ("b", 0.45)])
    assert ahead.sure and ahead.gate == 0.5
    assert not tied.sure and tied.gate == 0.75
    assert lay.choice("D1 place", [("a", 0.93), ("b", 0.7)]).gate == 0.9
    for ranked in ([("a", 0.6), ("b", 0.3)], [("a", 0.8), ("b", 0.49)], [("a", 0.55), ("b", 0.25)]):
        assert lay.choice("D1 place", ranked).sure == lay.acts(ch(*ranked))


def test_a_risky_action_needs_the_strict_tier():
    """Reversible actions are checked right after and a wrong pick is dropped; an action that cannot be undone
    (send, pay, delete, confirming one) needs the strict policy."""
    lay = laya(gates=POLICY)
    ahead = ch(("a", 0.7), ("b", 0.2), ("c", 0.1))
    assert lay.acts(ahead) and lay.acts(ahead, risk="reversible") and not lay.acts(ahead, risk="risky")
    assert lay.acts(ch(("a", 0.85), ("b", 0.1)), risk="risky")
    assert lay.acts(ch(("a", 0.97), ("b", 0.03)), risk="risky")
    assert not lay.acts(ch(("a", 0.85), ("b", 0.3)), risk="risky")
    assert laya().policy("D1 place", "risky") == decide.DEFAULT_RISKY
    with pytest.raises(ValueError):
        lay.acts(ahead, risk="maybe")


def test_the_runner_up_is_another_site_not_another_profile():
    lay = laya(gates=POLICY)
    ranked = [("x.com in Brave profile Work", 0.6), ("x.com in Brave profile Home", 0.6), ("Notes app", 0.25)]
    assert lay.needed("D1 place", ranked) == 0.55 and lay.acts(ch(*ranked))


def test_an_old_single_gate_still_means_what_it_did():
    lay = laya(gates={"D1 place": {"gate": 0.7}})
    assert lay.policy("D1 place") == {"floor": 0.7, "margin": 0.0, "high": 0.7}
    assert lay.acts(ch(("a", 0.7), ("b", 0.3))) and not lay.acts(ch(("a", 0.69), ("b", 0.0)))
    assert laya().policy("D1 place") == decide.DEFAULT_POLICY


def test_place_judges_the_policy_on_sites_not_on_profiles_of_one_site():
    lay = laya(favour="web.whatsapp.com", gates=POLICY)
    places = ["Safari app", "web.whatsapp.com in Brave profile Personal", "web.whatsapp.com in Brave profile Work"]
    choice = lay.place("WhatsApp chats", places)
    assert choice.ranked[0][1] == choice.ranked[1][1]  # the two profiles tie
    assert choice.sure  # but the site is 0.6 against 0.2


def test_place_asks_once_per_site_and_keeps_the_profile_order():
    lay = laya(favour="web.whatsapp.com")
    places = ["Safari app", "web.whatsapp.com in Brave profile Personal", "web.whatsapp.com in Brave profile Work"]
    choice = lay.place("WhatsApp chats", places)
    criteria = questions(lay.agent)[0]["criteria"]
    assert list(criteria) == ["Safari app", "web.whatsapp.com", UNLISTED]
    assert [o for o, _ in choice.ranked][:2] == places[1:]
    assert choice.ranked[0][1] == choice.ranked[1][1]
    assert NONE in dict(choice.ranked)


def test_page_state_settles_the_place_by_code_when_the_site_is_named():
    lay = laya(favour="a popup or question in front")
    choice = lay.page_state("Blinkit", blinkit_popup())  # the site's own name: its identity, by code
    assert len(lay.agent.calls) == 1  # the place was read from the site, not asked
    assert choice.best == decide.DIALOG
    assert {o for o, _ in choice.ranked} == set(PAGE_STATES)
    assert "wanted" not in lay.agent.calls[0][0]  # the kind of screen is read from the page alone


def test_a_word_in_common_is_no_proof_of_the_place():
    news = Screen("News", [Item("Top Stories", "heading")], window="News")
    lay = laya(favour="a different app or site")
    assert lay.page_state("Hacker News", news).best == ELSEWHERE and len(lay.agent.calls) == 1  # Laya, not a word
    assert laya().here("News app", news).best == decide.READY  # the app itself


def test_page_state_elsewhere_stops_after_the_place_question():
    lay = laya(favour="a different app or site")
    notes = Screen("Notes", [Item("New Note", "button")], window="Notes")
    choice = lay.page_state("Blinkit grocery", notes)
    assert choice.best == ELSEWHERE
    assert len(lay.agent.calls) == 1


def test_page_state_reports_stable_keys_whatever_the_template():
    for template in Laya.TEMPLATES["D2 page state"]:
        choice = laya(templates={"D2 page state": template}).page_state("Blinkit grocery", blinkit_popup())
        assert {o for o, _ in choice.ranked} <= set(PAGE_STATES)


def test_target_offers_not_on_this_screen_and_reports_it_as_none():
    lay = laya(favour=ABSENT)
    choice = lay.target("delete my account", whatsapp_chat().page())
    assert ABSENT in questions(lay.agent)[0]["criteria"]
    assert choice.best == NONE


def test_target_takes_an_exact_name_without_asking():
    lay = laya()
    choice = lay.target("click Attach", whatsapp_chat().page())
    assert choice.best == "Attach" and choice.ranked[0][1] == 1.0
    assert lay.agent.calls == []


def test_target_offers_controls_only_and_says_which_is_a_text_box():
    lay = laya()
    lay.target("the message box", whatsapp_chat().page())
    criteria = questions(lay.agent)[0]["criteria"]
    assert "Type a message (text box)" in criteria
    assert "Ok, see you tomorrow" not in criteria  # plain text is not offered when controls exist


def test_labels_name_repeated_items_by_their_row_and_stay_unique():
    names = labels([i for i in blinkit_results().page() if i.name == "ADD"])
    assert len(names) == 8 and len(set(names)) == 8
    assert "ADD (Kurkure Masala Munch)" in names
    again = labels([Item("OK", "button"), Item("OK", "button"), Item(NONE, "button")])
    assert len(again) == 3 and NONE not in again


def test_pick_check_contrasts_the_item_with_another_control():
    lay = laya()
    item = next(i for i in blinkit_results().page() if i.row and i.row[0] == "Kurkure Masala Munch")
    choice = lay.pick_ok("add Kurkure to the cart", item)
    assert list(questions(lay.agent)[0]["criteria"]) == ["ADD (Kurkure Masala Munch)", "some other control"]
    assert {o for o, _ in choice.ranked} == {DOES_IT, DOES_OTHER}


def test_name_match_includes_none():  # a line saying nothing matched is ruled out before (Toolbox.no_results)
    lay = laya(favour=NONE)
    items = [Item("Anjana Rao", "row"), Item("Rahul K", "row")]
    choice = lay.name_match("the chat with Anjali", "Anjali", items)
    criteria = questions(lay.agent)[0]["criteria"]
    assert list(criteria)[-1] == NONE and "Anjana Rao" in criteria
    assert choice.best == NONE


def test_dialog_answer_offers_the_buttons_not_the_text():
    lay = laya()
    options = [
        Item("Do you trust the files in this folder?", "text"),
        Item("1. Yes, proceed", "button"),
        Item("2. No, exit", "button"),
    ]
    lay.dialog_answer("start Claude Code", options)
    assert list(questions(lay.agent)[0]["criteria"]) == ["1. Yes, proceed", "2. No, exit"]


def test_dialog_role_reports_part_of_the_step_or_an_interruption_whatever_the_template():
    items = [Item("Try Premium free for a month", "text", dialog=True), Item("Try it free", "button", dialog=True),
             Item("No thanks", "button", dialog=True)]  # fmt: skip
    for template in Laya.TEMPLATES["D6 dialog role"]:
        for favour in (None, "an interruption to close", "it appeared unasked", "none, it is in the way"):
            lay = laya(favour=favour, templates={"D6 dialog role": template})
            choice = lay.dialog_role("play a playlist", items)
            assert {o for o, _ in choice.ranked} == {PART_OF_STEP, INTERRUPTION}
            assert abs(sum(p for _, p in choice.ranked) - 1) < 1e-9
            state = lay.agent.calls[0][0]
            assert state["step"] == "play a playlist" and "Try it free" in state["popup"]


def test_dialog_role_with_the_interruption_favoured_says_so():
    lay = laya(favour="an interruption to close", templates={"D6 dialog role": "needed-or-close"})
    choice = lay.dialog_role("play a playlist", [Item("Get the app", "text"), Item("Not now", "button")])
    assert choice.best == INTERRUPTION and choice.sure


def test_dialog_role_takes_a_control_the_step_names_as_part_of_it_without_asking():
    lay = laya()
    items = [Item("Set a password", "heading"), Item("Password", "field"), Item("Set Password", "button")]
    choice = lay.dialog_role("click Set Password", items)
    assert choice.best == PART_OF_STEP and choice.ranked[0][1] == 1.0 and choice.sure
    assert lay.agent.calls == []


def test_outcome_never_counts_our_own_typing():
    for template in Laya.TEMPLATES["D7 outcome"]:
        lay = laya(templates={"D7 outcome": template})
        choice = lay.outcome("message 'see you at 5' sent", whatsapp_chat(typed="see you at 5"))
        text = str(lay.agent.calls)
        assert "Type a message" not in text
        assert {o for o, _ in choice.ranked} == {SHOWN, NOT_SHOWN}


def test_questions_are_cut_to_fit_the_head_and_the_context():
    lay = laya()
    items = [Item(f"Result number {n} with a rather long descriptive name here", "row") for n in range(40)]
    lay.name_match("result 3", "result", items)
    state, qs = lay.agent.calls[0]
    q = qs["q"]
    assert NONE in q["criteria"]
    assert lay._head(q["instructions"], q["criteria"]) <= decide.HEAD_TOKENS
    page = [Item(f"Line {n} " + "word " * 20, "text") for n in range(60)]
    lay.outcome("line 5", Screen("Notes", page))
    state, qs = lay.agent.calls[-1]
    assert lay._head(qs["q"]["instructions"], qs["q"]["criteria"]) + lay.tokens(str(state)) <= decide.MAX_TOKENS


def test_a_sharpened_many_option_answer_is_re_tempered():
    lay = laya()
    lay.agent.temperature_by_options = {"choice:11+": 0.5, "choice:6-10": 1.0}
    probs = {f"o{n}": 0.001 for n in range(11)} | {"o0": 0.99}
    softer = lay._calibrated(probs)
    assert softer["o0"] < 0.99 and abs(sum(softer.values()) - 1) < 1e-9
    assert max(softer, key=softer.get) == "o0"
    few = {"a": 0.9, "b": 0.1}
    assert lay._calibrated(few) == few


def test_gates_file_covers_every_decision_with_a_known_template():
    gates = load_gates()
    assert set(gates) == set(DECISIONS)
    for decision, entry in gates.items():
        assert entry["template"] in Laya.TEMPLATES[decision]
        assert 0.3 <= entry["gate"] <= 0.95
        policy = entry["policy"]
        assert policy["floor"] == entry["gate"] and 0 <= policy["margin"] <= 0.8 and policy["high"] > policy["floor"]
        risky = entry["risky_policy"]
        assert 0 <= risky["margin"] <= 0.8 and risky["high"] > risky["floor"]
        assert policy["floor"] <= 0.6 and policy["margin"] <= 0.2  # reversible: a moderate floor, a small margin
        for split in ("held_out", "tuning"):
            assert {"coverage", "accuracy@1", "accuracy@2"} <= set(entry[split]["reversible"])
            assert {"precision", "coverage"} <= set(entry[split]["risky"])
    assert load_gates(Path("/nonexistent/gates.json")) == {}


def test_templates_and_instructions_name_no_scenario_app():
    """Comments and docstrings may cite a finding; the strings Laya reads may not name a scenario's app."""
    tree = ast.parse(Path(decide.__file__).read_text())
    docs = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Module | ast.ClassDef | ast.FunctionDef) and ast.get_docstring(n)
    }
    strings = [
        n.value.lower() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    ]
    for word in ("blinkit", "whatsapp", "hemanshu", "detect my location", "linear"):
        assert not any(word in s for s in strings), word


def test_benchmark_is_big_diverse_and_answerable():
    cases = all_cases()
    for decision in METHODS:
        tune = [c for c in cases if c.decision == decision and c.split == "tune"]
        held = [c for c in cases if c.decision == decision and c.split == "held"]
        assert len(tune) >= 15 and len(held) >= len(tune), decision
        apps = Counter(c.app for c in tune)
        assert max(apps.values()) <= 0.15 * len(tune), (decision, apps.most_common(1))
        assert sum(n for a, n in apps.items() if any(s in a for s in SCENARIO_APPS)) <= len(tune) / 3, decision
        assert not {c.app for c in held} & {"whatsapp", "linear", "blinkit"}
    for c in cases:
        assert c.answers and c.app
        if c.decision in ITEM_DECISIONS:
            items = c.args[-1]
            names = {i.name for i in items} | set(labels(items)) | {NONE}
            short = {decide._short(n) for n in names}
            assert all(a in names or a in short for a in c.answers), (c.name, c.answers)


@real_laya
def test_real_laya_benchmark_holds_up_on_held_out_apps():
    sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
    import laya_mlx
    from decision_bench import run

    from karta.decider.laya import cached_embed, load_agent

    agent = load_agent("english")
    lay = Laya(agent, cached_embed(laya_mlx.embed_fn_from_agent(agent)))
    floors = {
        "D1 place": 0.75,
        "D2 page state": 0.65,
        "D3 target": 0.6,
        "D4 pick check": 0.6,
        "D5 name match": 0.7,
        "D6 dialog answer": 0.65,
        "D6 dialog role": 0.75,
        "D7 outcome": 0.45,
    }
    for decision, floor in floors.items():
        runs = run(lay, [c for c in all_cases() if c.decision == decision and c.split == "held"])
        accuracy = sum(r.ok for r in runs) / len(runs)
        assert accuracy >= floor, (decision, accuracy)
