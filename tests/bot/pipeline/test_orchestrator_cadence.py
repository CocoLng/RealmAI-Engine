"""Regression tests — audit 2026-06-10, chantier D (orchestrateur & cadences).

Covers:
- M1: turn counter must be read from ``session.campaign.interaction_count``
  (GameSession itself has no ``interaction_count`` field → the %6 Story
  Director cadence never fired).
- H2: BeatJudge.evaluate (blocking httpx) must run off the event loop.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from ai.beat_judge import JudgeResponse
from ai.models import InterpretedAction, NarrativeResult
from bot.pipeline.orchestrator import PipelineRunner, get_drift_tracker
from engine.character import AbilityScores, Race
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC
from world.story_arc import (
    BeatObjective,
    GateKind,
    ObjectiveGate,
    ObjectiveKind,
    StoryArc,
    StoryBeat,
)


@dataclass
class _StubInterpreter:
    """Returns a pre-set InterpretedAction."""

    response: InterpretedAction

    def interpret(self, player_text, actor_name, scene_context, language="fr"):
        return self.response


@dataclass
class _StubNarrator:
    """Returns a canned NarrativeResult."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def narrate(
        self,
        action_result_text,
        context_prompt,
        language="fr",
        player_intent="",
        outcome_facts="",
        has_npc_dialogue=False,
        director_note=None,
    ):
        self.calls.append({"director_note": director_note})
        return NarrativeResult(narrative="Vous observez.", tone="somber")


def _make_session(campaign_turn_count: int) -> MagicMock:
    """Minimal session mock mirroring prod: the turn counter lives ONLY on
    ``session.campaign.interaction_count``; GameSession has no such field
    (simulated by the stale 0 the buggy getattr fallback used to return)."""
    session = MagicMock()
    session.story_arc = None
    session.npcs = {}
    session.language = "fr"
    session.current_location = None
    session.combat_state = None
    session.inventory = None
    session.npc_agent = None
    session.npc_generator = None
    session.ollama_client = None
    session.interaction_count = 0  # stale attr — prod GameSession lacks the field
    session.campaign.interaction_count = campaign_turn_count
    session.campaign.id = "camp-cadence"
    return session


def _make_runner(campaign_id: str, session: MagicMock) -> PipelineRunner:
    look = InterpretedAction(
        action_type=ActionType.LOOK,
        actor_name="Hero",
        raw_input="je regarde autour de moi",
        confidence=0.95,
    )
    return PipelineRunner(
        interpreter=_StubInterpreter(response=look),  # type: ignore[arg-type]
        narrator=_StubNarrator(),  # type: ignore[arg-type]
        location=Location(name="Clairière", description="Une clairière calme."),
        npcs={},
        actor_name="Hero",
        campaign_id=campaign_id,
        session=session,
    )


class TestDirectorCadenceM1:
    async def test_director_scheduled_on_sixth_campaign_turn(self) -> None:
        """campaign.interaction_count == 6 → the Story Director is scheduled."""
        campaign_id = "camp-m1-sixth-turn"
        get_drift_tracker().reset(campaign_id)
        session = _make_session(campaign_turn_count=6)
        runner = _make_runner(campaign_id, session)

        scheduled: list[dict[str, Any]] = []
        runner._schedule_story_director = (  # type: ignore[method-assign]
            lambda **kwargs: scheduled.append(kwargs)
        )

        await runner.process("je regarde autour de moi")

        assert len(scheduled) == 1, (
            "Story Director must fire on the 6th campaign turn "
            "(counter read from session.campaign.interaction_count)"
        )

    async def test_director_not_scheduled_off_cadence(self) -> None:
        """campaign.interaction_count == 5 → no director run (no drift/force)."""
        campaign_id = "camp-m1-fifth-turn"
        get_drift_tracker().reset(campaign_id)
        session = _make_session(campaign_turn_count=5)
        runner = _make_runner(campaign_id, session)

        scheduled: list[dict[str, Any]] = []
        runner._schedule_story_director = (  # type: ignore[method-assign]
            lambda **kwargs: scheduled.append(kwargs)
        )

        await runner.process("je regarde autour de moi")

        assert scheduled == []


# ---------------------------------------------------------------------------
# H2 — BeatJudge must run off the event loop
# ---------------------------------------------------------------------------


def _stonewall_judge_scenario(
    campaign_id: str = "camp-h2-judge",
) -> tuple[PipelineRunner, MagicMock]:
    """TALK to the right NPC but with zero reveals → MIN_REVEALS gate fails
    → BeatProgressionEngine returns NEEDS_JUDGE."""
    loc = Location(
        name="Poste de garde",
        description="Un poste ruiné.",
        connections=[],
        npcs_present=["Kaelen"],
    )
    arc = StoryArc(
        campaign_id=campaign_id,
        theme="dungeon",
        premise="A dungeon adventure with many challenges ahead.",
        beats=[
            StoryBeat(
                beat_number=1,
                title="L'Interrogation",
                description="Questionner Kaelen.",
                location_hint="Poste de garde",
                encounter_type="social",
                objectives=[
                    BeatObjective(
                        id="talk_kaelen",
                        kind=ObjectiveKind.TALK,
                        target="Kaelen",
                        description="Faire parler Kaelen",
                        required=True,
                        gate=ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1),
                    ),
                ],
            ),
            *[
                StoryBeat(
                    beat_number=i + 2,
                    title=f"Beat {i + 2}",
                    description=f"Desc {i + 2}",
                    location_hint=f"Area {i + 2}",
                    encounter_type="exploration",
                )
                for i in range(9)
            ],
        ],
        villain_name="X",
        villain_motivation="Y.",
    )
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    npcs = {
        "Kaelen": NPC(
            name="Kaelen", race=Race.HUMAN, ability_scores=scores,
            hp=10, max_hp=10, ac=10,
            description="Un garde blessé.", personality="Méfiant.",
            location_name="Poste de garde",
        ),
    }
    session = _make_session(campaign_turn_count=1)
    session.story_arc = arc
    session.current_location = loc
    session.npcs = npcs

    talk = InterpretedAction(
        action_type=ActionType.TALK,
        actor_name="Hero",
        target_name="Kaelen",
        raw_input="bonjour",
        confidence=0.95,
    )
    runner = PipelineRunner(
        interpreter=_StubInterpreter(response=talk),  # type: ignore[arg-type]
        narrator=_StubNarrator(),  # type: ignore[arg-type]
        location=loc,
        npcs=npcs,
        actor_name="Hero",
        campaign_id=campaign_id,
        session=session,
    )
    return runner, session


class TestJudgeOffEventLoopH2:
    async def test_judge_evaluate_runs_in_worker_thread(self) -> None:
        """H2 regression: judge.evaluate wraps a blocking httpx POST (up to
        120 s) — it must NOT execute on the event-loop thread."""
        campaign_id = "camp-h2-judge"
        get_drift_tracker().reset(campaign_id)
        runner, _session = _stonewall_judge_scenario()

        recorded: dict[str, int] = {}

        class _RecordingJudge:
            def begin_turn(self, *, turn_id: str) -> None:
                pass

            def evaluate(self, request: Any) -> JudgeResponse:
                recorded["thread"] = threading.get_ident()
                return JudgeResponse(
                    passed=True, confidence=0.9, reasoning="creative success",
                )

        runner.beat_judge = _RecordingJudge()

        result = await runner.process("bonjour")

        assert "thread" in recorded, "NEEDS_JUDGE path did not fire the judge"
        assert recorded["thread"] != threading.get_ident(), (
            "judge.evaluate must run via asyncio.to_thread, "
            "not on the event loop"
        )
        # A passing judge (confidence >= 0.7) advances the beat.
        assert getattr(result, "new_beat", None) is not None


# ---------------------------------------------------------------------------
# M11 — cached DirectorNote invalidated on beat advance / location change
# ---------------------------------------------------------------------------


class _PassingJudge:
    def begin_turn(self, *, turn_id: str) -> None:
        pass

    def evaluate(self, request: Any) -> JudgeResponse:
        return JudgeResponse(passed=True, confidence=0.9, reasoning="ok")


def _seed_note(campaign_id: str) -> None:
    from ai.models import DirectorNote
    from ai.story_director import _store_latest_note, reset_latest_notes

    reset_latest_notes()
    _store_latest_note(
        campaign_id,
        DirectorNote(coherence_issues=[], suggested_hooks=[], priority="low"),
    )


class TestDirectorNoteInvalidationM11:
    async def test_beat_completion_invalidates_cached_note(self) -> None:
        """A stale note must not survive a beat advance — its objective and
        atmosphere describe the PREVIOUS beat."""
        from ai.story_director import cached_note_for

        campaign_id = "camp-m11-beat"
        get_drift_tracker().reset(campaign_id)
        runner, _session = _stonewall_judge_scenario(campaign_id)
        runner.beat_judge = _PassingJudge()
        _seed_note(campaign_id)

        result = await runner.process("bonjour")

        assert getattr(result, "new_beat", None) is not None  # beat advanced
        assert cached_note_for(campaign_id) is None

    async def test_location_change_invalidates_cached_note(
        self, monkeypatch: Any,
    ) -> None:
        """A stale note must not survive a location change — required
        mentions / hooks reference the previous scene."""
        from ai.models import PublicEffects
        from ai.story_director import cached_note_for
        from engine.contracts import MechanicsOutcome

        campaign_id = "camp-m11-move"
        get_drift_tracker().reset(campaign_id)
        _seed_note(campaign_id)

        session = _make_session(campaign_turn_count=1)
        session.current_location = Location(
            name="Crypte", description="Une crypte sombre.",
        )

        move = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Hero",
            target_name="Crypte",
            raw_input="je vais à la crypte",
            confidence=0.95,
        )
        runner = PipelineRunner(
            interpreter=_StubInterpreter(response=move),  # type: ignore[arg-type]
            narrator=_StubNarrator(),  # type: ignore[arg-type]
            location=Location(
                name="Clairière",
                description="Une clairière calme.",
                connections=["Crypte"],
            ),
            npcs={},
            actor_name="Hero",
            campaign_id=campaign_id,
            session=session,
        )

        async def fake_resolve(**kwargs: Any) -> MechanicsOutcome:
            return MechanicsOutcome(
                summary="Hero arrives at Crypte.",
                public_effects=PublicEffects(location_change="Crypte"),
            )

        monkeypatch.setattr("bot.pipeline.resolve.resolve_mechanics", fake_resolve)

        await runner.process("je vais à la crypte")

        assert cached_note_for(campaign_id) is None


# ---------------------------------------------------------------------------
# H16 — orchestrator persists objective completions across turns
# ---------------------------------------------------------------------------


class TestObjectivePersistenceH16:
    async def test_m_of_n_beat_completes_across_two_actions(self) -> None:
        """SEARCH on turn 1, EXAMINE on turn 2 — the orchestrator must write
        the turn-1 completion back into the beat so turn 2 reaches the
        M_OF_N threshold (the audit's insatisfiable-beat soft-lock)."""
        from world.story_arc import AdvanceRule

        campaign_id = "camp-h16-mofn"
        get_drift_tracker().reset(campaign_id)

        loc = Location(
            name="Crypte", description="Une crypte sombre.",
            items_available=["autel", "gravures"],
        )
        beat = StoryBeat(
            beat_number=1, title="Le Rituel", description="...",
            location_hint="Crypte", encounter_type="puzzle",
            objectives=[
                BeatObjective(
                    id="search_altar", kind=ObjectiveKind.SEARCH,
                    target="autel", description="Fouiller l'autel",
                ),
                BeatObjective(
                    id="examine_runes", kind=ObjectiveKind.EXAMINE,
                    target="gravures", description="Examiner les gravures",
                ),
            ],
            advance_rule=AdvanceRule.M_OF_N, advance_threshold=2,
        )
        arc = StoryArc(
            campaign_id=campaign_id,
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                beat,
                *[
                    StoryBeat(
                        beat_number=i + 2, title=f"Beat {i + 2}",
                        description=f"Desc {i + 2}",
                        location_hint=f"Area {i + 2}",
                        encounter_type="exploration",
                    )
                    for i in range(9)
                ],
            ],
            villain_name="X",
            villain_motivation="Y.",
        )
        session = _make_session(campaign_turn_count=1)
        session.story_arc = arc
        session.current_location = loc

        def _runner(action: InterpretedAction) -> PipelineRunner:
            return PipelineRunner(
                interpreter=_StubInterpreter(response=action),  # type: ignore[arg-type]
                narrator=_StubNarrator(),  # type: ignore[arg-type]
                location=loc,
                npcs={},
                actor_name="Hero",
                campaign_id=campaign_id,
                session=session,
            )

        search = InterpretedAction(
            action_type=ActionType.SEARCH, actor_name="Hero",
            target_name="autel", raw_input="je fouille l'autel",
            confidence=0.95,
        )
        r1 = await _runner(search).process("je fouille l'autel")
        assert getattr(r1, "new_beat", None) is None  # threshold not reached
        # Turn-1 completion written back into the beat (in-memory arc).
        assert "search_altar" in arc.beats[0].objectives_completed

        session.campaign.interaction_count = 2
        examine = InterpretedAction(
            action_type=ActionType.LOOK, actor_name="Hero",
            target_name="gravures", raw_input="j'examine les gravures",
            confidence=0.95,
        )
        r2 = await _runner(examine).process("j'examine les gravures")
        assert getattr(r2, "new_beat", None) is not None, (
            "second objective completion must reach the M_OF_N threshold"
        )


# ---------------------------------------------------------------------------
# M4 (partial) — ChromaDB indexing in _apply_beat_effects off the event loop
# ---------------------------------------------------------------------------


class TestBeatEffectsIndexingOffLoopM4:
    async def test_indexing_runs_in_worker_thread(self) -> None:
        """semantic_indexer.index_revealed_fact hits ChromaDB (blocking
        I/O) — it must run via asyncio.to_thread, not inline in the
        async pipeline."""
        from types import SimpleNamespace

        from world.story_arc import BeatEffects

        recorded: list[int] = []

        def _index(campaign_id: str, fact: str) -> None:
            recorded.append(threading.get_ident())

        runner = PipelineRunner(
            interpreter=MagicMock(),
            narrator=MagicMock(),
            location=None,
            npcs={},
            actor_name="Tester",
            campaign_id="cmp_m4",
            semantic_indexer=SimpleNamespace(index_revealed_fact=_index),
        )
        effects = BeatEffects(
            narrative_hint="A breach opens.",
            state_flags={"breach_open": True},
        )

        hint = await runner._apply_beat_effects(effects, beat_number=1)

        assert hint == "A breach opens."
        assert len(recorded) == 2  # narrative_hint + one truthy flag
        main_thread = threading.get_ident()
        assert all(t != main_thread for t in recorded), (
            "index_revealed_fact must run via asyncio.to_thread"
        )
