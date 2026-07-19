# Cohérence narrative

Comment le projet maintient la **cohérence** d'une campagne : canon respecté, PNJs qui évoluent logiquement, arc qui progresse, et un filet de sécurité via le Story Director.

## 1. Canon verrouillé (locked facts)

Le principe : **le code est seul propriétaire de la vérité mécanique et factuelle ; le LLM n'a que droit de description.**

### Sources de vérité

| Fait | Source | Écrit par |
|---|---|---|
| HP / AC / inventory | `Character` + `Inventory` in-memory + DB | `engine/` |
| Disposition PNJ | `NPC.disposition` | `ai.npc_agent` (retourne un delta) → appliqué par `action_pipeline` → persisté |
| Nom, description, exits d'une location | `Location` | `ai.world_generator` (une fois) puis immuable |
| Items d'une location | `Location.items_available` + `item_descriptions` | `world_generator` (avec validation stricte) |
| État d'arc | `StoryArc.current_beat_index` | `BeatProgressionEngine` (via l'orchestrateur) |
| State flags location | `Location.state_flags` | `action_pipeline._apply_beat_effects()` |
| Sorties débloquées | `Location.unlocked_exits` | `action_pipeline._apply_beat_effects()` |
| Mort d'un PNJ | `NPC.is_alive` | `engine.combat.trivial_resolve()` ou apply_damage |

### Protection dans les prompts

- `system_narrator.txt` contient explicitement :
  - « La description de la location et des items sont ABSOLUS et CANONIQUES — ne contredis jamais. »
  - « Si tu vois `<NPCName> dit : "…"` dans les State changes, tu DOIS reproduire ce dialogue VERBATIM entre guillemets dans ta narration. » (règle Lot F)
  - « Le `player_intent` est comment le joueur a phrasé son action — c'est du style, pas une vérité. »
- `system_world_generator.txt` force : chaque item doit avoir une description explicite (matériau, époque, état) — pas de « une petite chose » générique.

### Validation défensive

- `WorldGenerator.generate()` filtre silencieusement les `item_descriptions` dont la clé ne correspond à aucun item de `items_available`. Empêche une hallucination du LLM de polluer le canon. ⚠ Silencieux — idéalement logger, voir [ISSUES.md](ISSUES.md).
- `Narrator` attend un JSON strict `{narrative, tone}`. Sur échec de parse, retry + dump sur disque.

### Contexte narrateur en combat

Quand un combat est actif, [bot/scene_hydration.py::describe_scene_for_narrator](../../bot/scene_hydration.py) injecte une section `## COMBAT ACTIVE` dans le contexte passé au narrateur. Elle liste le round courant, le combattant de tour, chaque participant (HP exact pour les PCs, tier vague `indemne / légèrement blessé / gravement blessé / à l'article de la mort` pour les NPCs), la zone, les conditions actives, et l'archétype + tier du stat block ennemi. Les trois derniers événements mécaniques sont exposés via `CombatState.recent_events` — une liste cap-12 alimentée par le bot après chaque résolution (`engine.combat.record_combat_event`). Le bloc `## Acting character` est aussi enrichi en toutes circonstances avec race/classe/niveau/arme équipée pour que le narrateur puisse dire « le clerc nain abat sa masse » plutôt que « le joueur attaque ». [ai/prompts/system_narrator.txt](../../ai/prompts/system_narrator.txt) déclare les règles de narration spéciales (miss=miss, tour par tour, ton tendu, HP NPC vagues, invitation au tour suivant).

Les transitions de phase boss sont narrées via un chemin dédié [ai/narrator_phase.py::narrate_phase_transition](../../ai/narrator_phase.py) qui utilise le prompt [ai/prompts/system_narrator_phase.txt](../../ai/prompts/system_narrator_phase.txt) et retourne une prose cinématique 3-5 phrases. Le hook vit dans [bot/combat_turn_manager.py::_flush_pending_cues](../../bot/combat_turn_manager.py) : après chaque tour, les `PhaseTransitionEvent` non-consommés sont narrés et postés comme embeds dorés (`0xF1C40F`, titre « ✨ Phase transition — {boss} »). `event.consumed = True` est marqué **avant** l'appel LLM pour éviter toute double-narration sur retry ; sur échec narrateur ou client Ollama absent, on retombe gracieusement sur le `narrative_cue` brut du stat block.

## 2. PNJs — disposition et dialogue

Défini dans [ai/npc_agent.py](../../ai/npc_agent.py) + `ai/prompts/system_npc_agent.txt`.

### Modèle `NPC` ([world/npc.py](../../world/npc.py))

Champs pertinents pour la cohérence :
- `disposition` ∈ {`HOSTILE`, `UNFRIENDLY`, `NEUTRAL`, `FRIENDLY`, `ALLIED`}
- `secrets[]` — info dangereuse, révélée seulement sous haute confiance
- `knowledge[]` — info publique, partagée largement
- `dialogue_history[]` — 5 derniers échanges (`DialogueExchange(player_said, npc_said, revealed[])`)
- `personality`, `description`, `aliases[]` — générés par `NPCGenerator` à la première rencontre

### Flux d'un `TALK`

1. **Lazy generation** : si le PNJ n'a pas de sheet (personnalité, secrets, knowledge), `NPCGenerator.generate(name, location_context, campaign_theme)` est appelé (pipeline a intégré cela via Lot A / scene hydration).
2. **`NPCAgent.respond(npc, player_input, context_prompt)`** → `NPCResponse(dialogue, disposition_change, revealed_info)`
   - Le prompt système impose :
     - Knowledge partagé généreusement (même à faible disposition, sauf `HOSTILE`).
     - Secrets révélés seulement si `FRIENDLY + ≥2 positive exchanges` ou si le joueur tape pile sur le sujet.
     - Disposition peut **remonter** après un geste correctif — pas de verrou descendant.
   - `disposition_change` ∈ [-2, +2].
3. **Application côté pipeline** : `npc.disposition = clamp(current + delta)`. Les `revealed_info` sont ajoutés au `dialogue_history`.
4. **Narrator reçoit le dialogue** dans les `outcome_facts` : `{npc_name} dit : "..."` → il DOIT le reproduire verbatim (règle Lot F).

### Contagion d'hostilité

Si un joueur tue un PNJ pacifique devant témoins, les témoins (`disposition >= FRIENDLY` présents dans la même location) basculent en `HOSTILE`. Logique simpliste, dans `bot/action_pipeline.py` (pas de système de factions ou de témoins complexe).

## 3. Story arc — beats et progression

Défini dans [world/story_arc.py](../../world/story_arc.py) + [ai/arc_generator.py](../../ai/arc_generator.py).

### Structure

```python
StoryArc(
    campaign_id: str,
    theme: str,
    premise: str,
    beats: list[StoryBeat],   # 10-15 beats, dernier = encounter_type=boss
    current_beat_index: int,
    villain_name: str,
    villain_motivation: str,
    villain_stat_block: NPCStatBlock | None,  # stat block complet custom
)

StoryBeat(
    beat_number: int,
    title: str,
    description: str,
    location_hint: str,           # nom approximatif de la location attendue
    npc_names: list[str],
    encounter_type: Literal["social", "combat", "exploration", "puzzle", "boss"],
    encounter_subtype: str | None,
    is_twist: bool,
    objectives: list[BeatObjective],               # objectifs structurés (source de vérité)
    advance_rule: AdvanceRule,                      # ALL_REQUIRED / M_OF_N
    completion_trigger: CompletionTrigger | None,  # legacy — auto-migré en BeatObjective au chargement
    on_complete: BeatEffects,                       # mutations monde à appliquer
)
```

L'arc est généré **une fois** à la création de la campagne, avec `think=True` (mode raisonnement étendu de Qwen 3.5) pour la cohérence narrative. Le même appel LLM produit aussi le `villain_stat_block` complet du villain (tier boss, 2-3 signatures thématiques, 3 legendary_actions costs 1/2/3, 1-2 phases) — permettant au climax final de jouer des capacités uniques au villain plutôt qu'un generic_boss. Fallback automatique sur `engine.npc_library.get_archetype('generic_boss')` (tagué `generic_boss:<villain_name>`) si l'output LLM casse la validation Pydantic.

### Avancement

Point de décision unique : **`BeatProgressionEngine.evaluate()`** ([engine/beat_progression.py](../../engine/beat_progression.py)), appelé par `bot/pipeline/orchestrator.py` après chaque action résolue.

**1. Évaluation déterministe** :
- Chaque beat porte des `BeatObjective` structurés, générés par l'arc generator (les `completion_trigger` legacy sont auto-migrés au chargement par `world/story_arc.py::_migrate_legacy_completion_triggers`).
- L'engine confronte l'action interprétée, l'outcome, la location, les flags monde et l'inventaire aux objectifs selon l'`advance_rule` (ALL_REQUIRED / M_OF_N), et rend `ADVANCE`, `STAY` ou `NEEDS_JUDGE`.
- Les objectifs complétés sans avancement sont accumulés sur `beat.objectives_completed` — les beats multi-actions progressent tour après tour.

**2. Arbitrage LLM — `BeatJudge`** ([ai/beat_judge.py](../../ai/beat_judge.py)) :
- Sollicité uniquement quand l'engine rend `NEEDS_JUDGE`.
- Avance si `passed == true AND confidence ≥ 0.7`.
- Le code reste l'arbitre final.

Sur `ADVANCE` → `_apply_beat_effects(beat.on_complete)` mute la `Location` (unlock exits, add/remove items/NPCs, set state_flags) → `advance_beat()` → post d'un `beat_embed` + persist arc.

## 4. Story Director — coherence check périodique

Défini dans [ai/story_director.py](../../ai/story_director.py) + `ai/prompts/system_story_director.txt`.

### Déclenchement

Cadence primaire : planifié par l'orchestrateur via `should_run_director` (`bot/pipeline/orchestrator.py`) — toutes les 6 interactions, ou dès qu'un combat vient de se terminer, qu'un drift narratif est détecté (`DriftTracker`), ou sur demande explicite (force, ex. `/story_catch_up`). Un chemin legacy toutes les 20 interactions subsiste dans `bot/story_bible_logger.py::record_turn_and_maybe_check`.

### Fonctionnement

1. `context_prompt` = sortie complète de `ContextAssembler` (4 couches) — donc le directeur voit l'état + l'historique + les résumés + le RAG.
2. Appel Qwen 3.5 9B avec `think=True`.
3. Sortie : `DirectorNote`
   ```python
   DirectorNote(
       coherence_issues: list[str],    # "le PNJ X est mort au tour 12 mais apparaît au tour 18"
       suggested_hooks: list[str],     # "et si le villageois révélait qu'il connaît le villain ?"
       priority: Literal["low", "medium", "high"],
   )
   ```
4. **Side-effect** : stocké en `SemanticMemory` comme `SemanticDocument(doc_type=DirectorNote metadata)` pour consommation au prochain assemblage de contexte — le Narrator et l'Interpreter peuvent voir ces notes lors des tours suivants, ce qui pousse la suite de la campagne à intégrer les hooks.
5. Logué dans la story bible (`story_bible_logger.log_coherence_check()`).

### Limites connues

- ~~Pas de dédup~~ : les hooks sont maintenant dédupliqués (normalize + dict.fromkeys) dans `check_coherence()` avant stockage.
- Pas de remédiation automatique : le directeur **signale** les incohérences, il ne les corrige pas.
- `SemanticMemory` indisponible (ChromaDB cassé) → directeur désactivé silencieusement.

## 5. Story Bible — audit append-only

Défini dans [bot/story_bible_logger.py](../../bot/story_bible_logger.py). Un fichier Markdown par campagne dans `logs/campaigns/<campaign_id>.md`.

### Sections

1. **Header** (écrit une fois au launch) :
   - Nom de campagne, ID, date.
   - Liste des personnages joueurs (race, classe).
   - Arc complet : thème, premise, villain + motivation.
   - **Tous les beats** : numéro, titre, encounter type, twist flag, location hint, NPCs attendus.
   - Location de départ (nom, description, exits, PNJs, items).

2. **Journal** (append, 1 entrée par tour) :
   - Turn number, timestamp, acteur, beat marker.
   - Commande (brute).
   - Mechanics summary.
   - Narrative excerpt (400 premiers caractères).

3. **Coherence checks** (quand le Director tourne — voir §4 pour la cadence) :
   - Issues + hooks du Story Director.
   - Priority flag.

4. **World events** (à la demande) :
   - Morts de PNJ, basculements de faction.

Thread-safe (`threading.Lock`). **Durabilité > throughput** — écriture synchrone, pas de buffer.

Sert deux buts :
- **Pour le joueur/MJ humain** : relire sa campagne, écrire un blog post.
- **Pour l'agent** : analyser post-mortem ce qui a cassé (utilisé pendant les lots A-F).

## 6. Validation avant canonisation

Quelques barrières qui empêchent le LLM de casser le canon :

- `WorldGenerator.generate()` : `item_descriptions` filtré par intersection avec `items_available`.
- `ArcGenerator` : prompt force le dernier beat en `boss` (pas enforced code).
- `NPCAgent.respond()` : `disposition_change` clamped par l'application post-call.
- `Narrator` : JSON strict ; un fallback narratif est posté si LLMParseError.
- `Interpreter` : sur parse failure → `DEFEND`/`IMPROVISE` déterministe.
- `EntityResolver` : si entité inconnue → `UnknownEntityResult` narré comme refus in-character, **pas de création dynamique**.

## 7. Dette / gaps identifiés

Voir [ISSUES.md](ISSUES.md) pour le détail. Extraits :

- Pas d'enforcement code du beat boss final.
- Pas de dédup des hooks du Story Director.
- ~~Pas de check de cohérence arc/world~~ : les `location_hint` de l'arc sont maintenant passés au `WorldGenerator` via `location_hints`, qui instruit le LLM de réutiliser ces noms.
- La contagion d'hostilité est naïve (pas de témoins hors location, pas de propagation retardée).
- ~~Le `NPC.update()` repository perdait `dialogue_history`, `secrets`, `knowledge`, `aliases`~~ — corrigé.
- ~~Story Director ne tourne pas si `SemanticMemory` est indisponible (silent fail)~~ — corrigé, log WARNING.
