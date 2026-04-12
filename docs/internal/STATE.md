# État actuel du code (snapshot 2026-04-11)

Synthèse factuelle de ce qui est **implémenté, partiellement implémenté, ou non commencé**. Basé sur le code présent au commit `7c0f9a0`.

## Phases du projet

| Phase | État | Contenu |
|---|---|---|
| **Phase 1 — Engine** | 🟢 Terminée | `engine/` complet, ~98% coverage |
| **Phase 2a — World + DB** | 🟢 Terminée | `world/`, `db/`, 11 repos, mappers |
| **Phase 2b — Memory 4 couches** | 🟢 Terminée | `memory/`, ChromaDB, context assembler |
| **Phase 2c — AI Core** | 🟢 Terminée | `ai/` (8 services), prompts, entity resolver |
| **Phase 3 — Discord Bot** | 🟡 En cours | Cogs, pipeline, views, embeds, launcher — fonctionnel |
| **Phase 4 — Polish + ship** | 🔴 Non commencé | Pas de CI, pas de README final, pas de blog post |

## Chantier combat D&D 5e (tasks/combat/)

Refonte du combat pour atteindre une fidélité "core 5e" : orthogonal aux beats, NPCs richement statués, IA hybride scripted/LLM, initiative à 3 cas, boss avec signatures/phases/legendary actions. Plan coordinateur : [tasks/combat/README.md](../../tasks/combat/README.md).

| Phase | État | Contenu |
|---|---|---|
| **Phase 0 — Bugfixes** | 🟢 Done | Villain protégé du trivial resolve, MOVE bloqué en combat actif |
| **Phase 1 — Fondations NPC & engine** | 🟢 Done | `NPCStatBlock` + 11 archétypes + `Zone`/`ZoneTag` + conditions `SURPRISED`/`CONCENTRATING`, persistance DB complète |
| **Phase 2 — Moteur multi-ennemis** | 🟢 Done | `engine/combat_trigger.py` + `bot/combat_entry.py` (4 triggers), `start_combat(trigger=)` 3 cas surprise, `advance_turn` consume SURPRISED / reset ActionBudget / round wrap reactions, `check_combat_end` (VICTORY/DEFEAT/FLED/TRUCE), `resolve_npc_attack` pour stat-blocks, concentration hook sur `apply_damage`, `move_combatant_to_zone` + OOA + `disengage`, `combat_id`/`end_reason`/`pending_phase_narrations` sur `CombatState`, persistance roundtrip via `campaigns.combat_state_json` (Pydantic) |
| **Phase 3 — Validation & pipeline** | 🟢 Done | Task 30 ✅ — Validators stricts (SURPRISED guard, action budget, friendly fire, range zones, validate_move_in_combat) ; Task 31 ✅ — dispatch pipeline combat-aware + MOVE→FLEE auto-convert + guard ValueError ; Task 32 ✅ — résolveur FLEE async DEX DC 12, fled=True sur succès, action_used sur échec, check_combat_end FLED (vivants seulement), change_location sur full escape |
| **Phase 4 — Interprète & générateurs** | 🟢 Done | Task 40 ✅ — `InterpretedAction.is_lethal_intent` + prompt interpréteur. Task 41 ✅ — `CombatTriggerDef`, `Location.combat_triggers`, parser world generator (zones + triggers avec fallback silencieux sur graph cassé). Task 42 ✅ — `StoryArc.villain_stat_block: NPCStatBlock \| None`, parser arc_generator avec fallback `generic_boss:<villain>` sur ValidationError, prompt enrichi (schéma complet + règles de casing enum). Task 43 ✅ — `scene_hydration._build_npc_by_context` dispatche villain/role/combat-beat/commoner, `Location.npc_roles`, upgrade idempotent préservant narrative fields, persistance DB combat_triggers + npc_roles via migration v3→v4. |
| **Phase 5 — IA tactique NPC** | 🟢 Done | Task 50 ✅ — `engine/npc_ai/scripted.py` : `NPCActionPlan` Pydantic, `decide_minion_action` (weakest-in-range → BFS step → Dodge), `execute_action_plan` (ATTACK via `resolve_npc_attack`, MOVE via `move_combatant_to_zone`, DEFEND). Task 51 ✅ — `engine/npc_ai/elite.py` : `decide_elite_action` dispatche 4 profils (AGGRESSIVE/DEFENSIVE/SUPPORT/TACTICAL), `execute_signature_ability` résout damage/heal/condition (avec save throw + phase_save_bonus), kinds non-MVP (aoe_damage/buff/debuff/move) logguent WARNING + fallback summary. `decide_action_for` dispatcher par tier côté `scripted.py` ; `execute_action_plan` route signature_name vers elite executor. Task 52 ✅ — `ai/npc_tactician.py` + prompt `system_npc_tactician.txt` + `TacticalDecision` Pydantic + `engine/npc_ai/boss_brain.py::decide_boss_action` (retry x2 sur ValueError puis fallback elite AGGRESSIVE). Task 53 ✅ — `engine/npc_ai/legendary.py::maybe_spend_legendary_action` + heuristique `_pick_legendary` (cost-1 eager, cost-2 preferred, cost-3 only when HP < 30%), hooks `advance_turn` : off-turn fire après chaque tour PC, reset `legendary_points_remaining` au début du tour du boss, summaries accumulés sur `CombatState.pending_legendary_summaries`. Task 54 ✅ — `engine/combat_phases.py::check_phase_transition` + hook dans `apply_damage(combatant, damage, state=None)` : franchissement de seuil HP déclenche `triggered=True`, applique `attack_bonus` à chaque NPCAttack, accumule `save_bonus` sur `Combatant.phase_save_bonus`, unlock des signatures listées, append `PhaseTransitionEvent` à `CombatState.pending_phase_narrations` quand state fourni. Multi-phase possible sur gros hit, pas de retrigger après heal. **70 tests Phase 5 verts** (9 scripted + 19 elite + 6 tactician + 7 boss_brain + 14 legendary + 15 phases). |
| **Phase 6 — Discord UI** | 🟢 Done | Task 60 ✅ — `bot/embeds/dice_embed.py` (`build_attack_roll_embed`, `build_save_check_embed`, `build_damage_roll_embed`, `build_generic_check_embed`), couleurs hit/miss/crit, outcomes + damage types FR. Task 61 ✅ — `bot/embeds/combat_start_embed.py::build_combat_start_embed`, ordre d'initiative + surprise 3 cas + hint narratif, rouge #CC0000. Task 62 ✅ — refonte `bot/embeds/combat_embed.py` : zone grouping (`location.has_combat_zones()`), HP bars 10-char, conditions FR avec durée, champ Boss legendary points, skip dead/fled, backward-compat sans location. Task 63 ✅ — `CombatActionView` (5 boutons Attaquer/Sort/Défendre/Fuir/Déplacer, `timeout=None`, `interaction_check` par user_id, boutons pré-désactivés) + `TargetSelectView`/`SpellSelectView`/`ZoneSelectView` en ephemeral followup (timeout 60s), suppression wholesale des anciennes `CombatView`/`TargetSelectView`/`SpellSelectView`. Task 64 ✅ — `bot/combat_turn_manager.py::TurnManager` : hub Discord édité en place, dispatch NPC par tier (minion scripted / elite behavior profile / boss LLM tactician avec fallback elite), watcher asyncio 5 min + auto-Dodge via pipeline, flush `pending_legendary_summaries` + `pending_phase_narrations`, finalize avec XP stub (100/enemy split sur survivors — la Phase 8 task 80 le remplacera). Câblage dans `bot/cogs/action_handler.py` : bootstrap `TurnManager` sur `pipeline._pending_combat_start_embed`, relais `on_action_resolved` après chaque résolution pour avancer le tour. `ActionPipeline.process_interpreted_action` exposé publiquement pour le dispatch boutons (bypass interpreter). `bot/cogs/combat.py` devient un shell minimal (factory `build_turn_manager`). `build_pc_combatants` / `build_npc_combatant` déplacés dans `bot/combat_entry.py`. **56 tests Phase 6 verts** (17 dice + 10 combat_start + 14 combat_embed + 15 combat_action_view + 10 turn_manager). |
| **Phase 7 — Narrateur** | 🟢 Done | Task 70 ✅ — `bot/scene_hydration.py::describe_scene_for_narrator` injecte une section `## COMBAT ACTIVE` (round, current turn, combattants avec HP exact PC / vague NPC tiers `indemne/légèrement/gravement/à l'article`, zones, conditions FR, flavor archétype+tier stat block), trois derniers événements mécaniques lus depuis `CombatState.recent_events` (champ nouveau, cap 12, alimenté par `engine.combat.record_combat_event` depuis le bot). Bloc `## Acting character` enrichi always-on avec race/classe/niveau/arme équipée (résolution combat-first puis `session.characters` fallback). `ai/prompts/system_narrator.txt` : nouvelles sections "Acting character awareness" + "COMBAT ACTIVE — règles spéciales" (miss=miss, tour par tour, ton tendu, HP NPC vagues, invitation au tour suivant, pas d'évasion passive). Hook de recording dans `bot/action_pipeline.py` (après `_resolve_mechanics`) + `bot/combat_turn_manager.py::_resolve_npc_turn`. Task 71 ✅ — `ai/narrator_phase.py::narrate_phase_transition` + prompt dédié `system_narrator_phase.txt` (3-5 phrases cinématiques, sortie JSON `{"narration"}`). `PhaseTransitionEvent.consumed: bool` ajouté (default False, round-trip legacy OK). `bot/combat_turn_manager.py::_flush_pending_cues` remplace le post `🔥 cue` brut par un gold embed `✨ Phase transition — {boss}` avec narration LLM, `consumed=True` marqué **avant** l'appel LLM, fallback gracieux sur le cue brut si narrateur en erreur ou `session.ollama_client` absent. **28 tests Phase 7 verts** (7 engine + 11 scene_hydration + 3 narrator_prompt + 5 narrator_phase + 5 turn_manager + 2 action_pipeline). |
| **Phase 8 — Fin de combat** | 🟢 Done | Task 80 ✅ — `bot/combat_end.py::finalize_combat(session, reason) -> CombatEndSummary` : point d'entrée unique (idempotent via `CombatState._finalized` `PrivateAttr`) qui construit le récap (survivors/killed/fled + loot `stat_block.attacks[0].name` + XP par tier MINION=50/ELITE=150/BOSS=500/fallback=25), **applique** l'XP aux survivants via `add_xp` + flag `level_ups` via `check_level_up`, purge `SURPRISED` + `CONCENTRATING` (préserve POISONED/PRONE/etc.), préserve `session.combat_state` pour l'historique. `bot/embeds/combat_end_embed.py::build_combat_end_embed` : 4 couleurs (VICTORY vert / DEFEAT rouge / FLED gris / TRUCE violet), champs optionnels. `TurnManager._finalize` refactoré pour déléguer à `finalize_combat` + poster l'embed + freeze hub ; `_apply_xp_stub` supprimé. `ActionPipeline._resolve_flee` route le cleanup FLED via `finalize_combat` (import local, zéro cycle). **80.7** — persistance combat : `ActionPipeline` auto-checkpoint existe déjà pour les actions PC ; `TurnManager` reçoit désormais `db_factory` (via `CombatCog.build_turn_manager`) et appelle `_persist_state` après `advance_turn` + après `_finalize` pour couvrir les tours NPC et l'état final (pas de timeout côté combat — Discord tolère les déconnexions, combat reprenable). Task 81 ✅ — `NPCStatBlock.mindless: bool` ajouté. `engine/validators.py::validate_truce_attempt` rejette allié/sans stat_block/mindless/cible manquante. `validate_exploration_action` délègue TALK-en-combat à `validate_truce_attempt` (autres exploration actions restent bloquées). `bot/combat_truce.py::attempt_truce(actor, target, state) -> (succeeded, check, summary)` roule `1d20 + CHA_mod + 2` vs `aggression_threshold`, succès **strict** (SUCCESS + CRITICAL_SUCCESS uniquement, NEAR_SUCCESS compte comme échec), auto-refus mindless / boss-phase-2 (triggered à ≤50%), consomme l'Action sur roll réel, marque tous les enemies vivants `fled=True` sur succès. `ActionPipeline._resolve_talk_in_combat` dispatche TALK en combat vers `attempt_truce` + appelle `finalize_combat(session, TRUCE)` sur succès, queue le check dans `_pending_dice_embeds` pour affichage task 60. Task 82 ✅ — `tests/scenarios/test_combat_system_e2e.py` (13 tests Mageta vs Vellus : bootstrap, phase 2, truce success/refus/failure, VICTORY, DEFEAT, idempotence double-finalize, non-régression commoner/TALK hors combat/MOVE bloqué) + fixture `vellus_stat_block` dans `conftest.py`. Live Discord test plan documenté dans [combat_system_e2e_results.md](combat_system_e2e_results.md). **99 tests Phase 8 verts** (40 combat_end + 23 combat_truce + 13 e2e + 8 turn_manager persist/finalize + 15 autres). |
| **Phase 9 — Documentation** | 🔴 Non commencé | `docs/internal/COMBAT_SYSTEM.md` |

## Lots post-mortem campagne 1 (tasks/agents/)

Suite à une première campagne live (2026-04-07) avec 7 actions et 0 mutations significatives, le code a été refactoré en 6 lots parallèles — **tous complétés** :

| Lot | Sujet | État | Impact |
|---|---|---|---|
| A | Scene awareness | 🟢 Done | Scene embed au launch + post-MOVE, PNJs list[str] traités correctement |
| B | Entity resolution | 🟢 Done | Lemmes FR + fuzzy + fallback LLM, 35 tests verts |
| C | Combat initiation | 🟢 Done | Bootstrap `CombatState` depuis attaque free-text |
| D | Story progression | 🟢 Done | Avancement de beat par fuzzy match location (0.7) |
| E | Trivial NPC death | 🟢 Done | One-shot resolve pour PNJs faibles/pacifiques (durci en chantier combat Phase 0 Task 00 : villain + beats combat/boss jamais trivial-resolus) |
| F | Narrator JSON | 🟢 Done | Prompt durci, `LLMParseError` avec dump auto dans `logs/narrator_failures/` |

## Fonctionnalités implémentées

### Moteur de règles
- ✅ Dés (parseur `NdM+X`, d20 checks 6 tiers)
- ✅ Personnages (7 races, 6 classes, 9 alignments, 20 niveaux, XP, level-up)
- ✅ Inventaire (25+ items catalogue, 9 slots, attunement max 3)
- ✅ Armes et armures (4 catégories armes, 3 catégories armures, shield +2)
- ✅ Sorts (~20 sorts catalogue, slots full/half caster, cantrip scaling)
- ✅ Conditions (17 conditions SRD incluant SURPRISED et CONCENTRATING, durations, effets advantage/disadvantage, helpers `consume_surprise_if_present` + `check_concentration_save`)
- ✅ Combat (initiative 3 cas avec `CombatTrigger`, attaques PC + NPC stat-blocks, crits, death saves, sorts avec saves, `advance_turn` multi-ennemis avec reset ActionBudget + consume SURPRISED, `check_combat_end` VICTORY/DEFEAT/FLED/TRUCE, concentration hook sur damage)
- ✅ Action economy 5e (`ActionBudget` : Move + Action + Bonus Action + Reaction 1/round, reset par tour / round-wrap)
- ✅ Mouvement entre zones + attaques d'opportunité + action `Disengage`
- ✅ Trivial resolve (Lot E)
- ✅ Starter kits (15 kits sur 6 classes)
- ✅ Validators (combat + exploration ; exploration bloque MOVE/TALK/SEARCH/INTERACT/PICKUP en combat actif depuis Phase 0 Task 01 ; Task 30 : SURPRISED guard, action budget ATTACK/CAST_SPELL, friendly fire, range par zone, validate_move_in_combat)

### AI / LLM
- ✅ Interpreter (15 ActionType incl. QUESTION, fallback déterministe)
- ✅ Narrator (JSON strict, tone classification, canon faithfulness)
- ✅ NPC Agent (dialogue + disposition delta + revealed info)
- ✅ NPC Generator (fiches lazily à la 1ʳᵉ rencontre)
- ✅ World Generator (avec item_descriptions validation)
- ✅ Quest Generator
- ✅ Arc Generator (10-15 beats, boss final, villain)
- ✅ Story Director (coherence check périodique)
- ✅ Entity Resolver (exact → lemmes FR → fuzzy → fallback LLM)
- ✅ Scene Context builder
- ✅ Ollama client avec thinking mode
- ✅ Retry logic (5s, 15s) via `bot/llm_retry.py`

### Mémoire
- ✅ Layer 1 : structured state (SQLite)
- ✅ Layer 2 : sliding window 12 exchanges
- ✅ Layer 3 : compressed summaries tous les 20 tours
- ✅ Layer 4 : ChromaDB RAG par campagne
- ✅ Context assembler avec budget token + truncation par priorité

### Persistance
- ✅ 10 tables SQLAlchemy + migrations `ALTER TABLE` incrémentales
- ✅ 11 repositories
- ✅ Mappers bidirectionnels domaine ↔ DB
- ✅ Sérialisation JSON des champs nested
- ✅ Foreign keys CASCADE

### Discord Bot
- ✅ 7 cogs (+1 test_bridge conditionnel)
- ✅ Slash commands : session, character, inventory, rolls, exploration (legacy)
- ✅ `@mention` → ActionPipeline (cœur de l'UX)
- ✅ Pipeline 6 phases avec progress embed live
- ✅ `CampaignLauncher` avec onboarding multijoueur parallèle
  - ✅ Character re-creation — re-clic « Créer Personnage » pour recommencer avant le launch
  - ✅ Force-launch — créateur peut forcer le lancement, excluant joueurs non-ready
  - ✅ Launch immersion — purge channel, countdown 3-2-1, opening crawl embed
- ✅ 8 views Discord (character create, starter gear, combat, target select, spell select, clarification, start onboarding, force launch)
- ✅ 8 embeds (narrative + opening crawl, progress, scene, beat, character, combat, inventory, état/state)
- ✅ Channel manager avec permissions + archives
- ✅ i18n statique FR/EN (labels)
- ✅ Scene hydration (promotion PNJ string → rows DB)
- ✅ Story bible logger Markdown append-only
- ✅ Beat advancement fuzzy match (Lot D)
- ✅ Beat completion triggers (déterministe + fallback LLM)
- ✅ Environment state persistence (state_flags, unlocked_exits sur Location)
- ✅ QUESTION action type avec embed d'état bleu
- ✅ Arc generator produit completion_trigger et on_complete par beat
- ✅ Scene context inclut beat info et state flags pour le narrator

### Testing
- ✅ ~1 530 tests unitaires
- ✅ ScenarioRunner end-to-end (8 scénarios)
- ✅ MCP Discord server (7 tools)
- ✅ TesterBot pour live Discord
- ✅ Lessons file (`tasks/lessons.md`)

## Partiellement implémenté / stabilité limitée

| Feature | État | Gap |
|---|---|---|
| `/save` / `/resume` | 🟡 | Tests basiques OK, pas tous les edge cases (sessions concurrentes). Combat actif persiste maintenant après chaque tour (task 80.7) + à la fin. |
| Combat state persistance | 🟢 | Sérialisé en JSON dans `campaigns.combat_state_json` (roundtrip Pydantic incluant `combat_id`, `end_reason`, `pending_phase_narrations`, `ActionBudget`). Auto-checkpointé après chaque tour (PC via `ActionPipeline` auto-checkpoint + NPC via `TurnManager._persist_state`) et à la finalisation (task 80.7) — reprise après déconnexion Discord garantie. |
| i18n dynamique | 🟡 | Labels statiques OK ; contenu dynamique repose sur la compliance du prompt |
| Story Director | 🟡 | Implémenté mais ne s'auto-déclenche pas ; silent fail si ChromaDB down |
| Initiative complète | 🟢 | 3 cas supportés via `CombatTrigger` : PLAYER surprise (agresseur en tête, enemies SURPRISED), NPC surprise (ambushers en tête, tous les PCs SURPRISED), BOTH_READY (roll standard + DEX tiebreak) |
| Spell slots recovery | 🟡 | Long rest fonction existe mais pas intégrée à une mécanique de repos dans l'UX |
| Combat rests / short rest | 🔴 | Non implémenté |
| Check de concentration conflict | 🔴 | `cast_spell` n'interrompt pas l'ancienne concentration |
| Proficiency check | 🔴 | Bonus toujours ajouté, pas de check actuel |
| Persistance à chaud `bot.sessions` | 🔴 | Crash = perte de session en cours |

## Non commencé / pas dans le code

- CI GitHub Actions
- Loader custom spells / items (catalogues hardcodés)
- Système de factions / témoins complexe
- Multi-narrator / multi-MJ
- Voice chat integration
- Dashboard admin
- Quest completion automatique (objectives manuels)
- Rewards auto-distribués (XP / gold) après combat/quest
- Skill checks hors combat (DC-based)
- Narrator streaming (toute narration est renvoyée en block)
- Tests end-to-end avec vrai Ollama
- Tests de migration DB
- Rollback de migration
- Cleanup ChromaDB sur delete campagne

## Observabilité

- 🟢 Logs structurés par session (`logs/realm_YYYYMMDD_HHMMSS.log`)
- 🟢 Logs commande slash via `on_app_command_completion`
- 🟢 `logs/narrator_failures/` pour dumps LLM en échec
- 🟢 Story bible par campagne (`logs/campaigns/<id>.md`)
- 🟡 Pas de métriques Prometheus / OpenTelemetry
- 🟡 Pas d'alertes / monitoring actif

## Ce qui tourne aujourd'hui (happy path)

Un joueur peut aujourd'hui :

1. Lancer `/start_campaign "donjon maudit" @moi @ami`.
2. Attendre ~1-2 min (génération arc + location).
3. Créer un personnage via les boutons (race → classe → alignement → nom).
4. Choisir un starter kit.
5. Explorer librement via `@Realm <action>` :
   - `@Realm je regarde autour de moi`
   - `@Realm je parle au marchand`
   - `@Realm j'attaque le gobelin avec mon épée`
   - `@Realm je vais vers la forêt`
6. Voir son combat résolu mécaniquement avec embed narratif + effects footer.
7. Voir les beats d'arc avancer quand il résout les objectifs (puzzle, combat, dialogue) ou atteint les lieux attendus.
8. Recevoir une clarification view si l'entité est ambiguë.
9. Poser des questions méta (`@Realm qu'est-ce que je vois ?`) et recevoir un embed d'état bleu avec items, PNJ, sorties et objectif.
10. `/save` et plus tard `/resume`.
11. `/end_campaign` pour archiver le salon.

Tout ce qui est hors de ce happy path est possible mais **susceptible** de casser, principalement à cause des gaps listés dans [ISSUES.md](ISSUES.md).

## Où regarder en priorité pour continuer

- Travail Phase 3 restant : persistance robuste des sessions, tests d'intégration plus complets, gestion de crash.
- Fix du bug `NPCRepository.update()` qui perd `dialogue_history/secrets/knowledge/aliases`.
- Auto-trigger Story Director.
- Logger les filtrages silencieux de `WorldGenerator`.
- Uniformisation des patterns de mutation dans `engine/`.
- Phase 4 : README joueur, CI, blog post.
