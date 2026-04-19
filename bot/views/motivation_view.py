"""Motivation selection view — asked right after starter kit selection.

The player picks one of four narrative archetypes explaining WHY their
character is here. The chosen motivation is stored on the launcher and
later fed to the opening reframer so the campaign's call-to-action lands
consistently with player intent (no more mercenaries cast as "chosen ones").
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from bot.i18n import MOTIVATION_KEYS, get_motivation_label
from bot.views.base import LoggedView

OnMotivationSelected = Callable[
    [discord.Interaction, str],
    Coroutine[Any, Any, None],
]


class _MotivationButton(ui.Button["MotivationView"]):
    """A single motivation selection button."""

    def __init__(self, key: str, index: int, display_name: str) -> None:
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"motivation_{index}",
        )
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        assert self.view is not None
        await self.view._on_selected(interaction, self.key)
        self.view.stop()


class MotivationView(LoggedView):
    """Presents the four canonical motivations as buttons.

    The ``on_selected`` callback receives the **English canonical key** (one
    of ``"Contract" | "Personal" | "Curiosity" | "Conviction"``), not the
    localized display label — keys stay stable across languages.
    """

    timeout = 300.0  # 5 minutes

    def __init__(
        self,
        on_selected: OnMotivationSelected,
        language: str = "fr",
    ) -> None:
        super().__init__(timeout=self.timeout)
        self._on_selected = on_selected
        for i, key in enumerate(MOTIVATION_KEYS):
            display_name = get_motivation_label(language, key)
            self.add_item(_MotivationButton(key, i, display_name))
