# Task 54 — Phase transitions du boss

**Phase** : 5 — IA tactique (NPC brains)
**Dépendances** : [22](22_multi_enemy_combat_state.md), [42](42_arc_generator_villain_stat_block.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Les bosses D&D classiques ont des **phases** : leur comportement et leurs capacités changent quand ils atteignent un seuil HP. Classique : à 50% HP, un dragon rentre en rage, un lich devient intangible, Vellus le Mentisseur révèle sa vraie forme.

Le `NPCStatBlock` inclut déjà une liste `phases: list[PhaseTransition]` (tâche [10](10_npc_stat_block_model.md)). Cette tâche **détecte le trigger** et **applique les effets** :

- Debloquer de nouvelles signature abilities.
- Appliquer un bonus d'attaque / save.
- Déclencher une narration spéciale via un prompt narrateur dédié (tâche [71](71_narrator_phase_transition_prompt.md)).

## Scope

1. Hook dans le pipeline de dégâts : après qu'un boss prend des dégâts, vérifier si une phase non-triggered voit son seuil franchi.
2. Si oui, marquer `phase.triggered = True`, appliquer les bonus au combattant, dé-bloquer les signatures listées dans `unlock_signatures`.
3. Stocker une `pending_phase_transition` sur le state pour que le narrator dédié (tâche 71) puisse la consommer.
4. Tests unitaires rigoureux sur les seuils (edge cases : dégâts qui traversent plusieurs phases en un coup, HP déjà en dessous du seuil quand la phase est ajoutée, etc.).

## Fichiers à créer/modifier

- **Créer** `engine/combat_phases.py` (ou étendre `engine/combat.py`).
- **Modifier** [engine/combat.py](../../engine/combat.py) — hook after-damage.

## Implémentation — esquisse

```python
# engine/combat_phases.py

from engine.combat import Combatant, CombatState
from engine.npc_stat_block import PhaseTransition


def check_phase_transition(
    combatant: Combatant,
    state: CombatState,
) -> list[PhaseTransition]:
    """Check if any non-triggered phases should fire now.

    Returns a list of phases that just triggered (empty if none).
    Mutates the phase.triggered flag and applies bonuses.
    """
    if combatant.character.stat_block is None:
        return []
    if not combatant.is_alive:
        return []

    sb = combatant.character.stat_block
    if not sb.phases:
        return []

    hp_ratio = combatant.character.hp / max(1, combatant.character.max_hp)
    hp_percent = int(hp_ratio * 100)

    triggered_now: list[PhaseTransition] = []
    for phase in sb.phases:
        if phase.triggered:
            continue
        if hp_percent <= phase.trigger_hp_percent:
            phase.triggered = True
            _apply_phase_effects(combatant, phase)
            triggered_now.append(phase)

    return triggered_now


def _apply_phase_effects(combatant: Combatant, phase: PhaseTransition) -> None:
    """Apply the mechanical bonuses of a phase transition."""
    sb = combatant.character.stat_block
    assert sb is not None

    # Unlock signatures
    if phase.unlock_signatures:
        for sig_name in phase.unlock_signatures:
            existing = next(
                (s for s in sb.signature_abilities if s.name == sig_name),
                None,
            )
            if existing is None:
                # Signature not in the base list — can't unlock something
                # that doesn't exist. Log warning.
                continue
            if existing.uses_remaining == 0:
                existing.uses_remaining = 1

    # Apply attack bonus (add to each NPCAttack's to_hit_bonus)
    if phase.attack_bonus != 0:
        for atk in sb.attacks:
            atk.to_hit_bonus += phase.attack_bonus

    # Save bonus is tracked on combatant directly (new field) or
    # applied via a condition. For simplicity, add a new condition
    # "phase_boost" with a permanent duration that grants the bonus.
    # Or store on the combatant directly:
    combatant.phase_save_bonus = combatant.phase_save_bonus + phase.save_bonus
```

Sur `Combatant` :

```python
class Combatant(BaseModel):
    # ... existing ...
    phase_save_bonus: int = 0
```

Hook après damage dans `engine/combat.py::resolve_attack` (ou dans le `_on_damage_taken` hook de la tâche 22) :

```python
def _on_damage_taken(combatant: Combatant, damage: int) -> None:
    # ... existing concentration save logic ...

    # Phase transition check
    from engine.combat_phases import check_phase_transition
    triggered = check_phase_transition(combatant, state)
    if triggered:
        # Store pending transition on the CombatState for the narrator
        for phase in triggered:
            state.pending_phase_narrations.append(PhaseTransitionEvent(
                boss_name=combatant.name,
                narrative_cue=phase.narrative_cue,
            ))
```

Ajouter `pending_phase_narrations: list[PhaseTransitionEvent]` sur `CombatState`.

```python
class PhaseTransitionEvent(BaseModel):
    boss_name: str
    narrative_cue: str
    consumed: bool = False
```

## Acceptance criteria

- [ ] `check_phase_transition` détecte le franchissement du seuil HP.
- [ ] Une phase ne se déclenche qu'une seule fois (`triggered=True`).
- [ ] Plusieurs phases peuvent se déclencher si les dégâts traversent plusieurs seuils.
- [ ] Les signatures listées dans `unlock_signatures` voient leur `uses_remaining` remonté à 1.
- [ ] `attack_bonus` est appliqué aux `to_hit_bonus` de toutes les NPCAttacks du boss.
- [ ] `save_bonus` est stocké sur `combatant.phase_save_bonus`.
- [ ] `pending_phase_narrations` est peuplé et consommable par le narrateur (tâche 71).
- [ ] Le boss mort ne déclenche pas de phase (cas edge).

## Tests à ajouter

Dans `tests/test_phase_transitions.py` (nouveau) :

- `test_phase_triggers_at_exact_hp_percent`.
- `test_phase_does_not_retrigger_after_heal_and_redamage`.
- `test_multiple_phases_can_trigger_in_single_hit`.
- `test_phase_unlocks_signature`.
- `test_phase_applies_attack_bonus_to_all_attacks`.
- `test_phase_applies_save_bonus_to_combatant`.
- `test_phase_event_added_to_state_pending_narrations`.
- `test_phase_does_not_trigger_on_dead_boss`.
- `test_phase_at_100_percent_hp_does_not_trigger_initially`.

## Hors scope

- **Ne pas** implémenter le prompt narrateur de phase — tâche [71](71_narrator_phase_transition_prompt.md).
- **Ne pas** détecter des "phase 3" ou plus exotiques — 1-2 phases suffisent pour le MVP.
- **Ne pas** implémenter un rollback de phase si le boss est soigné — 5e RAW : phase triggered is permanent.

## Validation finale

```bash
uv run pytest tests/test_phase_transitions.py tests/test_combat.py -v
uv run ruff check engine/combat_phases.py engine/combat.py
uv run mypy engine/combat_phases.py engine/combat.py
```
