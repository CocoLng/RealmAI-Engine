"""Force-launch view -- lets the campaign creator start without all players."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

import discord

from bot.views.base import LoggedView

logger = logging.getLogger(__name__)


class ForceLaunchView(LoggedView):
    """Single-button view for the campaign creator to force-launch."""

    def __init__(
        self,
        *,
        creator_id: int,
        on_click: Callable[[discord.Interaction], Coroutine[Any, Any, None]],
        timeout: float = 600,
    ) -> None:
        super().__init__(timeout=timeout)
        self.creator_id = creator_id
        self._on_click = on_click

    @discord.ui.button(
        label="Lancer la partie",
        style=discord.ButtonStyle.danger,
        emoji="\u26a1",
    )
    async def launch_button(
        self, interaction: discord.Interaction, button: discord.ui.Button[ForceLaunchView],
    ) -> None:
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message(
                "Seul le createur de la campagne peut lancer la partie.",
                ephemeral=True,
            )
            return
        self.stop()
        await self._on_click(interaction)
