# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

## Chantier clos : combat concurrency bugs (audit) — 2026-06-10

Two confirmed concurrency bugs in combat turn handling, fixed TDD-style
(regression tests written and watched fail first).

- [x] **Bug 1 — same-task deadlock on free-text actions in combat.**
  `ActionHandlerCog.on_message` held `session.action_lock` across all of
  `_run_pipeline`, while `TurnManager.on_action_resolved` (called at the end
  of the pipeline) re-acquires the same non-reentrant `asyncio.Lock` →
  every @bot action during active combat hung forever. Same deadlock via
  `test_bridge._handle_narrate`. Fix: `_run_pipeline` now owns the lock
  itself (pipeline + render section extracted to `_process_and_render`)
  and releases it BEFORE the TurnManager handoff; both callers dropped
  their own `async with`.
- [x] **Bug 2 — auto-dodge timeout watcher cancelled itself.**
  `_timeout_watcher` → `dispatch_action` → `_cancel_timeout()` cancelled
  `pending_timeout`, which at that moment IS the running watcher task; the
  CancelledError fired at the first real suspension inside the pipeline
  (not caught by `except Exception`) → "Défense automatique" announced but
  DEFEND never resolved, turn never advanced. Fix: the watcher detaches
  itself (`pending_timeout = None` when it is the current task) before
  dispatching.
- [x] Regression tests: `tests/bot/test_combat_concurrency.py` (3 tests —
  on_message during combat, direct `_run_pipeline` à la test_bridge, and
  watcher end-to-end with short `_TIMEOUT_SECONDS` + a fake pipeline that
  really suspends).
- [x] Verify: `uv run pytest` → 2462 passed, 1 skipped; `ruff check .`
  clean; `mypy .` at pre-existing baseline (362 errors, 0 new).

**Open follow-up (separate audit finding, NOT fixed):** a free-text action
whose LLM pipeline takes >300 s does not pause the combat timeout watcher —
the watcher fires mid-pipeline, posts a spurious "Défense automatique", and
its dispatch then queues on `action_lock` behind the in-flight action
(double resolution). Pausing/cancelling the timer when a free-text action
for the current combatant enters the pipeline needs a re-arm path on
pipeline failure — design before coding.

## Chantier en cours : README + Architecture OSS modernization (2026-06-02)

Goal: bring README.md + ARCHITECTURE.md up to date with reality and to
open-source project norms. Scope confirmed with user: **Full OSS treatment**,
**no CI workflow for now** (static badges only), **placeholder Demo section**.

### Plan
- [x] Accuracy audit of README + ARCHITECTURE claims vs code (6 parallel agents, 14 drifts)
- [x] Fix drift in README.md + ARCHITECTURE.md
  - status line May → June 2026; Phase 3 functional / Phase 4 in progress
  - test count verified (2 219 test functions; "~2 200" kept, accurate)
  - documented the autonomous playthrough simulator (`tests/simulation/`)
- [x] README badges (License · Python 3.12+ · discord.py · Pydantic v2 · Ruff · mypy · tests) — no CI badge
- [x] README Demo section with image placeholders
- [x] README Features / "what you can do" highlight + Commands table (verified vs cogs)
- [x] LICENSE (MIT, 2026 CocoLng) — fixes the broken README link
- [x] CONTRIBUTING.md (uv setup, quality gates, engine invariants, test/sim how-to)
- [~] CODE_OF_CONDUCT.md — **user will create** (links pre-wired in README + CONTRIBUTING)
- [~] SECURITY.md — **user will create** (links pre-wired in README)
- [x] pyproject.toml metadata polish (description, authors, license, urls, classifiers)
- [x] Light refresh of docs/internal/STATE.md (update banner + Phase 3/4 status)
- [x] Verify: test count, `uv lock --check` (111 pkgs OK), doc link/consistency pass
- [ ] Commit (conventional commits, no AI attribution)

### Review

**Audit (6 Explore agents) surfaced 14 drifts — all fixed in docs:**
- engine: `combat_trigger` enum names (PLAYERS/NPCS/BOTH_READY), starter kits 15→14
- ai: npc_generator & npc_tactician are **4b** not 9b; prompts 12→10 system; models 14→8
- db: tables 10→11; **no `schema_version`/ALTER TABLE migrations exist** — `create_all()` only
- bot: views 10→9 (base.py is a base class), embeds 12→13
- the `grep "from ai" engine/` invariant was literally false → rewritten to the accurate one

**Two findings flagged to the user as real code (not just doc) issues:**
1. **No migration system.** `db/database.py` uses `Base.metadata.create_all()` only — adding a
   column to an existing DB silently breaks it. Docs now say so; recommend a real migration story
   for Phase 4 before persisting data that must survive schema changes.
2. **`engine/` imports `ai.models`** (Pydantic contracts) + `boss_brain` type-hints `NPCTactician`.
   No LLM *call* happens in engine (tactician is injected), so the spirit holds — but the literal
   grep-test was wrong. Tech-debt: relocate those contracts to `world/`.

**Added for OSS norms:** LICENSE, CONTRIBUTING.md, README badges + Demo + Features/Commands,
pyproject metadata (license/authors/urls/classifiers/keywords). User owns CoC + SECURITY.

**Did NOT run pytest:** only Markdown + pyproject *metadata* changed (pytest config untouched).
Verified pyproject via `uv lock --check`.

**Follow-ups for the user:** (a) create CODE_OF_CONDUCT.md + SECURITY.md; (b) add `docs/assets/`
GIFs and uncomment the README Demo lines; (c) decide on CI workflow (deferred this round).

## Chantier clos : code-level fixes from the audit (2026-06-03)

The two findings flagged above are now **fixed** (TDD, full suite green).

### Issue #2 — engine/ no longer imports ai/
- New `engine/contracts.py` owns the shared I/O contracts (`InterpretedAction`,
  `MechanicsOutcome`, `PublicEffects`, `TacticalDecision`); they referenced engine's
  `ActionType`, so engine — not `world/` — is their correct home.
- `ai/models.py` re-exports them (39 call sites unchanged); only the 3 engine files were rewired.
- `boss_brain` types its tactician as a local `Tactician` Protocol → zero `from ai`, even under TYPE_CHECKING.
- Guard: `tests/engine/test_no_ai_imports.py` (AST scan, RED→GREEN).

### Issue #1 — real forward-migration story
- New `db/migrations.py::ensure_schema`: `create_all()` + auto `ALTER TABLE ADD COLUMN`
  for any model column an existing table lacks (safe `DEFAULT` for NOT NULL) + `schema_version` stamp.
- `init_db` delegates to it. Tests: `tests/db/test_migrations.py` (6 cases incl. populated-table column add).

### Verification
- `uv run pytest` → 2459 passed, 1 skipped. `ruff check .` → clean. `mypy` on all changed files → clean
  (repo's 362 pre-existing test-typing errors unchanged — none added).

## Chantier clos : Simulator hardening (2026-05-25)

Context: first end-to-end tests of `tests/simulation/` surfaced 5 improvement leads
(memo : `~/.claude/.../memory/project_simulator_test_findings.md`).
This session landed the snapshot enrichment that unblocks rule visibility for the
remaining work.

### Done in this session
- [x] Enrich `_snapshot_from_session` (player_names, inventory_items/equipped,
      locations_known, factions_known, combat_zones, location_connections).
      File: `tests/simulation/__main__.py`.
- [x] Update `_state_view` to wrap snapshot primitives into rule-compatible
      objects (current_location, combat_state.zones, inventory.items).
      File: `tests/simulation/runner.py`.
- [x] Verify: `uv run pytest tests/simulation/ -q` → 108 passed
- [x] Verify: mock-LLM smoke 3 tours → exit_code=0, final_state.json carries
      all new keys

### Wave 1 — Rule precision (cheap, no LLM cost, ~30 min total) — done 2026-05-25 (commit c91d32c)
- [x] Lead 1 — R1.phantom_npc location-aware whitelist
  - `tests/simulation/rules/hard.py:_location_name_words` folds every token
    of `state.locations_known` into the rule's known set.
  - Tests: `test_location_name_not_phantom`, `test_truly_unknown_proper_noun_still_phantom`.
- [x] Lead 2 — Multi-word NPC name canonicalization (first-word match)
  - `tests/simulation/rules/hard.py:_canonical_npc_names` surfaces
    `npc_name.split()[0]` alongside the full name; reused by
    `check_npc_name_drift` in `soft.py` so short forms aren't mis-flagged.
  - Tests: `test_multi_word_npc_first_word_not_phantom`,
    `test_first_word_of_multi_word_name_no_drift`.

### Wave 2 — State mutation so the world comes alive (~2-3 h) — done 2026-05-25 (commit c0461e7)
- [x] Lead 3 — `move(direction)` mutates session + hydrates destination
  - `tests/scenarios/scenario_runner.py:_resolve_direction` + `move` now
    resolve via `exit_aliases` (then raw `connections`) and delegate to
    `bot.world_navigation.change_location`. Falls back to a neutral stub
    embed when the direction maps to nothing or the destination cannot be
    obtained (LocationChangeError).
  - `ScenarioRunner.start_campaign` now wires `session.ollama_client` when
    `ai_enabled=True` so the production hydration path has a client.
  - Tests: `tests/scenarios/test_exploration_move.py` (pre-seed happy path,
    unknown-direction stub, graceful no-Ollama fallback).
- [x] Lead 3.b — `look()` returns an updated observation from current state
  - `look` now reads `session.current_location.description` /
    `arrival_hook`, so the observation reflects mutations from `move`.
- [x] Lead 3.c — feed `runner._history` with `location_known` /
  `moved_this_turn` / `locked_facts` keys so R1.location_mismatch can fire
  - `tests/simulation/runner.py` history-append now derives
    `moved_this_turn` from the snapshot diff and accumulates
    `location_known` across turns; `locked_facts` is a placeholder until
    the session gets a real registry.

### Wave 3 — UX flow coverage — done 2026-05-25 (commits 8e0633e + 3615fdf)
- [x] Lead 4 — Headless `CharacterSetupFlow` driver (commit 8e0633e)
  - `tests/scenarios/headless_character_flow.py:HeadlessCharacterSetupFlow`
    drives every callback through fake `discord.Interaction` objects
    (`send_message` / `edit_message` are `AsyncMock`s). Steps:
    `IdentityModal.on_submit` (sets `_value` on the underlying `TextInput`)
    → `_on_race_selected` → `_on_class_selected` → `transition_to(STATS)`
    → `_on_preset_stats` | `_on_random_stats` → `transition_to(SKILLS)`
    → `_on_skills_selected` → `transition_to(KIT_MOTIV)`
    → `_on_kit_selected` → `_on_motivation_selected`
    → `transition_to(REVIEW)` → `_on_confirm`.
  - Public API: `run_full_flow(...)` for one-shot use plus fluent step
    methods. `from_flow(flow)` classmethod wraps an existing flow built
    elsewhere (consumed by Lead 5).
  - The resulting `Character` is exposed on `driver.character` /
    `kit_name` / `motivation_key`.
  - Tests: `tests/scenarios/test_headless_character_flow.py` (5 cases:
    preset stats, random stats, stepwise API, identity modal path,
    on_complete capture).
- [x] Lead 5 — Headless `SessionCog.start_campaign.callback` (commit 3615fdf)
  - `tests/scenarios/headless_session_flow.py:HeadlessSessionFlow` runs
    the real cog code under four targeted patches:
      - `create_session_channel` → returns `scenario.channel`
      - `SessionCog._pregenerate_campaign_world` → seeds
        `lobby.story_arc` / `lobby.current_location` inline (no LLM)
      - `bot.cogs.session.asyncio.sleep` → no-op (kills the 4.5 s
        launch countdown)
      - `StoryBibleLogger.write_header` → no-op (no Markdown audit
        artifacts under `logs/`)
  - The driver also rewires `channel.send` so the returned message
    exposes async `edit`/`delete`/`pin` (the cog awaits them for the
    lobby refresh + countdown).
  - `add_player` intercepts `inter.response.send_modal` in the
    `on_join` closure, extracts the live `CharacterSetupFlow` from the
    captured modal, and drives it via
    `HeadlessCharacterSetupFlow.from_flow(...)` — the cog's
    `on_setup_complete` closure (DB persistence, lobby roster
    transitions, `refresh_lobby_message`) still fires.
  - Tests: `tests/scenarios/test_headless_session_flow.py` (3 cases:
    happy path with opening crawl + scene assertion, 2-player launch,
    lobby → sessions transition).
  - Verification: `uv run pytest tests/` → 2452 passed, 1 skipped;
    ruff clean; mypy 0 errors on the new files.

### Order & dependencies
- Bundle Leads **1 + 2** as a single PR (both touch the same regex helpers)
- **Lead 3** is independent and the highest-leverage — it stops the agent
  from looping on the same world snapshot
- **Lead 4** is independent of 1-3 (touches Discord views, not rules)
- **Lead 5** consumes Lead 4

Recommended order: **1+2 → 3 → 4 → 5**.

### Deferred follow-up
- [ ] **Narrator monotony at T=0** — R2.repetition flagged a real Narrator
      pattern during the 2026-05-25 testing pass (identical text on T1 and
      T4 of the same `talk` action). Wave 2 mitigates indirectly via state
      mutation per turn, but the root cause (deterministic Narrator at
      `temperature=0` with identical memory context) is unfixed. Revisit
      if simulator runs keep flagging R2.repetition on `talk`/`look`.

## Chantier en cours : Système de Combat D&D 5e

Voir `tasks/combat/README.md` pour l'orchestration complète et `tasks/combat/*.md` pour les fiches détaillées.

### Phase 0 — Bugfix immédiat (shippable dès maintenant)
- [x] Task 00 — Protéger le villain du trivial resolve (filet de sécurité minimal)
- [x] Task 01 — Bloquer MOVE en combat actif (filet, sans auto-convert pour l'instant)

### Phase 1 — Fondations NPC & engine (parallèle Phase 0)
- [x] Task 10 — NPCStatBlock model
- [x] Task 11 — Librairie d'archétypes NPCs
- [x] Task 12 — Zone model
- [x] Task 13 — Conditions SURPRISED et CONCENTRATING

### Phase 2 — Moteur de combat multi-ennemis
- [x] Task 20 — Module d'entrée en combat
- [x] Task 21 — Initiative & surprise (3 cas)
- [x] Task 22 — CombatState multi-enemies + turn mgmt + persistence
- [x] Task 23 — Action economy (Move + Action + Bonus + Reaction)
- [x] Task 24 — Zone movement + opportunity attacks

### Phase 3 — Validation & pipeline
- [x] Task 30 — Validateurs de combat stricts
- [x] Task 31 — ActionPipeline : dispatch combat-aware + auto-convert MOVE→FLEE
- [x] Task 32 — Résolution de FLEE (check DEX)

### Phase 4 — Interprète & générateurs LLM (parallèle)
- [x] Task 40 — Interprète : détection d'intention létale
- [x] Task 41 — World generator : zones + triggers
- [x] Task 42 — Arc generator : villain stat block complet
- [x] Task 43 — Hydration : dispatch par tier d'archétype

### Phase 5 — IA tactique (NPC brains)
- [x] Task 50 — IA scripted pour minions
- [x] Task 51 — IA elite : behavior profiles + signatures
- [x] Task 52 — Boss : LLM tactician
- [x] Task 53 — Legendary actions off-turn
- [x] Task 54 — Phase transitions

### Phase 6 — Discord UI
- [x] Task 60 — Module d'embeds de jets de dés
- [x] Task 61 — Embed "Combat commence"
- [x] Task 62 — Refonte embed d'état combat
- [x] Task 63 — Vues d'actions de combat (boutons)
- [x] Task 64 — Ping de tour + timeout

### Phase 7 — Narrateur & cohérence narrative
- [x] Task 70 — Narrateur : contexte combat
- [x] Task 71 — Prompt narrateur pour transitions de phase

### Phase 8 — Fin de combat & intégration
- [x] Task 80 — Conditions de fin de combat
- [x] Task 81 — Résolution sociale mid-combat (truce)
- [x] Task 82 — Test end-to-end Discord live (**gate de fin**)

### Phase 9 — Documentation
- [x] Task 90 — Rédaction `docs/internal/COMBAT_SYSTEM.md`

## Différé (à faire plus tard)

- [ ] Backgrounds (Acolyte, Criminal, Noble, etc.) — 2 skill proficiencies + équipements + trait RP
- [ ] Feats (choix ASI-ou-feat aux niveaux 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Système de langues
- [ ] Tool proficiencies
- [ ] Class features de niveau 2+ (progression complète)
- [ ] Point Buy et 4d6-drop-lowest comme méthodes alternatives de stats
- [ ] Boutique / système achat-vente
- [ ] Catalogue de sorts étendu (>20 sorts actuels)

## Beat Progression Engine (2026-04-26 — completed)

The Beat Progression refactor (spec: docs/superpowers/specs/2026-04-25-beat-progression-engine-design.md, plan: docs/superpowers/plans/2026-04-25-beat-progression-engine.md) is complete on branch `feature/beat-progression-engine`.

- [x] Phase A — data model augmented (`world/story_arc.py` + auto-migration)
- [x] Phase B — `BeatProgressionEngine` (pure Python, 100% coverage)
- [x] Pre-C fix — extend ObjectiveKind for interact/search/pickup
- [x] Phase C — `BeatJudge` LLM 4b (structured fallback)
- [x] Phase D — bascule + legacy code removed (orchestrator, DriftTracker, DirectorNote)
- [x] Phase E — `/hint` cog (3 levels with DB-backed cooldown)
- [x] Phase F — Arc Tracker enriched (progress bar + checklist)
- [x] Phase G — telemetry + review script + scenarios + lessons

Total: ~21 commits, ~2240 tests pass (engine 100% coverage, matchers ~95%).

Follow-ups (not blocking, can land later):
- [ ] Run live Discord e2e scenario manually with DISCORD_TEST_BOT_TOKEN to validate /hint UX
- [ ] Tune BeatJudge confidence threshold per-beat after first prod week (currently fixed 0.7)
- [ ] Audit existing arcs in DB — those generated under the legacy schema may need a one-time backfill or arc regeneration to use proper objectives
- [ ] Consider re-evaluating engine after BeatJudge passes (currently we trust the judge directly; a second engine.evaluate() call after marking objectives satisfied would be more correct)
- [ ] POSSESS matcher — fuzzy match for item-name variants ("old silver key" should match "silver key")
- [ ] Last_attempt_action_id and completed_at_turn fields on ObjectiveState are never populated (engine is per-turn stateless); decide if they should be removed or wired
- [ ] Consider extracting `engine/beat_progression.py` shadow logger into `bot/pipeline/` since it's an orchestration concern

## Character Creation Redesign (2026-04-26 — completed)

Spec: `docs/superpowers/specs/2026-04-26-character-creation-redesign-design.md`
Plan: `docs/superpowers/plans/2026-04-26-character-creation-redesign.md`

The character creation redesign is complete on `main`. `/start_campaign` now
posts a persistent **lobby** (Rejoindre / Quitter / Démarrer) instead of pre-listing
players via mentions. Each player who clicks **Rejoindre** is taken through a
single auto-modifying setup view (6 steps: identity → race/class → stats → skills
→ kit/motivation → review). The `Alignment` enum has been dropped from the
engine, the ageing `CampaignLauncher` and its 9 view modules have been removed.

- [x] Wave A — engine cleanup (alignment removed, presets/random_stats added)
- [x] Wave B — new UI components (LobbyState, LobbyView, CharacterSetupFlow,
      lobby_embed, character_setup_v2 recap)
- [x] Wave C1 — `bot.lobbies` dict on `RealmBot`
- [x] Wave C2 — `/start_campaign` rewritten to post the lobby
- [x] Wave C3 — `on_launch` callback; lobby → GameSession via
      `_launch_campaign_from_lobby` (story arc, location, opening crawl, scene,
      countdown, party cards, Arc Tracker pin)
- [x] Wave C4 — `/create_character` slash deleted; onboarding goes through the
      lobby
- [x] Wave C5 — obsolete views removed (`character_create_view`,
      `stat_assignment_view`, `skill_selection_view`, `motivation_view`,
      `starter_gear_view`, `start_onboarding_view`, `character_edit_view`,
      `character_edit_flow`, `force_launch_view`) plus `campaign_launcher.py`
      itself, and their dedicated tests
- [x] Wave C6 — `bot/cogs/test_bridge.py` simplified to quick-create only;
      `tests/bot/test_test_bridge_views.py`, `test_views.py`, and the legacy
      pieces of `test_cog_session.py` and `test_cog_character.py` deleted /
      updated
- [x] Wave C7 — scenario test
      `tests/scenarios/test_character_creation_lobby.py` (3 cases: 2-player
      launch, no-ready-player gate, mid-flow cancel)
- [ ] Wave C8 — live Discord smoke test (skipped: bot offline in CI; orchestrator
      to run manually with the running bot)
- [x] Wave D — verification gate

Verification:
- `uv run pytest tests/ -q` → **2164 passed, 1 skipped**
- `uv run ruff check .` → clean
- `uv run mypy engine/ bot/ ai/` → 63 pre-existing errors (3 in
  `bot/cogs/session.py:721,797` for `end_campaign` channel union types — both
  predate this redesign; the rest in `bot/utils/arc_tracker.py`,
  `bot/views/character_setup_flow.py`, `bot/cogs/test_bridge.py` and also
  predate Wave C). My session.py edits introduced **0 new errors**.
- Audit grep `alignment|Alignment` in `*.py` → 0 hits (only the false positive
  in `ai/prompts/system_npc_agent.txt` line 32 — English word, not the engine
  concept)
- Audit grep `CampaignLauncher|campaign_launcher` in `*.py` → 2 docstring
  references in `bot/lobby_state.py` (pre-existing from Wave B; instructions
  forbade touching that file)

## Native Objectives Generation (2026-04-27 — completed)

The Arc Generator now emits native `objectives[]` per beat with calibrated
gates (MIN_REVEALS / MIN_DISPOSITION / HAS_ITEM / FLAG_SET) — eliminating
the need for the legacy `CompletionTrigger` migration on freshly generated
arcs. The migration in `world/story_arc.py` stays in place for backward-
compat with arcs that pre-date this change and live in the DB.

**Files changed:**
- `ai/objective_recipes.py` (NEW) — per-(encounter_type, subtype) recipe
  table + `scaffold_objectives()` deterministic builder
- `ai/arc_generator.py` — added `_sanitize_beat_objectives`,
  `_clean_objective_list`, `_sanitize_gate`, `_make_objective_id`,
  `_ensure_boss_defeat_objective`. All wired into the existing
  `_sanitize_arc_data` pipeline.
- `ai/prompts/system_arc_generator.txt` — replaced the
  `completion_trigger` schema section with a full native-objectives schema
  + per-(type, subtype) playbook with concrete JSON examples.
- `tests/ai/test_objective_recipes.py` (NEW) — 27 tests
- `tests/ai/test_arc_generator.py` — 27 new tests in three classes
  (TestNativeObjectivesSanitization, TestEndToEndNativeObjectives,
  TestNoLegacyMigrationNeeded)

**Key invariants enforced by the sanitizer:**
1. Every beat ends up with a non-empty, valid `objectives[]` list.
2. Boss beats always carry a `defeat <villain_name>` objective (injected
   if the LLM forgot or named the wrong target).
3. social/negotiation beats always have a MIN_REVEALS gate (and a
   secondary MIN_DISPOSITION objective).
4. puzzle/ritual beats always have a HAS_ITEM gate.
5. Invalid kinds, gates, or values are dropped/coerced silently with
   info-level log entries.
6. `judge_rubric` and `player_visible_hint` are backfilled from the
   recipe when the LLM left them empty.

Verification:
- `uv run pytest tests/ -q` → **2221 passed, 1 skipped** (+57 new tests)
- `uv run ruff check .` → clean
- `uv run mypy ai/ engine/ world/` → clean (0 errors)
