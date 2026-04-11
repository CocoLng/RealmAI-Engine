# Task 24 — Mouvement entre zones et attaques d'opportunité

**Phase** : 2 — Moteur de combat
**Dépendances** : [12](12_zone_model.md), [23](23_action_economy.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Avec le modèle de zones (tâche [12](12_zone_model.md)) et l'action economy (tâche [23](23_action_economy.md)) en place, on peut maintenant câbler **le mouvement réel entre zones pendant un combat** et les **attaques d'opportunité** (5e RAW : quitter le reach d'un ennemi hostile sans `Disengage` déclenche une attaque en réaction).

## Scope

1. Ajouter `Combatant.current_zone: str | None` — nom de la zone où se trouve le combattant (None si pas de zone-based combat).
2. Helper `move_combatant_to_zone(state, combatant, target_zone, location)` qui :
   - Valide que `target_zone` est adjacente à `combatant.current_zone` (via `Location.are_adjacent`).
   - Calcule le coût en feet (1 zone adjacente = 15 ft par défaut, double si `DIFFICULT_TERRAIN`).
   - Consomme le movement budget.
   - Déclenche une opportunity attack de chaque ennemi en mêlée dans la zone source (sauf si `Disengage` a été pris cette action).
   - Met à jour `current_zone`.
3. Ajouter un flag `disengaged_this_turn: bool` sur `Combatant.action_budget` (ou ajouter `used_disengage: bool`).
4. Action `Disengage` : set le flag, ne déclenche pas d'attaques d'opportunité ce tour.
5. Attaque d'opportunité = une attaque basique gratuite (consomme la Reaction de l'attaquant). Utilise `resolve_attack` existant.

## Fichiers à modifier

- [engine/combat.py](../../engine/combat.py) — `Combatant`, `ActionBudget`, nouveaux helpers.

## Implémentation — esquisse

```python
# engine/combat.py

from world.combat_zone import ZoneTag
from world.location import Location


class ActionBudget(BaseModel):
    # ... existing fields ...
    disengaged_this_turn: bool = False

    def reset_for_new_turn(self, base_speed_feet: int) -> None:
        self.movement_remaining_feet = base_speed_feet
        self.action_used = False
        self.bonus_action_used = False
        self.disengaged_this_turn = False


class Combatant(BaseModel):
    # ... existing fields ...
    current_zone: str | None = None


def move_combatant_to_zone(
    state: CombatState,
    combatant: Combatant,
    target_zone: str,
    location: Location,
) -> list[AttackResult]:
    """Move a combatant from its current zone to an adjacent zone.

    Returns a list of any opportunity attacks triggered by this movement.
    Raises ValueError if the move is illegal (non-adjacent, insufficient
    movement, no zones in location).
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
    assert target_zone_obj is not None  # validated by are_adjacent

    # Compute movement cost
    cost = 15  # base cost for 1 zone step
    if target_zone_obj.has_tag(ZoneTag.DIFFICULT_TERRAIN):
        cost *= 2

    consume_movement(combatant, cost)

    # Opportunity attacks — each hostile enemy in the source zone gets one
    # unless Disengage was used this turn.
    ooa_results: list[AttackResult] = []
    if not combatant.action_budget.disengaged_this_turn:
        for enemy in state.combatants:
            if enemy.name == combatant.name:
                continue
            if enemy.side == combatant.side:
                continue  # no OOA from allies
            if not enemy.is_alive:
                continue
            if enemy.current_zone != combatant.current_zone:
                continue  # not in melee range
            if enemy.action_budget.reaction_used_this_round:
                continue  # reaction already spent

            # Trigger opportunity attack
            try:
                consume_reaction(enemy)
                result = resolve_attack(
                    attacker=enemy,
                    defender=combatant,
                    weapon=_get_main_weapon(enemy),
                    state=state,
                )
                ooa_results.append(result)
                if not combatant.is_alive:
                    # Combatant died mid-move; abort.
                    return ooa_results
            except ValueError:
                # Can't attack for some reason; skip silently.
                pass

    # Complete the move
    combatant.current_zone = target_zone

    # Apply zone entry effects (hazards etc.)
    if target_zone_obj.has_tag(ZoneTag.HAZARD):
        # TODO: resolve hazard damage per-zone definition (future task)
        pass

    return ooa_results


def disengage(combatant: Combatant) -> None:
    """Action: suppress opportunity attacks for the remainder of this turn.
    Consumes the Action slot (5e RAW — Disengage is an Action)."""
    consume_action(combatant)
    combatant.action_budget.disengaged_this_turn = True
```

**Note** : `resolve_attack` existe déjà dans `engine/combat.py` avec la signature `resolve_attack(attacker, defender, weapon=None, state=None, ...)` — vérifier et adapter si besoin. L'appeler sans consommer l'Action de l'attaquant (OOA est une réaction, pas une action).

## Acceptance criteria

- [ ] `Combatant.current_zone: str | None`.
- [ ] `move_combatant_to_zone` raise si zone non adjacente.
- [ ] `move_combatant_to_zone` raise si movement budget insuffisant.
- [ ] Le coût est doublé sur zones `DIFFICULT_TERRAIN`.
- [ ] Sans `Disengage`, chaque ennemi en mêlée dans la zone source déclenche une OOA.
- [ ] Les OOA consomment la réaction de l'attaquant (plus d'OOA ce round).
- [ ] `disengage(combatant)` consomme l'Action et set le flag.
- [ ] Après `Disengage`, aucune OOA n'est déclenchée ce tour.
- [ ] Si un combattant meurt en plein move, la boucle arrête (pas d'OOA supplémentaires sur un cadavre).

## Tests à ajouter

Dans `tests/test_combat_zone_movement.py` (nouveau) :

- `test_move_to_adjacent_zone_succeeds`.
- `test_move_to_non_adjacent_zone_raises`.
- `test_move_with_insufficient_movement_raises`.
- `test_difficult_terrain_doubles_movement_cost`.
- `test_opportunity_attack_triggered_on_exit_without_disengage`.
- `test_disengage_suppresses_opportunity_attacks`.
- `test_ally_does_not_trigger_opportunity_attack`.
- `test_enemy_without_reaction_does_not_oa`.
- `test_dead_combatant_does_not_oa`.
- `test_combatant_dies_mid_move_aborts_remaining_oa`.
- `test_hazard_zone_tag_noted` (placeholder, the effect is TODO).

## Hors scope

- **Ne pas** implémenter les hazard zone effects en détail — placeholder TODO suffit pour cette tâche.
- **Ne pas** implémenter la réaction `Shield` ou autres sorts défensifs — futures tâches.
- **Ne pas** implémenter `Dash` pour doubler le mouvement — future tâche si besoin.
- **Ne pas** intégrer dans l'action pipeline — tâche [31](31_action_pipeline_combat_dispatch.md).
- **Ne pas** valider les actions hors-tour — tâche [30](30_strict_combat_validators.md).

## Validation finale

```bash
uv run pytest tests/test_combat_zone_movement.py tests/test_combat.py -v
uv run ruff check engine/combat.py
uv run mypy engine/combat.py
```
