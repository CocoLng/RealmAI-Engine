# Lot B — Entity Resolution robuste

> Index : [`README.md`](README.md) · Statut : **TODO** · Pré-requis : aucun

## Pourquoi ce lot existe

Lors de la première campagne live (2026-04-07), **aucune** interaction PNJ n'a fonctionné. Les PNJ générés s'appellent « Jeanne, la Villageoise Terrifiée » et « Père Thomas, le Moine Loyal ». Les joueurs ont tapé « le villageur », « les villageurs », « le villageur » — termes naturels en français. Le résolveur d'entité fait du **token-subset matching strict** : il vérifie si tous les tokens de la query sont présents dans le nom candidat. `{villageur} ⊄ {jeanne, la, villageoise, terrifiee}` → échec.

Pas de lemmatisation française (genre/nombre : villageur ↔ villageoise ↔ villageois ↔ villageoises), pas de fuzzy matching, pas d'alias d'archétype sur les PNJ. Le 4B fait pourtant son boulot correctement (il extrait `target_name="le villageur"` avec `confidence=0.90`) — c'est le code Python en aval qui n'arrive pas à mapper.

## Mission

Faire que la requête « le villageur » résolve vers « Jeanne, la Villageoise Terrifiée ». Pour ça : (1) lemmatisation FR par règles suffixes, (2) fuzzy Levenshtein ≥ 0.75 en 3e étape, (3) champ `aliases: list[str]` sur `NPC` rempli par le world generator avec les archétypes, (4) **fallback LLM 4B** quand le matching Python échoue. Couvrir le tout d'une batterie de tests FR.

## Contexte technique

### Code à lire avant
- [`ai/entity_resolver.py`](../../ai/entity_resolver.py) **complet**, en particulier :
  - lignes **129-165** : `_normalize` (NFKD + lowercase) et `_match_candidates` (exact + token-subset).
  - lignes **173-223** : `_resolve_npc` (TALK).
  - lignes **361-398** : `_resolve_combatant` (ATTACK / CAST_SPELL) — ce résolveur sera étendu par le **Lot C**, mais le matching que tu améliores ici doit aussi le servir, donc factorise bien.
- [`world/npc.py`](../../world/npc.py) — schéma NPC actuel. Il n'y a pas de champ `aliases` à ajouter.
- [`ai/world_generator.py`](../../ai/world_generator.py) + [`ai/prompts/system_world_generator.txt`](../../ai/prompts/system_world_generator.txt) — comment les PNJ sont générés. Il faut leur faire produire des aliases.
- [`ai/interpreter.py`](../../ai/interpreter.py) — pour le fallback LLM, tu réutiliseras `Interpreter._client.chat_json` ou tu créeras une méthode dédiée. Voir comment les appels existants sont structurés.
- [`tests/ai/`](../../tests/ai/) — convention de tests existante.

### Données dont tu disposes
- À l'entrée du resolver : la query brute du joueur (`action.target_name`), la liste des candidats (`[npc.name for npc in present]`).
- Tu peux maintenant aussi accéder à `[alias for npc in present for alias in npc.aliases]` une fois le champ ajouté.

## Plan d'implémentation

1. **Étendre `world/npc.py`** :
   - Ajouter `aliases: list[str] = Field(default_factory=list)` au modèle `NPC`.
   - Ajouter une migration douce : tous les NPC en DB existants auront `aliases=[]` par défaut, le code doit gérer ça gracefully.
   - Mettre à jour le repository [`db/repositories/npc_repo.py`](../../db/repositories/) si la sérialisation JSON ne le gère pas automatiquement.

2. **Faire générer les aliases par le world generator** :
   - Modifier [`ai/prompts/system_world_generator.txt`](../../ai/prompts/system_world_generator.txt) pour demander explicitement, dans le schéma JSON de chaque NPC, un champ `aliases` : « Liste de 2-4 mots ou expressions génériques que les joueurs pourraient utiliser pour désigner ce personnage en français (genre, métier, archétype, surnoms). Exemple pour 'Jeanne, la Villageoise Terrifiée' : ["villageoise", "villageois", "villageur", "femme", "paysanne", "habitante"]. Inclure les variantes de genre masculines/féminines et singulier/pluriel. »
   - Mettre à jour [`ai/world_generator.py`](../../ai/world_generator.py) pour parser `aliases` et le passer au modèle NPC.
   - Si Pydantic strict mode rejette les champs inconnus venant du LLM, ajouter `aliases` dans le schéma de validation.

3. **Étendre le matching dans `ai/entity_resolver.py`** :
   - Garder `_normalize` tel quel mais le séparer en `_strip_diacritics` réutilisable.
   - **Nouvelle fonction `_lemmatize_fr(token: str) -> set[str]`** qui retourne toutes les variantes morphologiques d'un token français. Règles suffixes (table simple, pas spaCy) :
     - eur → {eur, euse, eurs, euses}
     - eux → {eux, euse, euses}
     - ois → {ois, oise, oises}
     - ais → {ais, aise, aises}
     - ien → {ien, ienne, iens, iennes}
     - er → {er, ere, ers, eres}
     - on → {on, onne, ons, onnes}
     - eau → {eau, eaux, elle, elles}
     - + suffixes pluriels génériques s/x → singulier
   - **Nouvelle fonction `_expand_query(query: str) -> set[str]`** qui prend la query normalisée et retourne l'union des lemmes pour chaque token.
   - **Nouvelle fonction `_match_candidates_v2(query: str, candidates: list[str], aliases_by_candidate: dict[str, list[str]] | None = None) -> list[str]`** :
     1. Construire `query_lemmas = _expand_query(query)`.
     2. Pour chaque candidat, construire l'union de ses tokens normalisés ET des tokens normalisés de ses aliases. Lemmatiser le tout.
     3. Match si `query_lemmas ∩ candidate_lemmas ≠ ∅` (au moins un lemme commun) — c'est plus permissif que l'ancien token-subset.
     4. Si plusieurs matches, garder ceux avec le plus grand recouvrement.
     5. Si toujours rien, fallback Levenshtein : ratio ≥ 0.75 entre la query normalisée et le nom du candidat OU un de ses aliases. Utiliser `difflib.SequenceMatcher` (stdlib, pas de nouvelle dépendance).
   - Remplacer les appels à `_match_candidates` par `_match_candidates_v2` dans `_resolve_npc`, `_resolve_exit`, `_resolve_object`, `_resolve_combatant` (note : `_resolve_combatant` sera enrichi par le Lot C, ne le casse pas).
   - Pour `_resolve_npc`, passer `aliases_by_candidate = {npc.name: npc.aliases for npc in present}`.

4. **Fallback LLM 4B** :
   - Créer une méthode `Interpreter.disambiguate_entity(raw_reference: str, candidates: list[tuple[str, list[str]]], language: str) -> str | None` qui appelle le 4B avec un prompt minimal :
     ```
     Le joueur a fait référence à "{raw_reference}". Voici les entités disponibles dans la scène, avec leurs alias :
     - "Jeanne, la Villageoise Terrifiée" (alias: villageoise, villageur, paysanne, femme)
     - "Père Thomas, le Moine Loyal" (alias: moine, prêtre, religieux, homme)
     
     À laquelle de ces entités fait référence "{raw_reference}" ? Retourne uniquement le nom EXACT d'une de ces entités, ou "null" si aucune ne correspond.
     
     Réponse JSON: {"match": "..." ou null}
     ```
   - Appelée depuis `_resolve_npc` (et plus tard `_resolve_combatant` par Lot C) seulement si `_match_candidates_v2` retourne `[]`. Si le LLM retourne un nom valide, c'est résolu. Sinon, vraiment unknown.
   - Ajouter un timeout court (5s) et un fallback gracieux en cas d'erreur LLM (juste retourner unknown comme avant).
   - **Note de circularité** : `entity_resolver.py` ne dépend pas actuellement de `interpreter.py`. Il y a deux options : (1) injecter l'interpreter optionnel dans la signature `EntityResolver.resolve(..., interpreter: Interpreter | None = None)` et le passer depuis `bot/action_pipeline.py`, (2) créer un module `ai/llm_disambiguator.py` qui prend un `client` brut. **Préfère l'option 1** pour garder la cohérence.

5. **Tests** dans [`tests/ai/test_entity_resolver.py`](../../tests/ai/) (créer si absent) :
   - Cas exact : « Jeanne, la Villageoise Terrifiée » → match.
   - Cas token : « Jeanne » → match.
   - Cas alias : « villageoise » → match (via aliases).
   - **Cas lemme genre** : « villageur » → match Jeanne (via lemma de villageois/villageoise).
   - **Cas lemme nombre** : « les villageurs » → match Jeanne.
   - Cas fuzzy : « jean villageoise » (typo) → match Jeanne.
   - Cas ambigu : 2 villageois dans la scène, query « villageois » → status="ambiguous".
   - Cas unknown vrai : « dragon » → status="unknown".
   - Cas fallback LLM : mocker l'interpreter pour qu'il retourne un nom valide quand le matching Python échoue.

## Critère de succès

- `uv run pytest tests/ai/test_entity_resolver.py -v` : tous verts, au moins 8 cas.
- `uv run pytest` global vert (pas de régression).
- `uv run ruff check . && uv run mypy .` verts.
- Test live (optionnel mais recommandé) : créer une mini campagne via tester bot, injecter manuellement Jeanne, taper « @bot parle au villageois » et vérifier que Talk est résolu vers Jeanne.

## Hors scope

- **Ne pas** déclencher de combat depuis ATTACK hors combat (Lot C — tu prépares juste l'infrastructure de matching).
- **Ne pas** modifier `_resolve_combatant` au-delà de remplacer `_match_candidates` par `_match_candidates_v2`. Lot C l'enrichira.
- **Ne pas** ajouter spaCy ni de dépendance NLP lourde — règles suffixes manuelles uniquement.
- **Ne pas** toucher au prompt interpreter (`system_interpreter.txt`).
- **Ne pas** changer le narrateur ni le scene embed.

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Statut** : DONE (non commité — laissé à l'utilisateur).
- **Périmètre livré** :
  - `NPC.aliases: list[str]` ajouté (`world/npc.py`), persisté en JSON (`db/models.py`, `db/mappers.py`) avec migration douce dans `db/database.py` (ALTER TABLE npcs ADD COLUMN aliases JSON DEFAULT '[]').
  - `ai/entity_resolver.py` : extraction `_strip_diacritics`, normalisation enrichie (ponctuation strippée), `_lemmatize_fr` (table suffixes eur/eux/ois/ais/ien/er/on/eau/teur + pluriel s/x), `_expand_query`, `_match_candidates_v2(query, candidates, aliases_by_candidate)` avec lemma overlap puis fuzzy `difflib.SequenceMatcher` ≥ 0.75. Stopwords FR filtrés.
  - `_resolve_npc` consomme désormais les aliases ; tous les autres résolveurs (`_resolve_exit`, `_resolve_search`, `_resolve_object`, `_resolve_combatant`, `_resolve_item`) passent par `_match_candidates_v2` via le shim `_match_candidates`. `_resolve_combatant` non touché au-delà du remplacement (Lot C).
  - `Interpreter.disambiguate_entity(raw_reference, candidates, language)` ajouté dans `ai/interpreter.py` — appelle qwen3.5:4b avec prompt minimal FR/EN, JSON `{"match": "..."|null}`, `temperature=0.1`, `num_predict=64`, gracieux en cas d'erreur (`return None`).
  - `EntityResolver.resolve(..., interpreter, language)` injection optionnelle (option 1 du brief) ; `bot/action_pipeline.py` passe `self.interpreter` et `self.language` au call site (ligne 202).
  - Prompt `ai/prompts/system_world_generator.txt` : nouveau bloc `npc_details: [{name, aliases}]` avec exemple Jeanne. (Note : la chaîne d'instanciation NPC depuis le world generator n'existe pas encore dans le code — `npcs_present` reste une liste de noms ; le champ `npc_details` est consigné côté prompt mais pas encore parsé/persisté. Le couplage final aliases ↔ NPC live attendra que cette pipeline soit branchée — voir Lot A/D si pertinent.)
  - Tests : `TestFrenchLemmatization` ajouté dans `tests/bot/test_entity_resolver.py` (10 cas : exact, token, alias, lemme genre « villageur »→Jeanne, lemme nombre « les villageurs », fuzzy « jean villageoise », ambigu 2 villageois, unknown « dragon », fallback LLM mocké succès, fallback LLM mocké null). 35/35 verts.
- **Vérifs** :
  - `uv run pytest tests/bot/test_entity_resolver.py -q` → 35 passed.
  - `uv run ruff check` sur les fichiers modifiés → clean.
  - `uv run mypy` sur les fichiers modifiés → clean (les 2 erreurs `character_create_view.py` sont pré-existantes, hors scope).
  - `uv run pytest` global : ~1149 passed. 4 échecs pré-existants (1 client/2 interpreter `LLMParseError` vs `JSONDecodeError`, 1 scénario timeout Ollama narrator) — **non causés par Lot B** (vérifié : ces tests dépendent d'autres modifs en cours dans le working tree, pas du resolver).
- **Observations pour les lots suivants** :
  - Lot C (Combat Initiation) : `_resolve_combatant` utilise déjà `_match_candidates_v2` mais sans aliases (les `Combatant` n'ont pas de champ aliases). Quand Lot C ajoutera `Combatant.aliases` ou un mapping, il suffira de lui passer un `aliases_by_combatant` en analogie avec `_resolve_npc`. Le matcher est prêt.
  - Lot A (Scene Awareness) : si l'embed scène expose les NPC visibles, il devrait afficher leur nom canonique tel quel — la lemmatisation côté joueur règle déjà le pont sémantique.
  - Branchement aliases ↔ instanciation NPC live : pas de chemin de code aujourd'hui. Le seul créateur d'NPC dans le repo est `db/mappers.py:npc_from_db` (et les fixtures de tests). Quand un futur `NpcGenerator` arrivera, il devra alimenter `aliases=` à partir du `npc_details` du world generator.
