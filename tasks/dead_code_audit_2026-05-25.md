# Dead Code Audit — RealmAI-Engine — 2026-05-25

Enquête approfondie complémentaire à `tasks/audit_2026-05-25.md` (qui était imprécis sur la section P3). 6 agents Explore en parallèle + vérifications manuelles ciblées.

## 🟢 Cleanup exécuté (2026-05-25)

**Suppressions** (9 fichiers) :
- `ai/opening_reframer.py` + `tests/ai/test_opening_reframer.py` + `ai/prompts/system_opening_reframer.txt`
- `ai/quest_generator.py` + `tests/ai/test_quest_generator.py` + `ai/prompts/system_quest_generator.txt`
- `engine/npc_archetypes.py` + `tests/engine/test_npc_archetypes.py`
- `scripts/compare_shadow.py`

**Cascade Pydantic** :
- `world/story_arc.py` : champ `party_premise: str = ""` retiré (Pydantic ignore les champs orphelins en DB par défaut, pas de migration nécessaire)
- `bot/story_bible_logger.py` : bloc lecteur `party_premise` retiré + docstring mise à jour
- `engine/beat_progression.py` : fonction `log_shadow_decision` + entrée `__all__` retirées

**Tests** :
- `tests/bot/test_story_bible_logger.py` : test renommé `test_header_renders_party_composition` (suppression assertions party_premise)
- `tests/bot/test_action_pipeline.py:639` : commentaire stale supprimé
- `tests/scenarios/test_trivial_npc_kill.py` : `random.seed(0)` ajouté pour fixer flakiness RNG latente exposée par le changement d'ordre de collection pytest

**Docs** :
- `CLAUDE.md`, `ARCHITECTURE.md` (racine), `docs/internal/ARCHITECTURE.md`, `docs/internal/AI_LAYER.md`, `docs/internal/GAME_ENGINE.md` : références aux modules supprimés retirées
- `.claude/skills/ai-narrator.md:758` : référence à `quest_generator.py` non retirée (auto-mode classifier a bloqué l'édition de fichiers de config d'agent — cosmétique uniquement)

**Vérification gate** :
- `uv run pytest tests/` → **2329 passed, 1 skipped** (-27 tests vs baseline = exactement les tests supprimés)
- `uv run ruff check .` → clean
- `uv run mypy engine/ db/ memory/` → clean
- `uv run mypy bot/ ai/` → 63 erreurs **pré-existantes** (identique à l'audit baseline), **0 nouvelle**

**Stats** : +176 insertions, **-1357 deletions** (net **-1181 LoC**).


## TL;DR

- **Codebase globalement très sain.** Ruff F401 = 0. Aucun import inutilisé, aucun bloc commenté, aucun stub `NotImplementedError`, aucune branche morte (`if False:`).
- **3 modules entiers test-only** (~700 LoC + ~360 LoC de tests) : features câblées à moitié, prêtes à être supprimées OU wirées selon la décision produit.
- **1 fonction morte** dans `engine/beat_progression.py`.
- **1 script orphelin** post-migration (`scripts/compare_shadow.py`).
- **3 corrections à apporter à l'audit précédent** : des verdicts P3 étaient faux.

## ❌ Corrections à l'audit précédent (`tasks/audit_2026-05-25.md`)

L'audit P3 a déclaré ces éléments morts à tort. Ils sont **vivants** :

| Verdict P3 audit | Réalité vérifiée |
|---|---|
| `bot/views/equip_select_view.py` mort | Instancié dans [bot/views/combat_action_view.py:311](bot/views/combat_action_view.py:311) |
| `bot/views/potion_select_view.py` mort | Instancié dans [bot/views/combat_action_view.py:288](bot/views/combat_action_view.py:288) |
| `ai/beat_judge.py:120` `suggested_next_action` jamais lu | Lu dans [bot/cogs/hint.py:196](bot/cogs/hint.py:196) — affiché à l'utilisateur via `/hint` |
| `ai/narrator_phase.py` suggéré pour fusion (95 LoC isolés) | Vivant — importé par [bot/combat_turn_manager.py:64](bot/combat_turn_manager.py:64) pour les transitions de phase boss (task 71). Garder. |

**À retirer de l'audit principal P3.**

## 🔴 Code mort confirmé

### 1. `ai/opening_reframer.py` — module entier (~175 LoC)

**Verdict** : dead-on-arrival. La classe `OpeningReframer` est testée (175 LoC dans `tests/ai/test_opening_reframer.py`) mais **jamais instanciée en production**.

**Cascade de mort** :
- `OpeningReframer.reframe()` jamais appelé → `StoryArc.party_premise` reste `""` (valeur défaut)
- → la condition `if story_arc.party_premise.strip():` dans [bot/story_bible_logger.py:132](bot/story_bible_logger.py:132) ne se déclenche jamais
- → la ligne `lines.append(f"**Party premise (fait figé):** ...")` à [bot/story_bible_logger.py:134](bot/story_bible_logger.py:134) est inatteignable en prod
- → le prompt `ai/prompts/system_opening_reframer.txt` est chargé par le module mais jamais consommé

**Preuves** :
```bash
$ grep -rn "OpeningReframer\|opening_reframer" --include="*.py" bot/ ai/
ai/opening_reframer.py:58:class OpeningReframer:    # definition seule
# 0 instanciation en prod
```

**Recommandation** : soit wirer dans le flow `/start_campaign` (cf. docstring `world/story_arc.py:126` qui promet cette intégration), soit **supprimer** `ai/opening_reframer.py` + son test + son prompt + le champ `StoryArc.party_premise` + le bloc lecteur dans `story_bible_logger.py`.

### 2. `ai/quest_generator.py` — module entier (~120 LoC)

**Verdict** : dead-on-arrival. Le `QuestGenerator` est testé (123 LoC dans `tests/ai/test_quest_generator.py`) mais **jamais instancié en production**.

**Contexte** : le projet utilise `ai/arc_generator.py` pour produire les arcs narratifs avec leurs objectifs natifs ([commit 7fcf745](https://github.com/CocoLng/RealmAI-Engine)). Le générateur de quêtes isolées n'a jamais été intégré.

**Preuves** :
```bash
$ grep -rn "QuestGenerator" --include="*.py" bot/ ai/
ai/quest_generator.py:15:class QuestGenerator:    # definition seule
# 0 référence en prod
```

**Recommandation** : **supprimer** `ai/quest_generator.py` + son test + son prompt `ai/prompts/system_quest_generator.txt` (vérifier d'abord qu'il n'est utilisé nulle part ailleurs). L'arc generator couvre désormais le besoin.

### 3. `engine/npc_archetypes.py` — module entier (~270 LoC)

**Verdict** : système narratif parallèle remplacé par `engine/npc_library.py` mais jamais retiré.

**À ne pas confondre** :
- `engine/npc_archetypes.py` : 20 archétypes narratifs/sociaux (`NPCArchetype` avec `contradictory_traits`, `dialogue_pattern`) — **MORT**
- `engine/npc_library.py` : builders de `NPCStatBlock` (combat stats) — **VIVANT**, utilisé par `ai/arc_generator.py:13`

Le module `npc_archetypes` est référencé uniquement par `tests/engine/test_npc_archetypes.py`. Aucune intégration côté bot ou ai.

**Recommandation** : **supprimer** `engine/npc_archetypes.py` + `tests/engine/test_npc_archetypes.py`. Si certains hooks narratifs (e.g. `dialogue_pattern`) restent souhaitables, les fusionner dans `npc_library` ou un futur `npc_personality.py`.

### 4. `engine/beat_progression.py::log_shadow_decision` — fonction

**Verdict** : fonction morte post-bascule. Phase D du Beat Progression Engine a retiré l'appel sans nettoyer la fonction.

**Détails** :
- Définie à [engine/beat_progression.py:310](engine/beat_progression.py:310)
- Exportée dans `__all__` à [engine/beat_progression.py:43](engine/beat_progression.py:43)
- **0 appel** dans tout le repo (vérifié par grep)
- Le script `scripts/compare_shadow.py` lit la *clé JSON* `"shadow_decision"` mais n'invoque pas la fonction

**Recommandation** : **supprimer** `log_shadow_decision()` + son entrée dans `__all__`.

### 5. `scripts/compare_shadow.py` — script orphelin

**Verdict** : outil de migration Phase B → Phase D. La bascule étant terminée (cf. `tasks/lessons.md` "Phase D bascule + legacy code removed"), aucun log shadow n'est plus produit. Le script est exécutable mais lit un fichier `.jsonl` qui n'est plus alimenté.

**Références restantes** : uniquement dans le plan de migration `docs/superpowers/plans/2026-04-25-beat-progression-engine.md` (doc historique).

**Recommandation** : **supprimer** ou **archiver** dans `tasks/archive/` avec une note. Couplé à la suppression de `log_shadow_decision`.

## 🟡 Mineurs (cleanup ou documenter)

### Commentaire stale référençant un fichier supprimé

[tests/bot/test_action_pipeline.py:639](tests/bot/test_action_pipeline.py:639) : commentaire `# see tests/test_cog_exploration.py` mais ce fichier a été supprimé (`commit 5681a6b`). Le test équivalent vit maintenant ailleurs.

**Recommandation** : retirer la ligne ou la pointer vers le nouveau test.

### TODO `weapon_proficiency` (45 jours)

[engine/validators.py:284](engine/validators.py:284) — bloc commenté de 4 lignes décrivant un check de proficiency à implémenter quand le système sera là.

**Statut** : aligné avec `tasks/todo.md` section "Différé (à faire plus tard)" → garder en l'état. Ce n'est pas du code mort, c'est de la documentation d'intention.

## ✅ Cas piégés vérifiés vivants (ne PAS supprimer)

- `bot/views/equip_select_view.py` — vivant ([combat_action_view.py:311](bot/views/combat_action_view.py:311))
- `bot/views/potion_select_view.py` — vivant ([combat_action_view.py:288](bot/views/combat_action_view.py:288))
- `ai/beat_judge.py::BeatJudgeResponse.suggested_next_action` — vivant ([hint.py:196](bot/cogs/hint.py:196))
- `ai/narrator_phase.py` — vivant ([combat_turn_manager.py:64](bot/combat_turn_manager.py:64))
- `bot/utils/arc_tracker.py` + `bot/embeds/arc_tracker_embed.py` — vivants (3 fichiers de tests + intégrés au flow campagne)
- `bot/cogs/test_bridge.py` — vivant sous flag `TEST_MODE=true` ([bot.py:65-67](bot/bot.py:65))
- `mcp_discord/` (tout le module) — vivant via `.mcp.json` + `tests/test_tester_bot.py`
- `engine/npc_library.py` — vivant via `ai/arc_generator.py:13`

## 📊 Recommandations actionnables, priorisées (impact / effort)

| # | Action | LoC retirées (~) | Effort | Risque |
|---|---|---|---|---|
| 1 | Supprimer `engine/npc_archetypes.py` + test | ~390 | 5 min | Aucun (0 ref prod) |
| 2 | Supprimer `ai/quest_generator.py` + test + prompt | ~250 | 10 min | Aucun (0 ref prod) |
| 3 | Supprimer `log_shadow_decision` + `scripts/compare_shadow.py` | ~80 | 5 min | Aucun (post-migration) |
| 4 | **Décision produit** : wirer ou supprimer `opening_reframer.py` | ~400 (si suppression) | 30 min | Discuter d'abord — la docstring de `StoryArc.party_premise` promet l'intégration |
| 5 | Nettoyer commentaire `test_action_pipeline.py:639` | 1 ligne | 1 min | Aucun |

**Total potentiel de cleanup** : ~720 LoC + ~400 LoC additionnels si décision de tuer `opening_reframer`.

## Méthodologie

- 6 agents Explore en parallèle (orphelins engine/ai/memory/world/db, orphelins bot/, fonctions/classes non-appelées, tests obsolètes, dead imports/TODO/branches, scripts/mcp/docs)
- Vérification croisée manuelle des 5 claims critiques (anti-faux-positif suite aux erreurs de l'audit principal)
- `uv run ruff check . --select F401` → 0 hit (codebase déjà propre côté imports)
- Couverture : 292 fichiers `.py` (154 hors tests)
