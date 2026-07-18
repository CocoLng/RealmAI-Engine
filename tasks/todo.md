# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

> **État au 2026-07-18.** `main` = **2890 tests verts**, `ruff` clean,
> **`mypy` 0 erreur** sur 334 fichiers. Les **9 chantiers de l'audit
> 2026-06-10 sont clos et mergés**, les 5 critiques compris.
> Historique détaillé des chantiers clos : `tasks/archive/`.
>
> Reste : les vérifications live Discord (bot en ligne requis), le jet de
> recharge 5-6, et les fonctionnalités de jeu différées — voir plus bas.

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
| H. Porte mypy + qualité | M14 | ✅ clos 2026-07-18 |
| I. Latence | H8 | ✅ mergé |

**Les 5 critiques sont clos.** C5 était le dernier ouvert ; il est tombé avec
le merge de C.

### Branches et worktrees à nettoyer

Les 5 branches de chantier sont désormais toutes ancêtres de `main`. Elles et
leurs worktrees sous `.claude/worktrees/` peuvent être supprimés — opération
destructive, laissée à l'utilisateur :

```bash
git worktree remove .claude/worktrees/chantier-c-save-load
git worktree remove .claude/worktrees/chantier-d-orchestrator
git worktree remove .claude/worktrees/chantier-f-anti-cheat
git worktree remove .claude/worktrees/chantier-g-memory
git worktree remove .claude/worktrees/generation-robustness
git branch -d chantier/save-load-session worktree-chantier-d-orchestrator \
  feat/memory-coherence chantier/anti-cheat-clamps fix/generation-robustness \
  worktree-generation-robustness
```

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

## Chantier H — porte mypy + qualité (M14) — CLOS 2026-07-18

Dernier chantier de l'audit. `uv run mypy` passait sans configuration et
sortait 470 erreurs : aucune porte réelle. Désormais **0 erreur sur 334
fichiers** (commit `c9509b3`).

- [x] H1 — `[tool.mypy]` dans `pyproject.toml`. Pas `strict = true` : la
      porte doit être verte aujourd'hui pour attraper les régressions
      demain. `tests.*` exempté via `ignore_errors` (pytest est leur vraie
      porte) plutôt qu'une liste de ~30 codes que personne ne maintiendrait.
      `method-assign` toléré sur `bot/views/*` : assigner `.callback` est
      l'idiome documenté de discord.py pour les composants dynamiques.
- [x] H2 — les 63 erreurs de prod résorbées. **Trois vrais bugs trouvés au
      passage, pas seulement du typage** :
      - `CharacterSetupFlow._on_confirm` plantait (`AttributeError`) sur une
        vue sans personnage prévisualisé, et pouvait passer `None` à
        `on_complete` → garde + test de non-régression.
      - `test_bridge` : `look`/`move`/`search`/`talk` cherchaient
        `ExplorationCog`, supprimé en `5681a6b`. Le lookup renvoyait `None`
        et la branche tombait **en silence** — un test piloté par MCP
        n'obtenait ni réponse ni erreur. Routés vers le pipeline texte libre
        qui les a remplacés (`_exploration_text`), avec 6 tests.
      - `mcp_discord._serialize_components` itérait `.children` sur des
        composants qui n'en ont pas (Components V2) → ignorés proprement.
      Aussi : 68 `# type: ignore` morts supprimés, `arc_tracker` typé pour de
      vrai (il annotait `object` avec le type en commentaire), 10 lambdas de
      callback renommées (mypy compare les noms de paramètres des protocoles).
- [x] H3 — régime `tests/` : exemptés, cf. H1.
- [x] H4 — porte documentée dans `CONTRIBUTING.md`, avec la règle
      « `cast()` plutôt que `# type: ignore`, et surtout pas un `isinstance`
      ajouté pour faire plaisir à mypy — il peut sauter du travail réel en
      silence » (leçon tirée d'une régression commise puis corrigée dans
      cette session même).

-----

## Nettoyage identifié (audit plans/specs du 2026-07-18)

Vérification de 21 plans + 24 specs contre le code réel. La quasi-totalité est
implémentée et câblée. Reliquats réels :

- [x] **`KIT_LABELS` / `get_kit_label` câblés** (`54b4e9c`) — les kits
      s'affichaient « Sword & Shield » quelle que soit la langue. La Task 3
      du plan i18n est maintenant réellement livrée. La `value` de l'option
      reste la clé anglaise canonique (le moteur indexe dessus) ; seul le
      libellé est traduit.
- [x] **Récap de fiche traduit** (`b5b1561`) — corollaire trouvé en câblant
      ci-dessus : l'étape 5/6 affichait du français mais l'étape 6/6 rendait
      encore les clés brutes, donc le même kit changeait de langue entre deux
      écrans. `build_setup_recap_embed` prend désormais `language`.
- [x] **`EDIT_FIELD_LABELS` supprimé** (`bea1bae`) — zéro appelant confirmé.
- [x] **Message obsolète corrigé** (`a89ab76`) — `bot/cogs/inventory.py`
      renvoie vers le bouton **Rejoindre** du lobby au lieu de
      `/create_character`. C'était la seule occurrence côté joueur.
- [x] **Docstring périmée corrigée** (`bea1bae`) — `bot/embeds/beat_embed.py`
      décrit le vrai chemin (`BeatProgressionEngine` → `orchestrator` →
      `action_handler`).
- [x] **H19 (reliquat)** — clos en deux temps. `977fa26` avait déjà posé la
      garde moteur (`engine/npc_ai/boss_brain._validate_decision` : cible
      vivante / bon camp, mêlée same-zone, budget signature, zone adjacente
      pour `move`). `8425ff2` complète côté IA : `_validate_references`
      revalide vivant/fui/camp/portée/budget contre l'état réel avant même
      que le moteur regarde, et `recharge_5_6` reçoit enfin un budget
      (`engine/npc_stat_block.py`) — sans jet de recharge implémenté, le
      `uses_remaining=None` rendait le nuke utilisable chaque round.
      **Reste ouvert** : le jet de recharge 5-6 en début de tour (il faudrait
      un hook tour-par-tour dans `bot/combat_turn_manager` ; en attendant, une
      capacité « recharge » vaut 1×/combat).
- [x] **`attune_item` / `unattune_item`** — décision : **on garde, dormant**.
      Vérification faite : `grep -c "requires_attunement=True"` sur
      `engine/inventory.py` → **0**. Aucun objet du catalogue ne requiert
      d'attunement, donc l'API n'est pas un câblage oublié mais une capacité
      moteur en attente d'objets magiques. Correcte, testée, sans coût
      d'exécution — la retirer serait du churn à refaire au premier objet
      magique. À câbler en même temps que le catalogue d'objets magiques.

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
