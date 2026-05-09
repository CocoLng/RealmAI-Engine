"""Integration tests for the IMPROVISE → skill check resolver.

Covers:
1. Direct ``resolve_improvise`` — skill is inferred AND the actor's
   :class:`Character` is reachable through ``session`` → a d20 check is
   rolled, queued on ``side.pending_dice_embeds``, and the summary +
   outcome_facts mention the skill.
2. ``resolve_improvise`` with no recognised verb → falls back to the
   legacy "narrator arbitrates without a roll" summary, no dice embed.
3. ``resolve_improvise`` with a recognised verb but session=None →
   falls back to the legacy summary (no fake roll fabricated).
4. End-to-end via :class:`ActionPipeline.process_interpreted_action` —
   the ``ActionPipelineResult`` lands with the narrator having received
   the skill-check fact, and ``pipeline._pending_dice_embeds`` is
   populated with the right tuple shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction, NarrativeResult
from bot.action_pipeline import ActionPipeline, ActionPipelineResult
from bot.pipeline.resolve import (
    ResolveSideChannel,
    resolve_improvise,
)
from engine.character import (
    AbilityScores,
    Character,
    CharacterClass,
    Race,
    Skill,
    create_character,
)
from engine.dice import D20CheckResult
from engine.skill_check import (
    DEFAULT_SKILL_DC,
    HARD_DC,
    MODERATE_DC,
)
from engine.validators import ActionType
from world.npc import NPC, NPCDisposition


# ---------------------------------------------------------------------------
# Fakes (kept minimal — full fakes already exist in test_action_pipeline.py)
# ---------------------------------------------------------------------------


@dataclass
class _FakeNarrator:
    """Stub narrator returning canned narratives in order."""

    responses: list[NarrativeResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

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
                "player_intent": player_intent,
                "outcome_facts": outcome_facts,
            }
        )
        if not self.responses:
            return NarrativeResult(narrative="(default)", tone="dramatic")
        return self.responses.pop(0)


def _hero_character(name: str = "Shadow") -> Character:
    """Rogue with high DEX + Sleight of Hand proficiency for predictable mods."""
    scores = AbilityScores(STR=10, DEX=18, CON=12, INT=12, WIS=10, CHA=10)
    char = create_character(name, Race.HUMAN, CharacterClass.ROGUE, scores)
    char.skill_proficiencies = [
        Skill.SLEIGHT_OF_HAND,
        Skill.STEALTH,
        Skill.ACROBATICS,
        Skill.PERSUASION,
    ]
    return char


def _build_session(character: Character | None) -> Any:
    """Minimal session mock — characters dict, no combat, no agents."""
    session = MagicMock()
    if character is not None:
        session.characters = {1: character}
    else:
        session.characters = {}
    session.combat_state = None
    return session


# ---------------------------------------------------------------------------
# Direct resolver tests
# ---------------------------------------------------------------------------


class TestResolveImproviseDirect:
    """Unit-level checks of resolve_improvise's three branches."""

    def test_steal_triggers_sleight_of_hand_check(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je vole la bourse du marchand discrètement",
            improvise_description="vole la bourse du marchand",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        # 1. A dice embed entry was queued.
        assert len(side.pending_dice_embeds) == 1
        kind, check, name, skill = side.pending_dice_embeds[0]
        assert kind == "skill_check"
        assert isinstance(check, D20CheckResult)
        assert name == hero.name
        assert skill == Skill.SLEIGHT_OF_HAND

        # 2. The roll used the correct DEX-based modifier (DEX 18 → +4
        #    plus level-1 proficiency +2 = +6).
        natural_roll = check.rolls[0]
        assert check.total == natural_roll + 6
        # No NPC in scene → static default DC.
        assert check.dc == DEFAULT_SKILL_DC

        # 3. The summary + outcome_facts surface the skill so the
        #    narrator can color the prose accordingly.
        assert "Sleight of Hand" in outcome.summary
        assert "DEX" in outcome.summary
        assert "Skill check" in outcome.outcome_facts
        assert "Sleight of Hand" in outcome.outcome_facts

    def test_jump_triggers_athletics_check(self) -> None:
        hero = _hero_character("Acro")
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je saute par-dessus la crevasse",
            improvise_description="saute par-dessus la crevasse",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        assert len(side.pending_dice_embeds) == 1
        _, check, _, skill = side.pending_dice_embeds[0]
        assert skill == Skill.ATHLETICS
        # No proficiency in athletics → STR 10 → +0 modifier.
        assert check.total == check.rolls[0]
        assert "Athletics" in outcome.outcome_facts

    def test_persuade_triggers_persuasion_check(self) -> None:
        hero = _hero_character("Diplomat")
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je convaincs le garde de me laisser passer",
            improvise_description="convaincs le garde de me laisser passer",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        assert len(side.pending_dice_embeds) == 1
        _, _, _, skill = side.pending_dice_embeds[0]
        assert skill == Skill.PERSUASION
        assert "Persuasion" in outcome.summary

    def test_unrecognised_action_falls_back_to_legacy(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je m'assois sur la chaise et soupire",
            improvise_description="s'assoit sur la chaise et soupire",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        assert side.pending_dice_embeds == []
        assert "improvised action" in outcome.summary
        assert outcome.outcome_facts == ""

    def test_recognised_verb_without_session_character_falls_back(self) -> None:
        """If the actor's character is unreachable, no fake roll is made."""
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Ghost",  # not in the empty session.characters
            raw_input="Je vole la bourse",
            improvise_description="vole la bourse",
            confidence=0.9,
        )
        side = ResolveSideChannel()
        empty_session = _build_session(character=None)

        outcome = resolve_improvise(
            action=action,
            actor_name="Ghost",
            npcs={},
            session=empty_session,
            side=side,
        )

        assert side.pending_dice_embeds == []
        assert "improvised action" in outcome.summary

    def test_no_session_falls_back(self) -> None:
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name="Hero",
            raw_input="Je saute par-dessus la barrière",
            improvise_description="saute par-dessus la barrière",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name="Hero",
            npcs={}, session=None, side=side,
        )

        assert side.pending_dice_embeds == []
        assert "improvised action" in outcome.summary


# ---------------------------------------------------------------------------
# Contest + difficulty bias integration
# ---------------------------------------------------------------------------


def _make_npc(
    *,
    name: str,
    wis: int = 10,
    cha: int = 10,
    disposition: NPCDisposition = NPCDisposition.NEUTRAL,
) -> NPC:
    scores = AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=wis, CHA=cha)
    return NPC(
        name=name,
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=scores,
        hp=10, max_hp=10, ac=10,
        disposition=disposition,
    )


class TestImproviseContestedDC:
    """The DC adapts to the targeted NPC and to narrative qualifiers."""

    def test_steal_against_high_perception_merchant_raises_dc(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        # Sharp-eyed merchant (WIS 18 → +4 → passive perception 14).
        merchant = _make_npc(name="Marchand", wis=18)
        npcs = {merchant.name: merchant}
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je vole la bourse du marchand",
            improvise_description="vole la bourse du marchand",
            target_name=merchant.name,
            confidence=0.9,
        )
        side = ResolveSideChannel()

        resolve_improvise(
            action=action, actor_name=hero.name,
            npcs=npcs, session=session, side=side,
        )

        _, check, _, skill = side.pending_dice_embeds[0]
        assert skill == Skill.SLEIGHT_OF_HAND
        # Contested DC = 10 + WIS_mod(18) = 14.
        assert check.dc == 14

    def test_steal_against_oblivious_merchant_lowers_dc(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        oblivious = _make_npc(name="Vieillard", wis=6)  # WIS 6 → -2
        npcs = {oblivious.name: oblivious}
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je vole la bourse du vieillard",
            improvise_description="vole la bourse",
            target_name=oblivious.name,
            confidence=0.9,
        )
        side = ResolveSideChannel()

        resolve_improvise(
            action=action, actor_name=hero.name,
            npcs=npcs, session=session, side=side,
        )

        _, check, _, _ = side.pending_dice_embeds[0]
        # Contested DC = 10 + (-2) = 8.
        assert check.dc == 8

    def test_persuasion_dc_scales_with_disposition(self) -> None:
        hero = _hero_character("Diplomat")
        session = _build_session(hero)
        hostile = _make_npc(
            name="Garde",
            cha=10,
            disposition=NPCDisposition.HOSTILE,
        )
        friendly = _make_npc(
            name="Allié",
            cha=10,
            disposition=NPCDisposition.FRIENDLY,
        )

        # Hostile path
        action_h = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je convaincs le garde",
            improvise_description="convaincs le garde",
            target_name=hostile.name,
            confidence=0.9,
        )
        side_h = ResolveSideChannel()
        resolve_improvise(
            action=action_h, actor_name=hero.name,
            npcs={hostile.name: hostile},
            session=session, side=side_h,
        )
        _, check_hostile, _, _ = side_h.pending_dice_embeds[0]

        # Friendly path
        action_f = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je convaincs l'allié",
            improvise_description="convaincs l'allié",
            target_name=friendly.name,
            confidence=0.9,
        )
        side_f = ResolveSideChannel()
        resolve_improvise(
            action=action_f, actor_name=hero.name,
            npcs={friendly.name: friendly},
            session=session, side=side_f,
        )
        _, check_friendly, _, _ = side_f.pending_dice_embeds[0]

        assert check_hostile.dc > check_friendly.dc

    def test_risky_jump_raises_default_dc(self) -> None:
        hero = _hero_character("Bonheur")
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je tente un saut très risqué par-dessus le ravin",
            improvise_description="saut très risqué par-dessus le ravin",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        _, check, _, skill = side.pending_dice_embeds[0]
        assert skill == Skill.ATHLETICS
        # "Risky" qualifier pushes the DC above the moderate baseline.
        assert check.dc > MODERATE_DC
        assert check.dc >= HARD_DC

    def test_easy_action_lowers_default_dc(self) -> None:
        hero = _hero_character("Sage")
        session = _build_session(hero)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je tente un petit saut facile",
            improvise_description="petit saut facile",
            confidence=0.9,
        )
        side = ResolveSideChannel()

        resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={}, session=session, side=side,
        )

        _, check, _, _ = side.pending_dice_embeds[0]
        assert check.dc < MODERATE_DC

    def test_summary_mentions_contest_target(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        merchant = _make_npc(name="Vendeur", wis=10)
        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je vole la bourse du vendeur",
            improvise_description="vole la bourse du vendeur",
            target_name=merchant.name,
            confidence=0.9,
        )
        side = ResolveSideChannel()

        outcome = resolve_improvise(
            action=action, actor_name=hero.name,
            npcs={merchant.name: merchant},
            session=session, side=side,
        )

        # Both summary and outcome_facts should mention the contest
        # target so the narrator knows the merchant pushed back.
        assert "Vendeur" in outcome.summary
        assert "Vendeur" in outcome.outcome_facts


# ---------------------------------------------------------------------------
# Pipeline-level integration test
# ---------------------------------------------------------------------------


class TestImproviseSkillCheckThroughPipeline:
    """End-to-end check via :class:`ActionPipeline`."""

    @pytest.mark.asyncio
    async def test_pipeline_queues_skill_check_dice_embed(self) -> None:
        hero = _hero_character()
        session = _build_session(hero)
        # Pipeline runner expects a few extra session attrs.
        session.story_arc = None
        session.semantic_indexer = None
        session.interaction_count = 0
        session.language = "fr"
        session.npc_agent = None
        session.npc_generator = None
        session.current_location = None

        narrator = _FakeNarrator(
            responses=[
                NarrativeResult(
                    narrative="Le garde ne remarque rien.", tone="tense",
                )
            ]
        )
        pipeline = ActionPipeline(
            interpreter=MagicMock(),
            narrator=narrator,  # type: ignore[arg-type]
            location=None,
            npcs={},
            actor_name=hero.name,
            language="fr",
            campaign_id="test-campaign",
        )
        pipeline.session = session

        action = InterpretedAction(
            action_type=ActionType.IMPROVISE,
            actor_name=hero.name,
            raw_input="Je vole la bourse du marchand",
            improvise_description="vole la bourse du marchand",
            confidence=0.9,
        )

        result = await pipeline.process_interpreted_action(action)

        assert isinstance(result, ActionPipelineResult)

        # The pipeline staged exactly one skill-check dice embed.
        embeds = list(pipeline._pending_dice_embeds)
        assert len(embeds) == 1
        kind, check, name, skill = embeds[0]
        assert kind == "skill_check"
        assert isinstance(check, D20CheckResult)
        assert name == hero.name
        assert skill == Skill.SLEIGHT_OF_HAND

        # The narrator received the skill-check fact, NOT just the bare
        # "improvised action" summary — this is what lets the narrator
        # honor the d20 outcome instead of free-styling.
        assert narrator.calls, "Narrator was never called"
        narrator_facts = narrator.calls[-1]["outcome_facts"]
        assert "Sleight of Hand" in narrator_facts
        assert "outcome=" in narrator_facts
