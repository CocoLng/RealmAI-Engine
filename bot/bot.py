"""RealmAI Discord bot — setup, cog loading, lifecycle."""

import logging
import os

import discord
from discord.ext import commands

from db.database import get_engine, get_session_factory, init_db

logger = logging.getLogger(__name__)

EXTENSIONS: list[str] = [
    # Phase 3c will populate this list:
    # "bot.cogs.session",
    # "bot.cogs.character",
    # "bot.cogs.rolls",
]


class RealmBot(commands.Bot):
    """RealmAI Discord bot — AI-powered RPG Game Master."""

    db_factory: object  # sessionmaker[Session], typed loosely to avoid import in type position

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        engine = get_engine()
        init_db(engine)
        self.db_factory = get_session_factory(engine)

    async def setup_hook(self) -> None:
        """Load cog extensions and sync the command tree."""
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)
        await self.tree.sync()

    async def on_ready(self) -> None:
        """Log bot startup information."""
        logger.info("%s connected (%d guilds)", self.user, len(self.guilds))


def run_bot() -> None:
    """Entry point — read token from environment and start the bot."""
    token = os.environ["DISCORD_BOT_TOKEN"]
    bot = RealmBot()
    bot.run(token, log_handler=None)
