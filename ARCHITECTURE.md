# Architecture

> **The LLM narrates. The code arbitrates. No exceptions.**

RealmAI-Engine is a Discord-fronted AI Game Master. A deterministic Python
engine resolves every mechanical outcome (dice, damage, conditions, XP,
inventory). Local LLMs served by [Ollama](https://ollama.com) only ever turn
free text into JSON, or turn an `ActionResult` into prose. Anything that can
swing the game is computed in Python and checked by a validator before any
LLM is invoked.

This document is the **technical entry point** for the codebase. For deep
dives module-by-module, see [`docs/internal/`](docs/internal/README.md) (the
authoritative French-language reference, kept in lockstep with the code).

---

## 1. Layered view

```
                       ┌──────────────────────────────────────────┐
                       │            Discord (single UI)           │
                       │  slash commands · @mentions · buttons    │
                       └────────────────────┬─────────────────────┘
                                            │
                       ┌────────────────────▼─────────────────────┐
                       │  bot/   — presentation + orchestration   │
                       │  cogs · views · embeds · ActionPipeline  │
                       │  GameSession · TurnManager · launcher    │
                       └───┬───────────┬───────────┬──────────┬───┘
                           │           │           │          │
                  ┌────────▼──┐  ┌─────▼─────┐ ┌───▼────┐ ┌───▼────┐
                  │  engine/  │  │    ai/    │ │memory/ │ │ world/ │
                  │ rules,    │  │ LLM I/O,  │ │4-layer │ │domain  │
                  │ combat,   │  │ prompts,  │ │context │ │models  │
                  │ validators│  │ JSON only │ │        │ │(Pyd v2)│
                  └────────┬──┘  └─────┬─────┘ └───┬────┘ └───┬────┘
                           │           │           │          │
                           └───────────┴───────┬───┴──────────┘
                                               │
                                  ┌────────────▼──────────────┐
                                  │  db/   SQLAlchemy + SQLite│
                                  │  ChromaDB (PersistentClt) │
                                  └───────────────────────────┘

                       ┌──────────────────────────────────────────┐
                       │  mcp_discord/  MCP server (test rig)     │
                       │  drives a tester bot from Claude Code    │
                       └──────────────────────────────────────────┘
```

Dependency rule: arrows above are downward only. `engine/` makes **no LLM
calls** and never imports from `ai/`, `bot/`, `memory/`, or `db/`. The shared
I/O contracts (`InterpretedAction`, `MechanicsOutcome`, `PublicEffects`,
`TacticalDecision`) live in `engine/contracts.py` so the engine owns them; `ai/`
imports them downward and re-exports from `ai.models` for compatibility. The
boss brain takes its LLM tactician as an injected `Tactician` Protocol — no
`ai` import, even under `TYPE_CHECKING`. `ai/` never imports from `bot/`. The
bot orchestrates everything else. (Guard test:
`tests/engine/test_no_ai_imports.py`.)

---

## 2. Module responsibilities

### `engine/` — pure deterministic rules (zero LLM)

| File | Responsibility |
|---|---|
| `dice.py` | `NdM+X` parser, d20 check with 6 outcome tiers |
| `character/` | `Character`, `AbilityScores`, races (7), classes (6), XP, level-up, presets, random stats |
| `inventory.py` | Items catalogue (25+), 9 slots, attunement, weight, AC |
| `spells.py` | Spell catalogue (~20 SRD), slots, cantrip scaling |
| `conditions.py` | 17 SRD conditions + advantage/disadvantage effects, `consume_surprise_if_present`, `check_concentration_save` |
| `combat.py` | Initiative (3 cases), attacks, saves, death saves, `apply_damage`, `advance_turn`, `check_combat_end`, `record_combat_event` |
| `combat_phases.py` | Boss HP thresholds → `check_phase_transition` |
| `combat_trigger.py` | `CombatTriggerKind`, `InitiativeSide` (PLAYERS / NPCS / BOTH_READY), `CombatTrigger` model |
| `npc_ai/` | Tier-based brains: `scripted.py` (minion BFS), `elite.py` (4 behavior profiles), `legendary.py` (off-turn actions), `boss_brain.py` (LLM tactician fallback) |
| `npc_stat_block.py`, `npc_library.py` | NPC stat blocks + combat archetype builders |
| `validators.py` | `validate_combat_action`, `validate_exploration_action`, `validate_truce_attempt` — every player action passes through here |
| `contracts.py` | Shared I/O contracts: `InterpretedAction`, `MechanicsOutcome`, `PublicEffects`, `TacticalDecision` (here, not in `ai/`, so `engine/` never imports `ai/`) |
| `beat_progression.py` | `BeatProgressionEngine.evaluate()` → `{ADVANCE, STAY, NEEDS_JUDGE}` |
| `objective_matchers.py` | Deterministic gate matchers (DEFEAT, MIN_REVEALS, HAS_ITEM, FLAG_SET, …) |
| `skill_check.py` | `IMPROVISE` contested checks (D&D 5e) |
| `starter_gear.py` | 14 starter kits across 6 classes |

**Invariant**: `engine/` makes no LLM calls and has **zero** `from ai` imports.
The shared I/O contracts live in `engine/contracts.py`; the boss brain takes its
tactician as an injected `Tactician` Protocol. Enforced by
`tests/engine/test_no_ai_imports.py` (an AST scan that catches `TYPE_CHECKING`
imports too).

### `ai/` — LLM I/O, JSON-mode only

| File | Model | Role |
|---|---|---|
| `client.py` | — | `httpx` wrapper around Ollama `/api/chat`, `format: "json"` enforced |
| `interpreter.py` | qwen3.5:4b | Free text → `InterpretedAction` (15 `ActionType`s) |
| `narrator.py` | qwen3.5:9b | `MechanicsOutcome` → `{narrative, tone}` |
| `narrator_phase.py` | qwen3.5:9b | Phase transition cinematics (3-5 sentences) |
| `npc_agent.py` | qwen3.5:4b | NPC dialogue + `disposition_change` + `revealed_info` |
| `npc_generator.py` | qwen3.5:4b | Lazy NPC sheet on first encounter |
| `npc_tactician.py` | qwen3.5:4b | Boss combat decisions (`TacticalDecision`) |
| `world_generator.py` | qwen3.5:9b | Locations, NPCs, items, combat zones + triggers |
| `arc_generator.py` | qwen3.5:9b | 10-15 beats with calibrated `objectives[]`, boss villain stat block |
| `beat_judge.py` | qwen3.5:4b | Per-beat `judge_rubric` → confidence + reasoning |
| `story_director.py` | qwen3.5:9b | Periodic coherence check (~20 turns) |
| `entity_resolver.py` | qwen3.5:4b (fallback) | Rule-based first: exact → FR lemma → fuzzy → LLM fallback |
| `objective_recipes.py` | — | Recipe table + `scaffold_objectives()` (pure Python safety net) |
| `scene_context.py` | — | Snapshot of what the acting character perceives |
| `language.py` | — | Inject language directive into prompts |
| `models.py` | — | 4 ai-only models (`NarrativeResult`, `DirectorNote`, `NPCResponse`, `NPCSheet`) + re-exports the 4 `engine.contracts` models |
| `prompts/*.txt` | — | 10 system prompts + 1 brainstorm prompt |

**Invariant**: every Ollama call sets `format: "json"`. Ollama's native
tool-calling is broken with Qwen 3.5 (closed issues #14493, #14745). JSON
mode is also better for our anti-cheat posture — there is no scenario where
the LLM "calls a tool" that mutates state.

### `memory/` — 4-layer context, budgeted

| Layer | File | Source | Token budget | Truncation |
|---|---|---|---:|---|
| 1. Structured state | `state.py` | SQLite snapshot | 450 | never |
| 2. Sliding window | `sliding_window.py` | last 12 exchanges | 700 | oldest first |
| 3. Compressed summaries | `summarizer.py` | auto-gen every ~20 turns | 400 | oldest first |
| 4. Semantic RAG | `semantic.py` | ChromaDB (1 collection/campaign) | 350 | lowest score first |

`context_assembler.py` enforces a global ~2500-token cap and truncates by
priority. `token_utils.py` uses `max(chars/3.5, words×1.5)` — biased toward
**over-estimation** because the cost of overflow (Ollama OOM) is higher
than the cost of slightly less context. `indexer.py` writes to ChromaDB
on every persisted exchange.

### `world/` — domain models (Pydantic v2)

In-memory game state, no DB awareness:
`Campaign`, `Location`, `NPC`, `Quest`, `StoryArc` (+ `StoryBeat` +
`ObjectiveState`), `CombatZone`, `CombatTriggerDef`. Enums:
`Disposition`, `QuestStatus`, `EncounterType`, `ObjectiveKind`, `GateKind`,
`ZoneTag`.

### `db/` — persistence

- `database.py` — engine + session factory; `init_db` delegates to
  `migrations.ensure_schema`.
- `migrations.py` — forward schema reconciliation: `create_all()` for missing
  tables, then auto `ALTER TABLE ADD COLUMN` for any model column an existing
  table lacks (safe `DEFAULT` for NOT NULL), plus a `schema_version` stamp. No
  Alembic; `data/` stays dev-only and disposable (`scripts/reset_dev_data.py`).
- `models.py` — 11 SQLAlchemy tables (`campaigns`, `npcs`, `locations`,
  `quests`, `exchanges`, `summaries`, `story_arcs`, `player_characters`,
  `campaign_channels`, `guild_configs`, `hint_usage`).
- `mappers.py` — bidirectional `to_db` / `from_db` per entity with JSON
  serialization of nested lists/dicts. Corrupted entries are skipped + logged
  rather than crashing the load (`_validate_list` / `_validate_dict`
  helpers).
- `repositories/` — 11 CRUD repos. Mutations go through `upsert()` —
  explicit get-then-write, not exception-driven.

### `bot/` — Discord layer

Entry point: `main.py` → `bot.bot.run_bot()`.

| Sub | What lives here |
|---|---|
| `bot.py` | `RealmBot` (extends `commands.Bot`), cog loading, intents, `on_ready`, `bot.sessions: dict[int, GameSession]`, `bot.lobbies: dict[int, LobbyState]` |
| `cogs/` | `session`, `character`, `inventory`, `combat`, `rolls`, `hint`, `action_handler` (the `@mention` entry), `test_bridge` (gated on `TEST_MODE`) |
| `pipeline/` | `orchestrator.PipelineRunner` (6 phases), `interpret`, `resolve`, `narrate`, `drift_tracker` |
| `action_pipeline.py` | Backward-compat facade re-exporting `pipeline/` |
| `views/` | 9 Discord views (lobby, character setup, combat actions, target/spell/zone/equip/potion select, clarification) over a `LoggedView` base |
| `embeds/` | 13 embed builders (narrative, scene, combat start/state/end, dice, character, inventory, lobby, arc tracker, beat, action progress, character setup recap) |
| `combat_entry.py`, `combat_turn_manager.py`, `combat_end.py`, `combat_truce.py` | Combat lifecycle helpers — orchestrate the engine from the bot side |
| `game_session.py` | In-memory container for an active campaign (characters, NPCs, arc, `action_lock: asyncio.Lock`) |
| `lobby_state.py` | Pre-launch lobby (join/leave/start/cancel) before `GameSession` is born |
| `scene_hydration.py` | Promote NPCs declared as plain strings in `Location.npcs_present` into real DB rows on demand; also assembles the narrator scene description |
| `persistence.py` | Single `persist_session()` entry — explicit upsert per entity, single rollback on failure |
| `story_bible_logger.py` | Append-only Markdown log per campaign (`logs/campaigns/<id>.md`) |
| `llm_retry.py` | Exponential backoff (5s, 15s) on `OllamaUnavailableError` |
| `i18n.py` | Static FR/EN labels (races, classes, kits) |
| `world_navigation.py` | MOVE + location-change helpers |
| `utils/channel_manager.py` | Channel creation, permission overrides, archival |
| `utils/arc_tracker.py` | Arc tracker pinned message manager |

### `mcp_discord/` — MCP test rig

Stdio MCP server exposing 7 tools to Claude Code (read messages, send
command, click button, submit modal, select option, wait for response,
game state). Used to drive a separate "tester bot" against a live instance
of the game in a dedicated Discord channel. Not part of the runtime — only
loaded under `TEST_MODE`.

---

## 3. Action pipeline (6 phases)

Implemented in [`bot/pipeline/orchestrator.py`](bot/pipeline/orchestrator.py).
Entry: `bot/cogs/action_handler.py` (filters OOC text, takes
`session.action_lock`).

```
@Realm "j'attaque le gobelin avec mon épée"
      │
      ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 1. INTERPRETING        ai/interpreter.py   (qwen3.5:4b · JSON)      │
│    → InterpretedAction(action_type=ATTACK, target="gobelin", ...)   │
├─────────────────────────────────────────────────────────────────────┤
│ 2. RESOLVING_ENTITIES  ai/entity_resolver.py   (Python, LLM rare)   │
│    exact → FR lemma → fuzzy → LLM fallback                          │
│    → resolved NPC | candidates list | unknown                       │
│    → if ambiguous: post ClarificationView, suspend pipeline         │
├─────────────────────────────────────────────────────────────────────┤
│ 3. VALIDATING          engine/validators.py   (Python)              │
│    combat-aware: SURPRISED guard, action budget, friendly-fire,     │
│    range, ownership, alive, turn order                              │
│    side-effects: bootstrap CombatState if first hostile action;     │
│                  trivial_resolve for weak / pacific NPCs            │
│    → invalid: Discord error, narrator NEVER called                  │
├─────────────────────────────────────────────────────────────────────┤
│ 4. RESOLVING_ACTION    engine/combat.py · scene_hydration ·         │
│                        world_navigation                             │
│    → MechanicsOutcome (rolls, damage, public_effects,               │
│                        outcome_facts, target_defeated)              │
│    → DB writes for any mutation (HP, kill, pickup, move)            │
├─────────────────────────────────────────────────────────────────────┤
│ 5. ASSEMBLING_CONTEXT  memory/context_assembler.py                  │
│    4 layers concatenated, budget 2500 tokens, prioritized           │
├─────────────────────────────────────────────────────────────────────┤
│ 6. NARRATING           ai/narrator.py   (qwen3.5:9b · JSON)         │
│    → NarrativeResult {narrative, tone}                              │
└─────────────────────────────────────────────────────────────────────┘
      │
      ▼  bot/embeds/narrative_embed.py + PublicEffects footer
Discord embed
      │
      ├─ session.story_bible.log_turn(...)
      ├─ memory.indexer.add_exchange(...)  → ChromaDB
      └─ session.advance_beat_if_ready()   → BeatProgressionEngine
```

Progress is streamed to Discord via `progress_callback` so the user sees
"Interpreting → Validating → Narrating" live. Any phase can short-circuit
(invalid input ⇒ phase 3 error message; ambiguous target ⇒ phase 2
pause + `ClarificationView`).

---

## 4. Combat turn lifecycle

```
Player ATTACK (free text)
   │ action_handler.py acquires session.action_lock
   ▼
ActionPipeline → MechanicsOutcome  (HP applied, conditions, public_effects)
   │ lock released
   ▼
TurnManager.on_action_resolved(session)         (combat_turn_manager.py)
   │ re-acquires session.action_lock  (asyncio.Lock is NOT re-entrant —
   │  release before re-acquire is the explicit pattern)
   ▼
engine.combat.advance_turn(state)
   │ resets ActionBudget; consumes SURPRISED; wraps round → resets reactions
   │ if no eligible combatant → state.is_active=False, state.end_reason set
   ▼
check_combat_end → VICTORY | DEFEAT | FLED | TRUCE | still running
   │
   ├─ still running, NPC turn:
   │     dispatch by tier:
   │       MINION → engine/npc_ai/scripted.decide_minion_action
   │       ELITE  → engine/npc_ai/elite.decide_elite_action
   │       BOSS   → engine/npc_ai/boss_brain.decide_boss_action
   │                  → ai/npc_tactician (LLM) with elite fallback
   │     execute_action_plan → MechanicsOutcome → re-loop
   │
   └─ ended:
         bot.combat_end.finalize_combat(session, reason)
           idempotent (state._finalized flag)
           applies XP per tier (MINION 50 / ELITE 150 / BOSS 500)
           runs check_level_up; purges SURPRISED + CONCENTRATING
           returns CombatEndSummary (survivors, killed, fled, loot, level_ups)
         post combat_end embed, freeze hub buttons.

Off-turn boss legendary actions fire from advance_turn after each PC turn;
phase transitions fire from apply_damage when HP threshold is crossed.
Phase transition narration runs through ai/narrator_phase.py;
PhaseTransitionEvent has consumed: bool so we never re-narrate.
```

---

## 5. Persistence layout

### SQLite (relational source of truth)

11 tables, all under one SQLite file at `data/realmai.db`:

| Table | Purpose |
|---|---|
| `campaigns` | One row per campaign; `combat_state_json` field holds the full `CombatState` Pydantic dump (auto-checkpointed after each turn + at finalize) |
| `player_characters` | Pydantic `Character` JSON column + structured stats |
| `npcs` | NPC sheets with `dialogue_history`, `secrets`, `knowledge`, `aliases` |
| `locations` | Map nodes with `combat_zones`, `combat_triggers`, `state_flags`, `unlocked_exits` |
| `quests` | Active quest list per campaign |
| `story_arcs` | 10-15 beats with `objectives[]`, `villain_stat_block`, completion state |
| `exchanges` | Sliding window source (Layer 2) |
| `summaries` | Auto-generated summaries (Layer 3) |
| `campaign_channels` | Discord channel ↔ campaign mapping |
| `guild_configs` | Per-guild category override |
| `hint_usage` | `/hint` cooldown tracking |

Schema management lives in `db/migrations.py::ensure_schema` (called by
`init_db`): it runs `Base.metadata.create_all()` for missing tables, then adds
any model-defined column an existing table is missing (`ALTER TABLE ADD
COLUMN`, with a safe `DEFAULT` for NOT NULL columns), and records a
`schema_version`. This makes the common forward change — a new column — safe on
an existing DB, which bare `create_all()` could not do. Structural changes
(renames, type changes, backfills) still warrant an explicit migration
sequenced by `schema_version`. No Alembic; `data/` remains dev-only and
disposable (`scripts/reset_dev_data.py`).

### ChromaDB (semantic memory)

`PersistentClient(path="data/chromadb")`. One collection per campaign:
`campaign_<id>`. Indexed by `memory/indexer.py` on every persisted exchange,
queried by `memory/semantic.py` during context assembly.

### Logs (not persisted state)

- `logs/realm_YYYYMMDD_HHMMSS.log` — structured Python logs per process
- `logs/campaigns/<campaign_id>.md` — append-only story bible
- `logs/campaigns/<campaign_id>_facts.md` — locked facts ledger
- `logs/narrator_failures/` — raw LLM dumps when JSON parse fails
- `logs/beat_progression.jsonl` — telemetry for tuning the judge

`logs/` is gitignored — these are runtime artifacts.

---

## 6. Design invariants

The codebase is built around five non-negotiable rules. Each has CI-friendly
enforcement (test or grep) noted in parentheses.

1. **No LLM in `engine/`** — every dice roll, damage calculation, validator
   check is pure Python; `engine/` has **zero** `from ai` imports (the shared
   contracts live in `engine/contracts.py`, the boss tactician is an injected
   Protocol). (Enforcement: `tests/engine/test_no_ai_imports.py`, an AST scan.)

2. **JSON mode always** — every Ollama call sets `format: "json"` (handled
   centrally in `ai/client.py`). Native tool calling is never used.

3. **Pydantic v2 everywhere in the domain** — no raw dicts cross module
   boundaries. `db/models.py` is the only place SQLAlchemy types live; mappers
   convert at the seam.

4. **One asyncio lock per session** — `session.action_lock` ensures a single
   pipeline runs per campaign at a time. `asyncio.Lock` is **not
   re-entrant**: the `TurnManager` releases before re-acquiring for NPC
   turns.

5. **Mutations go through mappers + repositories** — never `session.add(row)`
   from a cog. The `upsert()` repo methods are explicit get-then-write,
   not exception-driven (avoids masking real SQLAlchemy errors).

Plus one safety invariant specific to combat:

6. **`finalize_combat` is idempotent** — `CombatState._finalized` (PrivateAttr)
   prevents double-XP / double-narration. `_resolve_flee`, `TurnManager._finalize`,
   and any retry path can call it safely.

---

## 7. Performance & memory budget

- **LLM models**: never loaded simultaneously. Ollama is asked to load a
  model on demand and the older model is unloaded. On an M3 Pro 18GB this
  leaves ~10-12GB headroom for the bot + SQLite + ChromaDB.
- **Per-LLM-call prompt**: hard-capped at ~2500 tokens by the context
  assembler. Average measured ~1800. Layer truncation order:
  Layer 4 → Layer 3 → Layer 2 → Layer 1 (never).
- **Token estimator**: `max(chars/3.5, words×1.5)` — over-estimates
  deliberately. No tiktoken dependency.
- **Combat turn loop**: per-turn LLM cost is `1× interpreter (4b)`
  + `1× narrator (9b)` for PC actions, `0× LLM` for minion turns,
  `0–1× tactician (4b)` for boss turns. Most turns cost 2 calls.
- **Beat advancement**: `BeatProgressionEngine.evaluate()` returns
  `{ADVANCE, STAY, NEEDS_JUDGE}`. The judge (`beat_judge.py`, 4b model)
  fires only on `NEEDS_JUDGE` — empirically ~20% of turns.

---

## 8. Extending the engine

| To add | Touch | Don't touch |
|---|---|---|
| New spell | `engine/spells.py` catalogue + test | Anywhere in `ai/` |
| New condition | `engine/conditions.py` + tests + advantage/disadvantage hook table | Validators (auto-picked up) |
| New `ActionType` | `engine/validators.py::ActionType` + validator branch + `ai/prompts/system_interpreter.txt` + `ai/models.py::InterpretedAction` + pipeline `_resolve_*` | `ai/client.py` |
| New objective gate | `engine/objective_matchers.py` + recipe in `ai/objective_recipes.py` + `world/story_arc.py::GateKind` | Beat judge prompt (it reads `judge_rubric` per beat) |
| New NPC archetype | `engine/npc_library.py` builder entry | Tactician prompt (it's tier-agnostic) |
| New slash command | `bot/cogs/<existing-or-new>.py` (one cog per domain) | `bot/bot.py` (cogs auto-load by filename) |
| New combat action button | `bot/views/combat_action_view.py` + select view in `bot/views/` + `ActionPipeline.process_interpreted_action` route | Free-text path (already handled by interpreter) |
| New persisted entity | `world/<entity>.py` (Pydantic) + `db/models.py` (SQLAlchemy) + `db/mappers.py` + `db/repositories/<entity>_repo.py` — new tables **and** new columns are picked up automatically on startup by `migrations.ensure_schema` | The pipeline (read through repos, mutate through `persist_session()`) |

---

## 9. Testing topology

- ~2 200 unit tests across `tests/{engine, ai, bot, memory, db, world}/`
- 13 end-to-end scenario files (60+ tests) via
  `tests/scenarios/scenario_runner.py` (campaign lifecycle, combat e2e,
  character creation lobby, beat progression, persistence integrity, edge
  cases…) — run with `uv run pytest tests/scenarios`
- **Autonomous playthrough simulator** (`tests/simulation/`) — drives an LLM
  `AutonomousAgent` through a full campaign via a headless `GameDriver` and
  flags narrative-coherence violations. Mock-LLM mode for fast deterministic
  smoke runs, real-Ollama mode for fidelity. Run:
  `uv run python -m tests.simulation [--mock-llm] [--max-turns N]`
- Live Discord smoke testing via `mcp_discord/` driving a tester bot
- Coverage targets: engine ≥98%, matchers ≥95%, everywhere else best-effort
- Quality gates: `uv run pytest`, `uv run ruff check .`, `uv run mypy .`

See [`docs/internal/TESTING.md`](docs/internal/TESTING.md) for the full
testing strategy.

---

## 10. See also

| Doc | Scope |
|---|---|
| [`docs/internal/ARCHITECTURE.md`](docs/internal/ARCHITECTURE.md) | Deep architecture reference (FR), kept in sync with the code |
| [`docs/internal/ACTION_PIPELINE.md`](docs/internal/ACTION_PIPELINE.md) | The 6-phase pipeline phase by phase |
| [`docs/internal/COMBAT_SYSTEM.md`](docs/internal/COMBAT_SYSTEM.md) | Combat — modules, data models, pipeline, API |
| [`docs/internal/CAMPAIGN_LIFECYCLE.md`](docs/internal/CAMPAIGN_LIFECYCLE.md) | `/start_campaign` → onboarding → `/end_campaign` |
| [`docs/internal/NARRATIVE_COHERENCE.md`](docs/internal/NARRATIVE_COHERENCE.md) | Locked canon, NPC disposition, Story Director |
| [`docs/internal/MEMORY_SYSTEM.md`](docs/internal/MEMORY_SYSTEM.md) | The 4 memory layers in detail |
| [`docs/internal/GAME_ENGINE.md`](docs/internal/GAME_ENGINE.md) | Each engine module, function by function |
| [`docs/internal/AI_LAYER.md`](docs/internal/AI_LAYER.md) | Each LLM service, prompt, and retry policy |
| [`docs/internal/DISCORD_BOT.md`](docs/internal/DISCORD_BOT.md) | Cogs, views, embeds, sessions, lobby |
| [`docs/internal/DATABASE.md`](docs/internal/DATABASE.md) | Tables, repos, mappers, migrations |
| [`docs/internal/STATE.md`](docs/internal/STATE.md) | What is implemented vs. pending |
| [`docs/internal/ISSUES.md`](docs/internal/ISSUES.md) | Known bugs and tech debt |
| [`tasks/lessons.md`](tasks/lessons.md) | Lessons accumulated across refactors |
