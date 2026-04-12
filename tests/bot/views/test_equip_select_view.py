"""Tests for EquipSelectView."""

from __future__ import annotations

from bot.views.equip_select_view import EquipSelectView


def test_equip_select_creates_options() -> None:
    async def noop(name: str) -> None:
        pass

    view = EquipSelectView(
        weapon_names=["Shortbow", "Dagger"],
        user_id=123,
        on_choice=noop,
        descriptions={"Shortbow": "1d6 perçant", "Dagger": "1d4 perçant"},
    )
    options = view.select.options
    assert len(options) == 2
    assert options[0].label == "Shortbow"


def test_equip_select_empty_shows_placeholder() -> None:
    async def noop(name: str) -> None:
        pass

    view = EquipSelectView(weapon_names=[], user_id=123, on_choice=noop)
    assert len(view.select.options) == 1
    assert view.select.options[0].value == "__none__"
