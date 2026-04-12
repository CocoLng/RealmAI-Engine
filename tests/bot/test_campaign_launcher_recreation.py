"""Tests for character edit flow (re-click on 'Create Character')."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.campaign_launcher import CampaignLauncher, PlayerProgress
from bot.views.character_edit_flow import CharacterEditFlow
from engine.character import (
    Ability,
    Alignment,
    Character,
    CharacterClass,
    Race,
    Skill,
    AbilityScores,
)
from engine.inventory import Inventory
from engine.spells import SpellcasterState
from world.campaign import Campaign


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAMPAIGN_ID = "recreate-test-001"
PLAYER_A = 111


@pytest.fixture()
def channel() -> AsyncMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    ch.id = 999
    return ch


@pytest.fixture()
def bot(channel: AsyncMock) -> MagicMock:
    b = MagicMock()
    b.launchers = {channel.id: None}
    b.sessions = {}
    b.db_factory = MagicMock(return_value=MagicMock())
    return b


@pytest.fixture()
def campaign() -> Campaign:
    return Campaign(id=CAMPAIGN_ID, name="Test Campaign", player_names=[str(PLAYER_A)])


@pytest.fixture()
def launcher(bot: MagicMock, campaign: Campaign, channel: AsyncMock) -> CampaignLauncher:
    return CampaignLauncher(
        bot=bot,
        campaign=campaign,
        channel=channel,
        player_ids=[PLAYER_A],
    )


def _make_interaction(user_id: int = PLAYER_A) -> AsyncMock:
    interaction = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "TestPlayer"
    interaction.response = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


def _make_character() -> Character:
    """Create a minimal Character for testing."""
    return Character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        level=1,
        xp=0,
        alignment=Alignment.LAWFUL_GOOD,
        ability_scores=AbilityScores(STR=16, DEX=12, CON=15, INT=8, WIS=10, CHA=13),
        hp=12,
        max_hp=12,
        ac=12,
        speed=25,
        proficiency_bonus=2,
        saving_throw_proficiencies=(Ability.STR, Ability.CON),
        hit_die="1d10",
        size="Medium",
        features=[],
        skill_proficiencies=[Skill.ATHLETICS, Skill.PERCEPTION],
    )


RAW_ASSIGNMENTS = {
    Ability.STR: 15,
    Ability.DEX: 12,
    Ability.CON: 13,
    Ability.INT: 8,
    Ability.WIS: 10,
    Ability.CHA: 14,
}


def _populate_player(
    launcher: CampaignLauncher,
    user_id: int,
    progress: PlayerProgress,
) -> None:
    """Set up a player with a real character at the given progress."""
    launcher.characters[user_id] = _make_character()
    launcher.inventories[user_id] = MagicMock(spec=Inventory)
    launcher.spellcasters[user_id] = MagicMock(spec=SpellcasterState)
    launcher.raw_assignments[user_id] = dict(RAW_ASSIGNMENTS)
    launcher.player_progress[user_id] = progress


# ---------------------------------------------------------------------------
# Launcher shows edit view instead of reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_character_shows_edit_view(launcher: CampaignLauncher) -> None:
    """Player with existing character sees the edit menu, not a full reset."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    # Should send an ephemeral message (the edit view)
    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs[1].get("ephemeral") is True
    # Character should NOT have been deleted
    assert PLAYER_A in launcher.characters
    assert launcher.player_progress[PLAYER_A] == PlayerProgress.GEAR_DONE


@pytest.mark.asyncio
async def test_character_done_shows_edit_view(launcher: CampaignLauncher) -> None:
    """Player at CHARACTER_DONE also sees edit menu."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.CHARACTER_DONE)
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    interaction.response.send_message.assert_called_once()
    assert PLAYER_A in launcher.characters


# ---------------------------------------------------------------------------
# Blocked after launch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_blocked_after_launch(launcher: CampaignLauncher) -> None:
    """_launched=True, click rejected."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    launcher._launched = True
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    interaction.response.send_message.assert_called_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "deja commence" in msg
    assert launcher.player_progress[PLAYER_A] == PlayerProgress.GEAR_DONE


# ---------------------------------------------------------------------------
# Stale callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_gear_callback_ignored(launcher: CampaignLauncher) -> None:
    """_on_gear_selected callback ignored if progress == PENDING."""
    launcher.player_progress[PLAYER_A] = PlayerProgress.PENDING
    interaction = _make_interaction()
    kit = MagicMock()

    await launcher._on_gear_selected(interaction, kit)

    assert launcher.player_progress[PLAYER_A] == PlayerProgress.PENDING


# ---------------------------------------------------------------------------
# CharacterEditFlow cascade tests
# ---------------------------------------------------------------------------


class TestEditFlowCascades:
    """Test that cascade rules add dependent fields."""

    def _make_flow(self) -> CharacterEditFlow:
        character = _make_character()
        return CharacterEditFlow(
            character=character,
            raw_assignments=dict(RAW_ASSIGNMENTS),
            language="fr",
            on_complete=AsyncMock(),
        )

    def test_cascade_race_adds_stats(self) -> None:
        flow = self._make_flow()
        fields = flow._apply_cascades({"race"})
        assert "stats" in fields

    def test_cascade_class_adds_skills(self) -> None:
        flow = self._make_flow()
        fields = flow._apply_cascades({"class"})
        assert "skills" in fields

    def test_cascade_alignment_no_extras(self) -> None:
        flow = self._make_flow()
        fields = flow._apply_cascades({"alignment"})
        assert fields == {"alignment"}

    def test_cascade_name_no_extras(self) -> None:
        flow = self._make_flow()
        fields = flow._apply_cascades({"name"})
        assert fields == {"name"}

    def test_cascade_race_and_class(self) -> None:
        flow = self._make_flow()
        fields = flow._apply_cascades({"race", "class"})
        assert "stats" in fields
        assert "skills" in fields


class TestEditFlowQueueOrder:
    """Test that the edit queue respects canonical order."""

    def _make_flow(self) -> CharacterEditFlow:
        character = _make_character()
        return CharacterEditFlow(
            character=character,
            raw_assignments=dict(RAW_ASSIGNMENTS),
            language="fr",
            on_complete=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_queue_order_respects_canonical(self) -> None:
        flow = self._make_flow()

        captured_queue: list[str] = []

        async def capture_advance(interaction: AsyncMock) -> None:
            captured_queue.extend(flow._edit_queue)

        flow._advance = capture_advance  # type: ignore[method-assign]

        interaction = AsyncMock()
        await flow.start(interaction, ["name", "race", "class"])

        # Full queue should be in canonical order (cascades add stats, skills)
        expected = ["race", "class", "stats", "skills", "name"]
        assert captured_queue == expected


class TestEditFlowClassChanged:
    """Test class_changed detection."""

    def test_same_class_not_changed(self) -> None:
        character = _make_character()
        flow = CharacterEditFlow(
            character=character,
            raw_assignments=dict(RAW_ASSIGNMENTS),
            language="fr",
            on_complete=AsyncMock(),
        )
        # Don't change class
        assert flow.char_class == CharacterClass.FIGHTER
        flow.class_changed = flow.char_class != flow.original_class
        assert flow.class_changed is False

    def test_different_class_changed(self) -> None:
        character = _make_character()
        flow = CharacterEditFlow(
            character=character,
            raw_assignments=dict(RAW_ASSIGNMENTS),
            language="fr",
            on_complete=AsyncMock(),
        )
        flow.char_class = CharacterClass.WIZARD
        flow.class_changed = flow.char_class != flow.original_class
        assert flow.class_changed is True
