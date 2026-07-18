"""Unit tests for bot/world_navigation.change_location and create_exit_stubs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bot.world_navigation import (
    LocationChangeError,
    change_location,
    create_exit_stubs,
)
from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
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
# Event-loop hygiene (M4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_location_db_work_runs_off_event_loop() -> None:
    """M4 — synchronous SQLAlchemy (lookup + persist) must use to_thread."""
    import threading

    session = _make_session()
    dest = Location(name="Donjon", description="dark")
    factory_threads: list[bool] = []

    def factory():
        factory_threads.append(
            threading.current_thread() is threading.main_thread(),
        )
        return _StubDBSession()

    with (
        patch("bot.world_navigation.LocationRepository") as loc_cls,
        patch("bot.world_navigation.CampaignRepository") as camp_cls,
        patch("bot.world_navigation.NPCRepository") as npc_cls,
        patch("bot.scene_hydration.hydrate_scene"),
    ):
        loc_repo = MagicMock()
        loc_repo.get_by_name.return_value = dest
        loc_cls.return_value = loc_repo
        camp_cls.return_value = MagicMock()
        npc_repo = MagicMock()
        npc_repo.list_by_location.return_value = []
        npc_cls.return_value = npc_repo

        await change_location(session, "Donjon", db_factory=factory)

    assert factory_threads  # both DB sessions were opened...
    assert all(on_main is False for on_main in factory_threads)


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
    # New flow: freshly-generated location is persisted via upsert() so the
    # stubbing path (see create_exit_stubs) can share the same code for
    # both first-visit and stub-hydration cases.
    loc_repo.upsert.assert_any_call(generated, session.campaign.id)
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


# ---------------------------------------------------------------------------
# create_exit_stubs — real DB tests
# ---------------------------------------------------------------------------


class TestCreateExitStubs:
    """Verify the pre-instantiation helper against a real in-memory DB."""

    def _setup_campaign(self, db_session, campaign_id: str = "cmp-1") -> str:
        CampaignRepository(db_session).save(
            Campaign(id=campaign_id, name="Test"),
        )
        db_session.commit()
        return campaign_id

    def test_creates_stubs_for_new_connections(self, db_session) -> None:
        campaign_id = self._setup_campaign(db_session)
        repo = LocationRepository(db_session)

        created = create_exit_stubs(
            repo,
            ["Couloir", "Salle des échos"],
            parent_name="Entrée",
            campaign_id=campaign_id,
        )
        db_session.commit()

        assert created == 2
        couloir = repo.get_by_name("Couloir", campaign_id)
        assert couloir is not None
        assert couloir.generated is False
        assert couloir.connections == ["Entrée"]  # back-link

        echos = repo.get_by_name("Salle des échos", campaign_id)
        assert echos is not None
        assert echos.generated is False
        assert echos.connections == ["Entrée"]

    def test_is_idempotent(self, db_session) -> None:
        """Calling the helper twice with the same connections is a no-op."""
        campaign_id = self._setup_campaign(db_session)
        repo = LocationRepository(db_session)

        create_exit_stubs(
            repo, ["Couloir"], parent_name="Entrée", campaign_id=campaign_id,
        )
        db_session.commit()
        created_second = create_exit_stubs(
            repo, ["Couloir"], parent_name="Entrée", campaign_id=campaign_id,
        )
        db_session.commit()

        assert created_second == 0
        assert len(repo.list_by_campaign(campaign_id)) == 1

    def test_adds_back_link_to_existing_row(self, db_session) -> None:
        """If a connection points at a row already in the DB but without
        the parent as a back-link, the helper adds the back-link in place.
        This is what keeps the graph bidirectional across generations."""
        campaign_id = self._setup_campaign(db_session)
        repo = LocationRepository(db_session)

        # Pre-existing fully-generated location with its own connections
        # that do NOT mention the parent we're about to link from.
        existing = Location(
            name="Ancienne place",
            description="Déjà connue.",
            connections=["Forêt"],  # no back-link to "Nouveau hameau"
            generated=True,
        )
        repo.save(existing, campaign_id)
        db_session.commit()

        create_exit_stubs(
            repo,
            ["Ancienne place"],
            parent_name="Nouveau hameau",
            campaign_id=campaign_id,
        )
        db_session.commit()

        updated = repo.get_by_name("Ancienne place", campaign_id)
        assert updated is not None
        assert "Nouveau hameau" in updated.connections
        assert "Forêt" in updated.connections  # original kept
        assert updated.generated is True  # still fully-generated

    def test_skips_self_reference(self, db_session) -> None:
        """A location listing itself as a connection should never create
        a stub — that would be a no-op cycle."""
        campaign_id = self._setup_campaign(db_session)
        repo = LocationRepository(db_session)

        created = create_exit_stubs(
            repo,
            ["Entrée", "Couloir"],
            parent_name="Entrée",
            campaign_id=campaign_id,
        )
        db_session.commit()

        assert created == 1
        assert repo.get_by_name("Couloir", campaign_id) is not None

    def test_empty_connections_is_noop(self, db_session) -> None:
        campaign_id = self._setup_campaign(db_session)
        repo = LocationRepository(db_session)
        assert create_exit_stubs(
            repo, [], parent_name="X", campaign_id=campaign_id,
        ) == 0


# ---------------------------------------------------------------------------
# Stub hydration flow — change_location finds a stub and fully generates it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_location_hydrates_stub_preserving_back_link() -> None:
    """When change_location finds a stub in the DB, it re-invokes the
    WorldGenerator with the stub's existing connections as
    ``required_connections`` so the back-link to the parent survives."""
    session = _make_session(ollama_client=MagicMock())

    # Stub sitting in the "DB": same name as our destination, generated=False,
    # back-link to "Origin" already recorded.
    stub = Location(
        name="Couloir",
        description="",
        connections=["Origin"],
        generated=False,
    )
    # What the LLM will produce when asked to hydrate the stub.
    hydrated = Location(
        name="Couloir",
        description="A long dark hallway.",
        connections=["Origin", "Salle du trône"],
        exit_aliases={"Salle du trône": ["trône", "salle"]},
        generated=True,
    )

    fake_gen = MagicMock()
    fake_gen.generate.return_value = hydrated

    with (
        patch("bot.world_navigation.LocationRepository") as loc_cls,
        patch("bot.world_navigation.CampaignRepository") as camp_cls,
        patch("bot.world_navigation.NPCRepository") as npc_cls,
        patch("ai.world_generator.WorldGenerator", return_value=fake_gen),
        patch("bot.scene_hydration.hydrate_scene"),
    ):
        loc_repo = MagicMock()
        loc_repo.get_by_name.return_value = stub
        loc_cls.return_value = loc_repo

        camp_cls.return_value = MagicMock()
        npc_repo = MagicMock()
        npc_repo.list_by_location.return_value = []
        npc_cls.return_value = npc_repo

        db_session = _StubDBSession()
        result = await change_location(
            session, "Couloir", db_factory=_factory_returning(db_session),
        )

    # The generator was asked to preserve the stub's connections.
    call_kwargs = fake_gen.generate.call_args.kwargs
    assert "required_connections" in call_kwargs
    assert "Origin" in (call_kwargs["required_connections"] or [])
    # The hydrated location was upserted (not save) so the stub row is
    # updated in place rather than duplicated.
    loc_repo.upsert.assert_any_call(hydrated, session.campaign.id)
    # The result is the hydrated location.
    assert result is hydrated
    assert session.current_location is hydrated
