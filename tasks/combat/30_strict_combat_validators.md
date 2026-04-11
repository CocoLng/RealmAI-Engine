# Task 30 — Validateurs de combat stricts

**Phase** : 3 — Validation & pipeline
**Dépendances** : [23](23_action_economy.md), [24](24_zone_movement_and_opportunity.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Les validators actuels [engine/validators.py](../../engine/validators.py) sont basiques : ils checkent que le combattant existe, n'est pas incapacité, que c'est son tour, et pour `validate_attack` qu'il y a une cible vivante. Ils **ne valident pas** :

- L'action economy (`consume_action` déjà utilisée ? ok non).
- La zone/range (cible en range pour attaque mêlée ? ranged ? LOS ?).
- Les slots de sort pour Cast Spell.
- La cohérence SURPRISED (un surpris ne peut rien faire → skip).
- Les actions hors-tour (en combat, off-turn, seuls LOOK/QUESTION/IMPROVISE sont permis).

Cette tâche refond les validators pour être **strict à la D&D 5e**. Toutes les règles 5e doivent être appliquées ici — pas dans le code de résolution.

## Scope

1. Étendre `validate_action` (le dispatcher combat) pour :
   - Rejeter si `is_surprised(actor.conditions)` (safety net — le turn manager devrait déjà skipper).
   - Consulter l'action economy et rejeter si l'action budget est épuisé.
   - Dispatcher selon le type d'action, et pour chaque type, valider les règles 5e spécifiques.
2. `validate_attack` : vérifier range (mêlée = même zone, ranged = n'importe quelle zone), cible vivante, cible côté adverse (pas d'ami feu), action budget (`action_used=False`).
3. `validate_cast_spell` : vérifier slot dispo, concentration conflict, range, cible valide.
4. `validate_move` (nouveau) : vérifier zone adjacente, movement budget suffisant.
5. `validate_disengage`, `validate_dodge`, `validate_help`, `validate_dash` (nouveaux types 5e — peuvent être traités comme sub-type de `Defend` ou nouvelles ActionType).
6. `validate_flee` : garder existant mais intégrer au flow action budget.
7. **Validation hors-tour** : nouveau check en tête de `validate_action` — si `_is_actors_turn(actor_name, state)` est False, rejeter toute action sauf les types autorisés off-turn (LOOK, QUESTION — gérés en chemin exploration de toute façon) et les réactions explicitement taguées.
8. **En chemin exploration** (`validate_exploration_action`) : durcir le check combat — déjà commencé en tâche [01](01_bugfix_move_blocked_in_combat.md), mais ici on affine : si combat actif, SEULS LOOK/QUESTION/IMPROVISE passent ; le reste retourne un message explicite "Attendez votre tour" ou "Utilisez Flee".

## Fichiers à modifier

- [engine/validators.py](../../engine/validators.py) — toutes les fonctions `validate_*`.

## Implémentation — esquisse

```python
# engine/validators.py

from engine.combat import CombatState, Combatant
from engine.conditions import is_surprised, is_incapacitated, cannot_move
from engine.spells import SPELL_CATALOG, can_cast_spell


def validate_action(action: Action, state: CombatState) -> ValidationResult:
    """Dispatcher — validate a combat action.

    Applies the common checks (turn order, surprised, incapacitated,
    action budget) before dispatching to the action-specific validator.
    """
    common = _validate_common(action, state)
    if common is not None:
        return common

    # Surprised safety net
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None  # checked in _validate_common
    if is_surprised(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.actor_name}' est surpris et ne peut rien faire ce tour.",
        )

    validators = {
        ActionType.ATTACK: validate_attack,
        ActionType.CAST_SPELL: validate_cast_spell,
        ActionType.DEFEND: validate_defend,
        ActionType.FLEE: validate_flee,
        ActionType.USE_ITEM: validate_use_item,
        ActionType.MOVE: validate_move_in_combat,  # new
    }
    validator = validators.get(action.action_type)
    if validator is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.action_type.value}' is not a combat action",
        )
    return validator(action, state)


def validate_attack(action: Action, state: CombatState) -> ValidationResult:
    """Validate an attack: action budget, target alive, range."""
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    # Action economy
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} has already used their Action this turn.",
        )

    # Target
    if action.target_name is None:
        return ValidationResult(is_valid=False, error_message="Attack requires a target")

    target = _find_combatant(action.target_name, state)
    if target is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Target '{action.target_name}' is not in combat",
        )
    if not target.is_alive:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.target_name}' is already dead",
        )
    if target.side == actor.side:
        return ValidationResult(
            is_valid=False,
            error_message=f"Cannot attack ally '{action.target_name}'",
        )

    # Range check (zone-based)
    weapon = _get_weapon(actor, action.weapon_name)
    range_ok = _check_range(actor, target, weapon)
    if not range_ok:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.target_name}' is not in range of "
                f"{action.weapon_name or 'your weapon'}"
            ),
        )

    return ValidationResult(is_valid=True)


def _check_range(
    attacker: Combatant,
    target: Combatant,
    weapon: Weapon | None,
) -> bool:
    """Check if target is in range. Zone-aware: melee = same zone,
    ranged = any zone (we don't simulate LOS)."""
    if attacker.current_zone is None or target.current_zone is None:
        return True  # zoneless combat — everyone in range
    if attacker.current_zone == target.current_zone:
        return True  # melee or ranged at point-blank
    if weapon is None:
        return False  # unarmed = melee only
    if weapon.has_property(WeaponProperty.RANGED):
        return True  # ranged weapon can reach other zones
    return False  # melee weapon out of zone = no


def validate_move_in_combat(action: Action, state: CombatState) -> ValidationResult:
    """Zone-aware move: must be adjacent, must have movement budget."""
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if cannot_move(actor.conditions):
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} cannot move (restrained/grappled/etc.)",
        )
    if actor.action_budget.movement_remaining_feet <= 0:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} has no movement remaining this turn.",
        )
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Move requires a target zone name",
        )
    # Adjacency check requires the Location — deferred to resolution.
    return ValidationResult(is_valid=True)


def validate_cast_spell(action: Action, state: CombatState) -> ValidationResult:
    actor = _find_combatant(action.actor_name, state)
    assert actor is not None

    if actor.spellcaster is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} cannot cast spells",
        )

    if action.spell_name is None:
        return ValidationResult(is_valid=False, error_message="Spell name required")

    spell = SPELL_CATALOG.get(action.spell_name)
    if spell is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"Unknown spell '{action.spell_name}'",
        )

    # Action budget (action-cost spell)
    if spell.casting_time == "action" and actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} has already used their Action",
        )
    if spell.casting_time == "bonus" and actor.action_budget.bonus_action_used:
        return ValidationResult(
            is_valid=False,
            error_message=f"{action.actor_name} has already used their Bonus Action",
        )
    # Reaction spells (Counterspell, Shield) have their own validation path.

    # Slot availability
    if not can_cast_spell(actor.spellcaster, spell.level):
        return ValidationResult(
            is_valid=False,
            error_message=f"No spell slot of level {spell.level} remaining",
        )

    # Concentration conflict
    if spell.requires_concentration and is_concentrating(actor.conditions):
        # Optional: allow the player to knowingly drop the previous one — for
        # now we warn but allow. The resolver will drop the old concentration.
        pass

    return ValidationResult(is_valid=True)
```

Et pour `validate_exploration_action` (déjà modifié en tâche 01, ici on **affine le message**) :

```python
_OFF_TURN_ALLOWED_IN_COMBAT: frozenset[ActionType] = frozenset({
    ActionType.LOOK,
    ActionType.QUESTION,
    ActionType.IMPROVISE,  # narrator arbitrates
})


def validate_exploration_action(
    action: Action,
    combat_state: CombatState | None = None,
) -> ValidationResult:
    # ... existing type check ...

    if combat_state is not None and combat_state.is_active:
        if action.action_type not in _OFF_TURN_ALLOWED_IN_COMBAT:
            return ValidationResult(
                is_valid=False,
                error_message=(
                    f"'{action.action_type.value}' impossible en plein combat. "
                    "Utilisez Flee pour fuir, ou attaquez un ennemi."
                ),
            )

    # ... existing field checks ...
```

## Acceptance criteria

- [ ] `validate_action` rejette SURPRISED (safety net).
- [ ] `validate_attack` rejette si Action déjà utilisée.
- [ ] `validate_attack` rejette friendly fire.
- [ ] `validate_attack` rejette cible hors range (melee vs ranged, zone-aware).
- [ ] `validate_move_in_combat` rejette sans movement budget.
- [ ] `validate_cast_spell` rejette sans slot, sans spellcaster, avec mauvais budget.
- [ ] `validate_exploration_action` rejette tout sauf LOOK/QUESTION/IMPROVISE pendant combat actif.
- [ ] Messages d'erreur clairs et actionables.

## Tests à ajouter

Dans `tests/test_validators.py` :

- `test_validate_attack_rejects_if_action_already_used`.
- `test_validate_attack_rejects_friendly_fire`.
- `test_validate_attack_rejects_out_of_range_melee_cross_zone`.
- `test_validate_attack_allows_ranged_cross_zone`.
- `test_validate_move_in_combat_rejects_without_movement`.
- `test_validate_move_in_combat_rejects_while_restrained`.
- `test_validate_cast_spell_rejects_without_slot`.
- `test_validate_cast_spell_rejects_if_bonus_action_used`.
- `test_validate_action_rejects_surprised_combatant`.
- `test_validate_exploration_rejects_move_in_combat`.
- `test_validate_exploration_allows_look_in_combat`.

## Hors scope

- **Ne pas** implémenter les sub-actions Dash/Disengage/Dodge comme nouveaux ActionType — les garder sous `Defend` ou les ajouter plus tard. Cette tâche couvre Attack, Cast, Move, Flee, Use Item.
- **Ne pas** implémenter les réactions automatiques (Shield, Counterspell) — futures tâches.
- **Ne pas** intégrer le dispatcher combat dans `action_pipeline.py` — tâche [31](31_action_pipeline_combat_dispatch.md).

## Validation finale

```bash
uv run pytest tests/test_validators.py -v
uv run ruff check engine/validators.py
uv run mypy engine/validators.py
```
