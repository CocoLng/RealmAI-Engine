# TODO — RealmAI-Engine

Commit at the end of a phase, do not co author claude.

> **État au 2026-07-20 (fin de session d'audit).** `main` = **2991 tests
> verts**, `ruff` clean, **`mypy` 0 erreur** sur 344 fichiers. Les 9
> chantiers de l'audit 2026-06-10 sont clos et mergés. L'audit specs↔code du
> jour (24 specs / 21 plans / 36 fiches) est clos aussi — voir **TEMPS 4**
> pour ce qui reste ouvert.
>
> **Poussé et nettoyé** : `main == origin/main`, CI verte sur le HEAD.
> Plus aucun worktree ni branche de chantier — l'arbre est à `main` seul.

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

- [x] **Diagramme d'architecture** — flowchart Mermaid rendu nativement par
      GitHub, remplace l'ASCII (+ statut juillet, compteurs réels ~2 890/63,
      correction du piège `mypy .` dans Quality gates)
- [ ] Captures/GIFs → `docs/assets/` puis décommenter la section Demo —
      **à ta main** (il faut un client Discord graphique). Checklist de
      capture, tout est prêt :
      1. Le bot tourne en TEST_MODE (sinon :
         `TEST_MODE=true uv run python -c "from bot.bot import run_bot; run_bot()"`)
         et la campagne « Dark Fantasy » (Thorin, combat au round 2 contre le
         Brigand) est reprise via `/resume` dans #test-realmai — ou lance une
         partie neuve avec `/start_campaign` pour filmer le vrai lobby.
      2. Shot 1 : `/start_campaign` → lobby → 🎭 Rejoindre → flux de création
         (modal, race/classe, stats, compétences, kit) → récap → Démarrer.
      3. Shot 2 : une action libre `@bot j'examine l'autel` → embed de
         progression 6 phases → narration + mécanique brute.
      4. Shot 3 : un tour de combat (boutons Attaque/Sort/Défense/Fuite).
      5. Shot 4 : le message épinglé Arc Tracker (`📖 Chapitre 1 …`).

### 1.4 — Billet blog / LinkedIn

- [ ] Rédaction (dernier item, une fois les GIFs disponibles)

-----

# TEMPS 2 — Correctness

### 2.1 — Dette résiduelle réelle

- [x] **Jet de recharge 5-6 : implémenté (2026-07-19).** Dans le moteur, pas
      dans le bot : `engine.combat.advance_turn` roule le d6 SRD au début du
      tour du porteur, là où les points légendaires se rechargent (5+ →
      l'usage revient, jamais re-roulé chargé, jamais cumulé ; cue ⚡ via
      `pending_legendary_summaries`). 5 tests
      (`tests/engine/combat/test_recharge.py`).

### 2.2 — Suivis Beat Progression (non bloquants)

- [ ] Calibrer le seuil de confiance du BeatJudge par beat — **différé tel
      quel** : exige une semaine de données de prod qui n'existent pas encore
- [x] Audit des arcs en base : **rien à migrer** — les 2 arcs stockés sont au
      schéma natif (11/11 beats avec `objectives[]`, 0 trigger legacy)
- [x] Matcher POSSESS flou — max des scores `_fuzzy` sur l'inventaire
      (« old silver key » ⊃ « silver key » → 1.0 par containment)
- [x] `last_attempt_action_id` **retiré** (jamais peuplé par design — moteur
      sans état par tour ; les arcs persistés portant la clé chargent sans
      erreur, test à l'appui)
- [ ] Extraire le shadow logger de `engine/beat_progression.py` vers
      `bot/pipeline/` — **différé** : refactor optionnel sans impact
      fonctionnel, à faire à l'occasion d'un chantier pipeline

### 2.3 — Qualité narrative

- [x] **Monotonie du narrateur : réglée côté narrateur** (run réel Ollama du
      2026-07-19, 16 tours, `tests/simulation/runs/20260719_180539__seed581176`).
      Décomposition : les 5 alertes R2 restantes tombent TOUTES sur des tours
      à 0.0 s — des `look` répétés servis sans LLM (réaffichage déterministe
      de la description statique du lieu), pas le narrateur. **Zéro
      répétition sur les tours narrés** (talk/move/improvise, 25-60 s) — la
      garde du chantier G fonctionne. (Le mock-llm ne peut pas juger : ses
      narrations canned déclenchent R2 par construction.)
      Suivis mineurs notés, non bloquants : le checker R2 compte le re-look
      statique comme répétition (faux positif à affiner, ou varier les
      re-look) ; 1 alerte R1.phantom_npc isolée (T09) à surveiller sur les
      prochaines vraies parties ; le brainstorm du Story Director en
      think=True sature ses 2048 tokens (~112 s perdues puis fallback propre
      single-call — envisager think=False ou un budget dédié).

**Signaux du check du 2026-07-19 soir** (bot live 20h-21h48 : 0 ERROR) :
- ~~Fallback systématique du Director brainstorm (~112 s/cadence)~~ **RÉGLÉ
  le 2026-07-20** (`perf(director)` b6674f6). Racine prouvée : think=True
  avec num_predict=2048, or num_predict plafonne thinking + contenu
  confondus — la trace du 9b saturait le cap à chaque cadence (15/15),
  contenu vide, fallback. Fix : think=False + `BRAINSTORM_NUM_PREDICT=1024`.
  À confirmer sur la prochaine session live : plus aucun « brainstorm
  failed » dans les logs et cadence Director raccourcie d'~112 s.
- ~~Collection ChromaDB de la campagne live inexistante (RAG lu à vide)~~
  **RÉGLÉ le 2026-07-20** (`fix(rag)` 33d1779). Racine : les générateurs
  acceptent un SemanticIndexer mais AUCUN site prod ne le passait
  (game_session créait même le NPCGenerator avant l'indexer) — la
  collection n'était créée que par la 1ʳᵉ note du Director, d'où les 5
  « not found » des premières interactions. Câblé sur les 5 sites : pregen
  lobby (arc + lieu de départ), create_ai_services (NPCGenerator),
  generate_destination (MOVE/prefetch), npc_prefetch, TALK lazy. 8 tests.
  **Suivi ouvert** : les campagnes créées AVANT ce fix (dont la campagne
  de test 260558f3) restent sans corpus initial — leur contenu est en DB,
  pas dans ChromaDB. Si on veut les rattraper : backfill au `/resume`
  (réindexer beats/lieux/NPC depuis la DB), non fait — à trancher.
- La garde anti-monotonie **détecte et re-tente en prod** (5× « NARRATION
  guard: repetition … retrying once » pendant les rounds de combat) —
  confirmation live du verdict simulateur.
- Dépréciation amont `discord.py` : `label` → composant `discord.ui.Label`
  (3 warnings pytest, prod utilise aussi `.label` dans le bridge et les
  vues). Rien de cassé, migration à planifier avant discord.py 3.x.
- Hygiène : la suite pytest écrit des `logs/realm_*.log` réels (fixtures
  d'échec incluses) — ça a piégé deux diagnostics aujourd'hui. Rediriger le
  FileHandler vers tmp_path sous pytest.
- ~~**Combat sans joueur = boucle infinie d'auto-dodge**~~ **RÉGLÉ par une
  décision de design (2026-07-19 soir)** : **le jeu ne joue plus jamais à
  la place du joueur.** L'auto-dodge sur timeout est supprimé (et le
  disjoncteur intermédiaire avec) — au bout de 5 min le watcher poste UN
  rappel (« c'est toujours ton tour ») puis attend indéfiniment : un tour
  peut rester ouvert 5 min ou 8 h, c'est voulu. **Sous TEST_MODE en
  revanche, l'auto-Défense est conservée** — un harnais de test a besoin
  d'un combat qui avance tout seul, ni bloqué ni en attente infinie
  (précision du 2026-07-20). La machinerie
  pause/rearm/stale-guard du watcher est conservée (elle protège le rappel
  des faux positifs mid-pipeline). Constat déclencheur : 49 esquives
  automatiques en 1 h 45 sur un combat de test sans joueur, 15 Story
  Directors traînés (~26 min de GPU). Tests adaptés
  (`TestTurnReminder`, concurrency bugs 2-4 reformulés). Le bot de test est
  arrêté ; l'état est persisté, la campagne reprend au `/resume`.

### 2.4 — Porte de cohérence câblée (2026-07-20)

- [x] **Noyau partagé** : `memory/coherence_rules.py` — 11 règles de cohérence
      (2 bloquantes jour 1 : `R1.npc_status`, `R1.zone_violation` ; 9
      observées), définies une seule fois, consommées par le simulateur
      (`tests/simulation/`) et la production (`bot/pipeline/`). La revue
      finale a déclassé `R1.item_use_without_owning` et
      `R1.locked_fact_violation` en OBSERVE (faux positifs prouvés :
      armes des PNJ, confirmations de faits matchant le regex de
      négation) — mitigations posées quand même : attribution de sujet,
      skip des faits auto-négateurs, exclusion des faits verrouillés le
      tour même, grâce du tour de mise à mort (`freshly_dead`).
- [x] **Adaptateurs simulateur** : `tests/simulation/rules/hard.py` et
      `soft.py` réécrits en adaptateurs minces du noyau (mapping state
      simulateur → `CoherenceSnapshot`, violations → `IncoherenceAlert`) ;
      `drift.py`, `rules/__init__.py` et `checker.py` inchangés. Suite
      simulation verte sans aucun ajustement de fixture.
- [x] **Orchestration** : `memory/narration_guard.py::check_narration()`
      exécute les 11 règles et retourne un `GuardVerdict`
      (blocking/observed). Câblée dans `call_narrator`
      (`bot/pipeline/narrate.py`).
- [x] **Politique retry → template** : violation bloquante → un retry
      correctif (contraintes dérivées des `expected`), re-check, puis
      `narrator.template_narration` (tier 3 sans LLM) si encore violé —
      jamais d'incohérence bloquante publiée. Chemin nominal : zéro appel
      LLM supplémentaire.
- [x] **Locked facts de beats** : `BeatEffects.locked_facts` +
      `narrative_hint` verrouillés à la complétion du beat via
      `world/story_arc.py::append_beat_locked_facts` (idempotent), appelé
      par `_apply_beat_effects` ; bloc `[LOCKED FACTS]` plafonné à 15
      lignes, morts d'abord.
- [x] **Télémétrie de promotion** : violations OBSERVE **et** BLOCK loggées
      sur le logger dédié `memory.coherence`. C'est la base de données pour
      promouvoir des règles OBSERVE → BLOCK.
- [x] **Vérification** : `uv run pytest -q` → 3032 passed, 1 skipped (~60
      tests nouveaux). `uv run ruff check .` → all checks passed.
      `uv run mypy .` → no issues found in 355 source files. Câblage
      prouvé : 4/4 symboles appelés hors tests (`check_narration`,
      `build_coherence_snapshot`, `append_beat_locked_facts`,
      `template_narration`). Mergé dans main (`af5d711`), gates
      re-vérifiés sur le résultat mergé.

**Suivi ouvert** : après quelques sessions réelles (~10), dépouiller les logs
`memory.coherence` et statuer sur la promotion de `R1.phantom_npc`,
`R1.hp_mismatch`, `R1.location_mismatch`, `R1.item_use_without_owning`,
`R1.locked_fact_violation` (faible taux de faux positifs confirmé par
relecture des extraits loggés). Surveiller aussi le taux de succès du retry
correctif dead-NPC (contrainte plus terse qu'avant) et le faux positif
possible de `R1.npc_status` sur la personnification près d'un cadavre.
Cleanup candidat : `find_dead_npc_violations` et `run_rules` n'ont plus
d'appelant prod (API gardées pour compat) ; `known_locations` du snapshot
prod = lieu courant + sorties (déviation documentée vs spec « tous les
lieux ») — à trancher avant toute promotion de `R1.location_mismatch`.

-----

# TEMPS 3 — Grand nettoyage

### 3.1 — Git ✅ fait le 2026-07-20

8 worktrees retirés et 9 branches supprimées, chacune vérifiée ancêtre de
`main` par `git merge-base --is-ancestor` avant suppression (pas seulement
par le listing de `git branch --merged`). `git branch` ne rend plus qu'une
ligne. Résidus locaux non versionnés purgés au passage : `.coverage` stale,
`.worktrees/` vide, tous les `__pycache__`.

Reste, à ta main :
- [x] `origin/copilot/write-code-of-conduct-and-security` — supprimée le
      2026-07-20 (`git push origin --delete`).
- [x] `logs/` purgé le 2026-07-20 — **avec une perte** : la purge a été
      exécutée en `rm -rf logs/*` sans relire cette entrée, donc
      `beat_progression.jsonl` (télémétrie shadow BeatJudge, partielle) et
      `logs/campaigns/` (audit story-bible des sessions passées) sont
      partis avec les `realm_*.log`. Irrécupérable (pas de snapshot ni de
      Time Machine). Impact : historique perdu, aucun impact fonctionnel —
      les fichiers se recréent aux prochaines sessions ; la calibration
      BeatJudge (TEMPS 2) repart de zéro sur les données à venir.
- [ ] `.env.bak-20260719` — backup de `.env` (contient des secrets). Laissé
      en place volontairement : à supprimer toi-même une fois sûr que le
      `TEST_CHANNEL_ID` courant est le bon.

### 3.2 — Documentation périmée

- [x] **Statut de phase de `CLAUDE.md`** — corrigé : Phase 4 marquée `[CURRENT]`,
      phases 1-3 `✅ shipped`. (Vérifié le 2026-07-20 : c'était déjà fait, la
      case était restée ouverte.)
- [x] `CLAUDE.md` — « Project Structure » : les 60 chemins de l'arbre ont été
      vérifiés un par un le 2026-07-20, **zéro manquant**. Ajoutés au passage :
      `engine/combat_trigger.py`, `engine/starter_gear.py`, `bot/combat_truce.py`
      (vivants en prod, absents de l'arbre).
- [x] **Cadence du Story Director tranchée (2026-07-20)** — le code fait
      `% 6` + 3 déclencheurs (fin de combat, drift, `/story_catch_up`) alors que
      `CLAUDE.md`, `README.md` et `ARCHITECTURE.md` annonçaient « ~20 ». Corrigé
      dans les trois. À ne pas confondre avec les **résumés mémoire**, eux bien
      à 20 (`memory/summarizer.py:SUMMARY_INTERVAL`).
- [x] Compteurs faux remis d'équerre : 26 → **25** objets, 17 → **18**
      conditions, 2890 → **2913** tests, 22/19 → **24/21** specs/plans,
      MCP 7 → **8** outils.
- [x] **Passe de cohérence `docs/internal/*.md` faite (2026-07-19)** — audit
      par agent (60+ écarts vérifiés contre le code) puis corrections : 12
      fichiers, +118/−119. Les plus graves : STATE.md annonçait « non fait »
      trois choses livrées (auto-trigger du Story Director, CI, cleanup
      ChromaDB) ; MEMORY_SYSTEM.md inversait la purge des exchanges résumés ;
      `advance_beat_if_ready()` (supprimé) était cité vivant dans 5 docs ;
      DATABASE.md décrivait un système de migrations inexistant. Contradiction
      résiduelle `NPCRepository.update()` tranchée par le code (corrigé
      depuis longtemps — mentions purgées).
- [x] `docs/audits/2026-06-10-system-audit.md` — bandeau « ✅ CLOS le
      2026-07-19 » posé en tête (document d'archive).

### 3.3 — Code dormant : décisions déjà prises, à ne pas rouvrir

- **`attune_item` / `unattune_item`** (`engine/inventory.py`) — **gardés
  dormants, décision assumée.** Vérifié : `grep -c "requires_attunement=True"`
  → **0**. Aucun objet du catalogue ne requiert d'attunement : ce n'est pas un
  câblage oublié mais une capacité moteur en attente d'objets magiques.
  Correcte, testée, sans coût d'exécution. À câbler avec le catalogue d'objets
  magiques, pas avant.

### 3.4 — Fondations pour la suite

- [x] `CODE_OF_CONDUCT.md` + `SECURITY.md` — **posés le 2026-07-20.** Repris
      du commit `be6d9fa` de la branche distante `copilot/…` (Contributor
      Covenant 2.1 correctement attribué, `SECURITY.md` pointant vers les
      advisories du dépôt sans exposer d'e-mail), relus intégralement avant
      reprise, auteur réattribué. Les 2 liens morts de README + CONTRIBUTING
      sont fermés.
- [ ] Une fois la CI en place : envisager de protéger `main` (PR obligatoire)
- [ ] `docs/assets/` n'existe toujours pas → les 2 liens d'images de la
      section Demo du README restent morts (mais commentés, donc invisibles).
      Se ferme avec les GIFs du 1.3.

-----

# TEMPS 4 — Audit specs↔code du 2026-07-20

24 specs, 21 plans et 36 fiches combat vérifiés contre le code par 5 agents.
Verdict : l'essentiel est livré, et la majorité des « écarts » étaient des
supersessions assumées où la doc avait du retard. Sur 1073 cases `- [ ]` des
plans, **3 seulement étaient réellement ouvertes** (le reste est de la
procédure TDD jamais cochée).

**Le fil rouge : une fonction testée n'est pas une fonction câblée.** Les
trois plus gros trous étaient du code correct et couvert que *rien
n'appelait en production*.

### 4.1 — Corrigé dans la foulée

- [x] **Jets de mort câblés** — `resolve_death_save` avait 6 tests verts et
      **0 appelant**. Prouvé : un PJ à 0 PV restait inconscient pour
      toujours (0 jet après 6 tours), `check_combat_end` le comptait comme
      debout → le combat ne se terminait jamais. Avec le tour qui attend
      indéfiniment (décision du 2026-07-19), un joueur solo qui tombait
      bloquait sa campagne définitivement. Le README le vendait comme
      livré. 22 tests.
- [x] **Retry sur la génération de campagne** — spec
      `campaign-launch-reliability` §3 : 3 tentatives. L'helper existait mais
      n'était branché que sur le pipeline d'action ; un seul
      `OllamaUnavailableError` tuait un `/start_campaign`.
- [x] **pytest n'écrit plus dans la télémétrie de prod** — +86 Ko de
      `realm_*.log` et +3,3 Ko de décisions synthétiques dans
      `beat_progression.jsonl` par run. Mesuré, corrigé, re-mesuré à 0.
- [x] **`concept` du personnage transmis au narrateur** (saisi, persisté,
      affiché… jamais montré au LLM).
- [x] **Variété de génération câblée** — atmosphères (12) + anti-répétition
      d'archétype d'arc entre campagnes d'un même serveur.
- [x] **Simulateur** — arrêt sur mort du personnage, R1.locked_fact_violation
      branchée (le registre existait, c'était un trou de plomberie),
      `agent_retries` honnête.
- [x] **`QUEST_DETAIL` indexé** dans ChromaDB.
- [x] Passe de cohérence doc↔code : cadence Director, statut CI, 12 liens
      morts de `docs/internal/`, `ARCHITECTURE.md` racine rattrapé.
- [x] `tasks/` nettoyé : chantier combat et audit dead-code archivés.

### 4.2 — Ouvert, décisions à prendre

- [x] **Bibliothèque d'archétypes NPC — écrite et câblée (2026-07-20)**.
      `engine/npc_archetypes.py` : 20 archétypes / 5 catégories, contenu
      d'auteur (traits contradictoires, hook jouable, tic de dialogue
      performable par un 4b). Tirage équilibré par catégorie, anti-doublon
      par lieu dans le prefetch H8, tirage simple dans le chemin lazy de
      `resolve.py`. `NPCGenerator` prend un `NPCArchetype` (l'ancien
      `archetype_context` jamais fourni est supprimé) et ses fallbacks
      dérivent du hook écrit. Spec :
      `2026-07-20-npc-archetypes-and-quest-retirement-design.md` §1.
- [x] **Sous-système quêtes — tranché : les beats le remplacent
      définitivement (2026-07-20)**. Retiré : `world/quest.py`,
      `quest_repo`, `QuestRow` + mappers, `session.quests`, save/resume,
      `active_quests` (Layer 1 + arc tracker + embed), `index_quest` +
      `QUEST_DETAIL`, `stale_quest_ids` (Director), règle simulateur
      `R3.quest_silent_progress`. Motifs : les `BeatObjective` (y compris
      optionnels) subsument le modèle, `quest_generator` était déjà
      supprimé comme code mort, `stale_quest_ids` n'avait aucun
      consommateur. Le contenu annexe passe par les hooks d'archétypes +
      `suggested_hooks` du Director. Les DB existantes gardent une table
      `quests` orpheline vide (aucune migration destructive). Spec : idem, §2.
- [ ] **~9 fonctions dormantes** restantes, toutes correctes et testées
      (`is_combat_over` wrapper legacy, `consume_bonus_action`,
      `get_exhaustion_level`, `compute_spell_attack_bonus`,
      `restore_spell_slots` — pas de repos long —, `list_archetypes`,
      `build_damage_roll_embed`, `all_recipes`, les 2 helpers SURPRISED de
      `conditions.py`). Aucune n'est un bug ; à câbler ou retirer au coup
      par coup.
- [ ] **Écarts de spec mineurs, non traités** : pas d'embed de résumé à
      `/end_campaign` ; budget « thinking » = cap 4096 au lieu du `+2048`
      spécifié ; pas de cap/éviction ChromaDB (soft cap ~500 docs/campagne) ;
      `ContextAssembler` recréé à chaque narration au lieu d'être porté par
      `GameSession` ; `tests/scenarios/test_multiplayer_scenarios.py` absent ;
      seuil BeatJudge à 0.7 vs 0.85 en spec ; `bot/pipeline/resolve.py`
      (1540 l.) et `orchestrator.py` (871 l.) dépassent la limite de 500
      lignes que se fixait la spec Director's Cut.
- [ ] **Specs à annoter « superseded »** — 3 specs décrivent encore comme
      in-scope des modules supprimés (`exploration.py`, `/create_character`,
      `campaign_launcher.py`) sans note. Le code a raison, la spec ment.
- [ ] Dépréciation amont `discord.py` : `label` → `discord.ui.Label`
      (3 warnings pytest). Rien de cassé, à planifier avant discord.py 3.x.

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

## Session 2026-07-20 — abandon de création câblé au lobby

Suite de l'audit doc↔code : `LobbyPlayerStatus.CANCELLED` était rendu par
`bot/embeds/lobby_embed.py` mais aucun chemin ne l'assignait — **Annuler**
dans `CharacterSetupFlow` arrêtait la vue sans prévenir le lobby, laissant le
joueur en `CREATING` (« Création en cours… ») indéfiniment. La porte de
lancement n'était pas touchée (`has_any_ready()` ne compte que `READY`) :
bug d'affichage, pas de blocage.

- `CharacterSetupFlow` prend un callback `on_cancel` optionnel, appelé par
  `_on_cancel` **et** par `on_timeout` (les 10 min d'expiration sont un
  abandon comme un autre).
- `SessionCog.on_join` le câble : statut `CANCELLED` + re-render de l'embed,
  avec garde anti-régression — un flow périmé ne dégrade pas un `READY`.
- Driver de scénario : `HeadlessSessionFlow.click_join` extrait de
  `add_player`, plus `cancel_player`. 6 tests ajoutés (vue + bout en bout).
- `docs/internal/CAMPAIGN_LIFECYCLE.md` mis à jour (le comportement cassé y
  était documenté depuis la veille).

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
