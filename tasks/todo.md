# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

> **État au 2026-07-18.** `main` = 2864 tests verts, ruff clean, mypy 63 erreurs
> en prod / 470 avec les tests (pas de config mypy — c'est le chantier H).
> Les 9 chantiers de l'audit 2026-06-10 sont tous mergés sauf **H**.
> Historique détaillé des chantiers clos : `tasks/archive/`.

-----

## Audit système 2026-06-10 — état des 9 chantiers

Rapport source : `docs/audits/2026-06-10-system-audit.md` (65 findings vérifiés :
5 critiques, ~22 élevés). Les findings ont été regroupés en 9 chantiers à
périmètre disjoint, chacun mené en worktree isolé.

| Chantier | Findings | État |
|---|---|---|
| A. Deadlock & intégrité des tours | C1, C3, H1, H14, M9 | ✅ mergé |
| B. Sorts/objets effectifs & morts NPC | C2, C4, H15, H18, H21, H22 | ✅ mergé |
| C. /resume, /settings & persistence | C5, H3-H7, M2, M3, M5 | ✅ mergé 2026-07-18 |
| D. Async pipeline & Story Director | H2, M1, M11, H16, M4 | ✅ mergé 2026-07-18 |
| E. Robustesse génération | H10-H13, M6-M8, M10, M13 | ✅ mergé |
| F. Clamps anti-triche | H20, M12, skill DC | ✅ mergé (H19 partiel) |
| G. Mémoire 4 couches & locked facts | H9, H17 | ✅ mergé 2026-07-18 |
| H. Porte mypy + qualité | M14 | ⬜ **seul chantier restant** |
| I. Latence | H8 | ✅ mergé |

**Les 5 critiques sont clos.** C5 était le dernier ouvert ; il est tombé avec
le merge de C.

### Merge des chantiers C, D, G (2026-07-18)

Ces trois chantiers étaient terminés mais coincés sur des branches non mergées
depuis le 2026-06-10 — `tasks/todo.md` ne le disait nulle part, et le fichier
prétendait à tort que seul le chantier I était fait. Audit de vérification
mené par 6 agents (plans/specs vs code réel) avant merge.

- [x] **C** — `chantier/save-load-session`, 14 commits. Rebase sur main :
      2 conflits dans `bot/cogs/session.py` (le travail H8 avait touché
      `/resume` et `/end_campaign`). Résolutions : `combat_active` de C
      conservé à côté du `schedule_location_prefetch` de main ;
      `cancel_for_campaign` **déplacé dans** le verrou `action_lock` du
      teardown de C (au lieu de rester après `archive_channel`) — le
      teardown est désormais entièrement sérialisé.
- [x] **Fix d'intégration C↔I** — `tests/bot/test_location_prefetch.py`
      créait son moteur SQLite in-memory sans `StaticPool`. C déporte la
      persistance hors boucle via `asyncio.to_thread` → chaque thread
      recevait sa propre base vide (`no such table: locations`, 3 tests
      rouges). Fixture alignée sur `db.database.create_db_engine`
      (`StaticPool` + `check_same_thread=False`). Commit `12bae6c`.
- [x] **D** — `worktree-chantier-d-orchestrator`, 5 commits. 1 conflit dans
      `ai/client.py` : main ajoutait `keep_alive` (M8), D ajoutait `timeout`
      (H2). Les deux paramètres conservés, ils se composent proprement
      (`timeout` explicite > timeout thinking > défaut client).
- [x] **G** — `feat/memory-coherence`, 7 commits. 1 conflit dans
      `tests/world/test_story_arc.py` : deux classes de tests additives
      (`TestObjectivesCompletedH16` de D, `TestLockedFacts` de G) — les deux
      conservées. `world/story_arc.py` porte bien les deux champs.
- [x] Vérification finale : `uv run pytest` → **2864 passed, 1 skipped** ;
      `ruff check .` clean ; mypy prod 63 erreurs (baseline inchangée).

**Ce que G débloque** — c'était le plus gros écart entre `CLAUDE.md` et le
code : la mémoire 4 couches existait mais n'était appelée nulle part en
production. Désormais `bot/pipeline/narrate.py:157-183` construit un préfixe
mémoire (fenêtre glissante + résumés), la cadence de résumé tourne en tâche
de fond, et `memory/context_assembler.py:87` lit le RAG — **ChromaDB n'est
plus write-only**.

-----

## Chantier restant : H — porte mypy + qualité (M14)

Le seul chantier de l'audit jamais entamé. Volontairement gardé pour la fin :
il touche tout le repo, donc il devait passer après le merge des autres.

État actuel :
- `pyproject.toml` n'a **aucune** section `[tool.mypy]` — mypy tourne sans
  configuration, d'où l'absence de porte de qualité.
- `uv run mypy .` → **470 erreurs** dans 42 fichiers.
- `uv run mypy engine/ bot/ ai/ world/ memory/ db/` → **63 erreurs** dans
  4 fichiers seulement. L'essentiel du bruit vient donc des tests.

- [ ] H1 — ajouter `[tool.mypy]` à `pyproject.toml` (cible : code de prod
      strict, tests plus permissifs)
- [ ] H2 — résorber les 63 erreurs de prod. Les 4 fichiers concernés :
      `bot/cogs/session.py` (unions de type de canal Discord sur
      `end_campaign` / `ArcTrackerManager.remove`), `bot/utils/arc_tracker.py`,
      `bot/views/character_setup_flow.py`, `bot/cogs/test_bridge.py`
- [ ] H3 — décider du régime pour `tests/` (ignorer, ou assouplir par module)
- [ ] H4 — une fois vert, documenter la porte dans `CONTRIBUTING.md`

-----

## Nettoyage identifié (audit plans/specs du 2026-07-18)

Vérification de 21 plans + 24 specs contre le code réel. La quasi-totalité est
implémentée et câblée. Reliquats réels :

- [ ] **`KIT_LABELS` / `get_kit_label` sont du code mort.** `bot/i18n.py:60,214`
      n'a aucun appelant : les options de kit de départ sont construites avec
      le nom anglais brut (`bot/views/character_setup_flow.py:439-442`,
      `label=k.name`), donc les kits s'affichent « Sword & Shield », « Shadow
      Blade »… quelle que soit la langue. La Task 3 du plan i18n
      (`docs/superpowers/plans/2026-04-06-i18n-ui-labels.md`) n'est pas
      réellement livrée.
- [ ] **`EDIT_FIELD_LABELS` (`bot/i18n.py:46`) est mort** — vestige des vues
      d'édition supprimées par la refonte de création de personnage. À retirer.
- [ ] **Message obsolète** : `bot/cogs/inventory.py:55` dit encore « Utilise
      `/create_character` » — commande supprimée en `1a17995`.
- [ ] **Docstring périmée** : `bot/embeds/beat_embed.py:3` référence
      `GameSession.advance_beat_if_ready`, disparu avec le Beat Progression
      Engine.
- [ ] **H19 (reliquat)** — `ai/npc_tactician.py:113-142` valide les noms
      (cible, signature, arme) contre le stat block, mais toujours pas la
      zone/portée, ni que la cible est vivante et du bon camp, ni le budget
      de signature.
- [ ] **`attune_item` / `unattune_item`** (`engine/inventory.py:338,378`) :
      implémentés et testés, zéro appelant en production — l'attunement n'est
      jamais exposé au joueur. Décider : câbler ou retirer.

Non-sujets (vérifiés, à ne pas rouvrir) :
- Le durcissement du prompt arc-generator (Task 1 du plan 2026-04-17) est
  **obsolète** : le prompt a été réécrit en contrat purement narratif
  (« mechanics are generated by the engine — NEVER include them »), donc les
  ancres visées n'existent plus. Le sanitizer Python reste la bonne défense.
- Les cogs d'exploration, `CampaignLauncher`, `quest_generator`,
  `npc_archetypes` ont été **remplacés**, pas oubliés (pipeline texte libre,
  lobby, arc_generator).

-----

## Vérifications live Discord jamais faites

Elles demandent un bot en ligne — impossible en CI, et jamais exécutées depuis.

- [ ] Smoke test du lobby de création de personnage (Wave C8 du plan
      2026-04-26) — artefact attendu `tasks/logs/2026-04-26-lobby-live-test.txt`,
      absent
- [ ] Mesure de latence H8 réelle (inclure un MOVE en plein prefetch et un
      round de combat avec prefetch actif). Le gain annoncé (~57-80 s → <2 s)
      reste **attendu**, jamais mesuré contre un vrai Ollama —
      `docs/audits/2026-06-10-h8-latency-measurements.md:70`
- [ ] `/hint` de bout en bout (3 niveaux + cooldown) avec `DISCORD_TEST_BOT_TOKEN`

-----

## Différé (fonctionnalités de jeu, hors dette technique)

- [ ] Backgrounds (Acolyte, Criminal, Noble…) — 2 maîtrises + équipement + trait RP
- [ ] Feats (choix ASI-ou-feat aux niveaux 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Système de langues
- [ ] Tool proficiencies
- [ ] Class features de niveau 2+ (progression complète)
- [ ] Point Buy et 4d6-drop-lowest comme méthodes alternatives de stats
- [ ] Boutique / système achat-vente
- [ ] Catalogue de sorts étendu (>20 sorts actuels)

### Suivis Beat Progression (non bloquants)

- [ ] Calibrer le seuil de confiance du BeatJudge par beat après une première
      semaine de prod (fixe à 0.7 aujourd'hui)
- [ ] Auditer les arcs déjà en base générés sous l'ancien schéma — backfill
      ponctuel ou régénération
- [ ] Matcher POSSESS — match flou sur les variantes de nom d'objet
      (« old silver key » devrait matcher « silver key »)
- [ ] `last_attempt_action_id` sur `ObjectiveState` n'est jamais peuplé
      (le moteur est sans état par tour) — câbler ou retirer
- [ ] Envisager d'extraire le shadow logger de `engine/beat_progression.py`
      vers `bot/pipeline/` (c'est une préoccupation d'orchestration)

### Autre

- [ ] Monotonie du narrateur à T=0 — R2.repetition remonte encore sur
      `talk`/`look` dans les runs du simulateur (4 alertes souples sur le run
      du 2026-06-10). La mutation d'état par tour atténue, la garde
      anti-monotonie de G devrait aider ; à réévaluer sur un prochain run.
- [ ] CODE_OF_CONDUCT.md et SECURITY.md — l'utilisateur les crée (liens déjà
      câblés dans README + CONTRIBUTING)
- [ ] `docs/assets/` GIFs puis décommenter la section Demo du README
- [ ] Décider d'un workflow CI (repoussé)
