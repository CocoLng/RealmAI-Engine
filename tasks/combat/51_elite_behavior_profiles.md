# Task 51 — IA elite : behavior profiles et signatures

**Phase** : 5 — IA tactique (NPC brains)
**Dépendances** : [50](50_scripted_minion_ai.md), [11](11_npc_library_archetypes.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Les elites (captain, brute, mage, etc.) sont plus riches que les minions :

- **Multiattack** : 2 attaques par tour d'Action.
- **Signature ability** : 1 capacité unique tirée de la librairie, utilisable à volonté ou 1/combat.
- **Behavior profile** : `AGGRESSIVE` / `DEFENSIVE` / `SUPPORT` / `TACTICAL` qui module les heuristiques.

Cette tâche étend le scripted AI (tâche [50](50_scripted_minion_ai.md)) pour supporter ces comportements.

## Scope

1. Créer `engine/npc_ai/elite.py` avec `decide_elite_action(combatant, state, location) -> NPCActionPlan`.
2. Dispatcher sur `combatant.character.stat_block.behavior_profile`.
3. Chaque behavior a sa propre logique :
   - **AGGRESSIVE** : priorité max damage. Utilise la signature si elle fait des dégâts et est dispo. Multi-attack toujours.
   - **DEFENSIVE** : priorité survie/protection alliés. Utilise Dodge ou Help si alliés en danger. Attaque prudemment.
   - **SUPPORT** : priorité heal/buff alliés. Lance Rally, Shield Wall, etc. en priorité. Attaque si rien à supporter.
   - **TACTICAL** : exploite les conditions (attaque les Frightened, applique Prone, etc.). Gère les ressources avec économie.
4. Ajouter `execute_signature_ability(combatant, signature, targets, state)` qui résout l'effet d'une signature via la table `SignatureAbilityEffect.kind`.
5. Tracker `uses_remaining` correctement (decrement, reset au début d'un nouveau combat via combat_entry).

## Fichiers à créer/modifier

- **Créer** `engine/npc_ai/elite.py`
- **Modifier** `engine/npc_ai/scripted.py` — `decide_action_for` dispatcher qui choisit entre minion et elite selon le tier.

## Implémentation — esquisse

```python
# engine/npc_ai/elite.py

from engine.npc_ai.scripted import (
    NPCActionPlan, _living_opposites, _pick_weakest, _in_attack_range,
    _closest_enemy_zone, _primary_weapon_name, _find_by_name,
)
from engine.npc_stat_block import (
    BehaviorProfile, SignatureAbility, SignatureAbilityEffect,
)
from engine.conditions import ConditionType, has_condition


def decide_elite_action(
    combatant: Combatant,
    state: CombatState,
    location: Location | None,
) -> NPCActionPlan:
    sb = combatant.character.stat_block
    if sb is None:
        # Fall back to minion logic if no stat block (defensive)
        from engine.npc_ai.scripted import decide_minion_action
        return decide_minion_action(combatant, state, location)

    profile = sb.behavior_profile
    if profile == BehaviorProfile.AGGRESSIVE:
        return _decide_aggressive(combatant, state, location, sb)
    if profile == BehaviorProfile.DEFENSIVE:
        return _decide_defensive(combatant, state, location, sb)
    if profile == BehaviorProfile.SUPPORT:
        return _decide_support(combatant, state, location, sb)
    if profile == BehaviorProfile.TACTICAL:
        return _decide_tactical(combatant, state, location, sb)

    # Unknown profile — fallback
    return _decide_aggressive(combatant, state, location, sb)


def _decide_aggressive(
    combatant, state, location, sb,
) -> NPCActionPlan:
    """Max damage. Use signature if damage-heavy and available."""
    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]

    # 1. Can we use a damaging signature?
    damaging_sig = _find_damage_signature(sb)
    if damaging_sig is not None and in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,  # or a new SIGNATURE action type
            target_name=target.name,
            rationale=f"AGGRESSIVE: signature {damaging_sig.name} on {target.name}",
            # Additional field to flag signature use — extend NPCActionPlan
        )

    # 2. Standard multi-attack on weakest in range
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            rationale=f"AGGRESSIVE: standard attack on weakest {target.name}",
        )

    # 3. Move toward enemy
    if location is not None and combatant.current_zone is not None:
        target_zone = _closest_enemy_zone(combatant, enemies, location)
        if target_zone is not None:
            return NPCActionPlan(
                action_type=ActionType.MOVE,
                move_to_zone=target_zone,
                rationale="AGGRESSIVE: closing distance",
            )

    return NPCActionPlan(action_type=ActionType.DEFEND, rationale="AGGRESSIVE: blocked")


def _decide_defensive(combatant, state, location, sb) -> NPCActionPlan:
    """Survival priority. Use defensive signatures, protect allies."""
    # 1. Low HP → Dodge or defensive signature
    hp_ratio = combatant.character.hp / max(1, combatant.character.max_hp)
    if hp_ratio < 0.3:
        defensive_sig = _find_signature_by_kind(sb, "buff")
        if defensive_sig:
            return NPCActionPlan(
                action_type=ActionType.ATTACK,
                rationale=f"DEFENSIVE: self-buff {defensive_sig.name}",
            )
        return NPCActionPlan(action_type=ActionType.DEFEND, rationale="DEFENSIVE: low HP")

    # 2. Ally in danger → Help or defensive signature
    allies_low = _find_allies_low_hp(combatant, state)
    if allies_low:
        # Apply Shield Wall or similar
        pass  # placeholder

    # 3. Otherwise attack prudently (no reckless charges)
    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            rationale="DEFENSIVE: cautious attack",
        )

    return NPCActionPlan(action_type=ActionType.DEFEND, rationale="DEFENSIVE: hold")


def _decide_support(combatant, state, location, sb) -> NPCActionPlan:
    """Heal/buff priority. Use Rally, Spirit Guardians, etc."""
    # 1. Any wounded ally → Rally / heal signature
    wounded = _find_allies_wounded(combatant, state)
    if wounded:
        heal_sig = _find_signature_by_kind(sb, "heal")
        if heal_sig and heal_sig.uses_remaining is not None and heal_sig.uses_remaining > 0:
            return NPCActionPlan(
                action_type=ActionType.ATTACK,
                target_name=wounded[0].name,
                rationale=f"SUPPORT: {heal_sig.name} on wounded {wounded[0].name}",
            )

    # 2. Otherwise attack
    enemies = _living_opposites(combatant, state)
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]
    if in_range:
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=_pick_weakest(in_range).name,
            rationale="SUPPORT: fallback attack",
        )

    return NPCActionPlan(action_type=ActionType.DEFEND, rationale="SUPPORT: hold")


def _decide_tactical(combatant, state, location, sb) -> NPCActionPlan:
    """Exploit conditions. Target Frightened/Prone first, apply new conditions."""
    enemies = _living_opposites(combatant, state)
    # Prioritize enemies with exploitable conditions
    vulnerable = [
        e for e in enemies
        if has_condition(e.conditions, ConditionType.FRIGHTENED)
        or has_condition(e.conditions, ConditionType.PRONE)
    ]
    targets = vulnerable or enemies
    in_range = [e for e in targets if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            rationale=f"TACTICAL: exploit condition on {target.name}",
        )

    return NPCActionPlan(action_type=ActionType.DEFEND, rationale="TACTICAL: reposition")


# ---------- helpers ----------

def _find_damage_signature(sb) -> SignatureAbility | None:
    for sig in sb.signature_abilities:
        if sig.uses_remaining == 0:
            continue
        if any(e.kind == "damage" or e.kind == "aoe_damage" for e in sig.effects):
            return sig
    return None


def _find_signature_by_kind(sb, kind: str) -> SignatureAbility | None:
    for sig in sb.signature_abilities:
        if sig.uses_remaining == 0:
            continue
        if any(e.kind == kind for e in sig.effects):
            return sig
    return None


def _find_allies_low_hp(me, state):
    return [
        c for c in state.combatants
        if c.side == me.side and c.is_alive
        and c.character.hp / max(1, c.character.max_hp) < 0.3
    ]


def _find_allies_wounded(me, state):
    return [
        c for c in state.combatants
        if c.side == me.side and c.is_alive
        and c.character.hp < c.character.max_hp
    ]
```

**Signature execution** :

```python
def execute_signature_ability(
    caster: Combatant,
    signature: SignatureAbility,
    targets: list[Combatant],
    state: CombatState,
) -> list[str]:
    """Resolve a signature ability. Applies effects, decrements uses, returns summaries."""
    if signature.uses_remaining is not None and signature.uses_remaining > 0:
        signature.uses_remaining -= 1

    summaries: list[str] = []
    for effect in signature.effects:
        if effect.kind == "damage":
            for target in targets:
                damage = roll(effect.dice or "1d6").total
                target.character.hp = max(0, target.character.hp - damage)
                summaries.append(f"{target.name} takes {damage} damage")
        elif effect.kind == "heal":
            for target in targets:
                heal = roll(effect.dice or "1d6").total
                target.character.hp = min(
                    target.character.max_hp,
                    target.character.hp + heal,
                )
                summaries.append(f"{target.name} healed {heal} HP")
        elif effect.kind == "condition":
            for target in targets:
                # Apply save if applicable
                if effect.save_ability and effect.save_dc:
                    save_mod = _compute_save_mod(target, effect.save_ability)
                    save = roll_check(f"1d20+{save_mod}", effect.save_dc)
                    if save.outcome.value.endswith("success"):
                        summaries.append(f"{target.name} resists {signature.name}")
                        continue
                apply_condition(
                    target.conditions,
                    ActiveCondition(
                        condition_type=ConditionType[effect.condition_name.upper()],
                        duration_rounds=effect.condition_duration_rounds or 3,
                    ),
                )
                summaries.append(f"{target.name} is now {effect.condition_name}")
        # ... other effect kinds ...
    return summaries
```

## Acceptance criteria

- [ ] `decide_elite_action` dispatche sur les 4 profils.
- [ ] AGGRESSIVE utilise la signature damage si dispo.
- [ ] DEFENSIVE fallback sur Dodge si HP < 30%.
- [ ] SUPPORT cible les alliés wounded pour heal/buff.
- [ ] TACTICAL priorise les enemies avec conditions exploitables.
- [ ] `execute_signature_ability` applique correctement les effets (damage, heal, condition).
- [ ] `uses_remaining` est décrémenté.
- [ ] Fallback vers `decide_minion_action` si `stat_block is None`.

## Tests à ajouter

Dans `tests/test_npc_ai_elite.py` (nouveau) :

- `test_aggressive_uses_damage_signature_when_available`.
- `test_aggressive_falls_back_to_standard_attack_when_no_signature`.
- `test_defensive_dodges_when_hp_low`.
- `test_defensive_attacks_cautiously_when_healthy`.
- `test_support_heals_wounded_ally`.
- `test_support_attacks_when_no_one_to_support`.
- `test_tactical_prioritizes_frightened_enemy`.
- `test_execute_signature_damage_effect`.
- `test_execute_signature_heal_effect`.
- `test_execute_signature_condition_with_save`.
- `test_signature_uses_remaining_decrements`.

## Hors scope

- **Ne pas** implémenter toutes les `kind` de `SignatureAbilityEffect` — damage, heal, condition suffisent pour le MVP ; buff/debuff/move peuvent arriver plus tard.
- **Ne pas** implémenter le LLM-tactician boss — tâche [52](52_boss_llm_tactician.md).
- **Ne pas** implémenter les legendary actions — tâche [53](53_legendary_actions_off_turn.md).

## Validation finale

```bash
uv run pytest tests/test_npc_ai_elite.py -v
uv run ruff check engine/npc_ai/elite.py
uv run mypy engine/npc_ai/elite.py
```
