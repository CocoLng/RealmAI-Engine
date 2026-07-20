# RealmAI-Engine

An AI-powered RPG Game Master Discord bot. A deterministic Python engine handles all game mechanics. Local LLMs (Ollama) handle narration only. Discord is the sole user interface.

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

Background: STORY DIRECTOR every 6 interactions, or on any of:
  combat just ended · narrative drift detected · /story_catch_up.
  (`bot/pipeline/orchestrator.py:should_run_director`)
```

### Tech Stack

- **discord.py 2.4+** — slash commands, buttons, modals, embeds
- **Pydantic v2** — all data models
- **SQLAlchemy + SQLite** — persistence
- **ChromaDB** — RAG for lore and NPC memory
- **Ollama** local (Mac M3 Pro 18GB) — `http://localhost:11434/v1`
- **pytest / ruff / mypy** — quality
- **uv** — project & dependency management (replaces pip/venv/pyenv)


### LLM Models (Ollama, never loaded simultaneously)

- **Narrator**: `qwen3.5:9b` (6.6GB, ~25-35 tok/s) — immersive narrative
- **Interpreter**: `qwen3.5:4b` (~3GB, ~50-70 tok/s) — fast text→JSON parsing
- **CRITICAL**: Do NOT use Ollama native tool calling (broken with Qwen 3.5). Use `response_format={"type": "json_object"}` instead.
### Memory System (4 layers, ~1500-2500 tokens per call)

1. **Structured state** (SQLite) — HP, AC, inventory, story arc. Source of truth.
1. **Sliding window** — last 10-12 exchanges for continuity.
1. **Compressed summaries** — auto-generated every 20 exchanges
   (`memory/summarizer.py:SUMMARY_INTERVAL`). Distinct from the Story
   Director cadence above — don't conflate the two.
1. **Semantic RAG** (ChromaDB) — lore, NPC sheets. Queried only when relevant.

### Anti-Cheat (non-negotiable)

- LLM NEVER decides dice rolls, damage, or loot
- ActionValidator checks EVERY action before engine processes it
- Narrator receives ActionResult and describes it — nothing more
- Discord shows both narrative AND raw mechanics

### Narrative Coherence

- **Locked facts**: world facts the LLM cannot contradict (Python/DB managed)
- **NPC registry**: status, disposition per player, secrets, personality prompt
- **Story Director**: periodic check for contradictions and abandoned threads

-----

## Project Structure

Key files only — run `ls` for the full picture rather than trusting this
tree to stay exhaustive.

```
realmAI-engine/
├── engine/           # Pure Python game logic (NO LLM EVER)
│   ├── dice.py           # Dice expressions ("2d6+3") → DiceResult, clamped
│   ├── character/        # PACKAGE: models, races, classes, abilities,
│   │                     #   progression, creation, features, presets
│   ├── combat.py         # Initiative, attacks, damage, turns, zones
│   ├── combat_phases.py  # Boss phase transitions
│   ├── combat_trigger.py # Why a fight starts: aggressor, joiners, surprise
│   ├── starter_gear.py   # Starter kits per class (used by the lobby flow)
│   ├── npc_ai/           # scripted (minions), elite, boss_brain, legendary
│   ├── npc_stat_block.py # NPC combat stats, LLM-authored values clamped here
│   ├── npc_library.py    # Combat archetypes by tier
│   ├── npc_archetypes.py # Narrative archetypes (authored content, 5 categories)
│   ├── inventory.py      # Items, equipment, weight, attunement (dormant)
│   ├── spells.py         # Spells and effects
│   ├── conditions.py     # Status conditions
│   ├── skill_check.py    # Skill DCs — never derived from player wording
│   ├── beat_progression.py   # Deterministic story-beat advancement
│   ├── objective_matchers.py # Objective gate matching
│   ├── arc_recipes.py    # Arc archetype recipes (variety, anti-repetition)
│   ├── contracts.py      # Shared I/O contracts (engine owns them, not ai/)
│   └── validators.py     # Action legality checks
├── ai/               # GenAI layer
│   ├── client.py         # Ollama wrapper (keep_alive + timeout policy)
│   ├── narrator.py       # 3-tier fallback chain + invented-damage guard
│   ├── interpreter.py    # text → JSON
│   ├── npc_agent.py · npc_generator.py · npc_tactician.py
│   ├── arc_generator.py · world_generator.py · story_director.py
│   ├── beat_judge.py     # LLM tiebreak when the engine is unsure
│   ├── prompt_safety.py  # Player input delimiting, secrets kept system-side
│   └── prompts/
├── memory/           # 4-layer memory (wired into prod since chantier G)
│   ├── state.py · sliding_window.py · summarizer.py
│   ├── semantic.py · indexer.py     # ChromaDB write + read
│   ├── narration_guard.py           # Dead-NPC / monotony guards
│   └── context_assembler.py
├── world/            # World state models (Pydantic)
│   ├── campaign.py · location.py · npc.py
│   ├── story_arc.py       # Beats, objectives, locked facts
│   └── combat_zone.py · combat_trigger_def.py
├── bot/              # Discord bot
│   ├── bot.py            # Bot setup, cog loading, intents
│   ├── config.py         # GuildConfig (category + language per guild)
│   ├── game_session.py   # In-memory session + AI service wiring
│   ├── lobby_state.py    # Lobby roster (replaced CampaignLauncher)
│   ├── pipeline/         # interpret → resolve → narrate + orchestrator
│   ├── action_pipeline.py    # Thin delegating facade over pipeline/
│   ├── combat_turn_manager.py · combat_entry.py · combat_end.py
│   ├── combat_truce.py   # CHA de-escalation: talk a fight to a close
│   ├── persistence.py · world_navigation.py · scene_hydration.py
│   ├── location_prefetch.py · npc_prefetch.py · prefetch_gate.py
│   ├── cogs/         # session, character, inventory, combat, rolls,
│   │                 #   hint, action_handler (@bot free text), test_bridge
│   ├── views/        # character_setup_flow, lobby_view,
│   │                 #   combat_action_view, *_select_view
│   ├── embeds/       # character, inventory, combat, narrative, lobby,
│   │                 #   dice, beat, arc_tracker, scene, ...
│   └── utils/        # channel_manager, arc_tracker
├── db/               # SQLAlchemy models, mappers, migrations, repositories/
├── mcp_discord/      # MCP server driving the tester bot
├── tests/            # pytest — mirrors source layout, plus:
│   ├── scenarios/    # End-to-end via ScenarioRunner (headless Discord)
│   └── simulation/   # Autonomous playthrough simulator
├── docs/             # internal/ (architecture), audits/, superpowers/
├── tasks/            # todo.md (task board) + lessons.md + archive/
├── CLAUDE.md · CONTRIBUTING.md · README.md · pyproject.toml · LICENSE (MIT)
```

-----

## Development Phases

> **Current status (2026-07-20): Phases 1-3 are shipped. Phase 4 is the only
> one still open.** Quality gates are all green — `pytest` 2913 passed,
> `ruff` clean, `mypy` 0 errors on 335 files — and **CI freezes the three of
> them on every push** (green on GitHub). The 9 chantiers of the 2026-06-10
> system audit are closed and merged, all 5 criticals included.
> Working task board: `tasks/todo.md`.

### Phase 1 — Game engine without AI ✅ shipped

`engine/` is pure deterministic Python with full test coverage.
dice → character → inventory → spells → conditions → combat → validators

### Phase 2 — AI layer ✅ shipped

Interpreter, Narrator, 4-layer memory, arc/NPC/world generation, Story
Director, Beat Progression Engine. Ollama integration.

Note: the 4-layer memory was only *wired into production* in July 2026
(chantier G). Before that the modules existed but nothing called them and
ChromaDB was write-only — worth knowing when reading older docs.

### Phase 3 — Discord bot + multiplayer ✅ shipped

> Design spec: `docs/superpowers/specs/2026-04-05-discord-bot-ux-design.md`

Cogs-by-domain architecture. Dedicated channel per campaign (created at `/start_campaign`, archived at `/end_campaign`). Slash commands for character/inventory/rolls with ephemeral responses (optional `public:` flag). Combat via buttons + select menus. No human GM — bot is the sole Game Master. See design spec for full details.

Superseded along the way: the `/look` `/move` `/search` `/talk` slash
commands and their `ExplorationCog` were replaced by free-text actions
through `bot/cogs/action_handler.py`; `/create_character` was replaced by
the `/start_campaign` lobby flow.

### Phase 4 — Polish + ship [CURRENT]

README with GIFs + architecture diagram, GitHub Actions CI/CD, real play sessions (3+ with friends), blog post / LinkedIn.

Shipped: **CI/CD** (`.github/workflows/ci.yml`, 3 parallel jobs, green on
GitHub since 2026-07-19) and the **architecture diagram** (Mermaid, in the
README). Three live-Discord sessions closed the verifications that had never
been run against an online bot — lobby → character creation → opening
narrative, exploration + H8 latencies, `/hint`. They were driven by autonomous
tester scripts, and they paid for themselves: two real bugs found (the DB
round-trip flattening every item to a base `Item`, and `/hint` leaking a raw
judge sentinel).

Remaining gaps, in order: **demo GIFs** (needs a graphical Discord client —
capture checklist in `tasks/todo.md` §1.3), **multi-player sessions with real
players** (the three closed sessions were solo and script-driven), then the
blog post / LinkedIn write-up.

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
- **Commits**: create commits autonomously when a piece of work is complete and verified — no need to ask each time. Conventional commits format (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`). Applies equally to subagent work in isolated worktrees — subagents should commit their changes before returning so the main session can review or amend.
- **Undercover mode** — Never add `Co-Authored-By`, AI attribution, or any Claude/AI mention in commit messages, PR descriptions, or code comments.
- **Still ask before**: pushing to a remote (`git push`), force-push, rebase that rewrites shared history, merging/closing PRs, or any destructive git operation (`reset --hard`, `branch -D`, `clean -f`).
