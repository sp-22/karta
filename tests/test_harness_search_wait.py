"""A search is judged only once the page moved after it was sent (harness.md, Tools, "find"). Made-up apps only."""

from __future__ import annotations

from test_harness_tools import FakeEnv, FakeLaya, page, toolbox

from karta.harness.types import Item, Outcome

BOX = Item("Search repos", "field", search=True, focused=True)


def test_find_waits_for_the_page_to_change_after_return_before_it_says_no_results():
    """The results page comes a moment after Return (an interstitial first): one quick re-read saw the old page and
    called it no results."""
    home = page("Brave Browser", BOX, Item("Explore", "heading"), url="https://forge.example/explore")
    results = page(
        "Brave Browser", Item("Search repos", "field", value="inkpad", search=True), Item("ada/inkpad", "link"),
        Item("lin/inkpad-lite", "link"), url="https://forge.example/explore?q=inkpad",
    )  # fmt: skip
    env = FakeEnv({"home": home, "results": results}, "home")
    waits: list[int] = []

    def slow(e: FakeEnv) -> None:  # the results page shows on the second wait after Return
        if "press return" in e.log:
            waits.append(1)
        if len(waits) >= 2:
            e.at = "results"

    env.on[("home", "wait")] = slow
    result = toolbox(env, FakeLaya()).tool_find("inkpad", "a markdown editor repository")
    assert result.outcome is not Outcome.NO_RESULTS, (result, env.log)
    assert "press return" in env.log and env.at == "results"
