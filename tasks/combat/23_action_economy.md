# Task 23 — Action economy 5e (Move + Action + Bonus Action + Reaction)

**Phase** : 2 — Moteur de combat
**Dépendances** : [22](22_multi_enemy_combat_state.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

En 5e, chaque combattant a à son tour :
- **Move** : jusqu'à `speed` pieds (≈ 1-2 zones dans notre modèle abstrait).
- **Action** : une action principale (Attack, Cast, Dash, Disengage, Dodge, Help, Hide, Ready, Search, Use Object).
- **Bonus Action** : optionnelle, dépend des features/sorts (off-hand attack, sort bonus, etc.).
- **Reaction** : 1/round, déclenchée off-turn (opportunity attack, Shield, Counterspell).

Aujourd'hui, `Combatant` ne track **aucun** de ces budgets — le joueur peut théoriquement faire 3 attaques en un tour. Cette tâche ajoute le tracking et le reset par round.

## Scope

1. Ajouter un modèle `ActionBudget` sur `Combatant` (ou nested dataclass dans combat.py) :
   - `movement_remaining_feet: int`
   - `action_used: bool`
   - `bonus_action_used: bool`
   - `reaction_used_this_round: bool`
2. Reset le budget au début du tour de chaque combattant.
3. Exposer des helpers :
   - `consume_action(combatant)` — raise si déjà utilisé.
   - `consume_bonus_action(combatant)` — idem.
   - `consume_movement(combatant, feet)` — soustrait, raise si insuffisant.
   - `consume_reaction(combatant)` — reaction, raise si déjà utilisée ce round.
4. Les validators de combat (tâche [30](30_strict_combat_validators.md)) consulteront ces états pour rejeter les actions illégales.
5. Persister le budget dans `CombatState` (roundtrip DB).

## Fichiers à modifier

- [engine/combat.py](../../engine/combat.py) — `Combatant`, `advance_turn`, nouveaux helpers.

## Implémentation — esquisse

```python
# engine/combat.py

class ActionBudget(BaseModel):
    """Per-turn action budget for a combatant, 5e RAW."""
    movement_remaining_feet: int = Field(default=30, ge=0)
    action_used: bool = False
    bonus_action_used: bool = False
    reaction_used_this_round: bool = False

    def reset_for_new_turn(self, base_speed_feet: int) -> None:
        self.movement_remaining_feet = base_speed_feet
        self.action_used = False
        self.bonus_action_used = False
        # reaction is NOT reset — it's per-round, reset at round start


class Combatant(BaseModel):
    # ... existing fields ...
    action_budget: ActionBudget = Field(default_factory=ActionBudget)


# Helpers

def consume_action(combatant: Combatant) -> None:
    if combatant.action_budget.action_used:
        raise ValueError(
            f"{combatant.name} has already used their Action this turn"
        )
    combatant.action_budget.action_used = True


def consume_bonus_action(combatant: Combatant) -> None:
    if combatant.action_budget.bonus_action_used:
        raise ValueError(
            f"{combatant.name} has already used their Bonus Action this turn"
        )
    combatant.action_budget.bonus_action_used = True


def consume_movement(combatant: Combatant, feet: int) -> None:
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
    if combatant.action_budget.reaction_used_this_round:
        raise ValueError(
            f"{combatant.name} has already used their Reaction this round"
        )
    combatant.action_budget.reaction_used_this_round = True
```

**Reset dans `advance_turn`** :

```python
def advance_turn(state: CombatState) -> CombatState:
    # ... existing tick conditions + surprise consume ...

    # Track if we wrapped to a new round
    # ... existing wrap detection ...

    # Reset action budget for the new current combatant
    new_current = state.combatants[next_index]
    base_speed = new_current.character.speed  # 5e default 30
    new_current.action_budget.reset_for_new_turn(base_speed)

    # Reset reactions at start of round (wrap detected)
    if round_incremented:
        for c in state.combatants:
            c.action_budget.reaction_used_this_round = False

    # ... check_combat_end ...
    return state
```

**Speed source** : `Character` a déjà un champ `speed: int` (généralement 30 ft). Utiliser. Pour les NPCs sans ce champ dans leur stat block, default 30 ft.

## Acceptance criteria

- [ ] `Combatant.action_budget` existe et est reset au début du tour.
- [ ] `consume_action` raise si déjà utilisé.
- [ ] `consume_bonus_action` raise si déjà utilisée.
- [ ] `consume_movement` raise si insuffisant, soustrait sinon.
- [ ] `consume_reaction` raise si déjà utilisée ce round.
- [ ] Les réactions sont reset au wrap-around de round, pas au début de tour.
- [ ] Le budget persiste dans `CombatState` et roundtrip DB correctement.

## Tests à ajouter

Dans `tests/test_combat.py` :

- `test_action_budget_defaults_reset_on_turn_start`.
- `test_consume_action_raises_on_second_call`.
- `test_consume_bonus_action_independent_from_action`.
- `test_consume_movement_with_insufficient_budget_raises`.
- `test_consume_movement_partial_spend`.
- `test_consume_reaction_raises_on_second_in_same_round`.
- `test_reaction_budget_resets_on_new_round`.
- `test_advance_turn_resets_action_budget_for_new_combatant`.
- `test_action_budget_roundtrips_via_db` (dans `tests/test_db_repos.py`).

## Hors scope

- **Ne pas** intégrer ces helpers dans les validators — tâche [30](30_strict_combat_validators.md).
- **Ne pas** ajouter les types d'action 5e (Dash, Disengage, Dodge) comme ActionType — ils peuvent être gérés via `Defend` existant ou un nouveau `Action.subtype` plus tard.
- **Ne pas** implémenter les réactions auto-déclenchées (opportunity attack, Shield) — tâche [24](24_zone_movement_and_opportunity.md) pour opportunity, autres reportés.

## Validation finale

```bash
uv run pytest tests/test_combat.py -v
uv run ruff check engine/combat.py
uv run mypy engine/combat.py
```
