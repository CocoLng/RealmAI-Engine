# Task 53 — Legendary Actions off-turn

**Phase** : 5 — IA tactique (NPC brains)
**Dépendances** : [22](22_multi_enemy_combat_state.md), [52](52_boss_llm_tactician.md)
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

En D&D 5e, les legendary monstres (dragons, liches, Vellus le Mentisseur) ont **3 legendary points** par round qu'ils peuvent dépenser **après le tour de chaque autre créature** pour des actions off-turn. Ça donne le feeling "le boss est partout, il ne laisse jamais les PCs respirer".

**Règles 5e adaptées** :
- 3 points reset au **début du tour du boss** (pas au début du round).
- Les legendary actions sont déclenchées **juste après** la fin du tour de n'importe quel autre combattant (pas entre des dégâts, pas en milieu d'action).
- Menu : chaque legendary action a un coût en points (1, 2, ou 3).
- Max 1 legendary action de coût ≥ 2 par round (empêche le spam de la plus puissante).
- Le boss choisit OU NON d'utiliser ses points. Il peut passer pour garder ses points pour un meilleur timing.

## Scope

1. Ajouter `legendary_points_remaining: int` sur `Combatant` (default 0).
2. Reset à `stat_block.legendary_points_per_round` au début du tour du boss.
3. Hook dans `advance_turn` : **après** avoir choisi le prochain combattant, appeler `maybe_spend_legendary_action(state, boss, previous_combatant)` qui demande au boss brain (scripted ou LLM) s'il veut dépenser.
4. Créer `engine/npc_ai/legendary.py` avec la logique de décision.
5. Intégration avec le LLM-tactician : si `TacticalDecision.legendary_action_name` est défini, exécuter avant la décision principale (ou séparément, selon le timing).

## Fichiers à créer/modifier

- **Modifier** [engine/combat.py](../../engine/combat.py) — `Combatant.legendary_points_remaining`, `advance_turn` hook.
- **Créer** `engine/npc_ai/legendary.py`

## Implémentation — esquisse

```python
# engine/combat.py

class Combatant(BaseModel):
    # ... existing fields ...
    legendary_points_remaining: int = 0
    """Points available for legendary actions this round. Reset at the
    start of this combatant's own turn (5e RAW)."""


def advance_turn(state: CombatState) -> CombatState:
    # ... existing logic to tick conditions, find next combatant ...

    # Hook: after any PC's turn, any living boss can spend legendary actions
    previous = state.combatants[start_index]
    if previous.is_alive and previous.side == CombatSide.PLAYER:
        _trigger_legendary_actions_from_bosses(state, previous)

    # Reset legendary points for the new current combatant if it's a boss
    new_current = state.combatants[next_index]
    if (
        new_current.character.stat_block is not None
        and new_current.character.stat_block.tier == NPCTier.BOSS
    ):
        new_current.legendary_points_remaining = (
            new_current.character.stat_block.legendary_points_per_round
        )

    # ... check_combat_end, return ...
```

```python
# engine/npc_ai/legendary.py

from engine.combat import Combatant, CombatState
from engine.npc_stat_block import LegendaryAction, NPCTier


def maybe_spend_legendary_action(
    state: CombatState,
    boss: Combatant,
    previous_combatant: Combatant,
) -> str | None:
    """Ask the boss AI whether to spend a legendary action now.

    Returns a summary string if an action was taken, None otherwise.
    """
    if boss.character.stat_block is None:
        return None
    if boss.character.stat_block.tier != NPCTier.BOSS:
        return None
    if not boss.is_alive or boss.fled:
        return None
    if boss.legendary_points_remaining <= 0:
        return None

    available = [
        la for la in boss.character.stat_block.legendary_actions
        if la.cost <= boss.legendary_points_remaining
    ]
    if not available:
        return None

    # Simple heuristic for MVP: if cost-1 action available, spend it
    # (cheap damage/attack). For cost-3 (big signature), spend only
    # after half the round has elapsed AND HP < 50%.
    chosen = _pick_legendary(available, boss, state)
    if chosen is None:
        return None

    boss.legendary_points_remaining -= chosen.cost
    return _execute_legendary(chosen, boss, previous_combatant, state)


def _pick_legendary(
    options: list[LegendaryAction],
    boss: Combatant,
    state: CombatState,
) -> LegendaryAction | None:
    """Heuristic selection — spend cost-1 actions eagerly, cost-3 rarely."""
    hp_ratio = boss.character.hp / max(1, boss.character.max_hp)

    # Cost 3 action only when HP low (desperate measure)
    cost_3 = [la for la in options if la.cost == 3]
    if cost_3 and hp_ratio < 0.3:
        return cost_3[0]

    # Cost 2 action when positioning or tactical advantage possible
    cost_2 = [la for la in options if la.cost == 2]
    if cost_2:
        return cost_2[0]

    # Cost 1 — spend eagerly
    cost_1 = [la for la in options if la.cost == 1]
    if cost_1:
        return cost_1[0]

    return None


def _execute_legendary(
    action: LegendaryAction,
    boss: Combatant,
    target: Combatant,
    state: CombatState,
) -> str:
    """Execute a legendary action's effects on the target.

    For MVP, most legendary actions are "attack" or "small AoE" — resolve
    via the existing signature effect pipeline (task 51).
    """
    from engine.npc_ai.elite import execute_signature_ability
    # Treat legendary as a one-shot signature with these effects
    fake_sig = SignatureAbility(
        name=action.name,
        description=action.description,
        usage="at_will",
        uses_remaining=None,
        action_cost="reaction",  # legendary is off-turn
        effects=action.effects,
    )
    summaries = execute_signature_ability(boss, fake_sig, [target], state)
    return f"[LEGENDARY] {boss.name} uses {action.name}: {'; '.join(summaries)}"
```

**Note sur le LLM-tactician** : quand le LLM choisit une legendary action via `TacticalDecision.legendary_action_name`, c'est géré séparément du choix de tour principal. L'idée : pendant le **tour du boss**, le LLM décide son action principale ; pendant les **tours entre**, la logique scripted de `maybe_spend_legendary_action` gère le off-turn. C'est plus simple et évite 3-4 appels LLM par round.

**Alternative** (plus riche, hors MVP) : le LLM tactician est appelé AUSSI entre les tours pour décider des legendary actions. Reporté.

## Acceptance criteria

- [ ] `Combatant.legendary_points_remaining` existe.
- [ ] Reset au début du tour du boss à `legendary_points_per_round`.
- [ ] `advance_turn` appelle `maybe_spend_legendary_action` après chaque tour de PC.
- [ ] L'heuristique dépense les cost-1 en priorité, cost-2 quand possible, cost-3 uniquement si HP critique.
- [ ] Le coût est décrémenté correctement.
- [ ] Les effets de la legendary action sont appliqués via le pipeline de signature existant.
- [ ] Un summary est retourné et peut être affiché au joueur.

## Tests à ajouter

Dans `tests/test_legendary_actions.py` (nouveau) :

- `test_legendary_points_reset_at_boss_turn_start`.
- `test_legendary_points_persist_between_pc_turns`.
- `test_maybe_spend_returns_none_when_no_points`.
- `test_maybe_spend_returns_none_for_non_boss`.
- `test_heuristic_spends_cost_1_eagerly`.
- `test_heuristic_prefers_cost_3_when_hp_critical`.
- `test_legendary_action_decrements_points`.
- `test_legendary_action_applies_effects_via_signature_pipeline`.
- `test_legendary_action_not_triggered_when_boss_dead`.

## Hors scope

- **Ne pas** donner le contrôle LLM sur les legendary actions off-turn — MVP = scripted heuristic.
- **Ne pas** implémenter "max 1 cost-3 action per round" restriction — peut être ajouté si nécessaire après test.
- **Ne pas** implémenter les phase transitions — tâche [54](54_phase_transitions.md).

## Validation finale

```bash
uv run pytest tests/test_legendary_actions.py tests/test_combat.py -v
uv run ruff check engine/npc_ai/legendary.py engine/combat.py
uv run mypy engine/npc_ai/legendary.py engine/combat.py
```
