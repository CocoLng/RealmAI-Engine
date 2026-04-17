"""End-to-end regression tests for the TestBridge view-driving path.

Exercises the click_button/select_option/submit_modal handlers against the
real CharacterCreateView + its sub-views, with a mocked Discord channel.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from bot.cogs.test_bridge import TestBridge
from bot.game_session import GameSession
from world.campaign import Campaign


class _FakeMessage:
    """Minimal stand-in for discord.Message.

    Tracks the last edit call so assertions can inspect what the view wrote.
    """

    def __init__(self, msg_id: int) -> None:
        self.id = msg_id
        self.last_edit: dict[str, Any] = {}

    async def edit(self, **kwargs: Any) -> None:
        self.last_edit = kwargs


class _FakeChannel:
    """Channel mock that hands out incrementing Message IDs on send()."""

    def __init__(self, channel_id: int = 12345) -> None:
        self.id = channel_id
        self._next_id = 1000
        self.messages: dict[int, _FakeMessage] = {}
        self.sent: list[dict[str, Any]] = []

    async def send(
        self, content: str | None = None, **kwargs: Any,
    ) -> _FakeMessage:
        self._next_id += 1
        msg = _FakeMessage(self._next_id)
        self.messages[msg.id] = msg
        if content is not None:
            kwargs.setdefault("content", content)
        self.sent.append(kwargs)
        return msg

    async def fetch_message(self, msg_id: int) -> _FakeMessage | None:
        return self.messages.get(msg_id)


@pytest.fixture()
def bridge(db_session: Session) -> TestBridge:
    """TestBridge wired with a mock bot + in-memory db session."""
    mock_bot = MagicMock()
    mock_bot.sessions = {}
    mock_bot.get_session = lambda cid: mock_bot.sessions.get(cid)
    mock_bot.db_factory = MagicMock(return_value=db_session)
    mock_bot.get_cog = MagicMock(return_value=None)
    with patch.dict("os.environ", {"TESTER_BOT_ID": "999999999"}):
        return TestBridge(mock_bot)


@pytest.fixture()
def session_on_channel(
    bridge: TestBridge, db_session: Session,
) -> tuple[_FakeChannel, GameSession]:
    """Register a Campaign + GameSession on a fake channel.

    The campaign is persisted first so PlayerCharacterRepository inserts
    satisfy the FK constraint.
    """
    from db.repositories import CampaignRepository

    channel = _FakeChannel()
    campaign = Campaign(id="test-campaign-view", name="View Flow Test")
    CampaignRepository(db_session).save(campaign)
    db_session.flush()
    session = GameSession(campaign=campaign)
    bridge.bot.sessions[channel.id] = session
    return channel, session


# ---------------------------------------------------------------------------
# Quick-path (backwards compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_character_quick_path(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """quick=1 still creates the character directly without a view."""
    channel, session = session_on_channel
    inter = bridge._make_interaction(MagicMock(id=1), channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(
        inter, {"quick": "1", "name": "Quicky", "race": "Human", "class_": "Fighter"},
    )
    user_id = bridge._get_virtual_player(1).id
    assert user_id in session.characters
    assert session.characters[user_id].name == "Quicky"


@pytest.mark.asyncio
async def test_create_character_legacy_args_shortcut(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """Passing name/race/class_ without quick= also triggers the shortcut."""
    channel, session = session_on_channel
    inter = bridge._make_interaction(MagicMock(id=1), channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(
        inter, {"name": "Legacy", "race": "Elf", "class_": "Wizard"},
    )
    user_id = bridge._get_virtual_player(1).id
    assert user_id in session.characters


# ---------------------------------------------------------------------------
# Full-flow view-driven path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_character_full_flow_registers_view(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """Without shortcut args, _handle_create_character sends a real view."""
    from bot.views.character_create_view import CharacterCreateView

    channel, _ = session_on_channel
    inter = bridge._make_interaction(MagicMock(id=1), channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(inter, {})

    assert len(bridge.active_views) == 1
    msg_id, view = next(iter(bridge.active_views.items()))
    assert isinstance(view, CharacterCreateView)
    assert view.race is None
    assert msg_id in channel.messages


@pytest.mark.asyncio
async def test_full_flow_drives_race_class_alignment(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """Drive race/class/alignment selects — ends with StatAssignmentView."""
    from bot.views.character_create_view import CharacterCreateView
    from bot.views.stat_assignment_view import StatAssignmentView

    channel, _ = session_on_channel
    guild = MagicMock(id=1)
    inter = bridge._make_interaction(guild, channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(inter, {})
    msg_id = next(iter(bridge.active_views))

    # Pick Elf
    await bridge._handle_select_option(
        {"msg": str(msg_id), "value": "Elf"}, 1, guild, channel,  # type: ignore[arg-type]
    )
    view = bridge.active_views[msg_id]
    assert isinstance(view, CharacterCreateView)
    assert view.race.value == "Elf"  # type: ignore[union-attr]
    assert not view.select_class.disabled

    # Pick Wizard
    await bridge._handle_select_option(
        {"msg": str(msg_id), "value": "Wizard"}, 1, guild, channel,  # type: ignore[arg-type]
    )
    assert view.char_class.value == "Wizard"  # type: ignore[union-attr]
    assert not view.select_alignment.disabled

    # Pick Lawful Good — transitions to StatAssignmentView
    await bridge._handle_select_option(
        {"msg": str(msg_id), "value": "Lawful Good"}, 1, guild, channel,  # type: ignore[arg-type]
    )
    assert isinstance(bridge.active_views[msg_id], StatAssignmentView)


@pytest.mark.asyncio
async def test_full_flow_to_character_creation(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """Drive the entire flow from race select through modal submit.

    Verifies that a character is created in the session with the choices
    made via the view (race=Elf, class=Wizard, specific stat & skill picks).
    """
    from engine.character import STANDARD_ARRAY

    channel, session = session_on_channel
    guild = MagicMock(id=1)

    # 1. Start the flow
    inter = bridge._make_interaction(guild, channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(inter, {})
    msg_id = next(iter(bridge.active_views))
    msg_arg = {"msg": str(msg_id)}

    async def select(value: str) -> None:
        await bridge._handle_select_option(
            {**msg_arg, "value": value}, 1, guild, channel,  # type: ignore[arg-type]
        )

    async def click(label: str) -> None:
        await bridge._handle_click_button(
            {**msg_arg, "button": label}, 1, guild, channel,  # type: ignore[arg-type]
        )

    # 2. Race / class / alignment
    await select("Elf")
    await select("Wizard")
    await select("Lawful Good")

    # 3. StatAssignmentView — pick each standard array value in descending
    # order, one per ability. The current view exposes a single value_select.
    for value in sorted(STANDARD_ARRAY, reverse=True):
        await select(str(value))
    await click("Confirmer")

    # 4. SkillSelectionView — pick the required skill count
    from bot.views.skill_selection_view import SkillSelectionView

    skill_view = bridge.active_views[msg_id]
    assert isinstance(skill_view, SkillSelectionView)
    options = [opt.value for opt in skill_view.skill_select.options]
    required = skill_view.required_count
    picks = options[:required]
    await bridge._handle_select_option(
        {**msg_arg, "value": ",".join(picks)}, 1, guild, channel,  # type: ignore[arg-type]
    )
    await click("Confirmer")

    # 5. Modal is now pending — submit it
    assert 1 in bridge.pending_modals
    await bridge._handle_submit_modal(
        {"field_Nom": "Elrond"}, 1, guild, channel,  # type: ignore[arg-type]
    )

    # 6. Character saved to session
    user_id = bridge._get_virtual_player(1).id
    assert user_id in session.characters
    character = session.characters[user_id]
    assert character.name == "Elrond"
    assert character.race.value == "Elf"
    assert character.char_class.value == "Wizard"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_click_button_unknown_message(bridge: TestBridge) -> None:
    """click_button with a msg id not in active_views returns an error message."""
    channel = _FakeChannel()
    channel.send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock(id=1)
    await bridge._handle_click_button(
        {"msg": "999", "button": "X"}, 1, guild, channel,  # type: ignore[arg-type]
    )
    channel.send.assert_called_once()
    assert "aucune view active" in channel.send.call_args[0][0]


@pytest.mark.asyncio
async def test_click_button_unknown_label(
    bridge: TestBridge, session_on_channel: tuple[_FakeChannel, GameSession],
) -> None:
    """click_button with an unknown label returns an error listing available labels."""
    channel, _ = session_on_channel
    # Monkey-patch channel.send after the first real send so we can capture error calls
    guild = MagicMock(id=1)
    inter = bridge._make_interaction(guild, channel, player_idx=1)  # type: ignore[arg-type]
    await bridge._handle_create_character(inter, {})
    msg_id = next(iter(bridge.active_views))
    # CharacterCreateView has no Button at the initial step (only selects)
    await bridge._handle_click_button(
        {"msg": str(msg_id), "button": "NonExistent"}, 1, guild, channel,  # type: ignore[arg-type]
    )
    # The last sent message should be the error
    assert any(
        "introuvable" in str(kw.get("content", ""))
        for kw in channel.sent
    )


@pytest.mark.asyncio
async def test_submit_modal_no_pending(bridge: TestBridge) -> None:
    """submit_modal when no modal is pending for the player returns an error."""
    channel = _FakeChannel()
    channel.send = AsyncMock()  # type: ignore[method-assign]
    guild = MagicMock(id=1)
    await bridge._handle_submit_modal({}, 1, guild, channel)  # type: ignore[arg-type]
    channel.send.assert_called_once()
    assert "aucun modal pending" in channel.send.call_args[0][0]
