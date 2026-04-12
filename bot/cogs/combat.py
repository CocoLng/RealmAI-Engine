"""Combat cog — thin shell that owns the TurnManager factory.

The heavy lifting moved to :mod:`bot.combat_turn_manager`. This cog
exists only so ``bot/bot.py`` can load it via the standard discord.py
extension mechanism and expose a single factory method the
:class:`~bot.cogs.action_handler.ActionHandlerCog` calls when a freshly
bootstrapped combat needs a turn manager attached to the session.

Nothing from the legacy pre-refactor combat loop survives here — the old
``start_combat_encounter`` / ``_prompt_turn`` / ``_handle_attack`` flow
was incompatible with the multi-enemy combat engine (zones, NPC tier AI,
boss legendary actions, phase transitions).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from bot.action_pipeline import ActionPipeline
from bot.combat_turn_manager import TurnManager

if TYPE_CHECKING:
    from bot.bot import RealmBot
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


class CombatCog(commands.Cog):
    """Expose a factory for building :class:`TurnManager` instances."""

    def __init__(self, bot: "RealmBot") -> None:
        self.bot = bot

    def build_turn_manager(
        self,
        channel: discord.abc.Messageable,
        session: "GameSession",
    ) -> TurnManager:
        """Create a TurnManager bound to a combat encounter.

        Called by ``ActionHandlerCog`` right after the pipeline bootstraps
        a new ``CombatState``. The returned manager is stored on
        ``session.combat_turn_manager`` and drives the turn lifecycle
        until the encounter ends.
        """
        return TurnManager(
            channel=channel,
            session=session,
            pipeline_factory=ActionPipeline,
            db_factory=getattr(self.bot, "db_factory", None),
        )


async def setup(bot: commands.Bot) -> None:
    """discord.py extension entry point — registers the shell cog."""
    await bot.add_cog(CombatCog(bot))  # type: ignore[arg-type]
