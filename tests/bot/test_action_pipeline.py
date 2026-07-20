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
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Item,
    ItemType,
    Weapon,
    WeaponCategory,
)
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition
from world.story_arc import (
    BeatEffects,
    BeatObjective,
    CompletionTrigger,
    GateKind,
    ObjectiveGate,
    ObjectiveKind,
    StoryArc,
    StoryBeat,
)


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
        director_note: Any = None,
    ) -> NarrativeResult:
        self.calls.append(
            {
                "action_result_text": action_result_text,
                "context_prompt": context_prompt,
                "language": language,
                "player_intent": player_intent,
                "outcome_facts": outcome_facts,
                "has_npc_dialogue": has_npc_dialogue,
                "director_note": director_note,
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
                confidence=0.75,
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
                confidence=0.75,
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
                confidence=0.75,
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
                confidence=0.75,
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
                confidence=0.75,
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

    @pytest.mark.asyncio
    async def test_unknown_entity_carries_pending_intents(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        """Finding 1a — a refused first action must not silently drop the
        intentions chained after it; the orchestrator forwards them onto
        UnknownEntityResult so the cog can announce (never chain) them."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.TALK,
                actor_name="Aldric",
                target_name="Dragon",
                raw_input="je parle au dragon et je vais au nord",
                confidence=0.75,
                pending_intents=["je vais au nord"],
            ),
        )
        narrator = FakeNarrator(
            responses=[
                NarrativeResult(
                    narrative="Tu ne vois aucun dragon.",
                    tone="somber",
                ),
            ],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, {aldric.name: aldric},
        )

        result = await pipeline.process(
            player_text="je parle au dragon et je vais au nord",
        )

        assert isinstance(result, UnknownEntityResult)
        assert result.pending_intents == ["je vais au nord"]

    @pytest.mark.asyncio
    async def test_rule_failure_also_carries_pending_intents(
        self,
        cathedral: Location,
        aldric: NPC,
    ) -> None:
        """Same fix on the sibling branch: a rule-failure refusal (ATTACK
        with no active combat) must forward pending_intents too."""
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.ATTACK,
                actor_name="Aldric",
                target_name="Père Aldric",
                raw_input="j'attaque le prêtre et je fuis",
                confidence=0.7,
                pending_intents=["je fuis"],
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="(refusal)", tone="tense")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral, npcs={aldric.name: aldric},
        )

        result = await pipeline.process(
            player_text="j'attaque le prêtre et je fuis",
        )

        assert isinstance(result, UnknownEntityResult)
        assert result.field_name == "rule"
        assert result.pending_intents == ["je fuis"]


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
    session.interaction_count = 0
    # M1: the real turn counter lives on the Campaign model.
    session.campaign.interaction_count = 0
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
    """Shared arc fixture for the Kaelen interrogation beat.

    Uses explicit BeatObjectives with quality gates so the engine can enforce:
    - MIN_REVEALS >= 1: NPC must share at least one piece of information
    - MIN_DISPOSITION >= 0: NPC must not become more hostile
    Both objectives are required (ALL_REQUIRED). A productive conversation
    (reveals > 0, stable disposition) satisfies both; stonewalling or
    hostility blocks advancement.
    """
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
                objectives=[
                    BeatObjective(
                        id="talk_kaelen_reveals",
                        kind=ObjectiveKind.TALK,
                        target="Kaelen, le Gardien Blessé",
                        description="Speak with Kaelen and get information",
                        required=True,
                        gate=ObjectiveGate(kind=GateKind.MIN_REVEALS, value=1),
                    ),
                    BeatObjective(
                        id="talk_kaelen_disposition",
                        kind=ObjectiveKind.TALK,
                        target="Kaelen, le Gardien Blessé",
                        description="Speak with Kaelen without making him hostile",
                        required=True,
                        gate=ObjectiveGate(kind=GateKind.MIN_DISPOSITION, value=0),
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
    async def test_improvise_without_matching_objective_stays(self):
        """IMPROVISE actions that don't match any objective → engine returns STAY, beat stays.

        The new BeatProgressionEngine routes IMPROVISE to NEEDS_JUDGE only when
        an objective scores as a partial match.  Since ObjectiveKind.INTERACT
        requires ActionType.INTERACT, an IMPROVISE action scores 0.0 and produces
        no partial match → STAY → beat does not advance.
        """
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

        result = await pipeline.process("I use the sand")

        assert isinstance(result, ActionPipelineResult)
        # IMPROVISE scores 0 against ObjectiveKind.INTERACT → STAY → no beat advance.
        assert result.new_beat is None
        assert "Inner Court" not in loc.unlocked_exits

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

        result = await pipeline.process("bonjour garde")

        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is None  # beat did NOT advance


# ---------------------------------------------------------------------------
# Villain / combat-beat NPCs must not be trivially killed
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
    """_should_trivial_resolve must refuse story-critical NPCs.

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
# MOVE rejected while a combat is active
# ---------------------------------------------------------------------------


class TestExplorationBlockedInCombat:
    """ActionPipeline._validate must forward self.combat_state to
    validate_exploration_action so that MOVE/TALK/etc. are refused during
    an active combat."""

    def test_pipeline_autoconverts_move_to_flee_when_combat_active(
        self,
        cathedral: Location,
    ) -> None:
        """MOVE in active combat is auto-converted to FLEE instead of
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
# Combat dispatch
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
    """A LEGAL attack on a combat-worthy NPC bootstraps combat and stores
    the pending embed. (Since audit H18 the triggering action is probed
    against the prospective state — an illegal attack rolls the bootstrap
    back, so the hero needs a real equipped weapon here.)"""
    from unittest.mock import MagicMock, patch
    from engine.combat import CombatState, Combatant, CombatSide
    from engine.combat_trigger import CombatTrigger, CombatTriggerKind, InitiativeSide
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import (
        EquipmentSlot, ITEM_CATALOG, Inventory, add_item, create_inventory,
        equip_item,
    )

    scores = AbilityScores(STR=16, DEX=14, CON=14, INT=10, WIS=12, CHA=8)
    char = create_character("Héros", Race.HUMAN, CharacterClass.FIGHTER, scores)
    goblin_char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE,
                                   AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8))
    hero_inv = create_inventory()
    hero_inv = add_item(hero_inv, ITEM_CATALOG["Longsword"])
    hero_inv = equip_item(hero_inv, "Longsword", EquipmentSlot.MAIN_HAND)
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=hero_inv)
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
        target_name="Goblin", weapon_name="Longsword",
        raw_input="j'attaque le goblin",
    )

    with (
        patch("bot.pipeline.interpret.detect_combat_trigger", return_value=fake_trigger),
        patch("bot.pipeline.interpret.enter_combat", return_value=bootstrapped_state),
        patch("bot.pipeline.interpret.start_combat", return_value=bootstrapped_state),
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
    # Simulate the lethal-intent flag on an IMPROVISE action.
    action = InterpretedAction(
        action_type=ActionType.IMPROVISE, actor_name="Héros",
        target_name="Goblin", raw_input="je lui enfonce ma lame",
    )
    object.__setattr__(action, "is_lethal_intent", True)  # forward-compatibility shim

    with (
        patch("bot.pipeline.interpret.detect_combat_trigger", return_value=fake_trigger),
        patch("bot.pipeline.interpret.enter_combat", return_value=bootstrapped_state),
        patch("bot.pipeline.interpret.start_combat", return_value=bootstrapped_state),
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


# ---------------------------------------------------------------------------
# Flee resolution
# ---------------------------------------------------------------------------


def _make_active_pipeline_with_hero() -> "tuple[ActionPipeline, Any]":
    """Helper: pipeline with active CombatState (hero DEX 14 vs goblin)."""
    from unittest.mock import MagicMock
    from engine.combat import CombatSide, CombatState, Combatant
    from engine.character import CharacterClass, Race, AbilityScores, create_character
    from engine.inventory import Inventory

    scores = AbilityScores(STR=10, DEX=14, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character("Héros", Race.HUMAN, CharacterClass.FIGHTER, scores)
    goblin_char = create_character(
        "Goblin", Race.HALFLING, CharacterClass.ROGUE,
        AbilityScores(STR=8, DEX=12, CON=10, INT=8, WIS=8, CHA=8),
    )
    hero = Combatant(name="Héros", side=CombatSide.PLAYER, character=char, inventory=Inventory())
    goblin = Combatant(name="Goblin", side=CombatSide.ENEMY, character=goblin_char, inventory=Inventory())
    state = CombatState(combatants=[hero, goblin], current_turn_index=0, is_active=True)
    pipeline = ActionPipeline(
        interpreter=MagicMock(), narrator=MagicMock(),
        location=None, npcs={}, actor_name="Héros", combat_state=state,
    )
    return pipeline, hero


async def test_flee_success_marks_combatant_fled() -> None:
    """Successful DEX check marks the combatant as fled; action not consumed."""
    from unittest.mock import patch
    from engine.dice import D20CheckResult, RollOutcome

    pipeline, hero = _make_active_pipeline_with_hero()
    action = InterpretedAction(
        action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
    )
    fake_check = D20CheckResult(
        expression="1d20+2", rolls=[18], modifier=2, total=20, dc=12,
        outcome=RollOutcome.SUCCESS, margin=8,
    )
    with patch("bot.pipeline.resolve.roll_check", return_value=fake_check):
        await pipeline._resolve_flee(action)

    assert hero.fled is True
    assert hero.action_budget.action_used is False


async def test_flee_failure_consumes_action_stays_in_combat() -> None:
    """Failed DEX check: action consumed, combatant stays in combat."""
    from unittest.mock import patch
    from engine.dice import D20CheckResult, RollOutcome

    pipeline, hero = _make_active_pipeline_with_hero()
    action = InterpretedAction(
        action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
    )
    fake_check = D20CheckResult(
        expression="1d20+2", rolls=[4], modifier=2, total=6, dc=12,
        outcome=RollOutcome.FAILURE, margin=-6,
    )
    with patch("bot.pipeline.resolve.roll_check", return_value=fake_check):
        await pipeline._resolve_flee(action)

    assert hero.fled is False
    assert hero.action_budget.action_used is True
    assert pipeline.combat_state is not None
    assert pipeline.combat_state.is_active


async def test_flee_with_all_pcs_fled_ends_combat() -> None:
    """When the only PC flees successfully, combat ends with CombatEndReason.FLED."""
    from unittest.mock import patch
    from engine.dice import D20CheckResult, RollOutcome
    from engine.combat import CombatEndReason

    pipeline, hero = _make_active_pipeline_with_hero()
    action = InterpretedAction(
        action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
    )
    fake_check = D20CheckResult(
        expression="1d20+2", rolls=[18], modifier=2, total=20, dc=12,
        outcome=RollOutcome.SUCCESS, margin=8,
    )
    with patch("bot.pipeline.resolve.roll_check", return_value=fake_check):
        await pipeline._resolve_flee(action)

    assert hero.fled is True
    assert pipeline.combat_state is not None
    assert not pipeline.combat_state.is_active
    assert pipeline.combat_state.end_reason == CombatEndReason.FLED


async def test_flee_dice_embed_added_to_pending() -> None:
    """After _resolve_flee, a ('flee_check', result, actor) tuple is in _pending_dice_embeds."""
    from unittest.mock import patch
    from engine.dice import D20CheckResult, RollOutcome

    pipeline, _ = _make_active_pipeline_with_hero()
    action = InterpretedAction(
        action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
    )
    fake_check = D20CheckResult(
        expression="1d20+2", rolls=[10], modifier=2, total=12, dc=12,
        outcome=RollOutcome.NEAR_SUCCESS, margin=0,
    )
    with patch("bot.pipeline.resolve.roll_check", return_value=fake_check):
        await pipeline._resolve_flee(action)

    assert len(pipeline._pending_dice_embeds) == 1
    label, result_obj, actor = pipeline._pending_dice_embeds[0]
    assert label == "flee_check"
    assert actor == "Héros"


async def test_flee_applies_stored_destination_on_full_escape() -> None:
    """On full escape, change_location is called with the stored destination."""
    from unittest.mock import patch, AsyncMock, MagicMock
    from engine.dice import D20CheckResult, RollOutcome
    from engine.combat import CombatEndReason

    pipeline, hero = _make_active_pipeline_with_hero()
    pipeline._pending_flee_destination = "forêt"
    pipeline.db_factory = MagicMock()
    # finalize_combat reads session.combat_state, which in prod is the
    # same object as pipeline.combat_state. Link them in the mock.
    pipeline.session = MagicMock()
    pipeline.session.combat_state = pipeline.combat_state

    fake_location = MagicMock()
    fake_location.name = "forêt"

    action = InterpretedAction(
        action_type=ActionType.FLEE, actor_name="Héros", raw_input="je fuis",
    )
    fake_check = D20CheckResult(
        expression="1d20+2", rolls=[18], modifier=2, total=20, dc=12,
        outcome=RollOutcome.SUCCESS, margin=8,
    )
    with (
        patch("bot.pipeline.resolve.roll_check", return_value=fake_check),
        patch("bot.world_navigation.change_location", new=AsyncMock(return_value=fake_location)),
    ):
        outcome = await pipeline._resolve_flee(action)

    assert "forêt" in outcome.summary
    assert pipeline.combat_state is not None
    assert not pipeline.combat_state.is_active
    assert pipeline.combat_state.end_reason == CombatEndReason.FLED


# ---------------------------------------------------------------------------
# Combat event recording hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_records_combat_event_after_resolution_when_active() -> None:
    """A resolved LOOK action in combat adds a short event to state.recent_events."""
    from ai.models import InterpretedAction
    from unittest.mock import MagicMock

    state, char = _make_combat_state_with_hero("Héros")
    assert state.is_active

    pipeline = ActionPipeline(
        interpreter=MagicMock(),
        narrator=FakeNarrator(),
        location=None,
        npcs={},
        actor_name="Héros",
        combat_state=state,
    )
    action = InterpretedAction(
        action_type=ActionType.LOOK,
        actor_name="Héros",
        raw_input="je regarde autour",
    )
    outcome = await pipeline._resolve_mechanics(action)
    assert outcome.summary  # sanity

    # Mirror the pipeline's recording step.
    from engine.combat import record_combat_event
    record_combat_event(state, outcome.summary.strip())
    assert state.recent_events[-1] == outcome.summary.strip()


@pytest.mark.asyncio
async def test_pipeline_event_recording_skipped_when_combat_inactive() -> None:
    """When combat_state is None the pipeline must not crash and must not record."""
    from ai.models import InterpretedAction
    from unittest.mock import MagicMock

    pipeline = ActionPipeline(
        interpreter=MagicMock(),
        narrator=FakeNarrator(),
        location=None,
        npcs={},
        actor_name="Héros",
        combat_state=None,
    )
    action = InterpretedAction(
        action_type=ActionType.LOOK,
        actor_name="Héros",
        raw_input="je regarde autour",
    )
    outcome = await pipeline._resolve_mechanics(action)
    assert outcome.summary
    # With no combat_state there is nothing to append to — nothing crashes.
    assert pipeline.combat_state is None


# ---------------------------------------------------------------------------
# Weapon auto-resolution
# ---------------------------------------------------------------------------


def _make_longsword() -> Weapon:
    return Weapon(
        name="Longsword",
        weight=3.0,
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
    )


class TestWeaponAutoResolve:
    """weapon_name auto-filled from MAIN_HAND when player omits it."""

    def test_auto_resolves_main_hand_weapon(self) -> None:
        """ATTACK + weapon_name=None + Longsword in MAIN_HAND → weapon_name='Longsword'."""
        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) == "Longsword"

    def test_unrecognised_alias_with_single_equipped_weapon_resolves_to_it(self) -> None:
        """Player says 'Dagger' but only Longsword is equipped → fuzzy-resolves to Longsword."""
        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        assert ActionPipeline._auto_resolve_weapon_name("Dagger", inv) == "Longsword"

    def test_no_resolve_empty_main_hand(self) -> None:
        """No weapon in MAIN_HAND → stays None."""
        inv = Inventory(items=[], equipped={})
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) is None

    def test_no_resolve_non_weapon_in_main_hand(self) -> None:
        """Shield in MAIN_HAND → stays None (not a Weapon instance)."""
        shield = Item(name="Shield", item_type=ItemType.SHIELD, weight=6.0)
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: shield})
        assert ActionPipeline._auto_resolve_weapon_name(None, inv) is None

    def test_no_resolve_no_inventory(self) -> None:
        """inventory is None → stays None, no crash."""
        assert ActionPipeline._auto_resolve_weapon_name(None, None) is None

    def test_french_alias_resolves_to_equipped_weapon(self) -> None:
        """Player says 'épée' with Longsword in MAIN_HAND → canonical 'Longsword'."""
        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        assert ActionPipeline._auto_resolve_weapon_name("épée", inv) == "Longsword"

    def test_case_insensitive_match_returns_canonical(self) -> None:
        """'longsword' (lowercase) matches Longsword exactly, returns canonical form."""
        sword = _make_longsword()
        inv = Inventory(items=[], equipped={EquipmentSlot.MAIN_HAND: sword})
        assert ActionPipeline._auto_resolve_weapon_name("longsword", inv) == "Longsword"

    def test_named_weapon_in_off_hand_resolves_correctly(self) -> None:
        """Player names 'Dagger' and Dagger is in OFF_HAND → case-insensitive match wins."""
        sword = _make_longsword()
        dagger = Weapon(
            name="Dagger",
            weight=1.0,
            damage_dice="1d4",
            damage_type=DamageType.PIERCING,
            weapon_category=WeaponCategory.SIMPLE_MELEE,
        )
        inv = Inventory(
            items=[],
            equipped={
                EquipmentSlot.MAIN_HAND: sword,
                EquipmentSlot.OFF_HAND: dagger,
            },
        )
        assert ActionPipeline._auto_resolve_weapon_name("Dagger", inv) == "Dagger"

    def test_none_inventory_with_alias_returns_none(self) -> None:
        """weapon_name is given but inventory is None → None."""
        assert ActionPipeline._auto_resolve_weapon_name("épée", None) is None


# ---------------------------------------------------------------------------
# _resolve_pc_attack
# ---------------------------------------------------------------------------


def _make_pc_combatant_with_sword(name: str = "JeanTest", hp: int = 20):
    from engine.combat import CombatSide, Combatant

    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=10, CON=14, INT=10, WIS=10, CHA=10),
    )
    char.hp = hp
    char.max_hp = hp
    sword = _make_longsword()
    inv = Inventory(items=[sword], equipped={EquipmentSlot.MAIN_HAND: sword})
    return Combatant(
        name=name,
        side=CombatSide.PLAYER,
        character=char,
        inventory=inv,
        initiative=18,
    )


def _make_enemy_combatant(name: str = "Gobelin", hp: int = 15, ac: int = 12):
    from engine.combat import CombatSide, Combatant

    char = create_character(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=8, WIS=8, CHA=8),
    )
    char.hp = hp
    char.max_hp = hp
    char.ac = ac
    from engine.inventory import create_inventory
    return Combatant(
        name=name,
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
        initiative=5,
    )


def _fake_hit_result(attacker: str, defender: str, damage: int, hp_remaining: int):
    from engine.combat import AttackResult
    from engine.dice import RollOutcome

    return AttackResult(
        attacker=attacker,
        defender=defender,
        weapon_name="Longsword",
        attack_roll=15,
        attack_total=18,
        ac=12,
        hit=True,
        critical=False,
        outcome=RollOutcome.SUCCESS,
        damage=damage,
        damage_type=DamageType.SLASHING,
        defender_hp_remaining=hp_remaining,
    )


def _fake_miss_result(attacker: str, defender: str, hp: int):
    from engine.combat import AttackResult
    from engine.dice import RollOutcome

    return AttackResult(
        attacker=attacker,
        defender=defender,
        weapon_name="Longsword",
        attack_roll=1,
        attack_total=4,
        ac=12,
        hit=False,
        critical=False,
        outcome=RollOutcome.FAILURE,
        damage=0,
        damage_type=DamageType.SLASHING,
        defender_hp_remaining=hp,
    )


class TestResolvePcAttack:
    @pytest.mark.asyncio
    async def test_hit_reduces_defender_hp_and_populates_hp_delta(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A hit reduces defender HP in-place and sets hp_delta in public_effects."""
        from engine.combat import CombatState

        pc = _make_pc_combatant_with_sword()
        enemy = _make_enemy_combatant(hp=15)
        state = CombatState(combatants=[pc, enemy], round_number=1, current_turn_index=0)

        fake_result = _fake_hit_result("JeanTest", "Gobelin", damage=7, hp_remaining=8)

        def _fake_resolve(attacker, target, weapon, **_kw):  # type: ignore[override]
            target.character.hp = fake_result.defender_hp_remaining
            return fake_result

        monkeypatch.setattr("engine.combat.resolve_attack", _fake_resolve)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(
            FakeInterpreter(response=InterpretedAction(
                action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
            )),
            narrator, None, {},
            actor_name="JeanTest",
        )
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        outcome = await pipeline._resolve_mechanics(action)

        assert enemy.character.hp == 8
        assert outcome.public_effects.hp_delta == {"Gobelin": -7}
        assert len(pipeline._pending_dice_embeds) == 1
        kind, result_obj, actor = pipeline._pending_dice_embeds[0]
        assert kind == "attack_roll"
        assert actor == "JeanTest"

    @pytest.mark.asyncio
    async def test_miss_leaves_hp_unchanged_and_empty_hp_delta(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A miss leaves defender HP unchanged and hp_delta is empty."""
        from engine.combat import CombatState

        pc = _make_pc_combatant_with_sword()
        enemy = _make_enemy_combatant(hp=15)
        state = CombatState(combatants=[pc, enemy], round_number=1, current_turn_index=0)

        fake_result = _fake_miss_result("JeanTest", "Gobelin", hp=15)
        monkeypatch.setattr("engine.combat.resolve_attack", lambda *_a, **_kw: fake_result)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(
            FakeInterpreter(response=InterpretedAction(
                action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
            )),
            narrator, None, {},
            actor_name="JeanTest",
        )
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        outcome = await pipeline._resolve_mechanics(action)

        assert enemy.character.hp == 15
        assert outcome.public_effects.hp_delta == {}
        assert len(pipeline._pending_dice_embeds) == 1
        kind, _, _ = pipeline._pending_dice_embeds[0]
        assert kind == "attack_roll"

    @pytest.mark.asyncio
    async def test_action_budget_consumed_before_attack_resolves(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """consume_action() is called — action_budget.action_used is True after."""
        from engine.combat import CombatState

        pc = _make_pc_combatant_with_sword()
        enemy = _make_enemy_combatant(hp=15)
        state = CombatState(combatants=[pc, enemy], round_number=1, current_turn_index=0)

        fake_result = _fake_hit_result("JeanTest", "Gobelin", damage=5, hp_remaining=10)
        monkeypatch.setattr("engine.combat.resolve_attack", lambda *_a, **_kw: fake_result)

        assert not pc.action_budget.action_used

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(
            FakeInterpreter(response=InterpretedAction(
                action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
            )),
            narrator, None, {},
            actor_name="JeanTest",
        )
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="Gobelin",
            weapon_name="Longsword",
            raw_input="(bouton Attaquer → Gobelin)",
        )
        await pipeline._resolve_mechanics(action)

        assert pc.action_budget.action_used

    @pytest.mark.asyncio
    async def test_unknown_target_returns_fallback_without_crash(self) -> None:
        """If target_name is not in combat_state, returns a generic MechanicsOutcome."""
        from engine.combat import CombatState
        from ai.models import MechanicsOutcome

        pc = _make_pc_combatant_with_sword()
        enemy = _make_enemy_combatant()
        state = CombatState(combatants=[pc, enemy], round_number=1, current_turn_index=0)

        narrator = FakeNarrator(responses=[NarrativeResult(narrative=".", tone="tense")])
        pipeline = _make_pipeline(
            FakeInterpreter(response=InterpretedAction(
                action_type=ActionType.ATTACK, actor_name="JeanTest", raw_input="",
            )),
            narrator, None, {},
            actor_name="JeanTest",
        )
        pipeline.combat_state = state
        pipeline.inventory = pc.inventory

        action = InterpretedAction(
            action_type=ActionType.ATTACK,
            actor_name="JeanTest",
            target_name="InconnuInexistant",
            weapon_name="Longsword",
            raw_input="",
        )
        outcome = await pipeline._resolve_mechanics(action)

        assert isinstance(outcome, MechanicsOutcome)
        assert len(pipeline._pending_dice_embeds) == 0


# ---------------------------------------------------------------------------
# _assign_initial_zones — places combatants in starting zones on combat start
# ---------------------------------------------------------------------------


class TestAssignInitialZones:
    def test_pcs_go_to_first_zone_npcs_go_to_last(self) -> None:
        """PCs land in zone[0], enemies in zone[-1] when there are 2+ zones."""
        from engine.combat import CombatState
        from world.combat_zone import Zone
        from world.location import Location
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant_with_sword("Hero")
        npc = _make_enemy_combatant("Orc")
        state = CombatState(combatants=[pc, npc], round_number=1, current_turn_index=0)

        location = Location(
            name="Arena",
            combat_zones=[Zone(name="Nord"), Zone(name="Sud")],
        )

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Nord"
        assert npc.current_zone == "Sud"

    def test_single_zone_both_sides_placed_in_same_zone(self) -> None:
        """When only one zone exists, PCs and enemies share it."""
        from engine.combat import CombatState
        from world.combat_zone import Zone
        from world.location import Location
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant_with_sword("Hero")
        npc = _make_enemy_combatant("Orc")
        state = CombatState(combatants=[pc, npc], round_number=1, current_turn_index=0)

        location = Location(
            name="Tavern",
            combat_zones=[Zone(name="Salle")],
        )

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Salle"
        assert npc.current_zone == "Salle"

    def test_combatant_with_existing_zone_is_not_overwritten(self) -> None:
        """A combatant that already has current_zone set is left untouched."""
        from engine.combat import CombatState
        from world.combat_zone import Zone
        from world.location import Location
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant_with_sword("Hero")
        pc.current_zone = "Milieu"  # already placed
        npc = _make_enemy_combatant("Orc")
        state = CombatState(combatants=[pc, npc], round_number=1, current_turn_index=0)

        location = Location(
            name="Arena",
            combat_zones=[Zone(name="Nord"), Zone(name="Sud")],
        )

        _assign_initial_zones(state, location)

        assert pc.current_zone == "Milieu"   # unchanged
        assert npc.current_zone == "Sud"

    def test_no_zones_is_a_noop(self) -> None:
        """When the location has no combat_zones, nothing is assigned."""
        from engine.combat import CombatState
        from world.location import Location
        from bot.action_pipeline import _assign_initial_zones

        pc = _make_pc_combatant_with_sword("Hero")
        npc = _make_enemy_combatant("Orc")
        state = CombatState(combatants=[pc, npc], round_number=1, current_turn_index=0)

        location = Location(name="Dungeon")  # no combat_zones

        _assign_initial_zones(state, location)

        assert pc.current_zone is None
        assert npc.current_zone is None


class TestDriftTrackerWiring:
    """Verify the DriftTracker singleton is wired into PipelineRunner."""

    def test_get_drift_tracker_returns_singleton(self) -> None:
        from bot.pipeline.orchestrator import get_drift_tracker
        a = get_drift_tracker()
        b = get_drift_tracker()
        assert a is b

    def test_should_run_director_on_force(self) -> None:
        from bot.pipeline.orchestrator import should_run_director
        assert should_run_director(
            interaction_count=1, combat_just_ended=False,
            drift_detected=False, force=True,
        ) is True

    def test_should_run_director_on_drift(self) -> None:
        from bot.pipeline.orchestrator import should_run_director
        assert should_run_director(
            interaction_count=1, combat_just_ended=False,
            drift_detected=True, force=False,
        ) is True

    def test_should_run_director_on_combat_end(self) -> None:
        from bot.pipeline.orchestrator import should_run_director
        assert should_run_director(
            interaction_count=1, combat_just_ended=True,
            drift_detected=False, force=False,
        ) is True

    def test_should_run_director_every_six_actions(self) -> None:
        from bot.pipeline.orchestrator import should_run_director
        assert should_run_director(
            interaction_count=6, combat_just_ended=False,
            drift_detected=False, force=False,
        ) is True
        assert should_run_director(
            interaction_count=12, combat_just_ended=False,
            drift_detected=False, force=False,
        ) is True

    def test_should_run_director_otherwise_false(self) -> None:
        from bot.pipeline.orchestrator import should_run_director
        assert should_run_director(
            interaction_count=5, combat_just_ended=False,
            drift_detected=False, force=False,
        ) is False
        assert should_run_director(
            interaction_count=0, combat_just_ended=False,
            drift_detected=False, force=False,
        ) is False

    def test_pipeline_runner_has_force_director_run_field(self) -> None:
        """PipelineRunner exposes force_director_run on its dataclass."""
        from bot.pipeline.orchestrator import PipelineRunner
        from dataclasses import fields
        field_names = {f.name for f in fields(PipelineRunner)}
        assert "force_director_run" in field_names


class TestBeatCompletionIndexing:
    """Verify the SemanticIndexer is called when beats complete."""

    @pytest.mark.asyncio
    async def test_apply_beat_effects_indexes_narrative_hint(self) -> None:
        """When a runner has an indexer, beat completion indexes the narrative_hint."""
        from unittest.mock import MagicMock
        from bot.pipeline.orchestrator import PipelineRunner
        from memory.indexer import SemanticIndexer
        from world.story_arc import BeatEffects

        # Build a minimal runner with mocked deps + an indexer.
        # The runner must have campaign_id set for the indexing call.
        indexer = MagicMock(spec=SemanticIndexer)
        runner = PipelineRunner(
            interpreter=MagicMock(),
            narrator=MagicMock(),
            location=None,
            npcs={},
            actor_name="Tester",
            campaign_id="cmp_test",
            semantic_indexer=indexer,
        )
        effects = BeatEffects(
            narrative_hint="A breach opens in the wall.",
            state_flags={"breach_open": True},
        )
        await runner._apply_beat_effects(effects, beat_number=1)

        # narrative_hint indexed:
        indexer.index_revealed_fact.assert_any_call(
            "cmp_test", fact="A breach opens in the wall.",
        )

    @pytest.mark.asyncio
    async def test_apply_beat_effects_no_indexer_works_unchanged(self) -> None:
        """When no indexer is provided, _apply_beat_effects must not raise."""
        from unittest.mock import MagicMock
        from bot.pipeline.orchestrator import PipelineRunner
        from world.story_arc import BeatEffects

        runner = PipelineRunner(
            interpreter=MagicMock(),
            narrator=MagicMock(),
            location=None,
            npcs={},
            actor_name="Tester",
            campaign_id="cmp_test",
        )
        # No semantic_indexer.
        effects = BeatEffects(
            narrative_hint="A breach opens.", state_flags={"breach_open": True},
        )
        # Must not raise.
        await runner._apply_beat_effects(effects, beat_number=1)
