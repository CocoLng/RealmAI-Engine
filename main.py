"""RealmAI Engine — Discord bot entry point.

Start the bot:
    uv run python main.py

Requires DISCORD_BOT_TOKEN in environment (or .env file).
"""

from bot.bot import run_bot


def main() -> None:
    """Launch the Discord bot."""
    run_bot()


if __name__ == "__main__":
    main()
