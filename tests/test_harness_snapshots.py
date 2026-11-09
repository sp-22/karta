"""The opt-in run recorder (karta/harness/snapshots.py): what it keeps, what it leaves out, and its file format."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from karta.harness.snapshots import CUT, VERSION, Recorder, host, screen_back, screen_json
from karta.harness.types import Item, Screen

LONG = "x" * 200


def page() -> Screen:
    return Screen(
        "Brave Browser",
        [
            Item("Search", "field", value=LONG, region="top", focused=True, search=True),
            Item("ADD", "button", region="middle", row=("Crunchy Chips", "₹20")),
            Item("Address bar", "field", chrome=True, value="https://shop.example/s?q=secret"),
            Item("Allow", "button", dialog=True, browser_ui=True),
        ],
        url="https://shop.example/s?q=secret#frag",
        window="Shop",
        profile="Personal",
    )


class FakeEnv:
    def __init__(self):
        self.done, self.reads = [], 0

    def screen(self):
        self.reads += 1
        return page()

    def click(self, item):
        self.done.append(("click", item.name))

    def set_text(self, item, text):
        self.done.append(("set_text", item.name, text))

    def press(self, keys):
        self.done.append(("press", keys))

    def scroll(self, direction):
        self.done.append(("scroll", direction))

    def open_url(self, url, browser, profile=None):
        self.done.append(("open_url", url))


@dataclass
class FakeResult:
    done: bool = True
    reason: str = "done: shown"
    ledger: tuple = ("✓ click ADD",)
    steps: tuple = ({"tool": "click", "args": {"target": "ADD"}, "outcome": "ok", "detail": "clicked"},)


def test_host_keeps_only_the_host():
    assert host("https://www.shop.example/s?q=chips#x") == "www.shop.example"
    assert host("about:newtab") == "about:newtab" and host(None) is None


def test_screen_json_cuts_text_and_keeps_the_flags():
    data = screen_json(page())
    assert data["url"] == "https://shop.example/s" and data["profile"] == "Personal" and data["app"] == "Brave Browser"
    assert "frag" not in json.dumps(data)  # the page's address has no query or fragment (a field's text is its value)
    assert data["window"] == "Shop"
    first = data["items"][0]
    assert set(first) == {"name", "kind", "region", "row", "value", "focused", "enabled", "dialog", "chrome", "browser_ui"}
    assert len(first["value"]) == CUT and first["focused"]
    assert data["items"][1]["row"] == ["Crunchy Chips", "₹20"]
    assert data["items"][2]["chrome"] and data["items"][3]["browser_ui"] and data["items"][3]["dialog"]
    back = screen_back(data)
    assert back.items[1].row == ("Crunchy Chips", "₹20") and back.items[1].ref is None


def test_recorder_passes_actions_through_and_never_reads_the_screen_itself():
    env = FakeEnv()
    rec = Recorder(env, "add chips", "/nonexistent")
    rec.press("cmd+l")  # before any read: no screen
    scr = rec.screen()
    rec.click(scr.items[1])
    rec.set_text(scr.items[0], "chips " + LONG)
    rec.scroll("down")
    assert env.reads == 1
    assert env.done[1] == ("click", "ADD") and env.done[2][0] == "set_text" and env.done[3] == ("scroll", "down")
    tools = [(a["tool"], a["fresh"], a.get("target_index")) for a in rec.actions]
    assert rec.events == [{"act": 0}, {"read": 0}, {"act": 1}, {"act": 2}, {"act": 3}]
    assert tools == [("press", False, None), ("click", True, 1), ("set_text", False, 0), ("scroll", False, None)]
    assert rec.actions[0]["screen"] is None and rec.actions[1]["screen"]["items"][1]["name"] == "ADD"
    assert len(rec.actions[2]["text"]) == CUT


def test_finish_writes_one_json_file_per_task(tmp_path):
    env = FakeEnv()
    rec = Recorder(env, "Add Crunchy Chips to the cart!", tmp_path / "snaps")
    rec.click(rec.screen().items[1])
    rec.open_url("https://shop.example/private/path?token=abc", "Brave Browser")
    now = datetime(2026, 9, 30, 10, 0, 0)
    path = rec.finish(FakeResult(), now=now)
    assert path.name == "20260930-100000-add-crunchy-chips-to-the-cart.json"
    data = json.loads(path.read_text())
    assert data["version"] == VERSION and data["task"] == "Add Crunchy Chips to the cart!"
    assert data["done"] is True and data["ledger"] == ["✓ click ADD"] and data["reason"] == "done: shown"
    assert data["steps"] == [{"tool": "click", "outcome": "ok", "detail": "clicked"}]
    assert [a["tool"] for a in data["actions"]] == ["click", "open_url"]
    assert "token" not in path.read_text() and "private" not in path.read_text()
    assert data["final_screen"] is None  # no read after the last action
    second = rec.finish(FakeResult(), now=now)
    assert second != path and second.exists()


def test_a_stopped_run_is_saved_with_its_reason(tmp_path):
    rec = Recorder(FakeEnv(), "anything", tmp_path)
    rec.screen()
    data = json.loads(rec.finish(None, reason="stopped by you").read_text())
    assert data["done"] is False and data["reason"] == "stopped by you" and data["ledger"] == []
    assert data["final_screen"]["url"] == "https://shop.example/s"


def test_a_write_failure_is_reported_not_raised(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert Recorder(FakeEnv(), "t", blocker / "sub").finish(None) is None
    assert "snapshot not saved" in capsys.readouterr().out


def test_the_app_records_only_when_asked():
    import inspect

    from karta import app

    source = inspect.getsource(app.Karta.run)
    assert "if self.record:" in source and "Recorder(shown" in source
    assert "record: str | None = None" in inspect.getsource(app.Karta.__init__)


def test_a_link_keeps_its_address_without_the_query_and_flags_only_when_set():
    data = screen_json(
        Screen(
            "Safari", [Item("Read more", "link", url="https://news.example/a/1?utm=x#top"), Item("Body", "field", multiline=True)]
        )
    )
    assert data["items"][0]["url"] == "https://news.example/a/1" and data["items"][1]["multiline"] is True
    assert "url" not in data["items"][1] and "checked" not in data["items"][0]
    back = screen_back(data)
    assert back.items[0].url == "https://news.example/a/1" and back.items[1].multiline


def test_a_version_1_screen_reads_back_with_its_host():
    assert screen_back({"app": "Safari", "url": "shop.example", "items": []}).url == "https://shop.example/"


class LineBrain:
    def stream_lines(self, system, user):
        yield "click ADD"
        yield "done: Added"


class Agent:
    temperature_by_options: ClassVar[dict] = {"choice:11+": 0.5}

    def system_one(self, state, spec):
        options = list(spec["q"]["criteria"])
        return {"answers": {"q": {"probabilities": {o: 1.0 / len(options) for o in options}}}}


class Memory:
    def place_for(self, what):
        return "Shop" if what == "groceries" else None

    def place_result(self, *args):
        self.wrote = args


def test_wrap_records_the_brain_laya_memory_the_user_and_the_mac():
    from datetime import datetime as dt

    from karta.harness.brain import PLAN_SYS, HarnessBrain
    from karta.harness.decide import Laya
    from karta.harness.facts import MacMap, Tab
    from karta.harness.types import MacFacts

    rec = Recorder(FakeEnv(), "add chips", "/nonexistent")
    mac = MacMap(
        MacFacts(["Safari"], {"Brave Browser": ["Personal"]}, ["~/Projects"], dt(2026, 10, 1, 9, 0)),
        tabs=[Tab("Brave Browser", 1, 1, "https://shop.example/cart?id=9", "Cart", "Personal")],
    )
    laya = Laya(Agent(), lambda texts: [[1.0, 0.0], [0.6, 0.8]][: len(texts)], gates={})
    parts = rec.wrap(
        laya=laya, brain=HarnessBrain(LineBrain()), facts=mac, memory=Memory(), confirm=lambda q: True, ask=lambda q: "yes"
    )
    lines = list(parts["brain"].brain.stream_lines(PLAN_SYS, "user"))
    empty = {"memory": [], "profile": [], "pinned": []}
    assert lines == ["click ADD", "done: Added"]
    assert rec.brain_log == [{"kind": "plan", "via": "lines", "lines": lines, "context": empty}]
    choice = parts["laya"].ask("D3 target", {"wanted": "x"}, "Which one?", {"a": None, "b": None})
    assert choice.ranked[0][1] == 0.5 and rec.laya_log == [
        {"instructions": "Which one?", "values": {"wanted": "x"}, "read": None, "options": ["a", "b"], "probs": [0.5, 0.5]}
    ]
    parts["laya"].embed(["q", "t"])
    assert rec.embeds == [[1.0, 0.6]] and rec.laya_temperatures == {"choice:11+": 0.5}
    assert laya.agent.__class__ is Agent  # the caller's Laya is left alone
    assert parts["memory"].place_for("groceries") == "Shop"
    parts["memory"].place_result("groceries", "Shop", True)  # a write passes through, unrecorded
    assert rec.memory_log == [{"call": "place_for", "args": ["groceries"], "result": "Shop"}]
    assert parts["confirm"]("Send it?") is True and parts["ask"]("Which?") == "yes"
    assert rec.confirms == [{"question": "Send it?", "answer": True}] and rec.asks == [{"question": "Which?", "reply": "yes"}]
    assert rec.mac["tabs"][0]["url"] == "https://shop.example/cart" and rec.mac["browsers"] == {"Brave Browser": ["Personal"]}
    assert "refresh" in parts and parts["facts"] is mac


def test_the_env_s_change_waits_are_recorded_with_their_answers():
    class Waiting(FakeEnv):
        def wait_change(self, timeout):
            return True

        def changed_since(self, before, timeout):
            return False

    rec = Recorder(Waiting(), "t", "/nonexistent")
    assert rec.wait_change(1.0) is True and rec.changed_since("x", 1.0) is False
    assert rec.events == [{"wait_change": True}, {"changed_since": False}]
    assert not hasattr(Recorder(FakeEnv(), "t", "/nonexistent"), "wait_change")  # an env without one stays without


def test_a_laya_question_keeps_the_screen_read_it_was_asked_against_and_its_values_cut():
    from karta.harness.decide import Laya

    rec = Recorder(FakeEnv(), "add chips", "/nonexistent")
    laya = rec.wrap(laya=Laya(Agent(), None, gates={}))["laya"]
    rec.screen()
    state = {"wanted": "w" * 200, "page": "'Deal' link https://shop.example/p/9?ref=mail#top\n'Cart' button"}
    laya.ask("D7 outcome", state, "Does `page` show `wanted`?", {"a": None, "b": None})
    (entry,) = rec.laya_log
    assert entry["read"] == 0 and rec.events[0] == {"read": 0}
    assert len(entry["values"]["wanted"]) == CUT
    assert entry["values"]["page"] == "'Deal' link https://shop.example/p/9\n'Cart' button"  # no query, no fragment


def test_laya_dataset_turns_a_recording_into_lines_to_label(tmp_path):
    import importlib.util
    from pathlib import Path

    from karta.harness.decide import Laya

    spec = importlib.util.spec_from_file_location("laya_dataset", Path(__file__).parents[1] / "scripts" / "laya_dataset.py")
    dataset = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dataset)
    rec = Recorder(FakeEnv(), "add chips", tmp_path)
    laya = rec.wrap(laya=Laya(Agent(), None, gates={}))["laya"]
    scr = rec.screen()
    laya.ask("D3 target", {"asked": "add chips"}, "Which control would a person use to do `asked`?", {"a": None, "b": None})
    path = rec.finish()
    out = tmp_path / "laya.jsonl"
    assert dataset.main([str(path), "-o", str(out)]) == 0
    (row,) = [json.loads(line) for line in out.read_text().splitlines()]
    assert row["values"] == {"asked": "add chips"} and row["options"] == ["a", "b"] and row["probs"] == {"a": 0.5, "b": 0.5}
    assert row["chosen"] in ("a", "b") and row["label"] == "" and row["task"] == "add chips"
    assert row["screen"].splitlines() == [i.describe() for i in screen_back(screen_json(scr)).items]


def test_each_brain_request_keeps_the_memory_profile_and_pins_it_was_given():
    from karta.harness.brain import PLAN_SYS_V2, HarnessBrain
    from karta.harness.replay import Replay, ReplayBrain

    rec = Recorder(FakeEnv(), "start a project called Garden in Tallybook", "/nonexistent")
    hb = rec.wrap(brain=HarnessBrain(LineBrain(), prompt="v2"))["brain"]
    know = [
        "Known about Tallybook (worked in 2 proven runs):",
        '- new project: click "Start project" → a dialog opened',
        "About the user (from tasks proven done):",
        "- email: jane.doe@example.com",
        "- browser in use: Safari",
    ]
    offers = [("r1", "start a project called {p1} in Tallybook")]
    list(hb.stream_plan("start a project called Garden in Tallybook", "Now: today", Screen("Finder", []), know, [], offers))
    assert rec.brain_log[0]["context"] == {
        "memory": know[:2],
        "profile": ["- email: …", "- browser in use: Safari"],  # the user's own detail as its key only
        "pinned": ["- r1: start a project called {p1} in Tallybook"],
    }
    assert "jane.doe" not in json.dumps(rec.record())
    replay = Replay({"brain": rec.brain_log})  # a replay reads the reply and ignores the context
    assert list(ReplayBrain(replay).stream_lines(PLAN_SYS_V2, "anything")) == ["click ADD", "done: Added"]
    assert replay.problems == []
