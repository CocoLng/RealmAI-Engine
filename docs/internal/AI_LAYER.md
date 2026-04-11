# AI Layer — `ai/`

Couche LLM pour narration, interprétation, génération de contenu et résolution d'entités. **Toutes les sorties sont JSON strict (Ollama `format: json`)**. Le tool-calling natif d'Ollama est évité (cassé avec Qwen 3.5).

## Stack

- **Ollama local** `http://localhost:11434` (via `httpx`)
- **Modèles** :
  - `qwen3.5:4b` — rapide (~50-70 tok/s), utilisé pour classification (interpreter, NPC agent, NPC generator)
  - `qwen3.5:9b` — riche (~25-35 tok/s), utilisé pour narration, génération de monde/quests/arc, story director
  - Jamais chargés simultanément (limitation M3 Pro 18GB)
- **Thinking mode** (`think=true`) activé pour : arc generation, world generation, quest generation, story director. Token budget cappé à `_THINKING_TOKEN_CAP = 4096`.
- **JSON mode** : toujours `format: "json"` dans l'appel HTTP. Les sorties sont validées par Pydantic v2.

## Fichier par fichier

### `client.py` — `OllamaClient`

Wrapper `httpx` autour d'`/api/chat`. Une méthode principale : `chat_json(model, messages, temperature, think=False, num_ctx=16384) -> dict`.

- Check de connectivité Ollama à l'init (raise `OllamaUnavailableError` si down).
- Timeout 600s en thinking mode, sinon défaut.
- Log DEBUG du prompt/réponse (tronqué).
- Empty content → `LLMParseError` avec request metadata préservée.
- **Pas de retry** : délégué à `bot/llm_retry.py`.
- **Pas de cap** sur les tokens de thinking.

### `language.py`

`language_instruction(code: str) -> str` — renvoie un préfixe à injecter dans les system prompts pour forcer la langue (fr, en, es, de, pt). Défaut `fr`. Implémentation minimaliste ; repose sur la compliance du LLM.

### `models.py` — contrats I/O Pydantic

| Modèle | Producteur | Champs clés |
|---|---|---|
| `InterpretedAction` | Interpreter | `action_type`, `actor_name`, `target_name`, `weapon_name`, `spell_name`, `item_name`, `talk_topic`, `search_detail`, `improvise_description`, `is_lethal_intent`, `confidence` |
| `NarrativeResult` | Narrator | `narrative`, `tone` (dramatic/tense/humorous/somber) |
| `DirectorNote` | Story Director | `coherence_issues`, `suggested_hooks`, `priority` (low/medium/high) |
| `NPCResponse` | NPC Agent | `dialogue`, `disposition_change` (-2 à +2), `revealed_info` |
| `NPCSheet` | NPC Generator | `personality`, `description`, `secrets`, `knowledge` |
| `MechanicsOutcome` | action_pipeline | `summary`, `player_intent`, `outcome_facts`, `public_effects` |
| `PublicEffects` | engine/bot | `hp_delta`, `items_gained/lost`, `gold_delta`, `location_change`, `xp_gained`, `level_up` |
| `CompletionTrigger` | ArcGenerator (dans StoryBeat) | `type` (interact/defeat/talk/arrive/search/pickup), `target` |
| `BeatEffects` | ArcGenerator (dans StoryBeat) | `unlock_exits`, `add_npcs`, `remove_items`, `add_items`, `state_flags`, `narrative_hint` |
| `TacticalDecision` | NPCTactician | `action_type` (attack/signature/move/dodge/disengage), `target_name`, `weapon_name`, `signature_name`, `move_to_zone`, `reasoning`, `legendary_action_name` |

`PublicEffects.to_footer_text()` rend un one-liner pour embed footer. **Aucune donnée sensible** (pas de disposition, pas de rolls cachés).

### `interpreter.py` — `Interpreter`

**Modèle** : `qwen3.5:4b`, temperature 0.3.

**Entrée** : `interpret(player_text, actor_name, scene_context: SceneContext, language)`

**Prompt** : `system_interpreter.txt` définit 15 ActionType valides (dont `QUESTION` pour les questions méta sur l'état du jeu) et règles de classification (combat vs exploration, confidence scoring, règles de résolution contextuelle par rapport à `scene_context`).

**Sortie** : `InterpretedAction` validé. Fallback déterministe si parse fail :
- En combat → `DEFEND`
- Hors combat → `IMPROVISE` (echo raw text)

**Détection d'intention létale** (Task 40) : le prompt contient une section "Détection d'intention létale" qui demande au LLM de flaguer `is_lethal_intent=True` quand le joueur exprime explicitement une volonté de blesser une créature nommée/visible ("je poignarde Vellus", "boule de feu sur les bandits"). Les intimidations ("je menace le garde") et attaques d'objets ("j'attaque la porte") restent `False`. Consommé par l'action pipeline pour bootstrap automatique d'un combat même quand `action_type ≠ ATTACK`. Rétro-compatible : legacy JSON sans le champ → default `False`.

**Autre méthode** : `disambiguate_entity(candidates, context)` — fallback LLM utilisé par `EntityResolver` uniquement quand Python a renvoyé 0 candidat (Lot B). `temperature 0.1, num_predict 64`. Ne raise jamais.

### `narrator.py` — `Narrator`

**Modèle** : `qwen3.5:9b`, temperature 0.8.

**Entrée** :
```python
narrate(
    action_result_text: str,       # "Thorin attaque Gobelin. Hit! 8 damage."
    context_prompt: str,           # 4-layer memory output
    language: str,
    player_intent: str,            # "HOW" — le style du joueur
    outcome_facts: str,            # "WHAT" — vérité canonique
)
```

**Prompt** : `system_narrator.txt` définit :
- Rôle MJ D&D 5e
- Tiers d'outcome guidant le ton
- **Canon faithfulness** : description de location et items = absolus
- **Dialogue verbatim** : tout `<NPCName> says: "..."` dans State changes DOIT être reproduit verbatim
- **Acting character awareness** : le narrateur doit utiliser race/classe/niveau/arme pour ancrer la prose (task 70).
- **COMBAT ACTIVE — règles spéciales** (task 70) : quand le contexte contient une section `## COMBAT ACTIVE`, le narrateur doit respecter mécaniquement chaque résultat, narrer tour par tour, terminer sur une invitation au tour suivant, garder les HP NPC vagues, et refuser toute évasion passive du combat.

**Sortie** : `NarrativeResult(narrative, tone)`. Tone pilote la couleur de l'embed.

### `narrator_phase.py` — `narrate_phase_transition` (task 71)

**Modèle** : `qwen3.5:9b`, temperature 0.85. **Prompt** : `system_narrator_phase.txt` — prompt court et dédié exigeant 3-5 phrases cinématiques, ton sombre, aucune mécanique chiffrée, fin sur menace implicite.

**Entrée** :
```python
narrate_phase_transition(
    client: OllamaClient,
    event: PhaseTransitionEvent,   # combatant_name + narrative_cue + phase_index
    boss: Combatant,
    state: CombatState,
    language: str = "fr",
) -> str
```

**Sortie** : texte brut (3-5 phrases) extrait du JSON `{"narration": "..."}`. Appelé par [bot/combat_turn_manager.py::_flush_pending_cues](../../bot/combat_turn_manager.py) après chaque tour pour transformer les `PhaseTransitionEvent` non-consommés en embeds dorés. Le caller marque `event.consumed = True` avant l'appel LLM et retombe sur le `narrative_cue` brut en cas d'échec ou d'absence de `session.ollama_client`.

### `npc_agent.py` — `NPCAgent`

**Modèle** : `qwen3.5:4b`, temperature 0.7.

**Entrée** : `respond(npc: NPC, player_input, context_prompt, language)` — NPC est **lu seulement**, jamais muté.

Construit un user message incluant : context + fiche NPC (perso, race, disposition, secrets, knowledge) + dialogue_history (5 derniers) + player_input.

**Règles** (dans le prompt) :
- `knowledge` : partagé largement (sauf HOSTILE).
- `secrets` : révélés à `FRIENDLY + 2+ positive` ou si le joueur tape pile sur le sujet.
- Disposition peut remonter après un geste correctif.

**Sortie** : `NPCResponse(dialogue, disposition_change, revealed_info)`. **Le caller doit appliquer** `npc.disposition += disposition_change`.

### `npc_generator.py` — `NPCGenerator`

**Modèle** : `qwen3.5:4b`, temperature 0.8.

`generate(npc_name, location_context, campaign_theme, language) -> NPCSheet`. Lazy : appelé une seule fois par PNJ à la première rencontre. Le résultat est persisté sur `NPC.personality`, `description`, `secrets`, `knowledge`.

**Prompt** : `system_npc_generator.txt` — personnalité spécifique et jouable (éviter les archétypes fades).

### `world_generator.py` — `WorldGenerator`

**Modèle** : `qwen3.5:9b` **avec `think=True`**, temperature 0.8.

`generate(campaign_context, location_type, location_name=None, language) -> Location`.

**Validation critique** : `item_descriptions` keys MUST match `items_available`. Les descriptions orphelines sont silencieusement filtrées ⚠ (pas de log — voir [ISSUES.md](ISSUES.md)).

**Prompt** : `system_world_generator.txt` — force :
- Chaque item DOIT avoir une description explicite (matériau, époque, condition).
- NPC aliases : variants de genre/nombre/profession/archétype (2-6 par PNJ).
- **Combat zones** (Task 41) : 2-4 zones nommées par location-combat avec adjacence symétrique, tags tactiques (`cover`, `difficult_terrain`, `elevated`, `hazard`, `obscured`), vide pour locations paisibles.
- **Combat triggers** (Task 41) : 0-2 ambushes par location, clé = nom d'item/mechanism, payload = `spawn_npcs` + `reveal_narration`, idempotence via `consumed=False`.

**Parser résilient** : le parseur construit d'abord les `Zone` individuellement (drop silencieux des entrées invalides), puis tente la construction du `Location` ; si l'adjacence globale casse (`ValidationError` du `_validate_zones_graph`), fallback sur `combat_zones=[]` sans perdre le reste de l'output.

### `quest_generator.py` — `QuestGenerator`

**Modèle** : `qwen3.5:9b` **avec `think=True`**, temperature 0.8.

`generate(campaign_context, location_name, available_npcs, language="fr") -> Quest`.

Contraintes enforced par prompt uniquement (pas code) :
- 1-4 objectives
- reward_xp : 50-2000
- reward_gold : 0-500

### `arc_generator.py` — `ArcGenerator`

**Modèle** : `qwen3.5:9b` **avec `think=True`**, temperature 0.8.

`generate(theme, player_count, language) -> StoryArc`.

**Prompt** : `system_arc_generator.txt` — Structure dramatique (introduction → montée → twist → climax boss → résolution). Règles :
- 10-15 beats, dernier = `encounter_type=boss`
- **Pas de mentions mécaniques** (HP, dés, dégâts) dans le narratif
- Contenu narratif en français (ou langue demandée)
- Chaque beat inclut un `completion_trigger` (type + target) et un `on_complete` (BeatEffects : unlock_exits, state_flags, narrative_hint)
- **Villain stat block mandatory** (Task 42) : le prompt exige un `villain_stat_block` complet (NPCStatBlock : tier=boss, 2-3 signatures thématiques, 3 legendary_actions costs 1/2/3, 1-2 phases). Casing enums strict : `damage_type` TitleCase (`"Slashing"`), `save_ability` UPPERCASE (`"WIS"`), `target_scope`/`kind` lowercase, `condition_name` TitleCase.

**Parser villain_stat_block** : `_resolve_villain_stat_block` valide séparément avant `StoryArc.model_validate`. En cas de `ValidationError` (casing cassé, champs manquants) ou payload absent, fallback sur `get_archetype('generic_boss')` tagué `archetype="generic_boss:<villain_name>"` pour traçabilité. Un arc généré a donc TOUJOURS un `villain_stat_block != None`.

⚠ `StoryArc.campaign_id` initialisé à `""` — le caller doit le remplir.

### `story_director.py` — `StoryDirector`

**Modèle** : `qwen3.5:9b` **avec `think=True`**, temperature 0.7.

`check_coherence(campaign_id, context_prompt) -> DirectorNote`.

**Side-effect** : persiste le `DirectorNote` comme `SemanticDocument` dans `SemanticMemory` pour consommation aux tours suivants.

⚠ **Ne s'auto-déclenche pas** — c'est au caller de vérifier `interaction_count % 20 == 0`. Actuellement hooké dans `action_handler_cog`.

**Prompt** : `system_story_director.txt` — identifie issues de cohérence (contradictions, threads abandonnés) et suggère hooks. Priorité high/medium/low.

### `npc_tactician.py` — `NPCTactician`

**Modèle** : `qwen3.5:4b`, temperature 0.7, `think=False` (même cadence que l'Interpreter — le boss doit jouer vite).

**Entrée** : `decide(boss, state, party_context, recent_events, language)`.

**Prompt** : `system_npc_tactician.txt` — schéma JSON strict, règles « pas de dés, jamais », rappel que `target_name` / `signature_name` / `weapon_name` / `move_to_zone` doivent référencer des entités existantes.

**Sortie** : `TacticalDecision` (Pydantic) — `action_type ∈ {attack, signature, move, dodge, disengage}`, `target_name`, `weapon_name`, `signature_name`, `move_to_zone`, `reasoning` (min 5 chars), `legendary_action_name` (réservé task 53, ignoré en MVP).

**Post-validation** : `NPCTactician._validate_references` vérifie que chaque référence (target, signature, weapon) existe réellement dans le `state` / `stat_block` du boss et raise `ValueError` sinon. C'est ce signal que `engine/npc_ai/boss_brain.py::decide_boss_action` utilise pour retry x2 puis fallback sur `decide_elite_action` (profil AGGRESSIVE) — le boss joue toujours, quitte à jouer bête.

**Règle d'or préservée** : le tactician ne roule aucun dé, n'applique aucun dégât, ne mute pas l'état. Il produit une intention ; l'engine exécute.

### `entity_resolver.py` — `EntityResolver`

**100% Python + fallback LLM optionnel**. Voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md#phase-2--resolving_entities) pour la stratégie complète.

Sortie : `ResolutionResult(status, field_name, resolved_entity, candidates, reason)`.

### `scene_context.py` — `SceneContext` + `build_scene_context`

Snapshot de ce que l'acteur perçoit. Pydantic model :
```python
SceneContext(
    location_name, location_description,
    visible_npcs, visible_exits, visible_objects,
    in_combat, combat_summary, enemies_visible,
)
```

`build_scene_context(location, npcs: dict[str, NPC], combat_state)` construit la snapshot. Prend des objets primitifs (pas `GameSession`) pour éviter les imports circulaires.

## Prompts (`ai/prompts/`)

| Fichier | Contenu |
|---|---|
| `system_interpreter.txt` | 15 ActionType (incl. QUESTION), règles de classification, confidence scoring |
| `system_narrator.txt` | Rôle MJ, canon faithfulness, dialogue verbatim, tiers d'outcome, beat awareness, **acting character awareness + COMBAT ACTIVE rules** (task 70) |
| `system_narrator_phase.txt` | Narration cinématique courte (3-5 phrases) pour transitions de phase boss, sortie JSON `{"narration"}` (task 71) |
| `system_npc_agent.txt` | Agency PNJ, règles knowledge/secrets, mécanique disposition |
| `system_npc_generator.txt` | Génération de fiches PNJ, personnalité spécifique |
| `system_world_generator.txt` | Générateur de locations, descriptions d'items explicites, aliases NPC |
| `system_quest_generator.txt` | Design de quêtes contextuelles |
| `system_arc_generator.txt` | Arc de campagne, structure dramatique, completion triggers, beat effects, contenu FR |
| `system_story_director.txt` | Analyse de cohérence, hooks, priorité |
| `system_npc_tactician.txt` | Brain tactique boss : schéma JSON strict, règles pas-de-dés, style FR/EN, no narration |

## Retry et résilience (`bot/llm_retry.py`)

Bien que dans `bot/`, c'est le wrapper utilisé pour tous les appels LLM critiques (Interpreter, Narrator, NPCAgent dans l'action_pipeline) :

```python
await retry_llm_call(
    fn,                  # coroutine sans arg
    max_retries=2,
    delays=(5.0, 15.0),  # backoff exponentiel
    on_retry=callback,   # async notify "MJ en attente..."
    log_label="Narrator",
)
```

Retry sur `OllamaUnavailableError` et `ValueError`. `LLMParseError` dump la paire request/response dans `logs/narrator_failures/` pour diagnostic offline.

## Test coverage (`tests/ai/`)

| Fichier | Couverture |
|---|---|
| `test_client.py` | HTTP mocking via `pytest-httpx`, parse JSON, timeouts, errors |
| `test_interpreter.py` | Classification, fallbacks combat/exploration |
| `test_narrator.py` | Prose generation, tone, temperature |
| `test_npc_agent.py` | Dialogue, disposition signals |
| `test_npc_generator.py` | Génération de fiches |
| `test_quest_generator.py`, `test_arc_generator.py`, `test_world_generator.py` | Générateurs |
| `test_story_director.py` | Coherence checking |
| `test_scene_context.py` | Scene assembly |
| `test_models.py` | Validation Pydantic |

**Gap** : pas de test end-to-end avec vrai Ollama. Tout mock via `pytest-httpx`.

## Principes de design

1. **Modularité** : une responsabilité par fichier, pas de re-exports dans `ai/__init__.py` pour éviter les imports circulaires.
2. **Defensive** : toutes les sorties LLM validées par Pydantic, fallbacks déterministes partout.
3. **Stateless** : aucun module n'a d'état partagé ; tous les services sont instanciés par `create_ai_services(session)`.
4. **Model-agnostic** : le modèle est paramétré (pas hardcodé sauf dans les constructeurs).
5. **Priority to gameplay stability** : les features fancy (reranking, chain-of-thought, multi-step reasoning) sont absentes au profit de la robustesse.
