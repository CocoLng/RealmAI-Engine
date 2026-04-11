"""Combat action hub view (task 63).

The 5-button panel posted by the TurnManager on each PC turn. Clicking a
button either dispatches an instant action (Defend, Flee) or opens an
ephemeral follow-up select view (target, spell, zone). All resolution
goes through the ``dispatch_callback`` wired by the TurnManager — this
view never touches the pipeline or the combat engine directly.

The buttons whose pre-conditions are not met (no enemies, no castable
spells, no adjacent zones) are disabled up-front so players can see at a
glance what is available this turn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Awaitable, Callable

import discord
from discord import ui

from ai.models import InterpretedAction
from bot.views.base import LoggedView
from bot.views.spell_select_view import SpellSelectView
from bot.views.target_select_view import TargetSelectView
from bot.views.zone_select_view import ZoneSelectView
from engine.validators import ActionType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


DispatchCallback = Callable[[InterpretedAction], Awaitable[None]]


class CombatActionView(LoggedView):
    """Hub panel: 5 action buttons for the active PC combatant.

    The TurnManager creates a fresh instance per PC turn, attaches it to
    the hub message, and wires ``dispatch_callback`` so button presses
    flow back through :meth:`bot.combat_turn_manager.TurnManager.dispatch_action`.
    ``timeout`` is ``None`` because the TurnManager runs its own 5-minute
    asyncio watcher — we do not want two competing timeout mechanisms.
    """

    def __init__(
        self,
        *,
        user_id: int,
        actor_name: str,
        target_names: list[str],
        spell_names: list[str],
        adjacent_zone_names: list[str],
        dispatch_callback: DispatchCallback,
    ) -> None:
        super().__init__(timeout=None)
        self.user_id = user_id
        self.actor_name = actor_name
        self.target_names = target_names
        self.spell_names = spell_names
        self.adjacent_zone_names = adjacent_zone_names
        self.dispatch_callback = dispatch_callback

        # Disable buttons whose pre-conditions are not satisfied.
        self._attack_button.disabled = not target_names
        self._spell_button.disabled = not spell_names
        self._move_button.disabled = not adjacent_zone_names

    # ------------------------------------------------------------------
    # Interaction guard
    # ------------------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour.", ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        """Disable every button on timeout — TurnManager still drives the turn."""
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    @ui.button(
        label="Attaquer",
        style=discord.ButtonStyle.danger,
        emoji="⚔️",
        row=0,
    )
    async def _attack_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await self._open_target_select(
            interaction,
            targets=self.target_names,
            action_type=ActionType.ATTACK,
        )

    @ui.button(
        label="Sort",
        style=discord.ButtonStyle.primary,
        emoji="✨",
        row=0,
    )
    async def _spell_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await self._open_spell_select(interaction)

    @ui.button(
        label="Défendre",
        style=discord.ButtonStyle.secondary,
        emoji="🛡️",
        row=1,
    )
    async def _defend_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await interaction.response.defer()
        await self._disable_self_and_edit(interaction)
        await self.dispatch_callback(
            InterpretedAction(
                action_type=ActionType.DEFEND,
                actor_name=self.actor_name,
                raw_input="(bouton Défendre)",
            ),
        )

    @ui.button(
        label="Fuir",
        style=discord.ButtonStyle.secondary,
        emoji="🏃",
        row=1,
    )
    async def _flee_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await interaction.response.defer()
        await self._disable_self_and_edit(interaction)
        await self.dispatch_callback(
            InterpretedAction(
                action_type=ActionType.FLEE,
                actor_name=self.actor_name,
                raw_input="(bouton Fuir)",
            ),
        )

    @ui.button(
        label="Se déplacer",
        style=discord.ButtonStyle.secondary,
        emoji="🧭",
        row=1,
    )
    async def _move_button(
        self, interaction: discord.Interaction, button: ui.Button["CombatActionView"],
    ) -> None:
        del button
        await self._open_zone_select(interaction)

    # ------------------------------------------------------------------
    # Secondary view orchestration
    # ------------------------------------------------------------------

    async def _open_target_select(
        self,
        interaction: discord.Interaction,
        *,
        targets: list[str],
        action_type: ActionType,
        spell_name: str | None = None,
    ) -> None:
        """Post an ephemeral target select dropdown for the active player."""

        async def on_choice(target_name: str) -> None:
            await self._disable_self_and_edit(interaction)
            await self.dispatch_callback(
                InterpretedAction(
                    action_type=action_type,
                    actor_name=self.actor_name,
                    target_name=target_name,
                    spell_name=spell_name,
                    raw_input=(
                        f"(bouton Sort → {spell_name} sur {target_name})"
                        if spell_name
                        else f"(bouton Attaquer → {target_name})"
                    ),
                ),
            )

        view = TargetSelectView(
            target_names=targets, user_id=self.user_id, on_choice=on_choice,
        )
        await interaction.response.send_message(
            "Choisis ta cible :", view=view, ephemeral=True,
        )

    async def _open_spell_select(self, interaction: discord.Interaction) -> None:
        """Post an ephemeral spell picker; chains into target select on choice."""

        async def on_spell_chosen(spell_name: str) -> None:
            if not self.target_names:
                # No enemies to target — dispatch as a non-targeted spell.
                await self._disable_self_and_edit(interaction)
                await self.dispatch_callback(
                    InterpretedAction(
                        action_type=ActionType.CAST_SPELL,
                        actor_name=self.actor_name,
                        spell_name=spell_name,
                        raw_input=f"(bouton Sort → {spell_name})",
                    ),
                )
                return
            await self._open_target_select(
                interaction,
                targets=self.target_names,
                action_type=ActionType.CAST_SPELL,
                spell_name=spell_name,
            )

        view = SpellSelectView(
            spell_names=self.spell_names,
            user_id=self.user_id,
            on_choice=on_spell_chosen,
        )
        await interaction.response.send_message(
            "Choisis ton sort :", view=view, ephemeral=True,
        )

    async def _open_zone_select(self, interaction: discord.Interaction) -> None:
        """Post an ephemeral zone picker for the Move button."""

        async def on_zone(zone_name: str) -> None:
            await self._disable_self_and_edit(interaction)
            await self.dispatch_callback(
                InterpretedAction(
                    action_type=ActionType.MOVE,
                    actor_name=self.actor_name,
                    target_name=zone_name,
                    raw_input=f"(bouton Se déplacer → {zone_name})",
                ),
            )

        view = ZoneSelectView(
            zone_names=self.adjacent_zone_names,
            user_id=self.user_id,
            on_choice=on_zone,
        )
        await interaction.response.send_message(
            "Vers quelle zone ?", view=view, ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _disable_self_and_edit(self, interaction: discord.Interaction) -> None:
        """Grey out every button on the hub panel while the action resolves."""
        for child in self.children:
            if isinstance(child, ui.Button):
                child.disabled = True
        self.stop()
        try:
            if interaction.message is not None:
                await interaction.message.edit(view=self)
        except discord.HTTPException as exc:
            logger.debug("combat action hub disable failed: %s", exc)
