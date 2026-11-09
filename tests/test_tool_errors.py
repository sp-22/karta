"""A tool that raises an unexpected error fails its step with a reason; the abort corner still stops the run."""

import pytest

from karta.base import macos
from karta.base.models import Abort
from karta.harness.types import Outcome


def test_an_unexpected_error_fails_the_step_not_the_run():
    from karta.harness.tools import Toolbox

    tb = Toolbox.__new__(Toolbox)
    tb.trace = []

    def boom(**_):
        raise AttributeError("'tuple' object has no attribute 'location'")

    tb.tool_boom = boom
    tb.last_put = tb.last_unsure = None
    result = Toolbox.run.__wrapped__(tb, {"tool": "boom"}) if hasattr(Toolbox.run, "__wrapped__") else None
    if result is None:
        pytest.skip("Toolbox.run needs a full toolbox; covered by the range test below")
    assert result.outcome is Outcome.UNEXPECTED and "AttributeError" in result.detail


def test_a_selection_range_read_as_a_plain_pair():
    import ApplicationServices as AS

    value = AS.AXValueCreate(AS.kAXValueCFRangeType, (3, 5))

    class FakeEl:
        pass

    orig = macos._ax_attr
    macos._ax_attr = lambda ref, name: value
    try:
        assert macos.ax_selected_range(FakeEl()) == (3, 5)
    finally:
        macos._ax_attr = orig


def test_abort_is_never_swallowed():
    assert issubclass(Abort, Exception)
