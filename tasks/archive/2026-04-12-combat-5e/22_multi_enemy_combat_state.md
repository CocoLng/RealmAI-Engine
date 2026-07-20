# Task 22 — CombatState multi-ennemis, turn management, persistence

**Phase** : 2 — Moteur de combat
**Dépendances** : [21](21_initiative_and_surprise.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le `CombatState` actuel supporte une liste de `Combatant`, mais le code partout suppose implicitement **1 PC vs 1 enemy**. Pour supporter une vraie partie D&D avec 2-4 joueurs et 2-6 ennemis, il faut :

1. **Turn management** qui itère correctement sur N combattants, skippe les morts et les SURPRISED, et incrémente le round au wrap-around.
2. **Détection de fin** quand tous les enemies OU tous les PCs sont hors combat (morts, fuit, inconscients).
3. **Persistence** : `CombatState` (initiative, HP, conditions, round) sauvé en DB sur la row session, pour pouvoir reprendre une session après restart.
4. **Consommation de `SURPRISED`** : à la fin du premier tour d'un combattant surpris, la condition est retirée (il pourra jouer normalement au round 2).

## Scope

1. Étendre `CombatState` avec `combat_id: str` (uuid) pour la persistence.
2. Étendre `advance_turn` pour :
   - Skipper les morts.
   - Skipper les combattants `SURPRISED` tout en consommant leur surprise à la fin de leur tour skippé.
   - Incrémenter `round_number` au wrap-around.
   - Détecter la fin de combat et set `is_active=False`.
3. Ajouter `check_combat_end(state) -> CombatEndReason | None` qui retourne la raison de fin si applicable.
4. Persister `CombatState` en JSON sur `SessionRow.combat_state_json`.
5. Supporter la réaction au dégât : si un combattant est touché pendant qu'il est `CONCENTRATING`, déclencher `check_concentration_save` et drop la condition si raté.

## Fichiers à modifier

- [engine/combat.py](../../engine/combat.py) — `CombatState`, `advance_turn`, nouveau `check_combat_end`.
- [db/models.py](../../db/models.py) — colonne JSON sur session.
- [db/mappers.py](../../db/mappers.py) — sérialisation CombatState.
- [db/repositories/session_repo.py](../../db/repositories/) (s'il existe) — roundtrip.

## Implémentation — esquisse

```python
# engine/combat.py

from enum import StrEnum
import uuid

from engine.conditions import (
    ConditionType,
    consume_surprise_if_present,
    is_surprised,
    check_concentration_save,
    is_concentrating,
    drop_concentration,
)


class CombatEndReason(StrEnum):
    VICTORY = "victory"       # all enemies down/fled
    DEFEAT = "defeat"         # all PCs down
    FLED = "fled"             # all PCs successfully fled
    TRUCE = "truce"           # social resolution (task 81)


class CombatState(BaseModel):
    combat_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    combatants: list[Combatant]
    round_number: int = 1
    current_turn_index: int = 0
    is_active: bool = True
    end_reason: CombatEndReason | None = None


def advance_turn(state: CombatState) -> CombatState:
    """Advance to the next eligible combatant.

    Ticks conditions on the finishing combatant. Skips dead combatants
    and consumes SURPRISED (which skips the combatant silently). Checks
    end conditions on every advance. Increments round_number at wrap.
    """
    # 1. Tick conditions on the combatant who just finished
    current = state.combatants[state.current_turn_index]
    tick_durations(current.conditions)

    # If this combatant was SURPRISED, consume it now (end of their
    # first turn — they skipped, but the condition is cleared).
    consume_surprise_if_present(current.conditions)

    # 2. Walk forward to next eligible combatant
    num = len(state.combatants)
    start_index = state.current_turn_index
    next_index = start_index

    for _ in range(num):
        next_index = (next_index + 1) % num
        if next_index == 0 and next_index != start_index:
            state.round_number += 1

        candidate = state.combatants[next_index]
        if not candidate.is_alive:
            continue
        # NOTE: we do NOT skip SURPRISED here — they still need to
        # "take" their turn (which is a no-op) so that the turn
        # manager can consume the condition. The validator will
        # reject any action they try to take.
        break

    state.current_turn_index = next_index

    # 3. Check end conditions
    end = check_combat_end(state)
    if end is not None:
        state.is_active = False
        state.end_reason = end

    return state


def check_combat_end(state: CombatState) -> CombatEndReason | None:
    """Return the reason combat should end, or None if it continues."""
    players_alive = [
        c for c in state.combatants
        if c.side == CombatSide.PLAYER and c.is_alive
    ]
    enemies_alive = [
        c for c in state.combatants
        if c.side == CombatSide.ENEMY and c.is_alive
    ]
    if not enemies_alive:
        return CombatEndReason.VICTORY
    if not players_alive:
        return CombatEndReason.DEFEAT
    return None
```

**Concentration save on damage** — intégré dans `resolve_attack` ou dans un hook après-damage :

```python
def _on_damage_taken(combatant: Combatant, damage: int) -> None:
    """Hook called whenever a combatant loses HP. Handles concentration saves."""
    if damage <= 0:
        return
    if not is_concentrating(combatant.conditions):
        return
    save = check_concentration_save(combatant, damage)
    if save.outcome in (RollOutcome.FAILURE, RollOutcome.CRITICAL_FAILURE):
        drop_concentration(combatant)
        # Note: the caller should clear any effects that depended on
        # this concentration (e.g., Spirit Guardians). This hook just
        # drops the condition.
```

Appeler `_on_damage_taken` à la fin de `resolve_attack` et partout où un combattant prend des dégâts.

**Persistence DB** — sur `SessionRow`, ajouter `combat_state_json: Mapped[str | None]`. Dans `mappers.py`, `to_row()` sérialise via `state.model_dump_json()` et `to_domain()` désérialise.

## Acceptance criteria

- [ ] `CombatState` a un `combat_id` unique.
- [ ] `advance_turn` skip les morts et incrémente correctement `round_number`.
- [ ] Un combattant SURPRISED passe son tour (pas d'action possible — voir tâche [30](30_strict_combat_validators.md)) et la condition est consommée à la fin.
- [ ] `check_combat_end` retourne VICTORY / DEFEAT / None correctement.
- [ ] `is_active` passe à False et `end_reason` est set quand le combat finit.
- [ ] `CombatState` roundtrip via DB (save avec conditions / HP / round, charger, comparer).
- [ ] `_on_damage_taken` déclenche un save CON si le cible est CONCENTRATING, drop sur échec.

## Tests à ajouter

Dans `tests/test_combat.py` :

- `test_combat_state_generates_unique_id`.
- `test_advance_turn_skips_dead_combatants`.
- `test_advance_turn_increments_round_on_wrap`.
- `test_advance_turn_consumes_surprise_at_end_of_first_turn`.
- `test_advance_turn_sets_victory_when_all_enemies_dead`.
- `test_advance_turn_sets_defeat_when_all_pcs_dead`.
- `test_check_combat_end_returns_none_when_both_sides_alive`.
- `test_concentration_save_triggered_on_damage` — combatant concentrating takes 10 damage, CON save rolled.
- `test_concentration_dropped_on_failed_save`.
- `test_concentration_kept_on_successful_save`.

Dans `tests/test_db_repos.py` :

- `test_session_repository_roundtrips_combat_state` — full combat state with conditions.

## Hors scope

- **Ne pas** implémenter l'action economy (`Move + Action + Bonus + Reaction` budgets) — tâche [23](23_action_economy.md).
- **Ne pas** implémenter le zone movement — tâche [24](24_zone_movement_and_opportunity.md).
- **Ne pas** implémenter la logique FLEE (cas `FLED`) — tâche [32](32_flee_resolution.md).
- **Ne pas** implémenter TRUCE — tâche [81](81_social_resolution_mid_combat.md).
- **Ne pas** déclencher les NPC turns (scripted/LLM) — tâches [50](50_scripted_minion_ai.md)/[51](51_elite_behavior_profiles.md)/[52](52_boss_llm_tactician.md).

## Validation finale

```bash
uv run pytest tests/test_combat.py tests/test_db_repos.py -v
uv run ruff check engine/combat.py db/
uv run mypy engine/combat.py db/
```
