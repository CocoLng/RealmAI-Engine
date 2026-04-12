"""Tests for PotionSelectView."""

from __future__ import annotations

from bot.views.potion_select_view import PotionSelectView


def test_potion_select_creates_options() -> None:
    """Options are built from potion name list."""
    async def noop(name: str) -> None:
        pass

    view = PotionSelectView(
        potion_names=["Healing Potion", "Greater Healing Potion"],
        user_id=123,
        on_choice=noop,
    )
    options = view.select.options
    assert len(options) == 2
    assert options[0].label == "Healing Potion"
    assert options[1].label == "Greater Healing Potion"


def test_potion_select_empty_shows_placeholder() -> None:
    """Empty list → sentinel '__none__' option."""
    async def noop(name: str) -> None:
        pass

    view = PotionSelectView(potion_names=[], user_id=123, on_choice=noop)
    assert len(view.select.options) == 1
    assert view.select.options[0].value == "__none__"
