# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

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
