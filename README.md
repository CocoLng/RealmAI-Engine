# RealmAI Engine

An AI-powered RPG Game Master that runs as a Discord bot. A deterministic
Python engine handles all game mechanics (dice, combat, inventory, rules).
Local LLMs via Ollama handle narration only. The engine is also exposed as
an MCP server for live-testing the bot from Claude Code.

**The LLM narrates. The code arbitrates. No exceptions.**

> **Status — May 2026:** Phase 3 (Discord multiplayer) is functional end-to-end.
> Engine + AI + memory + persistence are stable; ~2 200 tests pass. Phase 4
> (CI, docs polish, real play sessions) is the remaining gap. See
> [`docs/internal/STATE.md`](docs/internal/STATE.md) for the exact
> implementation status.

## Why this project

Most AI D&D projects let the LLM decide everything — dice rolls, damage,
loot. That leads to inconsistent rules, easy exploits, and broken immersion.
RealmAI Engine takes a different approach: a strict Python engine enforces
all mechanics, and the LLM is a blind narrator that describes what happened.
No other open-source project combines a deterministic engine + LLM narration
+ MCP server + Discord multiplayer.

## Architecture (one-screen view)

```
Player (Discord)
  │ free text or slash command
  ▼
ACTION INTERPRETER  (Qwen 3.5 4B, JSON mode)
  │ text → structured JSON
  ▼
ACTION VALIDATOR  (pure Python)
  │ weapon owned? correct turn? target in range? alive?
  ▼
GAME ENGINE  (pure Python)
  │ dice, damage, effects, XP, loot
  │ writes WorldState to SQLite
  ▼
CONTEXT ASSEMBLER
  │ 4 memory layers, ~2 500 tokens budget
  ▼
NARRATOR  (Qwen 3.5 9B, JSON mode)
  │ ActionResult → narrative text
  ▼
Discord (embed: narrative + raw mechanics side by side)

Background: STORY DIRECTOR every ~20 interactions
            BeatProgressionEngine after every player turn
```

The technical reference is in **[`ARCHITECTURE.md`](ARCHITECTURE.md)** —
module map, pipeline phases, persistence layout, invariants, extension
points.

### Memory system (4 layers)

| Layer | Source | Budget | Truncation |
|---|---|---:|---|
| 1. Structured state | SQLite snapshot | 450 tok | never |
| 2. Sliding window | last 12 exchanges | 700 tok | oldest first |
| 3. Compressed summaries | auto-generated every ~20 turns | 400 tok | oldest first |
| 4. Semantic RAG | ChromaDB (1 collection per campaign) | 350 tok | lowest score first |

## Tech stack

| Component | Technology |
|---|---|
| Language | Python 3.12+ |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Discord bot | discord.py 2.7+ |
| Data models | Pydantic v2 |
| Persistence | SQLAlchemy + SQLite |
| Semantic memory | ChromaDB |
| LLM inference | Ollama (local, OpenAI-compatible API) |
| MCP server | mcp Python SDK |
| Tests | pytest (+ pytest-asyncio, pytest-httpx, pytest-cov) |
| Linting | ruff |
| Type checking | mypy |

### LLM models (Ollama)

- **Narrator**: `qwen3.5:9b` (6.6 GB, ~25-35 tok/s on M3 Pro) — immersive narrative
- **Interpreter**: `qwen3.5:4b` (~3 GB, ~50-70 tok/s) — fast text-to-JSON parsing

Models are never loaded simultaneously to stay within the 18 GB memory
budget. JSON mode is forced everywhere — Ollama's native tool calling is
broken with Qwen 3.5 (see `ai/client.py`).

## Project structure

```
realmai-engine/
├── engine/          # Pure Python game logic (NO LLM EVER)
├── ai/              # LLM services (interpreter, narrator, tacticien, generators)
├── memory/          # 4-layer context assembly
├── world/           # Domain Pydantic models
├── bot/             # Discord bot — cogs, pipeline, views, embeds, lobby
├── mcp_discord/     # MCP server for Discord live-testing automation
├── db/              # SQLAlchemy models, mappers, repositories
├── data/            # Local SQLite + ChromaDB (gitignored)
├── logs/            # Runtime logs + story bibles (gitignored)
├── docs/
│   ├── internal/    # Up-to-date technical docs (architecture, flows, state)
│   └── superpowers/ # Historical design plans & specs
├── tasks/           # In-flight work tracking + accumulated lessons
├── tests/           # pytest unit tests + ScenarioRunner e2e
├── ARCHITECTURE.md  # Technical entry point
├── CLAUDE.md        # Conventions for Claude Code agents
└── README.md
```

## Documentation

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — technical entry point: layers,
  pipeline, persistence, invariants, extension points.
- **[docs/internal/](docs/internal/README.md)** — exhaustive technical
  reference, kept in lockstep with the code:
  - [ARCHITECTURE.md](docs/internal/ARCHITECTURE.md) — deep architecture (FR)
  - [STATE.md](docs/internal/STATE.md) — implemented vs. pending
  - [ISSUES.md](docs/internal/ISSUES.md) — known bugs, tech debt
  - [CAMPAIGN_LIFECYCLE.md](docs/internal/CAMPAIGN_LIFECYCLE.md) — `/start_campaign` → onboarding → save/resume → `/end_campaign`
  - [ACTION_PIPELINE.md](docs/internal/ACTION_PIPELINE.md) — the 6-phase pipeline
  - [COMBAT_SYSTEM.md](docs/internal/COMBAT_SYSTEM.md) — combat reference (modules, models, pipeline, API)
  - [NARRATIVE_COHERENCE.md](docs/internal/NARRATIVE_COHERENCE.md) — locked canon, NPCs, Story Director
  - [MEMORY_SYSTEM.md](docs/internal/MEMORY_SYSTEM.md) — 4-layer context assembly
  - [GAME_ENGINE.md](docs/internal/GAME_ENGINE.md) — deterministic rules module by module
  - [AI_LAYER.md](docs/internal/AI_LAYER.md) — Ollama services, prompts, retry logic
  - [DISCORD_BOT.md](docs/internal/DISCORD_BOT.md) — cogs, views, embeds, sessions
  - [DATABASE.md](docs/internal/DATABASE.md) — SQLAlchemy schema + repositories
  - [TESTING.md](docs/internal/TESTING.md) — pytest, ScenarioRunner, MCP Discord
- **[CLAUDE.md](CLAUDE.md)** — workflow conventions and coding standards for
  AI agents working in the repo.
- **[tasks/lessons.md](tasks/lessons.md)** — lessons accumulated across
  refactors (worth reading before touching memory, combat, or persistence).

## Getting started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Ollama](https://ollama.com/) (local LLM inference)
- A Discord bot token (see `.env.example`)

### Setup

```bash
# Clone the repo
git clone https://github.com/CocoLng/RealmAI-Engine.git
cd RealmAI-Engine

# Install dependencies (creates .venv, installs from uv.lock)
uv sync

# Pull the LLM models (~10 GB total)
ollama pull qwen3.5:9b
ollama pull qwen3.5:4b

# Configure the bot token
cp .env.example .env
# edit .env and set DISCORD_BOT_TOKEN
```

### Run the bot

```bash
uv run python main.py
```

### Run the test suite

```bash
uv run pytest                  # full suite (~2 200 tests)
uv run pytest tests/engine -q  # engine only
uv run pytest -m scenario      # end-to-end scenarios via ScenarioRunner
```

### Quality gates

```bash
uv run ruff check .            # linting
uv run mypy .                  # type checking
```

### Reset local game data

`data/realmai.db` and `data/chromadb/` are dev-only. To wipe them:

```bash
uv run python scripts/reset_dev_data.py
```

## Design principles

- **The LLM never decides mechanics** — dice, damage, loot, advancement, and
  combat outcomes are all deterministic Python.
- **Pydantic v2 everywhere in the domain** — strict types, no raw dicts at
  module boundaries.
- **Structured outputs only** — every Ollama call sets
  `response_format={"type": "json_object"}`; no regex parsing of free text.
- **Anti-cheat by design** — `ActionValidator` checks every action before
  the engine processes it. Discord shows both the narrative *and* the raw
  mechanics (dice, modifiers, outcomes).
- **Migrations are forward-only** — incremental `ALTER TABLE`s versioned by
  `schema_version`. No Alembic; the schema is small enough that hand-rolled
  is simpler and safer.

## License

MIT — see [LICENSE](LICENSE).
