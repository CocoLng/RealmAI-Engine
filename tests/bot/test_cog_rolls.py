"""Tests for the Rolls cog — /roll slash command."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.cogs.rolls import RollsCog


@pytest.fixture
def cog():
    bot = MagicMock()
    return RollsCog(bot)


@pytest.fixture
def interaction():
    inter = AsyncMock()
    inter.response = AsyncMock()
    inter.response.send_message = AsyncMock()
    return inter


class TestRollsCog:
    @pytest.mark.asyncio
    async def test_valid_roll(self, cog, interaction):
        await cog.roll_dice.callback(cog, interaction, "1d20")
        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "1d20" in msg
        assert "\U0001f3b2" in msg

    @pytest.mark.asyncio
    async def test_roll_with_modifier(self, cog, interaction):
        await cog.roll_dice.callback(cog, interaction, "2d6+3")
        msg = interaction.response.send_message.call_args[0][0]
        assert "2d6+3" in msg
        assert "+3" in msg

    @pytest.mark.asyncio
    async def test_invalid_expression(self, cog, interaction):
        await cog.roll_dice.callback(cog, interaction, "invalid")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_valid_roll_is_public(self, cog, interaction):
        await cog.roll_dice.callback(cog, interaction, "1d6")
        call_kwargs = interaction.response.send_message.call_args
        # No ephemeral kwarg or ephemeral=False
        assert call_kwargs[1].get("ephemeral") is not True

    @pytest.mark.asyncio
    async def test_dos_expression_rejected_ephemeral(self, cog, interaction):
        """Audit H20: an unbounded expression must be refused, not rolled."""
        await cog.roll_dice.callback(cog, interaction, "999999999d999999999")
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args
        assert call_kwargs[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_large_roll_display_truncated(self, cog, interaction):
        """Audit H20: 100 dice fit the bounds but the list display is capped."""
        await cog.roll_dice.callback(cog, interaction, "100d6")
        msg = interaction.response.send_message.call_args[0][0]
        assert len(msg) < 2000  # Discord hard limit
        assert "+80" in msg  # 20 rolls shown, 80 summarised
        assert "**100d6**" in msg

    @pytest.mark.asyncio
    async def test_small_roll_not_truncated(self, cog, interaction):
        await cog.roll_dice.callback(cog, interaction, "4d6")
        msg = interaction.response.send_message.call_args[0][0]
        assert "…" not in msg
        msg_rolls = msg.split("[")[1].split("]")[0]
        assert len(msg_rolls.split(",")) == 4
