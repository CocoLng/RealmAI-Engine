"""Configuration for the MCP Discord Test Server."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def get_config() -> dict[str, str | int]:
    """Load configuration from environment variables."""
    return {
        "tester_bot_token": os.environ.get("TESTER_BOT_TOKEN", ""),
        "game_bot_id": int(os.environ.get("GAME_BOT_ID", "0")),
        "test_channel_id": int(os.environ.get("TEST_CHANNEL_ID", "0")),
    }
