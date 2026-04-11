"""Zone select dropdown for combat (task 63).

Ephemeral single-option dropdown used by ``CombatActionView`` when the
player clicks **Se déplacer**. The dropdown lists the zones adjacent to
the active combatant's ``current_zone`` (computed by the TurnManager).
The selected zone is forwarded to ``on_choice``.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from bot.views.base import LoggedView

_MAX_OPTIONS = 25
_DEFAULT_TIMEOUT = 60.0


class ZoneSelectView(LoggedView):
    """Dropdown of adjacent zones the active combatant can move to."""

    def __init__(
        self,
        *,
        zone_names: list[str],
        user_id: int,
        on_choice: Callable[[str], Awaitable[None]],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=_DEFAULT_TIMEOUT)
        self.user_id = user_id
        self.on_choice = on_choice
        self.selected_zone: str | None = None

        options: list[discord.SelectOption] = []
        for name in zone_names[:_MAX_OPTIONS]:
            desc = (descriptions or {}).get(name) or "Distance 1"
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=name,
                    description=desc[:100],
                    emoji="📍",
                ),
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucune zone adjacente",
                    value="__none__",
                    emoji="🚫",
                ),
            )

        self.select: ui.Select["ZoneSelectView"] = ui.Select(
            placeholder="Vers quelle zone ?",
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
                content="Aucun déplacement possible.", view=None,
            )
            self.stop()
            return
        self.selected_zone = value
        await interaction.response.edit_message(
            content=f"✔ Destination : **{value}**", view=None,
        )
        self.stop()
        await self.on_choice(value)
