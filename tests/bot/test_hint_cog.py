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
    fake_session.interaction_count = 12  # current turn

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


async def test_no_active_session_returns_friendly_error(cog: HintCog, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no session is active, returns a human-readable error."""
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.channel_id = 999

    monkeypatch.setattr(cog, "_get_session", lambda channel_id: None)

    await cog.hint.callback(cog, interaction)

    sent_text = interaction.response.send_message.call_args[0][0]
    assert "campagne" in sent_text.lower() or "active" in sent_text.lower()
