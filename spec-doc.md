# RealmAI-Engine — Full Technical Specification

-----

# 1. OVERVIEW

Discord bot serving as an AI Game Master for tabletop RPG sessions with friends. A deterministic Python engine handles ALL game mechanics (dice, combat, inventory, rules). Local open-source LLMs (Ollama) handle narration ONLY.

**Core principle: the LLM narrates, the code arbitrates.**

-----

# 2. STRATEGIC GOAL

Open source portfolio project targeting GAFAM/MANGO recruitment. Profile: Python/GenAI developer, 2-3 years XP (PwC consulting).

Skills demonstrated: structured outputs, RAG, multi-agent architecture, async programming, Pydantic, SQLAlchemy, pytest, GitHub Actions, Discord bot UX, real-time multiplayer.

Why this project: real personal need (friends want to play RPGs without a human DM), 100% aligned with Python/GenAI skillset, hot topics (AI gaming, agents), no open-source competitor combines deterministic engine + LLM narration + Discord multiplayer.

-----

# 3. COMPETITIVE LANDSCAPE

## Open source projects

AI-DM, TD-LLM-DND, AIDM, dd-chatgpt-dm, aidnd: none combines deterministic engine + decoupled LLM narration + Discord multiplayer. Common weaknesses: LLM decides everything, memory loss in long sessions, no real mechanical rules.

## RealmAI-Engine positioning

Unique on the combination: deterministic engine + LLM narration + Discord multiplayer.

-----

# 4. TECH STACK

|Component      |Technology                           |
|---------------|-------------------------------------|
|Discord bot    |discord.py 2.4+                      |
|Data models    |Pydantic v2                          |
|Persistence    |SQLAlchemy + SQLite                  |
|Semantic memory|ChromaDB                             |
|LLM inference  |Ollama (local, OpenAI-compatible API)|
|Tests          |pytest                               |
|Linting        |ruff                                 |
|Typing         |mypy                                 |
|CI/CD          |GitHub Actions                       |

No LangChain — direct Ollama API (OpenAI-compatible) to demonstrate raw API mastery. No MCP — Discord is the sole interface; MCP would add complexity without serving any real user.

-----

# 5. LOCAL LLM MODELS

## Hardware constraints

Mac M3 Pro, 18GB unified memory, ~150 GB/s memory bandwidth. macOS uses ~3-4GB. Model budget: ~10-12GB max (leaving room for bot, SQLite, ChromaDB).

## Runtime: Ollama

- `brew install ollama`
- OpenAI-compatible API at `localhost:11434/v1`
- Native MLX backend for Apple Silicon (shipped late March 2026)
- Automatic Metal GPU acceleration

## Chosen models (Qwen 3.5 family, released January 2026)

Models are NEVER loaded simultaneously — load/unload per active role.

### Narrator (narrative quality)

- **Qwen 3.5 9B** (Q4_K_M) — 6.6GB, ~25-35 tok/s on M3 Pro
- Strong instruction following, good reasoning, /think mode available
- Apache 2.0 license
- `ollama pull qwen3.5:9b`

### Interpreter (fast text→JSON parsing)

- **Qwen 3.5 4B** — ~3GB, ~50-70 tok/s on M3 Pro
- Sufficient for parsing “I swing my axe at the goblin” → JSON
- `ollama pull qwen3.5:4b`

### Story Director / Summarizer

- Reuses Narrator model (Qwen 3.5 9B)
- Runs in background while players think
- Latency-tolerant

## Tool calling warning

Ollama native tool calling is broken with Qwen 3.5 (confirmed bugs in GitHub issues #14493 and #14745: wrong format pipeline, unclosed XML tags). Use **JSON mode** (`response_format: json_object`) instead — works correctly and is architecturally better for this project anyway.

## Code integration

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

# Narrator (heavy model, quality)
narrator_response = client.chat.completions.create(
    model="qwen3.5:9b",
    messages=[{"role": "system", "content": NARRATOR_PROMPT}, ...],
    temperature=0.8,
)

# Interpreter (light model, fast, JSON)
interpreter_response = client.chat.completions.create(
    model="qwen3.5:4b",
    messages=[{"role": "system", "content": INTERPRETER_PROMPT}, ...],
    temperature=0.1,
    response_format={"type": "json_object"},
)
```

## Local-only inference

All LLM inference runs locally via Ollama. No cloud API fallback — privacy, latency, and zero cost per token.

## Alternatives to watch

- **DeepSeek-V3.2**: integrates reasoning into tool use, good plan B if Ollama/Qwen bugs persist.
- **Qwen 3.5 35B-A3B** (MoE, 3B active params): ultra fast but requires 32GB+.

-----

# 6. ARCHITECTURE

## 6.1 Processing pipeline

```
Player (Discord)
  │ free text or slash command
  ▼
ACTION INTERPRETER (Qwen 3.5 4B, fast)
  │ text → structured JSON {"action","target","weapon"...}
  │ Only point where an LLM "decides" — but if interpretation
  │ is wrong, the Validator rejects it, player rephrases
  ▼
ACTION VALIDATOR (pure Python, no LLM)
  │ Checks: weapon owned? correct turn? target in range? player alive?
  │ If invalid → Discord error (Narrator never called)
  ▼
GAME ENGINE (pure Python, no LLM)
  │ Resolves: dice rolls, damage, effects, XP, loot
  │ Updates WorldState in DB
  │ Produces structured ActionResult
  ▼
CONTEXT ASSEMBLER
  │ Builds prompt from 4 memory layers
  │ ~1500-2500 tokens per call
  ▼
NARRATOR (Qwen 3.5 9B, quality)
  │ Receives ActionResult + context → narrative text
  │ Decides NOTHING mechanically
  ▼
DISCORD (embed: narrative + raw stats)

Background (~20 interactions):
STORY DIRECTOR checks coherence, rekindles stakes
```

## 6.2 File structure

```
realmAI-engine/
├── engine/                  # Pure Python game logic (NO LLM EVER)
│   ├── dice.py              # Dice system
│   ├── character.py         # Classes, races, stats, levels
│   ├── combat.py            # Initiative, attacks, damage, turns
│   ├── inventory.py         # Items, equipment, weight
│   ├── spells.py            # Spells and effects
│   ├── conditions.py        # Status conditions
│   ├── rules.py             # Simplified SRD 5e rules
│   └── validators.py        # Action validation
├── ai/                      # GenAI layer
│   ├── narrator.py
│   ├── interpreter.py
│   ├── npc_agent.py
│   ├── quest_generator.py
│   ├── world_generator.py
│   ├── story_director.py
│   └── prompts/
├── memory/                  # 4-layer memory system
│   ├── state.py
│   ├── sliding_window.py
│   ├── summarizer.py
│   ├── semantic.py
│   └── context_assembler.py
├── world/                   # World state models
│   ├── world_state.py
│   ├── facts.py
│   ├── npcs.py
│   ├── locations.py
│   ├── quests.py
│   └── factions.py
├── bot/                     # Discord bot (see Phase 3 section)
│   ├── bot.py               # Bot setup, cog loading, intents
│   ├── config.py            # GuildConfig (category per guild)
│   ├── cogs/                # Slash commands by domain
│   ├── views/               # Combat buttons + select menus
│   ├── embeds/              # Embed builders
│   └── utils/               # Channel manager
├── db/
│   ├── models.py
│   └── database.py
├── tests/
├── tasks/
├── .github/workflows/ci.yml
├── CLAUDE.md
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
└── LICENSE (MIT)
```

-----

# 7. MEMORY SYSTEM (4 LAYERS)

**Layer 1 — Structured state (SQLite)**: source of truth. Character sheets, inventories, positions, combat state, quests, NPCs, map. Injected as structured summary (~300-500 tokens).

**Layer 2 — Sliding window**: last 10-12 narrative exchanges (~500-800 tokens). Old exchanges are summarized and pushed to layer 3.

**Layer 3 — Compressed summaries**: auto-generated every ~20 interactions. Last 3-4 injected (~300-500 tokens).

**Layer 4 — Semantic RAG (ChromaDB)**: world lore, detailed NPC sheets, past events. Queried by semantic similarity only when relevant (~200-400 tokens).

**Total assembled prompt: ~1500-2500 tokens per LLM call.**

-----

# 8. GAME INTEGRITY & ANTI-CHEAT

The LLM is a blind narrator. It never decides dice rolls, damage, items, or combat outcomes.

**Pipeline**: Player action → Interpreter parses to JSON → Validator checks legality (weapon owned, correct turn, target in range, player alive) → if invalid: Discord error, Narrator never called → if valid: Engine resolves (rolls, damage, effects, DB update) → Narrator receives ActionResult → produces narrative text → Discord displays narrative AND raw mechanics.

**Narrator system prompt rules (non-negotiable)**: never decide dice results, never modify HP/inventory/stats, never spawn items not in game state.

-----

# 9. NARRATIVE COHERENCE

**Locked facts**: established world facts the LLM cannot contradict. Managed in Python/DB.
**NPC registry**: status (alive/dead/missing), disposition per player (-100 to +100), secrets, personality prompt.
**Story Director**: periodic agent (~20 interactions) checking contradictions, stale quests, abandoned threads.
**Post-generation validation**: optional second LLM call (light model) to detect contradictions.

-----

# 10. SESSION FORMAT

**Pre-session**: /create_character (Discord modal), LLM-generated backstory.
**Launch**: /start_campaign [theme] or /resume.
**In-game**: free text + slash commands + Discord buttons for combat.
**End**: /save + auto-generated session summary. Resume with /resume.

-----

# 11. DEVELOPMENT PHASES

## Phase 1 — Game engine without AI [CURRENT]

Build engine/ with full test coverage. Playable in terminal. No LLM, no Discord.
Order: dice → character → inventory → spells → conditions → combat → validators

## Phase 2 — AI layer

Interpreter, Narrator, 4-layer memory, quest/NPC generation, Story Director. Ollama integration.

## Phase 3 — Discord bot + multiplayer

> **Design spec:** `docs/superpowers/specs/2026-04-05-discord-bot-ux-design.md`

### Architecture: Cogs by domain

```
bot/
├── bot.py                  # Bot setup, cog loading, intents, on_ready
├── config.py               # GuildConfig Pydantic model (category per guild)
├── cogs/
│   ├── session.py          # /start_campaign, /resume, /save, /end_campaign, /settings
│   ├── character.py        # /create_character, /character, /level_up
│   ├── inventory.py        # /inventory, /equip, /unequip, /use_item
│   ├── combat.py           # Combat flow: posts embeds, attaches button views
│   ├── exploration.py      # /look, /search, /talk, /move (requires AI layer)
│   └── rolls.py            # /roll (free dice expression)
├── views/
│   ├── combat_view.py      # Buttons: Attack, Cast Spell, Defend, Flee
│   ├── target_select.py    # Select menu for target choice
│   └── spell_select.py     # Select menu for spell choice
├── embeds/
│   ├── character_embed.py  # Character sheet embed
│   ├── inventory_embed.py  # Inventory embed (items, equipped, weight, gold)
│   ├── combat_embed.py     # Combat status (initiative, HP bars, conditions)
│   └── narrative_embed.py  # Narrative + raw mechanics dual embed
└── utils/
    └── channel_manager.py  # Channel creation, permissions, archival
```

Cogs import engine functions directly — no intermediate abstraction layer.

### Channel Management

- **Dedicated channel per campaign:** Bot creates a text channel at `/start_campaign` in a configurable Discord category (default: "RealmAI Sessions").
- **Player invites:** The player who starts tags other players; only tagged players + bot have channel access (permission overrides).
- **Category configurable:** `/settings category:"My Category"` (requires `manage_channels`). Stored per guild in SQLite.
- **Archival:** `/end_campaign` moves the channel to "RealmAI Archives" category (read-only). History remains consultable.
- **No human GM:** The bot AI is the sole Game Master. All players have equal permissions.

### Slash Commands

| Command | Cog | Parameters | Ephemeral | `public:` flag |
|---------|-----|-----------|-----------|----------------|
| `/start_campaign` | session | `theme: str`, `players: str` (mentions) | No | — |
| `/resume` | session | — | No | — |
| `/save` | session | — | Yes | — |
| `/end_campaign` | session | — | No | — |
| `/settings` | session | `category: str` | Yes | — |
| `/create_character` | character | — (opens modal) | Yes | — |
| `/character` | character | `public: bool = False` | Yes | Yes |
| `/level_up` | character | `public: bool = False` | Yes | Yes |
| `/inventory` | inventory | `public: bool = False` | Yes | Yes |
| `/equip` | inventory | `item: str`, `slot: str` | Yes | — |
| `/unequip` | inventory | `slot: str` | Yes | — |
| `/use_item` | inventory | `item: str` | Yes | — |
| `/roll` | rolls | `expression: str` | No (always public) | — |
| `/look` | exploration | — | No | — |
| `/search` | exploration | `target: str` | No | — |
| `/talk` | exploration | `npc: str` | No | — |
| `/move` | exploration | `direction: str` | No | — |

**Visibility rule:** Personal commands (character, inventory) are ephemeral by default. The optional `public:` flag lets players share with the group. Session commands and rolls are always public.

### Combat UX (Buttons + Select Menus)

- **Trigger:** Engine detects combat → combat cog posts initiative embed with `CombatView`.
- **Turn flow:** Bot mentions active player → 4 buttons (Attack / Cast Spell / Defend / Flee) → player clicks → select menu for target/spell → engine resolves → narrative + mechanics embed → next turn.
- **Interaction guard:** Only the active player can use the buttons (check `interaction.user.id`).
- **Timeout:** 2 min reminder, 5 min auto-Defend.

### Required Bot Permissions

`manage_channels`, `manage_roles`, `send_messages`, `embed_links`, `use_external_emojis`

## Phase 4 — Polish + ship

README with GIFs + architecture diagram, GitHub Actions CI/CD, real play sessions (3+ with friends), blog post / LinkedIn.

-----

# 12. CLAUDE CODE / COWORK SKILLS

3 skills to create via skill-creator:

**`game-engine`**: Pydantic v2 conventions, simplified SRD 5e rules, ActionValidator pattern, ActionResult structure, pytest conventions. Triggers on: combat, dice, inventory, character, spells, conditions, HP, AC, engine/.

**`ai-narrator`**: system prompt templates, Ollama API pattern, JSON structured outputs, 4-layer memory architecture, Context Assembler, multi-model config. Triggers on: narration, prompts, NPCs, quests, LLM memory, RAG, ChromaDB, Ollama.

**`discord-bot-rpg`**: discord.py 2.4+ patterns (slash commands, Views, Modals, Embeds), Cogs, async interactions, embed formatting, multi-player sessions. Triggers on: slash commands, Discord buttons, embeds, modals, discord.py.

-----

# 13. COMPLEMENTARY ACTIONS

**Open source contributions**: LiteLLM, RAGAS, Langfuse, Instructor (good first issue labels).
**PwC resume**: rewrite with Action + Metric + Result formula.
**LinkedIn**: technical posts, gameplay video, learning shares.

-----

# 14. RISKS & MITIGATIONS

|Risk                          |Mitigation                                                     |
|------------------------------|---------------------------------------------------------------|
|Local LLM quality insufficient|Try larger quantization or alternative local models (DeepSeek-V3.2, Qwen 3.5 35B-A3B on 32GB+). Tune prompts and temperature.|
|Ollama tool calling bugs      |Use JSON mode, not native tool calling.                        |
|Incoherent narratives         |World State Contract + Story Director + post-gen validation    |
|Slow response times           |4B model for Interpreter, streaming, background pre-generation |
|Scope too ambitious           |Phased development. Phase 1 is playable standalone.            |
|18GB memory constraint        |Models never loaded simultaneously. ~10-12GB max budget.       |
|D&D licensing                 |Use SRD 5e only (open license). Never use “Dungeons & Dragons”.|

-----

# 15. SUCCESS METRICS

- [ ] Game engine functional, tests >80% coverage
- [ ] Discord bot playable multiplayer (3-4 friends)
- [ ] At least 3 complete sessions played
- [ ] GitHub README with GIFs, architecture diagram, quickstart
- [ ] CI/CD working (GitHub Actions)
- [ ] At least 1 LinkedIn post with engagement
- [ ] At least 1 open source contribution merged