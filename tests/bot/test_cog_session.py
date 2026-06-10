"""Tests for the Session cog -- campaign lifecycle commands."""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from bot.cogs.session import SessionCog
from bot.config import GuildConfig
from bot.game_session import GameSession
from db.database import Base
from db.repositories import (
    CampaignChannelRepository,
    CampaignRepository,
    GuildConfigRepository,
    LocationRepository,
    NPCRepository,
    PlayerCharacterRepository,
    QuestRepository,
)
from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.combat import CombatEndReason, CombatSide, CombatState, Combatant
from engine.inventory import create_inventory
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus


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
    mock_bot.lobbies = {}
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
# /start_campaign — covered by tests/scenarios/test_character_creation_lobby.py
# (the lobby flow is end-to-end and not unit-testable in isolation here).
# ---------------------------------------------------------------------------


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
    @patch("bot.location_prefetch.schedule_location_prefetch")
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_schedules_location_prefetch(
        self,
        mock_ai: MagicMock,
        mock_sched: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
    ) -> None:
        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        mock_sched.assert_called_once()
        assert mock_sched.call_args.args[0] is cog.bot.sessions[CHANNEL_ID]

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


def _make_combat_state(*, is_active: bool = True) -> CombatState:
    """An active (or finished) single-PC combat state for resume tests."""
    char = create_character(
        "Hero", Race.HUMAN, CharacterClass.FIGHTER,
        AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
    )
    combatant = Combatant(
        name="Hero", side=CombatSide.PLAYER,
        character=char, inventory=create_inventory(), initiative=15,
    )
    return CombatState(
        combatants=[combatant],
        round_number=2,
        current_turn_index=0,
        is_active=is_active,
        end_reason=None if is_active else CombatEndReason.VICTORY,
    )


class TestResumeCombatRebuild:
    """C5 — /resume must rebuild the TurnManager for an active combat."""

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_active_combat_rebuilds_turn_manager(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        persisted_campaign.combat_state_json = _make_combat_state().model_dump_json()
        CampaignRepository(db_session).update(persisted_campaign)
        db_session.flush()

        turn_manager = MagicMock()
        turn_manager._prompt_turn = AsyncMock()
        combat_cog = MagicMock()
        combat_cog.build_turn_manager = MagicMock(return_value=turn_manager)
        cog.bot.get_cog = MagicMock(return_value=combat_cog)

        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        session = cog.bot.sessions[CHANNEL_ID]
        combat_cog.build_turn_manager.assert_called_once_with(
            interaction.channel, session,
        )
        assert session.combat_turn_manager is turn_manager
        turn_manager._prompt_turn.assert_awaited_once()
        prompted = turn_manager._prompt_turn.call_args[0][0]
        assert prompted.name == "Hero"

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_inactive_combat_does_not_rebuild(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        """A finished-combat snapshot must not resurrect a TurnManager."""
        persisted_campaign.combat_state_json = (
            _make_combat_state(is_active=False).model_dump_json()
        )
        CampaignRepository(db_session).update(persisted_campaign)
        db_session.flush()

        combat_cog = MagicMock()
        cog.bot.get_cog = MagicMock(return_value=combat_cog)

        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        session = cog.bot.sessions[CHANNEL_ID]
        combat_cog.build_turn_manager.assert_not_called()
        assert session.combat_turn_manager is None
        msg = interaction.followup.send.call_args[0][0]
        assert "combat en cours" not in msg

    @pytest.mark.asyncio
    @patch("bot.cogs.session.create_ai_services")
    async def test_resume_combat_rebuild_failure_does_not_break_resume(
        self,
        mock_ai: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        """A TurnManager rebuild failure degrades gracefully — session stays up."""
        persisted_campaign.combat_state_json = _make_combat_state().model_dump_json()
        CampaignRepository(db_session).update(persisted_campaign)
        db_session.flush()

        combat_cog = MagicMock()
        combat_cog.build_turn_manager = MagicMock(side_effect=RuntimeError("boom"))
        cog.bot.get_cog = MagicMock(return_value=combat_cog)

        await cog.resume.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        assert CHANNEL_ID in cog.bot.sessions
        assert cog.bot.sessions[CHANNEL_ID].combat_turn_manager is None


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

    @pytest.mark.asyncio
    async def test_end_rejects_non_host(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
    ) -> None:
        """A player who is not the campaign host cannot end the campaign."""
        session = GameSession(campaign=persisted_campaign, creator_id=USER_ID)
        cog.bot.sessions[CHANNEL_ID] = session
        # Interaction comes from a *different* user
        interaction.user.id = PLAYER_ID

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.send_message.assert_called_once()
        msg = interaction.response.send_message.call_args[0][0]
        assert "hôte" in msg.lower() or "host" in msg.lower()
        assert interaction.response.send_message.call_args[1].get("ephemeral") is True
        # Defer + archive must NOT have been called
        interaction.response.defer.assert_not_called()
        # Session is still alive
        assert CHANNEL_ID in cog.bot.sessions

    @pytest.mark.asyncio
    @patch("bot.cogs.session.archive_channel")
    async def test_end_allows_host(
        self,
        mock_archive: AsyncMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
    ) -> None:
        """The campaign host can end the campaign."""
        session = GameSession(campaign=persisted_campaign, creator_id=USER_ID)
        cog.bot.sessions[CHANNEL_ID] = session
        interaction.user.id = USER_ID

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.defer.assert_called_once()
        mock_archive.assert_called_once()
        assert CHANNEL_ID not in cog.bot.sessions

    @pytest.mark.asyncio
    @patch("bot.location_prefetch.cancel_for_campaign")
    @patch("bot.cogs.session.archive_channel")
    async def test_end_cancels_location_prefetch(
        self,
        mock_archive: AsyncMock,
        mock_cancel: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
    ) -> None:
        """A dead campaign's background neighbor-prefetch loop is stopped
        (H8) — it would otherwise keep burning the shared gate for a
        session nobody plays anymore."""
        session = GameSession(campaign=persisted_campaign, creator_id=USER_ID)
        cog.bot.sessions[CHANNEL_ID] = session
        interaction.user.id = USER_ID

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        mock_cancel.assert_called_once_with(persisted_campaign.id)

    @pytest.mark.asyncio
    @patch("bot.location_prefetch.cancel_for_campaign")
    @patch("bot.cogs.session.archive_channel")
    async def test_end_survives_cancel_prefetch_failure(
        self,
        mock_archive: AsyncMock,
        mock_cancel: MagicMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
    ) -> None:
        """A failure cancelling background prefetch must never block the
        rest of /end_campaign — best-effort, like the ChromaDB/Arc Tracker
        cleanup steps."""
        mock_cancel.side_effect = RuntimeError("boom")
        session = GameSession(campaign=persisted_campaign, creator_id=USER_ID)
        cog.bot.sessions[CHANNEL_ID] = session
        interaction.user.id = USER_ID

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        mock_archive.assert_called_once()
        assert CHANNEL_ID not in cog.bot.sessions


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


# ---------------------------------------------------------------------------
# Round-trip integration tests (real in-memory SQLite, no mocks)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_factory():
    """In-memory SQLite session factory with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory


class TestPersistSessionRoundTrip:
    """Full save -> load round-trip through real DB."""

    def test_combat_state_roundtrip(self, db_factory):
        """Save a session with combat, reload campaign, verify combat JSON."""
        campaign = Campaign(id="rt-1", name="Round Trip")

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        db_session.commit()
        db_session.close()

        char = create_character(
            name="Hero", race=Race.HUMAN, char_class=CharacterClass.FIGHTER,
            ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
        )
        combatant = Combatant(
            name="Hero", side=CombatSide.PLAYER,
            character=char, inventory=create_inventory(), initiative=15,
        )
        combat = CombatState(combatants=[combatant], round_number=3, current_turn_index=0)

        campaign.combat_state_json = combat.model_dump_json()
        db_session = db_factory()
        CampaignRepository(db_session).update(campaign)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        restored = CampaignRepository(db_session).get_by_id("rt-1")
        db_session.close()

        assert restored is not None
        assert restored.combat_state_json is not None
        restored_combat = CombatState.model_validate_json(restored.combat_state_json)
        assert restored_combat.round_number == 3
        assert len(restored_combat.combatants) == 1
        assert restored_combat.combatants[0].name == "Hero"

    def test_npcs_roundtrip(self, db_factory):
        """Save NPCs for a campaign, reload, verify."""
        campaign = Campaign(id="rt-2", name="NPC Test")
        npc = NPC(
            name="Barkeep", race=Race.HUMAN, level=1,
            ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
            hp=8, max_hp=8, ac=10, disposition=NPCDisposition.FRIENDLY,
        )

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        NPCRepository(db_session).save(npc, campaign.id)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        npcs = NPCRepository(db_session).list_by_campaign("rt-2")
        db_session.close()

        assert len(npcs) == 1
        assert npcs[0].name == "Barkeep"
        assert npcs[0].disposition == NPCDisposition.FRIENDLY

    def test_quests_roundtrip(self, db_factory):
        """Save quests for a campaign, reload, verify."""
        campaign = Campaign(id="rt-3", name="Quest Test")
        quest = Quest(
            title="Find the key",
            description="A key is lost",
            status=QuestStatus.ACTIVE,
        )

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        QuestRepository(db_session).save(quest, campaign.id)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        quests = QuestRepository(db_session).list_by_campaign("rt-3")
        db_session.close()

        assert len(quests) == 1
        assert quests[0].title == "Find the key"
        assert quests[0].status == QuestStatus.ACTIVE

    def test_no_combat_state_returns_none(self, db_factory):
        """Campaign without combat -> combat_state_json is None."""
        campaign = Campaign(id="rt-4", name="Peaceful")

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        restored = CampaignRepository(db_session).get_by_id("rt-4")
        db_session.close()

        assert restored is not None
        assert restored.combat_state_json is None


# ---------------------------------------------------------------------------
# /story_catch_up
# ---------------------------------------------------------------------------


class TestStoryCatchUpCommand:
    """Tests for the /story_catch_up slash command."""

    @pytest.mark.asyncio
    async def test_no_active_session_returns_error(
        self, cog: SessionCog, interaction: AsyncMock,
    ) -> None:
        """When no session exists for the channel, posts an ephemeral error."""
        # No session registered for CHANNEL_ID
        await cog.story_catch_up.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is True
        call_text = interaction.followup.send.call_args[0][0]
        assert "Aucune campagne active" in call_text

    @pytest.mark.asyncio
    async def test_active_session_runs_director_and_posts_recap(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When a session exists, runs StoryDirector and posts a recap embed."""
        from ai.models import DirectorNote
        from ai.story_director import StoryDirector

        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=["Visit the elder", "Search the wagon", "Follow the stranger"],
            priority="low",
            current_objective="Find the ancient map",
            current_beat_atmosphere="A sense of urgency fills the air.",
        )

        mock_director = MagicMock(spec=StoryDirector)
        mock_director.check_coherence.return_value = note

        session = GameSession(campaign=persisted_campaign)
        session.semantic_memory = MagicMock()
        session.story_director = mock_director
        cog.bot.sessions[CHANNEL_ID] = session

        # Patch asyncio.to_thread so it just calls check_coherence synchronously
        import asyncio

        async def fake_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            return fn(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        await cog.story_catch_up.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        interaction.response.defer.assert_called_once()
        interaction.followup.send.assert_called_once()
        # Should post an embed (not ephemeral)
        call_kwargs = interaction.followup.send.call_args[1]
        assert call_kwargs.get("ephemeral") is not True
        embed = call_kwargs.get("embed")
        assert embed is not None
        # Embed description contains the objective
        assert "Find the ancient map" in embed.description
        # Embed fields contain the hooks
        field_values = " ".join(f.value for f in embed.fields)
        assert "Visit the elder" in field_values
        assert "Search the wagon" in field_values
        assert "Follow the stranger" in field_values
        # Director was called with campaign_id
        mock_director.check_coherence.assert_called_once_with(
            persisted_campaign.id, "(catch-up request)"
        )


# ---------------------------------------------------------------------------
# Arc Tracker lifecycle
# ---------------------------------------------------------------------------


class TestCampaignChannelArcStore:
    """Unit tests for _CampaignChannelArcStore — the store adapter."""

    def test_get_message_id_returns_none_when_no_row(self, db_session: Session) -> None:
        """get_message_id returns None when no mapping exists for the channel."""
        from bot.cogs.session import _CampaignChannelArcStore

        store = _CampaignChannelArcStore(lambda: db_session)
        assert store.get_message_id(99999) is None

    def test_get_and_set_message_id_roundtrip(self) -> None:
        """set_message_id persists; get_message_id reads it back via a real factory."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from bot.cogs.session import _CampaignChannelArcStore
        from db.database import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)

        # Seed a channel row so update_arc_tracker_message_id has a row to mutate
        seed_session = factory()
        CampaignChannelRepository(seed_session).save(CHANNEL_ID, "arc-rt-camp", GUILD_ID)
        seed_session.commit()
        seed_session.close()

        store = _CampaignChannelArcStore(factory)

        # Initially None
        assert store.get_message_id(CHANNEL_ID) is None
        # Set a value
        store.set_message_id(CHANNEL_ID, 12345678)
        assert store.get_message_id(CHANNEL_ID) == 12345678
        # Clear it
        store.set_message_id(CHANNEL_ID, None)
        assert store.get_message_id(CHANNEL_ID) is None

    def test_set_message_id_no_op_for_missing_row(self, db_session: Session) -> None:
        """set_message_id on an unknown channel silently does nothing."""
        from bot.cogs.session import _CampaignChannelArcStore

        store = _CampaignChannelArcStore(lambda: db_session)
        # Should not raise
        store.set_message_id(88888, 99999)


# NOTE: TestStartCampaignArcTracker (legacy /start_campaign players-string flow)
# was removed when /start_campaign became lobby-driven. The Arc Tracker pin now
# fires inside _launch_campaign_from_lobby; coverage for it lives in the lobby
# scenario test (tests/scenarios/test_character_creation_lobby.py).


class TestEndCampaignArcTracker:
    """Verify /end_campaign removes the pinned Arc Tracker message."""

    @pytest.mark.asyncio
    @patch("bot.cogs.session.archive_channel")
    async def test_end_campaign_removes_pin(
        self,
        mock_archive: AsyncMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        persisted_channel: int,
        db_session: Session,
    ) -> None:
        """If a message ID is stored, remove() unpins + deletes it."""
        from db.repositories import CampaignChannelRepository

        # Store an Arc Tracker message ID in the DB
        CampaignChannelRepository(db_session).update_arc_tracker_message_id(
            CHANNEL_ID, 777888999000,
        )
        db_session.flush()

        # Mock the Discord message that will be fetched
        mock_msg = AsyncMock()
        mock_msg.unpin = AsyncMock()
        mock_msg.delete = AsyncMock()
        interaction.channel.fetch_message = AsyncMock(return_value=mock_msg)

        session = GameSession(campaign=persisted_campaign)
        cog.bot.sessions[CHANNEL_ID] = session

        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        # The message should have been unpinned
        mock_msg.unpin.assert_called_once()
        # DB should be cleared
        stored = CampaignChannelRepository(db_session).get_arc_tracker_message_id(CHANNEL_ID)
        assert stored is None

    @pytest.mark.asyncio
    @patch("bot.cogs.session.archive_channel")
    async def test_end_campaign_remove_failure_does_not_break(
        self,
        mock_archive: AsyncMock,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
    ) -> None:
        """If Arc Tracker removal raises, /end_campaign still archives channel."""
        session = GameSession(campaign=persisted_campaign)
        cog.bot.sessions[CHANNEL_ID] = session

        # fetch_message raises so manager.remove raises internally
        interaction.channel.fetch_message = AsyncMock(side_effect=Exception("fetch failed"))

        # Should not raise
        await cog.end_campaign.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        # Archive was still called
        mock_archive.assert_called_once()


# ---------------------------------------------------------------------------
# /story_catch_up — force_next_director_run flag
# ---------------------------------------------------------------------------


class TestStoryCatchUpFlagsSession:
    """Verify /story_catch_up sets session.force_next_director_run = True."""

    @pytest.mark.asyncio
    async def test_story_catch_up_sets_force_flag_on_session(
        self,
        cog: SessionCog,
        interaction: AsyncMock,
        persisted_campaign: Campaign,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After /story_catch_up succeeds, session.force_next_director_run is True."""
        import asyncio

        from ai.models import DirectorNote
        from ai.story_director import StoryDirector

        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=["Inspect the ruin"],
            priority="low",
            current_objective="Reach the fortress",
        )

        mock_director = MagicMock(spec=StoryDirector)
        mock_director.check_coherence.return_value = note

        session = GameSession(campaign=persisted_campaign)
        assert session.force_next_director_run is False  # starts False
        session.semantic_memory = MagicMock()
        session.story_director = mock_director
        cog.bot.sessions[CHANNEL_ID] = session

        async def fake_to_thread(fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            return fn(*args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

        await cog.story_catch_up.callback(cog, interaction)  # type: ignore[call-arg, arg-type]

        # Flag must be set after successful recap
        assert session.force_next_director_run is True


# ---------------------------------------------------------------------------
# ActionHandlerCog — force_next_director_run consumed by pipeline
# ---------------------------------------------------------------------------


class TestActionHandlerConsumesForceDirectorFlag:
    """When session.force_next_director_run is True, the next ActionPipeline
    is constructed with force_director_run=True and the session flag is reset."""

    @pytest.mark.asyncio
    async def test_force_flag_passed_to_pipeline_and_consumed(self) -> None:
        """force_next_director_run=True on session → pipeline gets force_director_run=True,
        then session.force_next_director_run is reset to False."""
        import asyncio
        from dataclasses import dataclass, field as dc_field
        from typing import Any
        from unittest.mock import AsyncMock, MagicMock

        from bot.action_pipeline import ActionPipelineResult, PipelinePhase
        from bot.cogs.action_handler import ActionHandlerCog
        from engine.character import AbilityScores, CharacterClass, Race, create_character
        from world.campaign import Campaign

        player_id = 42
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        char = create_character("Hero", Race.HUMAN, CharacterClass.FIGHTER, scores)

        session = MagicMock()
        session.campaign = Campaign(id="force-test", name="Force Test", player_names=[str(player_id)])
        session.characters = {player_id: char}
        session.npcs = {}
        session.current_location = None
        session.combat_state = None
        session.combat_turn_manager = None
        session.inventories = {}
        session.language = "fr"
        session.interpreter = MagicMock()
        session.narrator = MagicMock()
        session.action_lock = asyncio.Lock()
        session.semantic_indexer = None
        # Key: flag is set to True (as if /story_catch_up was called)
        session.force_next_director_run = True

        captured_kwargs: list[dict[str, Any]] = []

        # Fake pipeline that captures kwargs and returns a valid result
        from ai.models import InterpretedAction
        from engine.validators import ActionType

        action = MagicMock(spec=InterpretedAction)
        action.action_type = ActionType.TALK
        fake_result = ActionPipelineResult(
            narrative="test",
            tone="dramatic",
            npc_name=None,
            npc_dialogue=None,
            mechanics_text="",
            interpreted_action=action,
            new_beat=None,
        )

        def fake_factory(**kwargs: Any) -> Any:
            captured_kwargs.append(dict(kwargs))
            pipeline = MagicMock()

            async def process(player_text: str, progress_callback: Any = None) -> Any:
                if progress_callback:
                    await progress_callback(PipelinePhase.DONE)
                return fake_result

            pipeline.process = process
            pipeline._pending_combat_start_embed = None
            return pipeline

        bot = MagicMock()
        bot.user = MagicMock()
        bot.user.id = 9999
        bot.sessions = {100: session}
        bot.db_factory = MagicMock()
        # get_cog must return None so combat bootstrap path is skipped
        bot.get_cog = MagicMock(return_value=None)

        cog = ActionHandlerCog(bot)
        cog._pipeline_factory = fake_factory  # type: ignore[method-assign]

        @dataclass
        class FakeAuthor:
            id: int
            bot: bool = False
            display_name: str = "Hero"

        @dataclass
        class FakeChannel:
            id: int
            send: AsyncMock = dc_field(default_factory=AsyncMock)

        @dataclass
        class FakeMessage:
            content: str
            author: Any
            channel: Any
            mentions: list[Any] = dc_field(default_factory=list)
            reply: AsyncMock = dc_field(default_factory=AsyncMock)

        # Patch channel.send to return a message mock for the progress embed
        channel = FakeChannel(id=100)
        progress_msg = AsyncMock()
        progress_msg.edit = AsyncMock()
        channel.send = AsyncMock(return_value=progress_msg)

        msg = FakeMessage(
            content="<@9999> je fouille l'autel",
            author=FakeAuthor(id=player_id),
            channel=channel,
            # Use the actual bot.user object so identity check passes (guard #3)
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        # Pipeline was constructed exactly once
        assert len(captured_kwargs) == 1
        # force_director_run was forwarded
        assert captured_kwargs[0]["force_director_run"] is True
        # Flag was consumed (reset to False)
        assert session.force_next_director_run is False


class TestLobbyPregenStatus:
    """H8 — the lobby embed shows the world-generation phase live."""

    def _make_lobby(self):
        from bot.lobby_state import GenerationPhase, LobbyState

        message = MagicMock()
        message.guild = MagicMock()
        message.guild.get_member.return_value = None
        message.edit = AsyncMock()
        lobby = LobbyState(
            creator_id=42,
            language="fr",
            campaign_name="Brumes du Nord",
            theme="Brumes du Nord",
        )
        lobby.lobby_message = message
        lobby.pregen_phase = GenerationPhase.ARC
        return lobby, message

    async def test_refresh_lobby_embed_passes_pregen_status(self):
        from bot.cogs.session import SessionCog

        lobby, message = self._make_lobby()
        cog = SessionCog.__new__(SessionCog)  # method under test needs no bot
        await cog._refresh_lobby_embed(lobby, lobby.lobby_message.guild)
        embed = message.edit.call_args.kwargs["embed"]
        assert any(
            "Génération du monde" in (field.name or "")
            for field in embed.fields
        )

    async def test_pregen_status_refresh_is_best_effort(self):
        from bot.cogs.session import SessionCog

        lobby, message = self._make_lobby()
        message.edit.side_effect = RuntimeError("discord down")
        cog = SessionCog.__new__(SessionCog)
        await cog._refresh_lobby_pregen_status(lobby)  # must not raise

    async def test_pregen_status_refresh_noop_without_message(self):
        from bot.cogs.session import SessionCog

        lobby, _ = self._make_lobby()
        lobby.lobby_message = None
        cog = SessionCog.__new__(SessionCog)
        await cog._refresh_lobby_pregen_status(lobby)  # must not raise

    async def test_pregen_refreshes_lobby_on_each_phase(self):
        from bot.cogs.session import SessionCog
        from bot.lobby_state import GenerationPhase
        from world.campaign import Campaign
        from world.location import Location

        lobby, _ = self._make_lobby()
        lobby.pregen_phase = GenerationPhase.PENDING
        cog = SessionCog.__new__(SessionCog)
        phases: list[GenerationPhase] = []

        async def record(lb) -> None:
            phases.append(lb.pregen_phase)

        cog._refresh_lobby_pregen_status = record  # type: ignore[method-assign]

        fake_arc = MagicMock()
        fake_arc.model_copy.return_value = SimpleNamespace(
            campaign_id="c1", beats=[], villain_name="L'Ombre",
        )
        fake_loc = Location(name="Place", description="d", generated=True)
        with (
            patch("ai.client.OllamaClient"),
            patch("engine.arc_recipes.generate_recipe"),
            patch("ai.arc_generator.ArcGenerator") as arc_cls,
            patch("ai.world_generator.WorldGenerator") as world_cls,
        ):
            arc_cls.return_value.generate.return_value = fake_arc
            world_cls.return_value.generate.return_value = fake_loc
            await cog._pregenerate_campaign_world(
                lobby, Campaign(name="Brumes du Nord"), "fr",
            )

        assert phases == [
            GenerationPhase.ARC,
            GenerationPhase.LOCATION,
            GenerationPhase.READY,
        ]

    async def test_pregen_refreshes_lobby_on_failure(self):
        from bot.cogs.session import SessionCog
        from bot.lobby_state import GenerationPhase
        from world.campaign import Campaign

        lobby, _ = self._make_lobby()
        lobby.pregen_phase = GenerationPhase.PENDING
        cog = SessionCog.__new__(SessionCog)
        phases: list[GenerationPhase] = []

        async def record(lb) -> None:
            phases.append(lb.pregen_phase)

        cog._refresh_lobby_pregen_status = record  # type: ignore[method-assign]

        with (
            patch("ai.client.OllamaClient"),
            patch("engine.arc_recipes.generate_recipe"),
            patch("ai.arc_generator.ArcGenerator") as arc_cls,
            patch("ai.world_generator.WorldGenerator"),
        ):
            arc_cls.return_value.generate.side_effect = RuntimeError("boom")
            await cog._pregenerate_campaign_world(
                lobby, Campaign(name="Brumes du Nord"), "fr",
            )

        assert phases[-1] == GenerationPhase.FAILED
