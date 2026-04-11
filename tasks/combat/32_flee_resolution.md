# Task 32 — Résolution de FLEE (check DEX + sortie de combat)

**Phase** : 3 — Validation & pipeline
**Dépendances** : [31](31_action_pipeline_combat_dispatch.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Le `validate_flee` existe déjà dans [engine/validators.py](../../engine/validators.py) mais `_resolve_mechanics` dans `action_pipeline.py` ne fait rien d'intéressant pour FLEE. Avec l'auto-conversion MOVE→FLEE en place (tâche [31](31_action_pipeline_combat_dispatch.md)), FLEE devient une action essentielle.

**Règles D&D 5e simplifiées pour Flee** :
- Le combattant fait un **check DEX (Acrobatics) DC 12** contre les ennemis adjacents.
- **Succès** : il quitte la zone sans OOA, ET si `self._pending_flee_destination` est défini, il effectue le `MOVE` vers cette location après la fin du combat (s'il est le dernier du côté joueur à fuir).
- **Échec** : il reste, son tour est perdu, les OOA sont déclenchées normalement.
- **Multi-PC** : si plusieurs PCs tentent de fuir au même tour, c'est un **group check** — majority must succeed (5e RAW).

Pour le MVP, on simplifie encore : chaque PC fait son check solo, celui qui rate reste sur place, celui qui réussit sort du combat. Quand **tous** les PCs vivants sont sortis, le combat se termine avec `CombatEndReason.FLED`.

## Scope

1. Ajouter `_resolve_flee` dans `ActionPipeline._resolve_mechanics`.
2. Roll du check DEX via `engine/dice.roll_check`.
3. Succès → retirer le combattant du `CombatState` (ou marquer `fled=True`).
4. Échec → `MechanicsOutcome` décrivant l'échec + `consume_action` pour forfait.
5. Post-resolve : vérifier si tous les PCs ont fui → `CombatEndReason.FLED` + `MOVE` vers `_pending_flee_destination` si défini.
6. Poster l'embed de jet de dés (tâche [60](60_dice_embed_module.md)) via `_pending_dice_embeds`.

## Fichiers à modifier

- [bot/action_pipeline.py](../../bot/action_pipeline.py) — `_resolve_mechanics` pour FLEE.
- [engine/combat.py](../../engine/combat.py) — ajouter `fled: bool` sur `Combatant` et gestion dans `advance_turn` (skip fled combatants comme les dead).

## Implémentation — esquisse

```python
# engine/combat.py

class Combatant(BaseModel):
    # ... existing fields ...
    fled: bool = False


# advance_turn — skip fled combatants too
def advance_turn(state: CombatState) -> CombatState:
    # ...
    for _ in range(num):
        next_index = (next_index + 1) % num
        # ...
        candidate = state.combatants[next_index]
        if not candidate.is_alive or candidate.fled:
            continue
        break
    # ...


# check_combat_end — all PCs fled → FLED
def check_combat_end(state: CombatState) -> CombatEndReason | None:
    players_still_fighting = [
        c for c in state.combatants
        if c.side == CombatSide.PLAYER and c.is_alive and not c.fled
    ]
    enemies_alive = [
        c for c in state.combatants
        if c.side == CombatSide.ENEMY and c.is_alive
    ]
    if not enemies_alive:
        return CombatEndReason.VICTORY
    if not players_still_fighting:
        # All PCs are down or have fled
        any_pc_alive_fled = any(
            c.side == CombatSide.PLAYER and c.is_alive and c.fled
            for c in state.combatants
        )
        if any_pc_alive_fled:
            return CombatEndReason.FLED
        return CombatEndReason.DEFEAT
    return None
```

```python
# bot/action_pipeline.py

from engine.character import Ability, compute_modifier
from engine.dice import roll_check, RollOutcome


def _resolve_flee(self, action: InterpretedAction) -> MechanicsOutcome:
    """Roll a DEX check vs DC 12 to escape combat.

    On success, the PC is marked as fled and removed from the turn
    rotation. On failure, the PC stays and loses their action this turn.
    When all alive PCs have fled, combat ends with CombatEndReason.FLED,
    and the stored flee destination (from MOVE auto-conversion) is
    applied via change_location.
    """
    assert self.combat_state is not None
    combatant = None
    for c in self.combat_state.combatants:
        if c.name == action.actor_name:
            combatant = c
            break
    if combatant is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} is not in combat.",
            player_intent=self._build_player_intent(action),
        )

    dex_mod = compute_modifier(
        combatant.character.ability_scores.get(Ability.DEX)
    )
    check = roll_check(f"1d20+{dex_mod}", dc=12)
    outcome_desc: str
    intent = self._build_player_intent(action)

    if check.outcome in (RollOutcome.SUCCESS, RollOutcome.NEAR_SUCCESS, RollOutcome.CRITICAL_SUCCESS):
        combatant.fled = True
        outcome_desc = (
            f"{action.actor_name} réussit à fuir (DEX {check.total} vs DC 12) "
            "et s'échappe de la zone de combat."
        )
    else:
        # Failure: action consumed, combatant stays
        combatant.action_budget.action_used = True
        outcome_desc = (
            f"{action.actor_name} échoue à fuir (DEX {check.total} vs DC 12) "
            "et reste bloqué en combat."
        )

    # Store dice embed data for the caller to display
    self._pending_dice_embeds.append(("flee_check", check, action.actor_name))

    # Check if combat should end (all PCs fled)
    end = check_combat_end(self.combat_state)
    if end == CombatEndReason.FLED:
        self.combat_state.is_active = False
        self.combat_state.end_reason = end
        # Apply stored destination if any
        if self._pending_flee_destination and self.db_factory:
            from bot.world_navigation import change_location
            try:
                dest = change_location(
                    self.session,
                    self._pending_flee_destination,
                    db_factory=self.db_factory,
                )
                outcome_desc += f" Le groupe s'échappe vers {dest.name}."
                public = PublicEffects(location_change=dest.name)
            except Exception:
                public = PublicEffects()
        else:
            public = PublicEffects()
        return MechanicsOutcome(
            summary=outcome_desc,
            player_intent=intent,
            outcome_facts=outcome_desc,
            public_effects=public,
        )

    return MechanicsOutcome(
        summary=outcome_desc,
        player_intent=intent,
        outcome_facts=outcome_desc,
    )
```

Et dans le dispatch de `_resolve_mechanics` :

```python
if at == ActionType.FLEE:
    return await asyncio.to_thread(self._resolve_flee, action)
```

## Acceptance criteria

- [ ] `Combatant.fled: bool` (default False).
- [ ] `advance_turn` skip les combattants `fled`.
- [ ] `check_combat_end` retourne `FLED` quand tous les PCs sont fuits/morts avec au moins un fui.
- [ ] `_resolve_flee` roll un check DEX et marque `fled=True` sur succès.
- [ ] `_resolve_flee` consomme l'action sur échec.
- [ ] Quand tous les PCs ont fui, combat se termine et `change_location` est appelé si `_pending_flee_destination` défini.
- [ ] L'embed de jet de dés est stocké dans `_pending_dice_embeds` pour affichage.

## Tests à ajouter

Dans `tests/bot/test_action_pipeline.py` :

- `test_flee_success_marks_combatant_fled`.
- `test_flee_failure_consumes_action_stays_in_combat`.
- `test_flee_with_all_pcs_fled_ends_combat`.
- `test_flee_applies_stored_destination_on_full_escape`.
- `test_flee_dice_embed_added_to_pending`.

Dans `tests/test_combat.py` :

- `test_advance_turn_skips_fled_combatant`.
- `test_check_combat_end_returns_fled_when_all_pcs_gone_but_some_fled`.
- `test_check_combat_end_returns_defeat_when_all_pcs_dead_none_fled`.

## Hors scope

- **Ne pas** implémenter le group check multi-PC (chaque PC fait son solo pour le MVP).
- **Ne pas** déclencher d'OOA sur flee — la règle 5e RAW est que Flee = Disengage implicite (safe move). On laisse ça.
- **Ne pas** implémenter l'embed de combat end — tâche [80](80_combat_end_conditions.md).

## Validation finale

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/test_combat.py -v
uv run ruff check bot/action_pipeline.py engine/combat.py
uv run mypy bot/action_pipeline.py engine/combat.py
```
