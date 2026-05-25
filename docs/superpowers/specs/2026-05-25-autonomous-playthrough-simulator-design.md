# Autonomous Playthrough Simulator — Design

**Date:** 2026-05-25
**Status:** Approved (brainstormed)
**Owner:** generali@notflixed.com
**Scope:** Phase 2 / Phase 3 — testing infrastructure

---

## 1. Context & Motivation

### What exists today

Two testing tools cover the bot:

- **`ScenarioRunner`** (`tests/scenarios/`) — pytest-based, mocks Discord interactions, uses the real engine + real in-memory DB, but **disables the AI layer** (Interpreter, Narrator, Story Director). Verifies mechanics in isolation.
- **`mcp_discord` + TesterBot** — connects a second Discord bot to a test server, drives the real game bot via `!test` commands or real Discord interactions, **AI is enabled**. Verifies UX and full-stack behavior, but requires manual piloting one command at a time.

### What is missing

There is no way to exercise a **full campaign run** through the **complete LLM pipeline** (Interpreter + Narrator + Story Director + 4-layer memory) **without manual piloting**. As a result:

- We have no systematic coverage of long-session behavior (10+ turns).
- We never observe how the LLM stack drifts over time (NPC names, locked facts, item presence, location consistency).
- Regressions in the LLM pipeline only surface when a human plays.

### Goal of this design

Build a **headless autonomous simulator** that plays a full campaign on its own (character creation, exploration, combat, dialogue, save/resume), exercises the **real** Ollama pipeline, and emits a **deterministic incoherence report** at the end.

The simulator's primary success criterion is **detecting narrative incoherences** between the Narrator's output and the engine's deterministic state.

---

## 2. Goals & Non-Goals

### Goals

- Run a full RealmAI campaign autonomously, through the real bot + AI pipeline, with zero human input.
- Produce a machine-readable transcript (JSONL) + human-readable report (Markdown) per run.
- Detect incoherences between Narrator output and engine state via deterministic heuristics (no LLM-as-judge in the MVP).
- Be reproducible (seed-based) and configurable (max turns, policy, character preset).
- Run in ≤ 5 minutes for a 30-turn solo run on a Mac M3 Pro 18GB.
- Cost zero $ — all LLM calls go through the local Ollama instance.

### Non-Goals

- **Live Discord runs.** The simulator runs headless (in-process). Live Discord verification stays the responsibility of the existing tester bot.
- **Multi-player simulations.** Single-character runs only in the MVP. Architecture must not preclude multi-player but does not implement it.
- **LLM-as-judge / semantic incoherence detection.** Out of scope MVP — heuristics first.
- **Cross-run regression comparison (`compare_runs.py`).** Out of scope MVP — but transcript format is designed to support it later.
- **Replacing the existing `ScenarioRunner` or tester bot.** This is an additional tool, not a replacement.
- **CI integration of full runs.** Only the simulator's own unit tests run in CI. Full runs are local / nightly / manual.

---

## 3. Architecture

### Components

Four units, each with a single responsibility, communicating through typed Pydantic interfaces. All code lives under `tests/simulation/`.

```
┌─────────────────────────────────────────────────────────────────┐
│                      SimulationRunner                           │
│  Orchestrates: start_campaign → loop → finalize                 │
└──────────┬──────────────┬───────────────┬────────────┬──────────┘
           │              │               │            │
           ▼              ▼               ▼            ▼
   AutonomousAgent   GameDriver    IncoherenceChecker  Recorder
   (the "brain")     (the "hands")  (the "linter")     (the "black box")
```

- **`SimulationRunner`** — sets up the run (db path, seed, config), loops over turns, applies stop criteria, finalizes the report. Has no domain knowledge of LLM, combat, or narrative; pure coordination.
- **`AutonomousAgent`** — observes a serialized `game_state`, calls the 4b (`qwen3.5:4b`) with a constrained JSON schema, emits an `AgentIntent`. Includes retry + fallback logic.
- **`GameDriver`** — translates `AgentIntent` into cog handler calls via an extended `ScenarioRunner` (with AI enabled). Captures the resulting narration, embed, view, action JSON, and any error. Returns a `TurnOutcome`.
- **`IncoherenceChecker`** — pure-function aggregator of `Rule` callbacks. Reads `(narration, state_after, diff, history)`, emits `list[IncoherenceAlert]`.
- **`Recorder`** — receives `TurnRecord` per turn, appends to `transcript.jsonl`, prints a runtime line to stdout, and writes the final `report.md` + `final_state.json` + `config.json`.

### Module boundaries

Each module's input and output are Pydantic models defined in `records.py`. No module reads another's internals. Consequences:

- Each unit testable in isolation.
- The `AutonomousAgent` can be swapped (e.g., for a rules-based agent in the future) without touching the runner.
- New rules can be added to the checker without touching anything else.
- The transport (in-process via ScenarioRunner today, possibly TestBridge in-process or live Discord tomorrow) is isolated in `GameDriver`.

### External dependencies

- **`ScenarioRunner`** (`tests/scenarios/scenario_runner.py`) — reused, extended with an `ai_enabled: bool = False` flag. When `True`, real `Interpreter` / `Narrator` / `StoryDirector` are wired instead of mocks.
- **`ai/client.py`** — minimal addition: `simulation_mode: bool` flag that forces `temperature=0.0` everywhere in the AI layer for reproducibility.
- **`engine/dice.py`** — already supports seeding.
- **`memory/state.py`** — used as is, with a per-run SQLite path.

No changes to `bot/`, `engine/`, or `world/` beyond the two flags above.

---

## 4. Per-Turn Data Flow

```
SimulationRunner.run_turn(N):

  1. state_before   = GameSession.snapshot()                    # Pydantic, deep copy
  2. observation    = build_observation(state_before)           # serialized for the agent
                       │
                       │ includes: hp, location, equipped, inventory,
                       │   visible enemies/NPCs, combat status, last 3 actions,
                       │   last narration excerpt
                       ▼
  3. intent         = agent.decide(observation)                 # LLM call #1 (4b)
                       │
                       │ AgentIntent:
                       │   {action: "attack" | "move" | ..., args: {...},
                       │    raw_text: str | None, reasoning: str}
                       ▼
  4. outcome        = driver.execute(intent)                    # LLM #2 (4b interpreter)
                       │                                          + LLM #3 (9b narrator)
                       │ delegates to ScenarioRunner with AI enabled.
                       │ Captures: narration_text, embed_captured,
                       │   action_resolved (JSON), error?, timing
                       ▼
  5. state_after    = GameSession.snapshot()
  6. diff           = state_diff(state_before, state_after)     # which fields changed
  7. alerts         = checker.check(narration_text, state_after, diff, history)
  8. record         = TurnRecord(N, observation, intent, outcome, diff, alerts, timing)
  9. recorder.append(record)
  10. return record
```

**3 LLM calls per turn nominal.** Ollama keeps the 4b warm across agent + interpreter calls, swaps to the 9b for narration, swaps back to the 4b on the next turn. Measured target: ~5–10 s per turn on Mac M3 Pro.

**Story Director** (`ai/story_director.py`) is triggered periodically (~ every 20 interactions, configurable). When it fires, it adds one extra 9b call to that turn. Its alerts are surfaced in the report alongside the deterministic `IncoherenceChecker` alerts but tagged separately (`source: story_director`).

---

## 5. Stop Criteria & Reproducibility

### Stop criteria

| Cause | Outcome marker |
|---|---|
| `turn_idx >= max_turns` (default 30) | `max_turns_reached` |
| Character `hp <= 0` | `character_death` |
| `outcome.error is not None` | `pipeline_error` (immediate exit, stack trace recorded) |
| `len(alerts) >= alert_budget` (default 5) | `alert_budget_exceeded` |
| `time.elapsed > max_wall_time` (default 600 s) | `wall_time_exceeded` |
| `intent.action == "wait"` returned 3 turns in a row | `agent_stuck` |

The cause and context (last turn record, final state) are written to `report.md`.

### Reproducibility

- **Seed** — a single `random.Random(seed)` is threaded through `engine.dice` via `ScenarioRunner` (existing support).
- **LLM temperature** — Interpreter and Narrator are forced to `temperature=0.0` during simulations via `ai/client.py`'s `simulation_mode` flag. The Agent stays at `0.3` by default for variety; CLI flag `--agent-temp 0.0` makes the agent deterministic too.
- **Database** — SQLite in a per-run directory (`runs/<ts>__seed<N>/realmai.db`), discarded at the end unless `--keep-db` is passed.
- **ChromaDB** — namespaced per run (`collection=f"simulation_{run_id}"`), purged at the end.

Two runs with the same seed, same code, same Ollama models, and `--agent-temp 0.0` are expected to produce structurally-identical transcripts: same intents, same engine state transitions, same alerts. Narration text may drift slightly between runs because Ollama's `temperature=0` is not guaranteed bit-exact across GPU calls — that is acceptable for the MVP. The `compare_runs.py` future tool will diff structural fields, not raw narration strings.

---

## 6. Agent Policy

### Observation format

A compact, hand-formatted text block (~300–500 tokens) produced from `GameSession.snapshot()`. Example:

```
TURN 5
You play: Aria (Elf, Wizard, lvl 1, HP 12/15, AC 13, MP 8/10)
Location: Cave entrance (dim, cold). Exits: north → Cave deep
Equipped: Quarterstaff (1d6)
Inventory: Healing potion (x2), Scroll of Magic Missile
Combat: IN COMBAT, your turn (initiative 18)
  Enemies (visible):
    - Goblin_1: HP 4/4, AC 12, zone "front", ranged
    - Goblin_2: HP 1/4, AC 12, zone "front", melee (BLOODIED)
  Allies: none
NPCs present: none
Last 3 turns: look, move(north), attack(Goblin_2)
Last narration: "Goblin_2 chancelle, du sang lui coule du front."
```

Anything not listed does not exist for the agent. This is the first anti-hallucination guardrail: if the LLM "sees" two goblins, it cannot invent a troll.

### `AgentIntent` schema

```python
class AgentIntent(BaseModel):
    reasoning: str = Field(..., max_length=200)
    action: Literal[
        "attack", "cast_spell", "defend", "flee",
        "move", "look", "talk", "search",
        "equip", "unequip", "use_item",
        "free_form", "wait"
    ]
    args: dict[str, str] = Field(default_factory=dict)
    raw_text: str | None = None   # required when action == "free_form"
```

Enforced via `response_format={"type": "json_object"}` (already supported by `ai/client.py`). Parsed with `AgentIntent.model_validate_json()` — `ValidationError` triggers retry.

### Legality validator

The schema validates shape only. A contextual `is_legal(intent, state) -> tuple[bool, str|None]` validator checks legality before dispatching to the cog:

| Action | Check |
|---|---|
| `attack` | `state.combat_active` AND `args["target"]` ∈ living enemies |
| `cast_spell` | `args["spell"]` ∈ spellbook AND sufficient mana |
| `move` | `args["direction"]` ∈ location.exits AND not in combat |
| `equip` / `unequip` | item present in inventory / valid slot |
| `use_item` | item in inventory AND consumable |
| `free_form` | `raw_text` non-empty AND len ≤ 200 |

If illegal, the runner re-prompts the 4b with a corrective hint (`"Action attack is not legal here because... Try again."`). Max 3 retries → fallback to a deterministic safe action (`look` out of combat, `defend` in combat).

### Policy modes

CLI flag `--policy={balanced|combat_focused|story_focused}`. The mode is an addendum to the agent's system prompt; no separate code paths.

- **balanced** *(default)* — mixes combat/exploration/dialogue, ~30 % `free_form` while exploring (exercises the @mention pipeline).
- **combat_focused** — seeks enemies, engages aggressively. Stress-tests combat pipeline.
- **story_focused** — favors `talk` and `free_form`, minimizes combat. Stress-tests Narrator + NPC memory.

### Anti-deadlock guardrails

- `intent.action == "wait"` returned 3 times in a row → `agent_stuck`.
- Same exact `(action, args)` emitted 4 times in a row → injected hint in the next prompt: *"You are repeating the same action. Pick a different one."*

---

## 7. IncoherenceChecker — Rules

Each rule is a pure function `(narration, state_after, diff, history) -> list[IncoherenceAlert]`. The checker is just an aggregator over the rule list. New rules are added without touching anything else.

### `IncoherenceAlert`

```python
class IncoherenceAlert(BaseModel):
    severity: Literal["hard", "soft", "drift"]
    category: str            # "dead_npc_speaks", "phantom_item", ...
    turn: int
    rule: str                # "R1.npc_status", ...
    narration_snippet: str   # offending excerpt (≤ 200 chars)
    expected: str            # what the deterministic state says
```

### Category 1 — *Hard* (direct DB contradiction)

| ID | Rule | Detection |
|---|---|---|
| R1.npc_status | NPC marked `dead` speaks / acts in the narration | NPC.name in narration AND (NPC.status == "dead" OR NPC.hp ≤ 0) AND narration matches regex `(parle\|dit\|attaque\|s'avance\|sourit)` |
| R1.phantom_npc | NPC named in narration but absent from the registry | Extract capitalized proper nouns AND filter ∉ NPC.registry AND ∉ player_names |
| R1.item_use_without_owning | Character "uses" an item absent from inventory | narration matches `(utilise\|boit\|consomme\|brandit) (le\|la\|une) ITEM` AND ITEM ∉ inventory.items |
| R1.locked_fact_violation | A locked world fact is contradicted (cf. `world/facts.py`) | locked_fact.text in narration negated (pattern-based negative detection) |
| R1.location_mismatch | Narration describes a location other than `current_location` | location.name in narration ≠ session.current_location.name AND no movement transition this turn |
| R1.hp_mismatch | Narration describes a character as wounded/dying while at full HP | regex `(agonise\|chancelle\|s'effondre\|grièvement blessé)` AND char.hp ≥ 0.8 × char.max_hp |
| R1.zone_violation | Combat action mentions a non-existent zone | regex `zone (front\|back\|flanc)` AND zone ∉ combat_state.zones |

### Category 2 — *Soft* (suspect, not certain)

| ID | Rule | Detection |
|---|---|---|
| R2.repetition | Identical phrase ≥ 2 times in 5 turns | Sliding window, n-gram match ≥ 10 consecutive words |
| R2.npc_name_drift | NPC referenced with a near-but-different name (e.g., "Garm" → "Garn") | Levenshtein ≤ 2 of a known NPC.name AND ≠ exact NPC.name |
| R2.tense_drift | Mixed past / present markers within the same sentence | regex co-occurrence of passé composé + present markers |
| R2.unknown_proper_noun | Proper noun matching no NPC, location, faction | extracted NPs minus registries minus whitelist |

### Category 3 — *Drift* (informational only)

| ID | Rule |
|---|---|
| R3.disposition_silent_change | NPC `disposition` changed (`friendly` → `hostile`) with no player action this turn |
| R3.quest_silent_progress | Quest objective advanced / completed without a corresponding intent |
| R3.condition_phantom | A condition (`poisoned`, `prone`, …) appeared/disappeared without a traceable cause this turn |

### False positives

Expected. Each alert in `report.md` shows a `false_positive_candidates` flag so we can tag manually and refine the regexes after a few runs. `tests/simulation/false_positives.yml` whitelists known-noisy patterns.

---

## 8. Recorder & Outputs

### Per-run directory

```
tests/simulation/runs/<YYYYMMDD_HHMMSS>__seed<N>/
├── transcript.jsonl   # one line per turn, machine-readable
├── report.md          # post-run synthesis, human-readable
├── final_state.json   # last GameSession.snapshot() (Pydantic dump)
├── system.log         # Python logging output
└── config.json        # run parameters (seed, policy, max_turns, ...)
```

`runs/` is added to `.gitignore`. Nothing committed by default.

### JSONL schema (`TurnRecord`)

```json
{
  "turn": 5,
  "ts": "2026-05-25T16:42:01.234Z",
  "observation": "TURN 5\nYou play: Aria...",
  "intent": {
    "reasoning": "Goblin_2 is bloodied, finishing it lowers threat",
    "action": "attack",
    "args": {"target": "Goblin_2"},
    "raw_text": null
  },
  "outcome": {
    "narration": "Aria abat son bâton sur le gobelin chancelant...",
    "action_resolved": {"type": "attack", "hit": true, "damage": 6, "killed": true},
    "error": null,
    "timing_ms": {"agent": 1240, "interpreter": 980, "engine": 8, "narrator": 3210}
  },
  "diff": {
    "Goblin_2.hp": [1, 0],
    "Goblin_2.status": ["alive", "dead"],
    "combat_state.round": [3, 3]
  },
  "alerts": [],
  "agent_retries": 0
}
```

Dedicated Pydantic schema (`TurnRecord` in `records.py`) for downstream parsing and a future `compare_runs.py`.

### `report.md` — post-run synthesis

Fixed structure:

- Header: timestamp, seed, policy.
- **Outcome**: status (max_turns_reached / character_death / pipeline_error / ...), wall time, turn count, final character state.
- **LLM calls**: counts and average latency per model.
- **Alerts** table: turn, severity, rule, snippet.
- **Turn-by-turn** summary: one block per turn with intent reasoning, narration, diff.
- **Final state**: rendered diff vs initial state.

### Runtime observability

stdout, one line per turn:

```
[T01 1.2s] look                         → ok          alerts:0
[T02 1.1s] move(north)                  → ok          alerts:0
[T03 5.8s] attack(Goblin_2)             → hit dmg=6   alerts:0
[T04 4.9s] attack(Goblin_2)             → killed      alerts:0
[T05 5.1s] @bot je fouille le cadavre   → ok          alerts:0
[T12 4.7s] talk(Garm)                   → ok          alerts:1  ⚠ R1.npc_status
```

Any `hard` alert also prints the snippet + expected to stderr immediately — no need to wait for the end.

---

## 9. Module Layout & CLI

### File tree

```
tests/simulation/
├── __init__.py
├── __main__.py             # CLI entry point
├── runner.py               # SimulationRunner (~150 LoC)
├── agent.py                # AutonomousAgent (~200 LoC)
├── driver.py               # GameDriver (~100 LoC)
├── checker.py              # IncoherenceChecker (~80 LoC)
├── recorder.py             # JSONL + markdown writer (~200 LoC)
├── records.py              # Pydantic: TurnRecord, AgentIntent, IncoherenceAlert
├── rules/
│   ├── __init__.py
│   ├── hard.py             # R1.* rules
│   ├── soft.py             # R2.* rules
│   └── drift.py            # R3.* rules
├── prompts/
│   ├── agent_system.txt
│   └── few_shots.json
├── false_positives.yml
├── runs/                   # gitignored
└── tests/                  # tests of the simulator itself
    ├── test_agent.py
    ├── test_driver_smoke.py
    ├── test_rules_hard.py
    ├── test_rules_soft.py
    ├── test_recorder_shape.py
    └── test_runner_e2e_mocked_llm.py
```

### CLI

```bash
# Single 30-turn run, balanced policy
uv run python -m tests.simulation --max-turns 30 --seed 42

# Combat-focused, shorter
uv run python -m tests.simulation --max-turns 15 --policy combat_focused --seed 7

# Batch (5 runs, seeds 1..5)
uv run python -m tests.simulation --batch 5 --max-turns 30

# CI-friendly mode (exit 1 on any hard alert)
uv run python -m tests.simulation --max-turns 30 --fail-on hard

# Reproduce a previous run from its config
uv run python -m tests.simulation --config tests/simulation/runs/20260525_164201__seed42/config.json
```

CLI flags (full list in `__main__.py`):

| Flag | Default | Description |
|---|---|---|
| `--max-turns` | 30 | Hard cap |
| `--seed` | random | Engine + agent seed |
| `--policy` | `balanced` | `balanced` / `combat_focused` / `story_focused` |
| `--agent-temp` | 0.3 | Agent LLM temperature |
| `--max-wall-time` | 600 | Seconds before forced exit |
| `--alert-budget` | 5 | Early-exit threshold on hard alerts |
| `--fail-on` | `none` | `none` / `hard` / `any` — exit code 1 if matching alerts |
| `--batch` | 1 | Number of runs (seeds increment from `--seed`) |
| `--keep-db` | false | Preserve SQLite + ChromaDB at the end |
| `--mock-llm` | false | Replace Ollama calls with scripted responses (for tests) |
| `--config` | none | Reproduce a previous run from its `config.json` |

---

## 10. Testing the Simulator

The simulator is itself a non-trivial harness; it has its own test suite.

| File | Coverage |
|---|---|
| `test_agent.py` | Mocked 4b client → JSON parsing, retry on invalid intent, fallback determinism |
| `test_driver_smoke.py` | One turn with `ScenarioRunner + AI` enabled (marked `@pytest.mark.slow`, hits Ollama) |
| `test_rules_hard.py` | Each R1.* rule: synthetic narration + state pairs that MUST / MUST NOT trigger |
| `test_rules_soft.py` | Same for R2.* |
| `test_recorder_shape.py` | Snapshot tests of JSONL line + markdown block for a canonical `TurnRecord` |
| `test_runner_e2e_mocked_llm.py` | 3-turn pipeline with `--mock-llm` — verifies orchestration end-to-end |

### CI integration

- **Standard CI** (`pytest tests/`) — runs `tests/simulation/tests/*` minus `@slow` markers. Verifies the simulator code itself stays green.
- **Nightly / manual** — full runs (`uv run python -m tests.simulation --max-turns 30 --seed 42 --fail-on hard`). Not part of the MVP, but the CLI is designed to support it.

---

## 11. Implementation Footprint

- New module `tests/simulation/` (~800 LoC + tests).
- `ScenarioRunner` — add `ai_enabled: bool = False` flag (~20 LoC).
- `ai/client.py` — add `simulation_mode: bool` flag forcing `temperature=0.0` (~10 LoC).
- `.gitignore` — add `tests/simulation/runs/`.

No changes to `bot/`, `engine/`, `world/`, `memory/` beyond the two flag additions.

---

## 12. Open Questions for Implementation

These are decisions deferred to the implementation plan, not blockers:

- Exact regex set per rule — will be tuned against the first 3–5 real transcripts.
- Few-shot examples for the agent prompt — will be authored after the first agent run.
- Whether to ship the `tests/simulation/false_positives.yml` whitelist empty or pre-populated.
- Exact format of the `diff` field (`{path: [old, new]}` vs JSON Patch) — to be picked when implementing `state_diff`.

---

## 13. Rationale Summary

| Choice | Reasoning |
|---|---|
| Headless via `ScenarioRunner + AI` rather than live Discord | Speed + reproducibility. Live Discord testing already exists. |
| 4b for the agent rather than rules-only | User wants creative free-form intents to exercise the `@mention` pipeline. |
| Deterministic heuristics rather than LLM-as-judge | Faster, free, deterministic — and the existing Story Director already covers LLM-side checks. |
| Single character / solo runs only | YAGNI for MVP. Adding multi-player later does not break the architecture. |
| Per-run directory in `tests/simulation/runs/` | Keeps the workspace clean, easy to compare runs post-hoc. |
