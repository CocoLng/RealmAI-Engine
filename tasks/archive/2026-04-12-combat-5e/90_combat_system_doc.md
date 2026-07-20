# Task 90 — Documentation du système de combat

**Phase** : 9 — Documentation
**Dépendances** : **TOUTES les phases précédentes**
**Coordinateur** : [tasks/combat/README.md](README.md)

## Contexte

Une fois le système de combat complet et validé end-to-end (tâche [82](82_end_to_end_live_test.md)), il faut produire une **documentation développeur** de référence dans `docs/internal/COMBAT_SYSTEM.md`. Cette doc sert à deux publics :

ATTENTION : Les référence au phase et task directement dans le code ou la doc sont à éviter — elles vont devenir obsolètes. Les docs/ or docs/internal sont temporaires tous comme les tasks / plan proposer, donc il faut supprimer ses références dans le code et la doc. Par contre, dans le plan coordinateur, il faut garder les références aux tasks et docs pour expliquer l'historique des décisions qui seront mises dans archive.

1. **Les futurs agents** qui toucheront au combat (bugfix, extension, refactor) — pour qu'ils comprennent l'architecture et les règles sans relire tout le code.
2. **L'équipe humaine** qui voudra debugger ou ajouter une feature — pour éviter que les règles soient orphelines dans leur tête.

## Scope

Rédiger `docs/internal/COMBAT_SYSTEM.md` avec les sections suivantes :

1. **Vision** — recap 1 paragraphe du "quoi" et du "pourquoi".
2. **Architecture** — diagramme ASCII des flux :
   - Action → détection trigger → bootstrap combat → validation → résolution → narration.
   - Turn loop : advance_turn → check_end → NPC brain OU user input → resolve.
3. **Modèles data** — diagramme ER des classes clés : `CombatState`, `Combatant`, `NPCStatBlock`, `SignatureAbility`, `LegendaryAction`, `PhaseTransition`, `Zone`, `CombatTrigger`.
4. **Initiative & surprise — 3 cas** — tableau décisionnel + exemples.
5. **Action economy** — table des actions/bonus/réactions/movement par tour.
6. **NPC AI tiers** — table minion/elite/boss avec leurs brains respectifs.
7. **Pipeline d'une attaque** — trace step-by-step d'une attaque du joueur jusqu'à l'embed final.
8. **Triggers de combat** — les 4 déclencheurs et leurs chemins de détection.
9. **Cycle de vie du combat** — entrée → rounds → fin → cleanup.
10. **Points d'extension** — où ajouter une nouvelle condition, une nouvelle signature ability, un nouvel archétype.
11. **Anti-patterns à éviter** — règle d'or (LLM ≠ referee), couplage excessif, etc.
12. **Référence API** — fonctions publiques clés avec leur signature et leur usage.
13. **Checklist de contribution** — que vérifier avant de merger un PR combat.
14. **Historique des décisions** — lien vers les plans coordinateur et les specs.

## Fichiers à créer

- **Créer** `docs/internal/COMBAT_SYSTEM.md`

## Implémentation — esquisse

La doc est une **narration technique**, pas une liste de bullet points. Voici l'ossature :

```markdown
# Combat System — RealmAI-Engine

> Documentation de référence pour le système de combat D&D 5e-core
> implémenté dans RealmAI-Engine. Lecture obligatoire avant tout PR
> touchant au combat.

## Vision

RealmAI-Engine implémente un système de combat tour par tour fidèle à
Donjons & Dragons 5e "core" : initiative par combattant, action economy
(Move + Action + Bonus + Reaction), positionnement abstrait par zones,
conditions 5e, NPCs avec stat blocks complets, hybride IA scripted + LLM
pour les bosses. Le mantra : **l'LLM narre, l'engine arbitre**. Aucune
décision mécanique ne passe par un LLM.

## Architecture

### Flux global d'une action

```
Discord message/button
  → InterpretedAction (ai/interpreter)
    → ActionPipeline._validate
      → detect_combat_trigger (bot/combat_entry)
      → enter_combat → start_combat → session.combat_state
      → validate_action (engine/validators)
    → ActionPipeline._resolve_mechanics
      → resolve_attack / _resolve_flee / _resolve_signature
      → advance_turn (engine/combat)
      → TurnManager.on_turn_advanced
        → NPC brain (scripted/elite/boss) if next is NPC
        → CombatActionView + @ping if next is PC
    → ActionPipeline._narrate
      → narrator LLM + phase narrator if pending
    → Discord embeds posted
```

### Modèles data (simplifié)

```
CombatState
├── combatants: list[Combatant]
│   ├── character: Character (PC or derived from NPCStatBlock)
│   ├── action_budget: ActionBudget
│   ├── current_zone: str | None
│   ├── conditions: list[ActiveCondition]
│   └── legendary_points_remaining: int  (boss only)
├── round_number: int
├── current_turn_index: int
├── is_active: bool
├── end_reason: CombatEndReason | None
└── pending_phase_narrations: list[PhaseTransitionEvent]

NPCStatBlock
├── tier: MINION | ELITE | BOSS
├── archetype: str
├── multiattack_count: int
├── attacks: list[NPCAttack]
├── signature_abilities: list[SignatureAbility]
├── legendary_actions: list[LegendaryAction]
├── legendary_points_per_round: int
├── phases: list[PhaseTransition]
├── behavior_profile: AGGRESSIVE | DEFENSIVE | SUPPORT | TACTICAL
└── aggression_threshold: int

Location
├── ... narrative fields ...
├── combat_zones: list[Zone]
└── combat_triggers: dict[str, CombatTriggerDef]
```

## Initiative & Surprise — 3 cas

| Cas | Déclencheur | Qui agit en premier | Qui est SURPRISED |
|---|---|---|---|
| **1. Agression joueur** | ATTACK/lethal intent contre non-hostile | PC attaquant | NPCs ciblés |
| **2. Ambush** | INTERACT sur trigger OU provocation | NPCs aggresseurs | Tous les PCs |
| **3. Face-à-face** | Beat combat scripté, ou hostile reconnu | Initiative normale d20+DEX | Personne |

Exemple : Mageta vs Vellus dans "L'Écume du Vent" — beat 1 scripté, Vellus est hostile et visible → **case 3**.

Exemple : Mageta poignarde un marchand (IMPROVISE, is_lethal_intent=True) → **case 1**.

Exemple : Mageta tire un levier piégé qui libère 3 gobelins → **case 2**.

## Action economy

Chaque combattant, à chaque tour :

| Slot | Quantité | Notes |
|---|---|---|
| **Move** | Jusqu'à `speed` feet (30 ft default) | Consomme `movement_remaining_feet`. 1 zone ≈ 15 ft. |
| **Action** | 1 par tour | Attack / Cast / Dodge / Disengage / Flee / Use Item |
| **Bonus Action** | 1 par tour | Si class/spell le permet |
| **Reaction** | 1 par ROUND (off-turn) | Opportunity attack, Shield, Counterspell |

Reset : Move/Action/Bonus au début du tour. Reaction au début du round.

## NPC AI — 3 tiers

| Tier | Exemples | Brain | LLM | Signature |
|---|---|---|---|---|
| **Minion** | Goblin, bandit | `decide_minion_action` (scripted) | Non | — |
| **Elite** | Capitaine, brute, mage | `decide_elite_action` (behavior profile) | Non | 1 tirée librairie |
| **Boss** | Vellus, arc villain | `decide_boss_action` (LLM-tactician) | **Oui** | 2-3 custom + Legendary |

Le LLM-tactician produit du JSON, l'engine arbitre. Fallback sur scripted elite après 2 échecs.

## Règle d'or

L'LLM ne touche **JAMAIS** aux dés, dégâts, ou résolution mécanique. Son unique rôle est de :
- Décider de l'intention narrative ou tactique (output JSON structuré).
- Produire de la prose décrivant CE QUI S'EST PASSÉ selon le résultat de l'engine.

Tout code de combat qui utilise un LLM sans passer par une validation Pydantic stricte est un bug.

## Pipeline d'une attaque (step-by-step)

1. **Joueur** : clic "Attack" dans la CombatActionView.
2. **TargetSelectView** : joueur choisit "Vellus le Mentisseur".
3. **Callback** : construit `InterpretedAction(action_type=ATTACK, target_name="Vellus le Mentisseur")`.
4. **ActionPipeline.handle_action** :
   a. `_validate` → dispatcher combat → `validate_attack` → OK (action economy, range, target alive).
   b. `_resolve_mechanics` → `resolve_attack(mageta, vellus, weapon, state)`.
   c. `resolve_attack` roule `1d20+5` vs AC 16, touche, roule 1d8+3 dégâts.
   d. `_on_damage_taken(vellus, 8)` → check phase transition (pas encore 50%).
   e. `consume_action(mageta)` → action budget épuisé.
5. **advance_turn** → next is Vellus (NPC).
6. **TurnManager** : tour NPC → `decide_boss_action` → LLM-tactician → `TacticalDecision(action=signature, signature=Chant du Silence Éternel)`.
7. **execute_signature_ability** → Vellus utilise sa signature.
8. **advance_turn** → next is Mageta.
9. **Narrateur LLM** : reçoit le scene context avec COMBAT ACTIVE + recent events + HP vague des NPCs → prose tendu tour par tour.
10. **Dice embeds** posté pour l'attaque de Mageta ET l'effet de signature de Vellus.
11. **CombatActionView** re-postée pour Mageta avec `@ping`.

## Cycle de vie du combat

```
[NO COMBAT] → detect_combat_trigger → enter_combat → start_combat → [ROUND 1]
   ↓                                                                    ↓
[finalize_combat] ← check_combat_end (each advance_turn) ← [ROUND N]
   ↓
[CLEANUP] : is_active=False, transient conditions cleared, embed end posted
```

## Points d'extension

### Ajouter une nouvelle condition 5e

1. Ajouter dans `ConditionType` enum (engine/conditions.py).
2. Ajouter ses effets dans les helpers (ex : `is_frozen`, `cannot_attack_if_frozen`).
3. Si la condition influence l'advance_turn, hook dans `advance_turn`.
4. Si elle influence la validation, hook dans `_validate_common`.
5. Tests unitaires dans `tests/test_conditions.py`.

### Ajouter un nouvel archétype NPC

1. Ajouter un builder `_build_my_archetype()` dans `engine/npc_library.py`.
2. Ajouter à `ARCHETYPE_BUILDERS`.
3. Tests dans `tests/test_npc_library.py`.
4. Si le prompt world generator doit l'utiliser, étendre `system_world_generator.txt` avec le nouveau role.

### Ajouter une nouvelle legendary action

1. L'arc generator ou le stat block fournit la definition.
2. Les effects sont résolus via le pipeline `execute_signature_ability`.
3. L'heuristique `_pick_legendary` peut être étendue pour prioriser.

## Anti-patterns

- ❌ **LLM qui roule des dés** : "the dragon rolls 18 on its attack" dans le prompt. Jamais. L'engine roule, le LLM lit.
- ❌ **Validation côté résolution** : si un check légal se fait dans `_resolve_mechanics` au lieu de `_validate`, c'est un bug. La séparation est stricte.
- ❌ **Couplage bot → engine** : le dossier `engine/` est pur Python, zéro import de `bot/` ou `ai/`. Si un helper engine a besoin d'un concept bot, passer par une injection de dépendance.
- ❌ **Shared state sur les archétypes** : `get_archetype()` doit retourner une copie fraîche. Sinon les `uses_remaining` décrémentés fuitent entre combats.

## Référence API

### `engine/combat.py`

- `start_combat(combatants, trigger=None) -> CombatState` — roll initiative, apply surprise, build state.
- `advance_turn(state) -> CombatState` — skip dead/fled, consume surprise, check end.
- `check_combat_end(state) -> CombatEndReason | None` — detect victory/defeat/fled.
- `resolve_attack(attacker, defender, weapon, state) -> AttackResult` — attack roll → damage → apply.
- `move_combatant_to_zone(state, combatant, zone, location) -> list[AttackResult]` — zone move + OOA.

### `bot/combat_entry.py`

- `detect_combat_trigger(action, session) -> CombatTrigger | None` — examine action, return trigger or None.
- `enter_combat(session, trigger, db_factory) -> CombatState` — build party-wide state.

### `engine/npc_ai/`

- `scripted.decide_minion_action(...)` — heuristic simple.
- `elite.decide_elite_action(...)` — profile-driven.
- `boss_brain.decide_boss_action(..., tactician)` — LLM-tactician with fallback.

### `engine/combat_phases.py`

- `check_phase_transition(combatant, state) -> list[PhaseTransition]` — detect seuil HP, apply buffs.

## Checklist de contribution

Avant de merger un PR qui touche au combat :

- [ ] `uv run pytest` tous verts, incluant `tests/scenarios/test_combat_system_e2e.py`.
- [ ] `uv run ruff check .` et `uv run mypy .` clean.
- [ ] Nouveau code ajoute des tests unitaires couvrant les edge cases.
- [ ] Si un LLM est ajouté, sa sortie est strictement validée par Pydantic et fallback en cas d'échec.
- [ ] Si un nouveau ActionType est ajouté, tous les validators sont étendus.
- [ ] Si une condition est ajoutée, `advance_turn` et `_validate_common` sont considérés.
- [ ] La doc `COMBAT_SYSTEM.md` est mise à jour si l'API publique change.
- [ ] Non-regression : la campagne Mageta (test e2e) passe toujours.

## Historique des décisions

- **Plan coordinateur** : `~/.claude/plans/glimmering-gliding-giraffe.md` — vision, décisions de scope, index des tâches.
- **Spec design combat** : (optionnel) `docs/superpowers/specs/2026-04-11-combat-system-design.md`.
- **Tâches du chantier** : `tasks/combat/README.md` + `tasks/combat/*.md`.

---

*Dernière mise à jour : par la task 90 à la fin du chantier combat.*
```

## Acceptance criteria

- [ ] `docs/internal/COMBAT_SYSTEM.md` existe et couvre toutes les sections listées.
- [ ] Les diagrammes ASCII sont lisibles et reflètent le code réel.
- [ ] Les exemples référencent des cas concrets (Mageta vs Vellus).
- [ ] Les règles d'or et anti-patterns sont clairs.
- [ ] La checklist de contribution est actionnable.
- [ ] Les liens vers le plan et les tasks fonctionnent.

## Tests à ajouter

Pas de tests de code pour cette tâche — c'est de la doc. Une revue humaine suffit.

## Validation finale

Relecture manuelle par le mainteneur. Vérifier que :
- Un nouveau développeur qui lit cette doc comprend l'architecture en < 20 minutes.
- Un agent Claude appelé sur un bug combat peut commencer par lire cette doc et avoir le contexte suffisant.
- Les exemples ne contredisent pas le code.
