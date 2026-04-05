# Lessons Learned

Patterns and corrections captured during development. Review at session start.

---

## Phase 1 Completion

### DamageType location
- `DamageType` lives in `engine/inventory.py`. Spells and combat import it from there.
- Thunder damage type doesn't exist — Thunderwave uses `DamageType.BLUDGEONING` as simplification.
- If more damage types are needed later (Force, Thunder, Psychic, Acid), add them to `inventory.py`.

### Ollama tool calling is broken with Qwen 3.5
- Do NOT use Ollama native tool calling. Use `response_format={"type": "json_object"}` instead.
- This is documented in CLAUDE.md but easy to forget when starting Phase 2.

### Mutation vs immutability pattern
- `inventory.py` functions return NEW instances (immutable pattern via `model_copy`)
- `character.py` `add_xp`/`level_up` mutate in place + return
- `combat.py` and `conditions.py` mutate in place + return
- Be consistent within a module. Document mutation in docstrings.

### ActiveCondition.duration_rounds has ge=1 constraint
- Cannot create a condition with `duration_rounds=0`. Minimum is 1 or None (indefinite).
- `tick_durations()` removes conditions when duration reaches 0 after decrement.

### Monkeypatching dice in tests
- Monkeypatch `engine.<module>.roll` (the local import reference), not `engine.dice.roll`.
- Example: `monkeypatch.setattr("engine.combat.roll", lambda expr: DiceResult(...))`

### Parallel subagents for independent modules
- spells.py and conditions.py were built in parallel (no dependency between them).
- combat.py depends on both → must wait for them to finish.
- This pattern saves significant time on multi-module work.

---

## Phase 2b — Memory System

### Circular imports when re-exporting from __init__.py
- `memory/__init__.py` must NOT re-export symbols. Keep it as docstring-only with usage instructions.
- The chain that breaks: `memory/__init__.py → context_assembler → summarizer → db.repositories.exchange_repo → db.mappers → memory.models` — memory package isn't fully initialized yet.
- **Pattern**: Whenever a subpackage re-exports from a deeply nested module that imports the subpackage, you get a circular import. Use docstring-only `__init__.py` in such cases.

### Token estimation ceil() causes off-by-one in truncation loops
- `estimate_tokens` uses `math.ceil()`, so after truncating in a loop, the total can still be 1-3 tokens over budget due to rounding artifacts.
- **Fix**: Always add a final clamp pass after the truncation loop. Check `total > budget` and truncate one more layer if needed.
- Apply this pattern to any loop-based token budget enforcement.

### ChromaDB EphemeralClient is a singleton — reset between tests
- `chromadb.EphemeralClient()` returns the same in-memory instance per process.
- Without `Settings(allow_reset=True)` + `client.reset()` in teardown, collections persist across tests and cause false positives.
- Always use: `client = chromadb.EphemeralClient(settings=Settings(allow_reset=True))` and reset in fixture teardown.

### Session commit contract in sliding window / context assembler
- `SlidingWindow.add_exchange()` and `ContextAssembler.record_exchange()` do NOT commit automatically.
- The **caller** must call `db_session.commit()` after recording exchanges.
- This matches the repository pattern used everywhere else in the codebase (repos add/save but don't commit).

### batch add_documents requires same campaign_id
- `SemanticMemory.add_documents()` silently used only the first document's campaign_id for collection routing.
- Added validation: raises `ValueError` if documents span multiple campaign_ids.
- Always validate batch operation assumptions explicitly, not silently.

### `except (json.JSONDecodeError, Exception)` is redundant
- `Exception` is a superclass of `json.JSONDecodeError`, making the tuple redundant.
- Always split into two separate `except` clauses with distinct handling when you want different log messages.

### ContextBudget total_max must be >= layer1_max
- Layer 1 is NEVER truncated by design. If `total_max < layer1_max`, the assembler silently produces over-budget output.
- Added `@model_validator` to enforce `total_max >= layer1_max` at construction time.
- For any "never truncated" priority constraint, add a validator at the model level.

---

## Phase 2c — AI Core

### pytest-httpx for mocking Ollama (OpenAI SDK)
- The `openai` Python SDK uses `httpx` internally. `pytest-httpx` patches `httpx` transport globally and intercepts all calls including those from the OpenAI SDK.
- The OpenAI SDK retries `ConnectError` by default (2 retries). Set `max_retries=0` when creating the `OpenAI` client to avoid exhausting the single mock exception from `httpx_mock.add_exception()`.
- Must also catch `APIConnectionError` and `APITimeoutError` from the OpenAI SDK — all three map to `OllamaUnavailableError`.

### OllamaClient as single injection point
- All 6 AI modules inject `OllamaClient` via constructor (`__init__`). None instantiate it internally.
- This makes testing trivial: one `httpx_mock` fixture intercepts all LLM calls.
- **Pattern**: For any external service wrapper, inject the client rather than creating it inside the module.

### `ai/__init__.py` must be docstring-only (same as memory/)
- Same circular import risk as `memory/__init__.py`. The chain `ai.__init__ → ai.interpreter → ai.client → openai → httpx` will break if `__init__.py` tries to re-export modules that import from each other.
- **Rule**: Any package whose modules cross-import each other should have a docstring-only `__init__.py`.

### Literal types need `# type: ignore[arg-type]` when value comes from LLM
- When a Pydantic model field uses `Literal["a", "b", "c"]` but the value comes from `dict.get()` (which returns `str`), mypy flags it.
- Pydantic validates at runtime, so the type safety is preserved. Add `# type: ignore[arg-type]` on the construction line.
- Same applies to tests that intentionally pass invalid values to verify `ValidationError` is raised.

### LLM output field names may not match domain model fields
- `QuestObjective` uses `is_complete` but the LLM prompt initially asked for `is_completed`.
- **Rule**: Always read the actual Pydantic model before writing the system prompt. Align prompt JSON keys to actual field names, or (better) hardcode safety-critical values like `is_complete=False` and `status=AVAILABLE` instead of trusting LLM output.
- Quest and Location generators both correctly hardcode safety-relevant fields.

### disposition_change is a signal, not a mutation
- `NPCAgent.respond()` returns `NPCResponse.disposition_change` as a [-2, +2] integer signal.
- The **caller** is responsible for applying it to the NPC object. The NPC Agent never mutates game state.
- Added `Field(ge=-2, le=2)` Pydantic validation on `NPCResponse.disposition_change` to catch LLM hallucinations at the boundary.
- **Pattern**: Any AI module that suggests state changes should return signals, not mutate state directly.

### Model assignment per module
- Fast parsing (Interpreter, NPC Agent): `qwen3.5:4b` — ~50-70 tok/s, low latency
- Quality generation (Narrator, Story Director, Quest/World generators): `qwen3.5:9b` — ~25-35 tok/s, better output
- Temperature: 0.3 for deterministic classification (Interpreter), 0.7 for analysis (StoryDirector, NPCAgent), 0.8 for creative generation (Narrator, Quest/World generators)

### Worktree workflow for feature branches
- `.worktrees/` must be in `.gitignore` before creating worktrees (committed separately).
- Run `uv sync` in worktree to install deps (each worktree gets its own `.venv`).
- Always verify baseline tests pass in fresh worktree before starting work.
- Remove worktree with `--force` if it has untracked files (like `__pycache__`) after merge.
- Delete branch AFTER removing worktree (git won't let you delete a branch used by a worktree).
