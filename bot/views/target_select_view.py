"""Target select dropdown for combat (task 63).

An ephemeral single-option dropdown used by ``CombatActionView`` when the
player clicks **Attaquer** or picks a spell that needs a target. The view
does not know about the engine — it just forwards the chosen target name
to the ``on_choice`` coroutine supplied by the caller.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import discord
from discord import ui

from bot.views.base import LoggedView


_MAX_OPTIONS = 25  # Discord hard limit on Select options
_DEFAULT_TIMEOUT = 60.0


class TargetSelectView(LoggedView):
    """Dropdown of combat targets (enemy combatants)."""

    def __init__(
        self,
        *,
        target_names: list[str],
        user_id: int,
        on_choice: Callable[[str], Awaitable[None]],
        descriptions: dict[str, str] | None = None,
    ) -> None:
        super().__init__(timeout=_DEFAULT_TIMEOUT)
        self.user_id = user_id
        self.on_choice = on_choice
        self.selected_target: str | None = None

        options: list[discord.SelectOption] = []
        for name in target_names[:_MAX_OPTIONS]:
            desc = (descriptions or {}).get(name)
            options.append(
                discord.SelectOption(
                    label=name[:100],
                    value=name,
                    description=(desc[:100] if desc else None),
                    emoji="👹",
                ),
            )

        # Defensive: if the caller passed an empty list the Select would
        # reject it at render time — surface a placeholder option instead.
        if not options:
            options.append(
                discord.SelectOption(
                    label="Aucune cible", value="__none__", emoji="🚫",
                ),
            )

        self.select: ui.Select["TargetSelectView"] = ui.Select(
            placeholder="Choisis ta cible",
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
                content="Aucune cible valide.", view=None,
            )
            self.stop()
            return
        self.selected_target = value
        await interaction.response.edit_message(
            content=f"✔ Cible : **{value}**", view=None,
        )
        self.stop()
        await self.on_choice(value)
