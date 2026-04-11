# Task 81 — Résolution sociale mid-combat (TRUCE)

**Phase** : 8 — Fin de combat & intégration
**Dépendances** : [80](80_combat_end_conditions.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

D&D 5e autorise les joueurs à tenter une **résolution sociale** en plein combat : un check CHA (Persuasion / Intimidation / Deception) vs un DC défini par le PNJ. Si le PNJ accepte, c'est la **trêve** et le combat s'arrête.

Exemples :
- Un garde provoqué en duel accepte de se rendre si bien persuadé.
- Un marchand enragé baisse les bras s'il est intimidé.
- Un cultiste perd sa conviction face à un argument convaincant.

**Restrictions** :
- Les **mindless** mobs (goblins enragés, zombies) refusent automatiquement.
- Les **boss en phase 2** refusent automatiquement (trop investis).
- Le villain d'arc ne peut JAMAIS être truce (narrativement il est la fin du combat).

## Scope

1. Ajouter `aggression_threshold: int` sur `NPCStatBlock` — c'est le DC vs lequel les PCs doivent réussir un check CHA. Déjà présent dans tâche [10](10_npc_stat_block_model.md).
2. Ajouter `mindless: bool` sur `NPCStatBlock` (default False) — true pour les mobs sans esprit.
3. Autoriser `TALK` en combat **uniquement** si la cible est un NPC non-mindless non-boss-phase-2 non-villain.
4. Créer `bot/combat_truce.py` avec `attempt_truce(pipeline, action) -> bool` qui roule le check CHA et, sur succès, déclenche `finalize_combat(session, TRUCE)`.
5. Dispatcher dans `_resolve_mechanics` pour `TALK` en combat.
6. Poster un embed de dice roll (tâche 60) pour montrer le check.

## Fichiers à créer/modifier

- **Créer** `bot/combat_truce.py`
- **Modifier** [engine/npc_stat_block.py](../../engine/npc_stat_block.py) — `mindless` field.
- **Modifier** [bot/action_pipeline.py](../../bot/action_pipeline.py) — dispatcher TALK en combat.
- **Modifier** [engine/validators.py](../../engine/validators.py) — autoriser TALK en combat sous conditions.

## Implémentation — esquisse

```python
# engine/npc_stat_block.py — ajout

class NPCStatBlock(BaseModel):
    # ... existing fields ...
    mindless: bool = False
    """True for non-sentient creatures (zombies, enraged beasts, elementals)
    that cannot be reasoned with. Blocks TRUCE attempts."""
```

```python
# engine/validators.py — validate_action dispatcher

def validate_action(action: Action, state: CombatState) -> ValidationResult:
    # ... existing dispatch ...

    if action.action_type == ActionType.TALK:
        return validate_truce_attempt(action, state)

    # ...


def validate_truce_attempt(action: Action, state: CombatState) -> ValidationResult:
    actor = _find_combatant(action.actor_name, state)
    if actor is None:
        return ValidationResult(is_valid=False, error_message="Actor not in combat")
    if actor.action_budget.action_used:
        return ValidationResult(
            is_valid=False,
            error_message="Talk requires your Action",
        )
    if action.target_name is None:
        return ValidationResult(
            is_valid=False,
            error_message="Talk requires a target",
        )
    target = _find_combatant(action.target_name, state)
    if target is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"'{action.target_name}' is not in combat",
        )
    if target.side == actor.side:
        return ValidationResult(
            is_valid=False,
            error_message="Cannot truce with an ally",
        )

    sb = getattr(target.character, "stat_block", None)
    if sb is None:
        return ValidationResult(
            is_valid=False,
            error_message=f"{target.name} cannot be reasoned with",
        )
    if sb.mindless:
        return ValidationResult(
            is_valid=False,
            error_message=f"{target.name} is mindless — no truce possible",
        )

    return ValidationResult(is_valid=True)
```

```python
# bot/combat_truce.py

import logging

from ai.models import InterpretedAction
from engine.character import Ability, compute_modifier
from engine.combat import CombatSide, CombatState, Combatant, CombatEndReason
from engine.dice import roll_check, RollOutcome
from engine.npc_stat_block import NPCTier

logger = logging.getLogger(__name__)


def attempt_truce(
    actor: Combatant,
    target: Combatant,
    state: CombatState,
) -> tuple[bool, str]:
    """Try to convince an enemy NPC to stop fighting.

    Rolls a CHA check vs the target's aggression_threshold. Returns
    (succeeded, summary). On success, the caller should finalize
    combat with CombatEndReason.TRUCE.
    """
    sb = target.character.stat_block
    if sb is None:
        return (False, f"{target.name} ne peut pas être raisonné.")
    if sb.mindless:
        return (False, f"{target.name} est trop bestial pour parler.")

    # Boss in phase 2 — automatic refusal
    if sb.tier == NPCTier.BOSS:
        phase_2_triggered = any(
            p.triggered and p.trigger_hp_percent <= 50
            for p in sb.phases
        )
        if phase_2_triggered:
            return (
                False,
                f"{target.name} est dans une rage absolue — aucune parole ne l'atteint.",
            )

    dc = sb.aggression_threshold
    cha_mod = compute_modifier(
        actor.character.ability_scores.get(Ability.CHA)
    )
    # Add proficiency if the PC has Persuasion (simplification: always add +2 for PCs)
    check = roll_check(f"1d20+{cha_mod + 2}", dc)

    # Also consume the actor's Action
    actor.action_budget.action_used = True

    if check.outcome in (
        RollOutcome.SUCCESS,
        RollOutcome.CRITICAL_SUCCESS,
        RollOutcome.NEAR_SUCCESS,
    ):
        logger.info(
            "TRUCE success actor=%s target=%s roll=%d dc=%d",
            actor.name, target.name, check.total, dc,
        )
        # Mark all enemies as "fled" (narrative: they back down)
        for c in state.combatants:
            if c.side == CombatSide.ENEMY and c.is_alive:
                c.fled = True
        return (
            True,
            f"{actor.name} convainc {target.name} de cesser le combat (CHA {check.total} vs DC {dc}).",
        )

    return (
        False,
        f"{actor.name} tente de parler mais {target.name} refuse (CHA {check.total} vs DC {dc}).",
    )
```

**Dans `action_pipeline.py::_resolve_mechanics`**, ajouter le cas TALK en combat :

```python
if at == ActionType.TALK and self.combat_state is not None and self.combat_state.is_active:
    # Truce attempt
    actor = _find_combatant(action.actor_name, self.combat_state)
    target = _find_combatant(action.target_name, self.combat_state)
    if actor is None or target is None:
        return MechanicsOutcome(
            summary=f"{action.actor_name} cannot talk now",
            player_intent=intent,
        )

    from bot.combat_truce import attempt_truce
    succeeded, summary = attempt_truce(actor, target, self.combat_state)
    if succeeded:
        from bot.combat_end import finalize_combat
        end_summary = finalize_combat(self.session, CombatEndReason.TRUCE)
        self._pending_combat_end_summary = end_summary

    return MechanicsOutcome(
        summary=summary,
        player_intent=intent,
        outcome_facts=summary,
    )
```

## Acceptance criteria

- [ ] `NPCStatBlock.mindless` ajouté.
- [ ] `validate_truce_attempt` rejette les cibles mindless, les alliés, les cibles inexistantes.
- [ ] `attempt_truce` roule un CHA check vs `aggression_threshold`.
- [ ] Succès : tous les enemies marqués `fled=True`, retourne `(True, summary)`.
- [ ] Refus automatique pour mindless.
- [ ] Refus automatique pour boss en phase 2.
- [ ] L'action est consommée même en cas d'échec.
- [ ] `finalize_combat` est appelé avec `CombatEndReason.TRUCE` sur succès.
- [ ] Villain d'arc ne peut pas être truce (edge case via phase 2 ou explicit tag).

## Tests à ajouter

Dans `tests/bot/test_combat_truce.py` (nouveau) :

- `test_truce_success_vs_standard_enemy`.
- `test_truce_failure_below_dc`.
- `test_truce_auto_refused_for_mindless_target`.
- `test_truce_auto_refused_for_boss_in_phase_2`.
- `test_truce_consumes_action_on_failure`.
- `test_truce_marks_all_enemies_fled_on_success`.
- `test_validate_talk_in_combat_accepts_valid_target`.
- `test_validate_talk_rejects_ally_target`.
- `test_truce_triggers_combat_finalize`.

## Hors scope

- **Ne pas** gérer les choix d'approche (Persuasion vs Intimidation vs Deception) — MVP : simple CHA check.
- **Ne pas** implémenter un système de reputation persistant — trêve donne juste une fin de combat propre.
- **Ne pas** permettre au joueur de négocier mid-check (l'LLM pourrait le faire plus tard, MVP = simple roll).

## Validation finale

```bash
uv run pytest tests/bot/test_combat_truce.py -v
uv run ruff check bot/combat_truce.py engine/validators.py
uv run mypy bot/combat_truce.py engine/validators.py
```
