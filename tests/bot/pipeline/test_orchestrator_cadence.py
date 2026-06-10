"""Regression tests — audit 2026-06-10, chantier D (orchestrateur & cadences).

Covers:
- M1: turn counter must be read from ``session.campaign.interaction_count``
  (GameSession itself has no ``interaction_count`` field → the %6 Story
  Director cadence never fired).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

from ai.models import InterpretedAction, NarrativeResult
from bot.pipeline.orchestrator import PipelineRunner, get_drift_tracker
from engine.validators import ActionType
from world.location import Location


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
