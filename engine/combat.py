"""Combat system — initiative, attacks, damage, death saves, turns.

Pure deterministic Python (no LLM).
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, PrivateAttr

from engine.character import Ability, Character, compute_modifier
from engine.combat_trigger import CombatTrigger, InitiativeSide
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    apply_condition,
    auto_fails_str_dex_saves,
    check_concentration_save,
    consume_surprise_if_present,
    drop_concentration,
    grants_advantage_to_attackers,
    has_condition,
    has_disadvantage_on_attacks,
    is_concentrating,
    remove_condition,
    tick_durations,
)
from engine.dice import RollOutcome, parse_dice, roll, roll_check
from engine.inventory import (
    DamageType,
    EquipmentSlot,
    Inventory,
    Weapon,
    WeaponCategory,
    WeaponProperty,
)
from engine.npc_stat_block import NPCAttack, NPCStatBlock, NPCTier
from world.combat_zone import ZoneTag
from world.npc import NPC
from engine.spells import (
    Spell,
    SpellcasterState,
    cast_spell,
    compute_spell_dc,
    get_cantrip_damage_dice,
)

if TYPE_CHECKING:
    from world.location import Location


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CombatSide(StrEnum):
    """Which side of the encounter a combatant is on."""

    PLAYER = "Player"
    ENEMY = "Enemy"


class CombatEndReason(StrEnum):
    """Why a combat encounter ended.

    The reason is set on ``CombatState.end_reason`` when :func:`check_combat_end`
    detects a terminal condition. ``FLED`` is populated by the flee resolver in
    :mod:`bot.action_pipeline`; ``TRUCE`` is populated by
    :mod:`bot.combat_truce` on social de-escalation success.
    """

    VICTORY = "victory"
    DEFEAT = "defeat"
    FLED = "fled"
    TRUCE = "truce"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class DeathSaves(BaseModel):
    """Tracks death saving throw successes and failures."""

    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)


class ActionBudget(BaseModel):
    """Per-turn 5e action economy budget for a combatant.

    Tracks the Move + Action + Bonus Action pools for the active turn and
    the per-round Reaction slot. ``consume_*`` helpers are the canonical
    way to spend a slot — they raise ``ValueError`` if the slot is
    already used or if movement is insufficient. ``reset_for_new_turn``
    refills the Move/Action/Bonus pools without touching the Reaction
    slot (which lives across turns and is reset at round boundaries by
    :func:`advance_turn`).
    """

    movement_remaining_feet: int = Field(default=30, ge=0)
    action_used: bool = False
    bonus_action_used: bool = False
    reaction_used_this_round: bool = False
    disengaged_this_turn: bool = False
    """Set by the Disengage action (task 24). Suppresses opportunity attacks
    for the remainder of this turn."""

    def reset_for_new_turn(self, base_speed_feet: int) -> None:
        """Refill Move/Action/Bonus pools. Reaction persists across turns."""
        self.movement_remaining_feet = base_speed_feet
        self.action_used = False
        self.bonus_action_used = False
        self.disengaged_this_turn = False


class PhaseTransitionEvent(BaseModel):
    """Queued narrator cue for a boss-NPC phase transition.

    :func:`engine.combat_phases.check_phase_transition` populates this list
    when an NPC's HP drops past one of its ``PhaseTransition`` thresholds;
    the narrator consumes the list to weave the phase cue into the next
    round's narration.
    """

    combatant_name: str = Field(min_length=1)
    phase_index: int = Field(default=0, ge=0)
    narrative_cue: str = ""
    consumed: bool = False
    """True once the dedicated phase-transition narrator path has produced a
    narration for this event. Orthogonal to the mechanical effect, which is
    applied in :func:`engine.combat_phases.check_phase_transition` as soon as
    the HP threshold is crossed. Setting ``consumed=True`` prevents double
    narration on action retries."""


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
    stat_block: NPCStatBlock | None = None
    """Set for ENEMY combatants derived from an NPC with a stat block.
    ``None`` for PCs and for legacy NPCs without a stat block (they fall
    back on ``character`` + ``inventory`` for attack resolution)."""
    fled: bool = False
    """Set by task 32 flee resolution. Treated as non-alive by
    :func:`check_combat_end`."""
    current_zone: str | None = None
    """Name of the zone the combatant currently occupies. ``None`` for
    zone-less combats (legacy encounters without a spatial layout)."""
    action_budget: ActionBudget = Field(default_factory=ActionBudget)
    legendary_points_remaining: int = Field(default=0, ge=0)
    """Boss-tier legendary action pool. Refilled at the boss's turn start
    by task 53."""
    phase_save_bonus: int = Field(default=0, ge=0)
    """Cumulative saving-throw bonus from phase transitions (task 54)."""


class CombatState(BaseModel):
    """The full state of an ongoing combat encounter."""

    combat_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    combatants: list[Combatant] = Field(default_factory=list)
    round_number: int = Field(default=1, ge=1)
    current_turn_index: int = Field(default=0, ge=0)
    is_active: bool = True
    end_reason: CombatEndReason | None = None
    pending_phase_narrations: list[PhaseTransitionEvent] = Field(default_factory=list)
    pending_legendary_summaries: list[str] = Field(default_factory=list)
    """Off-turn legendary action summaries accumulated during ``advance_turn``.
    Populated by task 53's ``maybe_spend_legendary_action`` hook and consumed
    by the TurnManager (task 64) so the player sees what the boss did in
    between everyone else's turns."""
    recent_events: list[str] = Field(default_factory=list)
    """Short narration-hint strings for the last few mechanical happenings.
    The bot appends entries via :func:`record_combat_event` after each combat
    resolution; the narrator reads the tail of the list to ground its prose.
    Capped at :data:`RECENT_EVENTS_CAP` to bound serialization size. The engine
    never reads this list — it is purely for downstream narration."""

    _finalized: bool = PrivateAttr(default=False)
    """Idempotence guard for :func:`bot.combat_end.finalize_combat`. Set to
    ``True`` on first run. Subsequent calls short-circuit and return a summary
    reconstructed from the frozen state without re-applying XP, condition
    cleanup, or other side effects.

    Private attribute (not serialized) because the guard only matters for
    the lifetime of a process — a reloaded ``CombatState`` with
    ``is_active=False`` is definitionally already finalized on disk."""


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
# Narration event log (task 70)
# ---------------------------------------------------------------------------


RECENT_EVENTS_CAP: int = 12
"""Maximum number of narration hints retained on :attr:`CombatState.recent_events`.

The narrator only reads the tail of the list (last 3 entries), so any size
above that is just headroom. The cap keeps ``combat_state_json`` bounded so a
long fight does not grow the persisted blob unboundedly."""


def record_combat_event(state: CombatState, event_text: str) -> None:
    """Append a short narration hint to ``state.recent_events`` with a ring cap.

    Bot layer helper — the engine never calls this itself. The wording is
    the bot's responsibility; the engine only owns the field shape. The
    list is trimmed to the last :data:`RECENT_EVENTS_CAP` entries on every
    append so that ``CombatState`` serialization stays bounded during long
    encounters.
    """
    state.recent_events.append(event_text)
    if len(state.recent_events) > RECENT_EVENTS_CAP:
        state.recent_events = state.recent_events[-RECENT_EVENTS_CAP:]


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def roll_initiative(combatant: Combatant) -> Combatant:
    """Roll d20 + DEX modifier. Mutates combatant.initiative in place."""
    dex_mod = compute_modifier(combatant.character.ability_scores.get(Ability.DEX))
    result = roll("1d20")
    combatant.initiative = result.total + dex_mod
    return combatant


def start_combat(
    combatants: list[Combatant],
    trigger: CombatTrigger | None = None,
) -> CombatState:
    """Roll initiative and build a :class:`CombatState`.

    With no ``trigger`` (or ``trigger.surprise_side == BOTH_READY``) this
    rolls ``1d20 + DEX`` for everyone and sorts by initiative — the legacy
    behaviour, preserved for regression tests.

    With a ``PLAYERS``-surprise trigger, the named aggressor acts first
    and every NPC enemy listed on the trigger receives the SURPRISED
    condition (5e case 1). With a ``NPCS``-surprise trigger, the NPC
    ambushers go first and every PC is SURPRISED (case 2). In all cases
    ``Combatant.initiative`` is populated so the turn order can be
    persisted and reconstructed after a restart.
    """
    for c in combatants:
        roll_initiative(c)

    if trigger is None or trigger.surprise_side == InitiativeSide.BOTH_READY:
        ordered = _sort_by_initiative(combatants)
    elif trigger.surprise_side == InitiativeSide.PLAYERS:
        ordered = _order_player_surprise(combatants, trigger)
        _apply_surprise_to_enemies(combatants, trigger)
    elif trigger.surprise_side == InitiativeSide.NPCS:
        ordered = _order_npc_surprise(combatants, trigger)
        _apply_surprise_to_players(combatants)
    else:  # pragma: no cover — StrEnum exhaustiveness guard
        raise ValueError(f"Unknown surprise_side: {trigger.surprise_side}")

    # Initialise each combatant's action budget so the first combatant is
    # ready to act on turn 1.
    for c in ordered:
        c.action_budget.reset_for_new_turn(max(c.character.speed, 0))
        c.action_budget.reaction_used_this_round = False

    return CombatState(combatants=ordered, round_number=1, current_turn_index=0)


def _sort_by_initiative(combatants: list[Combatant]) -> list[Combatant]:
    """Return combatants sorted by initiative desc, DEX score tiebreak (5e RAW)."""
    return sorted(
        combatants,
        key=lambda c: (
            c.initiative,
            c.character.ability_scores.get(Ability.DEX),
        ),
        reverse=True,
    )


def _order_player_surprise(
    combatants: list[Combatant],
    trigger: CombatTrigger,
) -> list[Combatant]:
    """Place the aggressor PC first, then standard initiative for the rest."""
    aggressor = [c for c in combatants if c.name == trigger.aggressor_name]
    others = [c for c in combatants if c.name != trigger.aggressor_name]
    return aggressor + _sort_by_initiative(others)


def _order_npc_surprise(
    combatants: list[Combatant],
    trigger: CombatTrigger,
) -> list[Combatant]:
    """Place ambusher NPCs first (sorted among themselves), then the rest."""
    ambushers = [c for c in combatants if c.name in trigger.enemy_names]
    others = [c for c in combatants if c.name not in trigger.enemy_names]
    return _sort_by_initiative(ambushers) + _sort_by_initiative(others)


def _apply_surprise_to_enemies(
    combatants: list[Combatant],
    trigger: CombatTrigger,
) -> None:
    """Apply SURPRISED to the ENEMY-side combatants named in the trigger."""
    for c in combatants:
        if c.side == CombatSide.ENEMY and c.name in trigger.enemy_names:
            apply_condition(
                c.conditions,
                ActiveCondition(
                    condition_type=ConditionType.SURPRISED,
                    source="combat_entry",
                ),
            )


def _apply_surprise_to_players(combatants: list[Combatant]) -> None:
    """Apply SURPRISED to every PLAYER-side combatant (ambush case)."""
    for c in combatants:
        if c.side == CombatSide.PLAYER:
            apply_condition(
                c.conditions,
                ActiveCondition(
                    condition_type=ConditionType.SURPRISED,
                    source="combat_entry",
                ),
            )


# ---------------------------------------------------------------------------
# Turn management
# ---------------------------------------------------------------------------


def get_current_combatant(state: CombatState) -> Combatant:
    """Return the combatant whose turn it is."""
    return state.combatants[state.current_turn_index]


def advance_turn(state: CombatState) -> CombatState:
    """Advance to the next eligible combatant.

    Ticks condition durations on the combatant who just finished their
    turn, consumes their SURPRISED condition if present (a surprised
    combatant's no-op turn has now elapsed), fires off-turn legendary
    actions for any living boss (task 53), walks forward to the next
    alive-and-not-fled combatant, increments ``round_number`` at the
    wrap, resets the incoming combatant's action budget (refilling
    legendary points too if the incoming combatant is a boss), refills
    reactions on round wrap, and sets ``is_active=False`` / ``end_reason``
    via :func:`check_combat_end` if the encounter should stop.

    Mutates in place and returns ``state``.
    """
    num = len(state.combatants)
    if num == 0:
        state.is_active = False
        return state

    # 1. Tick conditions on the combatant who just finished their turn,
    #    and consume SURPRISED if it was applied. Surprise is a one-turn
    #    no-op: the validator rejects their actions, then at turn end the
    #    condition is cleared so round 2 plays normally.
    current = state.combatants[state.current_turn_index]
    tick_durations(current.conditions)
    consume_surprise_if_present(current.conditions)

    # 1b. Off-turn legendary actions (task 53). After a PC's turn, every
    #     living boss NPC gets a chance to spend its legendary points.
    #     Summaries are queued on ``state.pending_legendary_summaries``
    #     for the TurnManager (task 64) to surface to the player.
    if current.is_alive and current.side == CombatSide.PLAYER:
        from engine.npc_ai.legendary import maybe_spend_legendary_action

        for boss in state.combatants:
            if boss.name == current.name:
                continue
            if boss.side == current.side:
                continue
            summary = maybe_spend_legendary_action(state, boss, current)
            if summary is not None:
                state.pending_legendary_summaries.append(summary)

    # 2. Walk forward to the next eligible combatant. A combatant is
    #    eligible if it is alive AND has not fled. Skip dead/fled slots
    #    silently. Increment round_number when we cross index 0.
    start_index = state.current_turn_index
    next_index = start_index
    round_incremented = False

    for _ in range(num):
        prev_index = next_index
        next_index = (next_index + 1) % num
        if next_index <= prev_index and not round_incremented:
            state.round_number += 1
            round_incremented = True
        candidate = state.combatants[next_index]
        if candidate.is_alive and not candidate.fled:
            break

    state.current_turn_index = next_index

    # 3. Reset reactions for everyone on round wrap.
    if round_incremented:
        for c in state.combatants:
            c.action_budget.reaction_used_this_round = False

    # 4. Reset the incoming combatant's turn pool (Move/Action/Bonus) and
    #    refill legendary points if the incoming combatant is a boss
    #    (task 53 — 5e RAW: reset at the start of the boss's own turn).
    new_current = state.combatants[next_index]
    new_current.action_budget.reset_for_new_turn(max(new_current.character.speed, 0))
    if (
        new_current.stat_block is not None
        and new_current.stat_block.tier == NPCTier.BOSS
    ):
        new_current.legendary_points_remaining = (
            new_current.stat_block.legendary_points_per_round
        )

    # 5. Check for end of combat.
    end = check_combat_end(state)
    if end is not None:
        state.is_active = False
        state.end_reason = end

    return state


def check_combat_end(state: CombatState) -> CombatEndReason | None:
    """Return the reason combat should end, or ``None`` if it continues.

    Victory when every ENEMY combatant is dead OR has fled; defeat when
    every PC combatant is dead OR has fled. Fled/dead combatants count as
    out of the fight for both sides. The flee and truce paths may override
    the result explicitly by setting ``state.end_reason`` before calling
    :func:`bot.combat_end.finalize_combat`.
    """
    players_standing = [
        c for c in state.combatants
        if c.side == CombatSide.PLAYER and c.is_alive and not c.fled
    ]
    enemies_standing = [
        c for c in state.combatants
        if c.side == CombatSide.ENEMY and c.is_alive and not c.fled
    ]

    if not enemies_standing and not players_standing:
        # Mutual wipe — rare but possible. Call it a defeat from the
        # PCs' point of view; the Discord layer can phrase it however.
        return CombatEndReason.DEFEAT
    if not enemies_standing:
        return CombatEndReason.VICTORY
    if not players_standing:
        # If every *alive* PC fled (not died), flag FLED instead of DEFEAT.
        # Dead PCs have fled=False, so checking all players would incorrectly
        # return DEFEAT when some died and the rest successfully fled.
        alive_pcs = [
            c for c in state.combatants
            if c.side == CombatSide.PLAYER and c.is_alive
        ]
        if alive_pcs and all(c.fled for c in alive_pcs):
            return CombatEndReason.FLED
        return CombatEndReason.DEFEAT
    return None


def is_combat_over(state: CombatState) -> bool:
    """True if :func:`check_combat_end` would set an end reason.

    Kept as a thin wrapper for backwards compatibility with legacy
    callers that only need a boolean.
    """
    return check_combat_end(state) is not None


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
    count, sides, modifier = parse_dice(dice_expr)
    mod_str = f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else "")
    return f"{count * 2}d{sides}{mod_str}"


def resolve_attack(
    attacker: Combatant,
    defender: Combatant,
    weapon: Weapon,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """Resolve a full weapon attack. Mutates defender HP in place. Returns AttackResult.

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
    """Resolve a spell cast. Mutates caster/target state in place. Returns SpellCastResult.

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
                # Parse extra dice using canonical parser and scale by upcast levels
                e_count, e_sides, e_mod = parse_dice(spell.higher_level_dice)
                e_count *= extra_levels
                mod_str = f"+{e_mod}" if e_mod > 0 else (str(e_mod) if e_mod < 0 else "")
                extra_expr = f"{e_count}d{e_sides}{mod_str}"
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


def apply_damage(
    combatant: Combatant,
    damage: int,
    state: "CombatState | None" = None,
) -> Combatant:
    """Subtract damage from HP, clamped at 0.

    If HP reaches 0:
    - PLAYER: set unconscious, reset death saves.
    - ENEMY: is_alive=False (instant death).

    Additional hooks:

    - **Concentration (task 22)** — if the combatant was concentrating
      on a spell, a CON save is rolled via :func:`_on_damage_taken` and
      the concentration condition is dropped on failure.
    - **Phase transitions (task 54)** — if the combatant has a stat
      block with phases, :func:`engine.combat_phases.check_phase_transition`
      runs and applies any crossed-threshold bonuses in place. When a
      ``state`` is supplied, the newly-triggered phases also emit a
      :class:`PhaseTransitionEvent` on ``state.pending_phase_narrations``
      so task 71 (narrator) can weave in the ``narrative_cue``.

    Mutates in place.
    """
    if damage <= 0:
        return combatant

    combatant.character.hp = max(0, combatant.character.hp - damage)

    # Concentration save hook — runs BEFORE death transitions so that a
    # caster who drops to 0 HP still loses concentration the same way a
    # still-standing caster would.
    _on_damage_taken(combatant, damage)

    # Phase transition hook (task 54). Always mutates the stat block so
    # the mechanical bonuses take effect immediately; only appends to
    # ``state.pending_phase_narrations`` when the caller supplies state.
    from engine.combat_phases import check_phase_transition

    triggered = check_phase_transition(combatant)
    if triggered and state is not None:
        for idx, phase in enumerate(triggered):
            # ``phase_index`` is the position within the just-triggered
            # batch; upstream narrator task 71 only needs the cue.
            state.pending_phase_narrations.append(
                PhaseTransitionEvent(
                    combatant_name=combatant.name,
                    phase_index=idx,
                    narrative_cue=phase.narrative_cue,
                )
            )

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


def _on_damage_taken(combatant: Combatant, damage: int) -> None:
    """Concentration hook fired whenever a combatant loses HP.

    Rolls a CON save (DC = max(10, damage // 2)) and drops the
    ``CONCENTRATING`` condition on failure. Callers that inflict damage
    through means other than :func:`apply_damage` should call this hook
    themselves; callers that use :func:`apply_damage` get it for free.

    Idempotent when the combatant is not concentrating — exits early.
    """
    if damage <= 0:
        return
    if not is_concentrating(combatant.conditions):
        return
    save = check_concentration_save(combatant, damage)
    if save.outcome in (RollOutcome.FAILURE, RollOutcome.CRITICAL_FAILURE):
        drop_concentration(combatant)


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


# ---------------------------------------------------------------------------
# Trivial NPC resolution (Lot E) — auto-resolve overwhelming attacks against
# defenseless NPCs without spinning up a CombatState. One attack, one damage
# roll, possibly one death.
# ---------------------------------------------------------------------------


class TrivialResolveResult(BaseModel):
    """Outcome of a trivial NPC resolution (one swing, no rounds)."""

    hit: bool
    damage: int
    target_killed: bool
    description: str


def trivial_resolve(
    attacker: Character,
    target_npc: NPC,
    weapon: Weapon | None = None,
) -> TrivialResolveResult:
    """Auto-resolve an attack against a defenseless NPC. Mutates target_npc.hp in place.

    Assumes the attacker has overwhelming advantage. Rolls one attack and
    one damage. The target is killed if HP reaches 0. Does NOT create or
    mutate any CombatState — that's the entire point.
    """
    str_mod = compute_modifier(attacker.ability_scores.get(Ability.STR))
    attack_bonus = str_mod + attacker.proficiency_bonus

    attack_check = roll_check(f"1d20+{attack_bonus}", target_npc.ac)
    hit = (
        attack_check.outcome != RollOutcome.CRITICAL_FAILURE
        and attack_check.total >= target_npc.ac
    )

    if not hit:
        return TrivialResolveResult(
            hit=False,
            damage=0,
            target_killed=False,
            description=(
                f"{attacker.name} manque {target_npc.name} de peu — "
                "celui-ci s'enfuit en panique."
            ),
        )

    damage_dice = weapon.damage_dice if weapon is not None else "1d4"
    damage_roll = roll(damage_dice)
    damage = max(1, damage_roll.total + str_mod)

    target_npc.hp = max(0, target_npc.hp - damage)
    killed = target_npc.hp <= 0
    if killed:
        target_npc.kill()
        description = (
            f"{attacker.name} frappe {target_npc.name} d'un coup décisif "
            f"({damage} dégâts) — {target_npc.name} s'effondre, mort."
        )
    else:
        description = (
            f"{attacker.name} frappe {target_npc.name} ({damage} dégâts), "
            f"qui chancelle mais tient debout."
        )

    return TrivialResolveResult(
        hit=True,
        damage=damage,
        target_killed=killed,
        description=description,
    )


# ---------------------------------------------------------------------------
# NPC attack resolution — 5e stat-block-driven attacks (task 22)
# ---------------------------------------------------------------------------


def resolve_npc_attack(
    attacker: Combatant,
    defender: Combatant,
    npc_attack: NPCAttack,
    advantage: bool = False,
    disadvantage: bool = False,
) -> AttackResult:
    """Resolve a stat-block-driven NPC attack against a combatant.

    Mirrors :func:`resolve_attack`'s contract (same ``AttackResult``
    shape, condition-based advantage/disadvantage, nat 1 auto-miss, nat
    20 auto-crit, auto-crit on unconscious/paralyzed defenders, doubled
    damage dice on crit) but pulls its numbers from the ``NPCAttack``
    entry on the attacker's :class:`NPCStatBlock` instead of from a PC
    ``Weapon``. The damage modifier is already baked into
    ``NPCAttack.damage_dice`` (e.g. ``"2d6+3"``) so no ability-score
    math is layered on top.

    Mutates ``defender`` HP in place via :func:`apply_damage` (which
    also runs the concentration hook).
    """
    if has_disadvantage_on_attacks(attacker.conditions):
        disadvantage = True
    if grants_advantage_to_attackers(defender.conditions):
        advantage = True
    if advantage and disadvantage:
        advantage = False
        disadvantage = False

    to_hit = npc_attack.to_hit_bonus
    target_ac = defender.character.ac
    expr = f"1d20+{to_hit}" if to_hit >= 0 else f"1d20{to_hit}"

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

    if has_condition(defender.conditions, ConditionType.UNCONSCIOUS) or has_condition(
        defender.conditions, ConditionType.PARALYZED
    ):
        critical = True
        if outcome != RollOutcome.CRITICAL_FAILURE:
            outcome = RollOutcome.CRITICAL_SUCCESS

    if raw_roll == 1:
        hit = False
    elif critical:
        hit = True
    else:
        hit = check.margin >= 0

    damage = 0
    if hit:
        damage_dice = npc_attack.damage_dice
        if critical:
            damage_dice = _double_dice(damage_dice)
        damage_roll = roll(damage_dice)
        damage = max(0, damage_roll.total)

    if damage > 0:
        apply_damage(defender, damage)

    return AttackResult(
        attacker=attacker.name,
        defender=defender.name,
        weapon_name=npc_attack.name,
        attack_roll=raw_roll,
        attack_total=check.total,
        ac=target_ac,
        hit=hit,
        critical=critical,
        outcome=outcome,
        damage=damage,
        damage_type=npc_attack.damage_type,
        defender_hp_remaining=defender.character.hp,
    )


# ---------------------------------------------------------------------------
# Action economy helpers (task 23)
# ---------------------------------------------------------------------------


def consume_action(combatant: Combatant) -> None:
    """Spend the combatant's Action slot. Raises if already used."""
    if combatant.action_budget.action_used:
        raise ValueError(
            f"{combatant.name} has already used their Action this turn"
        )
    combatant.action_budget.action_used = True


def consume_bonus_action(combatant: Combatant) -> None:
    """Spend the combatant's Bonus Action slot. Raises if already used."""
    if combatant.action_budget.bonus_action_used:
        raise ValueError(
            f"{combatant.name} has already used their Bonus Action this turn"
        )
    combatant.action_budget.bonus_action_used = True


def consume_movement(combatant: Combatant, feet: int) -> None:
    """Spend ``feet`` from the combatant's movement pool.

    Raises ``ValueError`` if ``feet`` is negative or exceeds the
    remaining pool. Zero is a no-op.
    """
    if feet < 0:
        raise ValueError("Cannot consume negative movement")
    if combatant.action_budget.movement_remaining_feet < feet:
        raise ValueError(
            f"{combatant.name} only has "
            f"{combatant.action_budget.movement_remaining_feet} ft of movement, "
            f"cannot move {feet} ft"
        )
    combatant.action_budget.movement_remaining_feet -= feet


def consume_reaction(combatant: Combatant) -> None:
    """Spend the combatant's once-per-round Reaction. Raises if already used."""
    if combatant.action_budget.reaction_used_this_round:
        raise ValueError(
            f"{combatant.name} has already used their Reaction this round"
        )
    combatant.action_budget.reaction_used_this_round = True


# ---------------------------------------------------------------------------
# Zone movement + opportunity attacks + Disengage (task 24)
# ---------------------------------------------------------------------------


def _get_main_weapon(combatant: Combatant) -> Weapon | None:
    """Return the weapon equipped in the main hand, or ``None``."""
    main_hand = combatant.inventory.equipped.get(EquipmentSlot.MAIN_HAND)
    if isinstance(main_hand, Weapon):
        return main_hand
    return None


def move_combatant_to_zone(
    state: CombatState,
    combatant: Combatant,
    target_zone: str,
    location: "Location",
) -> list[AttackResult]:
    """Move ``combatant`` from its current zone to an adjacent ``target_zone``.

    Validates adjacency via ``location.are_adjacent``, computes the
    movement cost (15 ft per step, doubled on DIFFICULT_TERRAIN zones),
    consumes the movement pool, then — unless the combatant used
    :func:`disengage` this turn — every living hostile in the source
    zone gets one opportunity attack (spends their Reaction, rolls via
    their main weapon or first NPC stat-block attack). The combatant is
    then relocated.

    Returns the list of opportunity attacks triggered (possibly empty).
    Raises ``ValueError`` on illegal moves (non-adjacent, insufficient
    movement, unknown zones, no zones on location, no current zone).
    """
    if not location.has_combat_zones():
        raise ValueError("Location has no combat zones; cannot move by zone")
    if combatant.current_zone is None:
        raise ValueError(f"{combatant.name} has no current zone set")
    if not location.are_adjacent(combatant.current_zone, target_zone):
        raise ValueError(
            f"Zone '{target_zone}' is not adjacent to '{combatant.current_zone}'"
        )

    target_zone_obj = location.get_zone(target_zone)
    assert target_zone_obj is not None  # guaranteed by are_adjacent

    cost = 15
    if target_zone_obj.has_tag(ZoneTag.DIFFICULT_TERRAIN):
        cost *= 2
    consume_movement(combatant, cost)

    source_zone = combatant.current_zone
    ooa_results: list[AttackResult] = []

    if not combatant.action_budget.disengaged_this_turn:
        for enemy in state.combatants:
            if enemy.name == combatant.name or enemy.side == combatant.side:
                continue
            if not enemy.is_alive or enemy.fled:
                continue
            if enemy.current_zone != source_zone:
                continue
            if enemy.action_budget.reaction_used_this_round:
                continue

            try:
                consume_reaction(enemy)
            except ValueError:
                continue

            ooa_result = _resolve_opportunity_attack(enemy, combatant)
            if ooa_result is not None:
                ooa_results.append(ooa_result)
            if not combatant.is_alive:
                # Combatant died mid-move; abort the exit and skip the
                # zone update so the body stays where it fell.
                return ooa_results

    combatant.current_zone = target_zone
    return ooa_results


def _resolve_opportunity_attack(
    attacker: Combatant,
    defender: Combatant,
) -> AttackResult | None:
    """Resolve a single OOA using the attacker's best available attack.

    NPCs with a stat block use their first :class:`NPCAttack`. Everyone
    else falls back on their equipped main-hand weapon. Returns ``None``
    if the attacker has no usable attack (silent skip).
    """
    if attacker.stat_block is not None and attacker.stat_block.attacks:
        npc_attack = attacker.stat_block.attacks[0]
        return resolve_npc_attack(attacker, defender, npc_attack)
    weapon = _get_main_weapon(attacker)
    if weapon is None:
        return None
    return resolve_attack(attacker, defender, weapon)


def disengage(combatant: Combatant) -> None:
    """Take the Disengage action: consume the Action slot and flag the turn.

    While ``disengaged_this_turn`` is set, :func:`move_combatant_to_zone`
    skips opportunity attacks from hostile combatants in the source
    zone. The flag is reset at the start of the combatant's next turn
    by :meth:`ActionBudget.reset_for_new_turn`.
    """
    consume_action(combatant)
    combatant.action_budget.disengaged_this_turn = True
