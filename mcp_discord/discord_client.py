"""TesterBot — lightweight Discord client for sending test commands."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

logger = logging.getLogger(__name__)


class TesterBot(discord.Client):
    """Lightweight bot that acts as test proxy on Discord.

    Sends !test messages to the game bot channel and reads responses.
    """

    def __init__(
        self,
        game_bot_id: int,
        test_channel_id: int,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.game_bot_id = game_bot_id
        self.test_channel_id = test_channel_id
        self._ready = asyncio.Event()

    async def on_ready(self) -> None:
        """Signal that the bot is connected."""
        logger.info("TesterBot connected as %s", self.user)
        self._ready.set()

    async def wait_until_ready_custom(self) -> None:
        """Wait until the bot is connected and ready."""
        await self._ready.wait()

    def _get_channel(self) -> discord.TextChannel:
        """Get the test channel."""
        channel = self.get_channel(self.test_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            msg = f"Test channel {self.test_channel_id} not found"
            raise RuntimeError(msg)
        return channel

    async def send_test_command(self, command: str, timeout: float = 15.0) -> dict[str, Any]:
        """Send !test command and wait for game bot response.

        Args:
            command: The command string (e.g. "look" or "attack target=Gobelin")
            timeout: Max seconds to wait for response

        Returns:
            Serialized message from the game bot
        """
        channel = self._get_channel()
        await channel.send(f"!test {command}")

        try:
            response = await self.wait_for(
                "message",
                check=lambda m: (
                    m.author.id == self.game_bot_id
                    and m.channel.id == self.test_channel_id
                ),
                timeout=timeout,
            )
            return self._serialize_message(response)
        except asyncio.TimeoutError:
            return {"error": "Timeout waiting for game bot response"}

    async def read_recent_messages(self, limit: int = 10) -> list[dict[str, Any]]:
        """Read recent messages from the test channel."""
        channel = self._get_channel()
        messages: list[dict[str, Any]] = []
        async for msg in channel.history(limit=min(limit, 50)):
            messages.append(self._serialize_message(msg))
        return messages

    def _serialize_message(self, msg: discord.Message) -> dict[str, Any]:
        """Convert a Discord message to a serializable dict."""
        return {
            "id": msg.id,
            "author": msg.author.name,
            "author_id": msg.author.id,
            "content": msg.content,
            "embeds": [self._serialize_embed(e) for e in msg.embeds],
            "components": self._serialize_components(msg.components),
            "timestamp": msg.created_at.isoformat(),
        }

    @staticmethod
    def _serialize_embed(embed: discord.Embed) -> dict[str, Any]:
        """Convert an embed to a serializable dict."""
        result: dict[str, Any] = {}
        if embed.title:
            result["title"] = embed.title
        if embed.description:
            result["description"] = embed.description
        if embed.color:
            result["color"] = str(embed.color)
        if embed.fields:
            result["fields"] = [
                {"name": f.name, "value": f.value, "inline": f.inline}
                for f in embed.fields
            ]
        if embed.footer and embed.footer.text:
            result["footer"] = embed.footer.text
        return result

    @staticmethod
    def _serialize_components(components: list[discord.ActionRow]) -> list[dict[str, Any]]:
        """Convert message components to a serializable list."""
        result: list[dict[str, Any]] = []
        for row in components:
            for child in row.children:
                if isinstance(child, discord.Button):
                    result.append({
                        "type": "button",
                        "label": child.label or "",
                        "custom_id": child.custom_id or "",
                        "disabled": child.disabled,
                    })
                elif isinstance(child, discord.SelectMenu):
                    result.append({
                        "type": "select",
                        "custom_id": child.custom_id or "",
                        "options": [
                            {"label": opt.label, "value": opt.value}
                            for opt in (child.options or [])
                        ],
                    })
        return result

    def is_game_bot_online(self) -> bool:
        """Check if the game bot appears online in the test channel's guild."""
        channel = self.get_channel(self.test_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return False
        guild = channel.guild
        member = guild.get_member(self.game_bot_id)
        return member is not None and member.status != discord.Status.offline
