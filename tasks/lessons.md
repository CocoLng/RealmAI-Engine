# Lessons Learned

## 2026-04-26 — Beat progression: single decision point

- **One source of truth beats three "smart" paths.** Three concurrent decision paths
  (deterministic + location + LLM fallback) competed without coordination,
  causing both blocks and double-advances. Replacing them with a single
  `BeatProgressionEngine.evaluate()` that returns one of {ADVANCE, STAY, NEEDS_JUDGE}
  fixed both bugs at once.

- **LLM fallback ≠ silent overrider.** The legacy 0.85 confidence threshold was
  a constant disconnected from the beat. The new `BeatJudge` takes a per-beat
  `judge_rubric` and returns a structured response — confidence becomes a
  signal, not an arbiter, and the threshold lives in the calling policy
  (orchestrator), not in the LLM call.

- **Whitelist objectives from LLM.** When a 4b model returns a list of strings
  (objective ids), it WILL hallucinate ones that weren't in the input. Always
  intersect with the input whitelist after parsing. Tested via
  `test_judge_strips_hallucinated_objective_ids`.

- **Snapshot state before legacy mutation when running shadow comparisons.**
  Phase B shadow mode initially logged the post-mutation arc state, making
  every advance look like a divergence. Always snapshot before the legacy
  code runs so the shadow evaluates the same input. Caught by the final
  Phase A+B code review.

- **Structured fields beat string-scanning for anti-cheat.** Phase B's first
  DEFEAT matcher scanned narrative summary for "vaincu"/"defeated" strings —
  but the production combat code writes those into `outcome_facts`, not
  `summary`. Fix: add a structured `target_defeated: str | None` field to
  `MechanicsOutcome` and have the combat code populate it. Eliminates
  i18n drift, false positives on substring (e.g. "ar" matching "bear"),
  and tests-against-prose-the-system-never-emits.

- **Don't introduce new game resources just to gate a feature.** The /hint
  level 3 originally considered consuming an Inspiration die or similar
  D&D resource — but Inspiration isn't implemented. Cooldown (5 turns
  after use) is simpler, doesn't require new infra, and naturally
  pencils in the "explore between hints" rhythm.

- **3-state decision (ADVANCE/STAY/NEEDS_JUDGE) lets you defer LLM cost
  to ~20% of turns.** The deterministic engine handles the 80% of clear-cut
  cases (no LLM call). The judge fires only on partial matches. With
  qwen3.5:4b (1-2s) on the rare path, average per-turn latency is unchanged.

- **Phase migration in 3 stages (modèle → shadow → bascule) catches
  surprises early.** Phase A (data model only, no behavior change) was
  100% safe. Phase B (engine + shadow logging) revealed the off-by-one
  bug AND the unmappable trigger types BEFORE Phase D's destructive
  cutover. Without shadow mode, Phase D would have shipped both bugs to
  prod.

## 2026-04-27 — Native objectives generation: prompt + Python safety net

- **Prompt + sanitizer is more robust than prompt-only.** Telling the LLM
  "emit calibrated `objectives[]`" works most of the time but qwen3.5:9b
  occasionally drops `gate`, picks an invalid `kind`, or omits the entire
  list. The sanitizer in `ai/arc_generator.py` (`_sanitize_beat_objectives`)
  drops bad entries, coerces gate values to the right type, fills missing
  ids — and when nothing valid remains, scaffolds a recipe-based fallback
  from `ai/objective_recipes.py`. Result: every beat ships with rich,
  valid objectives regardless of LLM quality. Tested via
  `TestNativeObjectivesSanitization` (~17 cases).

- **Calibration belongs in code, not prompt.** The per-(encounter_type,
  encounter_subtype) gate calibration lives in `_RECIPES` so it stays
  testable and consistent across regenerations. The prompt shows examples
  derived from the same table. If the table changes, the prompt examples
  should be regenerated to stay aligned (mostly automatic if you stick to
  the playbook section's structure).

- **Boss invariant is a Python guarantee, not a prompt prayer.**
  `_ensure_boss_defeat_objective` injects a DEFEAT villain_name objective
  into the boss beat if the LLM forgot. We trust this MORE than the prompt
  because shipping an unwinnable arc is a P0 bug. The prompt still asks
  for it (so the LLM produces flavorful descriptions) but Python enforces.

- **Legacy migration stays for DB read-back, but new generations bypass.**
  `StoryArc._migrate_legacy_completion_triggers` was the only thing
  keeping arcs functional after the data model change. After this refactor
  it's a backward-compat tool for old DB rows only — fresh arcs ship with
  native objectives and never trigger the migration path. Verified by the
  regression test `TestNoLegacyMigrationNeeded`.

- **YAGNI on advance_threshold default.** When the LLM picks `m_of_n` but
  forgets the threshold, defaulting to `ceil(N/2)` keeps the beat playable
  without inventing additional knobs. Recipe-driven defaults handle the
  rest.
