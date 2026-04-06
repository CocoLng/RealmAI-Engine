"""Onboarding entry view — presents a 'Create Character' button."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.views.base import LoggedView

OnClickCallback = Callable[
    [discord.Interaction],
    Coroutine[Any, Any, None],
]


class StartOnboardingView(LoggedView):
    """Single-button view shown in the campaign channel during onboarding.

    Each player clicks the button to begin their character creation flow.
    """

    timeout = 1200.0  # 20 minutes

    def __init__(self, on_click: OnClickCallback) -> None:
        super().__init__(timeout=self.timeout)
        self._on_click = on_click

    @ui.button(
        label="Créer mon personnage",
        style=discord.ButtonStyle.primary,
        emoji="⚔️",
    )
    async def create_character(
        self,
        interaction: discord.Interaction,
        button: ui.Button["StartOnboardingView"],
    ) -> None:
        """Delegate to the launcher's character creation handler."""
        await self._on_click(interaction)
