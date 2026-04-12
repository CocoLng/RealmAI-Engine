"""Tests for force-launch button: view behavior and campaign launcher integration."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.campaign_launcher import CampaignLauncher, PlayerProgress
from bot.views.force_launch_view import ForceLaunchView
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Constants & Fixtures
# ---------------------------------------------------------------------------

CAMPAIGN_ID = "force-test-001"
CREATOR = 100
PLAYER_A = 111
PLAYER_B = 222


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
    return Campaign(
        id=CAMPAIGN_ID,
        name="Donjon Ancien",
        player_names=[str(PLAYER_A), str(PLAYER_B)],
    )


def _make_location() -> Location:
    return Location(name="Taverne du Dragon", description="Une taverne enfumee.")


def _make_launcher(
    bot: MagicMock,
    campaign: Campaign,
    channel: AsyncMock,
    *,
    creator_id: int = CREATOR,
) -> CampaignLauncher:
    return CampaignLauncher(
        bot=bot,
        campaign=campaign,
        channel=channel,
        player_ids=[PLAYER_A, PLAYER_B],
        creator_id=creator_id,
    )


# ---------------------------------------------------------------------------
# test_force_launch_button_shown_when_conditions_met
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_button_shown_when_conditions_met(
    bot: MagicMock, campaign: Campaign, channel: AsyncMock,
) -> None:
    """When generation is done and 1/2 players ready, force-launch button is posted."""
    launcher = _make_launcher(bot, campaign, channel)
    launcher.story_arc = MagicMock()
    launcher.current_location = _make_location()
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE
    # PLAYER_B still PENDING

    await launcher._check_ready()

    # Should have sent generation-ready message + force-launch message
    calls = channel.send.call_args_list
    force_calls = [c for c in calls if c.kwargs.get("view") is not None]
    assert len(force_calls) == 1
    view = force_calls[0].kwargs["view"]
    assert isinstance(view, ForceLaunchView)


# ---------------------------------------------------------------------------
# test_force_launch_not_shown_zero_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_not_shown_zero_ready(
    bot: MagicMock, campaign: Campaign, channel: AsyncMock,
) -> None:
    """No force-launch button when 0 players are ready."""
    launcher = _make_launcher(bot, campaign, channel)
    launcher.story_arc = MagicMock()
    launcher.current_location = _make_location()
    # Both players PENDING

    await launcher._check_ready()

    calls = channel.send.call_args_list
    force_calls = [c for c in calls if c.kwargs.get("view") is not None]
    assert len(force_calls) == 0
    assert launcher._force_launch_offered is False


# ---------------------------------------------------------------------------
# test_force_launch_not_shown_all_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_not_shown_all_ready(
    bot: MagicMock, campaign: Campaign, channel: AsyncMock,
) -> None:
    """When all players are ready, normal launch happens, no force-launch button."""
    launcher = _make_launcher(bot, campaign, channel)
    launcher.story_arc = MagicMock()
    launcher.current_location = _make_location()
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE
    launcher.player_progress[PLAYER_B] = PlayerProgress.GEAR_DONE

    with patch.object(launcher, "_launch_campaign", new_callable=AsyncMock):
        await launcher._check_ready()

    calls = channel.send.call_args_list
    force_calls = [c for c in calls if c.kwargs.get("view") is not None]
    assert len(force_calls) == 0
    assert launcher._force_launch_offered is False


# ---------------------------------------------------------------------------
# test_force_launch_excludes_non_ready
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_excludes_non_ready(
    bot: MagicMock, campaign: Campaign, channel: AsyncMock,
) -> None:
    """After force-launch click, non-ready players are removed from all dicts."""
    launcher = _make_launcher(bot, campaign, channel)
    launcher.story_arc = MagicMock()
    launcher.current_location = _make_location()
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE
    # PLAYER_B still PENDING
    launcher.characters[PLAYER_A] = MagicMock()
    launcher.characters[PLAYER_B] = MagicMock()
    launcher.inventories[PLAYER_A] = MagicMock()
    launcher.inventories[PLAYER_B] = MagicMock()

    interaction = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = CREATOR

    with patch.object(launcher, "_launch_campaign", new_callable=AsyncMock):
        await launcher._on_force_launch(interaction)

    assert PLAYER_B not in launcher.player_progress
    assert PLAYER_B not in launcher.characters
    assert PLAYER_B not in launcher.inventories
    assert PLAYER_B not in launcher.player_ids
    # PLAYER_A still present
    assert PLAYER_A in launcher.player_progress
    assert PLAYER_A in launcher.characters


# ---------------------------------------------------------------------------
# test_force_launch_creator_only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_creator_only() -> None:
    """Non-creator click is rejected with ephemeral message."""
    on_click = AsyncMock()
    view = ForceLaunchView(creator_id=CREATOR, on_click=on_click)

    interaction = AsyncMock()
    interaction.user = MagicMock()
    interaction.user.id = 9999  # not the creator

    # Call the button callback directly via the view's children
    button = view.children[0]
    assert isinstance(button, discord.ui.Button)
    await button.callback(interaction)

    interaction.response.send_message.assert_called_once()
    call_kwargs = interaction.response.send_message.call_args
    assert call_kwargs.kwargs.get("ephemeral") is True
    on_click.assert_not_called()


# ---------------------------------------------------------------------------
# test_force_launch_offered_once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_launch_offered_once(
    bot: MagicMock, campaign: Campaign, channel: AsyncMock,
) -> None:
    """_check_ready() called twice only posts the force-launch button once."""
    launcher = _make_launcher(bot, campaign, channel)
    launcher.story_arc = MagicMock()
    launcher.current_location = _make_location()
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE

    await launcher._check_ready()
    await launcher._check_ready()

    calls = channel.send.call_args_list
    force_calls = [c for c in calls if c.kwargs.get("view") is not None]
    assert len(force_calls) == 1


# ---------------------------------------------------------------------------
# test_force_launch_view_button_label
# ---------------------------------------------------------------------------


def test_force_launch_view_button_label() -> None:
    """View has exactly 1 button with the correct label."""
    on_click = AsyncMock()
    view = ForceLaunchView(creator_id=CREATOR, on_click=on_click)

    buttons = [child for child in view.children if isinstance(child, discord.ui.Button)]
    assert len(buttons) == 1
    assert buttons[0].label == "Lancer la partie"
