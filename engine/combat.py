"""Combat system — initiative, attacks, damage, death saves, turns.

Pure deterministic Python (no LLM).
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from engine.character import Ability, Character, compute_modifier
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    apply_condition,
    auto_fails_str_dex_saves,
    grants_advantage_to_attackers,
    has_condition,
    has_disadvantage_on_attacks,
    remove_condition,
    tick_durations,
)
from engine.dice import RollOutcome, roll, roll_check
from engine.inventory import (
    DamageType,
    Inventory,
    Weapon,
    WeaponCategory,
    WeaponProperty,
)
from engine.spells import (
    Spell,
    SpellcasterState,
    cast_spell,
    compute_spell_dc,
    get_cantrip_damage_dice,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CombatSide(StrEnum):
    """Which side of the encounter a combatant is on."""

    PLAYER = "Player"
    ENEMY = "Enemy"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeathSaves(BaseModel):
    """Tracks death saving throw successes and failures."""

    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)


class Combatant(BaseModel):
    """A participant in combat."""

    name: str = Field(min_length=1)
    side: CombatSide
    character: Character
    inventory: Inventory
    spellcaster: SpellcasterState | None = None
    initiative: int = 0
    conditions: list[ActiveCondition] = Field(default_factory=list)
    death_saves: DeathSaves = Field(default_factory=DeathSaves)
    is_alive: bool = True


class CombatState(BaseModel):
    """The full state of an ongoing combat encounter."""

    combatants: list[Combatant] = Field(default_factory=list)
    round_number: int = Field(default=1, ge=1)
    current_turn_index: int = Field(default=0, ge=0)
    is_active: bool = True


class AttackResult(BaseModel):
    """The outcome of a weapon attack."""

    attacker: str
    defender: str
    weapon_name: str
    attack_roll: int
    attack_total: int
    ac: int
    hit: bool
    critical: bool
    outcome: RollOutcome
    damage: int
    damage_type: DamageType
    defender_hp_remaining: int


class SpellCastResult(BaseModel):
    """The outcome of casting a spell."""

    caster: str
    spell_name: str
    target: str | None = None
    slot_used: int | None = None
    damage: int = 0
    healing: int = 0
    condition_applied: str | None = None
    target_failed_save: bool = True  # True = spell fully effective; False = target saved
    save_outcome: RollOutcome | None = None


class DeathSaveResult(BaseModel):
    """The outcome of a death saving throw."""

    character_name: str
    roll: int
    success: bool
    outcome: RollOutcome
    total_successes: int
    total_failures: int
    stabilized: bool
    died: bool
    revived: bool


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def roll_initiative(combatant: Combatant) -> Combatant:
    """Roll d20 + DEX modifier. Mutates combatant.initiative in place."""
    dex_mod = compute_modifier(combatant.character.ability_scores.get(Ability.DEX))
    result = roll("1d20")
    combatant.initiative = result.total + dex_mod
    return combatant


def start_combat(combatants: list[Combatant]) -> CombatState:
    """Roll initiative for all combatants, sort descending (ties: higher DEX first).

    Returns a new CombatState ready for the first turn.
    """
    for c in combatants:
        roll_initiative(c)

    # Sort by initiative descending, then by DEX score descending as tiebreaker
    combatants.sort(
        key=lambda c: (
            c.initiative,
            c.character.ability_scores.get(Ability.DEX),
        ),
        reverse=True,
    )

    return CombatState(combatants=combatants, round_number=1, current_turn_index=0)


# ---------------------------------------------------------------------------
# Turn management
# ---------------------------------------------------------------------------


def get_current_combatant(state: CombatState) -> Combatant:
    """Return the combatant whose turn it is."""
    return state.combatants[state.current_turn_index]


def advance_turn(state: CombatState) -> CombatState:
    """Advance to the next living combatant.

    Ticks conditions on the combatant who just finished their turn.
    Increments round_number when wrapping past index 0.
    Skips dead combatants. Checks if combat is over.
    Mutates in place, returns state.
    """
    # Tick conditions on the combatant who just finished
    current = state.combatants[state.current_turn_index]
    tick_durations(current.conditions)

    num = len(state.combatants)
    start_index = state.current_turn_index
    next_index = start_index
    round_incremented = False

    # Walk forward through the turn order looking for the next living combatant
    for _ in range(num):
        next_index = (next_index + 1) % num
        # Increment round exactly once when we wrap past index 0
        if next_index <= start_index and not round_incremented:
            state.round_number += 1
            round_incremented = True
        if state.combatants[next_index].is_alive:
            break

    state.current_turn_index = next_index

    # Check if combat is over
    if is_combat_over(state):
        state.is_active = False

    return state


def is_combat_over(state: CombatState) -> bool:
    """True if all combatants on one side are dead (is_alive=False)."""
    players_alive = any(
        c.is_alive for c in state.combatants if c.side == CombatSide.PLAYER
    )
    enemies_alive = any(
        c.is_alive for c in state.combatants if c.side == CombatSide.ENEMY
    )
    return not players_alive or not enemies_alive


# ---------------------------------------------------------------------------
# Attack resolution
# ---------------------------------------------------------------------------


def _is_ranged(weapon: Weapon) -> bool:
    """Check if a weapon is ranged."""
    return weapon.weapon_category in (
        WeaponCategory.SIMPLE_RANGED,
        WeaponCategory.MARTIAL_RANGED,
    )


def _is_finesse(weapon: Weapon) -> bool:
    """Check if a weapon has the finesse property."""
    return WeaponProperty.FINESSE in weapon.properties


def compute_attack_modifier(combatant: Combatant, weapon: Weapon) -> int:
    """Compute attack modifier for a weapon attack.

    STR mod + proficiency for melee, DEX mod + proficiency for ranged.
    Finesse weapons use max(STR, DEX) mod + proficiency.
    """
    scores = combatant.character.ability_scores
    prof = combatant.character.proficiency_bonus

    if _is_finesse(weapon):
        str_mod = compute_modifier(scores.get(Ability.STR))
        dex_mod = compute_modifier(scores.get(Ability.DEX))
        return max(str_mod, dex_mod) + prof

    if _is_ranged(weapon):
        return compute_modifier(scores.get(Ability.DEX)) + prof

    return compute_modifier(scores.get(Ability.STR)) + prof


def compute_damage_modifier(combatant: Combatant, weapon: Weapon) -> int:
    """Compute damage modifier for a weapon attack.

    STR mod for melee, DEX mod for ranged. Finesse: max(STR, DEX) mod.
    """
    scores = combatant.character.ability_scores

    if _is_finesse(weapon):
        str_mod = compute_modifier(scores.get(Ability.STR))
        dex_mod = compute_modifier(scores.get(Ability.DEX))
        return max(str_mod, dex_mod)

    if _is_ranged(weapon):
        return compute_modifier(scores.get(Ability.DEX))

    return compute_modifier(scores.get(Ability.STR))


def _double_dice(dice_expr: str) -> str:
    """Double the number of dice in an expression (SRD crit rule: double dice only).

    '1d8' -> '2d8', '3d4+3' -> '6d4+3'. Only the dice count is doubled;
    flat modifiers are preserved as-is.
    """
    # Split on 'd', then isolate modifier from the die size
    parts = dice_expr.split("d", maxsplit=1)
    num = int(parts[0])
    rest = parts[1]
    # rest might be "8", "4+3", "6-1"
    modifier = ""
    for i, ch in enumerate(rest):
        if ch in ("+", "-"):
            modifier = rest[i:]
            rest = rest[:i]
            break
    return f"{num * 2}d{rest}{modifier}"


def resolve_attack(
    attacker: Combatant,
    defender: Combatant,
    weapon: Weapon,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """Resolve a full weapon attack.

    1. Check conditions for advantage/disadvantage.
    2. Roll d20 (advantage: best of 2, disadvantage: worst of 2).
    3. Nat 20 = crit, nat 1 = auto-miss.
    4. Auto-crit on unconscious/paralyzed defenders.
    5. Roll damage (double dice on crit).
    6. Apply damage to defender.
    """
    # Condition-based advantage/disadvantage
    if has_disadvantage_on_attacks(attacker.conditions):
        disadvantage = True
    if grants_advantage_to_attackers(defender.conditions):
        advantage = True

    # Cancel if both
    if advantage and disadvantage:
        advantage = False
        disadvantage = False

    # Compute modifier and target AC up front
    attack_mod = compute_attack_modifier(attacker, weapon)
    target_ac = defender.character.ac
    expr = f"1d20+{attack_mod}" if attack_mod >= 0 else f"1d20{attack_mod}"

    # Roll d20 check(s) against AC
    check1 = roll_check(expr, target_ac)
    if advantage or disadvantage:
        check2 = roll_check(expr, target_ac)
        if advantage:
            check = check1 if check1.total >= check2.total else check2
        else:
            check = check1 if check1.total <= check2.total else check2
    else:
        check = check1

    raw_roll = check.rolls[0]
    outcome = check.outcome
    critical = outcome == RollOutcome.CRITICAL_SUCCESS

    # Auto-crit on unconscious or paralyzed defenders
    if has_condition(defender.conditions, ConditionType.UNCONSCIOUS) or has_condition(
        defender.conditions, ConditionType.PARALYZED
    ):
        critical = True
        if outcome != RollOutcome.CRITICAL_FAILURE:
            outcome = RollOutcome.CRITICAL_SUCCESS

    # Determine hit
    if raw_roll == 1:
        hit = False
    elif critical:
        hit = True
    else:
        hit = check.margin >= 0

    # Damage
    damage = 0
    if hit:
        damage_dice = weapon.damage_dice
        if critical:
            damage_dice = _double_dice(damage_dice)
        damage_roll = roll(damage_dice)
        damage = max(0, damage_roll.total + compute_damage_modifier(attacker, weapon))

    # Apply damage
    if damage > 0:
        apply_damage(defender, damage)

    return AttackResult(
        attacker=attacker.name,
        defender=defender.name,
        weapon_name=weapon.name,
        attack_roll=raw_roll,
        attack_total=check.total,
        ac=target_ac,
        hit=hit,
        critical=critical,
        outcome=outcome,
        damage=damage,
        damage_type=weapon.damage_type,
        defender_hp_remaining=defender.character.hp,
    )


# ---------------------------------------------------------------------------
# Spell resolution
# ---------------------------------------------------------------------------


def resolve_spell(
    caster: Combatant,
    spell: Spell,
    target: Combatant | None = None,
    slot_level: int | None = None,
) -> SpellCastResult:
    """Resolve a spell cast.

    1. Consume spell slot via cast_spell.
    2. Handle damage (with saving throw if applicable).
    3. Handle healing.
    4. Apply conditions.
    """
    if caster.spellcaster is None:
        raise ValueError(f"{caster.name} is not a spellcaster")

    # Consume the slot
    cast_spell(caster.spellcaster, spell, slot_level)

    actual_slot = slot_level if spell.level > 0 else None
    if actual_slot is None and spell.level > 0:
        actual_slot = spell.level

    damage = 0
    healing = 0
    condition_name: str | None = None
    target_failed_save = True  # True = spell fully effective
    save_outcome: RollOutcome | None = None

    # Determine effective dice (handle upcasting via higher_level_dice)
    base_dice = spell.damage_dice

    # Damage
    if base_dice is not None:
        if spell.level == 0:
            dice_expr = get_cantrip_damage_dice(spell, caster.character.level)
        else:
            dice_expr = base_dice
            # Upcast: add extra dice per slot level above base
            effective_slot = slot_level if slot_level is not None else spell.level
            extra_levels = effective_slot - spell.level
            if extra_levels > 0 and spell.higher_level_dice is not None:
                extra_dice = spell.higher_level_dice
                # Parse "1d6" → add N copies of the extra die
                e_parts = extra_dice.split("d")
                e_count = int(e_parts[0]) * extra_levels
                extra_expr = f"{e_count}d{e_parts[1]}"
                # Roll base + extra separately and sum
                base_result = roll(dice_expr)
                extra_result = roll(extra_expr)
                damage = base_result.total + extra_result.total
                dice_expr = None  # signal that we already rolled

        if dice_expr is not None:
            damage_result = roll(dice_expr)
            damage = damage_result.total

        # Saving throw
        if spell.saving_throw is not None and target is not None:
            ability_score = caster.character.ability_scores.get(
                caster.spellcaster.spellcasting_ability
            )
            dc = compute_spell_dc(ability_score, caster.character.proficiency_bonus)

            # Target save roll
            save_ability = spell.saving_throw
            save_score = target.character.ability_scores.get(save_ability)
            save_mod = compute_modifier(save_score)

            # Check save proficiency
            if save_ability in target.character.saving_throw_proficiencies:
                save_mod += target.character.proficiency_bonus

            # Auto-fail STR/DEX saves if conditions dictate
            if (
                save_ability in (Ability.STR, Ability.DEX)
                and auto_fails_str_dex_saves(target.conditions)
            ):
                save_total = 0  # auto-fail
                save_outcome = RollOutcome.FAILURE
            else:
                save_expr = (
                    f"1d20+{save_mod}" if save_mod >= 0 else f"1d20{save_mod}"
                )
                save_check = roll_check(save_expr, dc)
                save_total = save_check.total
                save_outcome = save_check.outcome

            if save_total >= dc:
                damage = damage // 2
                target_failed_save = False

        if target is not None and damage > 0:
            apply_damage(target, damage)

    # Healing
    if spell.healing_dice is not None:
        heal_result = roll(spell.healing_dice)
        healing = heal_result.total

        heal_target = target if target is not None else caster
        apply_healing(heal_target, healing)

    # Condition (only applied if target failed save)
    if spell.condition_applied is not None and target is not None and target_failed_save:
        cond_type = ConditionType(spell.condition_applied)
        active_cond = ActiveCondition(
            condition_type=cond_type,
            source=spell.name,
            duration_rounds=spell.duration_rounds,
        )
        apply_condition(target.conditions, active_cond)
        condition_name = spell.condition_applied

    return SpellCastResult(
        caster=caster.name,
        spell_name=spell.name,
        target=target.name if target is not None else None,
        slot_used=actual_slot,
        damage=damage,
        healing=healing,
        condition_applied=condition_name,
        target_failed_save=target_failed_save,
        save_outcome=save_outcome,
    )


# ---------------------------------------------------------------------------
# Death saves
# ---------------------------------------------------------------------------


def resolve_death_save(combatant: Combatant) -> DeathSaveResult:
    """Roll a death saving throw for a downed combatant.

    - Nat 1: 2 failures.
    - Nat 20: revive at 1 HP (reset death saves, remove unconscious).
    - 10+: 1 success.
    - <10: 1 failure.
    - 3 successes: stabilized.
    - 3 failures: dead (is_alive=False).
    Mutates combatant in place.
    """
    check = roll_check("1d20", dc=10)
    raw = check.rolls[0]
    outcome = check.outcome

    stabilized = False
    died = False
    revived = False

    if outcome == RollOutcome.CRITICAL_FAILURE:
        combatant.death_saves.failures = min(3, combatant.death_saves.failures + 2)
        is_success = False
    elif outcome == RollOutcome.CRITICAL_SUCCESS:
        # Revive at 1 HP
        combatant.character.hp = 1
        combatant.death_saves = DeathSaves()
        if has_condition(combatant.conditions, ConditionType.UNCONSCIOUS):
            remove_condition(combatant.conditions, ConditionType.UNCONSCIOUS)
        revived = True
        is_success = True
    elif raw >= 10:
        combatant.death_saves.successes = min(3, combatant.death_saves.successes + 1)
        is_success = True
    else:
        combatant.death_saves.failures = min(3, combatant.death_saves.failures + 1)
        is_success = False

    # Check for stabilized / died
    if combatant.death_saves.successes >= 3:
        stabilized = True
    if combatant.death_saves.failures >= 3:
        died = True
        combatant.is_alive = False

    return DeathSaveResult(
        character_name=combatant.name,
        roll=raw,
        success=is_success,
        outcome=outcome,
        total_successes=combatant.death_saves.successes,
        total_failures=combatant.death_saves.failures,
        stabilized=stabilized,
        died=died,
        revived=revived,
    )


# ---------------------------------------------------------------------------
# Damage / healing
# ---------------------------------------------------------------------------


def apply_damage(combatant: Combatant, damage: int) -> Combatant:
    """Subtract damage from HP, clamped at 0.

    If HP reaches 0:
    - PLAYER: set unconscious, reset death saves.
    - ENEMY: is_alive=False (instant death).
    Mutates in place.
    """
    combatant.character.hp = max(0, combatant.character.hp - damage)

    if combatant.character.hp == 0 and combatant.is_alive:
        if combatant.side == CombatSide.PLAYER:
            apply_condition(
                combatant.conditions,
                ActiveCondition(
                    condition_type=ConditionType.UNCONSCIOUS,
                    source="damage",
                ),
            )
            combatant.death_saves = DeathSaves()
        else:
            combatant.is_alive = False

    return combatant


def apply_healing(combatant: Combatant, healing: int) -> Combatant:
    """Add healing to HP, capped at max_hp.

    If combatant was at 0 HP (unconscious): remove unconscious, reset death saves.
    Mutates in place.
    """
    was_at_zero = combatant.character.hp == 0

    combatant.character.hp = min(
        combatant.character.max_hp,
        combatant.character.hp + healing,
    )

    if was_at_zero and combatant.character.hp > 0:
        if has_condition(combatant.conditions, ConditionType.UNCONSCIOUS):
            remove_condition(combatant.conditions, ConditionType.UNCONSCIOUS)
        combatant.death_saves = DeathSaves()

    return combatant
