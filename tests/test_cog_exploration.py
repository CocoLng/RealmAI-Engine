"""Tests for the Exploration cog — /look, /search, /talk, /move."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.cogs.exploration import ExplorationCog
from bot.game_session import GameSession
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.inventory import create_inventory
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCORES = AbilityScores(STR=14, DEX=12, CON=13, INT=10, WIS=15, CHA=8)


def _make_session(with_location: bool = True) -> GameSession:
    campaign = Campaign(id="camp-1", name="Test", player_names=["Alice"])
    session = GameSession(campaign=campaign)
    char = create_character("Thorin", Race.DWARF, CharacterClass.FIGHTER, _SCORES)
    session.characters[100] = char
    session.inventories[100] = create_inventory()
    session.spellcasters[100] = None
    if with_location:
        session.current_location = Location(
            name="Tavern",
            description="A cozy tavern with a crackling fire.",
            connections=["Forest", "Market"],
            npcs_present=["Barkeep"],
            items_available=["Healing Potion"],
        )
    return session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bot() -> MagicMock:
    b = MagicMock()
    b.sessions = {}
    b.get_session = MagicMock(return_value=None)
    db_session_mock = MagicMock()
    b.db_factory = MagicMock(return_value=db_session_mock)
    return b


@pytest.fixture()
def cog(bot: MagicMock) -> ExplorationCog:
    return ExplorationCog(bot)


@pytest.fixture()
def interaction() -> AsyncMock:
    inter = AsyncMock(spec=discord.Interaction)
    inter.channel_id = 12345
    inter.user = MagicMock()
    inter.user.id = 100
    inter.response = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.followup = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


# ===========================================================================
# /look
# ===========================================================================


class TestLook:
    """Tests for /look command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None
        await cog.look.callback(cog, interaction)
        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]
        assert msg[1]["ephemeral"] is True

    @pytest.mark.asyncio()
    async def test_in_combat(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        session.combat_state = MagicMock()  # non-None = in combat
        cog.bot.get_session.return_value = session
        await cog.look.callback(cog, interaction)
        msg = interaction.response.send_message.call_args
        assert "combat" in msg[0][0].lower()

    @pytest.mark.asyncio()
    async def test_no_location(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session(with_location=False)
        cog.bot.get_session.return_value = session
        await cog.look.callback(cog, interaction)
        msg = interaction.response.send_message.call_args
        assert "Aucun lieu" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_shows_location_embed(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session
        await cog.look.callback(cog, interaction)
        call_kwargs = interaction.response.send_message.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)


# ===========================================================================
# /search
# ===========================================================================


class TestSearch:
    """Tests for /search command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None
        await cog.search.callback(cog, interaction, target="potion")
        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_found_item(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session
        await cog.search.callback(cog, interaction, target="Healing Potion")
        call_kwargs = interaction.response.send_message.call_args[1]
        embed = call_kwargs["embed"]
        assert isinstance(embed, discord.Embed)

    @pytest.mark.asyncio()
    async def test_not_found(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session
        await cog.search.callback(cog, interaction, target="Dragon Egg")
        call_kwargs = interaction.response.send_message.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)


# ===========================================================================
# /talk
# ===========================================================================


class TestTalk:
    """Tests for /talk command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None
        await cog.talk.callback(cog, interaction, npc="Barkeep")
        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_npc_not_present(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session
        await cog.talk.callback(cog, interaction, npc="Dragon")
        msg = interaction.response.send_message.call_args
        assert "Aucun PNJ" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_talk_to_present_npc(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        with MagicMock() as mock_db:
            mock_db.__enter__ = MagicMock(return_value=mock_db)
            mock_db.__exit__ = MagicMock(return_value=False)
            cog.bot.db_factory.return_value = mock_db

            from unittest.mock import patch
            with patch("bot.cogs.exploration.NPCRepository") as mock_repo_cls:
                mock_repo_cls.return_value.get_by_name.return_value = None
                await cog.talk.callback(cog, interaction, npc="Barkeep")

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert isinstance(call_kwargs["embed"], discord.Embed)


# ===========================================================================
# /move
# ===========================================================================


class TestMove:
    """Tests for /move command."""

    @pytest.mark.asyncio()
    async def test_no_session(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.get_session.return_value = None
        await cog.move.callback(cog, interaction, direction="Forest")
        msg = interaction.response.send_message.call_args
        assert "Aucune session active" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_in_combat(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        session.combat_state = MagicMock()
        cog.bot.get_session.return_value = session
        await cog.move.callback(cog, interaction, direction="Forest")
        msg = interaction.response.send_message.call_args
        assert "combat" in msg[0][0].lower()

    @pytest.mark.asyncio()
    async def test_invalid_direction(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session
        await cog.move.callback(cog, interaction, direction="Volcano")
        msg = interaction.response.send_message.call_args
        assert "Pas de chemin" in msg[0][0]
        assert "Forest" in msg[0][0]

    @pytest.mark.asyncio()
    async def test_move_to_valid_direction(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        from unittest.mock import patch
        with patch("bot.cogs.exploration.LocationRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_by_name.return_value = None
            await cog.move.callback(cog, interaction, direction="Forest")

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()

        # Session should be updated (fallback location created)
        assert session.current_location is not None
        assert session.current_location.name == "Forest"
        assert session.campaign.current_location == "Forest"

    @pytest.mark.asyncio()
    async def test_move_case_insensitive(
        self, cog: ExplorationCog, interaction: AsyncMock,
    ) -> None:
        session = _make_session()
        cog.bot.get_session.return_value = session

        from unittest.mock import patch
        with patch("bot.cogs.exploration.LocationRepository") as mock_repo_cls:
            mock_repo_cls.return_value.get_by_name.return_value = None
            await cog.move.callback(cog, interaction, direction="forest")

        assert session.current_location is not None
        assert session.current_location.name == "Forest"
