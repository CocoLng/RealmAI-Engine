# Narrative Display Cleanup — Implementation Plan

**Goal:** Remove mechanics field leaks (e.g. `disposition: +1`) from player-facing embeds, replace with a discreet footer of public-only effects, and dump full mechanics (hidden stats included) to the existing log file.

**Architecture:** Add a `PublicEffects` Pydantic model populated at resolution time. `MechanicsOutcome.summary` becomes dev-only (narrator + logs). `narrative_embed` drops its `Mecaniques` field and uses a footer from `PublicEffects`. A single `logger.info("ACTION complete", extra={...})` call at pipeline exit carries everything in JSON-on-one-line via a custom formatter.

**Tech stack:** Pydantic v2, discord.py, stdlib logging.

---

## File plan

| File | Action |
|---|---|
| `ai/models.py` | Add `PublicEffects` model + `public_effects` field on `MechanicsOutcome` |
| `bot/action_pipeline.py` | Populate `public_effects` in `_resolve_*`; add JSON debug log at end of `process`; carry `public_effects` through `ActionPipelineResult` |
| `bot/embeds/narrative_embed.py` | Drop `Mecaniques` field, add footer built from `PublicEffects` |
| `bot/cogs/action_handler.py` | Pass `public_effects` to embed builder (all render paths) |
| `bot/logging_config.py` | Custom formatter that appends JSON-serialized `extra` payload for the file handler |
| `tests/test_embeds.py` | Rewrite `TestNarrativeEmbed`: no field, footer assertions |
| `tests/test_public_effects.py` | New unit tests for `PublicEffects.to_footer_text()` |

---

## Task 1 — PublicEffects model & MechanicsOutcome field

- [ ] Add `PublicEffects` to `ai/models.py` with fields: `hp_delta: dict[str, int]`, `items_gained: list[str]`, `items_lost: list[str]`, `gold_delta: int`, `location_change: str | None`, `xp_gained: int`, `level_up: bool`, all defaulting to empty/zero/None/False.
- [ ] Add method `to_footer_text() -> str | None` returning `None` when empty, else a compact ` • `-separated line (`❤ -5`, `+ Potion`, `- Torche`, `+3 po`, `→ Crypte`, `+120 XP`, `⬆ LEVEL UP`).
- [ ] Add `public_effects: PublicEffects = Field(default_factory=PublicEffects)` to `MechanicsOutcome`.
- [ ] Run `uv run pytest tests/test_public_effects.py -v` (new test file, see Task 6).

## Task 2 — Populate public_effects in resolvers

- [ ] In `bot/action_pipeline.py::_resolve_talk` — leave `public_effects` empty (dialogue has no player-visible mechanical effect; the disposition shift is the *hidden* stat we want to suppress).
- [ ] In `_resolve_talk` fallback branches and MOVE/PICKUP paths, populate accordingly:
  - MOVE success → `public_effects = PublicEffects(location_change=dest.name)`
  - PICKUP success → `public_effects = PublicEffects(items_gained=[item_name])`
- [ ] For combat trivial-kill path (around line 630) — leave empty; combat handler (separate cog) has its own HP feedback via combat embed.
- [ ] Remove the `(disposition: {+d})` suffix from the TALK `summary` (line 861-863) — disposition never appears in summary either, since summary is also logged but more importantly still passed to narrator/context; narrator prompt contains disposition separately.

## Task 3 — Extend ActionPipelineResult + pass through

- [ ] Add `public_effects: PublicEffects = Field(default_factory=PublicEffects)` to `ActionPipelineResult` in `bot/action_pipeline.py`.
- [ ] Populate it in the single `return ActionPipelineResult(...)` at line ~321 from `outcome.public_effects`.
- [ ] Keep `mechanics_text=outcome.summary` for now (unused by embed after Task 4, but kept for logs/backwards compat).

## Task 4 — Embed refactor

- [ ] Rewrite `bot/embeds/narrative_embed.py::build_narrative_embed`:
  - Signature: `build_narrative_embed(narrative: str, *, public_effects: PublicEffects | None = None, tone: str = "dramatic", footer_override: str | None = None) -> discord.Embed`
  - No `add_field` call.
  - If `footer_override` → use it. Else if `public_effects` and `to_footer_text()` returns non-None → set as footer. Else no footer.
- [ ] In `bot/cogs/action_handler.py`:
  - `_render_success` → `build_narrative_embed(narrative=result.narrative, public_effects=result.public_effects, tone=result.tone)`
  - `_render_unknown` → `build_narrative_embed(narrative=result.refusal_narrative, tone=result.tone, footer_override=f"⚠️ {result.field_name}: '{result.raw_value}' introuvable.")`

## Task 5 — Full-mechanics logging

- [ ] In `bot/logging_config.py`, add a `_JsonExtraFormatter(logging.Formatter)` that checks `record.__dict__` for an `"extra_payload"` key and appends ` | <json>` to the formatted message if present. Use it for the **file handler only** (console stays clean).
- [ ] At the end of `ActionPipeline.process` → `_continue_from_resolution`, just before returning `ActionPipelineResult`, emit:
  ```python
  logger.info(
      "ACTION complete campaign=%s actor=%s action=%s",
      self.campaign_id, interpreted.actor_name, interpreted.action_type.value,
      extra={"extra_payload": {
          "mechanics_summary": outcome.summary,
          "player_intent": outcome.player_intent,
          "outcome_facts": outcome.outcome_facts,
          "public_effects": outcome.public_effects.model_dump(),
          "narrative": narration.narrative,
          "tone": narration.tone,
      }},
  )
  ```

## Task 6 — Tests

- [ ] New file `tests/test_public_effects.py`: test `PublicEffects()` footer → None; HP + item + location footer contains markers; level_up → `⬆ LEVEL UP`.
- [ ] Rewrite `TestNarrativeEmbed` in `tests/test_embeds.py`:
  - Remove `test_mechanics_field`, `test_field_count` (narrative embed now has 0 fields).
  - Add `test_no_fields`, `test_no_footer_when_effects_empty`, `test_footer_when_public_effects`, `test_footer_override`.
  - Keep tone color tests (update signatures: drop `mechanics=` arg, add `public_effects=None`).

## Task 7 — Verification

- [ ] `uv run pytest tests/test_embeds.py tests/test_public_effects.py -v` → all pass.
- [ ] `uv run pytest` → full suite green.
- [ ] `uv run ruff check bot/ ai/ tests/test_embeds.py tests/test_public_effects.py`.
- [ ] `uv run mypy bot/ ai/`.
- [ ] `rg 'Mecaniques' bot/ tests/` → only occurrences allowed are in summary strings, not in embed builder.
- [ ] Commit: `feat(ui): epure affichage joueur + logs mecaniques complets`.
