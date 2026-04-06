"""Tests for the Session cog -- campaign lifecycle commands."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.orm import Session

from bot.cogs.session import SessionCog
from bot.config import GuildConfig
from bot.game_session import GameSession
from db.repositories import (
    CampaignChannelRepository,
    CampaignRepository,
    GuildConfigRepository,
    LocationRepository,
    PlayerCharacterRepository,
)
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.inventory import create_inventory
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GUILD_ID = 111222333
CHANNEL_ID = 444555666
USER_ID = 777888999
PLAYER_ID = 123456789


@pytest.fixture()
def bot(db_session: Session) -> MagicMock:
    """Mock RealmBot that returns a real DB session."""
    mock_bot = MagicMock()
    mock_bot.sessions = {}
    mock_bot.get_session = lambda cid: mock_bot.sessions.get(cid)
    mock_bot.db_factory = MagicMock(return_value=db_session)
    return mock_bot


@pytest.fixture()
def cog(bot: MagicMock) -> SessionCog:
    """SessionCog wired to the mocked bot."""
    return SessionCog(bot)


@pytest.fixture()
def guild() -> MagicMock:
    """Mock Discord guild with essential attributes."""
    g = MagicMock()
    g.id = GUILD_ID
    g.me = MagicMock()  # bot's own Member
    g.categories = []
    member1 = MagicMock()
    member1.id = USER_ID
    member2 = MagicMock()
    member2.id = PLAYER_ID
    g.get_member = lambda uid: {USER_ID: member1, PLAYER_ID: member2}.get(uid)
    return g


@pytest.fixture()
def interaction(guild: MagicMock) -> AsyncMock:
    """Mock Discord Interaction for slash commands."""
    inter = AsyncMock()
    inter.response = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.followup = AsyncMock()
    inter.followup.send = AsyncMock()
    inter.guild = guild
    inter.user = MagicMock()
    inter.user.id = USER_ID
    inter.channel_id = CHANNEL_ID
    inter.channel = MagicMock()
    return inter


@pytest.fixture()
def persisted_campaign(db_session: Session) -> Campaign:
    """A campaign already saved in the DB (for resume/save/end tests)."""
    c = Campaign(id="test-camp-1", name="Dark Forest", player_names=[str(USER_ID)])
    CampaignRepository(db_session).save(c)
    db_session.flush()
    return c


@pytest.fixture()
def persisted_channel(db_session: Session, persisted_campaign: Campaign) -> int:
    """A channel mapping pointing to persisted_campaign."""
    CampaignChannelRepository(db_session).save(CHANNEL_ID, persisted_campaign.id, GUILD_ID)
    db_session.flush()
    return CHANNEL_ID


# ---------------------------------------------------------------------------
# _parse_mentions
# ---------------------------------------------------------------------------


class TestParseMentions:
    """Static helper for extracting user IDs from Discord mention syntax."""

    def test_standard_mentions(self) -> None:
        result = SessionCog._parse_mentions("<@123> <@456>")
        assert result == [123, 456]

    def test_nickname_mentions(self) -> None:
        result = SessionCog._parse_mentions("<@!789>")
        assert result == [789]

    def test_no_mentions(self) -> None:
        assert SessionCog._parse_mentions("hello world") == []

    def test_mixed_text(self) -> None:
        result = SessionCog._parse_mentions("invite <@100> and <@!200> please")
        assert result == [100, 200]


# ---------------------------------------------------------------------------
# /start_campaign
# ---------------------------------------------------------------------------


class TestStartCampaign:
    """Tests for the /start_campaign command."""

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    @patch("bot.cogs.session.create_session_channel")
    async def test_creates_campaign_and_channel(
        self,
        mock_create_channel: AsyncMock,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        db_session: Session,
    ) -> None:
        # Simulate channel creation returning a mock channel
        mock_channel = AsyncMock()
        mock_channel.id = 999000
        mock_channel.mention = "#campagne-foret-sombre"
        mock_channel.send = AsyncMock()
        mock_create_channel.return_value = mock_channel

        await cog.start_campaign.callback(cog, interaction, "Foret sombre", f"<@{PLAYER_ID}>")  # type: ignore[call-arg, arg-type]

        # Deferred response
        interaction.response.defer.assert_called_once()
        # Channel was created
        mock_create_channel.assert_called_once()
        # Welcome embed sent in new channel
        mock_channel.send.assert_called_once()
        # Session stored in bot
        assert 999000 in cog.bot.sessions
        session = cog.bot.sessions[999000]
        assert session.campaign.name == "Foret sombre"
        # Invoker added to player list
        assert str(USER_ID) in session.campaign.player_names
        # Campaign persisted
        camp = CampaignRepository(db_session).get_by_id(session.campaign.id)
        assert camp is not None
        assert camp.name == "Foret sombre"
        # Channel mapping persisted
        mapping = CampaignChannelRepository(db_session).get_by_channel(999000)
        assert mapping is not None

    @pytest.mark.asyncio
    async def test_no_players_mentioned(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        await cog.start_campaign.callback(cog, interaction, "Theme", "no mentions here")  # type: ignore[call-arg, arg-type]
        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args
        assert msg[1].get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_no_guild(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        interaction.guild = None
        await cog.start_campaign.callback(cog, interaction, "Theme", f"<@{PLAYER_ID}>")  # type: ignore[call-arg, arg-type]
        # Second followup call is the guild error
        calls = interaction.followup.send.call_args_list
        assert any(c[1].get("ephemeral") is True for c in calls)

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    @patch("bot.cogs.session.create_session_channel")
    async def test_invoker_not_duplicated(
        self,
        mock_create_channel: AsyncMock,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
    ) -> None:
        """If invoker already in the mention list, don't add twice."""
        mock_channel = AsyncMock()
        mock_channel.id = 999001
        mock_channel.send = AsyncMock()
        mock_channel.mention = "#test"
        mock_create_channel.return_value = mock_channel

        # Invoker mentions themselves
        await cog.start_campaign.callback(cog, interaction, "Theme", f"<@{USER_ID}> <@{PLAYER_ID}>")  # type: ignore[call-arg, arg-type]
        session = cog.bot.sessions[999001]
        # USER_ID should appear only once
        assert session.campaign.player_names.count(str(USER_ID)) == 1


# ---------------------------------------------------------------------------
# /resume
# ---------------------------------------------------------------------------


class TestResume:
    """Tests for the /resume command."""

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_loads_session(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
    ) -> None:
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.defer.assert_called_once()
        assert CHANNEL_ID in cog.bot.sessions
        session = cog.bot.sessions[CHANNEL_ID]
        assert session.campaign.id == persisted_campaign.id
        assert session.campaign.name == "Dark Forest"
        mock_ai.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_loads_location(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        # Persist a location and update campaign to point to it
        loc = Location(name="Tavern", description="A cozy tavern")
        LocationRepository(db_session).save(loc, persisted_campaign.id)
        persisted_campaign.current_location = "Tavern"
        CampaignRepository(db_session).update(persisted_campaign)
        db_session.flush()

        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        session = cog.bot.sessions[CHANNEL_ID]
        assert session.current_location is not None
        assert session.current_location.name == "Tavern"

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_loads_characters(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        # Persist a player character
        char = create_character(
            "Thorin", Race.DWARF, CharacterClass.FIGHTER,
            AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
        )
        inv = create_inventory()
        PlayerCharacterRepository(db_session).save(
            USER_ID, persisted_campaign.id, char, inv, None,
        )
        db_session.flush()

        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        session = cog.bot.sessions[CHANNEL_ID]
        assert USER_ID in session.characters
        assert session.characters[USER_ID].name == "Thorin"
        assert USER_ID in session.inventories

    @pytest.mark.asyncio
    async def test_resume_already_active(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        cog.bot.sessions[CHANNEL_ID] = MagicMock()
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]
        interaction.followup.send.assert_called_once()
        assert "deja active" in interaction.followup.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_resume_no_mapping(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]
        interaction.followup.send.assert_called_once()
        msg = interaction.followup.send.call_args[0][0]
        assert "Aucune campagne" in msg

    @pytest.mark.asyncio
    async def test_resume_no_channel_id(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        interaction.channel_id = None
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]
        interaction.followup.send.assert_called_once()
        assert interaction.followup.send.call_args[1].get("ephemeral") is True


# ---------------------------------------------------------------------------
# /save
# ---------------------------------------------------------------------------


class TestSave:
    """Tests for the /save command."""

    @pytest.mark.asyncio
    async def test_save_persists_campaign(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        db_session: Session,
    ) -> None:
        # Set up an active session
        session = GameSession(campaign=persisted_campaign)
        cog.bot.sessions[CHANNEL_ID] = session

        # Mutate something
        persisted_campaign.interaction_count = 42

        await cog.save.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.send_message.assert_called_once()
        assert "sauvegardee" in interaction.response.send_message.call_args[0][0]

        # Verify DB was updated
        reloaded = CampaignRepository(db_session).get_by_id(persisted_campaign.id)
        assert reloaded is not None
        assert reloaded.interaction_count == 42

    @pytest.mark.asyncio
    async def test_save_persists_characters(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        db_session: Session,
    ) -> None:
        char = create_character(
            "Elara", Race.ELF, CharacterClass.WIZARD,
            AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=13, CHA=10),
        )
        inv = create_inventory()

        session = GameSession(campaign=persisted_campaign)
        session.characters[USER_ID] = char
        session.inventories[USER_ID] = inv
        session.spellcasters[USER_ID] = None
        cog.bot.sessions[CHANNEL_ID] = session

        await cog.save.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        # Character persisted via save (insert path since it doesn't exist yet)
        result = PlayerCharacterRepository(db_session).get(USER_ID, persisted_campaign.id)
        assert result is not None
        loaded_char, _, _ = result
        assert loaded_char.name == "Elara"

    @pytest.mark.asyncio
    async def test_save_no_session(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        await cog.save.callback(cog, interaction)  # type: ignore[call-arg, arg-type]
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True


# ---------------------------------------------------------------------------
# /end_campaign
# ---------------------------------------------------------------------------


class TestEndCampaign:
    """Tests for the /end_campaign command."""

    @pytest.mark.asyncio
    @patch("bot.cogs.session.archive_channel")
    async def test_end_saves_archives_cleans(
        self,
        mock_archive: AsyncMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        guild: MagicMock,
    ) -> None:
        session = GameSession(campaign=persisted_campaign)
        cog.bot.sessions[CHANNEL_ID] = session

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.defer.assert_called_once()
        # Farewell message sent
        farewell = interaction.followup.send.call_args[0][0]
        assert persisted_campaign.name in farewell
        # Channel archived
        mock_archive.assert_called_once_with(interaction.channel, guild)
        # Session removed
        assert CHANNEL_ID not in cog.bot.sessions

    @pytest.mark.asyncio
    async def test_end_no_session(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True


# ---------------------------------------------------------------------------
# /settings
# ---------------------------------------------------------------------------


class TestSettings:
    """Tests for the /settings command."""

    @pytest.mark.asyncio
    async def test_upserts_guild_config(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        db_session: Session,
    ) -> None:
        await cog.settings.callback(cog, interaction, "My Sessions")  # type: ignore[call-arg, arg-type]

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "My Sessions" in msg

        # Verify persisted
        config = GuildConfigRepository(db_session).get(GUILD_ID)
        assert config is not None
        assert config.category_name == "My Sessions"

    @pytest.mark.asyncio
    async def test_upserts_overwrites_existing(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        db_session: Session,
    ) -> None:
        # Pre-save a config
        GuildConfigRepository(db_session).save(
            GuildConfig(guild_id=GUILD_ID, category_name="Old"),
        )
        db_session.flush()

        await cog.settings.callback(cog, interaction, "New Category")  # type: ignore[call-arg, arg-type]

        config = GuildConfigRepository(db_session).get(GUILD_ID)
        assert config is not None
        assert config.category_name == "New Category"

    @pytest.mark.asyncio
    async def test_settings_no_guild(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        interaction.guild = None
        await cog.settings.callback(cog, interaction, "Category")  # type: ignore[call-arg, arg-type]
        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True
