"""Tests for the production memory hook — bot/pipeline/narrate.py.

Covers update_memory_after_turn: recording each turn's exchanges
(player input + narration) into the Layer 2 sliding window from the
action pipeline (audit H9a — the 4-layer memory was never wired in prod).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ai.models import InterpretedAction, NarrativeResult
from bot.action_pipeline import ActionPipeline, ActionPipelineResult
from bot.game_session import GameSession
from bot.pipeline import narrate
from db.database import Base
from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from engine.validators import ActionType
from memory.models import ExchangeRole
from world.campaign import Campaign
from world.location import Location

from tests.bot.test_action_pipeline import FakeInterpreter, FakeNarrator


@pytest.fixture()
def thread_safe_db_factory():
    """In-memory SQLite factory usable across threads (asyncio.to_thread)."""
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


@pytest.fixture()
def game_session(thread_safe_db_factory) -> GameSession:
    campaign = Campaign(id="camp-mem-1", name="Memory Test")
    db = thread_safe_db_factory()
    CampaignRepository(db).save(campaign)
    db.commit()
    db.close()
    return GameSession(campaign=campaign)


class TestUpdateMemoryAfterTurn:
    @pytest.mark.asyncio
    async def test_records_player_and_narrator_exchanges(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="j'examine l'autel",
            narration="La pierre froide révèle des runes anciennes.",
        )

        db = thread_safe_db_factory()
        exchanges = ExchangeRepository(db).get_recent("camp-mem-1", limit=10)
        db.close()
        assert len(exchanges) == 2
        assert exchanges[0].role == ExchangeRole.PLAYER
        assert exchanges[0].content == "j'examine l'autel"
        assert exchanges[1].role == ExchangeRole.NARRATOR
        assert "runes anciennes" in exchanges[1].content
        assert exchanges[1].interaction_number == exchanges[0].interaction_number + 1

    @pytest.mark.asyncio
    async def test_second_turn_continues_numbering(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="tour un",
            narration="narration un",
        )
        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="tour deux",
            narration="narration deux",
        )

        db = thread_safe_db_factory()
        exchanges = ExchangeRepository(db).get_recent("camp-mem-1", limit=10)
        db.close()
        assert [e.interaction_number for e in exchanges] == [1, 2, 3, 4]
        assert exchanges[2].content == "tour deux"

    @pytest.mark.asyncio
    async def test_noop_without_session(self, thread_safe_db_factory) -> None:
        await narrate.update_memory_after_turn(
            session=None,
            db_factory=thread_safe_db_factory,
            player_input="x",
            narration="y",
        )

    @pytest.mark.asyncio
    async def test_noop_without_db_factory(self, game_session: GameSession) -> None:
        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=None,
            player_input="x",
            narration="y",
        )

    @pytest.mark.asyncio
    async def test_db_errors_are_swallowed(self, game_session: GameSession) -> None:
        """Memory recording must never break gameplay."""
        def _broken_factory():
            raise RuntimeError("db unavailable")

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=_broken_factory,
            player_input="x",
            narration="y",
        )


class TestPipelineRecordsExchanges:
    """The orchestrator hook: a full pipeline run records the turn."""

    @pytest.mark.asyncio
    async def test_pipeline_records_exchanges(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        location = Location(
            name="Crypte", description="Une crypte sombre.",
            connections=[], npcs_present=[], items_available=[],
        )
        game_session.current_location = location
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Aldric",
                raw_input="je regarde autour de moi",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(
                narrative="Les ombres dansent sur les murs de la crypte.",
                tone="tense",
            )],
        )
        pipeline = ActionPipeline(
            interpreter=interp,  # type: ignore[arg-type]
            narrator=narrator,  # type: ignore[arg-type]
            location=location,
            npcs={},
            actor_name="Aldric",
            campaign_id="camp-mem-1",
            session=game_session,
            db_factory=thread_safe_db_factory,
        )

        result = await pipeline.process(player_text="je regarde autour de moi")

        assert isinstance(result, ActionPipelineResult)
        db = thread_safe_db_factory()
        exchanges = ExchangeRepository(db).get_recent("camp-mem-1", limit=10)
        db.close()
        assert [e.role for e in exchanges] == [
            ExchangeRole.PLAYER, ExchangeRole.NARRATOR,
        ]
        assert exchanges[0].content == "je regarde autour de moi"
        assert "ombres dansent" in exchanges[1].content
