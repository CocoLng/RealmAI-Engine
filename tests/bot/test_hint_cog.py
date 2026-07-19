"""Tests for the /hint cog — three progressive hint levels."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.cogs.hint import HintCog


@pytest.fixture
def cog() -> HintCog:
    """HintCog wired to a mock bot."""
    bot = MagicMock()
    return HintCog(bot)


async def test_level1_uses_player_visible_hint(cog: HintCog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Level 1 returns player_visible_hint when available."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock()
    fake_beat.player_visible_hint = "You sense something at the marketplace."
    fake_beat.description = "Long beat description that should NOT be used."
    fake_beat.beat_number = 3
    fake_beat.objectives = [MagicMock(description="...", id="x")]

    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=0, level2_used=False, level3_last_used_turn=None,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    await cog.hint.callback(cog, interaction)

    interaction.response.send_message.assert_called_once()
    args, kwargs = interaction.response.send_message.call_args
    assert kwargs.get("ephemeral") is True
    sent_text = args[0] if args else kwargs.get("content", "")
    assert "marketplace" in sent_text.lower()


async def test_level2_lists_objectives(cog: HintCog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Level 2 lists objective descriptions when L1 already used."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 123

    obj = MagicMock(description="Speak to Kaelen", id="talk_kaelen")
    fake_beat = MagicMock()
    fake_beat.player_visible_hint = "vague"
    fake_beat.description = "..."
    fake_beat.beat_number = 1
    fake_beat.objectives = [obj]

    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    # L1 used once, L2 not yet used → next call is L2
    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=1, level2_used=False, level3_last_used_turn=None,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    await cog.hint.callback(cog, interaction)

    sent_text = interaction.response.send_message.call_args[0][0]
    assert "Kaelen" in sent_text
    fake_repo.set_level2_used.assert_called_once()


async def test_level3_cooldown_blocks(cog: HintCog, monkeypatch: pytest.MonkeyPatch) -> None:
    """Level 3 returns a cooldown message when used within 5 turns."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock(player_visible_hint="vague", description="...", beat_number=1)
    fake_beat.objectives = []
    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"
    # M1: the turn counter lives on campaign — GameSession has no such field.
    fake_session.campaign.interaction_count = 12  # current turn
    fake_session.interaction_count = 0  # stale attr the buggy getattr used to read

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    # L1 used, L2 used, L3 used at turn 10 (cooldown 5 → unavailable until turn 15)
    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=1, level2_used=True, level3_last_used_turn=10,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    await cog.hint.callback(cog, interaction)

    sent_text = interaction.response.send_message.call_args[0][0]
    assert "indisponible" in sent_text.lower() or "cooldown" in sent_text.lower()


async def test_level3_unlocks_when_campaign_cooldown_expired(
    cog: HintCog, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1 regression: level 3 must come back once campaign.interaction_count
    moves past the cooldown. With the bug (counter read on GameSession,
    always 0) level 3 stayed locked forever."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock(
        player_visible_hint="vague", description="...", beat_number=1,
        title="Beat", judge_rubric=None,
    )
    fake_beat.objectives = []
    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"
    fake_session.campaign.interaction_count = 15  # cooldown of 5 expired (used at 10)
    fake_session.interaction_count = 0  # stale attr — must NOT be read
    fake_session.current_location.name = "Forge"

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=1, level2_used=True, level3_last_used_turn=10,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    fake_judge = MagicMock()
    fake_judge.evaluate.return_value = MagicMock(
        suggested_next_action="Parle au forgeron", reasoning="...",
    )
    monkeypatch.setattr(cog, "_build_judge", lambda session: fake_judge)

    await cog.hint.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    sent_text = interaction.followup.send.call_args[0][0]
    assert "forgeron" in sent_text
    # The cooldown stamp must record the CAMPAIGN turn, not the stale 0.
    fake_repo.set_level3_last_used_turn.assert_called_once_with(
        campaign_id="c1", beat_number=1, turn=15,
    )


async def test_level3_judge_runs_off_event_loop(
    cog: HintCog, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 regression: judge.evaluate wraps a blocking httpx POST — it must
    run via asyncio.to_thread, not on the Discord event loop."""
    import threading
    from types import SimpleNamespace

    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock(
        player_visible_hint="vague", description="...", beat_number=1,
        title="Beat", judge_rubric=None,
    )
    fake_beat.objectives = []
    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"
    fake_session.campaign.interaction_count = 20
    fake_session.current_location.name = "Forge"

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=1, level2_used=True, level3_last_used_turn=None,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    recorded: dict[str, int] = {}

    class _RecordingJudge:
        def begin_turn(self, *, turn_id: str) -> None:
            pass

        def evaluate(self, request) -> SimpleNamespace:
            recorded["thread"] = threading.get_ident()
            return SimpleNamespace(
                suggested_next_action="Examine l'autel", reasoning="...",
            )

    monkeypatch.setattr(cog, "_build_judge", lambda session: _RecordingJudge())

    await cog.hint.callback(cog, interaction)

    assert "thread" in recorded
    assert recorded["thread"] != threading.get_ident(), (
        "judge.evaluate must run via asyncio.to_thread"
    )


async def test_hint_closes_db_session(
    cog: HintCog, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2 regression: each /hint invocation opened a DB session via
    db_factory and never closed it — one leaked session per command."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock()
    fake_beat.player_visible_hint = "Un indice vague."
    fake_beat.description = "..."
    fake_beat.beat_number = 1
    fake_beat.objectives = []

    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    # Real _get_repo path: db_factory builds the session the repo wraps.
    db_session = MagicMock()
    db_session.get.return_value = MagicMock(
        level1_uses=0, level2_used=False, level3_last_used_turn=None,
    )
    cog.bot.db_factory = lambda: db_session

    await cog.hint.callback(cog, interaction)

    db_session.close.assert_called_once()


async def test_no_active_session_returns_friendly_error(cog: HintCog, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no session is active, returns a human-readable error."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 999

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: None)

    await cog.hint.callback(cog, interaction)

    sent_text = interaction.response.send_message.call_args[0][0]
    assert "campagne" in sent_text.lower() or "active" in sent_text.lower()


@pytest.mark.parametrize("sentinel", ["judge_timeout", "judge_error"])
async def test_level3_judge_failure_is_clean_and_free(
    cog: HintCog, monkeypatch: pytest.MonkeyPatch, sentinel: str,
) -> None:
    """A failed judge (timeout/error) must NOT leak its internal sentinel to
    Discord, and must NOT consume the 5-turn cooldown — the player got no
    hint. Seen live 2026-07-19: `_judge_timeout_` shown, cooldown burned."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.channel_id = 123

    fake_beat = MagicMock(
        title="Beat", judge_rubric=None, description="...", beat_number=1,
        player_visible_hint="vague",
    )
    fake_beat.objectives = []
    fake_session = MagicMock()
    fake_session.story_arc.beats = [fake_beat] * 5
    fake_session.story_arc.current_beat_index = 0
    fake_session.campaign.id = "c1"
    fake_session.campaign.interaction_count = 20
    fake_session.current_location.name = "Forge"
    monkeypatch.setattr(cog, "_get_session", lambda channel_id: fake_session)

    fake_repo = MagicMock()
    fake_repo.get_or_create.return_value = MagicMock(
        level1_uses=1, level2_used=True, level3_last_used_turn=None,
    )
    monkeypatch.setattr(cog, "_get_repo", lambda: fake_repo)

    fake_judge = MagicMock()
    fake_judge.evaluate.return_value = MagicMock(
        passed=False, confidence=0.0, reasoning=sentinel,
        suggested_next_action=None,
    )
    monkeypatch.setattr(cog, "_build_judge", lambda session: fake_judge)

    await cog.hint.callback(cog, interaction)

    interaction.followup.send.assert_called_once()
    args, kwargs = interaction.followup.send.call_args
    sent_text = args[0] if args else kwargs.get("content", "")
    assert sentinel not in sent_text, "internal sentinel leaked to Discord"
    assert "oracle" in sent_text.lower() or "réessaie" in sent_text.lower()
    fake_repo.set_level3_last_used_turn.assert_not_called()
