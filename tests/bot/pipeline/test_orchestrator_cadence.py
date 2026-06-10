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


def _stonewall_judge_scenario() -> tuple[PipelineRunner, MagicMock]:
    """TALK to the right NPC but with zero reveals → MIN_REVEALS gate fails
    → BeatProgressionEngine returns NEEDS_JUDGE."""
    loc = Location(
        name="Poste de garde",
        description="Un poste ruiné.",
        connections=[],
        npcs_present=["Kaelen"],
    )
    arc = StoryArc(
        campaign_id="camp-h2-judge",
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
        campaign_id="camp-h2-judge",
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
