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
