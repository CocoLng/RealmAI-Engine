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
from world.npc import NPC, NPCDisposition
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
    # Disable agents/generators that would otherwise be MagicMocks and
    # short-circuit the _resolve_talk path down the cheap no-agent branch.
    session.npc_agent = None
    session.npc_generator = None
    return session


@dataclass
class StubNPCAgent:
    """A minimal NPC agent that returns a canned NPCResponse. Used to
    exercise the TALK-quality beat gate without calling a real LLM."""

    revealed: list[str] = field(default_factory=list)
    disposition_delta: int = 0
    dialogue: str = "..."

    def respond(self, npc, player_input, context_prompt, language="fr"):
        from ai.models import NPCResponse
        return NPCResponse(
            dialogue=self.dialogue,
            disposition_change=self.disposition_delta,
            revealed_info=list(self.revealed),
        )


def _kaelen_arc():
    """Shared arc fixture for the Kaelen interrogation beat."""
    loc = Location(
        name="Poste de garde",
        description="Un poste ruiné.",
        connections=[],
        npcs_present=["Kaelen, le Gardien Blessé"],
    )
    arc = StoryArc(
        campaign_id="test",
        theme="dungeon",
        premise="A dungeon adventure with many challenges ahead.",
        beats=[
            StoryBeat(
                beat_number=1,
                title="L'Interrogation du Gardien",
                description="Questionner Kaelen.",
                location_hint="Poste de garde",
                encounter_type="social",
                completion_trigger=CompletionTrigger(
                    type="talk",
                    target="Kaelen, le Gardien Blessé",
                ),
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
    return loc, arc


def _kaelen_npcs() -> dict[str, NPC]:
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    return {
        "Kaelen, le Gardien Blessé": NPC(
            name="Kaelen, le Gardien Blessé",
            race=Race.HUMAN,
            ability_scores=scores,
            hp=10, max_hp=10, ac=10,
            description="Un garde blessé.",
            personality="Méfiant.",
            location_name="Poste de garde",
        ),
        "Kaelen": NPC(
            name="Kaelen",
            race=Race.HUMAN,
            ability_scores=scores,
            hp=10, max_hp=10, ac=10,
            description="Un garde blessé.",
            personality="Méfiant.",
            location_name="Poste de garde",
        ),
    }


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

    @pytest.mark.asyncio
    async def test_talk_trigger_matches_short_vs_long_target(self):
        """Regression — observed 2026-04-11: the beat trigger was
        `talk on "Kaelen, le Gardien Blessé"` and the resolved action
        target was also the canonical long name, but short-vs-long fuzzy
        matching fell below the 0.6 threshold in an earlier bug. The
        substring pass must catch this case now. Also verifies that a
        productive conversation (reveals > 0, disposition stable) is
        enough to advance the beat."""
        loc, arc = _kaelen_arc()
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Hero",
                target_name="Kaelen",  # short form
                raw_input="je parle à Kaelen",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Kaelen répond.", tone="tense")],
        )
        npcs = _kaelen_npcs()
        session = _make_session_with_arc(loc, arc)
        session.npcs = npcs
        # Productive conversation: one reveal, disposition unchanged.
        session.npc_agent = StubNPCAgent(
            revealed=["the lever is behind the altar"],
            disposition_delta=0,
            dialogue="Kaelen parle.",
        )
        pipeline = _make_pipeline(
            interp, narrator, loc, npcs, actor_name="Hero",
        )
        pipeline.session = session

        result = await pipeline.process("je parle à Kaelen")
        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is not None
        assert result.new_beat.beat_number == 2  # advanced via trigger

    @pytest.mark.asyncio
    async def test_talk_beat_blocked_when_npc_reveals_nothing(self):
        """Quality gate — if the NPC stonewalled the player (0 reveals),
        the beat must NOT advance even though the action targeted the
        right NPC. Observed 2026-04-11: the player was pushed to the
        next beat without any information being shared."""
        loc, arc = _kaelen_arc()
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Hero",
                target_name="Kaelen",
                raw_input="bonjour",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Kaelen reste silencieux.", tone="tense")],
        )
        npcs = _kaelen_npcs()
        session = _make_session_with_arc(loc, arc)
        session.npcs = npcs
        # NPC stonewalls: no reveals, neutral disposition shift.
        session.npc_agent = StubNPCAgent(
            revealed=[],
            disposition_delta=0,
            dialogue="...",
        )
        pipeline = _make_pipeline(
            interp, narrator, loc, npcs, actor_name="Hero",
        )
        pipeline.session = session

        result = await pipeline.process("bonjour")
        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is None  # beat did NOT advance

    @pytest.mark.asyncio
    async def test_talk_beat_blocked_when_disposition_regressed(self):
        """Quality gate — even if the NPC technically shared something,
        a negative disposition shift (NPC got more hostile) means the
        conversation went poorly and the beat must NOT advance. This
        matches the exact failure in the 2026-04-11 log where Kaelen
        produced disposition_change=-1 revealed=1 and the beat
        advanced anyway."""
        loc, arc = _kaelen_arc()
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Hero",
                target_name="Kaelen",
                raw_input="qu'est-ce que tu caches ?",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Kaelen recule.", tone="tense")],
        )
        npcs = _kaelen_npcs()
        session = _make_session_with_arc(loc, arc)
        session.npcs = npcs
        session.npc_agent = StubNPCAgent(
            revealed=["vague hint about the ruins"],
            disposition_delta=-1,  # NPC got more hostile
            dialogue="Recule...",
        )
        pipeline = _make_pipeline(
            interp, narrator, loc, npcs, actor_name="Hero",
        )
        pipeline.session = session

        result = await pipeline.process("qu'est-ce que tu caches ?")
        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is None  # beat did NOT advance

    @pytest.mark.asyncio
    async def test_standard_action_does_not_trigger_llm_fallback(self):
        """Regression — the LLM creative-completion fallback must NOT fire
        for standard (non-IMPROVISE) actions, even if the beat has a
        trigger that failed to match. Observed 2026-04-11: saying hi to
        an NPC advanced the interrogation beat at confidence 0.95 via
        an over-permissive LLM fallback."""
        loc = Location(
            name="Place",
            description="Une place.",
            connections=[],
            npcs_present=["Garde Principal"],
        )
        arc = StoryArc(
            campaign_id="test",
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                StoryBeat(
                    beat_number=1,
                    title="Interroger le garde",
                    description="Extraire des informations du garde.",
                    location_hint="Place",
                    encounter_type="social",
                    completion_trigger=CompletionTrigger(
                        type="interact",  # <-- strict: requires INTERACT
                        target="Levier caché",
                    ),
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
        # Player does a TALK action — totally unrelated to the INTERACT
        # trigger. The fallback MUST not fire for TALK.
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Hero",
                target_name="Garde Principal",
                raw_input="bonjour garde",
                talk_topic="bonjour",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Le garde grogne.", tone="tense")],
        )
        npcs = {
            "Garde Principal": NPC(
                name="Garde Principal",
                race=Race.HUMAN,
                ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
                hp=10, max_hp=10, ac=10,
                location_name="Place",
            ),
        }
        session = _make_session_with_arc(loc, arc)
        session.npcs = npcs
        pipeline = _make_pipeline(
            interp, narrator, loc, npcs,
            actor_name="Hero",
        )
        pipeline.session = session

        # Spy on the LLM fallback — it must NEVER be called for TALK.
        from unittest.mock import AsyncMock, patch
        spy_judge = AsyncMock(return_value={"completed": True, "confidence": 1.0})
        with patch.object(pipeline, "_llm_beat_fallback", spy_judge):
            result = await pipeline.process("bonjour garde")

        spy_judge.assert_not_called()
        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is None  # beat did NOT advance


# ---------------------------------------------------------------------------
# Task 00 — Phase 0 bugfix: villain/combat-beat NPCs must not be trivially killed
# ---------------------------------------------------------------------------


def _weak_npc(name: str, disposition: NPCDisposition = NPCDisposition.NEUTRAL) -> NPC:
    """Build a commoner-style NPC (the shape scene_hydration produces today).

    HP/AC/disposition mirror bot/scene_hydration.py: hp=max_hp=4, ac=10,
    NEUTRAL — which is precisely what trivially_defeatable accepts.
    """
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    return NPC(
        name=name,
        race=Race.HUMAN,
        ability_scores=scores,
        hp=4,
        max_hp=4,
        ac=10,
        disposition=disposition,
        description="(hydrated placeholder)",
        location_name="Antre du méchant",
    )


def _arc_with_villain(
    villain_name: str,
    current_beat_encounter: str = "social",
    current_beat_npcs: list[str] | None = None,
) -> StoryArc:
    """Build a StoryArc with a named villain and a configurable first beat."""
    beats = [
        StoryBeat(
            beat_number=1,
            title="Première épreuve",
            description="La scène actuelle — type paramétrable par le test.",
            location_hint="Antre du méchant",
            npc_names=current_beat_npcs or [],
            encounter_type=current_beat_encounter,  # type: ignore[arg-type]
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
    ]
    return StoryArc(
        campaign_id="test-villain",
        theme="dungeon",
        premise="A dungeon adventure with a clearly named villain.",
        beats=beats,
        villain_name=villain_name,
        villain_motivation="Dominer le royaume.",
    )


class TestTrivialResolveGuards:
    """Phase 0 — Task 00: _should_trivial_resolve must refuse story-critical NPCs.

    Covers the regression observed in the Mageta campaign where `(Attack)
    j'attaque vellus` one-shot the villain because scene_hydration had
    placed him in the scene with commoner-style stats (hp=4, ac=10,
    NEUTRAL) — all three of which pass the existing trivial-resolve filter.
    """

    def test_trivial_resolve_blocked_for_villain_by_name(
        self,
        cathedral: Location,
    ) -> None:
        """An NPC whose name matches session.story_arc.villain_name is
        never trivially resolvable, even with weak stats on a social beat."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        villain = _weak_npc("Vellus le Mentisseur")
        arc = _arc_with_villain(
            villain_name="Vellus le Mentisseur",
            current_beat_encounter="social",
            current_beat_npcs=[],  # not in beat list — only the name match matters
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {villain.name: villain},
            actor_name="Hero",
        )
        pipeline.session = _make_session_with_arc(cathedral, arc)

        assert pipeline._should_trivial_resolve(villain) is False

    def test_trivial_resolve_blocked_for_combat_beat_npc(
        self,
        cathedral: Location,
    ) -> None:
        """Any NPC listed in the current beat's npc_names with
        encounter_type=='combat' or 'boss' is also protected, regardless
        of name. Covers the case where a minion in a combat beat was
        hydrated with weak stats."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        minion = _weak_npc("Spadassin encapuchonné")
        arc = _arc_with_villain(
            villain_name="Quelqu'un d'autre",
            current_beat_encounter="combat",
            current_beat_npcs=["Spadassin encapuchonné"],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {minion.name: minion},
            actor_name="Hero",
        )
        pipeline.session = _make_session_with_arc(cathedral, arc)

        assert pipeline._should_trivial_resolve(minion) is False

    def test_trivial_resolve_blocked_for_boss_beat_npc(
        self,
        cathedral: Location,
    ) -> None:
        """encounter_type == 'boss' triggers the same protection as 'combat'."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        boss_minion = _weak_npc("Garde du corps")
        arc = _arc_with_villain(
            villain_name="Un autre",
            current_beat_encounter="boss",
            current_beat_npcs=["Garde du corps"],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {boss_minion.name: boss_minion},
            actor_name="Hero",
        )
        pipeline.session = _make_session_with_arc(cathedral, arc)

        assert pipeline._should_trivial_resolve(boss_minion) is False

    def test_trivial_resolve_allowed_for_neutral_commoner_in_social_beat(
        self,
        cathedral: Location,
    ) -> None:
        """Non-regression: a weak NEUTRAL commoner who isn't the villain
        and isn't named in a combat beat must still be trivially resolvable."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        commoner = _weak_npc("Vieille mendiante")
        arc = _arc_with_villain(
            villain_name="Un Méchant",
            current_beat_encounter="social",
            current_beat_npcs=["Quelqu'un d'autre"],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {commoner.name: commoner},
            actor_name="Hero",
        )
        pipeline.session = _make_session_with_arc(cathedral, arc)

        assert pipeline._should_trivial_resolve(commoner) is True

    def test_trivial_resolve_unchanged_without_session(
        self,
        cathedral: Location,
    ) -> None:
        """Pipelines constructed without a session (legacy tests, simple
        callers) must still reach the original disposition/stats checks."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        commoner = _weak_npc("Commoner sans histoire")
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {commoner.name: commoner},
            actor_name="Hero",
        )
        # Explicitly no session set — must not crash, must defer to
        # disposition + is_trivially_defeatable and let the commoner die.
        assert pipeline.session is None
        assert pipeline._should_trivial_resolve(commoner) is True

    def test_trivial_resolve_blocked_via_full_validate(
        self,
        cathedral: Location,
    ) -> None:
        """Integration: calling _validate with an ATTACK against the
        villain must NOT call _trivial_kill. Since the villain has weak stats
        (max_hp=4, ac=10, NEUTRAL) they are not combat-worthy per
        _is_combat_worthy, so detect_combat_trigger returns None and the
        action falls to the no-combat error path. The key invariant is that
        _trivial_kill is never reached for the villain."""
        from unittest.mock import patch

        villain = _weak_npc("Vellus le Mentisseur")
        location = Location(
            name="Antre du méchant",
            description="Un repaire sombre.",
            connections=[],
            npcs_present=["Vellus le Mentisseur"],
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        arc = _arc_with_villain(
            villain_name="Vellus le Mentisseur",
            current_beat_encounter="social",
        )
        pipeline = _make_pipeline(
            interp, narrator, location, {villain.name: villain},
            actor_name="Hero",
        )
        pipeline.session = _make_session_with_arc(location, arc)

        attack_action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="Hero",
            target_name="Vellus le Mentisseur",
            raw_input="j'attaque Vellus",
            weapon_name="Longsword",
            confidence=0.95,
        )

        # Spy on _trivial_kill — it must NEVER be called on the villain.
        with patch.object(pipeline, "_trivial_kill") as trivial_spy:
            result = pipeline._validate(attack_action)
            trivial_spy.assert_not_called()

        # The villain is protected from trivial kill. Since they are also not
        # combat-worthy (weak stats, NEUTRAL), detect_combat_trigger returns
        # None and no full combat is bootstrapped either — the action is
        # simply rejected with a "needs active combat" error.
        assert not result.is_valid


# ---------------------------------------------------------------------------
# Task 01 — Phase 0 bugfix: MOVE rejected while a combat is active
# ---------------------------------------------------------------------------


class TestExplorationBlockedInCombat:
    """Phase 0 — Task 01: ActionPipeline._validate must forward
    self.combat_state to validate_exploration_action so that MOVE/TALK/etc.
    are refused during an active combat."""

    def test_pipeline_autoconverts_move_to_flee_when_combat_active(
        self,
        cathedral: Location,
    ) -> None:
        """Task 31: MOVE in active combat is auto-converted to FLEE instead of
        being rejected. The validation succeeds (FLEE is legal for an able
        combatant) and _pending_flee_destination captures the target zone."""
        from engine.combat import (
            CombatSide,
            CombatState,
            Combatant as EngineCombatant,
        )
        from engine.inventory import (
            DamageType,
            EquipmentSlot,
            Inventory,
            Weapon,
            WeaponCategory,
        )

        # Build a minimal active CombatState with the hero inside.
        scores = AbilityScores(STR=14, DEX=10, CON=12, INT=10, WIS=10, CHA=10)
        hero_char = create_character(
            "Hero", Race.HUMAN, CharacterClass.FIGHTER, scores,
        )
        longsword = Weapon(
            name="Longsword",
            damage_dice="1d8",
            damage_type=DamageType.SLASHING,
            weapon_category=WeaponCategory.MARTIAL_MELEE,
            weight=3.0,
        )
        hero_combatant = EngineCombatant(
            name="Hero",
            side=CombatSide.PLAYER,
            character=hero_char,
            inventory=Inventory(
                items=[],
                equipped={EquipmentSlot.MAIN_HAND: longsword},
                gold=0,
            ),
        )
        # A dummy enemy — combat needs >= 2 combatants.
        goblin_scores = AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8)
        goblin_char = create_character(
            "Goblin", Race.HALFLING, CharacterClass.ROGUE, goblin_scores,
        )
        goblin_combatant = EngineCombatant(
            name="Goblin",
            side=CombatSide.ENEMY,
            character=goblin_char,
            inventory=Inventory(items=[], equipped={}, gold=0),
        )
        active_state = CombatState(
            combatants=[hero_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )

        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {},
            actor_name="Hero",
        )
        pipeline.combat_state = active_state

        move_action = InterpretedAction(
            action_type=ActionType.MOVE,
            actor_name="Hero",
            target_name="Ruelle nord",
            raw_input="je vais dans la ruelle",
            confidence=0.9,
        )
        result = pipeline._validate(move_action)

        # MOVE is auto-converted to FLEE — should be valid for an able combatant.
        assert result.is_valid is True
        # The destination was captured for the flee resolver.
        assert pipeline._pending_flee_destination == "Ruelle nord"

    def test_pipeline_allows_look_when_combat_active(
        self,
        cathedral: Location,
    ) -> None:
        """Non-regression: LOOK is still allowed off-turn in active combat."""
        from engine.combat import (
            CombatSide,
            CombatState,
            Combatant as EngineCombatant,
        )
        from engine.inventory import Inventory

        scores = AbilityScores(STR=14, DEX=10, CON=12, INT=10, WIS=10, CHA=10)
        hero_char = create_character(
            "Hero", Race.HUMAN, CharacterClass.FIGHTER, scores,
        )
        hero_combatant = EngineCombatant(
            name="Hero",
            side=CombatSide.PLAYER,
            character=hero_char,
            inventory=Inventory(items=[], equipped={}, gold=0),
        )
        goblin_scores = AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8)
        goblin_char = create_character(
            "Goblin", Race.HALFLING, CharacterClass.ROGUE, goblin_scores,
        )
        goblin_combatant = EngineCombatant(
            name="Goblin",
            side=CombatSide.ENEMY,
            character=goblin_char,
            inventory=Inventory(items=[], equipped={}, gold=0),
        )
        active_state = CombatState(
            combatants=[hero_combatant, goblin_combatant],
            round_number=1,
            current_turn_index=0,
        )

        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.LOOK,
                actor_name="Hero",
                raw_input="placeholder",
                confidence=1.0,
            ),
        )
        narrator = FakeNarrator()
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {},
            actor_name="Hero",
        )
        pipeline.combat_state = active_state

        look_action = InterpretedAction(
            action_type=ActionType.LOOK,
            actor_name="Hero",
            raw_input="je regarde",
            confidence=0.95,
        )
        result = pipeline._validate(look_action)
        assert result.is_valid is True


# ---------------------------------------------------------------------------
# Task 31 — Combat dispatch
# ---------------------------------------------------------------------------


def _make_combat_state_with_hero(hero_name: str = "Héros") -> "tuple":
    """Helper: build a minimal active CombatState with hero + goblin using create_character."""
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import Inventory

    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = create_character(hero_name, Race.HUMAN, CharacterClass.FIGHTER, scores)
    hero = Combatant(name=hero_name, side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin_scores = AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8)
    goblin_char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE, goblin_scores)
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0)
    return state, char


def test_pipeline_autoconverts_move_to_flee_in_active_combat() -> None:
    """MOVE in active combat → _pending_flee_destination is set to the target zone."""
    from unittest.mock import MagicMock
    from ai.models import InterpretedAction

    state, _ = _make_combat_state_with_hero("Héros")
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.MOVE, actor_name="Héros",
        target_name="forêt", raw_input="je fuis vers la forêt",
    )
    pipeline._validate(action)
    assert pipeline._pending_flee_destination == "forêt"


def test_pipeline_stores_flee_destination() -> None:
    """_pending_flee_destination stores the original MOVE target_name."""
    from unittest.mock import MagicMock
    from ai.models import InterpretedAction

    state, _ = _make_combat_state_with_hero("Héros")
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.MOVE, actor_name="Héros",
        target_name="village", raw_input="vers le village",
    )
    pipeline._validate(action)
    assert pipeline._pending_flee_destination == "village"


def test_pipeline_dispatches_to_combat_validator_when_active() -> None:
    """When combat is active, an ATTACK action goes through the combat validator."""
    from unittest.mock import MagicMock
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import Inventory, EquipmentSlot, Weapon, WeaponCategory, DamageType
    from ai.models import InterpretedAction

    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = create_character("Héros", Race.HUMAN, CharacterClass.FIGHTER, scores)
    sword = Weapon(
        name="Longsword",
        damage_dice="1d8", damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
    )
    inv = Inventory()
    inv.equipped[EquipmentSlot.MAIN_HAND] = sword

    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=inv)
    goblin_scores = AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8)
    goblin_char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE, goblin_scores)
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0)

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.ATTACK, actor_name="Héros",
        target_name="Goblin", weapon_name="Longsword", raw_input="j'attaque",
    )
    result = pipeline._validate(action)
    assert result.is_valid


def test_pipeline_dispatches_to_exploration_validator_when_inactive() -> None:
    """When no combat, a LOOK action goes through validate_exploration_action."""
    from unittest.mock import MagicMock
    from ai.models import InterpretedAction

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=None,
    )
    action = InterpretedAction(
        action_type=ActionType.LOOK, actor_name="Héros", raw_input="je regarde",
    )
    result = pipeline._validate(action)
    assert result.is_valid


def test_pipeline_exploration_rejected_in_combat_except_info_actions() -> None:
    """TALK is rejected in active combat; LOOK is allowed."""
    from unittest.mock import MagicMock
    from ai.models import InterpretedAction

    state, _ = _make_combat_state_with_hero("Héros")
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )

    talk = InterpretedAction(
        action_type=ActionType.TALK, actor_name="Héros",
        target_name="Goblin", raw_input="je parle",
    )
    look = InterpretedAction(
        action_type=ActionType.LOOK, actor_name="Héros", raw_input="je regarde",
    )
    assert not pipeline._validate(talk).is_valid
    assert pipeline._validate(look).is_valid


def test_pipeline_detects_attack_trigger_and_bootstraps() -> None:
    """ATTACK on combat-worthy NPC bootstraps combat and stores pending embed."""
    from unittest.mock import MagicMock, patch
    from engine.combat import CombatState, Combatant, CombatSide
    from engine.combat_trigger import CombatTrigger, CombatTriggerKind, InitiativeSide
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import Inventory

    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = create_character("Héros", Race.HUMAN, CharacterClass.FIGHTER, scores)
    goblin_char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE,
                                   AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8))
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    bootstrapped_state = CombatState(combatants=[hero, goblin], current_turn_index=0, is_active=True)

    fake_trigger = CombatTrigger(
        kind=CombatTriggerKind.PLAYER_ATTACK,
        aggressor_name="Héros",
        enemy_names=["Goblin"],
        surprise_side=InitiativeSide.BOTH_READY,
        narrative_hint="Héros attaque Goblin.",
    )

    session = MagicMock()
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros",
        combat_state=None, session=session,
    )
    action = InterpretedAction(
        action_type=ActionType.ATTACK, actor_name="Héros",
        target_name="Goblin", raw_input="j'attaque le goblin",
    )

    with (
        patch("bot.action_pipeline.detect_combat_trigger", return_value=fake_trigger),
        patch("bot.action_pipeline.enter_combat", return_value=bootstrapped_state),
        patch("bot.action_pipeline.start_combat", return_value=bootstrapped_state),
    ):
        pipeline._validate(action)

    assert pipeline.combat_state is not None
    assert pipeline.combat_state.is_active
    assert pipeline._pending_combat_start_embed is not None
    assert pipeline._pending_combat_start_embed[1] is fake_trigger


def test_pipeline_detects_lethal_intent_and_bootstraps() -> None:
    """IMPROVISE with is_lethal_intent=True on combat-worthy NPC bootstraps combat."""
    from unittest.mock import MagicMock, patch
    from engine.combat import CombatState, Combatant, CombatSide
    from engine.combat_trigger import CombatTrigger, CombatTriggerKind, InitiativeSide
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import Inventory

    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = create_character("Héros", Race.HUMAN, CharacterClass.FIGHTER, scores)
    goblin_char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE,
                                   AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8))
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    bootstrapped_state = CombatState(combatants=[hero, goblin], current_turn_index=0, is_active=True)

    fake_trigger = CombatTrigger(
        kind=CombatTriggerKind.LETHAL_INTENT,
        aggressor_name="Héros",
        enemy_names=["Goblin"],
        surprise_side=InitiativeSide.PLAYERS,
        narrative_hint="Héros dégaine contre Goblin.",
    )

    session = MagicMock()
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros",
        combat_state=None, session=session,
    )
    # Simulate Task 40 flag: lethal intent on an IMPROVISE action
    action = InterpretedAction(
        action_type=ActionType.IMPROVISE, actor_name="Héros",
        target_name="Goblin", raw_input="je lui enfonce ma lame",
    )
    object.__setattr__(action, "is_lethal_intent", True)  # forward-compatibility shim (Task 40)

    with (
        patch("bot.action_pipeline.detect_combat_trigger", return_value=fake_trigger),
        patch("bot.action_pipeline.enter_combat", return_value=bootstrapped_state),
        patch("bot.action_pipeline.start_combat", return_value=bootstrapped_state),
    ):
        pipeline._validate(action)

    assert pipeline.combat_state is not None
    assert pipeline.combat_state.is_active
    assert pipeline._pending_combat_start_embed is not None


def test_pipeline_no_bootstrap_on_neutral_action() -> None:
    """A LOOK action with no session never triggers detect_combat_trigger."""
    from unittest.mock import MagicMock

    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros",
        combat_state=None, session=None,  # no session — detect_combat_trigger skipped
    )
    action = InterpretedAction(
        action_type=ActionType.LOOK, actor_name="Héros", raw_input="je regarde autour",
    )
    result = pipeline._validate(action)

    assert result.is_valid
    assert pipeline.combat_state is None
    assert pipeline._pending_combat_start_embed is None


def test_pipeline_trivial_kill_still_works_for_commoner() -> None:
    """ATTACK on a neutral commoner (low HP/AC, no stat block) resolves trivially."""
    from unittest.mock import MagicMock

    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    commoner = NPC(
        name="Paysan",
        race=Race.HUMAN,
        ability_scores=scores,
        disposition=NPCDisposition.NEUTRAL,
        hp=4,
        max_hp=4,
        ac=10,
        stat_block=None,
    )
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={"Paysan": commoner}, actor_name="Héros",
        combat_state=None, session=None,
    )
    action = InterpretedAction(
        action_type=ActionType.ATTACK, actor_name="Héros",
        target_name="Paysan", raw_input="j'attaque le paysan",
    )
    result = pipeline._validate(action)

    assert result.is_valid
    assert pipeline.combat_state is None  # trivial kill, no full combat


def test_pipeline_trivial_kill_blocked_for_villain() -> None:
    """ATTACK on a HOSTILE NPC without an active session returns is_valid=False."""
    from unittest.mock import MagicMock

    # HOSTILE disposition → _should_trivial_resolve returns False
    # No session → detect_combat_trigger not called → falls to trivial kill check → blocked
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    boss = NPC(
        name="Bandit Chef",
        race=Race.HUMAN,
        ability_scores=scores,
        disposition=NPCDisposition.HOSTILE,
        hp=4,
        max_hp=4,
        ac=10,
        stat_block=None,
    )
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={"Bandit Chef": boss}, actor_name="Héros",
        combat_state=None, session=None,
    )
    action = InterpretedAction(
        action_type=ActionType.ATTACK, actor_name="Héros",
        target_name="Bandit Chef", raw_input="j'attaque le bandit",
    )
    result = pipeline._validate(action)

    assert not result.is_valid  # hostile NPC → not trivially defeatable → needs full combat
    assert pipeline.combat_state is None
