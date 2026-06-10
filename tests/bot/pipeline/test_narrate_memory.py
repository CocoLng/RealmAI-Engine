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


class TestMemoryContextCache:
    """update_memory_after_turn refreshes the cached memory prefix that
    assemble_context prepends to the scene snapshot (audit H9b)."""

    @pytest.mark.asyncio
    async def test_hook_caches_memory_prefix_on_session(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="je fouille le coffre",
            narration="Le coffre grince et révèle une carte jaunie.",
        )

        assert game_session.memory_context is not None
        assert "[RECENT NARRATIVE]" in game_session.memory_context
        assert "carte jaunie" in game_session.memory_context

    @pytest.mark.asyncio
    async def test_assemble_context_prefixes_cached_memory(
        self, game_session: GameSession,
    ) -> None:
        game_session.memory_context = (
            "[RECENT NARRATIVE]\nNarrator: Le coffre grince."
        )
        game_session.current_location = Location(
            name="Crypte", description="Une crypte sombre.",
        )
        action = InterpretedAction(
            action_type=ActionType.LOOK, actor_name="Aldric",
            raw_input="je regarde", confidence=0.9,
        )
        context = narrate.assemble_context(
            action,
            actor_name="Aldric",
            location=game_session.current_location,
            npcs=None,
            session=game_session,
            combat_state=None,
            inventory=None,
            campaign_id="camp-mem-1",
        )
        assert context.startswith("[RECENT NARRATIVE]")
        assert "Le coffre grince." in context
        assert "Crypte" in context
        # The memory block comes BEFORE the scene snapshot
        assert context.index("[RECENT NARRATIVE]") < context.index("Crypte")

    @pytest.mark.asyncio
    async def test_assemble_context_without_cache_is_unchanged(
        self, game_session: GameSession,
    ) -> None:
        game_session.current_location = Location(
            name="Crypte", description="Une crypte sombre.",
        )
        action = InterpretedAction(
            action_type=ActionType.LOOK, actor_name="Aldric",
            raw_input="je regarde", confidence=0.9,
        )
        context = narrate.assemble_context(
            action,
            actor_name="Aldric",
            location=game_session.current_location,
            npcs=None,
            session=game_session,
            combat_state=None,
            inventory=None,
            campaign_id="camp-mem-1",
        )
        assert "[RECENT NARRATIVE]" not in context
        assert "Crypte" in context

    @pytest.mark.asyncio
    async def test_second_turn_narrator_sees_first_turn_narration(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        """End-to-end continuity: turn 2's narrator context includes
        turn 1's narration — the narrator is no longer amnesic."""
        location = Location(name="Crypte", description="Une crypte sombre.")
        game_session.current_location = location
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK, actor_name="Aldric",
                raw_input="je regarde", confidence=0.95,
            ),
        )
        narrator = FakeNarrator(responses=[
            NarrativeResult(
                narrative="Une statue de marbre noir pleure des larmes de sang.",
                tone="tense",
            ),
            NarrativeResult(narrative="Le silence retombe.", tone="somber"),
        ])

        def _make() -> ActionPipeline:
            return ActionPipeline(
                interpreter=interp,  # type: ignore[arg-type]
                narrator=narrator,  # type: ignore[arg-type]
                location=location,
                npcs={},
                actor_name="Aldric",
                campaign_id="camp-mem-1",
                session=game_session,
                db_factory=thread_safe_db_factory,
            )

        await _make().process(player_text="je regarde")
        await _make().process(player_text="je regarde encore")

        assert len(narrator.calls) == 2
        second_context = narrator.calls[1]["context_prompt"]
        assert "larmes de sang" in second_context


def _minimal_arc(campaign_id: str = "camp-mem-1"):
    from world.story_arc import StoryArc, StoryBeat

    beats = [
        StoryBeat(
            beat_number=i + 1, title=f"Beat {i + 1}",
            description="desc", location_hint="here",
            encounter_type="social",
        )
        for i in range(8)
    ]
    return StoryArc(
        campaign_id=campaign_id, theme="t",
        premise="A premise long enough.", beats=beats,
        villain_name="Nezznar", villain_motivation="m",
    )


def _make_npc(name: str, *, alive: bool):
    from engine.character import AbilityScores, Race
    from world.npc import NPC

    npc = NPC(
        name=name, race=Race.HUMAN,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=10 if alive else 0, max_hp=10, ac=10,
    )
    if not alive:
        npc.kill()
    return npc


class TestLockedFactsFlow:
    """H17 — NPC deaths become locked facts, injected with IDs into the
    narrator context, and enforced by a deterministic post-narration
    check with a single retry."""

    @pytest.mark.asyncio
    async def test_hook_registers_dead_npcs_as_locked_facts(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        from memory import narration_guard

        narration_guard.reset("camp-mem-1")
        game_session.story_arc = _minimal_arc()
        game_session.npcs = {
            "Grim": _make_npc("Grim", alive=False),
            "Mira": _make_npc("Mira", alive=True),
        }

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="je regarde",
            narration="Le silence règne.",
        )

        fact_ids = [f.id for f in game_session.story_arc.locked_facts]
        assert "npc_dead:Grim" in fact_ids
        assert all("Mira" not in fid for fid in fact_ids)
        # The guard registry now knows Grim is dead
        violations = narration_guard.find_dead_npc_violations(
            "camp-mem-1", narrative="Grim sourit.", npcs_mentioned=[],
        )
        assert violations == ["Grim"]
        narration_guard.reset("camp-mem-1")

    @pytest.mark.asyncio
    async def test_hook_does_not_duplicate_facts(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        from memory import narration_guard

        narration_guard.reset("camp-mem-1")
        game_session.story_arc = _minimal_arc()
        game_session.npcs = {"Grim": _make_npc("Grim", alive=False)}

        for _ in range(2):
            await narrate.update_memory_after_turn(
                session=game_session,
                db_factory=thread_safe_db_factory,
                player_input="encore",
                narration="Toujours rien.",
            )

        fact_ids = [f.id for f in game_session.story_arc.locked_facts]
        assert fact_ids.count("npc_dead:Grim") == 1
        narration_guard.reset("camp-mem-1")

    @pytest.mark.asyncio
    async def test_assemble_context_injects_locked_facts_with_ids(
        self, game_session: GameSession,
    ) -> None:
        from world.story_arc import LockedFact

        arc = _minimal_arc()
        game_session.story_arc = arc.model_copy(update={"locked_facts": [
            LockedFact(id="npc_dead:Grim", text="Grim est mort."),
        ]})
        game_session.current_location = Location(
            name="Crypte", description="Une crypte sombre.",
        )
        action = InterpretedAction(
            action_type=ActionType.LOOK, actor_name="Aldric",
            raw_input="je regarde", confidence=0.9,
        )
        context = narrate.assemble_context(
            action,
            actor_name="Aldric",
            location=game_session.current_location,
            npcs=None,
            session=game_session,
            combat_state=None,
            inventory=None,
            campaign_id="camp-mem-1",
        )
        assert "[LOCKED FACTS]" in context
        assert "npc_dead:Grim" in context
        assert "Grim est mort." in context

    @pytest.mark.asyncio
    async def test_call_narrator_retries_on_dead_npc_violation(self) -> None:
        from ai.models import MechanicsOutcome
        from memory import narration_guard

        narration_guard.reset("camp-guard-retry")
        narration_guard.set_dead_npcs("camp-guard-retry", ["Grim"])
        narrator = FakeNarrator(responses=[
            NarrativeResult(
                narrative="Grim vous accueille avec un sourire chaleureux.",
                tone="humorous", npcs_mentioned=["Grim"],
            ),
            NarrativeResult(
                narrative="Le cadavre de Grim gît toujours derrière le comptoir.",
                tone="somber",
            ),
        ])

        result = await narrate.call_narrator(
            narrator=narrator,  # type: ignore[arg-type]
            outcome=MechanicsOutcome(summary="Le joueur entre dans la taverne."),
            context_prompt="## Location\nTaverne",
            language="fr",
            campaign_id="camp-guard-retry",
        )

        assert len(narrator.calls) == 2
        assert "MORT" in narrator.calls[1]["action_result_text"]
        assert "Grim" in narrator.calls[1]["action_result_text"]
        assert "cadavre" in result.narrative
        narration_guard.reset("camp-guard-retry")

    @pytest.mark.asyncio
    async def test_call_narrator_single_call_when_clean(self) -> None:
        from ai.models import MechanicsOutcome
        from memory import narration_guard

        narration_guard.reset("camp-guard-clean")
        narration_guard.set_dead_npcs("camp-guard-clean", ["Grim"])
        narrator = FakeNarrator(responses=[
            NarrativeResult(narrative="La taverne est vide.", tone="somber"),
        ])

        result = await narrate.call_narrator(
            narrator=narrator,  # type: ignore[arg-type]
            outcome=MechanicsOutcome(summary="Le joueur entre."),
            context_prompt="## Location\nTaverne",
            language="fr",
            campaign_id="camp-guard-clean",
        )

        assert len(narrator.calls) == 1
        assert result.narrative == "La taverne est vide."
        narration_guard.reset("camp-guard-clean")

    @pytest.mark.asyncio
    async def test_retry_result_accepted_even_if_still_violating(self) -> None:
        """One retry only — no loops, the second result is final."""
        from ai.models import MechanicsOutcome
        from memory import narration_guard

        narration_guard.reset("camp-guard-loop")
        narration_guard.set_dead_npcs("camp-guard-loop", ["Grim"])
        narrator = FakeNarrator(responses=[
            NarrativeResult(narrative="Grim vous parle.", tone="tense"),
            NarrativeResult(narrative="Grim continue de parler.", tone="tense"),
        ])

        result = await narrate.call_narrator(
            narrator=narrator,  # type: ignore[arg-type]
            outcome=MechanicsOutcome(summary="Le joueur écoute."),
            context_prompt="## Location\nTaverne",
            language="fr",
            campaign_id="camp-guard-loop",
        )

        assert len(narrator.calls) == 2
        assert result.narrative == "Grim continue de parler."
        narration_guard.reset("camp-guard-loop")


class TestRagReadPath:
    """The hook queries semantic memory with the turn's text and the
    cached context surfaces the relevant lore (audit H9d)."""

    @pytest.mark.asyncio
    async def test_hook_surfaces_relevant_lore_in_cached_context(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        import chromadb
        from chromadb.config import Settings
        from memory.models import SemanticDocument, SemanticDocumentType
        from memory.semantic import SemanticMemory

        client = chromadb.EphemeralClient(settings=Settings(allow_reset=True))
        client.reset()
        semantic = SemanticMemory(client=client)
        semantic.add_document(SemanticDocument(
            campaign_id="camp-mem-1",
            doc_type=SemanticDocumentType.WORLD_LORE,
            content="La Forge des Sortilèges est dirigée par Nezznar l'Araignée Noire.",
        ))
        game_session.semantic_memory = semantic

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="je cherche l'entrée de la forge",
            narration="Vous longez les parois sombres de la mine.",
        )

        assert game_session.memory_context is not None
        assert "[RELEVANT LORE]" in game_session.memory_context
        assert "Nezznar" in game_session.memory_context


class TestSummarizationCadence:
    """update_memory_after_turn schedules background summarization once
    enough exchanges have left the window (audit H9c), without blocking
    the turn, and purges the summarized exchanges."""

    def _seed(self, factory, campaign_id: str, count: int) -> None:
        from db.repositories.exchange_repo import ExchangeRepository
        from memory.models import NarrativeExchange

        db = factory()
        repo = ExchangeRepository(db)
        for i in range(1, count + 1):
            repo.save(NarrativeExchange(
                campaign_id=campaign_id, role=ExchangeRole.NARRATOR,
                content=f"Vieux souvenir numéro {i}", interaction_number=i,
            ))
        db.commit()
        db.close()

    @pytest.mark.asyncio
    async def test_summarization_scheduled_and_purges(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        from unittest.mock import MagicMock
        game_session.ollama_client = MagicMock()
        game_session.ollama_client.chat_json.return_value = {
            "summary": "Les héros ont traversé les marais.",
        }
        # 30 already there; the hook records 2 more → 32 = window(12) + interval(20)
        self._seed(thread_safe_db_factory, "camp-mem-1", 30)

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="on continue",
            narration="La route s'étire devant vous.",
        )

        task = game_session.memory_summarize_task
        assert task is not None
        await task

        from db.repositories.summary_repo import SummaryRepository
        from db.repositories.exchange_repo import ExchangeRepository
        db = thread_safe_db_factory()
        summaries = SummaryRepository(db).list_by_campaign("camp-mem-1")
        remaining = ExchangeRepository(db).get_recent("camp-mem-1", limit=100)
        db.close()
        assert len(summaries) == 1
        assert "marais" in summaries[0].summary_text
        assert summaries[0].end_interaction == 20
        assert len(remaining) == 12

    @pytest.mark.asyncio
    async def test_no_summarization_below_cadence(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        from unittest.mock import MagicMock
        game_session.ollama_client = MagicMock()
        self._seed(thread_safe_db_factory, "camp-mem-1", 10)

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="on continue",
            narration="Rien de neuf.",
        )

        assert game_session.memory_summarize_task is None

    @pytest.mark.asyncio
    async def test_no_summarization_without_client(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        game_session.ollama_client = None
        self._seed(thread_safe_db_factory, "camp-mem-1", 40)

        await narrate.update_memory_after_turn(
            session=game_session,
            db_factory=thread_safe_db_factory,
            player_input="on continue",
            narration="Rien.",
        )

        assert game_session.memory_summarize_task is None

    @pytest.mark.asyncio
    async def test_inflight_task_not_duplicated(
        self, thread_safe_db_factory, game_session: GameSession,
    ) -> None:
        import asyncio
        from unittest.mock import MagicMock
        game_session.ollama_client = MagicMock()
        game_session.ollama_client.chat_json.return_value = {"summary": "S."}
        self._seed(thread_safe_db_factory, "camp-mem-1", 40)

        sentinel: asyncio.Task = asyncio.ensure_future(asyncio.sleep(30))
        game_session.memory_summarize_task = sentinel
        try:
            await narrate.update_memory_after_turn(
                session=game_session,
                db_factory=thread_safe_db_factory,
                player_input="on continue",
                narration="Rien.",
            )
            assert game_session.memory_summarize_task is sentinel
        finally:
            sentinel.cancel()


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
