"""Recorded runs replayed offline (karta/harness/replay.py), and the scrubber that makes them safe to commit.

Every case in tests/replay/<name>/ runs through loop.run_task with its screens, brain replies, Laya answers and the
user's answers served from run.json; the actions must be the recorded ones, in order, and the outcome expected.json's
(done or failed honestly, no false done, at most its brain calls). A case expected.json marks "xfail" carries the
reason it fails today. Add one with scripts/promote_run.py; the hand-built seeds come from tests/replay/_seeds.py.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from karta.harness.replay import (
    CASES,
    STANDIN,
    ReplayDivergence,
    Scrubber,
    action_line,
    case_folders,
    leaks,
    load_case,
    outcome_problems,
    replay,
    scrub_record,
    site_labels,
)

FOLDERS = case_folders(CASES)


def _param(folder):
    _record, expected = load_case(folder)
    marks = [pytest.mark.xfail(reason=expected["xfail"], strict=True)] if expected.get("xfail") else []
    return pytest.param(folder, id=folder.name, marks=marks)


def test_there_are_cases():
    assert {f.name for f in FOLDERS} >= {"seed-sent-before", "seed-stale-after-open", "seed-full-screen"}


@pytest.mark.parametrize("folder", [_param(f) for f in FOLDERS])
def test_a_recorded_run_replays_to_its_expected_outcome(folder):
    record, expected = load_case(folder)
    out = replay(record)
    assert out.ran == out.expected, out.divergence
    assert outcome_problems(expected, out) == []


@pytest.mark.parametrize("folder", FOLDERS, ids=[f.name for f in FOLDERS])
def test_a_committed_case_shows_no_one_s_content(folder):
    record, _expected = load_case(folder)
    assert record.get("scrubbed") is True
    assert leaks(record) == []
    text = json.dumps(record)
    assert not re.search(r"[\w.+-]+@(?!example\.)[\w-]+\.[\w.]+", text), "an email address on a real domain"
    assert "?" not in "".join(s.get("url") or "" for s in record["screens"]), "a query in a web address"


# ---------------------------------------------------------------- the replay itself


def seed():
    return load_case(CASES / "seed-stale-after-open")[0]


def test_a_replay_makes_no_brain_or_model_call_and_takes_the_recorded_actions():
    record = seed()
    out = replay(record)
    assert out.divergence is None and out.problems == [] and out.laya == "recorded"
    assert out.ran == [action_line(a) for a in record["actions"]]
    assert out.done and out.brain_calls == len(record["brain"])


def test_an_action_that_differs_fails_loudly_with_what_ran_and_what_should_have_run():
    record = seed()
    tampered = copy.deepcopy(record)
    for action in tampered["actions"]:
        if action["tool"] == "set_text":
            action["text"] = "Something else entirely"
    out = replay(tampered)
    assert out.result is None and out.divergence
    assert "differs (other text)" in out.divergence and "ran:" in out.divergence and "should have run:" in out.divergence
    assert out.ran != out.expected and out.ran[-1].startswith("set_text")


def test_a_run_that_stops_early_shows_the_actions_it_never_took():
    record = seed()
    longer = copy.deepcopy(record)
    longer["actions"].append({"tool": "click", "target": {"name": "Somewhere", "kind": "button"}, "target_index": None})
    longer["events"].append({"act": len(longer["actions"]) - 1})
    out = replay(longer)
    assert "never" in (out.divergence or "") or "stopped before" in (out.divergence or "")
    assert out.expected[-1] == "click 'Somewhere' button" and "click 'Somewhere' button" not in out.ran


def test_a_brain_request_the_run_never_made_is_a_problem_not_a_hang():
    record = seed()
    record = copy.deepcopy(record)
    record["brain"] = []
    out = replay(record)
    assert out.problems and "never made" in out.problems[0]
    assert not out.done


def test_a_question_to_the_user_the_run_never_asked_diverges():
    record = copy.deepcopy(load_case(CASES / "seed-sent-before")[0])
    record["confirms"] = []
    out = replay(record)
    assert out.result is None and "yes/no question the run never asked" in out.divergence


def test_the_expected_outcome_catches_a_false_done():
    record, expected = load_case(CASES / "seed-stale-after-open")
    out = replay(record)
    assert outcome_problems({**expected, "done": False}, out)[0].startswith("a false done")
    assert "brain calls" in outcome_problems({**expected, "brain_calls": 0}, out)[0]


def test_replay_divergence_gets_past_any_except_exception():
    assert not issubclass(ReplayDivergence, Exception)


# ---------------------------------------------------------------- the scrubber

PRIVATE = {
    "task": "email priya@example.com the Kumarakom trip photos",
    "screens": [
        {
            "app": "Brave Browser",
            "url": "https://mail.google.com/mail/u/0/inbox/FMfcgzQXJ",
            "window": "Inbox (1,284) - priya@example.com - Gmail",
            "profile": "Priya",
            "items": [
                {"name": "Compose", "kind": "button", "region": "left", "row": [], "value": ""},
                {
                    "name": "Rohan Mehta",
                    "kind": "row",
                    "region": "middle",
                    "row": ["Dinner at Toit tonight?", "9:41"],
                    "value": "",
                },
                {"name": "Call me on 98450 12345 re the Kumarakom booking", "kind": "text", "region": "middle"},
                {"name": "Search mail", "kind": "field", "region": "top", "value": "kumarakom"},
                {"name": "Shared album", "kind": "link", "region": "middle", "url": "https://photos.app.goo.gl/Xy7Qp9"},
                {"name": "priyaraman.substack.com", "kind": "link", "url": "https://priyaraman.substack.com/p/monsoon"},
            ],
        }
    ],
    "actions": [
        {
            "tool": "set_text",
            "target": {"name": "To", "kind": "field"},
            "text": "priya@example.com",
            "screen": None,
        },
        {"tool": "open_url", "detail": "mail.google.com in Brave Browser"},
    ],
    "events": [{"read": 0}, {"act": 0}, {"act": 1}],
    "brain": [{"kind": "plan", "via": "lines", "lines": ["write Kumarakom trip photos | where: Subject | then: Kumarakom"]}],
    "laya": [
        {
            "instructions": "Which item?",
            "values": {"asked": "email priya@example.com", "page": "'Rohan Mehta' row (row: Dinner at Toit tonight?)"},
            "read": 0,
            "options": ["'Rohan Mehta' row", "none of these"],
            "probs": [0.9, 0.1],
        }
    ],
    "memory": [{"call": "alias_for", "args": ["rohan", "Brave Browser"], "result": "Rohan Mehta"}],
    "confirms": [{"question": "send: click 'Send' (task: email priya@example.com)", "answer": True}],
    "asks": [{"question": "Which Rohan?", "reply": "Rohan from work"}],
    "mac": {
        "installed_apps": ["Brave Browser", "WhatsApp"],
        "browsers": {"Brave Browser": ["Priya", "Work"]},
        "folders": ["~/Documents/Kumarakom-2026"],
        "now": "2026-10-08T09:30",
        "tabs": [
            {
                "browser": "Brave Browser",
                "window_index": 1,
                "tab_index": 1,
                "url": "https://bank.example/acct/88812",
                "title": "HDFC NetBanking - Priya Raman",
                "profile": "Priya",
            }
        ],
        "signed_in": ["mail.google.com"],
    },
    "ledger": ["✓ write Kumarakom trip photos  ('Subject' holds 'Kumarakom trip photos')"],
    "answer": "Rohan asked about dinner at Toit",
}
SECRETS = [
    "priya",
    "raman",
    "rohan",
    "mehta",
    "kumarakom",
    "toit",
    "98450",
    "12345",
    "fmfcgzqxj",
    "xy7qp9",
    "88812",
    "hdfc",
    "monsoon",
    "netbanking",
    "dinner",
]


def test_the_scrubber_replaces_every_name_message_number_and_path_and_keeps_the_structure():
    out = scrub_record(PRIVATE, "test-salt")
    text = json.dumps(out).lower()
    for secret in SECRETS:
        assert secret not in text, secret
    screen = out["screens"][0]
    assert screen["app"] == "Brave Browser" and screen["items"][0]["name"] == "Compose"
    assert [i["kind"] for i in screen["items"]] == [i["kind"] for i in PRIVATE["screens"][0]["items"]]
    assert screen["items"][1]["region"] == "middle" and screen["url"].startswith("https://mail.google.com/mail/u/0/inbox/")
    assert screen["items"][5]["url"].endswith(".substack.com/p/" + screen["items"][5]["url"].rsplit("/", 1)[1])
    assert out["actions"][1]["detail"] == "mail.google.com in Brave Browser"
    assert out["mac"]["browsers"]["Brave Browser"][1] == "Work"
    assert out["laya"][0]["probs"] == [0.9, 0.1] and out["events"] == PRIVATE["events"]
    assert out["laya"][0]["read"] == 0 and set(out["laya"][0]["values"]) == {"asked", "page"}  # placeholders' names kept
    assert leaks(out) == [] and leaks(PRIVATE) != []


def test_the_same_word_becomes_the_same_stand_in_everywhere_so_checkpoints_still_match():
    out = scrub_record(PRIVATE, "test-salt")
    typed = out["actions"][0]["text"]
    assert out["task"].split()[1] == typed  # the address in the task is the one typed
    assert out["laya"][0]["values"]["asked"].split()[1] == typed  # a placeholder's value is scrubbed like typed text
    plan = out["brain"][0]["lines"][0]
    trip = plan.split()[1]
    assert STANDIN.match(trip.lower()) and trip in out["ledger"][0] and trip[0].isupper()
    assert out["memory"][0]["result"].split()[0] == out["screens"][0]["items"][1]["name"].split()[0]
    assert scrub_record(PRIVATE, "test-salt") == out  # deterministic
    other = scrub_record(PRIVATE, "another-salt")
    assert other["task"] != out["task"]  # and the stand-ins depend on the salt, so a name cannot be guessed back


def test_stand_ins_keep_the_shape_of_a_word_and_of_a_number():
    s = Scrubber("salt")
    assert s.word("Priya")[0].isupper() and s.word("PRIYA").isupper() and s.word("priya").islower()
    assert len(s.word("9845012345")) == 10 and s.word("9845012345").isdigit() and s.word("9845012345") != "9845012345"
    assert s.word("12") == "12" and s.word("Send") == "Send" and s.word(s.word("Priya")) == s.word("Priya")
    assert s.text("Message sent to Priya at 9:41") == f"Message sent to {s.word('Priya')} at 9:41"


def test_a_site_is_its_own_domain_never_a_subdomain_that_can_name_someone():
    assert site_labels("mail.google.com") == ["google", "com"]
    assert site_labels("www.example.co.uk") == ["example", "co", "uk"]
    assert site_labels("priyaraman.substack.com") == ["substack", "com"]


def test_promote_run_writes_a_scrubbed_case_that_replays(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("promote_run", CASES.parent.parent / "scripts" / "promote_run.py")
    promote = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(promote)
    record = json.loads((CASES / "seed-stale-after-open" / "run.json").read_text())
    recording = tmp_path / "rec.json"
    laya = [{**q, "values": {"wanted": "Kiln firing"}, "read": 0} for q in record["laya"]]  # a question's screen and values
    recording.write_text(json.dumps({**record, "laya": laya, "scrubbed": False}))
    assert promote.main([str(recording), "again", "--out", str(tmp_path), "--salt", "s"]) == 0
    case, expected = load_case(tmp_path / "again")
    assert case["scrubbed"] and expected == {"done": True, "brain_calls": 1, "why": ""} and leaks(case) == []
    assert case["laya"][0]["read"] == 0 and "Kiln" not in case["laya"][0]["values"]["wanted"]
    assert promote.main([str(recording), "again", "--out", str(tmp_path), "--salt", "s", "--expect", "failed", "--force"]) == 2
