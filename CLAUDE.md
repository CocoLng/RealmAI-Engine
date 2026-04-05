# RealmAI-Engine

An AI-powered RPG Game Master Discord bot. A deterministic Python engine handles all game mechanics. Local LLMs (Ollama) handle narration only. The engine is exposed as an MCP server.

**The LLM narrates. The code arbitrates. No exceptions.**

-----

## Workflow Orchestration

### 1. Plan Mode Default

- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy

- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop

- After ANY correction from the user: update tasks/lessons.md with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done

- Never mark a task complete without proving it works
- Run `pytest` after every module change — green tests or it’s not done
- Run `ruff check .` and `mypy .` before declaring victory
- Ask yourself: “Would a staff engineer at Google approve this?”

### 5. Demand Elegance (Balanced)

- For non-trivial changes: pause and ask “is there a more elegant way?”
- If a fix feels hacky: “Knowing everything I know now, implement the elegant solution”
- Skip this for simple, obvious fixes — don’t over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing

- When given a bug report: just fix it. Don’t ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

### 7. Session Handoff

- Before ending ANY session: update `tasks/todo.md` with current progress, blockers, and next steps
- If investigating a bug or issue: document findings, hypotheses tested, and what remains in `tasks/todo.md`
- Update `tasks/lessons.md` if anything was learned during the session
- The next agent starts cold — leave enough context for them to pick up without re-discovering anything
- Think: "If I forget everything, what do I need written down to continue?"

-----

## Task Management

1. **Plan First**: Write plan to tasks/todo.md with checkable items
1. **Verify Plan**: Check in before starting implementation
1. **Track Progress**: Mark items complete as you go
1. **Explain Changes**: High-level summary at each step
1. **Document Results**: Add review section to tasks/todo.md
1. **Capture Lessons**: Update tasks/lessons.md after corrections
1. **Session Handoff**: Before ending, update tasks/todo.md with progress, blockers, and next steps so the next agent can continue seamlessly

-----

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Minimal code impact.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Only touch what’s necessary. No side effects with new bugs.
- **LLM ≠ Referee**: The engine/ directory is pure deterministic Python. No LLM calls ever. If you’re tempted to let the LLM decide a mechanical outcome, stop — that’s a bug.
- **Pydantic Everywhere**: All data models use Pydantic v2 BaseModel with strict types. No raw dicts for game state.
- **Test Everything Mechanical**: Every engine module gets pytest coverage >80%. If it rolls dice, deals damage, or validates an action, it has a test.
- **Structured Over Freeform**: LLM outputs use `response_format={"type": "json_object"}`, never raw text parsing with regex.

-----

## Project Context

### Architecture (6-step pipeline)

```
Player (Discord) → INTERPRETER (LLM:4b, text→JSON)
                 → VALIDATOR (Python, checks legality)
                 → ENGINE (Python, resolves mechanics, updates DB)
                 → CONTEXT ASSEMBLER (builds prompt, 4 memory layers)
                 → NARRATOR (LLM:9b, ActionResult→narrative)
                 → Discord (embed: narrative + raw stats)

Background: STORY DIRECTOR every ~20 interactions.
```

### Tech Stack

- **discord.py 2.4+** — slash commands, buttons, modals, embeds
- **Pydantic v2** — all data models
- **SQLAlchemy + SQLite** — persistence
- **ChromaDB** — RAG for lore and NPC memory
- **Ollama** local (Mac M3 Pro 18GB) — `http://localhost:11434/v1`
- **mcp Python SDK** — MCP server
- **pytest / ruff / mypy** — quality
- **uv** — project & dependency management (replaces pip/venv/pyenv)


### LLM Models (Ollama, never loaded simultaneously)

- **Narrator**: `qwen3.5:9b` (6.6GB, ~25-35 tok/s) — immersive narrative
- **Interpreter**: `qwen3.5:4b` (~3GB, ~50-70 tok/s) — fast text→JSON parsing
- **CRITICAL**: Do NOT use Ollama native tool calling (broken with Qwen 3.5). Use `response_format={"type": "json_object"}` instead.
### Memory System (4 layers, ~1500-2500 tokens per call)

1. **Structured state** (SQLite) — HP, AC, inventory, quests. Source of truth.
1. **Sliding window** — last 10-12 exchanges for continuity.
1. **Compressed summaries** — auto-generated every ~20 interactions.
1. **Semantic RAG** (ChromaDB) — lore, NPC sheets. Queried only when relevant.

### Anti-Cheat (non-negotiable)

- LLM NEVER decides dice rolls, damage, or loot
- ActionValidator checks EVERY action before engine processes it
- Narrator receives ActionResult and describes it — nothing more
- Discord shows both narrative AND raw mechanics

### Narrative Coherence

- **Locked facts**: world facts the LLM cannot contradict (Python/DB managed)
- **NPC registry**: status, disposition per player, secrets, personality prompt
- **Story Director**: periodic check for contradictions, stale quests, abandoned threads

-----

## Project Structure

```
realmAI-engine/
├── engine/           # Pure Python game logic (NO LLM EVER)
│   ├── dice.py       # Dice expressions ("2d6+3") → DiceResult
│   ├── character.py  # Classes, races, stats, levels
│   ├── combat.py     # Initiative, attacks, damage, turns
│   ├── inventory.py  # Items, equipment, weight
│   ├── spells.py     # Spells and effects
│   ├── conditions.py # Status conditions
│   ├── rules.py      # Simplified SRD 5e
│   └── validators.py # Action legality checks
├── ai/               # GenAI layer
│   ├── narrator.py
│   ├── interpreter.py
│   ├── npc_agent.py
│   ├── quest_generator.py
│   ├── world_generator.py
│   ├── story_director.py
│   └── prompts/
├── memory/           # 4-layer memory
│   ├── state.py
│   ├── sliding_window.py
│   ├── summarizer.py
│   ├── semantic.py
│   └── context_assembler.py
├── world/            # World state models
│   ├── world_state.py
│   ├── facts.py
│   ├── npcs.py
│   ├── locations.py
│   ├── quests.py
│   └── factions.py
├── bot/              # Discord bot
│   ├── bot.py
│   ├── commands/
│   ├── views/
│   └── embeds/
├── mcp_server/       # MCP server
├── db/               # SQLAlchemy models + database
├── tests/            # pytest (mirrors engine/ structure)
├── tasks/            # todo.md + lessons.md
├── CLAUDE.md
├── pyproject.toml
├── README.md
└── LICENSE (MIT)
```

-----

## Development Phases

### Phase 1 — Game engine without AI [CURRENT]

Build engine/ with full test coverage. Playable in terminal. No LLM, no Discord.
Order: dice → character → inventory → spells → conditions → combat → validators

### Phase 2 — AI layer

Interpreter, Narrator, 4-layer memory, quest/NPC generation, Story Director. Ollama integration.

### Phase 3 — Discord bot + multiplayer

Slash commands, combat buttons, embeds, multi-player, save/resume.

### Phase 4 — MCP server + polish

MCP server, README with GIFs, CI/CD, CONTRIBUTING.md, blog post.

-----

## Coding Conventions

- All data models: **Pydantic v2 BaseModel** with strict types
- All functions: **type hints** (mypy enforced)
- All engine modules: **pytest tests** in tests/
- Use **dataclasses** only for internal state not needing validation
- **No LLM calls in engine/** — pure deterministic Python
- **Docstrings** on all public functions
- One responsibility per file
- Use **Enum** for fixed sets (DamageType, Ability, Condition, etc.)
- **No raw dicts** — always Pydantic models or named tuples
- Use `uv run` to execute anything (tests, scripts, linting) — never activate venv manually
- Add deps with `uv add`, dev deps with `uv add --dev`
- Commits: conventional commits format (feat:, fix:, test:, docs:)