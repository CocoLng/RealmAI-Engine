"""Tests for bot/views/combat_action_view.py and the secondary selects (task 63).

Discord interactions are mocked so tests run without a running bot. We
verify that:

- The hub view exposes exactly 5 buttons with the right labels.
- Buttons are disabled when their precondition is not met (no enemies,
  no castable spells, no adjacent zones).
- ``interaction_check`` blocks users other than the active combatant.
- Instant actions (Defend, Flee) produce the right ``InterpretedAction``
  and forward it to the dispatch callback.
- The secondary select views forward the chosen value to ``on_choice``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.models import InterpretedAction
from bot.views.combat_action_view import CombatActionView
from bot.views.spell_select_view import SpellSelectView
from bot.views.target_select_view import TargetSelectView
from bot.views.zone_select_view import ZoneSelectView
from engine.validators import ActionType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_view(
    *,
    targets: list[str] | None = None,
    spells: list[str] | None = None,
    zones: list[str] | None = None,
    user_id: int = 42,
) -> tuple[CombatActionView, AsyncMock]:
    dispatch = AsyncMock()
    view = CombatActionView(
        user_id=user_id,
        actor_name="Aragorn",
        target_names=targets if targets is not None else ["Gobelin"],
        spell_names=spells if spells is not None else ["Magic Missile"],
        adjacent_zone_names=zones if zones is not None else ["Nef"],
        dispatch_callback=dispatch,
    )
    return view, dispatch


def _fake_interaction(user_id: int = 42) -> Any:
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.message = MagicMock()
    interaction.message.edit = AsyncMock()
    return interaction


def _find_button(view: CombatActionView, label: str) -> Any:
    for child in view.children:
        if getattr(child, "label", None) == label:
            return child
    raise AssertionError(f"Button {label!r} not found on view")


# ---------------------------------------------------------------------------
# CombatActionView structure
# ---------------------------------------------------------------------------


class TestCombatActionViewStructure:
    def test_has_five_buttons(self) -> None:
        view, _ = _make_view()
        button_labels = {
            getattr(c, "label", None) for c in view.children
        }
        assert {"Attaquer", "Sort", "Défendre", "Fuir", "Se déplacer"} <= button_labels

    def test_attack_disabled_when_no_targets(self) -> None:
        view, _ = _make_view(targets=[])
        assert _find_button(view, "Attaquer").disabled is True

    def test_spell_disabled_when_no_known_spells(self) -> None:
        view, _ = _make_view(spells=[])
        assert _find_button(view, "Sort").disabled is True

    def test_move_disabled_when_no_adjacent_zones(self) -> None:
        view, _ = _make_view(zones=[])
        assert _find_button(view, "Se déplacer").disabled is True

    def test_defend_always_enabled(self) -> None:
        view, _ = _make_view(targets=[], spells=[], zones=[])
        assert _find_button(view, "Défendre").disabled is False
        assert _find_button(view, "Fuir").disabled is False


# ---------------------------------------------------------------------------
# Interaction check
# ---------------------------------------------------------------------------


class TestInteractionCheck:
    @pytest.mark.asyncio
    async def test_blocks_other_players(self) -> None:
        view, _ = _make_view(user_id=42)
        interaction = _fake_interaction(user_id=99)
        allowed = await view.interaction_check(interaction)
        assert allowed is False
        interaction.response.send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_allows_acting_player(self) -> None:
        view, _ = _make_view(user_id=42)
        interaction = _fake_interaction(user_id=42)
        allowed = await view.interaction_check(interaction)
        assert allowed is True
        interaction.response.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# Instant-action dispatch
# ---------------------------------------------------------------------------


class TestInstantDispatch:
    @pytest.mark.asyncio
    async def test_defend_button_dispatches_defend(self) -> None:
        view, dispatch = _make_view()
        interaction = _fake_interaction()
        # @ui.button wraps the method in an _ItemCallback that captures
        # `self`; external callers just pass the interaction.
        defend_button = _find_button(view, "Défendre")
        await defend_button.callback(interaction)
        dispatch.assert_awaited_once()
        arg = dispatch.await_args.args[0]
        assert isinstance(arg, InterpretedAction)
        assert arg.action_type == ActionType.DEFEND
        assert arg.actor_name == "Aragorn"

    @pytest.mark.asyncio
    async def test_flee_button_dispatches_flee(self) -> None:
        view, dispatch = _make_view()
        interaction = _fake_interaction()
        flee_button = _find_button(view, "Fuir")
        await flee_button.callback(interaction)
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0].action_type == ActionType.FLEE


# ---------------------------------------------------------------------------
# Secondary select chains
# ---------------------------------------------------------------------------


class TestTargetSelectView:
    @pytest.mark.asyncio
    async def test_forwards_choice(self) -> None:
        on_choice = AsyncMock()
        view = TargetSelectView(
            target_names=["Gobelin", "Orc"], user_id=42, on_choice=on_choice,
        )
        view.select._values = ["Gobelin"]  # discord.py internal state
        interaction = _fake_interaction()
        await view._on_selected(interaction)
        on_choice.assert_awaited_once_with("Gobelin")
        interaction.response.edit_message.assert_awaited_once()

    def test_empty_list_produces_placeholder(self) -> None:
        on_choice = AsyncMock()
        view = TargetSelectView(
            target_names=[], user_id=42, on_choice=on_choice,
        )
        assert len(view.select.options) == 1
        assert view.select.options[0].value == "__none__"

    def test_caps_at_25_options(self) -> None:
        on_choice = AsyncMock()
        names = [f"Gobelin{i}" for i in range(40)]
        view = TargetSelectView(
            target_names=names, user_id=42, on_choice=on_choice,
        )
        assert len(view.select.options) == 25


class TestSpellSelectView:
    @pytest.mark.asyncio
    async def test_forwards_choice(self) -> None:
        on_choice = AsyncMock()
        view = SpellSelectView(
            spell_names=["Magic Missile"], user_id=42, on_choice=on_choice,
        )
        view.select._values = ["Magic Missile"]
        interaction = _fake_interaction()
        await view._on_selected(interaction)
        on_choice.assert_awaited_once_with("Magic Missile")


class TestZoneSelectView:
    @pytest.mark.asyncio
    async def test_forwards_choice(self) -> None:
        on_choice = AsyncMock()
        view = ZoneSelectView(
            zone_names=["Nef"], user_id=42, on_choice=on_choice,
        )
        view.select._values = ["Nef"]
        interaction = _fake_interaction()
        await view._on_selected(interaction)
        on_choice.assert_awaited_once_with("Nef")

    def test_default_description_is_distance_one(self) -> None:
        view = ZoneSelectView(
            zone_names=["Nef"], user_id=42, on_choice=AsyncMock(),
        )
        assert view.select.options[0].description == "Distance 1"
