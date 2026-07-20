# Task 50 — IA scriptée pour minions

**Phase** : 5 — IA tactique (NPC brains)
**Dépendances** : [22](22_multi_enemy_combat_state.md), [24](24_zone_movement_and_opportunity.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Quand c'est le tour d'un NPC minion (goblin, bandit, mob lambda), quelque chose doit décider ce qu'il fait. Pour les minions, la règle du plan coordinateur est **scripted simple, zéro appel LLM** : heuristique deterministe "attaque la cible la plus fragile en range, sinon se déplace vers une cible".

## Scope

Créer un module `engine/npc_ai/scripted.py` avec :

1. `decide_minion_action(npc_combatant, state, location) -> NPCAction` — fonction pure qui retourne une action planifiée.
2. `NPCAction` Pydantic : le type d'action, la cible, l'arme choisie, etc. (similaire à `Action` mais côté NPC).
3. Helpers internes pour trouver "la cible la plus fragile en range" et "la zone adjacente la plus proche d'un PC".
4. Resolver qui exécute l'action décidée via les helpers existants (`resolve_attack`, `move_combatant_to_zone`, `consume_action`, etc.).

## Heuristiques minion

**Phase décision** :
1. Si au moins un ennemi vivant est en **range** (mêlée = même zone, ranged = toujours) :
   → attaquer la cible avec **le moins de HP actuels** (ignore les PCs inconscients).
2. Sinon, si l'ennemi le plus proche est dans une zone **adjacente** :
   → se déplacer vers lui (consomme Move).
3. Sinon (hors range ET hors zone adjacente) :
   → `Dodge` (defensive fallback) — signale qu'il est bloqué.

**Priorité de cible** : HP actuels ascendants, tiebreak par AC ascendant.

**Choix d'arme** : le minion utilise la première `NPCAttack` de son `stat_block` (pas de dispatch complexe au tier minion).

## Fichiers à créer

- **Créer** `engine/npc_ai/__init__.py`
- **Créer** `engine/npc_ai/scripted.py`

## Implémentation — esquisse

```python
# engine/npc_ai/scripted.py

from typing import TYPE_CHECKING

from pydantic import BaseModel

from engine.combat import (
    Combatant,
    CombatSide,
    CombatState,
    consume_action,
    consume_movement,
    resolve_attack,
)
from engine.validators import ActionType

if TYPE_CHECKING:
    from world.location import Location


class NPCActionPlan(BaseModel):
    """A planned action decided by an NPC AI brain."""
    action_type: ActionType
    target_name: str | None = None
    weapon_name: str | None = None
    move_to_zone: str | None = None
    rationale: str = ""


def decide_minion_action(
    combatant: Combatant,
    state: CombatState,
    location: "Location | None",
) -> NPCActionPlan:
    """Heuristic-based decision for a minion NPC.

    1. If any enemy in range → attack the weakest.
    2. Else if an enemy is in an adjacent zone → move toward them.
    3. Else → Dodge (blocked).
    """
    enemies = _living_opposites(combatant, state)

    # 1. Targets in range
    in_range = [e for e in enemies if _in_attack_range(combatant, e, location)]
    if in_range:
        target = _pick_weakest(in_range)
        return NPCActionPlan(
            action_type=ActionType.ATTACK,
            target_name=target.name,
            weapon_name=_primary_weapon_name(combatant),
            rationale=f"Attack weakest enemy in range: {target.name}",
        )

    # 2. Move toward closest enemy
    if location is not None and combatant.current_zone is not None:
        target_zone = _closest_enemy_zone(combatant, enemies, location)
        if target_zone is not None:
            return NPCActionPlan(
                action_type=ActionType.MOVE,
                move_to_zone=target_zone,
                rationale=f"Move toward enemy zone: {target_zone}",
            )

    # 3. Dodge fallback
    return NPCActionPlan(
        action_type=ActionType.DEFEND,
        rationale="No valid target or movement — holding ground",
    )


def execute_action_plan(
    combatant: Combatant,
    plan: NPCActionPlan,
    state: CombatState,
    location: "Location | None",
) -> str:
    """Execute a planned action, mutating state and returning a human summary."""
    if plan.action_type == ActionType.ATTACK:
        target = _find_by_name(plan.target_name, state)
        if target is None:
            return f"{combatant.name} could not find target {plan.target_name}"
        consume_action(combatant)
        # Multi-attack support: if tier is minion, do just 1 attack
        # regardless of multiattack_count (minions = 1 attack).
        result = resolve_attack(
            attacker=combatant,
            defender=target,
            weapon=None,  # minion uses its primary NPCAttack from stat_block
            state=state,
        )
        if result.hit:
            return f"{combatant.name} hits {target.name} for {result.damage} damage"
        return f"{combatant.name} misses {target.name}"

    if plan.action_type == ActionType.MOVE:
        if plan.move_to_zone is None or location is None:
            return f"{combatant.name} cannot move"
        from engine.combat import move_combatant_to_zone
        move_combatant_to_zone(state, combatant, plan.move_to_zone, location)
        return f"{combatant.name} moves to {plan.move_to_zone}"

    if plan.action_type == ActionType.DEFEND:
        consume_action(combatant)
        return f"{combatant.name} dodges"

    return f"{combatant.name} does nothing ({plan.action_type.value})"


# ---------- helpers ----------

def _living_opposites(me: Combatant, state: CombatState) -> list[Combatant]:
    return [
        c for c in state.combatants
        if c.side != me.side and c.is_alive and not c.fled
    ]


def _pick_weakest(combatants: list[Combatant]) -> Combatant:
    return min(
        combatants,
        key=lambda c: (c.character.hp, c.character.ac),
    )


def _in_attack_range(
    attacker: Combatant,
    target: Combatant,
    location: "Location | None",
) -> bool:
    """True if attacker can hit target with its primary weapon."""
    if location is None or not location.has_combat_zones():
        return True  # zoneless combat
    if attacker.current_zone is None or target.current_zone is None:
        return True
    if attacker.current_zone == target.current_zone:
        return True
    # Check if the NPC has a ranged attack
    if attacker.character.stat_block is not None:
        for atk in attacker.character.stat_block.attacks:
            if atk.range_type == "ranged":
                return True  # ranged attack can reach other zones
    return False


def _closest_enemy_zone(
    me: Combatant,
    enemies: list[Combatant],
    location: "Location",
) -> str | None:
    """BFS shortest path from me to any enemy, return the next zone to step."""
    if me.current_zone is None:
        return None
    visited = {me.current_zone}
    queue: list[tuple[str, str]] = []  # (current_zone, first_step_from_me)
    start_zone = location.get_zone(me.current_zone)
    if start_zone is None:
        return None
    for adj in start_zone.adjacent_zone_names:
        queue.append((adj, adj))
        visited.add(adj)

    while queue:
        current, first_step = queue.pop(0)
        for enemy in enemies:
            if enemy.current_zone == current:
                return first_step
        zone = location.get_zone(current)
        if zone is None:
            continue
        for adj in zone.adjacent_zone_names:
            if adj not in visited:
                visited.add(adj)
                queue.append((adj, first_step))
    return None


def _primary_weapon_name(combatant: Combatant) -> str | None:
    if combatant.character.stat_block is None:
        return None
    if not combatant.character.stat_block.attacks:
        return None
    return combatant.character.stat_block.attacks[0].name


def _find_by_name(name: str | None, state: CombatState) -> Combatant | None:
    if name is None:
        return None
    for c in state.combatants:
        if c.name == name:
            return c
    return None
```

**Note sur `resolve_attack` avec un NPCAttack** : `resolve_attack` dans `engine/combat.py` suppose un `Weapon` du PC inventory. Pour les NPCs, il faut soit adapter `resolve_attack` pour accepter un `NPCAttack`, soit créer un helper `resolve_npc_attack(attacker, defender, npc_attack, state)` qui roule 1d20+to_hit_bonus, compare à defender.ac, et applique les dégâts. **Préférer** le helper séparé pour ne pas alourdir `resolve_attack`. À décider et documenter dans cette tâche.

## Acceptance criteria

- [ ] `engine/npc_ai/scripted.py` existe avec `decide_minion_action` et `execute_action_plan`.
- [ ] `NPCActionPlan` Pydantic.
- [ ] Heuristique "weakest in range" fonctionne.
- [ ] BFS de mouvement trouve le prochain step correct.
- [ ] Fallback Dodge si bloqué.
- [ ] `resolve_npc_attack` (ou extension de `resolve_attack`) utilise le `NPCAttack` du stat_block.

## Tests à ajouter

Dans `tests/test_npc_ai_scripted.py` (nouveau) :

- `test_minion_attacks_weakest_enemy_in_same_zone`.
- `test_minion_moves_to_adjacent_zone_containing_enemy`.
- `test_minion_bfs_finds_next_step_toward_far_enemy`.
- `test_minion_dodges_when_no_target_reachable`.
- `test_minion_ranged_attack_across_zones`.
- `test_minion_does_not_target_fled_or_dead`.
- `test_execute_attack_plan_rolls_dice_and_applies_damage`.
- `test_execute_move_plan_calls_move_combatant_to_zone`.

## Hors scope

- **Ne pas** implémenter le comportement des elites — tâche [51](51_elite_behavior_profiles.md).
- **Ne pas** implémenter le LLM tactician — tâche [52](52_boss_llm_tactician.md).
- **Ne pas** ajouter la logique multi-attaques — les minions ont `multiattack_count=1` par définition du tier.
- **Ne pas** câbler l'invocation depuis le turn loop — tâche [22](22_multi_enemy_combat_state.md) ou une tâche dédiée de "combat loop driver" (pourrait être ajoutée si nécessaire).

## Validation finale

```bash
uv run pytest tests/test_npc_ai_scripted.py -v
uv run ruff check engine/npc_ai/
uv run mypy engine/npc_ai/
```
