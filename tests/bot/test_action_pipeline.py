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
from world.story_arc import StoryArc, StoryBeat, CompletionTrigger, BeatEffects


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
        has_npc_dialogue: bool = False,
    ) -> NarrativeResult:
        self.calls.append(
            {
                "action_result_text": action_result_text,
                "context_prompt": context_prompt,
                "language": language,
                "player_intent": player_intent,
                "outcome_facts": outcome_facts,
                "has_npc_dialogue": has_npc_dialogue,
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
    async def test_narrate_unknown_includes_items(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        """The unknown-entity refusal prompt must also list items_available."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="fantôme",
                raw_input="je parle au fantôme",
                confidence=0.5,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="(refusal)", tone="somber")],
        )
        pipeline = _make_pipeline(interp, narrator, cathedral, npcs={})

        await pipeline.process(player_text="je parle au fantôme")

        assert len(narrator.calls) == 1
        prompt = narrator.calls[0]["action_result_text"]
        assert "Autel de pierre" in prompt
        assert "Statue de saint" in prompt
        assert "Objets disponibles" in prompt

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


class TestQuestionAction:
    """QUESTION action type short-circuits the pipeline."""

    @pytest.mark.asyncio
    async def test_question_skips_entity_resolution(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="What do I see?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="You see a cathedral.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("What do I see?")
        assert isinstance(result, ActionPipelineResult)
        assert result.interpreted_action.action_type == ActionType.QUESTION
        assert len(narrator.calls) == 1

    @pytest.mark.asyncio
    async def test_question_returns_is_question_flag(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="Are there NPCs here?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="You see priests.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("Are there NPCs here?")
        assert isinstance(result, ActionPipelineResult)
        assert result.is_question is True

    @pytest.mark.asyncio
    async def test_question_outcome_facts_contain_state(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="What's around me?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Cathedral.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("What's around me?")
        assert isinstance(result, ActionPipelineResult)
        call = narrator.calls[0]
        assert "Place de la Cathédrale" in call["outcome_facts"]
        assert "Autel de pierre" in call["outcome_facts"]


# Concurrency serialization is enforced by the action_handler cog via
# GameSession.action_lock — see tests/test_cog_exploration.py.


# ---------------------------------------------------------------------------
# Beat completion helpers
# ---------------------------------------------------------------------------


def _make_session_with_arc(location, story_arc):
    """Create a minimal mock session for beat advancement tests."""
    from unittest.mock import MagicMock
    session = MagicMock()
    session.current_location = location
    session.story_arc = story_arc
    session.npcs = {}
    session.language = "fr"
    session.combat_state = None
    session.inventory = None
    return session


# ---------------------------------------------------------------------------
# Beat completion
# ---------------------------------------------------------------------------


class TestBeatCompletion:
    """Deterministic beat completion via triggers."""

    @pytest.mark.asyncio
    async def test_llm_fallback_fires_on_creative_solution(self):
        """When deterministic trigger doesn't match but player is creative, LLM fallback fires."""
        loc = Location(
            name="Bone Barrier",
            description="A wall of bones.",
            connections=[],
            items_available=["Le levier de l'Échiquier", "Sac de sable"],
        )
        arc = StoryArc(
            campaign_id="test",
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                StoryBeat(
                    beat_number=i + 1,
                    title=f"Beat {i + 1}",
                    description="Balance the mechanism to open a breach." if i == 0 else f"Desc {i + 1}",
                    location_hint="Bone Barrier" if i == 0 else f"Area {i + 1}",
                    encounter_type="puzzle" if i == 0 else "exploration",
                    completion_trigger=CompletionTrigger(type="interact", target="Le levier de l'Échiquier") if i == 0 else None,
                    on_complete=BeatEffects(
                        unlock_exits=["Inner Court"],
                        state_flags={"breach_open": True},
                        narrative_hint="A breach opens.",
                    ) if i == 0 else BeatEffects(),
                )
                for i in range(10)
            ],
            villain_name="Thaumiel",
            villain_motivation="Purify humanity.",
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.IMPROVISE,
                actor_name="Hero",
                target_name=None,
                raw_input="I use the sand to balance the mechanism",
                improvise_description="Hero uses sand to balance the mechanism",
                confidence=0.8,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Sand balances it.", tone="tense")],
        )
        session = _make_session_with_arc(loc, arc)
        pipeline = _make_pipeline(
            interp, narrator, loc, {},
            actor_name="Hero",
        )
        pipeline.session = session

        from unittest.mock import AsyncMock, patch
        mock_judge = AsyncMock(return_value={"completed": True, "confidence": 0.9})
        with patch.object(pipeline, "_llm_beat_fallback", mock_judge):
            result = await pipeline.process("I use the sand")

        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is not None
        assert result.new_beat.beat_number == 2
        assert "Inner Court" in loc.unlocked_exits

    @pytest.mark.asyncio
    async def test_interact_trigger_completes_beat(self):
        loc = Location(
            name="Bone Barrier",
            description="A wall of bones.",
            connections=[],
            items_available=["Le levier de l'Échiquier"],
        )
        arc = StoryArc(
            campaign_id="test",
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                StoryBeat(
                    beat_number=i + 1,
                    title=f"Beat {i + 1}",
                    description=f"Description {i + 1}",
                    location_hint="Bone Barrier" if i == 0 else f"Area {i + 1}",
                    encounter_type="puzzle" if i == 0 else "exploration",
                    completion_trigger=CompletionTrigger(
                        type="interact",
                        target="Le levier de l'Échiquier",
                    ) if i == 0 else None,
                    on_complete=BeatEffects(
                        unlock_exits=["Inner Court"],
                        state_flags={"breach_open": True},
                        narrative_hint="A breach opens.",
                    ) if i == 0 else BeatEffects(),
                )
                for i in range(10)
            ],
            villain_name="Thaumiel",
            villain_motivation="Purify humanity.",
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.INTERACT,
                actor_name="Hero",
                target_name="Le levier de l'Échiquier",
                raw_input="I pull the lever",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="The lever moves.", tone="tense")],
        )
        session = _make_session_with_arc(loc, arc)
        pipeline = _make_pipeline(
            interp, narrator, loc, {},
            actor_name="Hero",
        )
        pipeline.session = session

        result = await pipeline.process("I pull the lever")
        assert isinstance(result, ActionPipelineResult)
        # Beat should have advanced
        assert result.new_beat is not None
        assert result.new_beat.beat_number == 2
        # Location should have been mutated
        assert "Inner Court" in loc.unlocked_exits
        assert loc.state_flags.get("breach_open") is True
