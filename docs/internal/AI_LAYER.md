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
| `InterpretedAction` | Interpreter | `action_type`, `actor_name`, `target_name`, `weapon_name`, `spell_name`, `item_name`, `talk_topic`, `search_detail`, `confidence` |
| `NarrativeResult` | Narrator | `narrative`, `tone` (dramatic/tense/humorous/somber) |
| `DirectorNote` | Story Director | `coherence_issues`, `suggested_hooks`, `priority` (low/medium/high) |
| `NPCResponse` | NPC Agent | `dialogue`, `disposition_change` (-2 à +2), `revealed_info` |
| `NPCSheet` | NPC Generator | `personality`, `description`, `secrets`, `knowledge` |
| `MechanicsOutcome` | action_pipeline | `summary`, `player_intent`, `outcome_facts`, `public_effects` |
| `PublicEffects` | engine/bot | `hp_delta`, `items_gained/lost`, `gold_delta`, `location_change`, `xp_gained`, `level_up` |

`PublicEffects.to_footer_text()` rend un one-liner pour embed footer. **Aucune donnée sensible** (pas de disposition, pas de rolls cachés).

### `interpreter.py` — `Interpreter`

**Modèle** : `qwen3.5:4b`, temperature 0.3.

**Entrée** : `interpret(player_text, actor_name, scene_context: SceneContext, language)`

**Prompt** : `system_interpreter.txt` définit 14 ActionType valides et règles de classification (combat vs exploration, confidence scoring, règles de résolution contextuelle par rapport à `scene_context`).

**Sortie** : `InterpretedAction` validé. Fallback déterministe si parse fail :
- En combat → `DEFEND`
- Hors combat → `IMPROVISE` (echo raw text)

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

**Sortie** : `NarrativeResult(narrative, tone)`. Tone pilote la couleur de l'embed.

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
- **Pas de mentions mécaniques** (HP, dés, dégâts)
- Contenu narratif en français (ou langue demandée)

⚠ `StoryArc.campaign_id` initialisé à `""` — le caller doit le remplir.

### `story_director.py` — `StoryDirector`

**Modèle** : `qwen3.5:9b` **avec `think=True`**, temperature 0.7.

`check_coherence(campaign_id, context_prompt) -> DirectorNote`.

**Side-effect** : persiste le `DirectorNote` comme `SemanticDocument` dans `SemanticMemory` pour consommation aux tours suivants.

⚠ **Ne s'auto-déclenche pas** — c'est au caller de vérifier `interaction_count % 20 == 0`. Actuellement hooké dans `action_handler_cog`.

**Prompt** : `system_story_director.txt` — identifie issues de cohérence (contradictions, threads abandonnés) et suggère hooks. Priorité high/medium/low.

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
| `system_interpreter.txt` | 14 ActionType, règles de classification, confidence scoring |
| `system_narrator.txt` | Rôle MJ, canon faithfulness, dialogue verbatim, tiers d'outcome |
| `system_npc_agent.txt` | Agency PNJ, règles knowledge/secrets, mécanique disposition |
| `system_npc_generator.txt` | Génération de fiches PNJ, personnalité spécifique |
| `system_world_generator.txt` | Générateur de locations, descriptions d'items explicites, aliases NPC |
| `system_quest_generator.txt` | Design de quêtes contextuelles |
| `system_arc_generator.txt` | Arc de campagne, structure dramatique, contenu FR |
| `system_story_director.txt` | Analyse de cohérence, hooks, priorité |

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
