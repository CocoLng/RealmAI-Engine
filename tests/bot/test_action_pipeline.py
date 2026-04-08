"""Tests for bot/action_pipeline.py — orchestration of free-text actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ai.models import InterpretedAction, NarrativeResult
from ai.scene_context import SceneContext
from bot.action_pipeline import (
    ActionPipeline,
    ActionPipelineResult,
    AmbiguityResult,
    PipelinePhase,
    UnknownEntityResult,
)
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeInterpreter:
    """Stub interpreter that returns a pre-set InterpretedAction."""

    response: InterpretedAction
    last_call: dict[str, Any] = field(default_factory=dict)
    side_effect: Exception | None = None

    def interpret(
        self,
        player_text: str,
        actor_name: str,
        scene_context: SceneContext,
        language: str = "fr",
    ) -> InterpretedAction:
        self.last_call = {
            "player_text": player_text,
            "actor_name": actor_name,
            "scene_context": scene_context,
            "language": language,
        }
        if self.side_effect is not None:
            raise self.side_effect
        return self.response


@dataclass
class FakeNarrator:
    """Stub narrator returning canned narratives in order."""

    responses: list[NarrativeResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    side_effect: Exception | None = None

    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
    ) -> NarrativeResult:
        self.calls.append(
            {
                "action_result_text": action_result_text,
                "context_prompt": context_prompt,
                "language": language,
                "player_intent": player_intent,
                "outcome_facts": outcome_facts,
            },
        )
        if self.side_effect is not None:
            raise self.side_effect
        if not self.responses:
            return NarrativeResult(narrative="(default)", tone="dramatic")
        return self.responses.pop(0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def cathedral() -> Location:
    return Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée.",
        connections=["Intérieur de la cathédrale", "Ruelle nord"],
        npcs_present=["Père Aldric", "Frère Corin"],
        items_available=["Autel de pierre", "Statue de saint"],
    )


@pytest.fixture()
def aldric() -> NPC:
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    return NPC(
        name="Père Aldric",
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=15,
        max_hp=15,
        ac=12,
        description="Un vieil homme en prière.",
        location_name="Place de la Cathédrale",
    )


@pytest.fixture()
def corin() -> NPC:
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    return NPC(
        name="Frère Corin",
        race=Race.HUMAN,
        char_class=CharacterClass.CLERIC,
        ability_scores=scores,
        hp=10,
        max_hp=10,
        ac=10,
        description="Un jeune novice.",
        location_name="Place de la Cathédrale",
    )


@pytest.fixture()
def hero():
    scores = AbilityScores(STR=14, DEX=10, CON=12, INT=10, WIS=10, CHA=10)
    return create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)


def _make_pipeline(
    interpreter: FakeInterpreter,
    narrator: FakeNarrator,
    location: Location | None,
    npcs: dict[str, NPC],
    actor_name: str = "Aldric",
    language: str = "fr",
    campaign_id: str = "test-camp",
) -> ActionPipeline:
    return ActionPipeline(
        interpreter=interpreter,  # type: ignore[arg-type]
        narrator=narrator,  # type: ignore[arg-type]
        location=location,
        npcs=npcs,
        actor_name=actor_name,
        language=language,
        campaign_id=campaign_id,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_look_returns_pipeline_result(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Aldric",
                raw_input="je regarde",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Tu observes...", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {aldric.name: aldric},
        )

        result = await pipeline.process(player_text="je regarde")

        assert isinstance(result, ActionPipelineResult)
        assert result.narrative == "Tu observes..."
        assert result.tone == "dramatic"
        assert result.interpreted_action.action_type == ActionType.LOOK

    @pytest.mark.asyncio
    async def test_talk_resolves_unique_npc(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Aldric",  # partial match
                raw_input="je parle au prêtre Aldric",
                confidence=0.85,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Le prêtre vous regarde.", tone="tense")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {aldric.name: aldric},
        )

        result = await pipeline.process(player_text="je parle au prêtre Aldric")

        assert isinstance(result, ActionPipelineResult)
        assert result.interpreted_action.target_name == "Père Aldric"
        # Narrator was called with a non-empty action result text
        assert len(narrator.calls) == 1
        assert "Père Aldric" in narrator.calls[0]["action_result_text"]


# ---------------------------------------------------------------------------
# Ambiguity branch
# ---------------------------------------------------------------------------


class TestAmbiguity:
    @pytest.mark.asyncio
    async def test_returns_ambiguity_when_multiple_npcs_match(
        self,
        cathedral: Location,
    ) -> None:
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        marc1 = NPC(
            name="Frère Marc",
            race=Race.HUMAN,
            char_class=CharacterClass.CLERIC,
            ability_scores=scores,
            hp=10,
            max_hp=10,
            ac=10,
            description="Vieux moine.",
            location_name="Place de la Cathédrale",
        )
        marc2 = NPC(
            name="Frère Marc le Sage",
            race=Race.HUMAN,
            char_class=CharacterClass.CLERIC,
            ability_scores=scores,
            hp=10,
            max_hp=10,
            ac=10,
            description="Jeune novice.",
            location_name="Place de la Cathédrale",
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Marc",
                raw_input="je parle à Marc",
                confidence=0.4,
            ),
        )
        narrator = FakeNarrator()
        pipeline = _make_pipeline(
            interp, narrator,
            cathedral,
            {marc1.name: marc1, marc2.name: marc2},
        )

        result = await pipeline.process(player_text="je parle à Marc")

        assert isinstance(result, AmbiguityResult)
        assert result.field_name == "target_name"
        assert result.raw_value == "Marc"
        assert len(result.candidates) == 2
        # Narrator was NOT called — pipeline short-circuits before phase 5
        assert narrator.calls == []


class TestResumeWithResolution:
    @pytest.mark.asyncio
    async def test_resume_continues_with_chosen_entity(
        self,
        cathedral: Location,
    ) -> None:
        scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
        marc1 = NPC(
            name="Frère Marc",
            race=Race.HUMAN,
            char_class=CharacterClass.CLERIC,
            ability_scores=scores,
            hp=10, max_hp=10, ac=10,
            location_name="Place de la Cathédrale",
        )
        marc2 = NPC(
            name="Frère Marc le Sage",
            race=Race.HUMAN,
            char_class=CharacterClass.CLERIC,
            ability_scores=scores,
            hp=10, max_hp=10, ac=10,
            location_name="Place de la Cathédrale",
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Marc",
                raw_input="je parle à Marc",
                confidence=0.4,
            ),
        )
        narrator = FakeNarrator(
            responses=[
                NarrativeResult(narrative="Le sage te regarde.", tone="dramatic"),
            ],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {marc1.name: marc1, marc2.name: marc2},
        )

        ambig = await pipeline.process(player_text="je parle à Marc")
        assert isinstance(ambig, AmbiguityResult)

        result = await pipeline.resume_with_resolution(
            ambig, chosen_entity_id="Frère Marc le Sage",
        )
        assert isinstance(result, ActionPipelineResult)
        assert result.interpreted_action.target_name == "Frère Marc le Sage"
        assert "Frère Marc le Sage" in narrator.calls[0]["action_result_text"]
        # Interpreter is called only once — resume reuses the existing action
        assert interp.last_call.get("player_text") == "je parle à Marc"


# ---------------------------------------------------------------------------
# Unknown entity → in-character refusal
# ---------------------------------------------------------------------------


class TestRefusalGrounding:
    """Lot A — narrator refusal prompts must be grounded in the real scene."""

    @pytest.mark.asyncio
    async def test_narrate_unknown_injects_scene_grounding(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        """The unknown-entity refusal prompt must list the real npcs_present
        and connections, plus the anti-hallucination clause."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="dragon",
                raw_input="je parle au dragon",
                confidence=0.5,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="(refusal)", tone="somber")],
        )
        # NOTE: npcs={} on purpose — the new prompt must source NPCs from
        # location.npcs_present, NOT from self.npcs.
        pipeline = _make_pipeline(interp, narrator, cathedral, npcs={})

        await pipeline.process(player_text="je parle au dragon")

        assert len(narrator.calls) == 1
        prompt = narrator.calls[0]["action_result_text"]
        # The prompt must reference the real npcs_present from the location.
        assert "Père Aldric" in prompt
        assert "Frère Corin" in prompt
        # And the real connections.
        assert "Intérieur de la cathédrale" in prompt
        assert "Ruelle nord" in prompt
        # The anti-hallucination clause must be present.
        assert "N'invente AUCUN" in prompt
        assert "Aldric" in prompt  # actor name
        # The old English hint must be gone.
        assert "Describe their realisation" not in prompt

    @pytest.mark.asyncio
    async def test_narrate_rule_failure_injects_scene_grounding(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        """Rule-failure narration also exposes the real scene context."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.ATTACK,
                actor_name="Aldric",
                target_name="Père Aldric",  # exists in cathedral
                raw_input="j'attaque le prêtre",
                confidence=0.7,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="(refusal)", tone="tense")],
        )
        # combat_state=None on the pipeline — ATTACK then fails validation.
        pipeline = _make_pipeline(
            interp, narrator, cathedral, npcs={aldric.name: aldric},
        )

        result = await pipeline.process(player_text="j'attaque le prêtre")

        # Sanity: the rule-failure path was hit.
        assert isinstance(result, UnknownEntityResult)
        assert result.field_name == "rule"

        assert len(narrator.calls) == 1
        prompt = narrator.calls[0]["action_result_text"]
        # The prompt must mention the real scene.
        assert "Père Aldric" in prompt
        assert "Frère Corin" in prompt
        assert "Intérieur de la cathédrale" in prompt
        assert "N'invente AUCUN" in prompt


class TestUnknownEntity:
    @pytest.mark.asyncio
    async def test_unknown_npc_triggers_in_character_refusal(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Dragon",
                raw_input="je parle au dragon",
                confidence=0.5,
            ),
        )
        # Narrator generates the in-character refusal narrative
        narrator = FakeNarrator(
            responses=[
                NarrativeResult(
                    narrative="Tu scrutes les alentours mais tu ne vois aucun dragon.",
                    tone="somber",
                ),
            ],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {aldric.name: aldric},
        )

        result = await pipeline.process(player_text="je parle au dragon")

        assert isinstance(result, UnknownEntityResult)
        assert result.raw_value == "Dragon"
        assert "dragon" in result.refusal_narrative.lower()
        # The narrator was invoked to generate the refusal
        assert len(narrator.calls) == 1


# ---------------------------------------------------------------------------
# Progress callback observability
# ---------------------------------------------------------------------------


class TestProgressCallback:
    @pytest.mark.asyncio
    async def test_callback_emits_phases_in_order(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Aldric",
                raw_input="je regarde",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="x", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {aldric.name: aldric},
        )

        seen_phases: list[PipelinePhase] = []

        async def cb(phase: PipelinePhase) -> None:
            seen_phases.append(phase)

        await pipeline.process(player_text="je regarde", progress_callback=cb)

        # Must include INTERPRETING and DONE in increasing order
        assert PipelinePhase.INTERPRETING in seen_phases
        assert PipelinePhase.RESOLVING_ENTITIES in seen_phases
        assert PipelinePhase.VALIDATING in seen_phases
        assert PipelinePhase.NARRATING in seen_phases
        assert PipelinePhase.DONE in seen_phases
        assert seen_phases == sorted(seen_phases, key=lambda p: p.value)


# Concurrency serialization is enforced by the action_handler cog via
# GameSession.action_lock — see tests/test_cog_exploration.py.
