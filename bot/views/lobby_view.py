"""Campaign lobby view — Rejoindre / Quitter / Démarrer buttons.

Persistent view attached to the lobby message in the campaign channel.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord
from discord import ButtonStyle, ui

from bot.lobby_state import LobbyState
from bot.views.base import LoggedView

JoinCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]
LaunchCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]


class LobbyView(LoggedView):
    """Campaign lobby with Join / Leave / Launch buttons.

    The view does NOT mutate state directly for join — it delegates to
    ``on_join_clicked`` so the cog can open the CharacterSetupFlow as an
    ephemeral followup. Leave is handled inline (removes from state +
    refreshes the lobby message).
    """

    timeout = None  # persistent

    def __init__(
        self,
        lobby_state: LobbyState,
        host_id: int,
        language: str,
        on_join_clicked: JoinCallback,
        on_launch_clicked: LaunchCallback,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.lobby_state = lobby_state
        self.host_id = host_id
        self.language = language
        self._on_join = on_join_clicked
        self._on_launch = on_launch_clicked

    @ui.button(label="Rejoindre", emoji="🎭", style=ButtonStyle.primary, custom_id="lobby_join")
    async def join(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        await self._on_join(interaction, self)

    @ui.button(label="Quitter", emoji="🚪", style=ButtonStyle.secondary, custom_id="lobby_leave")
    async def leave(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        self.lobby_state.remove_player(interaction.user.id)
        # The cog (in Wave C) will refresh the lobby embed after this. For now,
        # acknowledge the interaction.
        if not interaction.response.is_done():
            await interaction.response.edit_message(view=self)

    @ui.button(label="Démarrer l'aventure", emoji="▶️", style=ButtonStyle.success, custom_id="lobby_launch")
    async def launch(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        if interaction.user.id != self.host_id:
            await interaction.response.send_message(
                "Seul le host peut démarrer la campagne.", ephemeral=True,
            )
            return
        if not self.lobby_state.has_any_ready():
            await interaction.response.send_message(
                "Il faut au moins un joueur prêt pour démarrer.", ephemeral=True,
            )
            return
        await self._on_launch(interaction, self)
