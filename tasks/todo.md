# Phase 1 — Game Engine (No AI, No Discord) ✅ COMPLETE

Build `engine/` with full test coverage. Playable in terminal.

## Module Order

- [x] **dice.py** — Dice expressions ("2d6+3") → DiceResult
- [x] **character.py** — Classes, races, ability scores, levels, HP, AC
- [x] **inventory.py** — Items, equipment, weight, attunement
- [x] **spells.py** — 21 SRD spells, SpellcasterState, slots (full/half caster), cantrip scaling
- [x] **conditions.py** — 15 SRD conditions, exhaustion stacking, advantage/disadvantage queries
- [x] **combat.py** — Initiative, attacks (finesse/ranged), spells (saves/damage/healing), death saves, turn management
- [x] **validators.py** — Action legality (attack, cast spell, defend, flee, use item)

## Quality Gates

- [x] pytest: **445 tests passing**, **98% coverage** global sur engine/
- [x] ruff check: clean
- [x] mypy: clean (0 issues, 9 fichiers)
- [x] Conventional commits per module

## Milestone

- [x] Terminal REPL (`main.py`) — solo combat: 3 presets (Fighter/Wizard/Rogue) vs Goblin + Skeleton
- Lancez avec `uv run python main.py`

## Post-review fixes (2026-04-05)

- [x] C1: `SpellCastResult.success` → `target_failed_save` (sémantique clarifiée)
- [x] C2: `_double_dice` robuste pour expressions avec modifier ("3d4+3")
- [x] I1: Ajout `FORCE` et `THUNDER` à DamageType, Magic Missile et Thunderwave corrigés
- [x] I4: `advance_turn` refactoré — un seul mécanisme d'incrémentation de round
- [x] I5: `validate_cast_spell` exige un target pour les sorts non-Self qui font des dégâts/conditions
- [x] I6: Upcasting via `higher_level_dice` implémenté dans `resolve_spell`

## Known simplifications (tracked, non-bloquant pour Phase 2)

- [ ] S2: Defend n'a pas d'effet mécanique (devrait donner disadvantage aux attaquants jusqu'au prochain tour)
- [ ] S4: Healing spells n'ajoutent pas le spellcasting ability modifier au healing
- [ ] S5: `Spell.condition_applied` est un `str` au lieu de `ConditionType | None` (fragile)
- [ ] PRONE condition simplifié (devrait dépendre de la distance pour advantage/disadvantage)

---

# Phase 2 — AI Layer [IN PROGRESS]

> Unblocked: Phase 1 complete

## Phase 2a — World Models & DB Persistence ✅ COMPLETE

> Design spec: `docs/superpowers/specs/2026-04-05-world-db-design.md`

- [x] **world/npc.py** — NPC model (Pydantic), NPCDisposition enum
- [x] **world/location.py** — Location model (connections, NPCs, items)
- [x] **world/quest.py** — Quest, QuestObjective, QuestStatus enum
- [x] **world/campaign.py** — Campaign model (UUID, datetime, interaction_count)
- [x] **db/database.py** — SQLAlchemy engine, session factory, init_db(), FK enforcement
- [x] **db/models.py** — CampaignRow, NPCRow, LocationRow, QuestRow (JSON columns, FK cascade, unique constraints)
- [x] **db/mappers.py** — 8 domain ↔ DB conversion functions
- [x] **db/repositories/** — CampaignRepository, NPCRepository, LocationRepository, QuestRepository

### Quality Gates

- [x] pytest: **517 tests passing** (445 engine + 72 new), **98% coverage** on world/ + db/
- [x] ruff check: clean
- [x] mypy: clean (0 issues, 14 fichiers)
- [x] Smoke test: save → reload → verify equality

## Phase 2b — Memory System ✅ COMPLETE

> Design spec: `docs/superpowers/specs/2026-04-05-memory-system-design.md`

- [x] **memory/models.py** — Pydantic models: GameStateSummary, NarrativeExchange, CompressedSummary, SemanticDocument, ContextBudget
- [x] **memory/token_utils.py** — Token estimation (words × 1.3) and truncation
- [x] **memory/state.py** — Layer 1: structured state from SQLite + in-memory combat/character
- [x] **memory/sliding_window.py** — Layer 2: last 12 exchanges via ExchangeRepository
- [x] **memory/summarizer.py** — Layer 3: Ollama (qwen3.5:9b) summaries every ~20 interactions
- [x] **memory/semantic.py** — Layer 4: ChromaDB RAG (all-MiniLM-L6-v2) for lore/NPC memory
- [x] **memory/context_assembler.py** — Builds ~1500-2500 token prompt from 4 layers
- [x] **db/models.py** — ExchangeRow, SummaryRow tables
- [x] **db/repositories/** — ExchangeRepository, SummaryRepository

### Quality Gates

- [x] pytest: **593 tests passing** (after post-review fixes), **91% coverage** on memory/
- [x] ruff check: clean
- [x] mypy: clean (0 issues on source; pre-existing test fixture errors unrelated)

### Post-review fixes (2026-04-05)

- [x] R1: `_assemble_prompt` truncation final clamp added — ceil() rounding no longer causes over-budget output
- [x] R2: `add_documents` validates all docs share same campaign_id (raises ValueError)
- [x] R3: `summarizer.py` split redundant `except (json.JSONDecodeError, Exception)` into two clauses
- [x] R4: Added `sample_exchange` and `sample_summary` fixtures to `tests/conftest.py`
- [x] R5: `state.py` bounds-check before `combatants[current_turn_index]` (uses `% len` guard)
- [x] R6: `semantic.py query()` bare except now logs with `logger.debug`
- [x] R7: `ContextBudget` model_validator enforces `total_max >= layer1_max`
- [x] R8: New test `test_truncation_clamp_enforces_budget` covers actual truncation path

## Phase 2c — AI Core [NEXT]

- [ ] **ai/interpreter.py** — Qwen 3.5 4B, text → structured JSON (action, target, weapon)
- [ ] **ai/narrator.py** — Qwen 3.5 9B, ActionResult → narrative text
- [ ] **ai/story_director.py** — Periodic coherence check (~20 interactions)
- [ ] **ai/npc_agent.py** — NPC dialogue and personality
- [ ] **ai/quest_generator.py** — Dynamic quest generation
- [ ] **ai/world_generator.py** — World/location generation
- [ ] **ai/prompts/** — System prompt templates for all LLM roles

---

# Phase 3 — Discord Bot + Multiplayer

> Blocked by: Phase 2 completion (some cogs can scaffold earlier, combat/exploration need engine + AI)
> Design spec: `docs/superpowers/specs/2026-04-05-discord-bot-ux-design.md`

## Phase 3a — Bot Foundation

- [ ] **bot/bot.py** — Bot setup, cog loading, intents, on_ready
- [ ] **bot/config.py** — GuildConfig Pydantic model (category per guild, stored in SQLite)

## Phase 3b — Channel Manager

- [ ] **bot/utils/channel_manager.py** — create_session_channel, archive_channel, get_or_create_category
  - Creates channel at `/start_campaign` with permission overrides (tagged players + bot only)
  - Configurable category per guild (default: "RealmAI Sessions")
  - Archives to "RealmAI Archives" (read-only) at `/end_campaign`

## Phase 3c — Cogs (slash commands)

- [ ] **bot/cogs/session.py** — `/start_campaign`, `/resume`, `/save`, `/end_campaign`, `/settings`
- [ ] **bot/cogs/character.py** — `/create_character` (modal), `/character`, `/level_up`
- [ ] **bot/cogs/inventory.py** — `/inventory`, `/equip`, `/unequip`, `/use_item`
- [ ] **bot/cogs/rolls.py** — `/roll` (free dice expression, always public)
- [ ] **bot/cogs/combat.py** — Combat flow with buttons + select menus (blocked by engine/combat.py)
- [ ] **bot/cogs/exploration.py** — `/look`, `/search`, `/talk`, `/move` (blocked by AI layer)

## Phase 3d — Views (combat interactions)

- [ ] **bot/views/combat_view.py** — 4 buttons: Attack, Cast Spell, Defend, Flee
- [ ] **bot/views/target_select.py** — Select menu for target choice
- [ ] **bot/views/spell_select.py** — Select menu for spell choice

## Phase 3e — Embeds

- [ ] **bot/embeds/character_embed.py** — Character sheet (abilities, HP, AC, XP)
- [ ] **bot/embeds/inventory_embed.py** — Inventory (items, equipped, attuned, weight, gold)
- [ ] **bot/embeds/combat_embed.py** — Combat status (initiative order, HP bars, conditions)
- [ ] **bot/embeds/narrative_embed.py** — Narrative + raw mechanics dual panel

## Key Design Decisions

- **Ephemeral by default** for personal commands (/character, /inventory) with optional `public:` flag
- **No human GM** — bot AI is the sole Game Master, all players equal
- **Buttons + select menus** for combat (no slash commands during combat turns)
- **Turn timeout:** 2 min reminder, 5 min auto-Defend
- **Bot permissions:** manage_channels, manage_roles, send_messages, embed_links, use_external_emojis

---

# Phase 4 — MCP Server + Polish

- [ ] **mcp_server/server.py** — MCP server setup
- [ ] **mcp_server/tools.py** — roll_dice, attack, cast_spell, get_inventory, search_lore, generate_npc, advance_quest
- [ ] **mcp_server/resources.py** — character_sheets/*, world_state, combat_log, quest_journal
- [ ] **mcp_server/prompts.py** — narrate_combat_result, describe_new_area, npc_dialogue
- [ ] README with GIFs and architecture diagram
- [ ] GitHub Actions CI/CD
- [ ] CONTRIBUTING.md
- [ ] Blog post / LinkedIn content
