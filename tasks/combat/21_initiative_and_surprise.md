# Task 21 — Initiative 5e avec surprise à 3 cas

**Phase** : 2 — Moteur de combat
**Dépendances** : [13](13_surprised_condition.md), [20](20_combat_entry_module.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le `start_combat` actuel ([engine/combat.py:145-163](../../engine/combat.py)) roule `1d20 + DEX mod` pour tous les combattants et les trie. C'est correct pour un face-à-face, mais il ne supporte pas la **surprise 5e** :

- **Cas 1 — Agression joueur** : l'attaquant joueur agit en premier, les NPCs ciblés sont `SURPRISED` et skippent leur premier tour.
- **Cas 2 — Ambush** : les NPCs agresseurs agissent en premier, les PCs sont `SURPRISED`.
- **Cas 3 — Face-à-face** : initiative 5e standard, pas de surprise.

Cette tâche refond `start_combat` pour consommer le `CombatTrigger` produit par [20](20_combat_entry_module.md) et appliquer la surprise correctement.

## Scope

1. Étendre `start_combat` pour accepter un `CombatTrigger` optionnel.
2. Appliquer `ConditionType.SURPRISED` sur le bon camp selon `trigger.surprise_side`.
3. Pour `case 1 (PLAYERS surprise)` : le `aggressor_name` est placé en **premier** dans l'ordre (il a l'initiative par design), les autres PCs sont triés par initiative roll normal après lui, puis les NPCs.
4. Pour `case 2 (NPCS surprise)` : les NPCs agresseurs sont placés en premier, puis initiative normale pour le reste.
5. Pour `case 3 (BOTH_READY)` : comportement actuel, `1d20 + DEX` pour tous.
6. Persister l'initiative rollée sur chaque `Combatant` pour reprise de session.

## Fichiers à modifier

- [engine/combat.py](../../engine/combat.py) — fonction `start_combat`, peut-être `roll_initiative`.

## Implémentation — esquisse

```python
# engine/combat.py

from bot.combat_entry import CombatTrigger, InitiativeSide
from engine.conditions import ConditionType, apply_condition, ActiveCondition


def start_combat(
    combatants: list[Combatant],
    trigger: CombatTrigger | None = None,
) -> CombatState:
    """Roll initiative and build a CombatState.

    If ``trigger`` is provided, applies the surprise rules based on
    ``trigger.surprise_side``:
    - PLAYERS  → aggressor PC first, NPC enemies get SURPRISED.
    - NPCS     → ambusher NPCs first, all PCs get SURPRISED.
    - BOTH_READY → standard d20+DEX roll for everyone.

    If ``trigger`` is None, defaults to BOTH_READY behavior (backward
    compatible with existing tests).
    """
    # Always roll initiative for everyone — used as the secondary ordering
    # even when surprise is in effect (the surprise cases only override
    # the leader's position).
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
    else:
        raise ValueError(f"Unknown surprise_side: {trigger.surprise_side}")

    return CombatState(
        combatants=ordered,
        round_number=1,
        current_turn_index=0,
        is_active=True,
    )


def _sort_by_initiative(combatants: list[Combatant]) -> list[Combatant]:
    """Descending by initiative, tiebreak by raw DEX score (5e RAW)."""
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
    """Aggressor PC acts first, then standard initiative for the rest."""
    aggressor = [c for c in combatants if c.name == trigger.aggressor_name]
    others = [c for c in combatants if c.name != trigger.aggressor_name]
    return aggressor + _sort_by_initiative(others)


def _order_npc_surprise(
    combatants: list[Combatant],
    trigger: CombatTrigger,
) -> list[Combatant]:
    """Ambusher NPCs act first, then standard initiative for the rest."""
    ambushers = [c for c in combatants if c.name in trigger.enemy_names]
    ambushers_sorted = _sort_by_initiative(ambushers)
    others = [c for c in combatants if c.name not in trigger.enemy_names]
    return ambushers_sorted + _sort_by_initiative(others)


def _apply_surprise_to_enemies(
    combatants: list[Combatant],
    trigger: CombatTrigger,
) -> None:
    """Apply SURPRISED to enemies that were ambushed."""
    for c in combatants:
        if c.name in trigger.enemy_names and c.side == CombatSide.ENEMY:
            apply_condition(
                c.conditions,
                ActiveCondition(
                    condition_type=ConditionType.SURPRISED,
                    duration_rounds=0,  # consumed at end of first turn
                ),
            )


def _apply_surprise_to_players(combatants: list[Combatant]) -> None:
    """Apply SURPRISED to all players (ambushed)."""
    for c in combatants:
        if c.side == CombatSide.PLAYER:
            apply_condition(
                c.conditions,
                ActiveCondition(
                    condition_type=ConditionType.SURPRISED,
                    duration_rounds=0,
                ),
            )
```

**Note sur l'import circulaire** : `engine/combat.py` important de `bot/combat_entry.py` créerait un cycle (bot dépend de engine). Solution : déplacer `CombatTriggerKind`, `InitiativeSide`, `CombatTrigger` dans `engine/combat_trigger.py` (pure Python, utilisable par engine et bot) et les ré-exporter depuis `bot/combat_entry.py` pour la lisibilité.

⚠️ **Cette décision impacte la tâche [20](20_combat_entry_module.md)**. Dans l'ordre d'exécution, la tâche [20] est faite avant [21], donc [20] doit déjà placer `CombatTrigger` et enums dans `engine/combat_trigger.py`. Corriger cet oubli dans le scope de [20] OU faire un petit move ici.

## Acceptance criteria

- [ ] `start_combat(combatants, trigger=None)` garde la compatibilité backward (case 3 implicite).
- [ ] `start_combat(combatants, trigger=PLAYER_SURPRISE_TRIGGER)` place l'agresseur en tête, applique SURPRISED aux enemies visés.
- [ ] `start_combat(combatants, trigger=NPC_SURPRISE_TRIGGER)` place les ambushers en tête, applique SURPRISED à tous les PCs.
- [ ] Tiebreak DEX score brut respecté dans `_sort_by_initiative`.
- [ ] `CombatTrigger` vit dans `engine/combat_trigger.py` (déplacé depuis `bot/combat_entry.py` si nécessaire).

## Tests à ajouter

Dans `tests/test_combat.py` (ou nouveau `tests/test_combat_initiative.py`) :

- `test_start_combat_without_trigger_uses_standard_initiative` — regression.
- `test_start_combat_player_surprise_places_aggressor_first`.
- `test_start_combat_player_surprise_applies_surprised_to_enemies`.
- `test_start_combat_npc_surprise_places_ambushers_first`.
- `test_start_combat_npc_surprise_applies_surprised_to_players`.
- `test_start_combat_both_ready_sorts_by_initiative_dex_tiebreak`.
- `test_start_combat_preserves_initiative_values_on_combatants` — chaque combatant a son `initiative` field roll.
- `test_surprise_not_applied_on_aggressor` — le PC déclencheur n'est PAS SURPRISED dans case 1.
- `test_surprise_not_applied_on_ambushers` — les NPCs ambushers ne sont PAS SURPRISED dans case 2.

## Hors scope

- **Ne pas** consommer `SURPRISED` dans `advance_turn` — tâche [22](22_multi_enemy_combat_state.md).
- **Ne pas** persister en DB l'initiative order — tâche [22](22_multi_enemy_combat_state.md).
- **Ne pas** toucher à `enter_combat` — il délègue le rolling à `start_combat` (voir tâche [20] sketch).

## Validation finale

```bash
uv run pytest tests/test_combat_initiative.py tests/test_combat.py -v
uv run ruff check engine/combat.py engine/combat_trigger.py
uv run mypy engine/combat.py engine/combat_trigger.py
```
