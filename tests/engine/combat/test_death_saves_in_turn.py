"""Death saves must actually be rolled, and a downed party must lose.

Before this, ``resolve_death_save`` had no production caller: a PC at 0 HP
went UNCONSCIOUS and stayed there forever. ``check_combat_end`` still
counted them as standing, so the encounter never ended — and since the turn
watcher waits indefinitely for the player (design decision of 2026-07-19),
a solo player who went down had their campaign permanently stuck.
"""

from __future__ import annotations

from unittest.mock import patch

from engine.character import AbilityScores, CharacterClass, Race, create_character
from engine.combat import (
    CombatEndReason,
    CombatSide,
    CombatState,
    Combatant,
    advance_turn,
    check_combat_end,
    is_downed,
)
from engine.conditions import ConditionType, has_condition
from engine.dice import D20CheckResult, RollOutcome
from engine.inventory import create_inventory

SCORES = AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=10, CHA=8)


def _combatant(name: str, side: CombatSide, initiative: int) -> Combatant:
    pc = create_character(
        name=name, race=Race.DWARF, char_class=CharacterClass.FIGHTER,
        ability_scores=SCORES,
    )
    return Combatant(
        name=name, side=side, character=pc,
        inventory=create_inventory(), initiative=initiative,
    )


def _duel() -> tuple[CombatState, Combatant, Combatant]:
    """PC at index 0 (acts first), enemy at index 1."""
    hero = _combatant("Thorin", CombatSide.PLAYER, 20)
    foe = _combatant("Brigand", CombatSide.ENEMY, 10)
    state = CombatState(combatants=[hero, foe], current_turn_index=0, round_number=1)
    return state, hero, foe


def _down(combatant: Combatant) -> None:
    """Put a combatant at 0 HP the way ``apply_damage`` does."""
    from engine.conditions import ActiveCondition, apply_condition

    combatant.character.hp = 0
    apply_condition(
        combatant.conditions,
        ActiveCondition(condition_type=ConditionType.UNCONSCIOUS, source="damage"),
    )


def _fixed_d20(raw: int) -> D20CheckResult:
    """A deterministic d20 check result for patching ``roll_check``."""
    if raw == 1:
        outcome = RollOutcome.CRITICAL_FAILURE
    elif raw == 20:
        outcome = RollOutcome.CRITICAL_SUCCESS
    elif raw >= 10:
        outcome = RollOutcome.SUCCESS
    else:
        outcome = RollOutcome.FAILURE
    return D20CheckResult(
        expression="1d20", rolls=[raw], total=raw,
        dc=10, outcome=outcome, margin=raw - 10,
    )


class TestIsDowned:
    def test_healthy_pc_is_not_downed(self) -> None:
        _, hero, _ = _duel()
        assert not is_downed(hero)

    def test_pc_at_zero_hp_is_downed(self) -> None:
        _, hero, _ = _duel()
        _down(hero)
        assert is_downed(hero)

    def test_stabilized_pc_is_no_longer_rolling(self) -> None:
        """3 successes = stable: still at 0 HP, but done making saves."""
        _, hero, _ = _duel()
        _down(hero)
        hero.death_saves.successes = 3
        assert not is_downed(hero)

    def test_dead_pc_is_not_downed(self) -> None:
        _, hero, _ = _duel()
        _down(hero)
        hero.is_alive = False
        assert not is_downed(hero)

    def test_npc_at_zero_hp_is_not_downed(self) -> None:
        """NPCs die outright — only PCs make death saves."""
        _, _, foe = _duel()
        _down(foe)
        assert not is_downed(foe)


class TestCombatEndsWhenThePartyIsDown:
    def test_downed_pc_does_not_count_as_standing(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        assert check_combat_end(state) == CombatEndReason.DEFEAT

    def test_stabilized_pc_does_not_count_as_standing(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        hero.death_saves.successes = 3
        assert check_combat_end(state) == CombatEndReason.DEFEAT

    def test_a_conscious_pc_still_holds_the_line(self) -> None:
        hero = _combatant("Thorin", CombatSide.PLAYER, 20)
        ally = _combatant("Elara", CombatSide.PLAYER, 15)
        foe = _combatant("Brigand", CombatSide.ENEMY, 10)
        state = CombatState(combatants=[hero, ally, foe], current_turn_index=0)
        _down(hero)
        assert check_combat_end(state) is None


class TestDeathSaveRolledOnTurnStart:
    def test_downed_pc_rolls_at_the_start_of_their_turn(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        state.current_turn_index = 1  # enemy acts, then it is the PC's turn

        with patch("engine.combat.roll_check", return_value=_fixed_d20(15)):
            advance_turn(state)

        assert hero.death_saves.successes == 1
        assert hero.death_saves.failures == 0

    def test_the_result_is_queued_for_the_bot(self) -> None:
        """Mirrors pending_legendary_summaries — the TurnManager surfaces it."""
        state, hero, _ = _duel()
        _down(hero)
        state.current_turn_index = 1

        with patch("engine.combat.roll_check", return_value=_fixed_d20(15)):
            advance_turn(state)

        assert len(state.pending_death_saves) == 1
        assert state.pending_death_saves[0].character_name == "Thorin"
        assert state.pending_death_saves[0].success is True

    def test_downed_pc_does_not_get_a_playable_turn(self) -> None:
        """An unconscious character cannot act — never park the turn on them."""
        hero = _combatant("Thorin", CombatSide.PLAYER, 20)
        ally = _combatant("Elara", CombatSide.PLAYER, 15)
        foe = _combatant("Brigand", CombatSide.ENEMY, 10)
        state = CombatState(combatants=[hero, ally, foe], current_turn_index=2)
        _down(hero)

        with patch("engine.combat.roll_check", return_value=_fixed_d20(15)):
            advance_turn(state)

        assert state.combatants[state.current_turn_index].name == "Elara"
        assert hero.death_saves.successes == 1, "the save was still rolled"

    def test_three_failures_kill(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        hero.death_saves.failures = 2
        state.current_turn_index = 1

        with patch("engine.combat.roll_check", return_value=_fixed_d20(5)):
            advance_turn(state)

        assert hero.death_saves.failures == 3
        assert hero.is_alive is False
        assert state.pending_death_saves[0].died is True

    def test_nat_20_revives_and_the_pc_gets_their_turn(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        state.current_turn_index = 1

        with patch("engine.combat.roll_check", return_value=_fixed_d20(20)):
            advance_turn(state)

        assert hero.character.hp == 1
        assert not has_condition(hero.conditions, ConditionType.UNCONSCIOUS)
        assert state.combatants[state.current_turn_index].name == "Thorin"
        assert state.pending_death_saves[0].revived is True

    def test_stabilized_pc_stops_rolling(self) -> None:
        hero = _combatant("Thorin", CombatSide.PLAYER, 20)
        ally = _combatant("Elara", CombatSide.PLAYER, 15)
        foe = _combatant("Brigand", CombatSide.ENEMY, 10)
        state = CombatState(combatants=[hero, ally, foe], current_turn_index=2)
        _down(hero)
        hero.death_saves.successes = 3

        with patch("engine.combat.roll_check", return_value=_fixed_d20(5)) as rolled:
            advance_turn(state)

        assert rolled.call_count == 0
        assert hero.death_saves.failures == 0

    def test_conscious_pc_never_rolls(self) -> None:
        state, _, _ = _duel()
        state.current_turn_index = 1

        with patch("engine.combat.roll_check", return_value=_fixed_d20(5)) as rolled:
            advance_turn(state)

        assert rolled.call_count == 0
        assert state.pending_death_saves == []

    def test_last_pc_dying_on_their_save_ends_the_combat(self) -> None:
        state, hero, _ = _duel()
        _down(hero)
        hero.death_saves.failures = 2
        state.current_turn_index = 1

        with patch("engine.combat.roll_check", return_value=_fixed_d20(5)):
            advance_turn(state)

        assert state.is_active is False
        assert state.end_reason == CombatEndReason.DEFEAT
