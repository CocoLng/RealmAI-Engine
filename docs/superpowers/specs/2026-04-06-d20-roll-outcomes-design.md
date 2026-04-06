# D20 Roll Outcomes — Design Spec

## Problem

The narrator AI receives flat strings like `"Touche — 12 degats (CRITIQUE !)"` with no structured information about how close or decisive a roll was. This limits narrative quality: a nat 20 and a roll that barely beats AC are described with similar intensity. The engine also hardcodes critical/fumble detection inline in `combat.py` rather than centralizing it.

## Goal

Add a 6-tier outcome classification to every d20 roll so the narrator can modulate tone and intensity based on structured data, not string parsing.

## Design

### New types in `engine/dice.py`

#### `RollOutcome` enum (StrEnum)

| Tier | Value | Condition |
|------|-------|-----------|
| `CRITICAL_FAILURE` | `"critical_failure"` | Natural 1 (regardless of total vs DC) |
| `FAILURE` | `"failure"` | Missed by 5+ (margin <= -5) |
| `NEAR_FAILURE` | `"near_failure"` | Missed by 1-4 (margin -4 to -1) |
| `NEAR_SUCCESS` | `"near_success"` | Beat DC by 0-4 (margin 0 to 4) |
| `SUCCESS` | `"success"` | Beat DC by 5+ (margin >= 5) |
| `CRITICAL_SUCCESS` | `"critical_success"` | Natural 20 (regardless of total vs DC) |

**Priority**: nat 1/nat 20 always override margin-based tiers.

#### `D20CheckResult(DiceResult)` model

```python
class D20CheckResult(DiceResult):
    dc: int                  # Difficulty class / AC
    outcome: RollOutcome     # Computed tier
    margin: int              # total - dc (negative = missed)
```

- Inherits all `DiceResult` fields (`expression`, `rolls`, `modifier`, `total`)
- All three new fields are **required** (not optional)
- Passes Liskov: works anywhere `DiceResult` is accepted

#### New function: `roll_check(expression: str, dc: int) -> D20CheckResult`

- Calls `roll(expression)` internally
- Extracts natural d20 from `rolls[0]`
- Computes `margin = total - dc`
- Computes outcome via `_compute_outcome(natural_roll, margin)`
- Returns `D20CheckResult`

#### Private function: `_compute_outcome(natural_roll: int, margin: int) -> RollOutcome`

```
if natural_roll == 1:  return CRITICAL_FAILURE
if natural_roll == 20: return CRITICAL_SUCCESS
if margin <= -5:       return FAILURE
if margin < 0:         return NEAR_FAILURE
if margin < 5:         return NEAR_SUCCESS
return SUCCESS
```

### Existing code stays unchanged

- `roll()` function: unchanged, returns `DiceResult`
- `DiceResult` model: unchanged, no new fields
- Damage rolls (`2d6+3`): continue using `roll()`, never see outcomes

## Impact on `engine/combat.py`

### `resolve_attack()`

- Replace `roll("1d20")` with `roll_check("1d20", target_ac)`
- Advantage/disadvantage: roll two `roll_check()` calls, select best/worst by `.total`
- Replace inline `is_nat_20` / `is_nat_1` with `result.outcome == CRITICAL_SUCCESS` / `CRITICAL_FAILURE`
- Auto-crit on unconscious/paralyzed: override outcome to `CRITICAL_SUCCESS` in `AttackResult` (the `D20CheckResult` itself stays pure)
- Add `outcome: RollOutcome` field to `AttackResult`

### `resolve_death_save()`

- Replace `roll("1d20")` with `roll_check("1d20", 10)` (DC 10 per RAW)
- Nat 20 revive / nat 1 double-failure keyed off `CRITICAL_SUCCESS` / `CRITICAL_FAILURE`

### Spell saving throws

- Replace save roll with `roll_check("1d20", spell_dc)`
- `SpellCastResult` gains `save_outcome: RollOutcome | None`

## Impact on `bot/cogs/combat.py`

Mechanics strings include the outcome tier:

```python
# Before
mechanics += "Touche — 12 degats (CRITIQUE !)"

# After
mechanics += f"Touche ({result.outcome.value}) — {result.damage} degats"
```

## Impact on narrator (`ai/prompts/system_narrator.txt`)

Add structured outcome guidance to the system prompt:

```
The mechanical result includes an outcome tier. Use it to guide your tone:
- critical_failure: Catastrophic. Describe dramatic failure, fumble, or backfire.
- failure: Clean miss. Brief, matter-of-fact.
- near_failure: Agonizingly close. Build tension — "the blade whistles past."
- near_success: Barely made it. Emphasize effort, luck, narrow margins.
- success: Solid hit. Confident, decisive description.
- critical_success: Spectacular. Go big — devastating blow, perfect execution.
```

## Extensibility

When skill checks and saving throws outside combat are added (Phase 2+), they simply call `roll_check("1d20", dc)`. Zero new infrastructure needed.

## Files to modify

| File | Change |
|------|--------|
| `engine/dice.py` | Add `RollOutcome`, `D20CheckResult`, `roll_check()`, `_compute_outcome()` |
| `engine/combat.py` | Use `roll_check()`, add `outcome` to result models |
| `bot/cogs/combat.py` | Include `outcome.value` in mechanics strings |
| `ai/prompts/system_narrator.txt` | Add outcome tier → tone guidance |
| `tests/test_dice.py` | Tests for `RollOutcome`, `D20CheckResult`, `roll_check()`, `_compute_outcome()` |
| `tests/test_combat.py` | Verify `outcome` in `AttackResult`, combat uses new API |

## Verification

1. `uv run pytest tests/test_dice.py` — all new outcome tests green
2. `uv run pytest tests/test_combat.py` — combat tests pass with `roll_check()` integration
3. `uv run pytest` — full suite green
4. `uv run ruff check .` — no lint issues
5. `uv run mypy .` — type checks pass
6. Manual: verify `D20CheckResult.model_dump()` includes `dc`, `outcome`, `margin` alongside inherited fields
