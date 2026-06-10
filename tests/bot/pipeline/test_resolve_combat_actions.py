"""Tests for the CAST_SPELL / DEFEND / DISENGAGE resolver branches (audit C2).

Before this chantier, ``resolve_mechanics`` had no branch for these three
action types: spells were validated and narrated but dealt 0 damage and
consumed no slot. These tests pin the real behaviour:

- CAST_SPELL → :func:`engine.combat.resolve_spell` (slot consumed,
  damage/healing applied, action-economy slot spent, dice embed queued,
  ``PublicEffects.hp_delta`` populated, ``target_defeated`` on kill).
- DEFEND → Action consumed + DODGING condition applied.
- DISENGAGE → Action consumed + ``disengaged_this_turn`` flag set.
"""

from __future__ import annotations

import pytest

from ai.models import InterpretedAction
from bot.embeds.dice_embed import embed_for_dice_entry
from bot.pipeline.resolve import ResolveSideChannel, resolve_mechanics
from engine.character import (
    Ability,
    AbilityScores,
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant, SpellCastResult
from engine.conditions import ConditionType, has_condition
from engine.inventory import create_inventory
from engine.spells import SpellcasterState
from engine.validators import ActionType


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard() -> Combatant:
    scores = apply_racial_bonuses(
        AbilityScores(STR=8, DEX=14, CON=12, INT=16, WIS=12, CHA=10),
        Race.HUMAN,
    )
    char = create_character("Elara", Race.HUMAN, CharacterClass.WIZARD, scores)
    state = SpellcasterState(
        spellcasting_ability=Ability.INT,
        spells_known=[
            "Fire Bolt", "Magic Missile", "Cure Wounds", "Healing Word",
        ],
        spell_slots_max={1: 2},
        spell_slots_remaining={1: 2},
    )
    return Combatant(
        name="Elara",
        side=CombatSide.PLAYER,
        character=char,
        inventory=create_inventory(),
        spellcaster=state,
    )


def _goblin(hp: int = 20) -> Combatant:
    scores = AbilityScores(STR=8, DEX=14, CON=10, INT=10, WIS=8, CHA=8)
    char = create_character("Goblin", Race.HALFLING, CharacterClass.ROGUE, scores)
    char.hp = hp
    char.max_hp = max(hp, char.max_hp)
    return Combatant(
        name="Goblin",
        side=CombatSide.ENEMY,
        character=char,
        inventory=create_inventory(),
    )


def _combat(*combatants: Combatant) -> CombatState:
    return CombatState(
        combatants=list(combatants),
        round_number=1,
        current_turn_index=0,
    )


def _action(
    action_type: ActionType,
    actor: str = "Elara",
    **kwargs: object,
) -> InterpretedAction:
    return InterpretedAction(
        action_type=action_type,
        actor_name=actor,
        raw_input="(test)",
        **kwargs,  # type: ignore[arg-type]
    )


async def _run(
    action: InterpretedAction,
    state: CombatState | None,
) -> tuple[object, ResolveSideChannel]:
    side = ResolveSideChannel()
    outcome = await resolve_mechanics(
        action=action,
        actor_name=action.actor_name,
        location=None,
        npcs={},
        combat_state=state,
        inventory=None,
        session=None,
        campaign_id="test-c2",
        db_factory=None,
        side=side,
    )
    return outcome, side


# ---------------------------------------------------------------------------
# CAST_SPELL
# ---------------------------------------------------------------------------


class TestResolveCastSpell:
    @pytest.mark.asyncio()
    async def test_damage_spell_hits_target_and_consumes_slot(self) -> None:
        wizard, goblin = _wizard(), _goblin(hp=20)
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Magic Missile",
        )

        outcome, side = await _run(action, state)

        damage = 20 - goblin.character.hp
        assert 6 <= damage <= 15  # 3d4+3, no save
        assert wizard.spellcaster is not None
        assert wizard.spellcaster.spell_slots_remaining[1] == 1
        assert wizard.action_budget.action_used is True
        assert "Magic Missile" in outcome.summary
        assert outcome.outcome_facts
        assert outcome.public_effects.hp_delta == {"Goblin": -damage}

    @pytest.mark.asyncio()
    async def test_spell_cast_queues_dice_embed(self) -> None:
        wizard, goblin = _wizard(), _goblin(hp=20)
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Magic Missile",
        )

        _outcome, side = await _run(action, state)

        assert len(side.pending_dice_embeds) == 1
        kind, payload, name = side.pending_dice_embeds[0]
        assert kind == "spell_cast"
        assert isinstance(payload, SpellCastResult)
        assert name == "Elara"
        # The shared dispatcher must render it (visible roll on Discord).
        embed = embed_for_dice_entry(
            side.pending_dice_embeds[0], fallback_actor="Elara",
        )
        assert embed is not None
        assert "Magic Missile" in (embed.title or "") + (embed.description or "")

    @pytest.mark.asyncio()
    async def test_cantrip_consumes_no_slot(self) -> None:
        wizard, goblin = _wizard(), _goblin(hp=50)
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Fire Bolt",
        )

        await _run(action, state)

        assert wizard.spellcaster is not None
        assert wizard.spellcaster.spell_slots_remaining[1] == 2
        assert goblin.character.hp < 50
        assert wizard.action_budget.action_used is True

    @pytest.mark.asyncio()
    async def test_healing_spell_restores_hp(self) -> None:
        wizard = _wizard()
        wizard.character.hp = 1
        goblin = _goblin()
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            spell_name="Cure Wounds",
        )

        outcome, _side = await _run(action, state)

        healed = wizard.character.hp - 1
        assert healed >= 1  # 1d8
        assert wizard.character.hp <= wizard.character.max_hp
        assert outcome.public_effects.hp_delta == {"Elara": healed}
        assert wizard.spellcaster is not None
        assert wizard.spellcaster.spell_slots_remaining[1] == 1

    @pytest.mark.asyncio()
    async def test_overhealing_reports_actual_hp_delta(self) -> None:
        # Missing 1 HP, 1d8 heal: the roll may be up to 8 but the ACTUAL
        # change is +1 — hp_delta must report the clamped value.
        wizard = _wizard()
        wizard.character.hp = wizard.character.max_hp - 1
        state = _combat(wizard, _goblin())
        action = _action(
            ActionType.CAST_SPELL,
            spell_name="Cure Wounds",
        )

        outcome, _side = await _run(action, state)

        assert wizard.character.hp == wizard.character.max_hp
        assert outcome.public_effects.hp_delta == {"Elara": 1}

    @pytest.mark.asyncio()
    async def test_bonus_action_spell_spends_bonus_slot(self) -> None:
        wizard = _wizard()
        wizard.character.hp = 1
        state = _combat(wizard, _goblin())
        action = _action(
            ActionType.CAST_SPELL,
            spell_name="Healing Word",
        )

        await _run(action, state)

        assert wizard.action_budget.action_used is False
        assert wizard.action_budget.bonus_action_used is True

    @pytest.mark.asyncio()
    async def test_lethal_damage_sets_target_defeated(self) -> None:
        wizard, goblin = _wizard(), _goblin(hp=1)
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Magic Missile",
        )

        outcome, _side = await _run(action, state)

        assert goblin.is_alive is False
        assert outcome.target_defeated == "Goblin"

    @pytest.mark.asyncio()
    async def test_unknown_spell_is_a_safe_noop(self) -> None:
        wizard, goblin = _wizard(), _goblin()
        state = _combat(wizard, goblin)
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Méga Explosion",
        )

        outcome, side = await _run(action, state)

        assert wizard.spellcaster is not None
        assert wizard.spellcaster.spell_slots_remaining[1] == 2
        assert wizard.action_budget.action_used is False
        assert side.pending_dice_embeds == []
        assert outcome.summary  # graceful, no crash

    @pytest.mark.asyncio()
    async def test_no_combat_state_falls_back_gracefully(self) -> None:
        action = _action(
            ActionType.CAST_SPELL,
            target_name="Goblin",
            spell_name="Magic Missile",
        )

        outcome, _side = await _run(action, None)

        assert outcome.summary


# ---------------------------------------------------------------------------
# DEFEND
# ---------------------------------------------------------------------------


class TestResolveDefend:
    @pytest.mark.asyncio()
    async def test_defend_consumes_action_and_applies_dodging(self) -> None:
        wizard, goblin = _wizard(), _goblin()
        state = _combat(wizard, goblin)
        action = _action(ActionType.DEFEND)

        outcome, _side = await _run(action, state)

        assert wizard.action_budget.action_used is True
        assert has_condition(wizard.conditions, ConditionType.DODGING)
        assert outcome.outcome_facts  # real state change, narrator gets facts
        assert "performs" not in outcome.summary  # not the generic no-op

    @pytest.mark.asyncio()
    async def test_defend_without_combat_falls_back(self) -> None:
        action = _action(ActionType.DEFEND)

        outcome, _side = await _run(action, None)

        assert outcome.summary


# ---------------------------------------------------------------------------
# DISENGAGE
# ---------------------------------------------------------------------------


class TestResolveDisengage:
    @pytest.mark.asyncio()
    async def test_disengage_consumes_action_and_sets_flag(self) -> None:
        wizard, goblin = _wizard(), _goblin()
        state = _combat(wizard, goblin)
        action = _action(ActionType.DISENGAGE)

        outcome, _side = await _run(action, state)

        assert wizard.action_budget.action_used is True
        assert wizard.action_budget.disengaged_this_turn is True
        assert outcome.outcome_facts
        assert "performs" not in outcome.summary
