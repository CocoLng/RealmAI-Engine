# Task 01 — Bugfix : bloquer MOVE pendant un combat actif

**Phase** : 0 — Bugfix immédiat
**Dépendances** : aucune
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Dans la campagne test, Mageta a pu utiliser `(Move) je vais explorer le corridor des illusions` alors que le beat 1 était `combat`. Aucun test de fuite n'a été demandé, aucune attaque d'opportunité, rien.

**Cause racine** : [engine/validators.py:337-378](../../engine/validators.py) — `validate_exploration_action()` n'a **aucun accès** au `combat_state`. Le `MOVE` est classé dans `EXPLORATION_ACTION_TYPES` ([engine/validators.py:52-61](../../engine/validators.py)) et [bot/action_pipeline.py:534-535](../../bot/action_pipeline.py) le dispatche sans consulter `self.combat_state`.

Cette tâche est le deuxième filet de sécurité Phase 0. L'**auto-conversion MOVE → FLEE** est reportée à la tâche [31](31_action_pipeline_combat_dispatch.md) (elle dépend du nouveau `detect_combat_trigger` et du FLEE resolution complet). Ici, on se contente d'un refus explicite avec un message clair pour l'utilisateur.

## Scope

1. Étendre la signature de `validate_exploration_action` pour accepter `combat_state: CombatState | None = None`.
2. Si `combat_state is not None and combat_state.is_active`, autoriser uniquement `LOOK`, `QUESTION`, `IMPROVISE` ; rejeter tout le reste (en particulier MOVE, TALK, SEARCH, INTERACT, PICKUP) avec un message explicite.
3. Passer `self.combat_state` à l'appel depuis `ActionPipeline._validate`.

## Fichiers à modifier

- [engine/validators.py](../../engine/validators.py) — fonction `validate_exploration_action` (lignes ~337-378).
- [bot/action_pipeline.py](../../bot/action_pipeline.py) — méthode `_validate`, ligne 534-535.

## Implémentation — esquisse

Dans `validators.py` :

```python
from engine.combat import CombatState

_EXPLORATION_ALLOWED_IN_COMBAT: frozenset[ActionType] = frozenset({
    ActionType.LOOK,
    ActionType.QUESTION,
    ActionType.IMPROVISE,
})


def validate_exploration_action(
    action: Action,
    combat_state: CombatState | None = None,
) -> ValidationResult:
    """Validate a non-combat action against its own rules.

    If ``combat_state`` is active, most exploration actions are rejected —
    only informational actions (Look, Question, Improvise) are permitted
    off-turn. Players must use Flee to escape combat (handled by the
    combat path, not here).
    """
    if action.action_type not in EXPLORATION_ACTION_TYPES:
        return ValidationResult(
            is_valid=False,
            error_message=(
                f"'{action.action_type.value}' is not an exploration action"
            ),
        )

    if combat_state is not None and combat_state.is_active:
        if action.action_type not in _EXPLORATION_ALLOWED_IN_COMBAT:
            return ValidationResult(
                is_valid=False,
                error_message=(
                    f"Impossible de faire '{action.action_type.value}' "
                    "en plein combat. Utilisez Flee pour tenter de fuir, "
                    "ou attendez votre tour."
                ),
            )

    # Existing field checks (PICKUP, MOVE, TALK, INTERACT) continue...
```

Dans `action_pipeline.py` :

```python
if action.action_type in EXPLORATION_ACTION_TYPES:
    return validate_exploration_action(eng_action, combat_state=self.combat_state)
```

## Acceptance criteria

- [ ] `validate_exploration_action` accepte un param `combat_state` optionnel.
- [ ] Quand `combat_state.is_active is True`, MOVE / TALK / SEARCH / INTERACT / PICKUP sont rejetés avec `is_valid=False` et un `error_message` explicite.
- [ ] LOOK, QUESTION et IMPROVISE sont toujours autorisés (off-turn actions permises).
- [ ] `ActionPipeline._validate` passe `self.combat_state` au validateur.
- [ ] Le joueur reçoit le message d'erreur côté Discord (le flow `ValidationResult.error_message` → narrator refuse est déjà câblé).

## Tests à ajouter

Dans `tests/test_validators.py` (ou nouveau `tests/test_validators_combat.py`) :

- `test_move_blocked_during_active_combat` — valide `validate_exploration_action(MOVE, combat_state=active)` retourne `is_valid=False`.
- `test_look_allowed_during_active_combat` — valide LOOK passe.
- `test_question_allowed_during_active_combat` — valide QUESTION passe.
- `test_improvise_allowed_during_active_combat` — valide IMPROVISE passe.
- `test_talk_blocked_during_active_combat` — valide TALK rejeté.
- `test_search_blocked_during_active_combat` — valide SEARCH rejeté.
- `test_exploration_unchanged_without_combat_state` — regression : sans `combat_state`, comportement identique à avant.
- `test_exploration_unchanged_with_inactive_combat_state` — un `CombatState(is_active=False)` ne bloque rien.

Dans `tests/bot/test_action_pipeline.py` :

- `test_pipeline_rejects_move_when_combat_active` — integration : session avec combat_state actif, `(Move) X` → `ValidationResult(is_valid=False)`.

## Hors scope

- **Ne pas** auto-convertir MOVE en FLEE — c'est la tâche [31](31_action_pipeline_combat_dispatch.md).
- **Ne pas** implémenter le FLEE resolver avec check DEX — tâche [32](32_flee_resolution.md).
- **Ne pas** valider l'action economy (Move budget, Action consumé) — tâche [30](30_strict_combat_validators.md).
- **Ne pas** bloquer les actions hors-tour finement — pour l'instant LOOK/QUESTION/IMPROVISE sont toujours OK, on affinera en [30](30_strict_combat_validators.md).

## Validation finale

```bash
uv run pytest tests/test_validators.py tests/bot/test_action_pipeline.py -v
uv run ruff check engine/validators.py bot/action_pipeline.py
uv run mypy engine/validators.py bot/action_pipeline.py
```
