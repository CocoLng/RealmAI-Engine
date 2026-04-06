"""Starter gear selection view — kit buttons for new characters."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

import discord
from discord import ui

from engine.starter_gear import StarterKit

OnGearSelected = Callable[
    [discord.Interaction, StarterKit],
    Coroutine[Any, Any, None],
]


class _KitButton(ui.Button["StarterGearView"]):
    """A single kit selection button."""

    def __init__(self, kit: StarterKit, index: int) -> None:
        super().__init__(
            label=kit.name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"kit_{index}",
        )
        self.kit = kit

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        """Invoke the parent view's on_selected callback."""
        assert self.view is not None
        await self.view._on_selected(interaction, self.kit)
        self.view.stop()


class StarterGearView(ui.View):
    """Presents 2-3 starter kits as buttons for a character class.

    The on_selected callback is invoked with the chosen kit.
    """

    timeout = 300.0  # 5 minutes

    def __init__(
        self,
        kits: list[StarterKit],
        on_selected: OnGearSelected,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self._kits = kits
        self._on_selected = on_selected
        for i, kit in enumerate(kits):
            self.add_item(_KitButton(kit, i))
