# Anomalies, bugs et points d'amelioration

Snapshot 2026-04-09. Classement par severite.

**Legende** : 🔴 bloquant · 🟠 eleve · 🟡 moyen · 🟢 mineur · ✅ resolu

---

## ✅ Resolus

### B2. Runaway thinking mode Qwen 3.5 ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Restructuration en chaine 2-phases (brainstorm → generate) pour les 4 modules thinking (arc, world, quest, story_director). Ajout du param `thinking_budget` per-call dans `chat_json()`. Fallback graceful si le brainstorm echoue. Prompts brainstorm dedies crees dans `ai/prompts/`.

### H1. `NPCRepository.update()` perd des champs ✅
**Statut** : Deja resolu — `npc_repo.py:58-73` mappe tous les champs (aliases, secrets, knowledge, dialogue_history).

### H2. Disposition PNJ non persistee si caller oublie ✅
**Statut** : Deja resolu — disposition appliquee dans `action_pipeline.py:856-868` et persistee via `npc_repo.update()` + `commit()`.

### H3. Story Director ne s'auto-declenche pas ✅ (partiel)
**Statut** : Partiellement resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : `record_turn_and_maybe_check` ajoute dans `action_handler.py` (etait deja wire dans combat.py et exploration.py). Le trigger existe via `story_bible_logger.py`.
**Reste** : verifier que `interaction_count` est incremente correctement dans tous les flows.

### H4. Silent-fail si `SemanticMemory` indisponible ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Ajout de `ai_warnings: list[str]` sur `GameSession`. Warning affiche dans le salon de campagne au lancement (`session.py` et `campaign_launcher.py`).

### H5. `WorldGenerator` filtre silencieusement les `item_descriptions` invalides ✅
**Statut** : Deja resolu — `world_generator.py:69-74` log un WARNING avec la liste des cles filtrees.

### H7. Quest generator sans parametre langue ✅
**Statut** : Deja resolu — `quest_generator.py:32` a deja `language: str = "fr"` avec `language_instruction()`.

### M1. Mutation vs copie inconsistante dans `engine/` ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : `inventory.py` converti du pattern immutable (`model_copy`) vers mutation in-place, alignant avec character.py, combat.py, spells.py. 6 fonctions converties, ~40 call sites mis a jour.

### M3. Pas d'index SQL sur requetes frequentes ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Index composites ajoutes — `(campaign_id, interaction_number)` sur ExchangeRow, `(campaign_id, start_interaction)` sur SummaryRow.

### M7. Trivial kill par heuristique `max_hp < 10` ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Helper `is_trivially_defeatable(npc)` verifie HP < seuil AND AC <= 12 AND pas de conditions defensives. Constantes nommees `TRIVIAL_RESOLVE_HP_THRESHOLD` et `TRIVIAL_RESOLVE_AC_THRESHOLD`.

### M8. Concentration non interrompue au cast ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : `cast_spell()` log et casse l'ancienne concentration avant d'en set une nouvelle.

### M10. Emoji de scene par keyword anglais ✅
**Statut** : Deja resolu — `scene_embed.py` a deja un mapping bilingue FR/EN dans `_TYPE_EMOJI`.

### M11. `NPCSheet` generation sans validation non-vide ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Validation non-vide pour `secrets` et `knowledge` apres generation LLM. Fallbacks generiques si vide.

### L1. Dead code ✅
**Statut** : Deja resolu — `compute_ac`, `save_ability`, `save_dc` deja supprimes.

### L2. Constantes magiques dispersees ✅ (partiel)
**Statut** : Partiellement resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : `_CARRYING_CAPACITY_MULTIPLIER = 15.0` extraite dans `inventory.py`.
**Reste** : les autres constantes (cantrip scale, token budgets) sont deja dans des constantes nommees.

### L3. Remove condition raise ValueError ✅
**Statut** : Deja resolu — `remove_condition()` retourne la liste inchangee avec warning (pas de ValueError).

### L4. `ExhaustionLevel` hardcode a 6 ✅
**Statut** : Deja resolu — `MAX_EXHAUSTION_LEVEL = 6` deja extrait comme constante.

### L6. `starter_gear.apply_starter_kit` : auto-equip restant ✅
**Statut** : Resolu — `fix/issues-batch-2026-04-09`
**Fix applique** : Commentaires step-by-step clarifiants la logique auto-equip.

### L8. `confidence` d'`InterpretedAction` non valide ✅
**Statut** : Deja resolu — `Field(ge=0.0, le=1.0)` dans `ai/models.py:20`.

### L10. Empty scene handling ✅
**Statut** : Deja resolu — `build_scene_context(location=None)` gere proprement dans `ai/scene_context.py:49`.

---

## 🟡 Severite moyenne (restants)

### M2. Parsing de fragile
**Ou** :
- [engine/combat.py](../../engine/combat.py) `_double_dice()` — parse string sur `d`
- [engine/spells.py](../../engine/spells.py) `get_cantrip_damage_dice()` — suppose `"1dX"`
**Probleme** : fail sur formats inattendus (`"2d6+1"`, `"1d10+DEX"`).
**Note** : les deux utilisent deja `parse_dice()` de `dice.py` (parseur canonique regex). Risque faible tant que les expressions restent simples.

### M9. Validators ne checkent pas la proficiency / concentration conflict
**Ou** : [engine/validators.py](../../engine/validators.py).
**Statut** : Partiellement resolu.
- Concentration conflict : deja implemente (log info quand conflit detecte).
- Weapon proficiency : TODO ajoute, en attente du systeme de `weapon_proficiencies` sur Character.

---

## 🟢 Mineurs (restants)

Aucun.

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
