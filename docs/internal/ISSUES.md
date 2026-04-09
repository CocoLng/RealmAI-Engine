# Anomalies, bugs et points d'amélioration

Snapshot 2026-04-09. Classement par sévérité. Chaque entrée inclut la localisation et, si pertinent, une piste de fix.

**Légende** : 🔴 bloquant · 🟠 élevé · 🟡 moyen · 🟢 mineur

---

## 🔴 Bloquants (ou risques de perte de données)

### B1. Perte de session sur crash du bot
**Où** : [bot/bot.py](../../bot/bot.py), `bot.sessions` et `bot.launchers` in-memory only.
**Problème** : un redémarrage ou un crash perd toute campagne active pour laquelle `/save` n'a pas été appelé. Combat en cours, modifs HP, items pickups non persistés disparaissent.
**Fix** : flush auto après chaque action, ou utiliser `combat_state_json` + checkpoints après chaque mutation. Voir aussi B3.

### B2. Runaway thinking mode Qwen 3.5
**Où** : [ai/client.py](../../ai/client.py), `chat_json(..., think=True)`.
**Problème** : pas de budget cap sur la phase de thinking. Un prompt long/ambigu peut faire hang le bot jusqu'au timeout (600s).
**Fix** : ajouter un `num_predict` maximum dédié au thinking, ou un watchdog async côté caller.

---

## 🟠 Sévérité élevée

### H1. `NPCRepository.update()` perd des champs
**Où** : [db/repositories/npc_repo.py](../../db/repositories/npc_repo.py) (~ligne 48-70).
**Problème** : la fonction `update()` ne réécrit pas `aliases`, `secrets`, `knowledge`, `dialogue_history`. Un appel `update(npc)` avec un `dialogue_history` enrichi l'efface.
**Impact** : perte de l'historique de dialogue PNJ — casse la continuité narrative.
**Fix** : ajouter ces champs au `update` mapper.

### H2. Disposition PNJ non persistée si caller oublie
**Où** : [ai/npc_agent.py](../../ai/npc_agent.py) + [bot/action_pipeline.py](../../bot/action_pipeline.py).
**Problème** : `NPCAgent.respond()` retourne un `disposition_change` mais **ne mute pas** `npc.disposition`. Le caller doit appliquer le delta + persister. Un oubli silencieux casse la mémoire sociale.
**Fix** : wrapper qui applique + persist automatiquement, ou test d'intégration qui vérifie la propagation.

### H3. Story Director ne s'auto-déclenche pas
**Où** : [ai/story_director.py](../../ai/story_director.py).
**Problème** : le module ne vérifie pas `interaction_count % 20 == 0` ; c'est au caller (cog) de le faire. Facile à oublier lors d'un refactor.
**Fix** : déplacer le trigger dans `action_pipeline` en post-pipeline inconditionnel + check interne.

### H4. Silent-fail si `SemanticMemory` indisponible
**Où** : [memory/semantic.py](../../memory/semantic.py), [bot/game_session.py](../../bot/game_session.py) `create_ai_services`.
**Problème** : si ChromaDB échoue à l'init (disque plein, permissions, corruption), le Layer 4 et le Story Director sont désactivés **silencieusement**. L'utilisateur ne sait pas qu'il perd la cohérence long-terme.
**Fix** : logger un warning visible, poster un message dans le salon de campagne au launch.

### H5. `WorldGenerator` filtre silencieusement les `item_descriptions` invalides
**Où** : [ai/world_generator.py](../../ai/world_generator.py).
**Problème** : les clés qui ne matchent aucun `items_available` sont droppées sans log. Un bug de prompt ou une hallucination LLM perd des descriptions sans trace.
**Fix** : logger la liste filtrée avec niveau WARNING.

### H6. Beat advancement par fuzzy match seul
**Où** : [bot/game_session.py](../../bot/game_session.py), `advance_beat_if_ready()`.
**Problème** : `difflib.ratio() >= 0.7` sur `current_location.name` vs `beat.location_hint`. Si le `WorldGenerator` nomme les locations différemment de l'`ArcGenerator`, l'arc ne progresse jamais.
**Fix** : contraindre le `WorldGenerator` à accepter le `location_hint` comme nom suggéré, ou centraliser la génération dans un service unique qui réutilise les noms de l'arc.

### H7. Quest generator sans paramètre langue
**Où** : [ai/quest_generator.py](../../ai/quest_generator.py).
**Problème** : pas de `language` param, contrairement à `arc_generator` et `world_generator`. Les quêtes peuvent sortir dans une langue mixte selon l'ambiguité du prompt campagne.
**Fix** : ajouter le param et l'injecter via `language_instruction(language)`.

---

## 🟡 Sévérité moyenne

### M1. Mutation vs copie inconsistante dans `engine/`
**Où** : [engine/character.py](../../engine/character.py), [engine/combat.py](../../engine/combat.py), [engine/spells.py](../../engine/spells.py), vs [engine/inventory.py](../../engine/inventory.py).
**Problème** : `level_up`, `apply_damage`, `cast_spell` mutent en place **et** retournent. `add_item`, `equip_item` retournent une copie. Risque de bugs (double mutation, perdre le résultat).
**Fix** : choisir une convention par module et la documenter en docstring.

### M2. Parsing dé fragile
**Où** :
- [engine/combat.py](../../engine/combat.py) `_double_dice()` — parse string sur `d`
- [engine/spells.py](../../engine/spells.py) `get_cantrip_damage_dice()` — suppose `"1dX"`
**Problème** : fail sur formats inattendus (`"2d6+1"`, `"1d10+DEX"`).
**Fix** : utiliser un mini-parseur commun ou réutiliser `dice.py`.

### M3. Pas d'index SQL sur requêtes fréquentes
**Où** : [db/models.py](../../db/models.py), tables `exchanges`, `summaries`.
**Problème** : `ExchangeRepository.get_recent()` et `get_unsummarized()` scanent sans index. OK à petite échelle, problématique > 1 000 tours.
**Fix** : ajouter un index composite `(campaign_id, interaction_number)`.

### ~~M4. Migration schema brittle~~ ✅ FIXED
**Où** : [db/database.py](../../db/database.py), `_migrate_schema()`.
**Résolu** : migrations versionnées via `PRAGMA user_version`, chaque étape dans une transaction avec rollback automatique. Tests de migration ajoutés dans `test_database.py`.

### ~~M5. `StoryArc` en JSON blob unique~~ ✅ FIXED
**Où** : table `story_arcs`, colonne `arc_json` + nouvelle colonne `current_beat_index`.
**Résolu** : `current_beat_index` extrait en colonne dédiée (migration V2). `StoryArcRepository.update_beat_index()` permet des updates partiels efficaces. La colonne est autoritaire à la lecture.

### ~~M6. Combat bootstrap sans armes pour PNJ~~ ✅ FIXED
**Où** : [bot/cogs/combat.py](../../bot/cogs/combat.py) `build_npc_combatant()`.
**Problème** : les PNJs bootstrappés en combat depuis une attaque free-text combattaient mains nues. Pas d'arme par défaut attachée.
**Fix** : ajout de `default_weapon_for_class()` dans `engine/inventory.py` qui mappe chaque classe à une arme sensible du `ITEM_CATALOG`. `build_npc_combatant()` appelle cette fonction et équipe l'arme en `MAIN_HAND`.

### M7. Trivial kill par heuristique `max_hp < 10`
**Où** : [bot/action_pipeline.py](../../bot/action_pipeline.py) Lot E.
**Problème** : seuil hardcodé. Pas configurable. Ne tient pas compte de l'AC ni des conditions.
**Fix** : constante nommée, et checker `is_defenseless()` qui combine hp/ac/conditions.

### M8. Concentration non interrompue au cast
**Où** : [engine/spells.py](../../engine/spells.py) `cast_spell()`.
**Problème** : lancer un nouveau sort de concentration ne casse pas l'ancien. Bug règle SRD.
**Fix** : auto-break `state.concentration_spell` avant d'en set un nouveau.

### M9. Validators ne checkent pas la proficiency / concentration conflict
**Où** : [engine/validators.py](../../engine/validators.py).
**Problème** : `validate_cast_spell` ne regarde pas `concentration_spell` courant. `validate_attack` ajoute prof bonus sans vérifier la maîtrise de l'arme.
**Fix** : ajouter ces checks ; tests associés.

### M10. Emoji de scène par keyword anglais
**Où** : [bot/embeds/scene_embed.py](../../bot/embeds/scene_embed.py).
**Problème** : le mapping `"dungeon" → ⚔️` fail pour les noms de location en français (qui sont la norme).
**Fix** : mapping bilingue, ou lookup sur `encounter_type` du beat courant plutôt que sur le nom.

### M11. `NPCSheet` generation sans validation non-vide
**Où** : [ai/npc_generator.py](../../ai/npc_generator.py).
**Problème** : `secrets` et `knowledge` peuvent sortir vides sans erreur. Le PNJ sera alors muet sur ces axes.
**Fix** : Pydantic `min_length=1` + fallback si vide.

---

## 🟢 Mineurs

### L1. Dead code
- [engine/character.py](../../engine/character.py) `compute_ac(character)` — retourne 10 + DEX, ignore l'armure. Jamais utilisé en prod.
- [engine/conditions.py](../../engine/conditions.py) `ActiveCondition.save_ability` et `save_dc` — jamais lus par aucune fonction.

### L2. Constantes magiques dispersées
- Thresholds outcome d20 (`-5`, `0`, `5`) dans `dice.py` — pas de constantes nommées.
- Attunement max `3` dans `inventory.py`.
- Cantrip scale `[(17,4),(11,3),(5,2),(1,1)]` dans `spells.py`.
- Fuzzy thresholds `0.75` et `0.7` dans `entity_resolver.py` et `game_session.py`.
- Budget tokens `450/700/400/350/2500` dans `memory/models.py` — OK car dans `ContextBudget`, mais pas exposés config.

### L3. Remove condition raise ValueError
**Où** : [engine/conditions.py](../../engine/conditions.py), `remove_condition()`.
**Problème** : raise si la condition n'est pas présente. Pattern incohérent avec les fonctions soft-delete ailleurs.
**Fix** : retourner la liste inchangée ou un bool.

### L4. `ExhaustionLevel` hardcodé à 6
Pas de constante `MAX_EXHAUSTION_LEVEL`.

### L5. Orphan ChromaDB collections
Si une campagne est supprimée, la collection ChromaDB `campaign_<id>` n'est pas nettoyée. Peu grave actuellement (pas de `/delete campaign`), à prévoir si on l'implémente.

### ~~L6. `starter_gear.apply_starter_kit` : auto-equip fragile~~ ✅ PARTIALLY FIXED
- ~~Lookup `"Shield"` par string~~ → remplacé par détection par type `item.item_type == ItemType.SHIELD`.
- Si kit a 2+ armes, seule la première est équipée (pas de dual-wield auto).
- Pas de gestion multi-armor (ex. casque + armure corps).

### L7. `language` dans `guild_configs` stocké mais peu utilisé
Le champ existe, est sauvé, mais l'i18n dynamique réelle dépend de la compliance du LLM (il n'y a pas de fallback Python si le narrator produit en anglais).

### L8. `confidence` d'`InterpretedAction` non validé
Le champ est attendu en `[0.0, 1.0]` mais aucun validator Pydantic ne le contraint.

### L9. Pas de dédup des hooks du Story Director
Les mêmes hooks peuvent revenir à plusieurs checks successifs, polluant le RAG.

### L10. Empty scene handling
`build_scene_context(location=None, …)` retourne `location_name=""` — un caller qui ne check pas peut se retrouver avec un contexte vide silencieux.

---

## Améliorations non-bugs (nice-to-have)

- **Streaming** du Narrator pour latence perçue (actuellement tout le narratif arrive en un bloc après ~10-20s).
- **Narrator cache** pour actions répétitives (LOOK sur même location).
- **Prompt tokenizer réel** (tiktoken-like) pour remplacer `word_count * 1.3`.
- **Extract `ITEM_CATALOG` et `SPELL_CATALOG`** dans des YAML éditables.
- **Config du budget mémoire** par campagne (actuellement global dans `ContextBudget`).
- **Alembic** si besoin de renommage/suppression de colonnes (migrations actuelles via `PRAGMA user_version` suffisent pour les ajouts).
- **Métriques** Prometheus/OpenTelemetry.
- **Dashboard admin** pour inspecter `GameSession` live.
- **Tests d'intégration vrai Ollama** en CI optionnelle.
- **Entity resolver multilingue** — actuellement les lemmes sont FR only, extensibles via stratégie pluggable.

---

## Priorisation suggérée pour les prochaines sessions

1. **H1** — fix `NPCRepository.update()` — 10 minutes de travail, impact continuité narrative.
2. **H2** — wrapper d'application de `disposition_change` + test — 30 minutes.
3. **B2** — watchdog sur thinking mode — 1 heure.
4. **H3** — auto-trigger Story Director — 30 minutes.
5. **H4/H5** — logger les silent fails — 20 minutes.
6. **M1** — choisir et documenter convention mutation/copie en docstring — 1 heure.
7. **M6** — attacher weapon par défaut au bootstrap combat — 2 heures.
8. **B1** — checkpointing auto des sessions — plus gros chantier, ~1 jour.
