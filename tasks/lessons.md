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
