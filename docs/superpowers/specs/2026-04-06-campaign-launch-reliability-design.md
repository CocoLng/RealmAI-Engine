# Campaign Launch Reliability — Design Spec

**Date**: 2026-04-06
**Problem**: Campaign launches with `arc_beats=0 location=none` because Ollama times out during thinking mode, concurrent LLM calls compete for resources, and error handling treats failures as success.

## Context

The campaign launcher already starts AI generation (arc + location) at channel creation, before players create characters. This is the correct design. But three bugs cause it to fail:

1. **httpx timeout (120s) too short for thinking mode** — `think=True` on `qwen3.5:9b` needs 2-4 minutes
2. **Concurrent LLM calls** — arc + location both use `qwen3.5:9b`, Ollama serializes them, doubling wait time
3. **`arc_done` uses `task.done()` not `self.story_arc is not None`** — task completion with exception counts as "done"

## Changes

### 1. Dynamic timeout in `OllamaClient` (`ai/client.py`)

- Default timeout stays **120s** for non-thinking requests
- When `think=True`, use **600s** (10 min) timeout per-request
- Implemented by creating a per-request `httpx.Timeout` in `chat_json` when `think=True`, overriding the client's default

```python
# In chat_json, before the POST:
request_timeout = httpx.Timeout(600.0, connect=10.0) if think else None
response = self._client.post(..., timeout=request_timeout)
```

### 2. Sequential LLM calls in `CampaignLauncher` (`bot/campaign_launcher.py`)

Replace two parallel `asyncio.Task`s with a single task that runs arc then location sequentially:

```
start_background_tasks()
  └─ single asyncio.Task:
       1. arc_gen.generate(theme, player_count, language)  # with retry
       2. world_gen.generate(context_with_arc, location_type, language)  # with retry
```

Benefits:
- No resource contention on Ollama
- Location generation can use arc context for coherence (e.g., `"Campaign: {theme}. Arc villain: {arc.villain_name}"`)
- Single callback to manage

### 3. Retry with backoff

Each LLM call (arc, then location) retries up to **2 times** on `OllamaUnavailableError`:
- Attempt 1: immediate
- Attempt 2: after 5s sleep
- Attempt 3: after 15s sleep

Retry logic lives in `campaign_launcher.py` (not in the client — the client stays thin).

### 4. Mandatory success — block launch on failure

**`_check_ready` change**:
```python
# Before (broken):
arc_done = self._arc_task is None or self._arc_task.done()

# After (correct):
arc_done = self.story_arc is not None and self.current_location is not None
```

**On failure after retries**:
- Set a `_generation_failed` flag
- Send a Discord message: "Ollama est indisponible. Impossible de demarrer la campagne. Verifiez que le serveur Ollama est en cours d'execution, puis relancez avec `/start_campaign`."
- Do NOT call `_launch_campaign()`
- Clean up the launcher from `bot.launchers`

### 5. Remove `_wait_for_arc_with_timeout`

No longer needed. The background task handles everything:
- If players finish first → `_check_ready` sees `arc_done=False`, returns (waits)
- When background task completes → callback calls `_check_ready`, which sees both ready → launches
- If background task fails → sends error message, never launches

The `ARC_WAIT_TIMEOUT` constant and `_wait_for_arc_with_timeout` method are removed entirely.

## Flow Summary

```
/start_campaign
  ├─ Create channel + campaign
  ├─ start_background_tasks()
  │    └─ Task: arc_gen.generate() → world_gen.generate() → _on_generation_done()
  │         (with retry on each step)
  └─ start() → show "Create Character" button
       └─ Players create characters + select gear
            └─ _on_gear_selected() → _check_ready()

_check_ready():
  if not all players GEAR_DONE → return
  if story_arc is None or current_location is None → return (wait)
  if _generation_failed → return (already notified)
  → _launch_campaign()

_on_generation_done():
  if success → set story_arc + current_location → _check_ready()
  if failure → set _generation_failed → send error to channel
```

## Files Modified

| File | Change |
|------|--------|
| `ai/client.py` | Dynamic timeout: 120s default, 600s when `think=True` |
| `bot/campaign_launcher.py` | Sequential gen, retry, mandatory success, remove `_wait_for_arc_with_timeout` |
| `tests/ai/test_client.py` | Test dynamic timeout behavior |
| `tests/test_campaign_launcher.py` (if exists) | Adapt to new sequential flow |

## Verification

1. `uv run pytest` — all tests green
2. `uv run ruff check .` — no lint errors
3. `uv run mypy .` — no type errors
4. Manual test: start Ollama, run bot, `/start_campaign` — verify arc + location generate before launch
5. Manual test: stop Ollama, `/start_campaign` — verify error message appears, campaign does not launch
