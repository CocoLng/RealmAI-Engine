# Lessons Learned

## 2026-06-10 — Combat concurrency: locks, self-cancel, and honest async tests

- **`asyncio.Lock` is non-reentrant — enumerate ALL call paths before adding
  an acquisition to a shared helper.** Commit e73af43 added
  `async with session.action_lock` inside `TurnManager.on_action_resolved`
  with a commit message claiming "both callers release the lock first" —
  true for the button path, false for `ActionHandlerCog._run_pipeline` and
  `test_bridge`, which held the lock around the whole pipeline → same-task
  deadlock on every free-text action in combat. Before locking inside a
  helper, `grep` every caller and state the invariant ("callers must NOT
  hold the lock") in the docstring.

- **A task that cancels a shared task-handle may be cancelling itself.**
  The timeout watcher called `dispatch_action`, whose `_cancel_timeout()`
  cancelled `pending_timeout` — which WAS the running watcher. Self-cancel
  is silent at first: `_must_cancel` only detonates at the next real
  suspension point, and `except Exception` does NOT catch `CancelledError`
  (BaseException since 3.8). Any "fire-then-call-back-into-the-manager"
  task must detach its own handle first (`if handle is
  asyncio.current_task(): handle = None`).

- **Concurrency tests need real suspension points and deadline guards.**
  AsyncMock-only fakes never yield to the event loop, so pending
  cancellations and deadlocks are invisible — the old tests covered
  `_cancel_timeout` in isolation and stayed green through both bugs. Put
  `await asyncio.sleep(0)` inside fake pipeline bodies, drive the real
  entry points (`on_message`, the armed watcher with a shortened
  `_TIMEOUT_SECONDS`), and wrap in `asyncio.wait_for` so a hang fails the
  test instead of freezing the suite.

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

## 2026-05-25 — Audit P0 fixes : 7 patches isolés, suite verte d'un coup

- **Helpers d'audit > corrections inline.** Trois fixes (C2 auto-crit, C7
  mappers, C5 upsert) ont gagné en lisibilité en extrayant un helper privé
  plutôt qu'en inlinant la logique. `_in_melee_range` + `_crit_eligible_helpless`
  rendent la règle SRD lisible d'un coup d'œil ; `_validate_list` /
  `_validate_dict` factorisent le `try/except ValidationError + log + skip`
  qu'il aurait fallu copier dans 4 endroits.

- **Backward compat des conditions de combat via `current_zone is None`.**
  La fix C2 (auto-crit nécessite same-zone) cassait tous les tests legacy qui
  ne définissaient pas de zone. Le filet : si attaquant ET défenseur ont
  `current_zone=None`, on considère qu'on est en mêlée. Aucun test à patcher.

- **`asyncio.Lock` n'est pas ré-entrant — release avant re-acquire.** Pour C3,
  `dispatch_action` libère le lock à la fin de son `async with`, puis
  `on_action_resolved` (appelé après) le prend à nouveau. Pas de deadlock,
  pas de re-entrance. Le pattern marche aussi pour le free-text path qui
  appelle `on_action_resolved` depuis `action_handler.py` après avoir
  libéré son propre lock.

- **Token-budget heuristic : `max(chars/3.5, words×1.5)` au lieu de
  `words×1.3`.** Plus de dépendance externe (tiktoken/transformers), mais
  on biaise vers la sur-estimation — c'est ce qu'on veut quand le risque
  est l'overflow. Les tests de bornes (`<= 30`, `<= 250`) tenaient déjà
  une marge, donc seuls les tests qui asseyaient une valeur exacte ont
  dû être réécrits en propriétés (monotonicity, ≥ ancienne estimation).

- **`advance_turn` doit signaler son échec via `is_active`, pas via
  l'index courant.** Avant : si tous les combattants éligibles étaient
  morts/fui, l'index restait sur un combattant ineligible et
  `check_combat_end` rattrapait après. Maintenant : tracking explicite
  `found_eligible`, sortie immédiate avec `state.is_active = False` et
  `state.end_reason` posé. Les callers (`TurnManager.on_action_resolved`)
  voient `is_active=False` et `_finalize()` proprement.

- **`upsert()` explicite > `try update / except ValueError → save`.**
  Le pattern exception-driven dans `bot/persistence.py` masquait les
  vraies erreurs (si l'update échouait pour une autre raison qu'un
  missing row). Les nouveaux `upsert()` sont un get-then-write
  single-trip qui ne lève que sur les vraies erreurs SQLAlchemy, plus
  un `db_session.rollback()` explicite dans le `except` du caller.

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
