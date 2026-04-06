"""Base view with error logging for all Discord UI views."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import ui

logger = logging.getLogger(__name__)


class LoggedView(ui.View):
    """Base view that logs interaction errors to our logger instead of stderr."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: ui.Item[Any],
    ) -> None:
        """Log view interaction errors to the application logger."""
        logger.error(
            "VIEW ERROR view=%s item=%s user=%s: %s",
            type(self).__name__,
            type(item).__name__,
            interaction.user,
            error,
            exc_info=error,
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "Une erreur est survenue.", ephemeral=True,
            )
