# RealmAI Engine

An AI-powered RPG Game Master that runs as a Discord bot. A deterministic Python engine handles all game mechanics (dice, combat, inventory, rules). Local LLMs via Ollama handle narration only. The engine is also exposed as an MCP server.

**The LLM narrates. The code arbitrates. No exceptions.**

## Why This Project

Most AI D&D projects let the LLM decide everything — dice rolls, damage, loot. That leads to inconsistent rules, easy exploits, and broken immersion. RealmAI Engine takes a different approach: a strict Python engine enforces all mechanics, and the LLM is a blind narrator that describes what happened. No other open-source project combines a deterministic engine + LLM narration + MCP server + Discord multiplayer.

## Architecture

```
Player (Discord)
  │ free text or slash command
  ▼
ACTION INTERPRETER (Qwen 3.5 4B, fast)
  │ text → structured JSON
  ▼
ACTION VALIDATOR (pure Python)
  │ checks legality (weapon owned? correct turn? target in range?)
  ▼
GAME ENGINE (pure Python)
  │ resolves dice, damage, effects, XP, loot
  │ updates world state in DB
  ▼
CONTEXT ASSEMBLER
  │ builds prompt from 4 memory layers (~1500-2500 tokens)
  ▼
NARRATOR (Qwen 3.5 9B, quality)
  │ receives ActionResult → narrative text
  ▼
Discord (embed: narrative + raw stats side by side)

Background: STORY DIRECTOR every ~20 interactions
```

### Memory System (4 layers)

| Layer | Source | Purpose |
|-------|--------|---------|
| Structured state | SQLite | Source of truth — HP, inventory, quests, positions |
| Sliding window | Last 10-12 exchanges | Short-term continuity |
| Compressed summaries | Auto-generated every ~20 turns | Medium-term recall |
| Semantic RAG | ChromaDB | Lore, NPC sheets, past events (queried on relevance) |

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.12+ |
| Package manager | uv |
| Discord bot | discord.py 2.4+ |
| Data models | Pydantic v2 |
| Persistence | SQLAlchemy + SQLite |
| Semantic memory | ChromaDB |
| LLM inference | Ollama (local, OpenAI-compatible API) |
| MCP server | mcp Python SDK |
| Tests | pytest |
| Linting | ruff |
| Type checking | mypy |

### LLM Models (Ollama)

- **Narrator**: `qwen3.5:9b` (6.6 GB, ~25-35 tok/s on M3 Pro) — immersive narrative
- **Interpreter**: `qwen3.5:4b` (~3 GB, ~50-70 tok/s) — fast text-to-JSON parsing

Models are never loaded simultaneously to stay within memory budget.

## Project Structure

```
realmAI-engine/
├── engine/           # Pure Python game logic (NO LLM EVER)
├── ai/               # GenAI layer (narrator, interpreter, story director)
├── memory/           # 4-layer memory system
├── world/            # World state Pydantic models
├── bot/              # Discord bot (cogs, action pipeline, views, embeds, launcher)
├── mcp_discord/      # MCP server for Discord live-testing automation
├── db/               # SQLAlchemy models, mappers, repositories
├── docs/
│   ├── internal/     # Up-to-date technical docs (architecture, flows, state, issues)
│   └── superpowers/  # Historical design plans & specs
└── tests/            # pytest unit tests + ScenarioRunner end-to-end scenarios
```

## Documentation

The most up-to-date description of what's actually in the code lives in [docs/internal/](docs/internal/README.md):

- [ARCHITECTURE.md](docs/internal/ARCHITECTURE.md) — layered architecture, data flow, stack
- [CAMPAIGN_LIFECYCLE.md](docs/internal/CAMPAIGN_LIFECYCLE.md) — `/start_campaign` → onboarding → save/resume → `/end_campaign`
- [ACTION_PIPELINE.md](docs/internal/ACTION_PIPELINE.md) — the 6-phase pipeline turning free text into narration
- [NARRATIVE_COHERENCE.md](docs/internal/NARRATIVE_COHERENCE.md) — locked canon, NPC disposition, story arc beats, Story Director
- [MEMORY_SYSTEM.md](docs/internal/MEMORY_SYSTEM.md) — 4-layer context assembly
- [GAME_ENGINE.md](docs/internal/GAME_ENGINE.md) — deterministic rules engine module by module
- [AI_LAYER.md](docs/internal/AI_LAYER.md) — Ollama services, prompts, retry logic
- [DISCORD_BOT.md](docs/internal/DISCORD_BOT.md) — cogs, views, embeds, session management
- [DATABASE.md](docs/internal/DATABASE.md) — SQLAlchemy schema + repositories
- [TESTING.md](docs/internal/TESTING.md) — pytest, ScenarioRunner, MCP Discord
- [STATE.md](docs/internal/STATE.md) — what's implemented vs pending
- [ISSUES.md](docs/internal/ISSUES.md) — known bugs, anomalies, improvement backlog

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Ollama](https://ollama.com/) (local LLM inference)

### Setup

```bash
# Clone the repo
git clone https://github.com/CocoLng/RealmAI-Engine.git
cd RealmAI-Engine

# Install dependencies
uv sync

# Pull the LLM models
ollama pull qwen3.5:9b
ollama pull qwen3.5:4b

# Run tests
uv run pytest

# Run linting & type checks
uv run ruff check .
uv run mypy .
```


## Design Principles

- **LLM never decides mechanics** — dice rolls, damage, loot are all deterministic Python
- **Pydantic everywhere** — all data models use Pydantic v2 with strict types, no raw dicts
- **Structured outputs** — LLM responses use `response_format={"type": "json_object"}`, never regex parsing
- **Anti-cheat by design** — ActionValidator checks every action before the engine processes it; Discord shows both narrative and raw mechanics
