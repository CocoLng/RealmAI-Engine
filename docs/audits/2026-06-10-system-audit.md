# Audit système — 2026-06-10

Audit multi-agents (8 dimensions, chaque finding vérifié adversarialement par un agent
indépendant qui devait le réfuter en lisant le code). Périmètre : runtime Discord,
persistence/DB, mémoire/contexte, pipeline de génération LLM, cohérence narrative,
frontière anti-triche, logs de runs réels (avril–juin), portes de qualité (pytest/ruff/mypy).

**Bilan : 65 findings confirmés, 4 réfutés** (3 réfutés car déjà corrigés par des commits
postérieurs aux logs — la vérification a fait son travail).

Toutes les références `fichier:ligne` ont été vérifiées dans le code au moment de l'audit.

---

## Causes racines transversales

1. **Mécanique « validée mais jamais résolue »** — plusieurs systèmes passent la validation,
   sont narrés par le LLM, mais ne touchent jamais l'état du jeu : sorts, /use_item,
   effets de beat sur les locations, locked facts, mémoire 4 couches. Le narrateur raconte
   des choses que le moteur n'a pas faites — l'invariant « le code arbitre » est violé
   silencieusement.
2. **Concurrence asyncio fragile** — lock non réentrant (deadlock), watcher qui s'annule
   lui-même, appels LLM/SQLAlchemy/ChromaDB synchrones sur l'event loop, double-clics non
   gardés.
3. **Chemin de reprise (/resume) cassé de bout en bout** — TurnManager jamais reconstruit,
   blobs JSON non protégés, zones invalides → ValidationError, locations jamais persistées.
4. **Frontière LLM asymétrique** — le joueur est strictement validé, mais le tacticien de
   boss, les stat blocks LLM et les commandes slash d'inventaire contournent le validateur.

---

## CRITIQUES (5)

### C1. Deadlock `action_lock` : une action libre en combat brique la campagne
`bot/cogs/action_handler.py:140` ↔ `bot/combat_turn_manager.py:160`
`on_message` tient `session.action_lock` (asyncio.Lock **non réentrant**,
`game_session.py:94`) pendant tout `_run_pipeline` ; l'étape 5 appelle
`on_action_resolved`, qui ré-acquiert le même lock → deadlock permanent. Se déclenche dès
la première action texte-libre qui démarre ou continue un combat (le chemin normal).
Tous les messages suivants reçoivent « ⏳ Une action est déjà en cours » jusqu'au restart.
La docstring du lock (commit e73af43) affirme à tort que tous les appelants ont relâché le
lock — vrai pour `dispatch_action` (boutons), faux pour `on_message`. Idem
`test_bridge.py:777`.
**Fix** : sortir l'étape 5 du `async with` (miroir de `dispatch_action`).

### C2. CAST_SPELL / DEFEND / DISENGAGE jamais résolus — les sorts sont de la pure narration
`bot/pipeline/resolve.py:327`
`resolve_mechanics()` n'a aucune branche pour ces trois actions → no-op générique.
`engine/combat.resolve_spell()` (ligne 754) n'est appelé que par les tests. Un magicien
lance Fireball : validation OK, narration épique, embed — mais 0 dégât, aucun slot
consommé, tour brûlé pour rien.
**Fix** : brancher `resolve_spell` + condition DODGING + désengagement dans
`resolve_mechanics`.

### C3. Le tour de combat avance sur TOUTE sortie de pipeline
`bot/combat_turn_manager.py:166`
`on_action_resolved` fait `del result` et `advance_turn` inconditionnellement. Appelé pour
les actions **refusées** (UnknownEntityResult), les QUESTION/LOOK autorisés hors-tour
(`validators.py:71-75`), et les messages d'autres joueurs (aucun garde-tour dans
`on_message`). Un coéquipier qui pose une question en plein combat consomme le tour du
combattant actif ; une attaque refusée fait quand même passer le tour.
**Fix** : n'avancer que si l'action appartient au combattant courant ET a réellement
consommé son action.

### C4. NPCs tués en combat complet jamais marqués morts
`bot/combat_end.py:113` (+ `bot/combat_entry.py:174`)
`build_npc_combatant` copie le NPC dans un nouveau `Character` ; les dégâts/morts ne
touchent que la copie. `finalize_combat` ne propage rien : pas de `npc.kill()`, pas de
retrait de `location.npcs_present`, pas de persist. Le chemin « trivial kill » a un
propagateur complet (`resolve.py:1148`), le combat complet n'en a aucun. Le boss d'arc
vaincu reste vivant, full HP, listé dans « NPCs present », et on peut lui parler.
**Fix** : à la fin du combat, itérer les ennemis tués et réutiliser `handle_npc_death`.

### C5. /resume ne reconstruit jamais le TurnManager — combat repris mort
`bot/cogs/session.py:993` + `bot/cogs/action_handler.py:256`
/resume restaure `combat_state` et annonce « (combat en cours !) », mais le TurnManager
n'est construit que sur un bootstrap frais (`pending_combat_start`). Après restart :
boutons morts (aucun `bot.add_view` dans le repo), pas de hub, pas de tours NPC, validateur
qui refuse « pas ton tour ». La fonctionnalité phare de checkpoint par tour est vaincue au
moment exact où on en a besoin.
**Fix** : dans /resume, si `combat_state.is_active`, reconstruire le TurnManager et
re-poster le hub.

---

## ÉLEVÉS (par axe, dédupliqués)

### Run / runtime
- **H1. Auto-défense AFK gèle le combat** — `combat_turn_manager.py:657` :
  `_timeout_watcher` appelle `dispatch_action`, dont la 1ʳᵉ ligne `_cancel_timeout()`
  annule… la tâche watcher elle-même. CancelledError part au prochain await (après
  application des mécaniques), rien ne l'attrape → tour jamais avancé, plus aucun joueur
  ne peut cliquer. Fix : `self.pending_timeout = None` avant `dispatch_action`.
- **H2. Appels Ollama synchrones sur l'event loop** — `bot/pipeline/orchestrator.py:545`
  (BeatJudge), `_dispatch_npc_brain` boss, `bot/cogs/hint.py:195` (niveau 3). httpx
  bloquant, timeout par défaut 120 s ; `BeatJudge.TIMEOUT_SECONDS = 5.0` n'est jamais
  passé et son `except TimeoutError` est du code mort (httpx lève TimeoutException). Un
  tour de boss peut geler TOUT le bot 2-4 min, heartbeat gateway compris. Fix :
  `asyncio.to_thread` + passer le timeout.
- **H3. /resume sans garde sur `model_validate_json`** — `session.py:996`,
  `db/mappers.py:443-447,499` : CombatState/Character/Inventory/StoryArc non protégés
  (contrairement à la politique `_safe_validate_json` des mappers). Un blob drifté après
  upgrade = campagne définitivement inaccessible.
- **H4. Zone invalide « skippée » → crash de chargement** — `db/mappers.py:252` :
  `_validate_list(Zone, ...)` droppe la zone, mais `_validate_zones_graph` lève ensuite
  ValueError sur les adjacences orphelines → la Location entière devient inchargeable
  (le warning vu en prod est le prélude au crash, pas un skip bénin).
- **H5. `persist_session` ne sauvegarde jamais les Locations** — `bot/persistence.py:27` :
  les effets de beat (`unlocked_exits`, `state_flags`, NPCs/items spawnés,
  `orchestrator.py:750-762`) ne vivent qu'en mémoire pendant que l'arc avancé, lui, est
  persisté → après restart le monde régresse mais l'histoire prétend le contraire =
  soft-lock d'arc.
- **H6. `LocationRepository.update/upsert` perdent `combat_triggers` + `npc_roles`** —
  `db/repositories/location_repo.py:39` : les embuscades générées disparaissent au premier
  save/reload, les ennemis re-spawnent en roturiers 8 HP, et les triggers ne sont jamais
  marqués consommés (re-farmables).
- **H7. /settings brique la guilde** — `session.py:1164` : `model_copy(update=...)` ne
  valide PAS (Pydantic v2) ; `language: "French"` est persisté, puis chaque relecture
  (`guild_config_from_db`) lève ValidationError → /start_campaign, /resume et /settings
  morts pour la guilde, sans récupération possible in-Discord.
- **H8. Latence réelle injouable** — logs réels : 7-8 min entre /start_campaign et la
  première scène (arc gen = un seul appel 9b de ~5000 tokens, 355-401 s), 36-84 s par
  action libre (3-4 appels LLM séquentiels).

### Contexte / mémoire
- **H9. La mémoire 4 couches n'est PAS branchée en production** — `bot/pipeline/narrate.py:75` :
  `ContextAssembler`, `SlidingWindow`, `Summarizer` et `SemanticMemory.query` n'ont **zéro
  appelant** dans `bot/` (admis dans `story_bible_logger.py:361`). Le contexte narrateur
  réel = snapshot de scène brut sans budget de tokens. ChromaDB est write-only (on paye
  l'indexation, on ne lit jamais). Conséquence directe : narrateur amnésique d'un tour à
  l'autre → la monotonie/répétition observée dans les transcripts, et contradiction
  possible avec ce qui a été narré 2 tours plus tôt. C'est l'écart le plus important entre
  l'architecture documentée (CLAUDE.md/README) et le code.

### Génération
- **H10. Validation NarrativeResult non catchée après mécanique appliquée** —
  `ai/narrator.py:138` : `tone` est un Literal strict ; le 9b en français peut émettre
  « dramatique » → ValidationError hors de la chaîne « never throws », action visiblement
  échouée APRÈS application des dégâts ; le conseil « réessayez » double-applique.
- **H11. Échec de parse interpréteur → DEFEND silencieux** — `ai/interpreter.py:64` :
  les JSONDecodeError sont avalées dans `interpret()` donc `retry_llm_call` ne retente
  jamais ; en combat le fallback est DEFEND confidence=0 → le joueur tape « j'attaque »,
  le perso se met en défense, tour consommé, sans message.
- **Évidence logs réels** (session « donjon » du 18/04) :
  - **H12.** Le narrateur invente une riposte ennemie « douze de votre santé » jamais
    résolue par le moteur (le tour suivant : MISS) — violation directe de l'invariant.
  - **H13.** Placeholder `[nom]` affiché tel quel au joueur (copié depuis l'exemple de
    `ai/prompts/system_narrator.txt:50`).
  - **H14.** Tours NPC = mécanique brute en anglais postée comme narration, y compris le
    texte d'erreur interne « aoe_damage not implemented — fallback to standard attack »
    (`combat_turn_manager.py:351-365`, `engine/npc_ai/elite.py:302`).

### Cohérence
- **H15. Les NPCs morts réapparaissent** — `bot/scene_hydration.py:474`,
  `ai/scene_context.py:52`, `npc_repo.list_by_location` : aucun filtre `is_alive` ; après
  revisite ou /resume, le cadavre est listé « present », TALK fonctionne et il dialogue.
- **H16. Objectifs de beat recalculés de zéro à chaque tour** — `engine/beat_progression.py:181` :
  `completed_at_turn` jamais persisté, `history=BeatHistory()` neuf à chaque tour ;
  les beats M_OF_N (SEARCH+EXAMINE) et FLAG sans writer sont mécaniquement insatisfiables
  → campagne soft-lockée sur les beats puzzle/investigation/rituel scaffoldés.
- **H17. Les « locked facts » n'existent pas** — `ai/prompts/system_narrator.txt:65` :
  pas de module facts, pas de stockage, rien d'injecté ; le narrateur doit rapporter des
  IDs qu'on ne lui donne jamais et personne ne consomme `locked_facts_used`. L'enforcement
  promis par CLAUDE.md est fictif.

### Anti-triche / frontière moteur
- **H18. Combat bootstrappé pendant la validation, persiste si l'attaque est refusée** —
  `bot/pipeline/interpret.py:244` : `start_combat` + SURPRISED + zones AVANT la validation
  de l'action ; garanti pour toute attaque de mêlée dans une location multi-zones
  (PCs en zones[0], ennemis en zones[-1] → « hors de portée ») : bannière de combat,
  refus, tour de surprise perdu, combat non voulu.
- **H19. Tacticien boss à peine validé** — `ai/npc_tactician.py:114` : seul le nom de la
  cible est vérifié — pas de zone/portée (le boss frappe en mêlée à travers la map alors
  que le joueur est strictement rangé-gated), pas de camp/vivant, pas de budget de
  signature (le nuke « 1×/combat » utilisable chaque round).
- **H20. /roll non borné = DoS en une commande** — `bot/cogs/rolls.py:25` +
  `engine/dice.py:84` : `100000000d100` matérialise la liste synchroniquement sur l'event
  loop → gel/OOM. Clamper num_dice/num_sides dans `engine/dice` (protège aussi les
  heal_dice et stat blocks LLM).
- **H21. /equip & /unequip contournent le combat** — `bot/cogs/inventory.py:76` : pas de
  check combat, pas de lock, jamais `validate_equip` ; AC boostée pendant le tour ennemi,
  swaps illimités gratuits (les Combatants référencent les mêmes objets que la session).
- **H22. /use_item détruit l'objet sans effet** — `bot/cogs/inventory.py:146` : seule
  `remove_item` est appelée ; la potion de soin disparaît sans soigner (la vraie logique
  n'existe que sur le chemin texte-libre `resolve.py:374-435`).

---

## MOYENS (sélection — détail complet dans la sortie workflow)

- **M1.** `interaction_count` lu sur le mauvais objet (GameSession au lieu de
  session.campaign) → la cadence %6 du Story Director **ne se déclenche jamais** et
  /hint niveau 3 est verrouillé à vie (`orchestrator.py:663`, `hint.py:120`).
  Signalé indépendamment par 3 dimensions.
- **M2.** Double-clics non gardés : boutons combat instantanés (defer avant disable,
  `combat_action_view.py:151`) et lobby « Démarrer » (`session.py:317`) → double dispatch /
  double lancement.
- **M3.** /end_campaign sans `action_lock` ni teardown du TurnManager → sessions terminées
  ressuscitées qui postent dans le channel archivé (`session.py:1094`).
- **M4.** SQLAlchemy + ChromaDB synchrones sur l'event loop dans /save, /end_campaign,
  change_location, hydrate_scene, `_apply_beat_effects` — `persist_session` documente
  lui-même qu'il faut `to_thread` (`persistence.py:30`).
- **M5.** Échecs d'auto-checkpoint avalés sans signal joueur, sans dirty flag, sans retry
  (`combat_turn_manager.py:742`) ; pas de WAL/busy_timeout SQLite (`db/database.py:34`).
- **M6.** Injection de prompt : texte joueur non délimité dans tous les prompts ; les
  secrets NPC sont dans le même message → exfiltrables (`ai/npc_agent.py:108`).
- **M7.** `num_predict=-1` partout, sortie jamais tronquée avant les embeds Discord
  (limite 4096) (`ai/client.py:80`, `narrative_embed.py:62`).
- **M8.** `keep_alive=10m` pour les DEUX modèles → 4b + 9b résidents simultanément,
  contredit la contrainte 18 GB documentée (`ai/client.py:127`).
- **M9.** Timer auto-dodge (300 s) court pendant le pipeline LLM d'une action texte-libre
  → une génération lente déclenche la défense auto en plein milieu (`combat_turn_manager.py:642`).
- **M10.** Fallbacks/refus hardcodés en français quelle que soit la langue de campagne
  (`ai/narrator.py:188`).
- **M11.** DirectorNote en cache jamais invalidée à l'avancement de beat → directives
  périmées injectées au narrateur (`orchestrator.py:642`).
- **M12.** Stat blocks de vilain LLM : `damage_dice`/`to_hit_bonus`/`save_dc` non clampés
  — le 9b décide littéralement des dégâts du boss (`engine/npc_stat_block.py:88`).
- **M13.** L'interpréteur transforme des questions joueur en actions exécutées
  (log réel : « comment sceller cette faille ? » → Improvise confidence 0.95).
- **M14.** mypy : **362 erreurs / 37 fichiers, aucune config mypy committée** —
  la porte qualité de CLAUDE.md est en échec permanent ; 2/3 des erreurs non-test viennent
  du pattern `Button.callback = lambda` de `character_setup_flow.py`.

## BAS (sélection)

- Skill check : « facile »/« simple » dans le texte joueur baisse la DC (`engine/skill_check.py:418`).
- Lobbies abandonnés jamais expirés ; httpx OllamaClient jamais fermé.
- `take_scene_item` répond succès après un persist raté → duplication d'objets au reload.
- Migrations : SCHEMA_VERSION stampé sans jamais être lu (pas de garde downgrade).
- Table exchanges non bornée + duplication fenêtre/résumés by design.
- Tests unitaires écrivent dans le store ChromaDB de prod (`test_game_session.py:54`).
- Chemins DB relatifs au CWD → lancer le bot d'ailleurs crée une DB vide silencieuse.
- Monotonie narrative confirmée dans les transcripts (même squelette de phrase).
- `logs/` mélange runs réels et fixtures pytest (99 % de la télémétrie beat = tests).
- `engine/npc_ai/scripted.py` à 73 % de couverture (< 80 % policy).

## Réfutés pendant la vérification (4)

- « Summarizer death spiral » — mécanique intra-module exacte mais impact surévalué.
- 3 findings de logs réels déjà corrigés par des commits postérieurs aux logs
  (08945c4 TALK migration, 0214635 world-gen coercion, fix du progress-embed).

---

## Ordre de réparation suggéré

1. **C1 deadlock** (1 ligne à déplacer) puis **H1 watcher** — les deux gels totaux.
2. **C3 + C5 + H18** — intégrité du tour de combat et de la reprise.
3. **C2 + H22 + H21** — la mécanique fantôme (sorts, items, équipement).
4. **C4 + H15** — propagation des morts.
5. **H2 + M4** — tout passer en `to_thread` (un sweep).
6. **H3/H4/H5/H6/H7** — durcir le chemin save/load.
7. **H9** — brancher la mémoire (gros chantier, mais c'est le cœur de la promesse produit).
8. **M1** (one-liner `session.campaign.interaction_count`) réactive Story Director + /hint.
9. **H20 + M12 + H19** — clamps anti-triche.
10. **H8** — latence (découpage arc gen, pré-génération, statuts progressifs).

Sortie brute complète du workflow (65 findings avec evidence/verifier notes) :
`/private/tmp/claude-501/-Users-cocolng-Documents-GitHub-RealmAI-Engine/aae66ba8-77fa-42cc-8387-c865931e1088/tasks/wt6a4cs6x.output`
