"""macos.py provides the whole Desktop surface, it is the host, and a block can swap in another adapter."""

import inspect
from types import SimpleNamespace

import pytest

from karta.base import macos
from karta.base.platform_adapter import Desktop, current, desktop, host, using

SURFACE = sorted(name for name, value in vars(Desktop).items() if callable(value) and not name.startswith("_"))


def shape(function) -> list[tuple[str, object, object]]:
    """Parameter names, kinds and defaults: what a caller relies on, without the annotations."""
    params = list(inspect.signature(function).parameters.values())
    if params and params[0].name == "self":
        params = params[1:]
    return [(p.name, p.kind, p.default) for p in params]


# Read at collection, before the conftest guard swaps the machine-touching functions for refusals.
PROVIDED = {name: shape(getattr(macos, name)) if callable(getattr(macos, name, None)) else None for name in SURFACE}


@pytest.mark.parametrize("name", SURFACE)
def test_macos_provides_the_desktop_surface(name):
    provided = PROVIDED[name]
    assert provided is not None, f"macos lacks {name}"
    assert provided == shape(getattr(Desktop, name))


def test_the_surface_covers_what_the_callers_use():
    assert len(SURFACE) >= 20 and {"click_at", "recognize_text", "actionable_elements"} <= set(SURFACE)


def test_macos_is_the_host():
    assert host is macos and current() is macos


def fake_desktop() -> SimpleNamespace:
    return SimpleNamespace(frontmost_app_and_pid=lambda: ("Fake", 7), click_at=lambda point: None)


def test_using_swaps_the_adapter_for_the_block_and_restores_the_host():
    fake = fake_desktop()
    with using(fake) as swapped:
        assert swapped is fake and current() is fake
        assert desktop.frontmost_app_and_pid() == ("Fake", 7)
    assert current() is host


def test_using_restores_the_host_after_an_exception():
    with pytest.raises(RuntimeError, match="inside"), using(fake_desktop()):
        raise RuntimeError("inside")
    assert current() is host


def test_blocks_nest_and_unwind_in_order():
    outer, inner = fake_desktop(), fake_desktop()
    with using(outer):
        with using(inner):
            assert current() is inner
        assert current() is outer
    assert current() is host


def test_a_patch_through_desktop_inside_the_block_lands_on_the_fake(monkeypatch):
    fake = fake_desktop()
    fake_click_at, host_click_at = fake.click_at, host.click_at
    clicked = []
    with using(fake), monkeypatch.context() as patch:
        patch.setattr(desktop, "click_at", clicked.append)
        desktop.click_at((1.0, 2.0))
        assert fake.click_at == clicked.append
        assert host.click_at is host_click_at
    assert clicked == [(1.0, 2.0)]
    assert fake.click_at is fake_click_at, "the undo went to the fake, inside the block"
    assert host.click_at is host_click_at


def test_a_patch_through_desktop_outside_any_block_lands_on_the_host(monkeypatch):
    host_screenshot = host.screenshot
    with monkeypatch.context() as patch:
        patch.setattr(desktop, "screenshot", lambda: "patched")
        assert host.screenshot() == "patched"
    assert host.screenshot is host_screenshot


def test_deletes_go_to_the_adapter_in_use():
    fake = fake_desktop()
    with using(fake):
        del desktop.frontmost_app_and_pid
        with pytest.raises(AttributeError):
            desktop.frontmost_app_and_pid  # noqa: B018
    assert not hasattr(fake, "frontmost_app_and_pid")
