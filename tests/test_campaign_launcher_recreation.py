"""Tests for character re-creation before campaign launch."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.campaign_launcher import CampaignLauncher, PlayerProgress
from engine.character import Character
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
    return interaction


def _populate_player(launcher: CampaignLauncher, user_id: int, progress: PlayerProgress) -> None:
    """Set up a player with dummy character/inventory/spellcaster at the given progress."""
    launcher.characters[user_id] = MagicMock(spec=Character)
    launcher.inventories[user_id] = MagicMock(spec=Inventory)
    launcher.spellcasters[user_id] = MagicMock(spec=SpellcasterState)
    launcher.player_progress[user_id] = progress


# ---------------------------------------------------------------------------
# Re-creation resets state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_resets_state_from_gear_done(launcher: CampaignLauncher) -> None:
    """Player at GEAR_DONE re-clicks, state goes back to PENDING, dicts cleared."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    assert launcher.player_progress[PLAYER_A] == PlayerProgress.PENDING
    assert PLAYER_A not in launcher.characters
    assert PLAYER_A not in launcher.inventories
    assert PLAYER_A not in launcher.spellcasters


@pytest.mark.asyncio
async def test_recreate_resets_state_from_character_done(launcher: CampaignLauncher) -> None:
    """Player at CHARACTER_DONE re-clicks, same reset."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.CHARACTER_DONE)
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    assert launcher.player_progress[PLAYER_A] == PlayerProgress.PENDING
    assert PLAYER_A not in launcher.characters
    assert PLAYER_A not in launcher.inventories
    assert PLAYER_A not in launcher.spellcasters


# ---------------------------------------------------------------------------
# DB deletion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_deletes_db_record(launcher: CampaignLauncher) -> None:
    """PlayerCharacterRepository.delete is called on re-creation."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    interaction = _make_interaction()

    with patch("db.repositories.PlayerCharacterRepository") as MockRepo:
        mock_instance = MockRepo.return_value
        await launcher._on_create_character_clicked(interaction)

    mock_instance.delete.assert_called_once_with(PLAYER_A, CAMPAIGN_ID)


# ---------------------------------------------------------------------------
# Blocked after launch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_blocked_after_launch(launcher: CampaignLauncher) -> None:
    """_launched=True, click rejected."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    launcher._launched = True
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    interaction.response.send_message.assert_called_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "deja commence" in msg
    # State should NOT have been reset
    assert launcher.player_progress[PLAYER_A] == PlayerProgress.GEAR_DONE


# ---------------------------------------------------------------------------
# Stale callbacks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_gear_callback_ignored(launcher: CampaignLauncher) -> None:
    """_on_gear_selected callback ignored if progress == PENDING (reset happened)."""
    launcher.player_progress[PLAYER_A] = PlayerProgress.PENDING
    interaction = _make_interaction()
    kit = MagicMock()

    await launcher._on_gear_selected(interaction, kit)

    # Progress should still be PENDING
    assert launcher.player_progress[PLAYER_A] == PlayerProgress.PENDING


# ---------------------------------------------------------------------------
# Notified flag reset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recreate_resets_notified_flag(launcher: CampaignLauncher) -> None:
    """_notified_players_ready goes back to False on re-creation."""
    _populate_player(launcher, PLAYER_A, PlayerProgress.GEAR_DONE)
    launcher._notified_players_ready = True
    interaction = _make_interaction()

    await launcher._on_create_character_clicked(interaction)

    assert launcher._notified_players_ready is False
