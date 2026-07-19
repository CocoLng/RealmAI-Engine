# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

> **État au 2026-07-18.** `main` = **2890 tests verts**, `ruff` clean,
> **`mypy` 0 erreur** sur 334 fichiers. Les **9 chantiers de l'audit
> 2026-06-10 sont clos et mergés**, les 5 critiques compris. Aucune branche
> en attente. Rien n'a été poussé sur le remote.

## Objectif, en trois temps

L'ordre compte : on ne nettoie pas un chantier encore ouvert.

1. **Terminer la Phase 4** — c'est la seule phase encore en cours. Phases 1
   à 3 sont livrées et fonctionnelles de bout en bout.
2. **Prouver que tout est correct** — les vérifications qui n'ont jamais
   tourné contre un vrai bot, et la dette résiduelle identifiée.
3. **Grand nettoyage** — git, docs périmées, code dormant, préparation du
   futur.

-----

# TEMPS 1 — Terminer la Phase 4

Phase 4 = « Polish + ship ». Définition dans `CLAUDE.md` : README avec GIFs +
diagramme d'architecture, CI/CD GitHub Actions, 3+ vraies sessions de jeu,
billet blog / LinkedIn.

### 1.1 — CI/CD GitHub Actions ⭐ à faire en premier

**Pourquoi maintenant** : les trois portes (`pytest`, `ruff`, `mypy`) sont
vertes simultanément pour la première fois de l'histoire du projet. C'est la
fenêtre pour les figer avant qu'elles ne redérivent. C'est aussi le seul item
de Phase 4 qui ne demande ni bot en ligne ni décision de game design.

- [x] Workflow `.github/workflows/ci.yml` : 3 jobs parallèles ruff / mypy
      (sans argument) / pytest, `uv sync --locked`, cache du modèle ONNX
      ChromaDB, actions épinglées (setup-uv par SHA — pas de tag flottant v8)
- [x] Version de Python en CI → **3.12 seul** (venv réel 3.12.8, seule
      version où les portes sont prouvées vertes ; le « dev sur 3.14 » était
      le Python système, pas le venv)
- [x] Badge CI dans le README (+ badge tests 2200+ → 2890+ au passage)
- [x] CI **informative** d'abord — protection de `main` différée au 3.4
- [x] **Prouvé vert sur GitHub** — 3 runs verts consécutifs (2f9755d,
      d90f062, de120f5). Historique du figement : run 1 rouge (`setup-uv@v8`
      n'existe pas — pas de tag majeur flottant, épinglé par SHA) ; run 2
      rouge sur **une vraie course de prod** débusquée par le runner 2 cœurs
      (MOVE payait une 2e génération quand le job prefetch finissait pendant
      sa lecture DB — fix : relecture inconditionnelle après
      `wait_for_started_job`, test déterministe ajouté, 2891e test). Scan de
      secrets du delta avant premier push : propre.

Détail utile : `mypy` se lance **sans argument** (la config `pyproject.toml`
porte déjà la clé `files`). Un `mypy .` re-scannerait des chemins exclus.
Baseline locale du 2026-07-19 : ruff 0.1 s · mypy 0.6 s (334 fichiers) ·
pytest 7.1 s (2890 passed, 1 skipped).

### 1.2 — Vraies sessions de jeu (3+)

**Ceci referme d'un coup les trois vérifications live jamais faites.** Ne pas
les traiter comme des tâches séparées — une vraie partie les couvre toutes.

- [x] **Session 1 — PASS bout en bout (2026-07-19 13:04)**. Flux lobby réel
      complet : Rejoindre → IdentityModal (Thorin, « Vétéran grisonnant ») →
      Nain/Guerrier → stats préset → Athletics+Intimidation → Épée &
      Bouclier/Contrat → récap → Confirmer → roster ✅ → Démarrer →
      **narration d'ouverture 33 s après le clic** (pregen de fond déjà
      prête — baseline d'avril : 438-478 s). Artefact :
      `tasks/logs/2026-04-26-lobby-live-test.txt`. Piloté par script tester
      autonome (scratchpad `c8_lobby_smoke.py`).
- [x] **Session 2 — exploration + latences H8 : PASS, mesures consignées**
      dans `docs/audits/2026-06-10-h8-latency-measurements.md` (section
      « Mesures live 2026-07-19 »). MOVE préfetché = **0 génération**
      (35,7 s de pipeline LLM seulement) ; **MOVE en plein prefetch = une
      seule génération**, le MOVE attend le job (comportement verrouillé le
      matin même par le fix CI). Resume post-restart : 6 s.
- [x] **Session 3 — `/hint` : PASS** (niveau 1 vague → niveau 2 objectifs →
      niveau 3 + cooldown « réessaie dans 5 tour(s) »).
- [x] Consigner les résultats — doc H8 à jour + transcripts commités
      (`tasks/logs/`).

**Trouvailles des sessions live (les 3 vérifications ont payé) :**
- **Bug majeur fixé** — le round-trip DB aplatissait tout item en `Item` de
  base : après CHAQUE save/resume, toute attaque était refusée (« Attack
  requires a weapon », `damage_dice` perdu, AC d'armure fantôme). Invisible
  des 2890 tests (inventaires construits au catalogue, jamais round-trippés).
  Fix `fix(inventory)` + 4 tests, réparation auto des rows tronquées par le
  catalogue. Combat live re-prouvé après fix.
- **Bug UX fixé** — `/hint` niveau 3 affichait `_judge_timeout_` brut et
  consommait le cooldown quand le BeatJudge timeout sous contention Ollama.
  Fix `fix(hint)` + 2 tests.
- Écarts mineurs notés (Temps 3 ou différé) : `equip` répond « not found in
  inventory » pour un item déjà équipé (trompeur) ; `inject_scene` ne prend
  pas l'`action_lock` (écrase le lieu pendant une action en vol — outillage
  de test) ; MCP discord-test peu fiable (wait_for qui rate les réponses,
  flag `online` faux, IDs arrondis en float côté client) — les scripts
  tester autonomes sont la voie ; la doc du skill `discord-live-testing`
  décrit un `create_character` à vues qui n'existe plus (quick-only,
  remplacé par `!test lobby`).

Outillage disponible : skill `discord-live-testing`, MCP `discord-test`
(`discord_status` d'abord pour vérifier que le bot répond), et
`bot/cogs/test_bridge.py` en `TEST_MODE`.

État 2026-07-19 : le smoke C8 n'était **pas exécutable** — le bridge n'avait
aucun accès au vrai lobby (les « lobby helpers » de ses commentaires
n'existaient pas). Ajouté : commande `!test lobby` qui rejoue le vrai
callback `/start_campaign` sur le canal de test (seam `create_session_channel`,
même couture que le driver headless), avec purge du mapping réutilisé et
canal non-supprimable (le 1er essai a déclenché le rollback prod qui a
**supprimé le canal de test Discord** — recréé : `#test-realmai`,
`TEST_CHANNEL_ID` mis à jour dans `.env`, backup `.env.bak-20260719`).
Le MCP discord-test s'est montré peu fiable (wait_for ratés, cache) →
sessions pilotées par script tester autonome (pattern du skill).

### 1.3 — README : GIFs + diagramme

- [ ] Créer `docs/assets/`, y déposer les captures/GIFs (les sessions du 1.2
      sont l'occasion de les enregistrer)
- [ ] Décommenter la section Demo du README (placeholders déjà en place)
- [ ] Diagramme d'architecture

### 1.4 — Billet blog / LinkedIn

- [ ] Rédaction (dernier item, une fois les GIFs disponibles)

-----

# TEMPS 2 — Correctness

### 2.1 — Dette résiduelle réelle

- [ ] **Jet de recharge 5-6 en début de tour.** Seul reliquat technique de
      l'audit. `engine/npc_stat_block.py` budgète désormais `recharge_5_6`
      comme `per_combat`, faute de mieux : sans jet de recharge, une capacité
      « recharge » vaut **1×/combat**. C'est strictement plus sûr que le bug
      d'origine (`uses_remaining=None` = nuke à chaque round), mais ce n'est
      pas la règle. Implémenter demande un hook tour-par-tour dans
      `bot/combat_turn_manager.py`.

### 2.2 — Suivis Beat Progression (non bloquants)

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

### 2.3 — Qualité narrative

- [ ] Monotonie du narrateur à T=0 — R2.repetition remonte encore sur
      `talk`/`look` dans les runs du simulateur (4 alertes souples sur le run
      du 2026-06-10). La garde anti-monotonie du chantier G devrait aider :
      **relancer un run du simulateur pour vérifier** avant d'investiguer plus
      loin. `uv run python -m tests.simulation --mock-llm --max-turns 20`

-----

# TEMPS 3 — Grand nettoyage

### 3.1 — Git ⚠️ destructif, à lancer par l'utilisateur

Les 6 branches de chantier sont toutes ancêtres de `main`.

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

Vérifier avant : `git branch --merged main`.

### 3.2 — Documentation périmée

- [ ] **`CLAUDE.md` annonce « Phase 1 — Game engine without AI [CURRENT] »**
      alors que les phases 1 à 3 sont livrées. Trois phases de retard, dans le
      fichier que **chaque agent lit en premier**. À corriger en priorité : ça
      oriente mal toute session qui démarre. Le README, lui, est juste.
- [ ] `CLAUDE.md` — la « Project Structure » liste des fichiers qui n'existent
      plus (`engine/character.py` devenu un package, `bot/views/combat_view.py`
      renommé `combat_action_view.py`, `world/facts.py`/`npcs.py`/`locations.py`
      jamais créés sous ces noms, `bot/cogs/character.py` documenté avec
      `/create_character` supprimé)
- [ ] Passe de cohérence sur `docs/internal/*.md` — le dernier audit doc date
      du 2026-06-02 et trois chantiers ont été mergés depuis (C, D, G). Vérifier
      en particulier `MEMORY_SYSTEM.md` : la mémoire 4 couches est **enfin
      branchée**, le doc décrivait peut-être l'état débranché.
- [ ] `docs/audits/2026-06-10-system-audit.md` — ajouter un bandeau de tête
      « tous les findings traités au 2026-07-18 » pour qu'il ne soit pas relu
      comme une liste ouverte.

### 3.3 — Code dormant : décisions déjà prises, à ne pas rouvrir

- **`attune_item` / `unattune_item`** (`engine/inventory.py`) — **gardés
  dormants, décision assumée.** Vérifié : `grep -c "requires_attunement=True"`
  → **0**. Aucun objet du catalogue ne requiert d'attunement : ce n'est pas un
  câblage oublié mais une capacité moteur en attente d'objets magiques.
  Correcte, testée, sans coût d'exécution. À câbler avec le catalogue d'objets
  magiques, pas avant.

### 3.4 — Fondations pour la suite

- [ ] `CODE_OF_CONDUCT.md` + `SECURITY.md` — **l'utilisateur les crée** (liens
      déjà câblés dans README + CONTRIBUTING, actuellement morts)
- [ ] Une fois la CI en place : envisager de protéger `main` (PR obligatoire)

-----

# Annexe — Historique clos

## Audit système 2026-06-10 — 9/9 chantiers clos

Rapport source : `docs/audits/2026-06-10-system-audit.md` (65 findings
vérifiés : 5 critiques, ~22 élevés).

| Chantier | Findings | État |
|---|---|---|
| A. Deadlock & intégrité des tours | C1, C3, H1, H14, M9 | ✅ mergé |
| B. Sorts/objets effectifs & morts NPC | C2, C4, H15, H18, H21, H22 | ✅ mergé |
| C. /resume, /settings & persistence | C5, H3-H7, M2, M3, M5 | ✅ mergé 2026-07-18 |
| D. Async pipeline & Story Director | H2, M1, M11, H16, M4 | ✅ mergé 2026-07-18 |
| E. Robustesse génération | H10-H13, M6-M8, M10, M13 | ✅ mergé |
| F. Clamps anti-triche | H20, H19, M12, skill DC | ✅ mergé |
| G. Mémoire 4 couches & locked facts | H9, H17 | ✅ mergé 2026-07-18 |
| H. Porte mypy + qualité | M14 | ✅ clos 2026-07-18 |
| I. Latence | H8 | ✅ mergé |

**Ce que G a débloqué** : c'était le plus gros écart entre `CLAUDE.md` et le
code. La mémoire 4 couches existait mais n'était appelée nulle part en
production. Désormais `bot/pipeline/narrate.py:157-183` construit un préfixe
mémoire (fenêtre glissante + résumés), la cadence de résumé tourne en tâche de
fond, et `memory/context_assembler.py:87` lit le RAG — **ChromaDB n'est plus
write-only**.

## Session 2026-07-18 — merge du retard + porte mypy

C, D et G étaient **terminés mais coincés sur des branches non mergées depuis
le 2026-06-10**, et `tasks/todo.md` ne le disait nulle part — il prétendait au
contraire que seul I était fait. Audit de vérification par 6 agents (21 plans +
24 specs contre le code réel) avant merge.

- Conflits de rebase résolus : `session.py` ×2 (`cancel_for_campaign` déplacé
  **dans** le verrou de teardown), `ai/client.py` (`keep_alive` + `timeout`
  conservés), tests `story_arc` (deux classes additives).
- Bug d'intégration C↔I : la persistance passant hors boucle via
  `asyncio.to_thread`, le SQLite in-memory des tests H8 donnait une base vide
  par thread. Fixture alignée sur la prod (`StaticPool`).
- Chantier H : 470 → **0 erreur mypy**. A débusqué 3 vrais bugs — `_on_confirm`
  plantait sans personnage prévisualisé, 4 commandes du test bridge
  échouaient **en silence** sur un cog supprimé, le sérialiseur MCP itérait
  `.children` sur des composants qui n'en ont pas.
- Nettoyage i18n : kits + récap de fiche traduits, `EDIT_FIELD_LABELS` mort
  supprimé, 2 références obsolètes corrigées. H19 clos.

Détail complet des chantiers antérieurs : `tasks/archive/`.

-----

# Différé — fonctionnalités de jeu (hors dette technique)

- [ ] Backgrounds (Acolyte, Criminal, Noble…) — 2 maîtrises + équipement + trait RP
- [ ] Feats (choix ASI-ou-feat aux niveaux 4/8/12/16/19)
- [ ] Multiclassing
- [ ] Système de langues
- [ ] Tool proficiencies
- [ ] Class features de niveau 2+ (progression complète)
- [ ] Point Buy et 4d6-drop-lowest comme méthodes alternatives de stats
- [ ] Boutique / système achat-vente
- [ ] Catalogue de sorts étendu (>20 sorts actuels) — déclencheur naturel pour
      câbler l'attunement (cf. 3.3)
