"""Tests for the TestBridge cog — command parsing, dispatch, game_state."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from bot.cogs.test_bridge import TestBridge, ChannelTestInteraction, _VirtualMember


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def bot(db_session: Session) -> MagicMock:
    """Mock RealmBot."""
    mock_bot = MagicMock()
    mock_bot.sessions = {}
    mock_bot.get_session = lambda cid: mock_bot.sessions.get(cid)
    mock_bot.db_factory = MagicMock(return_value=db_session)
    mock_bot.get_cog = MagicMock(return_value=None)
    return mock_bot


@pytest.fixture()
def bridge(bot: MagicMock) -> TestBridge:
    """TestBridge wired to the mocked bot."""
    with patch.dict("os.environ", {"TESTER_BOT_ID": "999999999"}):
        return TestBridge(bot)


# ---------------------------------------------------------------------------
# _parse tests
# ---------------------------------------------------------------------------


def test_parse_simple_command(bridge: TestBridge) -> None:
    """Parse a simple command with no args."""
    cmd, args, player = bridge._parse("!test look")
    assert cmd == "look"
    assert args == {}
    assert player == 1


def test_parse_command_with_args(bridge: TestBridge) -> None:
    """Parse command with key=value arguments."""
    cmd, args, player = bridge._parse("!test attack target=Gobelin")
    assert cmd == "attack"
    assert args == {"target": "Gobelin"}
    assert player == 1


def test_parse_command_with_player(bridge: TestBridge) -> None:
    """Parse command with explicit player index."""
    cmd, args, player = bridge._parse("!test player=2 attack target=Rat")
    assert cmd == "attack"
    assert args == {"target": "Rat"}
    assert player == 2


def test_parse_command_with_multiple_args(bridge: TestBridge) -> None:
    """Parse command with multiple arguments."""
    cmd, args, player = bridge._parse(
        "!test start_campaign theme=Donjon players=2",
    )
    assert cmd == "start_campaign"
    assert args == {"theme": "Donjon", "players": "2"}
    assert player == 1


def test_parse_game_state(bridge: TestBridge) -> None:
    """Parse the game_state command."""
    cmd, args, player = bridge._parse("!test game_state")
    assert cmd == "game_state"
    assert args == {}


# ---------------------------------------------------------------------------
# Virtual player tests
# ---------------------------------------------------------------------------


def test_get_virtual_player_creates_member(bridge: TestBridge) -> None:
    """Virtual player is created on first access."""
    player = bridge._get_virtual_player(1)
    assert isinstance(player, _VirtualMember)
    assert player.id == 200_000_001
    assert player.name == "TestPlayer1"


def test_get_virtual_player_is_stable(bridge: TestBridge) -> None:
    """Same index returns the same virtual player."""
    p1 = bridge._get_virtual_player(1)
    p2 = bridge._get_virtual_player(1)
    assert p1 is p2


def test_different_players_have_different_ids(bridge: TestBridge) -> None:
    """Different player indices get different IDs."""
    p1 = bridge._get_virtual_player(1)
    p2 = bridge._get_virtual_player(2)
    assert p1.id != p2.id


# ---------------------------------------------------------------------------
# ChannelTestInteraction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_interaction_response_posts_to_channel() -> None:
    """ChannelTestInteraction.response.send_message posts to channel."""
    channel = AsyncMock()
    channel.id = 12345
    bot = MagicMock()
    guild = MagicMock()
    guild.id = 99999
    user = _VirtualMember(id=1, name="Test", display_name="Test")
    inter = ChannelTestInteraction(bot, guild, channel, user)

    await inter.response.send_message("hello", embed=None)
    channel.send.assert_called_once_with(content="hello")


@pytest.mark.asyncio
async def test_channel_interaction_followup_posts_to_channel() -> None:
    """ChannelTestInteraction.followup.send posts to channel."""
    channel = AsyncMock()
    channel.id = 12345
    bot = MagicMock()
    guild = MagicMock()
    guild.id = 99999
    user = _VirtualMember(id=1, name="Test", display_name="Test")
    inter = ChannelTestInteraction(bot, guild, channel, user)

    await inter.followup.send("followup msg")
    channel.send.assert_called_once_with(content="followup msg")


@pytest.mark.asyncio
async def test_channel_interaction_defer_is_noop() -> None:
    """Defer does nothing but marks responded."""
    channel = AsyncMock()
    channel.id = 12345
    bot = MagicMock()
    guild = MagicMock()
    guild.id = 99999
    user = _VirtualMember(id=1, name="Test", display_name="Test")
    inter = ChannelTestInteraction(bot, guild, channel, user)

    await inter.response.defer()
    assert inter.response.is_done()
    channel.send.assert_not_called()


# ---------------------------------------------------------------------------
# game_state serialization tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_game_state_no_session(bridge: TestBridge) -> None:
    """game_state with no active session returns error JSON."""
    channel = AsyncMock()
    channel.id = 12345

    await bridge._handle_game_state(channel)
    channel.send.assert_called_once()
    msg = channel.send.call_args[0][0]
    assert "No active session" in msg


@pytest.mark.asyncio
async def test_game_state_with_session(bridge: TestBridge) -> None:
    """game_state with active session returns serialized state."""
    from bot.game_session import GameSession
    from world.campaign import Campaign

    campaign = Campaign(id="test-1", name="Test Campaign")
    session = GameSession(campaign=campaign)
    bridge.bot.sessions[12345] = session

    channel = AsyncMock()
    channel.id = 12345

    await bridge._handle_game_state(channel)
    channel.send.assert_called_once()
    msg = channel.send.call_args[0][0]

    # Extract JSON from code block
    assert msg.startswith("```json\n")
    json_str = msg[8:-4]
    data = json.loads(json_str)
    assert data["campaign"]["name"] == "Test Campaign"
    assert data["combat_active"] is False
    assert data["characters"] == []


# ---------------------------------------------------------------------------
# Legacy exploration commands (ExplorationCog was deleted in 5681a6b)
# ---------------------------------------------------------------------------


def test_exploration_text_renders_each_legacy_command() -> None:
    """look/move/search/talk become free text the interpreter can classify."""
    from bot.cogs.test_bridge import _exploration_text

    assert _exploration_text("move", {"direction": "nord"}) == "je vais vers nord"
    assert _exploration_text("search", {"target": "le coffre"}) == "je fouille le coffre"
    assert _exploration_text("talk", {"npc": "Grim"}) == "je parle à Grim"
    assert "observe" in _exploration_text("look", {})


def test_exploration_text_tolerates_missing_args() -> None:
    """A bare command must still produce usable text, never a dangling verb."""
    from bot.cogs.test_bridge import _exploration_text

    assert _exploration_text("search", {}) == "je fouille les lieux"
    assert _exploration_text("talk", {}) == "je parle aux gens ici"


@pytest.mark.parametrize("command", ["look", "move", "search", "talk"])
async def test_exploration_commands_route_to_narrate(
    bridge: TestBridge, command: str,
) -> None:
    """They must reach the action pipeline, not a cog lookup that can never resolve.

    ExplorationCog no longer exists: `get_cog("ExplorationCog")` returned
    None and the branch fell through silently, so an MCP-driven test got
    no reply and no error.
    """
    guild, channel = MagicMock(), MagicMock()
    channel.id = 42
    bridge._handle_narrate = AsyncMock()

    await bridge._dispatch(command, {}, 1, guild, channel)

    bridge._handle_narrate.assert_awaited_once()
    text = bridge._handle_narrate.await_args.args[1]["text"]
    assert text, "exploration command produced empty free text"
