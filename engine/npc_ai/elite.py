"""Elite NPC tactical brain — behavior profiles + signature executor (task 51).

Elite NPCs carry a ``behavior_profile`` that steers their decision-making:

- ``AGGRESSIVE`` — pick damaging signatures first, else hit the weakest.
- ``DEFENSIVE`` — dodge on low HP, otherwise attack prudently.
- ``SUPPORT`` — heal/buff wounded allies before attacking.
- ``TACTICAL`` — preferentially target enemies with exploitable conditions
  (Frightened, Prone, Paralyzed, Restrained, Stunned).

Signatures are resolved by :func:`execute_signature_ability`, which handles
the three MVP effect kinds (``damage``, ``heal``, ``condition``) and
gracefully degrades the other four with a warning and a fallback summary —
the caller is expected to fall back on a standard NPCAttack when that
happens.

Boss-tier decisions layer the LLM tactician on top of this module
(task 52). Everything here is pure heuristic Python.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from engine.character.enums import Ability
from engine.combat import (
    Combatant,
    CombatState,
    apply_damage,
    apply_healing,
)
from engine.conditions import (
    ActiveCondition,
    ConditionType,
    apply_condition,
    has_condition,
)
from engine.dice import D20CheckResult, RollOutcome, roll, roll_check
from engine.npc_ai.scripted import (
    NPCActionPlan,
    _closest_enemy_zone,
    _in_attack_range,
    _living_opposites,
    _pick_weakest,
    _primary_weapon_name,
)
from engine.npc_stat_block import (
    BehaviorProfile,
    NPCStatBlock,
    SignatureAbility,
    SignatureAbilityEffect,
)
from engine.validators import ActionType

if TYPE_CHECKING:
    from world.location import Location

logger = logging.getLogger(__name__)


# Condition types whose victim makes a juicy tactical target. Used by the
# TACTICAL profile to prioritise ennemis frappés par un malus exploitable.
_EXPLOITABLE_CONDITIONS: frozenset[ConditionType] = frozenset({
    ConditionType.FRIGHTENED,
    ConditionType.PRONE,
    ConditionType.PARALYZED,
    ConditionType.RESTRAINED,
    ConditionType.STUNNED,
})


_MVP_EFFECT_KINDS: frozenset[str] = frozenset({"damage", "heal", "condition"})


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def decide_elite_action(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
) -> NPCActionPlan:
    """Route an elite combatant to the brain matching its behavior profile.

    Falls back on ``decide_minion_action`` when the combatant has no
    ``stat_block`` (e.g. legacy NPCs) so a missing profile never crashes
    the turn loop.
    """
    if combatant.stat_block is None:
        from engine.npc_ai.scripted import decide_minion_action

        return decide_minion_action(combatant, state, location)

    sb = combatant.stat_block
    profile = sb.behavior_profile

    if profile == BehaviorProfile.AGGRESSIVE:
        return _decide_aggressive(combatant, state, location, sb)
    if profile == BehaviorProfile.DEFENSIVE:
        return _decide_defensive(combatant, state, location, sb)
    if profile == BehaviorProfile.SUPPORT:
        return _decide_support(combatant, state, location, sb)
    if profile == BehaviorProfile.TACTICAL:
        return _decide_tactical(combatant, state, location, sb)

    # Unknown profile — defensive fallback to aggressive logic
    return _decide_aggressive(combatant, state, location, sb)


# ---------------------------------------------------------------------------
# Profile implementations
# ---------------------------------------------------------------------------


def _decide_aggressive(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    sb: NPCStatBlock,
) -> NPCActionPlan:
    """Maximise damage. Spend a damaging signature when possible."""
    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]

    if in_range:
        target = _pick_weakest(in_range)
        damaging_sig = _find_damage_signature(sb)
        if damaging_sig is not None:
            return NPCActionPlan(
                action_type=ActionType.ATTACK,
                target_name=target.name,
                signature_name=damaging_sig.name,
                rationale=f"AGGRESSIVE: {damaging_sig.name} on {target.name}",
            )
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale=f"AGGRESSIVE: attack weakest {target.name}",
        )

    if location is not None and combatant.current_zone is not None:
        target_zone = _closest_enemy_zone(combatant, enemies, location)
        if target_zone is not None:
            return NPCActionPlan(
                action_type=ActionType.MOVE,
                move_to_zone=target_zone,
                rationale="AGGRESSIVE: close distance",
            )

    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="AGGRESSIVE: blocked",
    )


def _decide_defensive(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    sb: NPCStatBlock,
) -> NPCActionPlan:
    """Survive first, then strike prudently."""
    hp_ratio = combatant.character.hp / max(1, combatant.character.max_hp)

    if hp_ratio < 0.3:
        return NPCActionPlan(
            action_type=ActionType.DEFEND,
            rationale="DEFENSIVE: low HP, dodging",
        )

    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]

    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale="DEFENSIVE: cautious attack",
        )

    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="DEFENSIVE: hold ground",
    )


def _decide_support(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    sb: NPCStatBlock,
) -> NPCActionPlan:
    """Heal wounded allies. Fall back on attacks when nobody needs help."""
    wounded = _find_allies_wounded(combatant, state)
    if wounded:
        heal_sig = _find_signature_by_kind(sb, "heal")
        if heal_sig is not None:
            target = wounded[0]
            return NPCActionPlan(
                action_type=ActionType.ATTACK,
                target_name=target.name,
                signature_name=heal_sig.name,
                rationale=f"SUPPORT: {heal_sig.name} on {target.name}",
            )

    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale="SUPPORT: fallback attack",
        )

    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="SUPPORT: hold",
    )


def _decide_tactical(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
    sb: NPCStatBlock,
) -> NPCActionPlan:
    """Exploit existing conditions on enemies before anything else."""
    enemies = _living_opposites(combatant, state)
    vulnerable = [
        e
        for e in enemies
        if any(has_condition(e.conditions, c) for c in _EXPLOITABLE_CONDITIONS)
    ]
    pool = vulnerable or enemies
    in_range = [e for e in pool if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale=f"TACTICAL: target {target.name}",
        )

    if location is not None and combatant.current_zone is not None:
        target_zone = _closest_enemy_zone(combatant, enemies, location)
        if target_zone is not None:
            return NPCActionPlan(
                action_type=ActionType.MOVE,
                move_to_zone=target_zone,
                rationale="TACTICAL: reposition",
            )

    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="TACTICAL: blocked",
    )


# ---------------------------------------------------------------------------
# Signature executor
# ---------------------------------------------------------------------------


def execute_signature_ability(
    caster: Combatant,
    signature: SignatureAbility,
    targets: list[Combatant],
    state: CombatState,
) -> list[str]:
    """Resolve a signature ability against ``targets`` and return summaries.

    Handles the MVP effect kinds (``damage``, ``heal``, ``condition``) in
    place. Non-MVP kinds (``aoe_damage``, ``buff``, ``debuff``, ``move``)
    are not implemented yet — they log a WARNING and produce a fallback
    summary. Callers that depend on those kinds should fall back on a
    standard ``NPCAttack`` via :func:`engine.combat.resolve_npc_attack`.

    ``uses_remaining`` is decremented when it is an integer (limited
    usage). ``at_will`` signatures leave it at ``None``.
    """
    if signature.uses_remaining is not None and signature.uses_remaining > 0:
        signature.uses_remaining -= 1

    summaries: list[str] = []
    for effect in signature.effects:
        if effect.kind not in _MVP_EFFECT_KINDS:
            logger.warning(
                "Signature %s effect kind %r is not MVP — falling back to "
                "standard attack.",
                signature.name,
                effect.kind,
            )
            summaries.append(
                f"[{signature.name}] {effect.kind} not implemented — "
                "fallback to standard attack."
            )
            continue

        if effect.kind == "damage":
            summaries.extend(_apply_damage_effect(effect, targets))
        elif effect.kind == "heal":
            summaries.extend(_apply_heal_effect(effect, targets))
        elif effect.kind == "condition":
            summaries.extend(_apply_condition_effect(signature, effect, targets))

    return summaries


def _apply_damage_effect(
    effect: SignatureAbilityEffect,
    targets: list[Combatant],
) -> list[str]:
    dice_expr = effect.dice or "1d6"
    summaries: list[str] = []
    for target in targets:
        if not target.is_alive:
            continue
        rolled = roll(dice_expr).total
        damage = max(0, rolled)
        apply_damage(target, damage)
        summaries.append(f"{target.name} takes {damage} damage")
    return summaries


def _apply_heal_effect(
    effect: SignatureAbilityEffect,
    targets: list[Combatant],
) -> list[str]:
    dice_expr = effect.dice or "1d6"
    summaries: list[str] = []
    for target in targets:
        if not target.is_alive:
            continue
        healing = max(0, roll(dice_expr).total)
        apply_healing(target, healing)
        summaries.append(f"{target.name} healed {healing} HP")
    return summaries


def _apply_condition_effect(
    signature: SignatureAbility,
    effect: SignatureAbilityEffect,
    targets: list[Combatant],
) -> list[str]:
    if effect.condition_name is None:
        logger.warning(
            "Signature %s condition effect missing condition_name — skipping.",
            signature.name,
        )
        return [f"[{signature.name}] missing condition_name"]

    try:
        cond_type = ConditionType[effect.condition_name.upper()]
    except KeyError:
        logger.warning(
            "Signature %s condition effect has unknown condition_name %r — skipping.",
            signature.name,
            effect.condition_name,
        )
        return [f"[{signature.name}] unknown condition {effect.condition_name!r}"]

    summaries: list[str] = []
    for target in targets:
        if not target.is_alive:
            continue
        if effect.save_ability is not None and effect.save_dc is not None:
            save_result = _roll_signature_save(target, effect.save_ability, effect.save_dc)
            if save_result.outcome == RollOutcome.SUCCESS or save_result.total >= effect.save_dc:
                summaries.append(f"{target.name} resists {signature.name}")
                continue
        apply_condition(
            target.conditions,
            ActiveCondition(
                condition_type=cond_type,
                source=signature.name,
                duration_rounds=effect.condition_duration_rounds,
            ),
        )
        summaries.append(f"{target.name} is now {effect.condition_name}")
    return summaries


def _roll_signature_save(
    target: Combatant,
    save_ability: str,
    dc: int,
) -> D20CheckResult:
    """Roll ``1d20 + target save modifier`` versus ``dc``.

    The modifier is the ability modifier plus proficiency if the target
    is proficient in that save. Does not apply any phase save bonus from
    task 54 — phase save bonuses feed a cumulative bonus directly into
    the save roll caller-side (TBD in task 54).
    """
    from engine.character import compute_modifier

    ability = Ability(save_ability)
    score = target.character.ability_scores.get(ability)
    mod = compute_modifier(score)
    if ability in target.character.saving_throw_proficiencies:
        mod += target.character.proficiency_bonus
    mod += target.phase_save_bonus

    expr = f"1d20+{mod}" if mod >= 0 else f"1d20{mod}"
    return roll_check(expr, dc)


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------


def _find_damage_signature(sb: NPCStatBlock) -> SignatureAbility | None:
    """Return the first signature with a damage effect and uses left, else None."""
    for sig in sb.signature_abilities:
        if sig.uses_remaining == 0:
            continue
        if any(
            e.kind in ("damage", "aoe_damage") for e in sig.effects
        ):
            # aoe_damage is non-MVP — the brain still picks it because it is
            # a damaging intent, but the executor will log a warning and
            # fallback on a standard attack. We keep the selection logic
            # optimistic so the fallback path is exercised.
            if any(e.kind == "damage" for e in sig.effects):
                return sig
    return None


def _find_signature_by_kind(sb: NPCStatBlock, kind: str) -> SignatureAbility | None:
    """Return the first available signature carrying an effect of ``kind``."""
    for sig in sb.signature_abilities:
        if sig.uses_remaining == 0:
            continue
        if any(e.kind == kind for e in sig.effects):
            return sig
    return None


def _find_allies_wounded(me: Combatant, state: CombatState) -> list[Combatant]:
    """Living allies (same side) whose current HP is below max HP."""
    wounded: list[Combatant] = []
    for c in state.combatants:
        if c.name == me.name:
            continue
        if c.side != me.side:
            continue
        if not c.is_alive or c.fled:
            continue
        if c.character.hp < c.character.max_hp:
            wounded.append(c)
    return wounded
