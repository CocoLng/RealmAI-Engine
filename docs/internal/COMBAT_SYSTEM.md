# Combat System — RealmAI-Engine

Documentation de référence du système de combat D&D 5e-core implémenté dans le dépôt. Lecture utile avant tout PR qui touche au combat. Cette doc est un **survol architectural** ; pour le détail des règles, voir [GAME_ENGINE.md](GAME_ENGINE.md), et pour le flux d'action complet voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md).

> Règle d'or : **l'LLM narre, l'engine arbitre**. Aucune décision mécanique ne passe par un LLM. Même le boss LLM-tactician ne produit qu'un `NPCActionPlan` Pydantic que l'engine valide et résout.

## Vision

Combat tour par tour fidèle au SRD 5e "core" : initiative par combattant, action economy (Move + Action + Bonus + Reaction), positionnement par **zones abstraites** (pas de grille), conditions SRD complètes, NPCs avec stat blocks riches (minions/elites/bosses), IA hybride scripted + LLM-tactician pour les bosses. Le combat est **orthogonal** : il peut se déclencher n'importe où dans l'exploration via quatre triggers distincts, pas lié aux beats narratifs.

## Architecture

### Flux global d'une action combat

```
Joueur clique "Attack" (Discord button)
  └─► CombatActionView → TargetSelectView → construit un InterpretedAction
        └─► ActionPipeline.run (bot/action_pipeline.py)
              │
              ├─ Phase 3 _validate
              │   ├─ MOVE en combat actif → auto-convert en FLEE
              │   ├─ Si pas de combat actif → detect_combat_trigger
              │   │     └─ match → enter_combat → start_combat (CombatState posé)
              │   └─ validate_action (ou validate_exploration_action si hors combat)
              │
              ├─ Phase 4 _resolve_mechanics
              │   ├─ ATTACK → resolve_attack (PC) ou resolve_npc_attack
              │   ├─ CAST_SPELL → resolve_spell
              │   ├─ FLEE → _resolve_flee (DEX DC 12)
              │   ├─ TALK en combat → _resolve_talk_in_combat (TRUCE)
              │   └─ consume_action / consume_movement / etc.
              │
              ├─ advance_turn (tick conditions, skip morts/fuis, reset budget,
              │                jet de recharge 5-6 des signatures épuisées,
              │                check_combat_end, queue legendary actions boss)
              │
              ├─ TurnManager.on_action_resolved
              │   ├─ Si next = NPC → decide_action_for → execute_action_plan
              │   ├─ Si next = PC → re-post CombatActionView + ping
              │   └─ _persist_state (checkpoint combat_state_json)
              │
              └─ Phase 6 _narrate (narrator LLM + phase narrator si pending)
                    └─► Embed narratif + dice embeds + state embed
```

### Modules clés

| Responsabilité | Fichier |
|---|---|
| Moteur combat (turn loop, attaques, zones, action economy) | [engine/combat.py](../../engine/combat.py) |
| Détection de trigger + bootstrap party-wide | [bot/combat_entry.py](../../bot/combat_entry.py) |
| Orchestration tour-par-tour côté Discord | [bot/combat_turn_manager.py](../../bot/combat_turn_manager.py) |
| Fin de combat centralisée (XP, loot, cleanup) | [bot/combat_end.py](../../bot/combat_end.py) |
| Résolution sociale TRUCE mid-combat | [bot/combat_truce.py](../../bot/combat_truce.py) |
| Stat blocks D&D 5e-style pour NPCs | [engine/npc_stat_block.py](../../engine/npc_stat_block.py) |
| Librairie d'archétypes combat | [engine/npc_library.py](../../engine/npc_library.py) |
| Zones abstraites + triggers | [world/combat_zone.py](../../world/combat_zone.py), [engine/combat_trigger.py](../../engine/combat_trigger.py) |
| IA minions (scripted) | [engine/npc_ai/scripted.py](../../engine/npc_ai/scripted.py) |
| IA elites (behavior profiles) | [engine/npc_ai/elite.py](../../engine/npc_ai/elite.py) |
| IA boss (LLM tactician) | [engine/npc_ai/boss_brain.py](../../engine/npc_ai/boss_brain.py) |
| Legendary actions off-turn | [engine/npc_ai/legendary.py](../../engine/npc_ai/legendary.py) |
| Phase transitions HP | [engine/combat_phases.py](../../engine/combat_phases.py) |
| Validateurs combat stricts | [engine/validators.py](../../engine/validators.py) |

## Modèles data

Diagramme des classes clés (noms de champs réels, voir [GAME_ENGINE.md](GAME_ENGINE.md#combatpy) pour le détail des enums et helpers).

```
CombatState (engine/combat.py)
├── combat_id: str (UUID)
├── combatants: list[Combatant]
├── round_number: int
├── current_turn_index: int
├── is_active: bool
├── end_reason: CombatEndReason | None  (VICTORY|DEFEAT|FLED|TRUCE)
├── pending_phase_narrations: list[PhaseTransitionEvent]
├── pending_legendary_summaries: list[str]
├── recent_events: list[str]            (pour contexte narrateur)
└── _finalized: bool  (PrivateAttr, idempotence guard)

Combatant
├── name, side (PLAYER|ENEMY), initiative
├── character: Character
├── inventory: Inventory | None
├── spellcaster: SpellcasterState | None
├── stat_block: NPCStatBlock | None     (None pour PCs)
├── conditions: list[ActiveCondition]
├── action_budget: ActionBudget
├── current_zone: str | None
├── death_saves, is_alive, fled
├── legendary_points_remaining: int     (boss only, reset au début de son tour)
└── phase_save_bonus: int                (cumulatif, injecté par phase transitions)

ActionBudget
├── movement_remaining_feet: int
├── action_used / bonus_action_used / reaction_used_this_round: bool
└── disengaged_this_turn: bool

NPCStatBlock (engine/npc_stat_block.py)
├── tier: MINION | ELITE | BOSS
├── archetype: str
├── multiattack_count: int
├── attacks: list[NPCAttack]
├── signature_abilities: list[SignatureAbility]
├── legendary_actions: list[LegendaryAction]
├── legendary_points_per_round: int
├── phases: list[PhaseTransition]
├── behavior_profile: AGGRESSIVE | DEFENSIVE | SUPPORT | TACTICAL
├── aggression_threshold: int            (DC TRUCE 1-30)
└── mindless: bool                       (bloque auto le TRUCE)

Location (world/location.py)
├── ... narrative fields ...
├── combat_zones: list[Zone]             (graphe nommé, adjacence symétrique)
└── combat_triggers: dict[str, CombatTriggerDef]

Zone (world/combat_zone.py)
├── name, description
├── adjacent_zone_names: list[str]
└── tags: list[ZoneTag]                  (COVER, DIFFICULT_TERRAIN, ELEVATED, HAZARD, OBSCURED)
```

## Initiative & surprise — 3 cas

| Cas | Déclencheur | Qui agit en premier | Qui est SURPRISED |
|---|---|---|---|
| **1 — Agression joueur** | ATTACK ou IMPROVISE `is_lethal_intent=True` contre cible non-hostile | PC attaquant en tête, puis roll d20+DEX | Les enemies nommés dans `trigger.enemy_names` |
| **2 — Ambush** | `INTERACT` sur un `Location.combat_triggers[key]`, ou beat scripté ambush | NPCs ambushers en tête (tri interne initiative+DEX), puis roll | Tous les PCs |
| **3 — Face-à-face** | Beat combat scripté normal, ou cible déjà `HOSTILE` et reconnue | Roll standard `1d20 + DEX`, tiebreak DEX score | Personne |

La condition `SURPRISED` est appliquée par `start_combat` sur la base du `CombatTrigger.surprise_side`. `advance_turn` la consomme via `consume_surprise_if_present` **à la fin** du premier tour du combattant surpris — leur tour est donc un no-op (le validator rejette toute action), puis la condition est nettoyée.

**Exemples concrets** :
- Mageta vs Vellus dans "L'Écume du Vent" — beat 1 scripté face-à-face, Vellus est reconnu hostile → **cas 3**, initiative normale.
- Mageta poignarde un marchand passif (IMPROVISE avec `is_lethal_intent=True`) → **cas 1**, marchand surpris.
- Mageta tire un levier piégé qui libère trois gobelins → **cas 2**, party entière surprise.

## Action economy

Chaque combattant, à chaque tour :

| Slot | Quantité | Notes |
|---|---|---|
| **Move** | jusqu'à `speed` feet (30 par défaut) | Consomme `movement_remaining_feet`. 1 zone ≈ 15 ft ; `DIFFICULT_TERRAIN` double le coût |
| **Action** | 1 par tour | Attack / Cast / Dodge / Disengage / Flee / Use Item / Talk (TRUCE) |
| **Bonus Action** | 1 par tour | Conditionnée à la classe ou au sort |
| **Reaction** | 1 par **round** (off-turn) | Opportunity attacks + futurs Shield/Counterspell |

**Reset** : Move/Action/Bonus sont reset au début du tour par `ActionBudget.reset_for_new_turn(speed)`. La Reaction persiste entre tours et reset quand le round wrappe (via `advance_turn`).

**Recharge 5-6 (SRD)** : au début du tour d'un combattant à stat block, chaque signature `usage="recharge_5_6"` épuisée (`uses_remaining == 0`) fait l'objet d'un jet de `1d6` — sur 5+, elle récupère son usage ([engine/combat.py](../../engine/combat.py)). Une signature encore chargée n'est jamais re-rollée ni stackée. Le cue est queue sur `CombatState.pending_legendary_summaries` pour affichage par le TurnManager.

Les helpers de mutation (`consume_action`, `consume_bonus_action`, `consume_movement`, `consume_reaction`) dans [engine/combat.py](../../engine/combat.py) sont les **seuls** points de décrémentation autorisés — ils raise si le budget est déjà consommé, ce qui permet aux validators et aux brains NPC de détecter les erreurs tôt.

## NPC AI — 3 tiers

| Tier | Brain | Appel LLM | Signatures | Legendary |
|---|---|---|---|---|
| **Minion** | [scripted.py::decide_minion_action](../../engine/npc_ai/scripted.py) | Non | — | — |
| **Elite** | [elite.py::decide_elite_action](../../engine/npc_ai/elite.py) | Non | 1 tirée librairie | — |
| **Boss** | [boss_brain.py::decide_boss_action](../../engine/npc_ai/boss_brain.py) + [ai/npc_tactician.py](../../ai/npc_tactician.py) | **Oui** (JSON mode, Pydantic-validé) | 2-3 custom ou librairie | 3 points/round |

- **Minion** : heuristique 3 règles — (1) attaque la cible en range avec le moins de HP (tiebreak AC ascendant), (2) BFS vers la zone ennemie la plus proche, (3) sinon Dodge.
- **Elite** : dispatcher par `behavior_profile`. AGGRESSIVE privilégie un signature damage dispo ; DEFENSIVE Dodge si HP < 30% ; SUPPORT heal les alliés ; TACTICAL cible en priorité les ennemis avec condition exploitable.
- **Boss** : le tactician produit un `{action, target, reasoning}` structuré. L'engine valide et roule. **Fallback automatique sur elite scripted après 2 échecs** (parse error, target invalide, action illégale).

**Point d'entrée unique** : [scripted.py::decide_action_for(combatant, state, location)](../../engine/npc_ai/scripted.py) regarde `stat_block.tier` et route vers le bon brain. Le `TurnManager` l'appelle pour chaque tour NPC et consomme le `NPCActionPlan` via `execute_action_plan` — qui à son tour call `resolve_npc_attack`, `execute_signature_ability`, `move_combatant_to_zone`, etc. selon le plan.

**Legendary actions off-turn** : [legendary.py::maybe_spend_legendary_action](../../engine/npc_ai/legendary.py) est hookée par `advance_turn` après chaque fin de tour PC. Elle itère sur les bosses ennemis vivants et dépense 1-3 points selon `_pick_legendary` (cost-3 uniquement si HP < 30%, sinon cost-2 si dispo, sinon cost-1 eagerly). Les summaries sont queue sur `CombatState.pending_legendary_summaries` pour que le TurnManager poste les dice embeds.

**Phase transitions** : [combat_phases.py::check_phase_transition(combatant)](../../engine/combat_phases.py) est appelée par `apply_damage` après chaque dégât infligé au boss. Elle flippe `PhaseTransition.triggered`, applique les buffs (`attack_bonus` sur toutes les attaques, `save_bonus` cumulatif, unlock des signatures via bump `uses_remaining`), et queue un `PhaseTransitionEvent` sur `state.pending_phase_narrations` pour que le narrateur tisse la `narrative_cue` au prochain tour.

## Pipeline d'une attaque (step-by-step)

1. **Joueur** clique "Attack" dans la `CombatActionView` postée pour son tour.
2. **TargetSelectView** affiche les ennemis vivants ; Mageta choisit "Vellus le Mentisseur".
3. **Callback** construit `InterpretedAction(action_type=ATTACK, target_name="Vellus le Mentisseur")` et appelle `ActionPipeline.run`.
4. **`_validate`** dispatch combat → `validate_attack` → OK (action non consommée, cible vivante, range zone compatible).
5. **`_resolve_mechanics`** call `resolve_attack(mageta, vellus, longsword, advantage=False, disadvantage=False)` dans [engine/combat.py:605](../../engine/combat.py#L605) : roule `1d20+5` vs AC 16 → touche, roule `1d8+3` dégâts → applique via `apply_damage`.
6. **`apply_damage`** déclenche le hook `_on_damage_taken` (concentration save si `CONCENTRATING`), puis `check_phase_transition(vellus)` — si Vellus passe le seuil 50% HP, sa phase 2 flippe, ses attaques gagnent `+2` to-hit et sa signature "Chant du Silence Éternel" se débloque.
7. **`consume_action(mageta)`** → action budget épuisé.
8. **`advance_turn(state)`** : tick conditions, skip morts/fuis, next = Vellus (NPC), reset son `ActionBudget`, reset `legendary_points_remaining = 3`, `check_combat_end` → None.
9. **`TurnManager.on_action_resolved`** détecte que le tour actif est un NPC et appelle `decide_action_for(vellus, state, location)` → dispatch BOSS → `decide_boss_action` → LLM tactician → `NPCActionPlan(action_type=CAST_SIGNATURE, signature_name="Chant du Silence Éternel", ...)`.
10. **`execute_action_plan`** consume l'action, route vers `execute_signature_ability` qui applique les effets déterministes.
11. **Narrateur** reçoit le scene context enrichi (COMBAT ACTIVE + recent_events + PhaseTransitionEvent "Vellus entre en phase 2") et produit une prose tendue.
12. **Embeds postés** : dice embeds pour l'attaque de Mageta et pour les saves/damage du signature, state embed combat mis à jour, `CombatActionView` re-postée pour Mageta avec un `@ping`.

## Triggers de combat

Le combat est **orthogonal** à l'exploration — il peut démarrer via quatre déclencheurs, détectés par [bot/combat_entry.py::detect_combat_trigger](../../bot/combat_entry.py#L69) sur toute action hors combat.

| Déclencheur | `CombatTriggerKind` | Surprise | Chemin de détection |
|---|---|---|---|
| **Attaque explicite** sur NPC combat-worthy (stat_block ou HP/AC seuil) | `PLAYER_ATTACK` | `PLAYERS` si cible passive, `BOTH_READY` si déjà hostile | path `ATTACK` dans `detect_combat_trigger` |
| **Intention létale** (IMPROVISE flaggé `is_lethal_intent=True` par l'interpreter) | `LETHAL_INTENT` | `PLAYERS` | path `IMPROVISE` |
| **Piège / ambush** (INTERACT sur un `Location.combat_triggers[key]`) | `AMBUSH` | `NPCS` | path `INTERACT` |
| **Beat scripté** combat (le générateur d'arc pose un trigger sur le beat) | `SCRIPTED_BEAT` | décidé par le générateur | depuis `campaign_launcher` |

Le cinquième vecteur — **provocation sociale** (TALK qui dépasse `aggression_threshold`) — est réservé comme `PROVOCATION` mais pour l'instant la résolution sociale passe par le chemin TRUCE inverse (voir "Cycle de vie").

`enter_combat(session, trigger)` assemble un `CombatState` party-wide (tous les PCs + les ennemis résolus via `session.npcs`), `start_combat(combatants, trigger)` roule l'initiative et applique `SURPRISED`. `CombatTrigger` / `CombatTriggerKind` / `InitiativeSide` vivent dans [engine/combat_trigger.py](../../engine/combat_trigger.py) pour que `engine/` puisse les importer sans violer la règle « engine ne dépend jamais de bot/ai ».

## Cycle de vie du combat

```
[NO COMBAT]
    │ (action joueur hors combat)
    ▼
detect_combat_trigger ──► None ──► exploration normale
    │ trigger
    ▼
enter_combat(session, trigger) ──► CombatState (party + enemies)
    │
    ▼
start_combat(combatants, trigger) ──► initiative + SURPRISED
    │
    ▼
[ROUND 1] ─┐
    │      │ PC action → ActionPipeline → advance_turn
    │      │ NPC tour → decide_action_for → execute_action_plan → advance_turn
    │      │ Legendary off-turn → maybe_spend_legendary_action → queue
    │      │ Phase transitions → check_phase_transition → queue
    │      │ check_combat_end (chaque advance_turn)
    │      ▼
[ROUND N]  │
    │      │
    └──────┘
    │ end_reason posé
    ▼
bot.combat_end.finalize_combat(session, reason) ──► CombatEndSummary
    │ (idempotent, guard _finalized)
    │  - XP (50 MINION / 150 ELITE / 500 BOSS) réparti PC survivants
    │  - Loot MVP (attacks[0].name de chaque enemy tombé)
    │  - Purge SURPRISED + CONCENTRATING
    │  - is_active=False, combat_state reste set pour inspection
    ▼
[CLEANUP] ──► embed end posté, state persisté, session back to exploration
```

**5 conditions de fin** (détaillées dans [ACTION_PIPELINE.md](ACTION_PIPELINE.md)) :
- **VICTORY** — `check_combat_end` → aucun ENEMY debout (non mort, non fui)
- **DEFEAT** — aucun PC debout (mutual wipe = DEFEAT)
- **FLED** — tous les PCs alive ont `fled=True`
- **TRUCE** — succès du check CHA mid-combat via `bot.combat_truce.attempt_truce`
- **~~TIMEOUT~~** — explicitement hors scope ; le combat reste reprenable tant que la session vit

Le watcher 5 min auto-DEFEND du turn manager est un **filet AFK court**, pas une fin. Il force l'action DEFEND et avance le tour, il ne finalize pas.

## Points d'extension

### Ajouter une nouvelle condition 5e

1. Ajouter la valeur dans `ConditionType` enum ([engine/conditions.py](../../engine/conditions.py#L31)).
2. Si elle bloque les attaques, les saves, ou le mouvement : ajouter au frozenset de classification approprié (`has_disadvantage_on_attacks`, `cannot_move`, `auto_fails_str_dex_saves`, `is_incapacitated`).
3. Si elle a un effet actif, ajouter le helper et le hook dans `advance_turn` / `_validate_common` / `_on_damage_taken` selon besoin.
4. Tests dans [tests/test_conditions.py](../../tests/test_conditions.py).

### Ajouter un nouvel archétype NPC

1. Ajouter une factory `_build_my_archetype()` dans [engine/npc_library.py](../../engine/npc_library.py).
2. Enregistrer dans `ARCHETYPE_BUILDERS` (à la fin du fichier) — la clé est le nom utilisé par le hydration layer et le world generator.
3. Tests dans [tests/test_npc_library.py](../../tests/test_npc_library.py).
4. Si le world generator doit pouvoir l'invoquer, étendre [ai/prompts/system_world_generator.txt](../../ai/prompts/system_world_generator.txt) avec le nouveau role.

**Rappel** : `get_archetype(name)` doit **toujours** retourner une instance fraîche (construction complète, pas un `deepcopy` d'un singleton) — sinon les `uses_remaining` décrémentés et les `phases[].triggered=True` fuitent entre combats.

### Ajouter une nouvelle signature ability ou legendary action

1. La définition vit dans le stat block (soit en dur dans `npc_library.py`, soit construit dynamiquement par l'arc generator).
2. Les effets sont résolus par `execute_signature_ability(caster, signature, targets, state)` — 3 `effect.kind` supportés MVP : `damage`, `heal`, `condition`. Les autres (`aoe_damage`, `buff`, `debuff`, `move`) logguent un WARNING et retombent sur une attaque standard.
3. Pour une **legendary action**, ajouter une entrée dans `stat_block.legendary_actions` ; la heuristique `_pick_legendary` dans [legendary.py](../../engine/npc_ai/legendary.py) peut être étendue pour prioriser.

## Anti-patterns

- **LLM qui roule des dés** — « the dragon rolls 18 on its attack » dans un prompt, ou un LLM qui produit un entier en output et le code qui le trust. Jamais. L'engine roule, le LLM lit.
- **Validation côté résolution** — si un check de légalité est fait dans `_resolve_mechanics` au lieu de `_validate`, c'est un bug. La séparation est stricte : Phase 3 bloque, Phase 4 suppose que tout est déjà légal.
- **Couplage `engine/` → `bot/` ou `ai/`** — le dossier `engine/` est pur Python déterministe. Si un helper engine a besoin d'un concept bot (session, canal Discord, db_factory…), passer par une injection de dépendance ou déplacer le helper dans `bot/`. C'est pour ça que `finalize_combat` vit dans [bot/combat_end.py](../../bot/combat_end.py) et pas dans `engine/`.
- **Shared state sur les archétypes** — `get_archetype` doit retourner une copie fraîche. Les mutations sur `uses_remaining`, `phases[].triggered`, `legendary_points_remaining` sont par-combattant et ne doivent jamais remonter au template.
- **LLM tactician sans validation Pydantic stricte** — toute sortie JSON du boss brain passe par un `NPCActionPlan.model_validate(...)`. Si le LLM produit une action illégale, on fallback sur elite scripted — on ne trust jamais le JSON brut.

## Référence API

### [engine/combat.py](../../engine/combat.py)

```python
start_combat(combatants, trigger: CombatTrigger | None = None) -> CombatState
advance_turn(state: CombatState) -> CombatState
check_combat_end(state: CombatState) -> CombatEndReason | None
is_combat_over(state: CombatState) -> bool

resolve_attack(attacker, defender, weapon, advantage=False, disadvantage=False) -> AttackResult
resolve_npc_attack(attacker, defender, npc_attack: NPCAttack) -> AttackResult
resolve_spell(caster, spell, target, slot_level) -> SpellCastResult
apply_damage(combatant, damage, state: CombatState | None = None) -> None
apply_healing(combatant, amount) -> None

move_combatant_to_zone(state, combatant, target_zone, location) -> list[AttackResult]
disengage(combatant) -> None

consume_action(c) / consume_bonus_action(c) / consume_movement(c, feet) / consume_reaction(c)
```

### [bot/combat_entry.py](../../bot/combat_entry.py)

```python
detect_combat_trigger(action: InterpretedAction, session: GameSession) -> CombatTrigger | None
enter_combat(session: GameSession, trigger: CombatTrigger) -> CombatState
```

### [bot/combat_turn_manager.py](../../bot/combat_turn_manager.py)

```python
class TurnManager:
    def __init__(self, session, channel, db_factory, ...): ...
    async def on_action_resolved(self, pipeline_result) -> None
    async def _persist_state(self) -> None
    async def _finalize(self, reason: CombatEndReason) -> None
```

### [bot/combat_end.py](../../bot/combat_end.py) / [bot/combat_truce.py](../../bot/combat_truce.py)

```python
finalize_combat(session: GameSession, reason: CombatEndReason) -> CombatEndSummary
attempt_truce(actor, target, state) -> tuple[bool, D20CheckResult | None, str]
```

### [engine/npc_ai/](../../engine/npc_ai/)

```python
# scripted.py — point d'entrée commun
decide_action_for(combatant, state, location) -> NPCActionPlan

# tiers
decide_minion_action(combatant, state, location) -> NPCActionPlan
decide_elite_action(combatant, state, location) -> NPCActionPlan
decide_boss_action(combatant, state, location, tactician) -> NPCActionPlan

# resolver
execute_action_plan(combatant, plan, state, location) -> str
execute_signature_ability(caster, signature, targets, state) -> str

# off-turn
maybe_spend_legendary_action(state, boss, previous_combatant) -> list[str]
```

### [engine/combat_phases.py](../../engine/combat_phases.py)

```python
check_phase_transition(combatant: Combatant) -> list[PhaseTransition]
```

### [engine/npc_library.py](../../engine/npc_library.py)

```python
get_archetype(name: str) -> NPCStatBlock        # raise KeyError si inconnu
list_archetypes() -> list[str]
ARCHETYPE_BUILDERS: dict[str, Callable[[], NPCStatBlock]]
```

## Checklist de contribution

Avant de merger un PR qui touche au combat :

- [ ] `uv run pytest` tous verts (inclure les scenarios e2e combat dans [tests/scenarios/](../../tests/scenarios/))
- [ ] `uv run ruff check .` clean
- [ ] `uv run mypy .` clean
- [ ] Nouveau code ajoute des tests unitaires couvrant les edge cases
- [ ] Si un appel LLM est ajouté, sa sortie est strictement validée par Pydantic avec fallback déterministe en cas d'échec
- [ ] Si un nouveau `ActionType` est ajouté, tous les validators sont étendus (combat et exploration)
- [ ] Si une nouvelle condition est ajoutée, `advance_turn` et `_validate_common` sont considérés
- [ ] Si une fonction publique change de signature, cette doc est mise à jour dans la section "Référence API"
- [ ] La règle d'or est respectée — aucun dé roulé par un LLM, aucune validation dans `_resolve_mechanics`
- [ ] Non-regression : les scénarios combat e2e existants passent toujours
