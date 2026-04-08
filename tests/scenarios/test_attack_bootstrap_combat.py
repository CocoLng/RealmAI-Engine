"""Lot C — bootstrap a CombatState from a free-text Attack against an NPC.

Direct ActionPipeline test (Discord/cogs out of scope). Mocks Interpreter
and Narrator; the entity resolver, validator and combat models run for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai.models import InterpretedAction, NarrativeResult
from bot.action_pipeline import ActionPipeline
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Weapon,
    WeaponCategory,
)
from world.location import Location
from world.npc import NPC


@pytest.mark.asyncio
async def test_attack_bootstraps_combat_against_present_npc() -> None:
    scores = AbilityScores(STR=12, DEX=12, CON=12, INT=10, WIS=10, CHA=10)
    pc = create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)
    sword = Weapon(
        name="Longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        weight=3.0,
    )
    inventory = Inventory(equipped={EquipmentSlot.MAIN_HAND: sword})

    location = Location(
        name="Place de la Cathédrale",
        description="Une vaste place pavée.",
        connections=[],
        npcs_present=["Jeanne"],
        items_available=[],
    )
    jeanne = NPC(
        name="Jeanne",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=scores,
        hp=15,
        max_hp=15,
        ac=10,
        description="Une villageoise armée et entraînée.",
        location_name="Place de la Cathédrale",
        aliases=["villageoise", "paysanne"],
    )

    session = SimpleNamespace(
        characters={1: pc},
        inventories={1: inventory},
        spellcasters={1: None},
        combat_state=None,
        current_location=location,
        npcs={jeanne.name: jeanne},
        language="fr",
        campaign=SimpleNamespace(id="cmp-1"),
    )

    interpreter = MagicMock()
    interpreter.interpret.return_value = InterpretedAction(
        action_type="Attack",  # type: ignore[arg-type]
        actor_name="Aldric",
        target_name="la villageoise",
        weapon_name="Longsword",
        raw_input="j'attaque le villageois",
        confidence=0.9,
    )

    narrator = MagicMock()
    narrator.narrate.return_value = NarrativeResult(
        narrative="Aldric fond sur Jeanne, lame brandie.",
        tone="tense",
    )

    pipeline = ActionPipeline(
        interpreter=interpreter,
        narrator=narrator,
        location=location,
        npcs={jeanne.name: jeanne},
        actor_name="Aldric",
        language="fr",
        campaign_id="cmp-1",
        combat_state=None,
        inventory=inventory,
        session=session,  # type: ignore[arg-type]
    )

    result = await pipeline.process("j'attaque le villageois")

    # Combat must have been bootstrapped on the live session.
    assert session.combat_state is not None
    state = session.combat_state
    names = [c.name for c in state.combatants]
    assert "Aldric" in names
    assert "Jeanne" in names

    aldric_c = next(c for c in state.combatants if c.name == "Aldric")
    jeanne_c = next(c for c in state.combatants if c.name == "Jeanne")
    assert aldric_c.side == CombatSide.PLAYER
    assert jeanne_c.side == CombatSide.ENEMY

    # Attacker holds the surprise → first in initiative order.
    assert state.combatants[0].name == "Aldric"
    assert state.round_number == 1
    assert state.current_turn_index == 0

    # The attack reached the narrator (validation succeeded).
    assert narrator.narrate.called
    # Pipeline returned a successful result type (not a refusal).
    assert result.__class__.__name__ == "ActionPipelineResult"
