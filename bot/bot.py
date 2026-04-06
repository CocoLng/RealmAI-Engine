"""RealmAI Discord bot — setup, cog loading, lifecycle."""

from __future__ import annotations

import logging
import os

import discord
from dotenv import load_dotenv
from discord.ext import commands

from sqlalchemy.orm import Session, sessionmaker

from bot.campaign_launcher import CampaignLauncher
from bot.game_session import GameSession
from bot.logging_config import setup_logging
from db.database import get_engine, get_session_factory, init_db

logger = logging.getLogger(__name__)

EXTENSIONS: list[str] = [
    "bot.cogs.rolls",
    "bot.cogs.session",
    "bot.cogs.character",
    "bot.cogs.inventory",
    "bot.cogs.combat",
    "bot.cogs.exploration",
]


class RealmBot(commands.Bot):
    """RealmAI Discord bot — AI-powered RPG Game Master."""

    db_factory: sessionmaker[Session]
    sessions: dict[int, GameSession]  # channel_id → active GameSession
    launchers: dict[int, CampaignLauncher]  # channel_id → onboarding in progress

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        engine = get_engine()
        init_db(engine)
        self.db_factory = get_session_factory(engine)
        self.sessions = {}
        self.launchers = {}

    def get_session(self, channel_id: int | None) -> GameSession | None:
        """Get the active game session for a channel, or None."""
        if channel_id is None:
            return None
        return self.sessions.get(channel_id)

    async def setup_hook(self) -> None:
        """Load cog extensions and sync the command tree."""
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)
        await self.tree.sync()

    async def on_ready(self) -> None:
        """Log bot startup information."""
        guild_names = [g.name for g in self.guilds]
        logger.info(
            "BOT %s connected — %d guild(s): %s",
            self.user, len(self.guilds), ", ".join(guild_names),
        )
        logger.info("BOT %d command(s) synced", len(self.tree.get_commands()))

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: discord.app_commands.Command | discord.app_commands.ContextMenu,
    ) -> None:
        """Log every slash command invocation."""
        guild_name = interaction.guild.name if interaction.guild else "DM"
        channel = getattr(interaction.channel, "name", str(interaction.channel_id))
        logger.info(
            "CMD user=%s command=/%s guild=%s channel=%s",
            interaction.user, command.name, guild_name, channel,
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        """Log slash command errors."""
        guild_name = interaction.guild.name if interaction.guild else "DM"
        logger.error(
            "CMD ERROR user=%s guild=%s: %s",
            interaction.user, guild_name, error,
            exc_info=error,
        )


def run_bot() -> None:
    """Entry point — read token from .env / environment and start the bot."""
    setup_logging()
    logger.info("BOT starting RealmAI Engine")
    load_dotenv()
    token = os.environ["DISCORD_BOT_TOKEN"]
    bot = RealmBot()
    bot.run(token, log_handler=None)
