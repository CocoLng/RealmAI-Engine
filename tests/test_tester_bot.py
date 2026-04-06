"""Tests for TesterBot message serialization."""

from __future__ import annotations

from unittest.mock import MagicMock
from datetime import datetime, timezone

from mcp_discord.discord_client import TesterBot


def _make_message(
    content: str = "",
    embeds: list | None = None,
    components: list | None = None,
) -> MagicMock:
    """Create a mock Discord message."""
    msg = MagicMock()
    msg.id = 123456789
    msg.author = MagicMock()
    msg.author.name = "GameBot"
    msg.author.id = 111
    msg.content = content
    msg.embeds = embeds or []
    msg.components = components or []
    msg.created_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
    return msg


def _make_embed(
    title: str = "Test",
    description: str = "Desc",
    fields: list[tuple[str, str]] | None = None,
) -> MagicMock:
    """Create a mock Discord embed."""
    embed = MagicMock()
    embed.title = title
    embed.description = description
    embed.color = MagicMock()
    embed.color.__str__ = lambda _: "#ff0000"
    embed.footer = MagicMock()
    embed.footer.text = "footer text"
    embed.fields = []
    if fields:
        for name, value in fields:
            f = MagicMock()
            f.name = name
            f.value = value
            f.inline = False
            embed.fields.append(f)
    return embed


def test_serialize_message_basic() -> None:
    """Serialize a basic text message."""
    bot = TesterBot(game_bot_id=111, test_channel_id=222)
    msg = _make_message(content="Hello world")

    result = bot._serialize_message(msg)

    assert result["id"] == 123456789
    assert result["author"] == "GameBot"
    assert result["content"] == "Hello world"
    assert result["embeds"] == []
    assert result["components"] == []


def test_serialize_message_with_embed() -> None:
    """Serialize a message with an embed."""
    bot = TesterBot(game_bot_id=111, test_channel_id=222)
    embed = _make_embed(
        title="Combat !",
        description="Le combat commence",
        fields=[("HP", "10/10"), ("AC", "15")],
    )
    msg = _make_message(embeds=[embed])

    result = bot._serialize_message(msg)

    assert len(result["embeds"]) == 1
    e = result["embeds"][0]
    assert e["title"] == "Combat !"
    assert e["description"] == "Le combat commence"
    assert len(e["fields"]) == 2
    assert e["fields"][0]["name"] == "HP"
    assert e["fields"][0]["value"] == "10/10"


def test_serialize_embed_without_optional_fields() -> None:
    """Serialize an embed with minimal fields."""
    embed = MagicMock()
    embed.title = None
    embed.description = "Just a description"
    embed.color = None
    embed.footer = MagicMock()
    embed.footer.text = None
    embed.fields = []

    result = TesterBot._serialize_embed(embed)

    assert "title" not in result
    assert result["description"] == "Just a description"
    assert "color" not in result
    assert "footer" not in result


def test_is_game_bot_online_when_offline() -> None:
    """Returns False when game bot is not in guild."""
    bot = TesterBot(game_bot_id=111, test_channel_id=222)
    # Bot not connected — get_channel returns None
    assert bot.is_game_bot_online() is False
