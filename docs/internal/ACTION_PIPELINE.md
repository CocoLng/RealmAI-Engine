# Pipeline d'action — Comment le joueur interagit avec le monde

Toute action d'un joueur (en combat ou en exploration) passe par **la même pipeline à 6 phases** orchestrée dans [bot/action_pipeline.py](../../bot/action_pipeline.py). Elle est déclenchée par `ActionHandlerCog` ([bot/cogs/action_handler.py](../../bot/cogs/action_handler.py)) lorsqu'un joueur `@mentionne` le bot dans un salon de campagne.

## Contrat d'entrée

```python
ActionPipeline.run(
    player_text: str,            # phrase libre "j'attaque le gobelin"
    actor_name: str,             # nom in-game du perso joueur
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

**Sortie** : `InterpretedAction` (pydantic) avec `action_type` parmi 14 enum values (`ATTACK`, `CAST_SPELL`, `DEFEND`, `FLEE`, `USE_ITEM`, `PICK_UP`, `LOOK`, `SEARCH`, `TALK`, `MOVE`, `INTERACT`, `IMPROVISE`, …), `target_name`, `item_name`, `spell_name`, `talk_topic`, `search_detail`, `confidence`.

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
| `LOOK`, `DEFEND`, `FLEE`, `IMPROVISE` | `not_applicable` |

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

### Cas spéciaux (Lots)

- **Lot C — Combat bootstrap** : si `ATTACK` hors combat sur un PNJ existant, crée un `CombatState` à la volée (joueur surprise ⇒ joueur initie). Implémenté dans l'action_pipeline directement (pas dans validators).
- **Lot E — Trivial resolve** : si PNJ pacifique (`disposition ≥ NEUTRAL`) et fragile (heuristique `max_hp < 10`), appelle `engine.combat.trivial_resolve()` qui résout l'attaque en one-shot sans démarrer un `CombatState` complet. Flip l'état `is_alive=False` du PNJ.
- **Hostile witnessing** : si un PNJ ami voit un kill gratuit, il passe `HOSTILE` (logique de propagation simpliste, voir `bot/action_pipeline.py`).

Sortie : `ValidationResult(is_valid, error_message)`. Si invalide → narré comme échec in-character.

## Phase 4 — RESOLVING_ACTION

**Acteur** : moteur (`engine/`) + helpers bot (`scene_hydration`, `world_navigation`).

Dispatch par `action_type` :

| action_type | Résolution |
|---|---|
| `LOOK` | Description textuelle (depuis `describe_scene_for_narrator()`). Pas de mutation. |
| `SEARCH` | Révèle un détail. Pas de mutation (les items sont déjà dans `location.items_available`). |
| `TALK` | `_resolve_talk()` : appelle `NPCAgent.respond(npc, player_input, context)` → `NPCResponse(dialogue, disposition_change, revealed_info)`. L'action pipeline applique `npc.disposition += change` et persiste. |
| `MOVE` | `change_location(session, new_location_name)` — si nouvelle location inconnue, `WorldGenerator.generate()` en live (c'est le cas frequent en exploration). Update `session.current_location` + `campaign.current_location` + persistance. `hydrate_scene()` pour les nouveaux PNJs. |
| `PICKUP` | `take_scene_item(location, item_name, inventory)` — retire de `items_available`, ajoute à `inventory.items`. Update DB. |
| `USE_ITEM` | Consommation (potion → healing, scroll → spell). |
| `ATTACK` | `engine.combat.resolve_attack(attacker, defender, weapon, advantage, disadvantage)` → `AttackResult` + mutation HP. |
| `CAST_SPELL` | `engine.combat.resolve_spell(caster, spell, target, slot_level)` → `SpellCastResult` + consommation slot. |
| `DEFEND` | Applique un effet temporaire (advantage défensif). |
| `FLEE` | Termine le combat si réussi (check d'opportunité). |
| `IMPROVISE` | Construit un `MechanicsOutcome` générique avec l'intent joueur, laissé à narrer tel quel. |

**Sortie** : `MechanicsOutcome(summary, player_intent, outcome_facts, public_effects: PublicEffects)`.

`PublicEffects` inclut uniquement les changements **visibles au joueur** : `hp_delta`, `items_gained/lost`, `gold_delta`, `location_change`, `xp_gained`, `level_up`. Pas de disposition interne, pas de rolls cachés. Render en footer via `to_footer_text()`.

## Phase 5 — ASSEMBLING_CONTEXT

**Acteur** : `memory.context_assembler.ContextAssembler.assemble(campaign_id, …)` (4 couches) OR `bot.action_pipeline._assemble_context()` (version simplifiée sans RAG pour certains chemins).

Construit une chaîne markdown avec :
- Layer 1 : état structuré (HP/AC/inventory highlights/location/NPCs/quest active) — max 450 tokens.
- Layer 2 : 12 derniers exchanges narratifs — 700 tokens.
- Layer 3 : derniers résumés compressés — 400 tokens.
- Layer 4 : résultats RAG ChromaDB pertinents (lore, fiches NPC, past events) — 350 tokens.

Budget total : **2500 tokens**. Truncation par priorité croissante : on tronque Layer 4 en premier, Layer 1 jamais. Voir [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md).

Side-effect : peut déclencher `Summarizer.summarize()` si seuil de 20 exchanges non-résumés atteint.

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
4. **Beat advancement (Lot D)** : `session.advance_beat_if_ready()` — fuzzy match location courante vs `arc.beats[current + 1].location_hint`, seuil 0.7. Si match, `arc.current_beat_index += 1` + persist + poste un `beat_embed`.
5. **Story Director (tous les ~20 tours)** : `StoryDirector.check_coherence(campaign_id, context)` → `DirectorNote(coherence_issues, suggested_hooks, priority)`, persisté en `SemanticMemory` comme `SemanticDocument`. Voir [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md).

## Cas d'erreur

- `OllamaUnavailableError` → retry 2 fois (5s, 15s), sinon post un message "MJ indisponible, réessayez".
- `LLMParseError` → dump du request/response dans `logs/narrator_failures/<timestamp>.json` + narration fallback.
- Action invalide → narré comme un refus in-character (pas une erreur Python brute).
- Ambiguïté non résolue (timeout view) → pipeline annulée, lock libéré.

## Retour de la pipeline au cog

`ActionHandlerCog` reçoit un des 3 types :

| Type | Traitement |
|---|---|
| `ActionPipelineResult` | Post embed narratif + footer effects + mise à jour story bible. |
| `AmbiguityResult` | Post `ClarificationView`, attend clic, relance Phase 2. |
| `UnknownEntityResult` | Post message de refus in-character (pas d'erreur). |

Puis libération du `action_lock`.
