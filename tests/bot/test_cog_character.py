"""Tests for the Character cog — /character, /level_up.

Character creation is no longer a slash command; it happens via the lobby
flow in /start_campaign. See tests/scenarios/test_character_creation_lobby.py
for end-to-end coverage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.cogs.character import CharacterCog
from bot.game_session import GameSession
from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    XP_THRESHOLDS,
    add_xp,
    create_character,
)
from engine.inventory import create_inventory
from world.campaign import Campaign


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_SCORES = AbilityScores(STR=14, DEX=12, CON=13, INT=10, WIS=15, CHA=8)


def _make_character(name: str = "Thorin", **kwargs: object) -> Character:
    """Shortcut to create a test character with sensible defaults."""
    defaults: dict[str, object] = {
        "race": Race.DWARF,
        "char_class": CharacterClass.FIGHTER,
        "ability_scores": _DEFAULT_SCORES,
    }
    defaults.update(kwargs)
    return create_character(name=name, **defaults)  # type: ignore[arg-type]


def _make_session() -> GameSession:
    """Create a minimal GameSession for testing."""
    campaign = Campaign(name="Test Campaign")
    return GameSession(campaign=campaign)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bot() -> MagicMock:
    """A mocked RealmBot."""
    b = MagicMock()
    b.sessions = {}
    b.get_session = MagicMock(return_value=None)
    # db_factory returns a mock DB session
    db_session = MagicMock()
    b.db_factory = MagicMock(return_value=db_session)
    return b


@pytest.fixture()
def cog(bot: MagicMock) -> CharacterCog:
    return CharacterCog(bot)


@pytest.fixture()
def interaction() -> AsyncMock:
    """A mocked discord.Interaction with response and followup."""
    inter = AsyncMock(spec=discord.Interaction)
    inter.channel_id = 12345
    inter.user = MagicMock()
    inter.user.id = 99999

    inter.response = AsyncMock()
    inter.response.send_message = AsyncMock()

    inter.followup = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


# ===========================================================================
# /character
# ===========================================================================


class TestViewCharacter:
    """Tests for the /character command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None

        await cog.character.callback(cog, interaction, public=False)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.character.callback(cog, interaction, public=False)

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_view_character_ephemeral(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        """Default (public=False) should send the embed ephemerally."""
        session = _make_session()
        char = _make_character("Legolas", race=Race.ELF, char_class=CharacterClass.RANGER)
        session.characters[99999] = char
        cog.bot.get_session.return_value = session

        await cog.character.callback(cog, interaction, public=False)

        call_kwargs = interaction.response.send_message.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)
        assert call_kwargs["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_view_character_public(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        """public=True should send the embed non-ephemerally."""
        session = _make_session()
        session.characters[99999] = _make_character()
        cog.bot.get_session.return_value = session

        await cog.character.callback(cog, interaction, public=True)

        call_kwargs = interaction.response.send_message.call_args[1]
        assert call_kwargs["ephemeral"] is False


# ===========================================================================
# /level_up
# ===========================================================================


class TestLevelUp:
    """Tests for the /level_up command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None

        await cog.level_up_cmd.callback(cog, interaction, public=False)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_no_character(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        await cog.level_up_cmd.callback(cog, interaction, public=False)

        msg = interaction.response.send_message.call_args
        assert "pas de personnage" in str(msg)

    @pytest.mark.asyncio()
    async def test_not_enough_xp(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        """Should reject when character doesn't have enough XP."""
        session = _make_session()
        char = _make_character()
        assert char.level == 1
        assert char.xp == 0  # not enough for level 2
        session.characters[99999] = char
        cog.bot.get_session.return_value = session

        await cog.level_up_cmd.callback(cog, interaction, public=False)

        msg = interaction.response.send_message.call_args
        assert "Pas assez d'XP" in str(msg)
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_successful_level_up(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        """Should level up when the character has enough XP."""
        session = _make_session()
        char = _make_character()
        # Give enough XP for level 2 (need 300)
        add_xp(char, XP_THRESHOLDS[2])
        session.characters[99999] = char
        session.inventories[99999] = create_inventory()
        session.spellcasters[99999] = None
        cog.bot.get_session.return_value = session

        with patch("bot.cogs.character.PlayerCharacterRepository") as MockRepo:
            await cog.level_up_cmd.callback(cog, interaction, public=False)

            # Character leveled up
            assert char.level == 2

            # DB update called
            MockRepo.return_value.update.assert_called_once()

            # Embed sent with level info
            call_kwargs = interaction.response.send_message.call_args[1]
            assert "niveau 2" in call_kwargs["content"]
            assert isinstance(call_kwargs["embed"], discord.Embed)
            assert call_kwargs["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_level_up_public(
        self, cog: CharacterCog, interaction: AsyncMock,
    ) -> None:
        """public=True should make the level-up announcement visible."""
        session = _make_session()
        char = _make_character()
        add_xp(char, XP_THRESHOLDS[2])
        session.characters[99999] = char
        session.inventories[99999] = create_inventory()
        session.spellcasters[99999] = None
        cog.bot.get_session.return_value = session

        with patch("bot.cogs.character.PlayerCharacterRepository"):
            await cog.level_up_cmd.callback(cog, interaction, public=True)

            call_kwargs = interaction.response.send_message.call_args[1]
            assert call_kwargs["ephemeral"] is False
