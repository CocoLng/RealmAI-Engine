# Architecture

RealmAI-Engine est un Game Master JDR propulsé par IA, accessible via Discord. La règle d'or est :

> **Le LLM narre. Le code arbitre. Aucune exception.**

Le moteur de règles est en Python pur (déterministe, testé à ~98%). Les LLM locaux (Ollama / Qwen 3.5) ne font que produire du texte : interprétation de l'entrée joueur, dialogue PNJ, narration. Aucune décision mécanique n'est déléguée au modèle.

## Couches

```
┌─────────────────────────────────────────────────────┐
│                 Discord (UI unique)                 │
│   slash commands · @mentions · buttons · embeds     │
└───────────────────────┬─────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────┐
│  bot/   — couche présentation + orchestration       │
│   cogs · views · embeds · ActionPipeline · Session  │
└───┬───────────────┬───────────────┬────────────┬────┘
    │               │               │            │
┌───▼────┐    ┌─────▼────┐    ┌─────▼─────┐ ┌────▼────┐
│ engine/│    │   ai/    │    │  memory/  │ │ world/  │
│ règles │    │   LLM    │    │ contexte  │ │ domaine │
│ D&D 5e │    │  Ollama  │    │ 4 couches │ │ Pydant. │
└───┬────┘    └─────┬────┘    └─────┬─────┘ └────┬────┘
    │               │               │            │
    └───────────────┴───────┬───────┴────────────┘
                            │
                  ┌─────────▼──────────┐
                  │  db/  (SQLAlchemy) │
                  │  SQLite + ChromaDB │
                  └────────────────────┘
```

### `engine/` — moteur de règles (pur Python, zéro LLM)
- `dice.py` : parseur `"2d6+3"`, 6 tiers d'outcome d20.
- `character.py` : Pydantic `Character`, `AbilityScores`, races, classes, XP, level-up.
- `inventory.py` : items, armures, armes, slots, attunement, AC.
- `spells.py` : slots, cantrips scaling, 20+ sorts SRD.
- `conditions.py` : 15 conditions SRD + effets (advantage/disadvantage).
- `combat.py` : initiative, attaques, jets de sauvegarde, death saves, `trivial_resolve`.
- `validators.py` : légalité des actions (combat + exploration).
- `starter_gear.py` : 15 kits de départ pré-construits.

### `ai/` — couche LLM (Ollama, JSON mode forcé)
- `client.py` : wrapper httpx autour d'Ollama `/api/chat` avec `format: json`.
- `interpreter.py` (qwen3.5:4b) : texte libre → `InterpretedAction`.
- `narrator.py` (qwen3.5:9b) : `MechanicsOutcome` → prose.
- `npc_agent.py` (4b) : génère le dialogue d'un PNJ + `disposition_change`.
- `npc_generator.py` : fiches PNJ lazily à la première rencontre.
- `world_generator.py`, `quest_generator.py`, `arc_generator.py` : contenu de campagne.
- `story_director.py` : check de cohérence périodique (tous les ~20 tours).
- `entity_resolver.py` : résolveur rule-based (lemmatisation FR + fuzzy) avec fallback LLM.
- `scene_context.py` : snapshot de ce que l'acteur perçoit.
- `models.py` : contrats I/O Pydantic v2 (14 modèles).
- `language.py` : injection directive de langue dans les prompts.
- `prompts/*.txt` : 8 system prompts.

### `memory/` — système de mémoire 4 couches
1. **Layer 1 — État structuré** (`state.py`) : snapshot SQLite, max 450 tokens, jamais tronqué.
2. **Layer 2 — Fenêtre glissante** (`sliding_window.py`) : 12 derniers échanges, 700 tokens.
3. **Layer 3 — Résumés compressés** (`summarizer.py`) : générés tous les 20 tours, 400 tokens.
4. **Layer 4 — RAG sémantique** (`semantic.py`) : ChromaDB, 1 collection/campagne, 350 tokens.
- `context_assembler.py` : orchestre et tronque par priorité pour respecter le budget total (2500 tokens).

### `world/` — domaine métier (Pydantic v2)
Modèles in-memory : `Campaign`, `NPC`, `Location`, `Quest`, `StoryArc` (+ `StoryBeat`). Enums : `Disposition`, `QuestStatus`, `EncounterType`.

### `db/` — persistance
- `database.py` : moteur SQLite + migrations incrémentales (`ALTER TABLE`).
- `models.py` : 10 tables SQLAlchemy (`campaigns`, `npcs`, `locations`, `quests`, `exchanges`, `summaries`, `story_arcs`, `player_characters`, `campaign_channels`, `guild_configs`).
- `mappers.py` : `to_db`/`from_db` pour chaque entité (sérialisation JSON des listes/dicts).
- `repositories/` : 11 repositories CRUD.

### `bot/` — couche Discord
- `bot.py` : `RealmBot`, chargement des 7 cogs + `test_bridge` (si `TEST_MODE=true`).
- `cogs/` : `session`, `character`, `inventory`, `combat`, `exploration`, `rolls`, `action_handler`, `test_bridge`.
- `action_pipeline.py` : les 6 phases (voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md)).
- `campaign_launcher.py` : orchestrateur d'onboarding (arc + location + persos + gear en parallèle).
- `game_session.py` : conteneur in-memory d'une campagne active (chars, inv, npcs, arc, lock asyncio).
- `scene_hydration.py` : promeut les NPCs string de `Location.npcs_present` en vraies lignes DB.
- `story_bible_logger.py` : log Markdown append-only par campagne (`logs/campaigns/<id>.md`).
- `llm_retry.py` : retry exponentiel (5s, 15s) sur `OllamaUnavailableError`.
- `views/`, `embeds/` : composants UI Discord.
- `i18n.py` : labels FR/EN statiques (races, classes, kits).
- `world_navigation.py` : helpers MOVE et location change.

### `mcp_discord/` — serveur MCP pour tests live
Expose 7 outils MCP à Claude Code pour piloter un bot « testeur » qui envoie de vraies commandes à l'instance de jeu dans un salon Discord dédié. Voir [TESTING.md](TESTING.md).

## Flux de données — tour de jeu

```
Joueur ─ "@Realm j'attaque le gobelin avec mon épée"
   │
   ▼  bot/cogs/action_handler.py  (filtre OOC, prend session.action_lock)
ActionPipeline (bot/action_pipeline.py)
   │
   ├─ 1. INTERPRETING    → ai/interpreter.py  (qwen3.5:4b, JSON)
   │                       → InterpretedAction(action_type=ATTACK, target_name="gobelin")
   │
   ├─ 2. RESOLVING_ENTITIES → ai/entity_resolver.py  (Python: exact → lemma FR → fuzzy)
   │                          → NPC canonique OR candidates OR unknown
   │                          → ambigu : post ClarificationView, pause
   │
   ├─ 3. VALIDATING     → engine/validators.py
   │                       → bootstrap combat si attaque hors combat (Lot C)
   │                       → trivial_resolve si PNJ faible/pacifique (Lot E)
   │
   ├─ 4. RESOLVING_ACTION → engine/combat.py / scene_hydration / world_navigation
   │                        → MechanicsOutcome (dégâts, public_effects, outcome_facts)
   │                        → DB writes si mutation (move, kill, pickup)
   │
   ├─ 5. ASSEMBLING_CONTEXT → memory/context_assembler.py
   │                          → 4 couches concaténées, budget 2500 tokens
   │
   └─ 6. NARRATING       → ai/narrator.py  (qwen3.5:9b, JSON {narrative, tone})
                           → NarrativeResult
                           ↓
              bot/embeds/narrative_embed.py + PublicEffects footer
                           ↓
                      Discord embed
                           ↓
       session.story_bible.log_turn(...)   + exchange persisté (Layer 2)
                           ↓
         session.advance_beat_if_ready()   (Lot D — fuzzy match location)
```

## Stack

| Couche | Tech |
|---|---|
| Bot | `discord.py ≥2.7` |
| Modèles | `pydantic ≥2,<3` |
| DB | `SQLAlchemy ≥2.0` + SQLite |
| RAG | `chromadb ≥1.5` (PersistentClient `data/chromadb`) |
| LLM | Ollama local (`http://localhost:11434`), Qwen 3.5 4B + 9B |
| HTTP | `httpx` |
| Qualité | `pytest`, `pytest-asyncio`, `pytest-httpx`, `pytest-cov`, `ruff`, `mypy` |
| Outillage | `uv` (project manager) |
| MCP | `mcp ≥1.27` (serveur stdio pour tests Discord live) |

## Points d'ancrage critiques

- **Jamais de LLM dans `engine/`** — grep `from ai` dans `engine/` doit toujours être vide.
- **Toujours `format: json`** (`ai/client.py`) — le tool-calling natif d'Ollama est cassé avec Qwen 3.5.
- **Pydantic v2 partout** en domaine ; `db/models.py` est le seul endroit SQLAlchemy.
- **1 lock asyncio par session** (`session.action_lock`) : un seul pipeline à la fois par campagne.
- **Transitions d'état via mappers** uniquement — jamais de `session.add(row)` dans les cogs.
- **Les sessions in-memory sont perdues au redémarrage** — `/resume` ré-hydrate depuis la DB, mais un crash en plein combat laisse un état partiel (voir [ISSUES.md](ISSUES.md)).
