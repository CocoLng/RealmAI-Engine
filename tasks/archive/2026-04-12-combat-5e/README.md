# Chantier — Système de Combat D&D 5e

Refonte du système de combat pour atteindre une fidélité D&D 5e niveau "core" : combat orthogonal aux beats, NPCs richement statués, IA hybride scripted/LLM, initiative à 3 cas, signatures boss avec legendary actions et phases.

**Plan coordinateur complet** : `~/.claude/plans/glimmering-gliding-giraffe.md` (archivé ; la vision, les décisions de scope et le raisonnement complet y vivent).

> ⚠️ **Avant de démarrer une tâche**, lis [PRE_IMPLEMENTATION_FIXES.md](PRE_IMPLEMENTATION_FIXES.md). Il liste 5 corrections structurelles à appliquer au fil de l'eau (circular imports, engine/bot boundary, modèle `Combatant` à étendre d'un coup en tâche 22, etc.). Ne pas sauter cette lecture.

## Vision (résumé exécutif)

- **Combat = mode orthogonal**, peut se déclencher n'importe où (pas lié au beat type).
- **Party-wide** : toute la party entre en combat ensemble.
- **4 déclencheurs** : attaque explicite, intention létale détectée, piège/ambush scripté, provocation sociale.
- **D&D 5e-core** : Move + Action + Bonus Action + Reaction, zones abstraites, opportunity attacks, concentration, death saves, conditions 5e complètes.
- **Initiative à 3 cas** : agression joueur → surprise des cibles ; ambush → surprise des PCs ; face-à-face reconnu → initiative normale `1d20 + DEX`.
- **NPCs par tier** :
  - **Minion** (scripted simple) — 1 attaque, pas de signature.
  - **Elite** (scripted + behavior profile) — 2 attaques, 1 signature tirée de la librairie.
  - **Boss** (LLM-tactician) — 3 attaques, 2-3 signatures custom, **Legendary Actions** (3 pts/round), **2 phases HP**.
- **Règle d'or** : le LLM ne touche JAMAIS aux dés. Même en LLM-tactician, output = `{action, target, reasoning}` ; l'engine valide et roule.

## Ordre d'exécution

```
Phase 0 ──────► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 5 ──► Phase 6 ──► Phase 7 ──► Phase 8 ──► Phase 9
(bugfix)        (foundations) (engine)  (pipeline)  (NPC AI)    (Discord UI) (narrator)  (end of combat)  (docs)
                     ▲                                              ▲
                     └─────── Phase 4 (interpreter & generators) ───┘
```

- **Phase 0** est shippable immédiatement (débloquer la campagne Mageta bloquée).
- **Phase 1** et **Phase 4** peuvent s'exécuter en parallèle — aucune dépendance runtime.
- **Phase 6 tasks 60, 61** (dice embed, combat start embed) sont parallélisables tôt, pas de dépendance.
- **Phase 8 task 82** (live Discord test) est le **gate de fin** — tant qu'il n'est pas vert, feature non livrée.

## Liste des tâches

### Phase 0 — Bugfix immédiat
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 00 | [bugfix_villain_trivial_resolve](00_bugfix_villain_trivial_resolve.md) | — | ✅ |
| 01 | [bugfix_move_blocked_in_combat](01_bugfix_move_blocked_in_combat.md) | — | ✅ |

### Phase 1 — Fondations NPC & engine
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 10 | [npc_stat_block_model](10_npc_stat_block_model.md) | — | ⬜ |
| 11 | [npc_library_archetypes](11_npc_library_archetypes.md) | 10 | ⬜ |
| 12 | [zone_model](12_zone_model.md) | — | ⬜ |
| 13 | [surprised_condition](13_surprised_condition.md) | — | ⬜ |

### Phase 2 — Moteur de combat multi-ennemis
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 20 | [combat_entry_module](20_combat_entry_module.md) | 10, 11, 13 | ✅ |
| 21 | [initiative_and_surprise](21_initiative_and_surprise.md) | 13, 20 | ✅ |
| 22 | [multi_enemy_combat_state](22_multi_enemy_combat_state.md) | 21 | ✅ |
| 23 | [action_economy](23_action_economy.md) | 22 | ✅ |
| 24 | [zone_movement_and_opportunity](24_zone_movement_and_opportunity.md) | 12, 23 | ✅ |

### Phase 3 — Validation, pipeline, détection de triggers
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 30 | [strict_combat_validators](30_strict_combat_validators.md) | 23, 24 | ⬜ |
| 31 | [action_pipeline_combat_dispatch](31_action_pipeline_combat_dispatch.md) | 20, 30, 40 | ⬜ |
| 32 | [flee_resolution](32_flee_resolution.md) | 31 | ⬜ |

### Phase 4 — Interprète et générateurs LLM (parallèle)
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 40 | [interpreter_lethal_intent](40_interpreter_lethal_intent.md) | — | ⬜ |
| 41 | [world_generator_zones_triggers](41_world_generator_zones_triggers.md) | 12 | ⬜ |
| 42 | [arc_generator_villain_stat_block](42_arc_generator_villain_stat_block.md) | 10 | ⬜ |
| 43 | [hydration_dispatches_tier](43_hydration_dispatches_tier.md) | 11, 42 | ⬜ |

### Phase 5 — IA tactique (NPC brains)
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 50 | [scripted_minion_ai](50_scripted_minion_ai.md) | 22, 24 | ⬜ |
| 51 | [elite_behavior_profiles](51_elite_behavior_profiles.md) | 50, 11 | ⬜ |
| 52 | [boss_llm_tactician](52_boss_llm_tactician.md) | 51, 42 | ⬜ |
| 53 | [legendary_actions_off_turn](53_legendary_actions_off_turn.md) | 22, 52 | ⬜ |
| 54 | [phase_transitions](54_phase_transitions.md) | 22, 42 | ⬜ |

### Phase 6 — Discord UI
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 60 | [dice_embed_module](60_dice_embed_module.md) | — | ✅ |
| 61 | [combat_start_embed](61_combat_start_embed.md) | 21 | ✅ |
| 62 | [combat_state_embed](62_combat_state_embed.md) | 22, 12 | ✅ |
| 63 | [combat_action_views](63_combat_action_views.md) | 31, 62 | ✅ |
| 64 | [turn_ping_and_timeout](64_turn_ping_and_timeout.md) | 63 | ✅ |

### Phase 7 — Narrateur & cohérence narrative
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 70 | [narrator_combat_context](70_narrator_combat_context.md) | 22 | ✅ |
| 71 | [narrator_phase_transition_prompt](71_narrator_phase_transition_prompt.md) | 54, 70 | ✅ |

### Phase 8 — Fin de combat & intégration
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 80 | [combat_end_conditions](80_combat_end_conditions.md) | 22, 32 | ✅ |
| 81 | [social_resolution_mid_combat](81_social_resolution_mid_combat.md) | 80 | ✅ |
| 82 | [end_to_end_live_test](82_end_to_end_live_test.md) | **tout le reste** | ✅ |

### Phase 9 — Documentation
| # | Tâche | Dépendances | Status |
|---|-------|-------------|--------|
| 90 | [combat_system_doc](90_combat_system_doc.md) | toutes | ⬜ |

## Légende status

- ⬜ À faire
- 🔄 En cours
- ✅ Terminé
- ❌ Bloqué

## Règles pour chaque agent

1. **Lire la fiche tâche en entier** avant de commencer.
2. **Vérifier les dépendances** : toutes les tâches listées en "Dépendances" doivent être ✅ avant de commencer.
3. **Respecter le scope** : chaque tâche liste explicitement ce qui est *hors scope*. Ne pas déborder — les autres tâches couvrent le reste.
4. **Respecter la règle d'or** : aucune tâche ne doit laisser le LLM décider d'un dé, d'un dégât ou d'une résolution mécanique. L'engine est le référé.
5. **Tests obligatoires** : chaque tâche liste les tests à ajouter. Tous doivent passer avant de marquer ✅.
6. **Mettre à jour `docs/internal/`** selon la table ci-dessous (voir section "Documentation"). Additions **concises** — paragraphe court, pas de duplication du code.
7. **Valider à la fin** : `uv run pytest` + `uv run ruff check .` + `uv run mypy .` — tout vert.
8. **Commit conventionnel** : `feat(combat): …`, `fix(combat): …`, `test(combat): …`. Undercover mode : aucune mention Claude/AI dans le message.
9. **Ask User** si ambiguïté — ne pas deviner sur les règles de combat.

## Documentation — obligatoire pour toute tâche qui touche au comportement visible

La documentation interne dans `docs/internal/` **doit rester cohérente avec le code**. Chaque tâche qui introduit ou modifie un comportement, une API publique, un flux, ou un modèle data **doit mettre à jour les fichiers concernés** listés dans la table ci-dessous, avec des additions **concises** :

- **Paragraphe court** (3-8 phrases) ou **ligne de table** — pas de dissertation.
- **Référencer le code** (`bot/combat_entry.py`, `engine/npc_stat_block.py`) plutôt que le dupliquer.
- **Pas de copier-coller** du README du chantier ou des prompts LLM dans les docs internes.
- **Pas de section "Future work"** dans les docs internes — les hors scopes restent dans les fiches tâches.
- **Garder l'esprit "snapshot à jour"** : les docs décrivent ce qui est *implémenté*, pas ce qui est *prévu*.
- Si une doc existante devient obsolète, la **corriger**, pas la reléguer en section "Legacy".

### Mapping tâche → docs à mettre à jour

| Tâche | Docs à toucher | Nature de l'update |
|---|---|---|
| **00, 01** (bugfix) | [ISSUES.md](../../docs/internal/ISSUES.md) | **Retirer** les bugs (villain one-shot, MOVE en combat) de la liste |
| **10** NPCStatBlock | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md), [DATABASE.md](../../docs/internal/DATABASE.md) | Mentionner `NPCStatBlock`, tier, persistance JSON |
| **11** Librairie archétypes | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Liste des archétypes dispos + `get_archetype()` |
| **12** Zones | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md), [DATABASE.md](../../docs/internal/DATABASE.md) | Concept `Zone`, adjacency, validation |
| **13** Conditions SURPRISED/CONCENTRATING | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Ajouter lignes à la liste des conditions |
| **20** Combat entry | [ACTION_PIPELINE.md](../../docs/internal/ACTION_PIPELINE.md) | Section "Détection de trigger combat" avec les 4 déclencheurs |
| **21** Initiative/surprise | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Table des 3 cas de surprise |
| **22** Multi-enemy + persistence | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md), [DATABASE.md](../../docs/internal/DATABASE.md), [STATE.md](../../docs/internal/STATE.md) | Flow `advance_turn`, `check_combat_end`, persistance `combat_state_json` |
| **23** Action economy | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Table Move/Action/Bonus/Reaction |
| **24** Zone movement + OOA | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Règles de mouvement zone, opportunity attacks |
| **30** Validators stricts | [ACTION_PIPELINE.md](../../docs/internal/ACTION_PIPELINE.md) | Refonte section validation combat |
| **31** Dispatch pipeline | [ACTION_PIPELINE.md](../../docs/internal/ACTION_PIPELINE.md) | Organigramme combat-aware, MOVE→FLEE |
| **32** Flee | [ACTION_PIPELINE.md](../../docs/internal/ACTION_PIPELINE.md) | Mention `_resolve_flee`, check DEX |
| **40** Lethal intent | [AI_LAYER.md](../../docs/internal/AI_LAYER.md) | Nouveau flag `is_lethal_intent` sur l'interprète |
| **41** World gen zones/triggers | [AI_LAYER.md](../../docs/internal/AI_LAYER.md) | Nouveaux champs output world_generator |
| **42** Arc gen villain stat block | [AI_LAYER.md](../../docs/internal/AI_LAYER.md), [NARRATIVE_COHERENCE.md](../../docs/internal/NARRATIVE_COHERENCE.md) | Stat block custom dans l'output arc |
| **43** Hydration dispatch | [DATABASE.md](../../docs/internal/DATABASE.md), [NARRATIVE_COHERENCE.md](../../docs/internal/NARRATIVE_COHERENCE.md) | Règles d'hydration par tier, upgrade idempotent |
| **50-51** Minion/Elite AI | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Section "NPC AI" : tier-based brain dispatch |
| **52** LLM tactician | [AI_LAYER.md](../../docs/internal/AI_LAYER.md) | Nouveau LLM call, prompt `system_npc_tactician.txt` |
| **53** Legendary actions | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | Mécanisme off-turn points/round |
| **54** Phase transitions | [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md), [NARRATIVE_COHERENCE.md](../../docs/internal/NARRATIVE_COHERENCE.md) | Seuils HP, effet sur les stats, événements |
| **60-64** Discord UI | [DISCORD_BOT.md](../../docs/internal/DISCORD_BOT.md) | Nouveaux embeds (dice, combat start, state), views (CombatActionView), turn manager |
| **70-71** Narrateur | [NARRATIVE_COHERENCE.md](../../docs/internal/NARRATIVE_COHERENCE.md), [AI_LAYER.md](../../docs/internal/AI_LAYER.md) | Contexte `COMBAT ACTIVE`, prompt phase transition |
| **80-81** Fin de combat | [ACTION_PIPELINE.md](../../docs/internal/ACTION_PIPELINE.md), [GAME_ENGINE.md](../../docs/internal/GAME_ENGINE.md) | 5 conditions de fin, cleanup, truce |
| **82** E2E test | [TESTING.md](../../docs/internal/TESTING.md) | Nouveau scénario `test_combat_system_e2e.py` |
| **90** Documentation | **créer** `docs/internal/COMBAT_SYSTEM.md` + mise à jour [README.md](../../docs/internal/README.md) pour l'indexer | Doc de référence dédiée au combat (voir fiche 90) |

### Règle "STATE.md"

[STATE.md](../../docs/internal/STATE.md) est le snapshot d'avancement. **Chaque tâche terminée doit mettre à jour les lignes pertinentes** : passer un item de "❌ Non commencé" ou "🔄 En cours" vers "✅ Implémenté" avec une phrase courte de description. C'est **l'update le plus important pour la traçabilité** — tout le reste est secondaire.

### Règle "ISSUES.md"

Si une tâche **corrige** un bug listé dans [ISSUES.md](../../docs/internal/ISSUES.md), le **retirer** du fichier (ou le marquer ✅ et déplacer vers une section "Résolus récemment" si elle existe). Ne pas laisser un bug résolu traîner dans la liste active.

### Validation doc

Avant de marquer ✅ une tâche, vérifier :
- [ ] Les fichiers listés dans la table ci-dessus ont été mis à jour.
- [ ] Les additions sont concises (pas de blob de code dupliqué, pas de narration marketing).
- [ ] `STATE.md` reflète l'avancement.
- [ ] `grep` rapide dans `docs/internal/` pour détecter les infos obsolètes contredisant le nouveau code, et les corriger.

## Scope global — ce qui est HORS périmètre

- **Lair Actions** (effets environnementaux init 20)
- **Legendary Resistance** (auto-succès boss sur saves)
- **Frightful Presence** (aura passive fear)
- **Ritual spell casting**
- **Grid 5-pieds** (on reste en zones abstraites)
- **PvP** (joueur contre joueur)
- **Companion NPCs** (alliés contrôlés par le bot)

Ces items sont notés pour un chantier ultérieur. Si une tâche en cours suggère qu'ils seraient utiles, les ajouter au backlog — ne pas les implémenter.
