"""Tests for the campaign lobby view (Rejoindre / Quitter / Démarrer)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.lobby_state import LobbyPlayerStatus, LobbyState
from bot.views.lobby_view import LobbyView


def _make_view(host_id=42):
    state = LobbyState(creator_id=host_id, language="fr")
    on_join = AsyncMock()
    on_launch = AsyncMock()
    return LobbyView(
        lobby_state=state, host_id=host_id, language="fr",
        on_join_clicked=on_join, on_launch_clicked=on_launch,
    ), state, on_join, on_launch


@pytest.mark.asyncio
async def test_join_button_calls_on_join_callback():
    view, state, on_join, _ = _make_view()
    interaction = MagicMock()
    interaction.user.id = 100
    interaction.response.send_message = AsyncMock()
    await view.join.callback(interaction)  # type: ignore[arg-type]
    on_join.assert_called_once_with(interaction, view)


@pytest.mark.asyncio
async def test_leave_button_removes_player_and_refreshes():
    view, state, _, _ = _make_view()
    state.add_player(100)
    interaction = MagicMock()
    interaction.user.id = 100
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    await view.leave.callback(interaction)  # type: ignore[arg-type]
    assert 100 not in state.players


@pytest.mark.asyncio
async def test_launch_button_host_only():
    view, state, _, on_launch = _make_view(host_id=42)
    interaction = MagicMock()
    interaction.user.id = 999  # NOT host
    interaction.response.send_message = AsyncMock()
    await view.launch.callback(interaction)  # type: ignore[arg-type]
    on_launch.assert_not_called()
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_launch_button_blocked_when_no_ready_players():
    view, state, _, on_launch = _make_view(host_id=42)
    state.add_player(100)
    state.set_status(100, LobbyPlayerStatus.CREATING)  # joined but not ready
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    await view.launch.callback(interaction)  # type: ignore[arg-type]
    on_launch.assert_not_called()


@pytest.mark.asyncio
async def test_launch_button_fires_when_host_and_ready():
    view, state, _, on_launch = _make_view(host_id=42)
    state.add_player(100)
    state.set_status(100, LobbyPlayerStatus.READY)
    interaction = MagicMock()
    interaction.user.id = 42
    await view.launch.callback(interaction)  # type: ignore[arg-type]
    on_launch.assert_called_once_with(interaction, view)
