# Anomalies, bugs et points d'amelioration

Snapshot 2026-04-09. Classement par severite.

**Legende** : 🔴 bloquant · 🟠 eleve · 🟡 moyen · 🟢 mineur

---

## 🔴 Bloquants

### B2. Runaway thinking mode Qwen 3.5
**Ou** : [ai/client.py](../../ai/client.py), `chat_json(..., think=True)`.
**Probleme** : pas de budget cap sur la phase de thinking. Un prompt long/ambigu peut faire hang le bot jusqu'au timeout (600s).
**Fix** : ajouter un `num_predict` maximum dedie au thinking, ou un watchdog async cote caller.

---

## 🟠 Severite elevee

### H1. `NPCRepository.update()` perd des champs
**Ou** : [db/repositories/npc_repo.py](../../db/repositories/npc_repo.py) (~ligne 48-70).
**Probleme** : la fonction `update()` ne reecrit pas `aliases`, `secrets`, `knowledge`, `dialogue_history`. Un appel `update(npc)` avec un `dialogue_history` enrichi l'efface.
**Impact** : perte de l'historique de dialogue PNJ — casse la continuite narrative.
**Fix** : ajouter ces champs au `update` mapper.

### H2. Disposition PNJ non persistee si caller oublie
**Ou** : [ai/npc_agent.py](../../ai/npc_agent.py) + [bot/action_pipeline.py](../../bot/action_pipeline.py).
**Probleme** : `NPCAgent.respond()` retourne un `disposition_change` mais **ne mute pas** `npc.disposition`. Le caller doit appliquer le delta + persister. Un oubli silencieux casse la memoire sociale.
**Fix** : wrapper qui applique + persist automatiquement, ou test d'integration qui verifie la propagation.

### H3. Story Director ne s'auto-declenche pas
**Ou** : [ai/story_director.py](../../ai/story_director.py).
**Probleme** : le module ne verifie pas `interaction_count % 20 == 0` ; c'est au caller (cog) de le faire. Facile a oublier lors d'un refactor.
**Fix** : deplacer le trigger dans `action_pipeline` en post-pipeline inconditionnel + check interne.

### H4. Silent-fail si `SemanticMemory` indisponible
**Ou** : [memory/semantic.py](../../memory/semantic.py), [bot/game_session.py](../../bot/game_session.py) `create_ai_services`.
**Probleme** : si ChromaDB echoue a l'init (disque plein, permissions, corruption), le Layer 4 et le Story Director sont desactives **silencieusement**. L'utilisateur ne sait pas qu'il perd la coherence long-terme.
**Fix** : logger un warning visible, poster un message dans le salon de campagne au launch.

### H5. `WorldGenerator` filtre silencieusement les `item_descriptions` invalides
**Ou** : [ai/world_generator.py](../../ai/world_generator.py).
**Probleme** : les cles qui ne matchent aucun `items_available` sont droppees sans log. Un bug de prompt ou une hallucination LLM perd des descriptions sans trace.
**Fix** : logger la liste filtree avec niveau WARNING.

### H7. Quest generator sans parametre langue
**Ou** : [ai/quest_generator.py](../../ai/quest_generator.py).
**Probleme** : pas de `language` param, contrairement a `arc_generator` et `world_generator`. Les quetes peuvent sortir dans une langue mixte selon l'ambiguite du prompt campagne.
**Fix** : ajouter le param et l'injecter via `language_instruction(language)`.

---

## 🟡 Severite moyenne

### M1. Mutation vs copie inconsistante dans `engine/`
**Ou** : [engine/character.py](../../engine/character.py), [engine/combat.py](../../engine/combat.py), [engine/spells.py](../../engine/spells.py), vs [engine/inventory.py](../../engine/inventory.py).
**Probleme** : `level_up`, `apply_damage`, `cast_spell` mutent en place **et** retournent. `add_item`, `equip_item` retournent une copie. Risque de bugs (double mutation, perdre le resultat).
**Fix** : choisir une convention par module et la documenter en docstring.

### M2. Parsing de fragile
**Ou** :
- [engine/combat.py](../../engine/combat.py) `_double_dice()` — parse string sur `d`
- [engine/spells.py](../../engine/spells.py) `get_cantrip_damage_dice()` — suppose `"1dX"`
**Probleme** : fail sur formats inattendus (`"2d6+1"`, `"1d10+DEX"`).
**Fix** : utiliser un mini-parseur commun ou reutiliser `dice.py`.

### M3. Pas d'index SQL sur requetes frequentes
**Ou** : [db/models.py](../../db/models.py), tables `exchanges`, `summaries`.
**Probleme** : `ExchangeRepository.get_recent()` et `get_unsummarized()` scanent sans index. OK a petite echelle, problematique > 1 000 tours.
**Fix** : ajouter un index composite `(campaign_id, interaction_number)`.

### M7. Trivial kill par heuristique `max_hp < 10`
**Ou** : [bot/action_pipeline.py](../../bot/action_pipeline.py) Lot E.
**Probleme** : seuil hardcode. Pas configurable. Ne tient pas compte de l'AC ni des conditions.
**Fix** : constante nommee, et checker `is_defenseless()` qui combine hp/ac/conditions.

### M8. Concentration non interrompue au cast
**Ou** : [engine/spells.py](../../engine/spells.py) `cast_spell()`.
**Probleme** : lancer un nouveau sort de concentration ne casse pas l'ancien. Bug regle SRD.
**Fix** : auto-break `state.concentration_spell` avant d'en set un nouveau.

### M9. Validators ne checkent pas la proficiency / concentration conflict
**Ou** : [engine/validators.py](../../engine/validators.py).
**Probleme** : `validate_cast_spell` ne regarde pas `concentration_spell` courant. `validate_attack` ajoute prof bonus sans verifier la maitrise de l'arme.
**Fix** : ajouter ces checks ; tests associes.

### M10. Emoji de scene par keyword anglais
**Ou** : [bot/embeds/scene_embed.py](../../bot/embeds/scene_embed.py).
**Probleme** : le mapping `"dungeon" -> ⚔️` fail pour les noms de location en francais (qui sont la norme).
**Fix** : mapping bilingue, ou lookup sur `encounter_type` du beat courant plutot que sur le nom.

### M11. `NPCSheet` generation sans validation non-vide
**Ou** : [ai/npc_generator.py](../../ai/npc_generator.py).
**Probleme** : `secrets` et `knowledge` peuvent sortir vides sans erreur. Le PNJ sera alors muet sur ces axes.
**Fix** : Pydantic `min_length=1` + fallback si vide.

---

## 🟢 Mineurs

### L1. Dead code
- [engine/character.py](../../engine/character.py) `compute_ac(character)` — retourne 10 + DEX, ignore l'armure. Jamais utilise en prod.
- [engine/conditions.py](../../engine/conditions.py) `ActiveCondition.save_ability` et `save_dc` — jamais lus par aucune fonction.

### L2. Constantes magiques dispersees
- Thresholds outcome d20 (`-5`, `0`, `5`) dans `dice.py` — pas de constantes nommees.
- Attunement max `3` dans `inventory.py`.
- Cantrip scale `[(17,4),(11,3),(5,2),(1,1)]` dans `spells.py`.
- Budget tokens `450/700/400/350/2500` dans `memory/models.py` — OK car dans `ContextBudget`, mais pas exposes config.

### L3. Remove condition raise ValueError
**Ou** : [engine/conditions.py](../../engine/conditions.py), `remove_condition()`.
**Probleme** : raise si la condition n'est pas presente. Pattern incoherent avec les fonctions soft-delete ailleurs.
**Fix** : retourner la liste inchangee ou un bool.

### L4. `ExhaustionLevel` hardcode a 6
Pas de constante `MAX_EXHAUSTION_LEVEL`.

### L6. `starter_gear.apply_starter_kit` : auto-equip restant
- Si kit a 2+ armes, seule la premiere est equipee (pas de dual-wield auto).
- Pas de gestion multi-armor (ex. casque + armure corps).

### L8. `confidence` d'`InterpretedAction` non valide
Le champ est attendu en `[0.0, 1.0]` mais aucun validator Pydantic ne le contraint.

### L10. Empty scene handling
`build_scene_context(location=None, ...)` retourne `location_name=""` — un caller qui ne check pas peut se retrouver avec un contexte vide silencieux.

---

## Ameliorations non-bugs (nice-to-have)

- **Streaming** du Narrator pour latence percue (actuellement tout le narratif arrive en un bloc apres ~10-20s).
- **Narrator cache** pour actions repetitives (LOOK sur meme location).
- **Prompt tokenizer reel** (tiktoken-like) pour remplacer `word_count * 1.3`.
- **Extract `ITEM_CATALOG` et `SPELL_CATALOG`** dans des YAML editables.
- **Config du budget memoire** par campagne (actuellement global dans `ContextBudget`).
- **Alembic** si besoin de renommage/suppression de colonnes (migrations actuelles via `PRAGMA user_version` suffisent pour les ajouts).
- **Metriques** Prometheus/OpenTelemetry.
- **Dashboard admin** pour inspecter `GameSession` live.
- **Tests d'integration vrai Ollama** en CI optionnelle.
- **Entity resolver multilingue** — actuellement les lemmes sont FR only, extensibles via strategie pluggable.

---

## Priorisation suggeree pour les prochaines sessions

1. **H1** — fix `NPCRepository.update()` — 10 minutes, impact continuite narrative.
2. **H2** — wrapper d'application de `disposition_change` + test — 30 minutes.
3. **B2** — watchdog sur thinking mode — 1 heure.
4. **H3** — auto-trigger Story Director — 30 minutes.
5. **H4/H5** — logger les silent fails — 20 minutes.
6. **M8** — concentration auto-break — 30 minutes, bug regle SRD.
7. **M1** — choisir et documenter convention mutation/copie — 1 heure.
