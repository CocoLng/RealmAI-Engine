"""Observability tests for CampaignLauncher: Discord feedback deduplication
and GenerationPhase transitions.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.campaign_launcher import CampaignLauncher, GenerationPhase, PlayerProgress
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CAMPAIGN_ID = "obs-test-001"
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
    return Campaign(id=CAMPAIGN_ID, name="Forêt Sombre", player_names=[str(PLAYER_A)])


@pytest.fixture()
def launcher(bot: MagicMock, campaign: Campaign, channel: AsyncMock) -> CampaignLauncher:
    return CampaignLauncher(
        bot=bot,
        campaign=campaign,
        channel=channel,
        player_ids=[PLAYER_A],
    )


@pytest.fixture()
def launcher_two_players(bot: MagicMock, campaign: Campaign, channel: AsyncMock) -> CampaignLauncher:
    c = Campaign(id=CAMPAIGN_ID, name="Forêt Sombre", player_names=[str(PLAYER_A), str(PLAYER_B)])
    return CampaignLauncher(
        bot=bot,
        campaign=c,
        channel=channel,
        player_ids=[PLAYER_A, PLAYER_B],
    )


def _make_location() -> Location:
    return Location(name="Clairière des Ombres", description="Une clairière sinistre.")


# ---------------------------------------------------------------------------
# GenerationPhase initial state
# ---------------------------------------------------------------------------


def test_generation_phase_starts_at_pending(launcher: CampaignLauncher) -> None:
    """GenerationPhase is PENDING before any background task starts."""
    assert launcher._generation_phase == GenerationPhase.PENDING


# ---------------------------------------------------------------------------
# start_background_tasks — Ollama unavailable at startup
# ---------------------------------------------------------------------------


def test_start_background_tasks_notifies_on_ollama_failure(
    launcher: CampaignLauncher,
) -> None:
    """When OllamaClient init fails, a notification task is scheduled and phase is FAILED."""
    from ai.client import OllamaUnavailableError

    # OllamaClient is imported locally inside start_background_tasks → patch at source
    def _close_coro(coro: object) -> None:
        """Prevent 'coroutine never awaited' warning by closing it immediately."""
        if hasattr(coro, "close"):
            coro.close()  # type: ignore[union-attr]

    with (
        patch("ai.client.OllamaClient", side_effect=OllamaUnavailableError("down")),
        patch("asyncio.create_task", side_effect=_close_coro) as mock_create_task,
    ):
        launcher.start_background_tasks()

    assert launcher._generation_failed is True
    assert launcher._generation_phase == GenerationPhase.FAILED
    # A notification task must have been scheduled (not a generation task)
    mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# _check_ready — "players ready, generation still running" message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_players_ready_message_sent_once(launcher: CampaignLauncher) -> None:
    """'Tous les joueurs sont prêts' message is sent exactly once even if _check_ready is called twice."""
    # All players GEAR_DONE, generation NOT done
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE

    await launcher._check_ready()
    await launcher._check_ready()

    assert launcher._notified_players_ready is True
    # One send call for the "players ready" message
    assert launcher.channel.send.call_count == 1
    sent_text = launcher.channel.send.call_args[0][0]
    assert "joueurs" in sent_text.lower()


@pytest.mark.asyncio
async def test_players_ready_message_not_sent_when_generation_done(
    launcher: CampaignLauncher,
) -> None:
    """'Players ready' message NOT sent when generation is also done (launch path, not wait path)."""
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE
    launcher.story_arc = MagicMock()  # only needs to be not None
    launcher.current_location = _make_location()

    # Patch _launch_campaign to avoid DB operations in this test
    with patch.object(launcher, "_launch_campaign", new_callable=AsyncMock):
        await launcher._check_ready()

    assert launcher._notified_players_ready is False


# ---------------------------------------------------------------------------
# _check_ready — "generation done, waiting for players" message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generation_ready_message_sent_once(
    launcher_two_players: CampaignLauncher,
) -> None:
    """'Univers généré' message is sent exactly once even if _check_ready is called twice."""
    launcher = launcher_two_players
    # Generation done, but neither player is GEAR_DONE yet
    launcher.story_arc = MagicMock()  # only needs to be not None
    launcher.current_location = _make_location()

    await launcher._check_ready()
    await launcher._check_ready()

    assert launcher._notified_generation_ready is True
    assert launcher.channel.send.call_count == 1
    sent_text = launcher.channel.send.call_args[0][0]
    assert "univers" in sent_text.lower() or "généré" in sent_text.lower()


@pytest.mark.asyncio
async def test_generation_ready_message_not_sent_when_players_also_done(
    launcher: CampaignLauncher,
) -> None:
    """'Generation ready' message NOT sent when all players are also GEAR_DONE (launch path)."""
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE
    launcher.story_arc = MagicMock()  # only needs to be not None
    launcher.current_location = _make_location()

    with patch.object(launcher, "_launch_campaign", new_callable=AsyncMock):
        await launcher._check_ready()

    assert launcher._notified_generation_ready is False


# ---------------------------------------------------------------------------
# No duplicate messages across multiple _check_ready calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_duplicate_discord_messages_on_repeated_check(
    launcher: CampaignLauncher,
) -> None:
    """Calling _check_ready() many times never sends the same status message twice."""
    # Single-player: mark as GEAR_DONE → all_ready=True, generation NOT done
    launcher.player_progress[PLAYER_A] = PlayerProgress.GEAR_DONE

    for _ in range(5):
        await launcher._check_ready()

    # Only one "players ready" message, no duplicates
    assert launcher.channel.send.call_count == 1


# ---------------------------------------------------------------------------
# _retry_llm_call — ValueError (empty LLM content) is retried
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_llm_call_retries_on_empty_content(launcher: CampaignLauncher) -> None:
    """_retry_llm_call retries when ValueError (empty LLM content) is raised."""
    call_count = 0

    def fn() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("LLM returned empty content (model=qwen3.5:9b, think=True)")
        return "success"

    result = await launcher._retry_llm_call(fn)
    assert result == "success"
    assert call_count == 2


# ---------------------------------------------------------------------------
# Lot A — scene embed posted at launch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_posts_scene_embed_after_narrative(
    launcher: CampaignLauncher,
) -> None:
    """A scene embed is sent right after the opening narrative — players
    must see who/where they are at the moment the campaign starts."""
    launcher.current_location = Location(
        name="Le Village",
        description="Un hameau brumeux.",
        connections=["forêt"],
        npcs_present=["Jeanne, la Villageoise"],
    )

    with (
        patch("bot.campaign_launcher.create_ai_services"),
        patch("db.repositories.LocationRepository"),
        patch("db.repositories.CampaignRepository"),
        patch("db.repositories.StoryArcRepository"),
    ):
        await launcher._launch_campaign()

    embeds_sent = [
        call.kwargs.get("embed")
        for call in launcher.channel.send.call_args_list
        if call.kwargs.get("embed") is not None
    ]
    assert len(embeds_sent) >= 2  # opening narrative + scene embed
    scene_titles = [
        e.title for e in embeds_sent if e.title and "Le Village" in e.title
    ]
    assert scene_titles, "expected a scene embed whose title contains the location name"


@pytest.mark.asyncio
async def test_launch_skips_scene_when_no_current_location(
    launcher: CampaignLauncher,
) -> None:
    """If somehow current_location is None at launch, no scene embed posted."""
    launcher.current_location = None

    with (
        patch("bot.campaign_launcher.create_ai_services"),
        patch("db.repositories.LocationRepository"),
        patch("db.repositories.CampaignRepository"),
        patch("db.repositories.StoryArcRepository"),
    ):
        await launcher._launch_campaign()

    embeds_sent = [
        call.kwargs.get("embed")
        for call in launcher.channel.send.call_args_list
        if call.kwargs.get("embed") is not None
    ]
    # Countdown embed + opening narrative embed (no scene embed).
    assert len(embeds_sent) == 2
