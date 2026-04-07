"""Unit tests for bot/world_navigation.change_location."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.world_navigation import LocationChangeError, change_location
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(*, ollama_client=None) -> SimpleNamespace:
    campaign = Campaign(name="Test", current_location="Origin")
    return SimpleNamespace(
        campaign=campaign,
        current_location=Location(name="Origin", description="start"),
        npcs={},
        ollama_client=ollama_client,
        language="fr",
    )


class _StubDBSession:
    def __init__(self) -> None:
        self.committed = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def _factory_returning(db_session: _StubDBSession):
    return lambda: db_session


# ---------------------------------------------------------------------------
# DB-existing path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_location_loads_from_db() -> None:
    session = _make_session()
    dest = Location(name="Donjon", description="dark", npcs_present=["Orc"])
    db_sessions: list[_StubDBSession] = []

    def factory():
        s = _StubDBSession()
        db_sessions.append(s)
        return s

    with (
        patch("bot.world_navigation.LocationRepository") as loc_cls,
        patch("bot.world_navigation.CampaignRepository") as camp_cls,
        patch("bot.world_navigation.NPCRepository") as npc_cls,
        patch("bot.scene_hydration.hydrate_scene") as hydrate,
    ):
        loc_repo = MagicMock()
        loc_repo.get_by_name.return_value = dest
        loc_cls.return_value = loc_repo

        camp_repo = MagicMock()
        camp_cls.return_value = camp_repo

        npc_obj = SimpleNamespace(name="Orc")
        npc_repo = MagicMock()
        npc_repo.list_by_location.return_value = [npc_obj]
        npc_cls.return_value = npc_repo

        result = await change_location(session, "Donjon", db_factory=factory)
        hydrate.assert_called_once()

    assert result is dest
    assert session.current_location is dest
    assert session.campaign.current_location == "Donjon"
    assert session.npcs == {"Orc": npc_obj}
    loc_repo.save.assert_not_called()
    camp_repo.update.assert_called_once_with(session.campaign)
    npc_repo.list_by_location.assert_called_once_with("Donjon", session.campaign.id)
    # Two sessions opened: lookup + persist; second one committed.
    assert len(db_sessions) == 2
    assert db_sessions[1].committed is True
    assert all(s.closed for s in db_sessions)


# ---------------------------------------------------------------------------
# Generation path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_location_generates_when_missing() -> None:
    session = _make_session(ollama_client=MagicMock())
    generated = Location(name="Crypt", description="bones")

    fake_gen = MagicMock()
    fake_gen.generate.return_value = generated

    with (
        patch("bot.world_navigation.LocationRepository") as loc_cls,
        patch("bot.world_navigation.CampaignRepository") as camp_cls,
        patch("bot.world_navigation.NPCRepository") as npc_cls,
        patch("ai.world_generator.WorldGenerator", return_value=fake_gen),
        patch("bot.scene_hydration.hydrate_scene"),
    ):
        loc_repo = MagicMock()
        loc_repo.get_by_name.return_value = None
        loc_cls.return_value = loc_repo

        camp_cls.return_value = MagicMock()

        npc_repo = MagicMock()
        npc_repo.list_by_location.return_value = []
        npc_cls.return_value = npc_repo

        db_sessions: list[_StubDBSession] = []

        def factory():
            s = _StubDBSession()
            db_sessions.append(s)
            return s

        result = await change_location(session, "Crypt", db_factory=factory)

    assert result is generated
    assert session.current_location is generated
    fake_gen.generate.assert_called_once()
    loc_repo.save.assert_called_once_with(generated, session.campaign.id)
    assert db_sessions[-1].committed is True


# ---------------------------------------------------------------------------
# Failure path: no DB entry, no Ollama
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_location_raises_without_ollama() -> None:
    session = _make_session(ollama_client=None)

    with patch("bot.world_navigation.LocationRepository") as loc_cls:
        loc_repo = MagicMock()
        loc_repo.get_by_name.return_value = None
        loc_cls.return_value = loc_repo

        with pytest.raises(LocationChangeError):
            await change_location(
                session, "Nowhere", db_factory=_factory_returning(_StubDBSession()),
            )
