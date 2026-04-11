# Task 13 — Conditions SURPRISED et CONCENTRATING

**Phase** : 1 — Fondations NPC & engine
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le système d'initiative 5e utilisé par RealmAI (tâche [21](21_initiative_and_surprise.md)) repose sur la condition **`Surprised`** pour empêcher un combattant d'agir à son premier tour quand l'autre camp a eu l'initiative. 5e RAW : *"If you're surprised, you can't move or take an action on your first turn of the combat, and you can't take a reaction until that turn ends."*

Séparément, le système de sorts (tâches [51](51_elite_behavior_profiles.md), [52](52_boss_llm_tactician.md)) a besoin de **`Concentrating`** pour tracker un sort à concentration actif et forcer un save CON quand le lanceur subit des dégâts.

Les deux conditions **n'existent pas** dans [engine/conditions.py](../../engine/conditions.py) aujourd'hui. Cette tâche les ajoute.

## Scope

1. Ajouter `SURPRISED` et `CONCENTRATING` à `ConditionType`.
2. Ajouter leurs helpers de check (`is_surprised`, `is_concentrating`).
3. Ajouter la logique mécanique qui découle :
   - `cannot_act_due_to_surprise(conditions) -> bool` — utilisé par le turn loop pour skip le tour.
   - `cannot_react_due_to_surprise(conditions) -> bool` — bloque les réactions tant que la condition est active.
   - `check_concentration_save(caster, incoming_damage) -> D20CheckResult` — CON save DC = max(10, damage/2). Utilise `engine.dice.roll_check`.
   - `drop_concentration(combatant)` — retire la condition `CONCENTRATING` et les effets liés.
4. Gestion de la durée : `SURPRISED` est retiré à la **fin du premier tour** du combattant. L'engine `advance_turn` doit tick cette condition.

## Fichiers à modifier

- [engine/conditions.py](../../engine/conditions.py)

## Implémentation — esquisse

```python
# Dans ConditionType enum — ajouter :
    SURPRISED = "surprised"
    CONCENTRATING = "concentrating"


# Nouveau helper :
def is_surprised(conditions: list[ActiveCondition]) -> bool:
    return has_condition(conditions, ConditionType.SURPRISED)


def is_concentrating(conditions: list[ActiveCondition]) -> bool:
    return has_condition(conditions, ConditionType.CONCENTRATING)


def cannot_act_due_to_surprise(conditions: list[ActiveCondition]) -> bool:
    """A surprised creature cannot act on its first turn."""
    return is_surprised(conditions)


def cannot_react_due_to_surprise(conditions: list[ActiveCondition]) -> bool:
    """A surprised creature cannot take reactions until its first turn ends."""
    return is_surprised(conditions)
```

**Tick durée `SURPRISED`** :
`SURPRISED` est la seule condition dont la durée est "until end of first turn" — unique en son genre. Deux options :

- **Option A** : ajouter dans `apply_condition` un cas spécial pour `SURPRISED` avec `duration_rounds=0` signifiant "until end of current turn". Le turn manager de `engine/combat.py::advance_turn` tick en premier la condition `SURPRISED` puis la retire explicitement.
- **Option B** : utiliser `duration_rounds=1` et laisser `tick_durations` la retirer naturellement à la fin du tour. Mais 1 tour = 6 secondes = un round complet, pas le premier tour. Probable off-by-one.

**Préférer Option A** avec un helper explicite :

```python
def consume_surprise_if_present(conditions: list[ActiveCondition]) -> bool:
    """Remove the SURPRISED condition from the list. Returns True if removed.

    Called by the turn manager at the END of the surprised creature's first
    turn (not the beginning — the whole point of surprise is the creature
    cannot act this turn). Idempotent : no-op if not surprised.
    """
    if has_condition(conditions, ConditionType.SURPRISED):
        remove_condition(conditions, ConditionType.SURPRISED)
        return True
    return False
```

Et dans la tâche [22](22_multi_enemy_combat_state.md), le turn manager appellera `consume_surprise_if_present` quand un combattant skippé par surprise passe son tour.

**Concentration save** :

```python
from engine.dice import D20CheckResult, roll_check

def check_concentration_save(
    combatant: "Combatant",
    incoming_damage: int,
) -> D20CheckResult:
    """Roll a CON save against DC = max(10, damage // 2). 5e RAW."""
    if not is_concentrating(combatant.conditions):
        raise ValueError("Cannot check concentration on non-concentrating combatant")
    con_mod = compute_modifier(
        combatant.character.ability_scores.get(Ability.CON)
    )
    dc = max(10, incoming_damage // 2)
    return roll_check(f"1d20+{con_mod}", dc)


def drop_concentration(combatant: "Combatant") -> None:
    """Remove CONCENTRATING. Effects that depended on it should also be cleared
    by the caller (this function does not know about the specific effects)."""
    remove_condition(combatant.conditions, ConditionType.CONCENTRATING)
```

**Note** : `drop_concentration` ne sait pas quels effets dépendaient de la concentration (ex : Spirit Guardians reste actif jusqu'à ce qu'on le drop). Ça sera géré par l'appelant dans la tâche [51](51_elite_behavior_profiles.md).

## Acceptance criteria

- [ ] `ConditionType.SURPRISED` et `CONCENTRATING` existent.
- [ ] `is_surprised`, `is_concentrating` fonctionnent.
- [ ] `cannot_act_due_to_surprise` retourne True quand SURPRISED, False sinon.
- [ ] `cannot_react_due_to_surprise` retourne True quand SURPRISED.
- [ ] `consume_surprise_if_present` retire la condition et retourne True, idempotent sinon.
- [ ] `check_concentration_save` roule un CON save contre DC correct et retourne `D20CheckResult`.
- [ ] `drop_concentration` retire la condition ; no-op si pas concentrant.

## Tests à ajouter

Dans `tests/test_conditions.py` :

- `test_surprised_condition_applied_and_detected` — apply, check, remove.
- `test_surprised_blocks_action_and_reaction` — `cannot_act_due_to_surprise` et `cannot_react_due_to_surprise` retournent True.
- `test_consume_surprise_removes_condition` — True première fois, False après.
- `test_concentrating_condition_applied_and_detected`.
- `test_check_concentration_save_dc_floor_10` — dégâts=5 → DC=10.
- `test_check_concentration_save_dc_half_damage` — dégâts=40 → DC=20.
- `test_check_concentration_save_raises_if_not_concentrating` — safety check.
- `test_drop_concentration_idempotent` — appel sur non-concentrant = no-op.

## Hors scope

- **Ne pas** intégrer `consume_surprise_if_present` dans `advance_turn` — tâche [22](22_multi_enemy_combat_state.md).
- **Ne pas** tracker "quel sort est actuellement concentré" — à faire au niveau `SpellcasterState` ou `Combatant` dans une tâche future.
- **Ne pas** implémenter les auto-saves de concentration quand un combattant est touché — câblé dans la tâche [22](22_multi_enemy_combat_state.md).

## Validation finale

```bash
uv run pytest tests/test_conditions.py -v
uv run ruff check engine/conditions.py
uv run mypy engine/conditions.py
```
