"""Spell select dropdown for combat (task 63).

Ephemeral single-option dropdown used by ``CombatActionView`` when the
player clicks **Sort**. The selected spell name is forwarded to
``on_choice``; target selection (if needed) is handled by the hub view,
not here. Descriptions for each option can be supplied by the caller
(typically the TurnManager) to surface slot level, damage, etc.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from bot.views.base import LoggedView

_MAX_OPTIONS = 25
_DEFAULT_TIMEOUT = 60.0


class SpellSelectView(LoggedView):
    """Dropdown of castable spells for the active combatant."""

    def __init__(
        self,
        *,
        spell_names: list[str],
        user_id: int,
        on_choice: Callable[[str], Awaitable[None]],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=_DEFAULT_TIMEOUT)
        self.user_id = user_id
        self.on_choice = on_choice
        self.selected_spell: str | None = None

        options: list[discord.SelectOption] = []
        for name in spell_names[:_MAX_OPTIONS]:
            desc = (descriptions or {}).get(name)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=name,
                    description=(desc[:100] if desc else None),
                    emoji="✨",
                ),
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucun sort disponible", value="__none__", emoji="🚫",
                ),
            )

        self.select: ui.Select["SpellSelectView"] = ui.Select(
            placeholder="Choisis ton sort",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.select.callback = self._on_selected  # type: ignore[method-assign]
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour.", ephemeral=True,
            )
            return False
        return True

    async def _on_selected(self, interaction: discord.Interaction) -> None:
        value = self.select.values[0]
        if value == "__none__":
            await interaction.response.edit_message(
                content="Aucun sort à lancer.", view=None,
            )
            self.stop()
            return
        self.selected_spell = value
        await interaction.response.edit_message(
            content=f"✔ Sort : **{value}**", view=None,
        )
        self.stop()
        await self.on_choice(value)
