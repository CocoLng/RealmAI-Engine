"""MCP Discord Test Server — exposes Discord interaction tools to Claude Code.

Run with: uv run python -m mcp_discord.server
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_discord.config import get_config
from mcp_discord.discord_client import TesterBot

logger = logging.getLogger(__name__)

mcp = FastMCP("discord-test", instructions=(
    "Tools for interacting with the RealmAI Discord bot on a test server. "
    "Use discord_status to check if the bot is online, then send commands "
    "and read responses to test gameplay scenarios."
))

# Global TesterBot instance (initialized on first tool call)
_bot: TesterBot | None = None
_bot_task: asyncio.Task[None] | None = None


async def _get_bot() -> TesterBot:
    """Get or initialize the TesterBot singleton."""
    global _bot, _bot_task

    if _bot is not None and _bot.is_ready():
        return _bot

    config = get_config()
    token = str(config["tester_bot_token"])
    if not token:
        msg = "TESTER_BOT_TOKEN not set in environment"
        raise RuntimeError(msg)

    _bot = TesterBot(
        game_bot_id=int(config["game_bot_id"]),
        test_channel_id=int(config["test_channel_id"]),
    )

    # Start the bot in a background task
    _bot_task = asyncio.create_task(_bot.start(token))

    # Wait for it to be ready
    try:
        await asyncio.wait_for(_bot.wait_until_ready_custom(), timeout=30.0)
    except asyncio.TimeoutError:
        msg = "TesterBot failed to connect within 30 seconds"
        raise RuntimeError(msg)

    return _bot


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def discord_status() -> dict[str, Any]:
    """Check if the game bot is online and responsive.

    Returns:
        online: Whether the game bot appears online
        tester_connected: Whether the tester bot is connected
        test_channel_id: The configured test channel
    """
    try:
        bot = await _get_bot()
        return {
            "online": bot.is_game_bot_online(),
            "tester_connected": bot.is_ready(),
            "test_channel_id": bot.test_channel_id,
        }
    except Exception as e:
        return {
            "online": False,
            "tester_connected": False,
            "error": str(e),
        }


@mcp.tool()
async def discord_send_command(
    command: str,
    args: dict[str, str] | None = None,
    player: int = 1,
) -> dict[str, Any]:
    """Send a game command to the bot via TestBridge.

    Args:
        command: The command name (e.g. "attack", "look", "start_campaign")
        args: Command arguments as key-value pairs (e.g. {"target": "Gobelin"})
        player: Virtual player index (1-based, default 1)

    Returns:
        success: Whether a response was received
        response: The game bot's response (embeds, components, content)
    """
    bot = await _get_bot()

    # Build !test command string
    parts = [f"player={player}", command]
    if args:
        parts.extend(f"{k}={v}" for k, v in args.items())
    cmd_str = " ".join(parts)

    result = await bot.send_test_command(cmd_str)

    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "response": result}


@mcp.tool()
async def discord_read_messages(limit: int = 10) -> list[dict[str, Any]]:
    """Read recent messages from the test channel.

    Args:
        limit: Number of messages to read (max 50)

    Returns:
        List of messages with embeds, components, timestamps
    """
    bot = await _get_bot()
    return await bot.read_recent_messages(limit=min(limit, 50))


@mcp.tool()
async def discord_click_button(
    message_id: int,
    button_label: str,
    player: int = 1,
) -> dict[str, Any]:
    """Click a button on a game bot message via TestBridge.

    Sends a !test click_button command. The TestBridge finds the active view
    and triggers the matching button callback.

    Args:
        message_id: Discord message ID containing the button
        button_label: Label text of the button to click
        player: Virtual player index
    """
    bot = await _get_bot()
    cmd = f"player={player} click_button msg={message_id} button={button_label}"
    result = await bot.send_test_command(cmd)
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "response": result}


@mcp.tool()
async def discord_select_option(
    message_id: int,
    value: str,
    player: int = 1,
) -> dict[str, Any]:
    """Select an option from a dropdown on a game bot message.

    Args:
        message_id: Discord message ID containing the select
        value: The value to select
        player: Virtual player index
    """
    bot = await _get_bot()
    cmd = f"player={player} select_option msg={message_id} value={value}"
    result = await bot.send_test_command(cmd)
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "response": result}


@mcp.tool()
async def discord_submit_modal(
    fields: dict[str, str],
    player: int = 1,
) -> dict[str, Any]:
    """Submit a modal currently pending for this virtual player.

    Drives the Discord Modal that the game bot opened via
    ``interaction.response.send_modal``. The TestBridge pops the pending modal
    for the given player, populates each TextInput whose label matches a key
    in ``fields``, and invokes ``modal.on_submit``.

    Args:
        fields: Map of TextInput label -> value. Labels are case-sensitive.
        player: Virtual player index (1-based, default 1)

    Returns:
        success: Whether a response was received
        response: The game bot's follow-up message
    """
    bot = await _get_bot()
    parts = [f"player={player}", "submit_modal"]
    for key, value in fields.items():
        safe_key = key.replace(" ", "~")
        safe_value = value.replace(" ", "~")
        parts.append(f"field_{safe_key}={safe_value}")
    cmd = " ".join(parts)
    result = await bot.send_test_command(cmd)
    if "error" in result:
        return {"success": False, "error": result["error"]}
    return {"success": True, "response": result}


@mcp.tool()
async def discord_wait_for_response(timeout: float = 10.0) -> dict[str, Any]:
    """Wait for the next message from the game bot.

    Args:
        timeout: Max seconds to wait

    Returns:
        The game bot's message (embeds, components, content), or error on timeout
    """
    bot = await _get_bot()
    try:
        response = await bot.wait_for(
            "message",
            check=lambda m: (
                m.author.id == bot.game_bot_id
                and m.channel.id == bot.test_channel_id
            ),
            timeout=timeout,
        )
        return bot._serialize_message(response)
    except asyncio.TimeoutError:
        return {"error": f"No response from game bot within {timeout}s"}


@mcp.tool()
async def discord_get_game_state() -> dict[str, Any]:
    """Get current game state by sending !test game_state.

    The TestBridge serializes the active GameSession and returns it as JSON.

    Returns:
        campaign, characters, combat_active, combat_state, location, npcs, quests
    """
    bot = await _get_bot()
    result = await bot.send_test_command("game_state")
    if "error" in result:
        return {"error": result["error"]}

    # The game_state response is a JSON code block in the message content
    content = result.get("content", "")
    if content.startswith("```json\n") and content.endswith("\n```"):
        import json

        try:
            return json.loads(content[8:-4])
        except json.JSONDecodeError:
            return {"raw_content": content}
    return {"raw_content": content}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
