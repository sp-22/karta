"""Memory reaches the plan: how a proven step worked kept beside its element, pitfalls ("not X but Y"), the user's own
details, and the block the plan and rescue prompts get (memory.brief, memory.profile, loop.know_how). Made-up sites
and apps only."""

from __future__ import annotations

import sqlite3

import pytest
from test_harness_memory import cli
from test_harness_memory_trust import NO_TARGET, RESCUED, shop_env, yes
from test_harness_tools import BTN, FACTS, FakeEnv, FakeLaya, FakeLineBrain, page

from karta.harness.brain import HarnessBrain, _context
from karta.harness.loop import change_of, know_how, own_details, remember, run_task, way
from karta.harness.memory import ELEMENT, PITFALL, PROFILE, HarnessMemory
from karta.harness.types import Item, Outcome, Screen, ToolResult

pytestmark = pytest.mark.v1  # these tests plan in the v1 format (then: words)

REELS = "https://www.reelhub.tv/"

PROOF = "created(a made-up thing)"  # the contract a proven run met: what memory keeps rows by


def reels_env() -> FakeEnv:
    """A made-up video site: a search field and a Create button; return in the field opens /results."""
    home = page("Brave Browser", Item("Search", "field", search=True), Item("Create", BTN), Item("For you", "heading"), url=REELS)
    results = page(
        "Brave Browser",
        Item("Search", "field", value="lofi beats", search=True),
        Item("Filters", BTN),
        Item("Lofi beats to study to", "link"),
        url=REELS + "results?q=lofi+beats",
    )
    return FakeEnv({"home": home, "results": results}, "home", {("home", "press return"): "results"})


SEARCH_PLAN = "contract: opened(lofi beats)\nwrite lofi beats | where: search box\nsubmit | expect: Filters\ndone: Filters"


# ---------------------------------------------------------------- writing rows: how it worked, pitfalls


def test_a_proven_search_keeps_how_it_worked_beside_its_element():
    memory = HarnessMemory(":memory:")
    result = run_task("search reelhub for lofi beats", reels_env(), FakeLaya(), FakeLineBrain(SEARCH_PLAN), FACTS, memory, yes)
    assert result.done, result.trace
    [row] = memory.list(ELEMENT)
    assert (row.app, row.key, row.value, row.how) == (
        "reelhub.tv",
        "search box",
        "Search",
        "write|pasted|Return|opened page /results",
    )
    assert memory.brief(["reelhub.tv"]) == [
        "Known about reelhub.tv (worked in 1 proven run):",
        '- search box: type into "Search" (pasted), then Return → opened page /results',
    ]


def test_nothing_is_kept_from_a_run_not_proven_done():
    memory = HarnessMemory(":memory:")
    brain = FakeLineBrain("write lofi beats | where: search box\nsubmit | expect: Filters\ndone: Nowhere to be seen")
    result = run_task("search reelhub with my email a.b@example.com", reels_env(), FakeLaya(), brain, FACTS, memory, yes)
    assert not result.done
    assert memory.list() == []


def test_a_rescued_steps_plan_words_are_never_kept_as_a_pitfall(tmp_path):
    """The failed step's target is the plan's description, never a name read on screen: no "not X but Y" for it."""
    memory = HarnessMemory(tmp_path / "m.sqlite")
    result = run_task("search the grocery aisles", shop_env(), NO_TARGET, FakeLineBrain(*RESCUED), FACTS, memory, yes)
    assert result.done, result.trace
    assert memory.list(PITFALL) == []
    assert not any("the search bar" in line for line in memory.brief(["pantrycart.com"]))


class _Tools:
    """What `way` reads of the toolbox."""

    task = "find a clip"
    submitted = None


def test_a_wrong_pick_before_the_right_one_is_a_pitfall_and_a_later_success_clears_it():
    before = page("Brave Browser", Item("Create", BTN), Item("Search", BTN), url=REELS)
    after = page("Brave Browser", Item("Search", "field", focused=True, search=True), url=REELS)
    ways, pitfalls = {}, []
    picks = [("reelhub.tv", "the search control", "Search", True, False)]
    ok = ToolResult(Outcome.OK, "")
    way(
        _Tools(),
        {"tool": "click", "target": "the search control"},
        ok,
        None,
        before,
        after,
        picks,
        ways,
        None,
        pitfalls,
        ["Create"],
        None,
    )
    assert pitfalls == [("reelhub.tv", "the search control", "Create", "Search")]
    assert ways[("reelhub.tv", "the search control", "Search")] == "click|||a new focused field"
    memory = HarnessMemory(":memory:")
    memory.add_pitfall(*pitfalls[0])
    assert '- pitfall: for the search control, not "Create" but "Search"' in memory.brief(["reelhub.tv"])
    memory.element_result("reelhub.tv", "the search control", "Create", True, how="click|||")
    assert memory.list(PITFALL) == []  # "Create" worked for it after all


def test_a_content_item_keeps_no_way():
    before = page("Brave Browser", Item("Lofi beats to study to", "link"), url=REELS)
    ways = {}
    picks = [("reelhub.tv", "the first video", "Lofi beats to study to", True, False)]
    way(_Tools(), {"tool": "click"}, ToolResult(Outcome.OK, ""), None, before, before, picks, ways, None, [], [], None)
    assert ways == {}


def test_the_kind_of_change_is_built_by_code_and_holds_no_content():
    base = page("Brave Browser", Item("New", BTN), url=REELS)
    assert change_of(base, page("Brave Browser", url=REELS + "results?q=x"), "find x") == "opened page /results"
    assert change_of(base, page("Brave Browser", url=REELS + "lofi-beats/9"), "play lofi beats") == "opened another page"
    assert change_of(base, page("Brave Browser", url=REELS + "c/9"), "x") == "opened another page"
    assert change_of(base, page("Brave Browser", url="https://help.othersite.io/a"), "x") == "opened othersite.io"
    dialog = page("Brave Browser", Item("Title", "field", dialog=True), Item("Cancel", BTN, dialog=True), url=REELS)
    assert change_of(base, dialog, "x") == "a dialog opened"
    row = Screen("Listly", [Item("Groceries", "row"), Item("", "field", focused=True)])
    assert change_of(Screen("Listly", [Item("Groceries", "row")]), row, "x") == "a new focused field"
    assert change_of(Screen("Listly", []), Screen("Listly", [Item("a", "row")]), "x") == "a new row"
    assert change_of(base, base, "x") == ""


# ---------------------------------------------------------------- the block


def test_the_block_is_capped_stable_and_skips_rows_without_a_way():
    memory = HarnessMemory(":memory:")
    memory.place_result("reelhub", "www.reelhub.tv in Brave Browser, profile Work", True)
    memory.element_result("reelhub.tv", "old pick", "Upload", True)  # from before: serves picks at step time only
    for n in range(12):
        memory.element_result("reelhub.tv", f"control {n:02}", f"Control {n:02}", True, how="click|||a dialog opened")
    memory.add_know_how("reelhub.tv", "results are links in the main column")
    for app in ("Listly", "Tallybook", "Fernpad"):
        memory.element_result(app, "new item", "Add", True, how="click|||a new row")
    block = memory.brief(["reelhub.tv", "Listly", "Tallybook", "Fernpad", ""])
    assert block[:3] == [
        "Known about reelhub.tv (worked in 1 proven run):",
        "- opens in: Brave Browser, profile Work",
        '- control 00: click "Control 00" → a dialog opened',
    ]
    reel = block[: block.index("Known about Listly (worked in 1 proven run):")]
    assert len(reel) == 1 + 8 and not any("Upload" in line for line in reel)
    assert "Known about Fernpad (worked in 1 proven run):" not in block  # three places at most
    assert memory.brief(["reelhub.tv", "Listly", "Tallybook", "Fernpad"]) == block  # the same every time


def test_know_how_collects_the_screen_the_named_sites_apps_and_remembered_places():
    memory = HarnessMemory(":memory:")
    memory.place_result("reelhub", "www.reelhub.tv in Brave Browser, profile Work", True)
    memory.element_result("reelhub.tv", "search box", "Search", True, how="write|pasted|Return|opened page /results")
    memory.element_result("Notes", "new note", "New Note", True, how="click|||a new focused field")
    finder = page("Finder", Item("Desktop", "row"))
    block = know_how(memory, "find a cooking clip on reelhub and save it in Notes", finder, FACTS)
    assert block[0] == "Known about Notes (worked in 1 proven run):"  # an app the task names
    assert "Known about reelhub.tv (worked in 1 proven run):" in block  # a place whose intent the task names
    assert know_how(memory, "make a note", finder, FACTS)[0] == block[0]  # named in the singular
    assert know_how(memory, "rename a file", finder, FACTS) == []


def test_the_block_reaches_the_plan_and_the_rescue_under_both_prompts():
    memory = HarnessMemory(":memory:")
    memory.element_result("reelhub.tv", "search box", "Search", True, how="write|pasted|Return|opened page /results")
    memory.add_profile("email", "jane.doe@example.com")
    brain = FakeLineBrain("stop: nothing to do", "stop: nothing to do")
    run_task("find a clip on reelhub.tv", reels_env(), FakeLaya(), brain, FACTS, memory, yes)
    user = brain.asked[0][1]
    assert '- search box: type into "Search" (pasted), then Return → opened page /results' in user
    assert "About the user (from tasks proven done):\n- email: jane.doe@example.com" in user
    lines = know_how(memory, "find a clip on reelhub.tv", reels_env().screen(), FACTS)
    for v2 in (False, True):
        assert "\n".join(lines) in _context("find a clip", "Now: today", reels_env().screen(), lines, (), v2)
    rescue = FakeLineBrain("stop: no")
    HarnessBrain(rescue, prompt="v2").rescue(
        "find a clip", "Now: today", reels_env().screen(), lines, [], {"tool": "click", "target": "x"},
        ToolResult(Outcome.NOT_FOUND, ""), {}, [],
    )  # fmt: skip
    assert "Known about reelhub.tv" in rescue.asked[0][1]


# ---------------------------------------------------------------- the user's profile


def test_own_details_come_only_from_what_the_user_said():
    assert own_details("sign up with my email jane.doe@example.com") == [("email", "jane.doe@example.com")]
    assert own_details("send the deck to sam.lee@example.org") == []  # someone else's
    assert own_details("my name is Jane Doe and I need a table") == [("name", "Jane Doe")]
    assert own_details("check my name is on the list") == []
    replied = "book it\n(The user, asked 'What is your email address?', said: jd@example.com)"
    assert own_details(replied) == [("email", "jd@example.com")]
    assert own_details("book it\n(The user, asked 'Which day?', said: jd@example.com)") == []


def test_a_proven_run_keeps_the_profile_and_never_reads_it_off_the_screen():
    memory = HarnessMemory(":memory:")
    env = reels_env()
    env.pages["results"].items.append(Item("signed in as other.person@example.com", "text"))
    task = "search reelhub for lofi beats, my email is jane.doe@example.com"
    result = run_task(task, env, FakeLaya(), FakeLineBrain(SEARCH_PLAN), FACTS, memory, yes)
    assert result.done, result.trace
    assert [(f.key, f.value) for f in memory.list(PROFILE)] == [("email", "jane.doe@example.com")]
    memory.add_profile("browser in use", "Brave Browser, profile Work")
    memory.remember_alias("asha", "Asha Rao", app="chirpline.io")
    assert memory.profile("message asha on chirpline") == [
        "About the user (from tasks proven done):",
        "- browser in use: Brave Browser, profile Work",
        "- email: jane.doe@example.com",
        '- "asha" is "Asha Rao" on chirpline.io',
    ]
    assert not any("asha" in line for line in memory.profile("message Bea"))  # an alias only when the task names it


def test_remember_keeps_the_browser_in_use_and_the_default_browser():
    from test_harness_tools import toolbox

    from karta.harness.facts import Place

    memory = HarnessMemory(":memory:")
    tb = toolbox(FakeEnv({"a": page("Finder")}, "a"), memory=memory)
    tb.places["reels"] = Place("www.reelhub.tv in Brave Browser, profile Work", "Brave Browser", REELS, "Work")
    tb.default_browser = "Brave Browser"
    remember(memory, tb, [], proof=PROOF)
    assert sorted((f.key, f.value) for f in memory.list(PROFILE)) == [
        ("browser in use", "Brave Browser, profile Work"),
        ("default browser", "Brave Browser"),
    ]


def test_the_profile_is_shown_and_forgotten_by_the_cli(tmp_path, capsys):
    path = tmp_path / "m.sqlite"
    HarnessMemory(path).add_profile("email", "jane.doe@example.com")
    cli("--path", str(path), "show", "--kind", "profile")
    assert "profile   email -> jane.doe@example.com" in capsys.readouterr().out
    cli("--path", str(path), "forget", "profile", "email", "--value", "jane.doe@example.com")
    assert "Forgot 1" in capsys.readouterr().out and HarnessMemory(path).profile() == []


# ---------------------------------------------------------------- files from before the `how` column


def test_a_file_from_before_the_how_column_opens_and_keeps_its_rows(tmp_path):
    path = tmp_path / "m.sqlite"
    db = sqlite3.connect(path)
    db.execute(
        "create table harness_facts (kind text not null, app text not null, key text not null, value text not null, "
        "good integer not null default 0, bad integer not null default 0, updated real not null, "
        "primary key (kind, app, key, value))"
    )
    db.execute("insert into harness_facts values ('element', 'Listly', 'new item', 'Add', 2, 0, 1e12)")
    db.execute("pragma user_version = 1")
    db.commit()
    db.close()
    assert [f.how for f in HarnessMemory(path, readonly=True).list()] == [""]
    memory = HarnessMemory(path)  # an element learned before contracts has no proof: dropped
    assert memory.list() == []
    memory.proving = PROOF
    memory.element_result("Listly", "new item", "Add", True, how="click|||a new row")
    assert [(f.good, f.how) for f in memory.list()] == [(1, "click|||a new row")]
    assert memory.db.execute("select proof from harness_facts").fetchone() == (PROOF,)  # each row keeps its proof


# ---------------------------------------------------------------- the bench's memory pairs (no brain)


def test_every_memory_pair_gets_its_block_and_a_plan_is_scored_against_it():
    from karta.harness import prompt_bench as pb

    assert len(pb.PAIRS) >= 10 and sum(p.case.held_out for p in pb.PAIRS) >= 6
    for pair in pb.PAIRS:
        assert pb.block(pair)[0].startswith("Known about "), pair.case.id
    steps = [
        {"tool": "click", "target": "Log spend"},
        {"tool": "fill", "fields": {"Amount (USD)": "40", "Category": "Office"}},
        {"tool": "click", "target": "Save draft"},
    ]
    score = pb.memory_score({"case": "m03", "steps": steps, "done": "", "errors": [], "ask": ""})
    assert (score["targets"], score["remembered"], score["guessed"], score["pitfall used"]) == (4, 2, 2, 1)


def test_the_memory_ab_bench_is_six_made_up_pairs_within_its_call_budget():
    from karta.harness import prompt_bench as pb

    pairs = [pb.PAIR_BY_ID[i] for i in pb.AB]
    assert len(pairs) == 6 and all(p.case.held_out for p in pairs)
    for pair in pairs:  # every block names a remembered control the student needs
        assert any(line.startswith("- ") and '"' in line for line in pb.block(pair)), pair.case.id
    budget = pb.Budget(lambda: FakeLineBrain(*["stop: x"] * 3), limit=2)
    brain = budget()
    assert list(brain.stream_lines("s", "u")) == ["stop: x"] and list(budget().stream_lines("s", "u")) == ["stop: x"]
    with pytest.raises(RuntimeError, match="2 brain calls are spent"):
        list(brain.stream_lines("s", "u"))
    guess = [{"tool": "click", "target": "Add sheet"}, {"tool": "write", "text": "Packing list", "where": "Body"}]
    score = pb.memory_score({"case": "m09", "steps": guess, "done": "", "errors": [], "ask": ""})
    assert score["looks"] == 1  # "Add sheet" names nothing Quillnote shows; its body field is one it does
