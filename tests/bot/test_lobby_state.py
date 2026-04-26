"""LobbyState lifecycle: add, remove, status, ready predicate."""

import pytest
from bot.lobby_state import LobbyPlayerStatus, LobbyState


def test_lobby_state_starts_empty():
    state = LobbyState(creator_id=42, language="fr")
    assert state.players == {}
    assert not state.has_any_ready()


def test_add_player_records_joined_status():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    assert 100 in state.players
    assert state.players[100].status == LobbyPlayerStatus.JOINED


def test_add_player_idempotent():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.add_player(user_id=100)  # no error, no duplicate
    assert len(state.players) == 1


def test_remove_player():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.remove_player(user_id=100)
    assert 100 not in state.players


def test_remove_unknown_player_is_noop():
    state = LobbyState(creator_id=42, language="fr")
    state.remove_player(user_id=999)  # no error


def test_set_status_transitions():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.set_status(100, LobbyPlayerStatus.CREATING)
    assert state.players[100].status == LobbyPlayerStatus.CREATING
    state.set_status(100, LobbyPlayerStatus.READY)
    assert state.players[100].status == LobbyPlayerStatus.READY


def test_has_any_ready_true_when_one_ready():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.add_player(user_id=200)
    state.set_status(100, LobbyPlayerStatus.READY)
    assert state.has_any_ready()


def test_has_any_ready_false_when_none_ready():
    state = LobbyState(creator_id=42, language="fr")
    state.add_player(user_id=100)
    state.set_status(100, LobbyPlayerStatus.CREATING)
    assert not state.has_any_ready()


def test_max_players_default_six():
    state = LobbyState(creator_id=42, language="fr")
    for i in range(6):
        state.add_player(user_id=i)
    with pytest.raises(ValueError, match="full"):
        state.add_player(user_id=7)
