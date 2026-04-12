"""Tests for launch immersion: purge, countdown, and opening crawl embed."""

from __future__ import annotations

import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.campaign_launcher import CampaignLauncher
from bot.embeds.narrative_embed import build_opening_crawl_embed
from world.campaign import Campaign
from world.location import Location
from world.story_arc import StoryArc, StoryBeat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAMPAIGN_ID = "immersion-test-001"
PLAYER_A = 111


def _make_story_arc() -> StoryArc:
    """Create a minimal StoryArc for testing."""
    beats = [
        StoryBeat(
            beat_number=i,
            title=f"Beat {i}",
            description=f"Description of beat {i}",
            location_hint=f"Location {i}",
            encounter_type="exploration",
        )
        for i in range(1, 9)
    ]
    return StoryArc(
        campaign_id=CAMPAIGN_ID,
        theme="Dark Fantasy",
        premise="Un ancien mal se reveille dans les profondeurs.",
        beats=beats,
        villain_name="Sombre Seigneur",
        villain_motivation="Conquerir le monde",
    )


def _make_location() -> Location:
    return Location(name="Clairiere des Ombres", description="Une clairiere sinistre.")


@pytest.fixture()
def channel() -> AsyncMock:
    ch = AsyncMock()
    ch.send = AsyncMock()
    ch.purge = AsyncMock()
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
    return Campaign(id=CAMPAIGN_ID, name="Foret Sombre", player_names=[str(PLAYER_A)])


@pytest.fixture()
def launcher(bot: MagicMock, campaign: Campaign, channel: AsyncMock) -> CampaignLauncher:
    lnchr = CampaignLauncher(
        bot=bot,
        campaign=campaign,
        channel=channel,
        player_ids=[PLAYER_A],
    )
    lnchr.story_arc = _make_story_arc()
    lnchr.current_location = _make_location()
    return lnchr


def _patch_externals():
    """Context-manager stack to patch DB repos and AI services."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        with (
            patch("bot.campaign_launcher.create_ai_services"),
            patch("db.repositories.LocationRepository"),
            patch("db.repositories.CampaignRepository"),
            patch("db.repositories.StoryArcRepository"),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# Purge tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_purges_channel(launcher: CampaignLauncher) -> None:
    """channel.purge(limit=200) is called during _launch_campaign()."""
    with _patch_externals():
        await launcher._launch_campaign()

    launcher.channel.purge.assert_called_once_with(limit=200)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_launch_continues_if_purge_fails(launcher: CampaignLauncher) -> None:
    """Purge raising HTTPException does not block the launch."""
    launcher.channel.purge.side_effect = discord.HTTPException(  # type: ignore[attr-defined]
        MagicMock(status=403), "Forbidden",
    )

    with _patch_externals():
        await launcher._launch_campaign()

    # Launch completed — session was created
    assert launcher.channel.id in launcher.bot.sessions


# ---------------------------------------------------------------------------
# Countdown tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_sends_countdown(launcher: CampaignLauncher) -> None:
    """channel.send is called with countdown embed and edit is called on the message."""
    countdown_msg = AsyncMock()

    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First send after purge is the countdown embed
        if call_count == 1:
            return countdown_msg
        return AsyncMock()

    launcher.channel.send = AsyncMock(side_effect=_side_effect)  # type: ignore[method-assign]

    with _patch_externals():
        await launcher._launch_campaign()

    # Verify countdown embed was sent (first call)
    first_call = launcher.channel.send.call_args_list[0]
    countdown_embed = first_call.kwargs.get("embed")
    assert countdown_embed is not None
    assert "3" in countdown_embed.title

    # Verify edits happened (steps 2 → 1)
    assert countdown_msg.edit.call_count == 2


@pytest.mark.asyncio
async def test_countdown_message_deleted(launcher: CampaignLauncher) -> None:
    """The countdown message is deleted after the sequence."""
    countdown_msg = AsyncMock()

    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return countdown_msg
        return AsyncMock()

    launcher.channel.send = AsyncMock(side_effect=_side_effect)  # type: ignore[method-assign]

    with _patch_externals():
        await launcher._launch_campaign()

    countdown_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_countdown_failure_does_not_block_launch(launcher: CampaignLauncher) -> None:
    """If edit/delete fail, launch still completes."""
    countdown_msg = AsyncMock()
    countdown_msg.edit.side_effect = discord.HTTPException(
        MagicMock(status=500), "Internal",
    )
    countdown_msg.delete.side_effect = discord.HTTPException(
        MagicMock(status=500), "Internal",
    )

    call_count = 0

    async def _side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return countdown_msg
        return AsyncMock()

    launcher.channel.send = AsyncMock(side_effect=_side_effect)  # type: ignore[method-assign]

    with _patch_externals():
        await launcher._launch_campaign()

    # Launch completed despite edit/delete failures
    assert launcher.channel.id in launcher.bot.sessions


# ---------------------------------------------------------------------------
# Opening crawl embed tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_opening_crawl_embed_contains_premise() -> None:
    """Embed description contains story_arc.premise."""
    arc = _make_story_arc()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=arc,
        location=None,
    )
    assert arc.premise in str(embed.description)


@pytest.mark.asyncio
async def test_opening_crawl_embed_contains_location() -> None:
    """Embed has field 'Lieu de départ' with location name."""
    location = _make_location()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=None,
        location=location,
        language="fr",
    )
    field_names = [f.name for f in embed.fields]
    assert "Lieu de départ" in field_names
    loc_field = next(f for f in embed.fields if f.name == "Lieu de départ")
    assert location.name in str(loc_field.value)


@pytest.mark.asyncio
async def test_opening_crawl_embed_contains_first_beat() -> None:
    """Embed has field 'Premier chapitre' with first beat description."""
    arc = _make_story_arc()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=arc,
        location=None,
        language="fr",
    )
    field_names = [f.name for f in embed.fields]
    assert "Premier chapitre" in field_names
    beat_field = next(f for f in embed.fields if f.name == "Premier chapitre")
    assert arc.beats[0].description in str(beat_field.value)


@pytest.mark.asyncio
async def test_opening_crawl_embed_fallback() -> None:
    """Without arc or location, description is 'Votre aventure commence...'."""
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=None,
        location=None,
    )
    assert embed.description == "Votre aventure commence..."
    assert len(embed.fields) == 0
