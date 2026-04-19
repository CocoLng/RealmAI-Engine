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
        situation="Les villages frontaliers murmurent une prophétie oubliée.",
        call_to_action="Vous avez accepté la mission du Conseil : enquêter sur les disparitions.",
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
async def test_opening_crawl_embed_contains_situation() -> None:
    """Embed has field 'La situation' with story_arc.situation."""
    arc = _make_story_arc()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=arc,
        location=None,
        language="fr",
    )
    sit_field = next(
        (f for f in embed.fields if f.name is not None and "La situation" in f.name),
        None,
    )
    assert sit_field is not None
    assert arc.situation in str(sit_field.value)


@pytest.mark.asyncio
async def test_opening_crawl_embed_contains_call_to_action() -> None:
    """Embed has field 'Votre appel' with story_arc.call_to_action."""
    arc = _make_story_arc()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=arc,
        location=None,
        language="fr",
    )
    call_field = next(
        (f for f in embed.fields if f.name is not None and "Votre appel" in f.name),
        None,
    )
    assert call_field is not None
    assert arc.call_to_action in str(call_field.value)


@pytest.mark.asyncio
async def test_opening_crawl_embed_skips_empty_new_fields() -> None:
    """Backward compat: arcs pre-refactor have no situation/call_to_action."""
    beats = [
        StoryBeat(
            beat_number=i,
            title=f"Beat {i}",
            description=f"Desc {i}",
            location_hint="X",
            encounter_type="exploration",
        )
        for i in range(1, 9)
    ]
    legacy_arc = StoryArc(
        campaign_id=CAMPAIGN_ID,
        theme="Dark",
        premise="Legacy premise without hook fields.",
        beats=beats,
        villain_name="X",
        villain_motivation="Y",
    )
    embed = build_opening_crawl_embed(
        campaign_name="Test",
        story_arc=legacy_arc,
        location=_make_location(),
    )
    field_names = [f.name for f in embed.fields if f.name]
    assert not any("La situation" in n for n in field_names)
    assert not any("Votre appel" in n for n in field_names)
    # Premise still shown in description.
    assert "Legacy premise" in str(embed.description)


@pytest.mark.asyncio
async def test_opening_crawl_embed_no_longer_shows_location_or_first_beat() -> None:
    """Old 'Lieu de départ' / 'Premier chapitre' fields are gone (moved to scene embed)."""
    arc = _make_story_arc()
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=arc,
        location=_make_location(),
        language="fr",
    )
    field_names = [f.name for f in embed.fields if f.name]
    assert not any("Lieu de départ" in n for n in field_names)
    assert not any("Premier chapitre" in n for n in field_names)


@pytest.mark.asyncio
async def test_opening_crawl_embed_fallback() -> None:
    """Without arc, description is 'Votre aventure commence...' and no fields."""
    embed = build_opening_crawl_embed(
        campaign_name="Test Campaign",
        story_arc=None,
        location=None,
    )
    assert embed.description == "Votre aventure commence..."
    assert len(embed.fields) == 0


# ---------------------------------------------------------------------------
# Opening Reframer integration
# ---------------------------------------------------------------------------


def _make_rogue_character():
    from engine.character import (
        AbilityScores,
        CharacterClass,
        Race,
        create_character,
    )
    scores = AbilityScores(STR=10, DEX=15, CON=12, INT=10, WIS=10, CHA=10)
    return create_character("Roub", Race.HUMAN, CharacterClass.ROGUE, scores)


@pytest.mark.asyncio
async def test_reframer_rewrites_arc_and_arrival_hook(
    launcher: CampaignLauncher,
) -> None:
    """When kit + motivation are captured, the reframer rewrites the opening
    fields before launch — premise/situation/call_to_action/party_premise
    on the arc, arrival_hook on the location."""
    from ai.opening_reframer import ReframedOpening

    launcher.characters[PLAYER_A] = _make_rogue_character()
    launcher.character_kits[PLAYER_A] = "Shadow Blade"
    launcher.character_motivations[PLAYER_A] = "Contract"

    reframed = ReframedOpening(
        premise="La cathédrale se dresse dans le brouillard du petit matin.",
        situation="Depuis trois lunes, les portes scellées s'entrouvrent seules.",
        call_to_action="Un notable de la ville basse vous a payé pour fouiller la crypte.",
        arrival_hook="Vous franchissez le Porche à la nuit tombée, la lettre du commanditaire sous la ceinture.",
        party_premise="Une lame de l'ombre payée pour un contrat dans une cathédrale abandonnée.",
    )

    with _patch_externals():
        with patch(
            "ai.opening_reframer.OpeningReframer.reframe",
            return_value=reframed,
        ):
            with patch("ai.client.OllamaClient") as client_cls:
                client_cls.return_value = MagicMock()
                await launcher._launch_campaign()

    assert launcher.story_arc is not None
    assert "payé" in launcher.story_arc.call_to_action
    assert launcher.story_arc.party_premise.startswith("Une lame de l'ombre")
    assert launcher.current_location is not None
    assert "Porche" in launcher.current_location.arrival_hook


@pytest.mark.asyncio
async def test_reframer_skipped_when_kit_missing(
    launcher: CampaignLauncher,
) -> None:
    """Force-launch path (players without kit/motivation) must not crash —
    the reframer is a no-op and the original arc text is preserved."""
    launcher.characters[PLAYER_A] = _make_rogue_character()
    # No kit or motivation — simulates a force-launch on an incomplete player.
    original_call_to_action = launcher.story_arc.call_to_action  # type: ignore[union-attr]

    with _patch_externals():
        with patch("ai.opening_reframer.OpeningReframer.reframe") as reframe_mock:
            await launcher._launch_campaign()
            reframe_mock.assert_not_called()

    assert launcher.story_arc is not None
    assert launcher.story_arc.call_to_action == original_call_to_action


@pytest.mark.asyncio
async def test_reframer_failure_falls_back_to_original(
    launcher: CampaignLauncher,
) -> None:
    """An LLM failure inside the reframer must NOT block the launch —
    the original arc text is used and a warning is logged."""
    launcher.characters[PLAYER_A] = _make_rogue_character()
    launcher.character_kits[PLAYER_A] = "Shadow Blade"
    launcher.character_motivations[PLAYER_A] = "Contract"
    original_call_to_action = launcher.story_arc.call_to_action  # type: ignore[union-attr]

    with _patch_externals():
        with patch(
            "ai.opening_reframer.OpeningReframer.reframe",
            side_effect=RuntimeError("LLM blew up"),
        ):
            with patch("ai.client.OllamaClient") as client_cls:
                client_cls.return_value = MagicMock()
                # Keep retries tight so the test doesn't wait 20s.
                with patch("bot.campaign_launcher.MAX_RETRIES", 0):
                    await launcher._launch_campaign()

    # Launch still completed AND the arc stayed unchanged.
    assert launcher.channel.id in launcher.bot.sessions
    assert launcher.story_arc is not None
    assert launcher.story_arc.call_to_action == original_call_to_action
