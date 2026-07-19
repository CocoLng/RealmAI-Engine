# Pipeline d'action — Comment le joueur interagit avec le monde

Toute action d'un joueur (en combat ou en exploration) passe par **la même pipeline à 6 phases** orchestrée par `PipelineRunner` dans [bot/pipeline/orchestrator.py](../../bot/pipeline/orchestrator.py) — [bot/action_pipeline.py](../../bot/action_pipeline.py) n'est plus qu'une façade de compatibilité qui y délègue (les phases vivent dans `bot/pipeline/interpret.py`, `resolve.py` et `narrate.py`). Elle est déclenchée par `ActionHandlerCog` ([bot/cogs/action_handler.py](../../bot/cogs/action_handler.py)) lorsqu'un joueur `@mentionne` le bot dans un salon de campagne.

## Contrat d'entrée

```python
ActionPipeline(actor_name=..., interpreter=..., narrator=..., session=..., ...)  # actor_name au constructeur

pipeline.process(
    player_text: str,            # phrase libre "j'attaque le gobelin"
    progress_callback=None,      # async callback(phase, status) pour l'embed progress
) -> ActionPipelineResult | AmbiguityResult | UnknownEntityResult
```

Un filtre amont dans `ActionHandlerCog` élimine :
- messages < 4 caractères,
- interjections connues (`ok`, `lol`, `mdr`, …),
- utilisateurs non-joueurs,
- messages sans `@Realm`.

Le lock `session.action_lock` (asyncio) sérialise : **une seule pipeline active à la fois par campagne**.

Un **embed de progression** est posté dès le début (phases `⚪ pending → 🔄 in progress → ✅ done / ❌ failed`, elapsed time en footer) — voir `bot/embeds/action_progress_embed.py`.

## Phase 1 — INTERPRETING

**Acteur** : `ai.interpreter.Interpreter` — `qwen3.5:4b`, temperature 0.3, JSON mode.

**Prompt** : `ai/prompts/system_interpreter.txt` + `build_scene_context(...)` injecté dans le user message :
- location + description
- PNJs visibles
- sorties
- objets visibles
- combat actif (round, tour courant, enemies)

**Sortie** : `InterpretedAction` (pydantic) avec `action_type` parmi 15 enum values (`ATTACK`, `CAST_SPELL`, `DEFEND`, `DISENGAGE`, `FLEE`, `USE_ITEM`, `EQUIP`, `LOOK`, `SEARCH`, `TALK`, `MOVE`, `INTERACT`, `PICKUP`, `IMPROVISE`, `QUESTION`), `target_name`, `item_name`, `spell_name`, `talk_topic`, `search_detail`, `confidence`.

**Resilience** :
- Wrappé dans [bot/llm_retry.py](../../bot/llm_retry.py) `retry_llm_call(max_retries=2, delays=(5, 15))`.
- Fallback déterministe en cas de parse failure :
  - En combat → `DEFEND`
  - Hors combat → `IMPROVISE` avec echo du texte brut
- `LLMParseError` est loggé + dumpé dans `logs/narrator_failures/` pour diagnostic offline.

## Phase 2 — RESOLVING_ENTITIES

**Acteur** : `ai.entity_resolver.EntityResolver` — **100% Python**, avec fallback LLM optionnel.

### Stratégie (stop-early, par action_type)

1. **Exact normalisé** : lowercase, diacritiques strippés, ponctuation retirée.
2. **Lemmes FR** : groupes morphologiques hardcodés (`eur`↔`euse`/`eurs`/`euses`, `ois`↔`oise`, etc.), stopwords (`le/la/les/un/une/…`) supprimés.
3. **Fuzzy** : `difflib.SequenceMatcher.ratio() ≥ 0.75` vs nom + aliases.
4. **Fallback LLM (Lot B)** : si les 3 étapes précédentes retournent 0 candidat **et** l'Interpreter est dispo, appel `Interpreter.disambiguate_entity()` (`temperature 0.1, num_predict 64`). Toute exception swallowée (log warning). Ne retourne jamais d'erreur dure.

### Cible par action_type

| action_type | Source de résolution |
|---|---|
| `TALK` | PNJs de la location |
| `MOVE` | `location.connections` |
| `SEARCH`, `INTERACT` | `location.items_available` (SEARCH permissif, INTERACT strict) |
| `PICKUP` | location items |
| `ATTACK`, `CAST_SPELL` | Combat enemies d'abord, fallback PNJs de la location (Lot C — bootstrap) |
| `USE_ITEM` | `inventory.items` |
| `LOOK`, `DEFEND`, `FLEE`, `IMPROVISE`, `QUESTION` | `not_applicable` |

### Résultats possibles

- `resolved` → on continue Phase 3 avec l'entité canonique.
- `ambiguous` (2-4 candidates) → `ActionPipeline` renvoie un `AmbiguityResult` ; `ActionHandlerCog` poste une `ClarificationView` (boutons, seul l'acteur original peut cliquer, timeout 2 min) et **suspend** la pipeline. Au clic : la phase recommence avec l'entité choisie.
- `unknown` → `UnknownEntityResult` ; le bot poste un refus in-character narré.

## Phase 3 — VALIDATING

**Acteur** : `engine.validators.validate_action()` (combat) ou `validate_exploration_action()` (hors combat).

Checks :
- Action légale pour le contexte (`ATTACK` requiert `combat_state.is_active` ou bootstrap).
- Joueur vivant, pas `INCAPACITATED`.
- C'est bien son tour (en combat).
- Pour `CAST_SPELL` : sort connu, slot dispo, `can_cast_spell()`.
- Pour `USE_ITEM` : item présent en inventaire.
- Pour `FLEE` : `can_move()` (pas `RESTRAINED` etc).

### Règles strictes D&D 5e

`validate_action()` applique un garde `SURPRISED` en amont : un combatant surpris ne peut rien faire ce tour (belt-and-suspenders — le turn manager skipe déjà les surpris, mais le validator le renforce). Les types d'action inconnus sont rejetés avec un message clair au lieu de lever une `KeyError`. `validate_attack()` vérifie dans l'ordre : budget action (action déjà utilisée = refus), existence et vie de la cible, absence de tir allié (`CombatSide` identique = refus), arme équipée, puis contrôle de portée par zone (`current_zone`) — une arme de mêlée ne peut pas toucher un combatant dans une zone différente, mais une arme de jet ou à distance (`SIMPLE_RANGED`/`MARTIAL_RANGED`, propriété `THROWN`) passe toutes les zones. `validate_cast_spell()` vérifie maintenant le budget action economy selon le `casting_time` du sort (`ACTION`, `BONUS_ACTION`, `REACTION`). `validate_move_in_combat()` (nouvelle fonction) vérifie les conditions bloquantes (`cannot_move`) et le mouvement restant (`movement_remaining_feet > 0`) avant d'autoriser un déplacement de zone.

### Dispatch pipeline

`_validate()` applique la logique suivante **dans l'ordre** :

1. **MOVE → FLEE en combat actif** : si un combat est actif et l'action est `MOVE`, l'action est silencieusement convertie en `FLEE`. La destination (`target_name`) est sauvegardée dans `_pending_flee_destination` pour être consommée par le résolveur de fuite. Le dispatcher continue vers les validateurs combat.
2. **Détection de trigger & bootstrap** : si aucun combat actif, `detect_combat_trigger(action, session)` est appelé. S'il retourne un `CombatTrigger`, `enter_combat(session, trigger)` assemble le `CombatState` party-wide, puis `start_combat(combatants, trigger)` roule l'initiative et applique les conditions `SURPRISED` 5e. Le `CombatState` résultant est posé sur `self.combat_state` et sur `session.combat_state`. Un tuple `(combat_state, trigger)` est aussi stocké dans `_pending_combat_start_embed` pour que l'appelant (`ActionHandlerCog`) poste l'embed de début de combat avant la narration.
3. **Dispatch combat** : si un combat est actif, les actions `EXPLORATION_ACTION_TYPES` passent par `validate_exploration_action(eng_action, combat_state=self.combat_state)` (refusées sauf `LOOK`/`QUESTION`/`IMPROVISE`). **Cas spécial TRUCE** : `TALK` en combat est délégué à `validate_truce_attempt` (allié, mindless, sans stat_block, cible absente → rejetés) — c'est la porte d'entrée de la résolution sociale. Les autres actions passent par `validate_action(eng_action, self.combat_state)`.
4. **Chemin exploration / trivial kill** : si aucun combat actif, les actions exploration passent par `validate_exploration_action(eng_action, combat_state=None)`. Une `ATTACK` hors combat sur un NPC est testée contre `_should_trivial_resolve(npc)` — si trivial, `_trivial_kill()` est appelé et `ValidationResult(is_valid=True)` est retourné directement. Sinon, une erreur `"'attack' nécessite un combat actif."` est retournée.

### Champs de pipeline ajoutés

| Champ | Type | Usage |
|---|---|---|
| `_pending_flee_destination` | `str \| None` | Zone cible d'un MOVE auto-converti en FLEE ; consommé par `_resolve_flee` |
| `_pending_combat_start_embed` | `tuple[CombatState, CombatTrigger] \| None` | Lu par `ActionHandlerCog` pour poster l'embed de début de combat |
| `_pending_dice_embeds` | `list[Any]` | Résultats de jets de dés à afficher ; produit par `_resolve_flee` et les résolveurs futurs |

### Cas spéciaux (Lots)

- **Lot C — Combat bootstrap** : via `detect_combat_trigger` + `enter_combat` + `start_combat`. Les NPCs combat-worthies (`stat_block` non nul, `disposition == HOSTILE`, ou `max_hp >= 10` / `ac > 12`) déclenchent un combat party-wide avec initiative et surprise 5e. Les NPCs faibles/pacifiques tombent dans le Lot E.
- **Lot E — Trivial resolve** : si PNJ pacifique (`disposition ≥ NEUTRAL`) et fragile (`max_hp < TRIVIAL_RESOLVE_HP_THRESHOLD`, =10), appelle `engine.combat.trivial_resolve()` qui résout l'attaque en one-shot sans démarrer un `CombatState` complet. Flip l'état `is_alive=False` du PNJ.
- **Hostile witnessing** : si un PNJ ami voit un kill gratuit, il passe `HOSTILE` (logique de propagation simpliste, voir `bot/pipeline/resolve.py`).

### Détection de trigger combat (`bot/combat_entry.py`)

`detect_combat_trigger(action, session)` est appelé par `_validate` pour toute action hors combat. Il retourne un `CombatTrigger | None` selon :

| Déclencheur | `CombatTriggerKind` | Surprise | Source |
|---|---|---|---|
| `ATTACK` sur NPC combat-worthy (`stat_block` ou HP/AC seuil) | `PLAYER_ATTACK` | `PLAYERS` si cible NEUTRAL/FRIENDLY, `BOTH_READY` si HOSTILE/UNFRIENDLY | `detect_combat_trigger` — path ATTACK |
| `IMPROVISE` flaggé `is_lethal_intent=True` | `LETHAL_INTENT` | `PLAYERS` | `detect_combat_trigger` — path IMPROVISE |
| `INTERACT` sur `Location.combat_triggers[target]` | `AMBUSH` | `NPCS` (party surprise) | `detect_combat_trigger` — path INTERACT (dormant : `hasattr` guard) |
| `TALK` dépassant l'`aggression_threshold` d'un PNJ | `PROVOCATION` | décidé par le resolver TRUCE | Reserved |
| Beat scripté combat au lancement/advancement | `SCRIPTED_BEAT` | décidé par le générateur d'arc | Called from `bot/campaign_launcher.py` |

`enter_combat(session, trigger)` assemble un `CombatState` party-wide (tous les PCs + les enemies nommés dans `trigger.enemy_names`, résolus via `session.npcs`) et le stocke sur `session.combat_state`. Raise `ValueError` si aucun enemy trouvable. L'initiative et la condition `SURPRISED` sont appliquées par `engine.combat.start_combat(combatants, trigger)` — pas par `enter_combat`. `CombatTrigger` / `CombatTriggerKind` / `InitiativeSide` vivent dans `engine/combat_trigger.py` pour que `engine/` puisse les importer sans violer la règle « engine ne dépend jamais de bot/ai ».

Sortie : `ValidationResult(is_valid, error_message)`. Si invalide → narré comme échec in-character.

## Phase 4 — RESOLVING_ACTION

**Acteur** : moteur (`engine/`) + helpers bot (`scene_hydration`, `world_navigation`).

Dispatch par `action_type` :

| action_type | Résolution |
|---|---|
| `LOOK` | Description textuelle (depuis `describe_scene_for_narrator()`). Pas de mutation. |
| `SEARCH` | Révèle un détail. Pas de mutation (les items sont déjà dans `location.items_available`). |
| `TALK` | **Hors combat** : `_resolve_talk()` : appelle `NPCAgent.respond(npc, player_input, context)` → `NPCResponse(dialogue, disposition_change, revealed_info)`. L'action pipeline applique `npc.disposition += change` et persiste. **En combat** : `_resolve_talk_in_combat()` → `bot.combat_truce.attempt_truce` (check CHA + 2 prof vs `aggression_threshold`, SUCCESS/CRIT uniquement), sur succès appelle `bot.combat_end.finalize_combat(session, CombatEndReason.TRUCE)` et queue le dice embed. Auto-refus mindless et boss en phase 2 (triggered ≤50% HP). |
| `MOVE` | `change_location(session, new_location_name)` — si nouvelle location inconnue, `WorldGenerator.generate()` en live (c'est le cas frequent en exploration). Update `session.current_location` + `campaign.current_location` + persistance. `hydrate_scene()` pour les nouveaux PNJs. |
| `PICKUP` | `take_scene_item(location, item_name, inventory)` — retire de `items_available`, ajoute à `inventory.items`. Update DB. |
| `USE_ITEM` | Consommation (potion → healing, scroll → spell). |
| `ATTACK` | `engine.combat.resolve_attack(attacker, defender, weapon, advantage, disadvantage)` → `AttackResult` + mutation HP. |
| `CAST_SPELL` | `engine.combat.resolve_spell(caster, spell, target, slot_level)` → `SpellCastResult` + consommation slot. |
| `DEFEND` | Applique un effet temporaire (advantage défensif). |
| `FLEE` | `_resolve_flee()` : check DEX DC 12, `fled=True` sur succès. Si `check_combat_end` détecte FLED, appelle `bot.combat_end.finalize_combat(session, CombatEndReason.FLED)` (import local, pas de cycle). |
| `IMPROVISE` | Construit un `MechanicsOutcome` générique avec l'intent joueur, laissé à narrer tel quel. |
| `QUESTION` | Court-circuit : construit un résumé factuel de l'état du jeu (location, items, PNJ, sorties, state_flags, objectif du beat courant) dans `outcome_facts`. Pas de mutation. |

**Sortie** : `MechanicsOutcome(summary, player_intent, outcome_facts, public_effects: PublicEffects)`.

`PublicEffects` inclut uniquement les changements **visibles au joueur** : `hp_delta`, `items_gained/lost`, `gold_delta`, `location_change`, `xp_gained`, `level_up`. Pas de disposition interne, pas de rolls cachés. Render en footer via `to_footer_text()`.

### Fin de combat — 5 conditions centralisées

Toutes les conditions de fin passent maintenant par un point d'entrée unique : **`bot.combat_end.finalize_combat(session, reason) -> CombatEndSummary`**. Ce helper construit le summary (survivors/killed/fled, loot MVP, XP par tier, level-up flags), applique l'XP aux survivants PC, purge `SURPRISED`/`CONCENTRATING`, et pose le flag `_finalized` (PrivateAttr sur `CombatState`) pour être **idempotent** — les appelants pipeline et TurnManager peuvent l'appeler tous les deux sans doublonner les effets.

| Condition | Détection | Appelant `finalize_combat` | Reason |
|---|---|---|---|
| **VICTORY** | `check_combat_end` après mutation HP enemy → tous morts/fuis | `TurnManager._finalize` après `advance_turn` | `VICTORY` |
| **DEFEAT** | `check_combat_end` → tous PCs morts | `TurnManager._finalize` après `advance_turn` | `DEFEAT` |
| **FLED** | `check_combat_end` → tous PCs alive ont `fled=True` | `ActionPipeline._resolve_flee` (sur détection) + ré-appel idempotent par `TurnManager._finalize` | `FLED` |
| **TRUCE** | `bot.combat_truce.attempt_truce` succès (CHA SUCCESS+) | `ActionPipeline._resolve_talk_in_combat` | `TRUCE` |
| ~~TIMEOUT~~ | **Pas implémenté — hors scope**. Discord tolère les déconnexions longues, le combat reste reprenable tant qu'on ne `/end_campaign` pas. Le watcher 5 min auto-DEFEND de la TurnManager est un filet AFK court, pas une fin. La persistance post-tour garantit la reprise. |

**Persistance combat** : `TurnManager` reçoit le `db_factory` (via `CombatCog.build_turn_manager`) et appelle `_persist_state` (async, thread-offloaded `persist_session`) après `advance_turn` (couvre les tours NPC qui ne passent pas par le pipeline) et après `_finalize` (capture l'état terminal). `ActionPipeline` garde son auto-checkpoint pour les actions PC. Résultat : après chaque tour et à la fin du combat, `campaigns.combat_state_json` reflète l'état courant.

### Phase 4b — BEAT COMPLETION CHECK

Après Phase 4, le pipeline évalue la progression du beat courant via **`BeatProgressionEngine.evaluate()`** ([engine/beat_progression.py](../../engine/beat_progression.py), appelé dans `bot/pipeline/orchestrator.py`) — point de décision unique sur les **`BeatObjective` structurés** du beat (`objectives` + `advance_rule` ALL_REQUIRED / M_OF_N). L'ancien `completion_trigger` unique est du legacy : il est auto-migré en `BeatObjective` au chargement de l'arc (`world/story_arc.py::_migrate_legacy_completion_triggers`).

L'engine confronte l'action interprétée, l'outcome, la location, les flags monde et l'inventaire aux objectifs, et rend une décision : `ADVANCE`, `STAY`, ou `NEEDS_JUDGE`. Dans ce dernier cas, **`BeatJudge`** ([ai/beat_judge.py](../../ai/beat_judge.py)) tranche — avance seulement si `passed == True` et `confidence ≥ 0.7`. Le code reste l'arbitre final. Les objectifs complétés sans avancement sont accumulés sur `beat.objectives_completed` — les beats multi-actions progressent tour après tour au lieu de recomputer de zéro.

Si la décision est d'avancer → `_apply_beat_effects(beat.on_complete)` mute la `Location` :
- `unlock_exits` → ajoutés à `location.unlocked_exits`
- `add_npcs` / `remove_items` / `add_items` → mutations directes
- `state_flags` → merges dans `location.state_flags`
- `narrative_hint` → ajouté à `outcome_facts` pour le Narrator

Puis `advance_beat(arc)` incrémente `current_beat_index`.

## Phase 5 — ASSEMBLING_CONTEXT

**Acteur** : `bot.pipeline.narrate.assemble_context()` (la façade `ActionPipeline._assemble_context()` y délègue). Le contexte du tour = préfixe mémoire caché sur `session.memory_context` (résumés + fenêtre glissante + RAG, pré-calculé en fin de tour précédent par `ContextAssembler.assemble_memory_prefix`) + locked facts + scene snapshot (`describe_scene_for_narrator`, qui joue le rôle de la Layer 1).

Construit une chaîne markdown avec :
- Layer 1 : état structuré (HP/AC/inventory highlights/location/NPCs/quest active) — max 450 tokens.
- Layer 2 : 12 derniers exchanges narratifs — 700 tokens.
- Layer 3 : derniers résumés compressés — 400 tokens.
- Layer 4 : résultats RAG ChromaDB pertinents (lore, fiches NPC, past events) — 350 tokens.

Budget total : **2500 tokens**. Truncation par priorité croissante : on tronque Layer 4 en premier, Layer 1 jamais. Voir [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md).

Side-effect : en fin de tour, `update_memory_after_turn` planifie `Summarizer.summarize()` en tâche de fond si le backlog d'exchanges non-résumés a dépassé le seuil (voir [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md)).

## Phase 6 — NARRATING

**Acteur** : `ai.narrator.Narrator` — `qwen3.5:9b`, temperature 0.8, JSON mode.

**Prompt** : `ai/prompts/system_narrator.txt` + section de contexte assemblée + section mechanics + `player_intent` (comment le joueur a phrasé son action, traité comme **style**, pas comme vérité) + `outcome_facts` (vérité canonique à restituer).

**Règles canoniques critiques** (enforced par prompt, pas par code) :

1. La description de la location et des items est **canon** — le Narrator ne peut pas la contredire.
2. Si `outcome_facts` contient du dialogue PNJ entre guillemets (`<NPC> dit : "…"`), ce dialogue doit apparaître **verbatim** dans la narration (enforced par prompt, issu du Lot F).
3. Les tiers d'outcome (`critical_failure → critical_success`) guident le ton.
4. Le `player_intent` est le HOW, pas le WHAT.

**Sortie** : `NarrativeResult(narrative, tone)`. `tone` ∈ {`dramatic`, `tense`, `humorous`, `somber`} → couleur de l'embed (or, rouge, vert, violet).

## Post-pipeline

1. **Embed narratif** posté via `bot/embeds/narrative_embed.py` (description + footer `PublicEffects`).
2. **Story bible** : `session.story_bible.log_turn(turn_number, actor, command, outcome_summary, narrative_excerpt)` — Markdown append-only.
3. **Exchange persisté** en Layer 2 : `PLAYER` entry + `NARRATOR` entry.
4. **Beat advancement** : décision unique par `BeatProgressionEngine.evaluate()` sur les `BeatObjective` structurés (Phase 4b), avec arbitrage `BeatJudge` (confidence ≥ 0.7) quand l'engine hésite. `advance_beat_if_ready()` (Lot D) n'existe plus. Sur avancement : `on_complete` appliqué, persist arc + poste d'un `beat_embed`.
5. **Story Director** : planifié par l'orchestrateur via `should_run_director` (`bot/pipeline/orchestrator.py`) — cadence primaire toutes les 6 interactions, plus fin de combat, drift narratif détecté, ou force ; un chemin legacy à 20 tours subsiste dans `bot/story_bible_logger.py`. `StoryDirector.check_coherence(campaign_id, context, beat_progress)` → `DirectorNote(coherence_issues, suggested_hooks, priority)`, persisté en `SemanticMemory` comme `SemanticDocument`. Voir [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md).

## Cas d'erreur

- `OllamaUnavailableError` → retry 2 fois (5s, 15s), sinon post un message "MJ indisponible, réessayez".
- `LLMParseError` → dump du request/response dans `logs/narrator_failures/<timestamp>.json` + narration fallback.
- Action invalide → narré comme un refus in-character (pas une erreur Python brute).
- Ambiguïté non résolue (timeout view) → pipeline annulée, lock libéré.

## Retour de la pipeline au cog

`ActionHandlerCog` reçoit un des 3 types :

| Type | Traitement |
|---|---|
| `ActionPipelineResult` | Si `is_question=True` → post **embed d'état** (bleu, 0x4A90D9) avec sections items/PNJ/sorties/beat. Sinon → post embed narratif + footer effects + mise à jour story bible. |
| `AmbiguityResult` | Post `ClarificationView`, attend clic, relance Phase 2. |
| `UnknownEntityResult` | Post message de refus in-character (pas d'erreur). |

Puis libération du `action_lock`.
