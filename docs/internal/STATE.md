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

## Lots post-mortem campagne 1 (tasks/agents/)

Suite à une première campagne live (2026-04-07) avec 7 actions et 0 mutations significatives, le code a été refactoré en 6 lots parallèles — **tous complétés** :

| Lot | Sujet | État | Impact |
|---|---|---|---|
| A | Scene awareness | 🟢 Done | Scene embed au launch + post-MOVE, PNJs list[str] traités correctement |
| B | Entity resolution | 🟢 Done | Lemmes FR + fuzzy + fallback LLM, 35 tests verts |
| C | Combat initiation | 🟢 Done | Bootstrap `CombatState` depuis attaque free-text |
| D | Story progression | 🟢 Done | Avancement de beat par fuzzy match location (0.7) |
| E | Trivial NPC death | 🟢 Done | One-shot resolve pour PNJs faibles/pacifiques |
| F | Narrator JSON | 🟢 Done | Prompt durci, `LLMParseError` avec dump auto dans `logs/narrator_failures/` |

## Fonctionnalités implémentées

### Moteur de règles
- ✅ Dés (parseur `NdM+X`, d20 checks 6 tiers)
- ✅ Personnages (7 races, 6 classes, 9 alignments, 20 niveaux, XP, level-up)
- ✅ Inventaire (25+ items catalogue, 9 slots, attunement max 3)
- ✅ Armes et armures (4 catégories armes, 3 catégories armures, shield +2)
- ✅ Sorts (~20 sorts catalogue, slots full/half caster, cantrip scaling)
- ✅ Conditions (15 conditions SRD, durations, effets advantage/disadvantage)
- ✅ Combat (initiative, attaques, crits, death saves, sorts avec saves)
- ✅ Trivial resolve (Lot E)
- ✅ Starter kits (15 kits sur 6 classes)
- ✅ Validators (combat + exploration)

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
| `/save` / `/resume` | 🟡 | Tests basiques OK, pas tous les edge cases (combat actif, sessions concurrentes) |
| Combat state persistance | 🟡 | Sérialisé en JSON dans `campaigns.combat_state_json` mais pas de test cross-restart |
| i18n dynamique | 🟡 | Labels statiques OK ; contenu dynamique repose sur la compliance du prompt |
| Story Director | 🟡 | Implémenté mais ne s'auto-déclenche pas ; silent fail si ChromaDB down |
| Initiative complète | 🟡 | Roll d'initiative présent mais ordre = surprise attacker first dans bootstrap |
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
