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
LeaveCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]
LaunchCallback = Callable[[discord.Interaction, "LobbyView"], Awaitable[None]]


class LobbyView(LoggedView):
    """Campaign lobby with Join / Leave / Launch buttons.

    All three buttons delegate to cog-supplied callbacks so the cog can
    coordinate roster mutation, embed refresh, and (for launch) the
    transition to a GameSession. The view itself never mutates the lobby
    state — it just routes button clicks.
    """

    timeout = None  # persistent

    def __init__(
        self,
        lobby_state: LobbyState,
        host_id: int,
        language: str,
        on_join_clicked: JoinCallback,
        on_launch_clicked: LaunchCallback,
        on_leave_clicked: LeaveCallback | None = None,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.lobby_state = lobby_state
        self.host_id = host_id
        self.language = language
        self._on_join = on_join_clicked
        self._on_launch = on_launch_clicked
        self._on_leave = on_leave_clicked

    @ui.button(label="Rejoindre", emoji="🎭", style=ButtonStyle.primary, custom_id="lobby_join")
    async def join(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        await self._on_join(interaction, self)

    @ui.button(label="Quitter", emoji="🚪", style=ButtonStyle.secondary, custom_id="lobby_leave")
    async def leave(
        self, interaction: discord.Interaction, button: ui.Button[LobbyView],
    ) -> None:
        if self._on_leave is not None:
            await self._on_leave(interaction, self)
            return
        # Fallback for tests that don't provide an on_leave callback: just
        # mutate state and acknowledge. Real cog wiring always supplies one.
        self.lobby_state.remove_player(interaction.user.id)
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
