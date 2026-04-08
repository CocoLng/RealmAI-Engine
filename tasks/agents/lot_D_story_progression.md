# Lot D — Story Progression (vrai MOVE + advance_beat)

> Index : [`README.md`](README.md) · Statut : **TODO** · Pré-requis : Lot A (idéal pour l'embed scène à poster), Lot B (idéal pour le fuzzy match du beat→location)

## Pourquoi ce lot existe

Lors de la première campagne live (2026-04-07), la story arc générée comportait 13 beats. Au terme des 22 minutes de session, **`current_beat_index` valait toujours 0**. La fonction `advance_beat()` existe dans [`world/story_arc.py`](../../world/story_arc.py) lignes 36-43 mais **n'est jamais appelée nulle part**. Le repository [`db/repositories/story_arc_repo.py`](../../db/repositories/story_arc_repo.py) a une méthode `update()` ligne 32 qui n'est **jamais invoquée non plus** (`grep` confirme).

Pire, le MOVE du turn 5 — « on rentre dans le donjon » — a été interprété correctement (`Move target=Donjon de Malphas conf=0.95`), narré joliment, mais **n'a rien changé en DB**. La méthode `_resolve_mechanics` ligne 364 retourne juste « JeanTest moves toward Donjon de Malphas. » sans toucher à `session.current_location` ni à `Campaign.current_location`. La seule code path qui change vraiment de lieu est la commande `/move` legacy ([`bot/cogs/exploration.py:255-370`](../../bot/cogs/exploration.py)), et même elle ne persiste pas en DB et n'avance aucun beat.

## Mission

(1) Le MOVE en texte libre doit **vraiment** changer `session.current_location`, persister en DB, générer la nouvelle location si absente, et déclencher l'embed scène (Lot A). (2) Hooker `advance_beat_if_ready()` après chaque action résolue : si `session.current_location.name` matche (fuzzy) le `location_hint` du prochain beat, advance, persiste, poste un embed « ✨ Nouveau chapitre ». (3) Marquer `/move` deprecated.

## Contexte technique

### Code à lire avant
- [`world/story_arc.py`](../../world/story_arc.py) — modèle `StoryArc`, `StoryBeat`, fonction `advance_beat` ligne 36.
- [`db/repositories/story_arc_repo.py`](../../db/repositories/story_arc_repo.py) — méthodes `save()`, `get_by_campaign()`, `update()`.
- [`bot/game_session.py`](../../bot/game_session.py) — comment `session.story_arc` et `session.current_location` sont stockés. Comment `session.npcs` est rechargé après changement de location.
- [`bot/action_pipeline.py`](../../bot/action_pipeline.py) ligne **340** (`_resolve_mechanics`) et ligne **364** (cas MOVE) — où mettre la mutation. Voir aussi ligne **272** où le pipeline retourne `DONE` — c'est là qu'on hooke `advance_beat_if_ready`.
- [`bot/cogs/exploration.py`](../../bot/cogs/exploration.py) lignes **255-370** — la commande `/move` actuelle, qui sait charger une location depuis la DB ou en générer une nouvelle via `WorldGenerator`. **Réutiliser cette logique**, ne pas la dupliquer. Idéalement, extraire un helper `async def change_location(session, destination_name) -> Location` quelque part dans `bot/` (peut-être `bot/world_navigation.py` neuf, ou ajouter au game_session) et l'appeler depuis les deux.
- [`bot/campaign_launcher.py`](../../bot/campaign_launcher.py) — pour comprendre comment les locations/NPCs sont chargés au launch.
- [`db/repositories/campaign_repo.py`](../../db/repositories/) — pour persister `Campaign.current_location`.
- [`bot/embeds/scene_embed.py`](../../bot/embeds/) — créé par le Lot A. À réutiliser pour l'embed après MOVE.
- [`logs/campaigns/276fb1eb-...md`](../../logs/campaigns/276fb1eb-e000-4c77-8e42-21b10cd84595.md) — voir le format des beats : champ `location_hint` est un texte libre du genre « Donjon de Malphas », « Portail du donjon ». Le matching avec une `Location.name` réelle ne sera **jamais exact** — il faut du fuzzy.

## Plan d'implémentation

### Étape 1 — Helper de changement de location

Créer (ou extraire depuis `exploration.py`) `bot/world_navigation.py` :

```python
async def change_location(
    session: GameSession,
    destination_name: str,
    *,
    world_generator: WorldGenerator,
    location_repo: LocationRepository,
    campaign_repo: CampaignRepository,
    npc_repo: NPCRepository,
) -> Location:
    """Move the session to `destination_name`. Loads from DB or generates."""
```

Logique :
1. Chercher la location en DB par nom (avec fuzzy si Lot B fait, sinon exact).
2. Si absente, appeler `world_generator.generate(context=f"Moving from {current.name} to {destination_name}", current_location=current)` et la sauver via `location_repo.save()`.
3. Mettre à jour `session.current_location = new_location`.
4. Mettre à jour `session.campaign.current_location = new_location.name`.
5. Persister via `campaign_repo.update(session.campaign)`.
6. Recharger `session.npcs` pour inclure les NPC du nouveau lieu (`npc_repo.list_by_location(new_location.name)`).
7. Retourner la nouvelle location.

Refactor `/move` dans [`bot/cogs/exploration.py`](../../bot/cogs/exploration.py) pour appeler ce helper, et ajouter un decorator/log « DEPRECATED » + une notice dans la réponse.

### Étape 2 — MOVE en texte libre fait le vrai changement

Dans [`bot/action_pipeline.py:340`](../../bot/action_pipeline.py) `_resolve_mechanics`, le cas `MOVE` doit appeler `change_location`. Problème : `_resolve_mechanics` est synchrone et n'a pas accès aux repos. Solution :
- Faire de `_resolve_mechanics` une `async def` (refactor mineur, voir si d'autres branches en bénéficient).
- Injecter les repos dans `ActionPipeline` (`@dataclass` actuel — ajouter des champs optionnels `location_repo`, `campaign_repo`, `npc_repo`, `world_generator`).
- Le cog [`bot/cogs/action_handler.py`](../../bot/cogs/action_handler.py) construit le pipeline ; il devra passer ces dépendances depuis le `bot.client` ou la `session`.
- Dans le cas MOVE, après résolution réussie, appeler `await change_location(session, action.target_name, ...)`. En cas d'erreur (génération qui foire), retourner un mechanics_text de refus et laisser le narrateur expliquer.

Important : ce changement de location doit déclencher l'envoi de l'embed scène **après** le narrative embed habituel. Le hook côté `_render_success` du Lot A s'en charge déjà si `result.interpreted_action.action_type == MOVE`.

### Étape 3 — `advance_beat_if_ready` + persistance

Dans [`bot/game_session.py`](../../bot/game_session.py), ajouter une méthode :

```python
def advance_beat_if_ready(self) -> StoryBeat | None:
    """Check if the current location matches the next beat's hint and advance.
    
    Returns the new beat if advanced, None otherwise.
    """
```

Logique :
1. Si `current_beat_index + 1 >= len(beats)` : déjà au dernier beat, retourner None.
2. Récupérer `next_beat = beats[current_beat_index + 1]`.
3. Si `current_location.name` matche fuzzy `next_beat.location_hint` (utiliser le `_match_candidates_v2` du Lot B, ou `difflib.SequenceMatcher` ratio ≥ 0.7 en fallback si Lot B pas fait) → advance.
4. Pour advance : `self.story_arc = advance_beat(self.story_arc)` et retourner `self.story_arc.beats[self.story_arc.current_beat_index]`.
5. Sinon retourner None.

Hooker l'appel à la fin de `_continue_from_resolution` dans [`bot/action_pipeline.py:272`](../../bot/action_pipeline.py) (juste avant `return ActionPipelineResult(...)`) :
- Appeler `new_beat = session.advance_beat_if_ready()`.
- Si `new_beat is not None` : persister via `story_arc_repo.update(session.story_arc)`, et déclencher un callback `on_beat_advanced(new_beat)` qui poste un embed dans le canal (via `action_handler` ou via un nouveau callback dans le pipeline).
- Logger `INFO bot.game_session BEAT advanced campaign={id} from={old_idx} to={new_idx} beat='{title}'`.

### Étape 4 — Embed « nouveau chapitre »

Ajouter à `bot/embeds/scene_embed.py` (créé par Lot A) ou à un nouveau `bot/embeds/beat_embed.py` une fonction `build_beat_advance_embed(beat: StoryBeat) -> discord.Embed` :
- Titre : « ✨ Nouveau chapitre — Beat {n}/{total} »
- Description : `**{beat.title}** — {beat.description}`
- Field « Type » : `beat.encounter_type`
- Field « PNJ attendus » : `beat.npc_names` joinés
- Field « Twist » : seulement si `beat.is_twist`

Hook l'envoi depuis le callback `on_beat_advanced` côté `bot/cogs/action_handler.py`.

### Étape 5 — Marquer `/move` deprecated

Dans [`bot/cogs/exploration.py`](../../bot/cogs/exploration.py) `/move` :
- Ajouter au début de la commande un `await interaction.followup.send(":warning: `/move` est déprécié. Tape simplement `@bot {action}` pour te déplacer.", ephemeral=True)`.
- Garder le comportement (qui appelle maintenant `change_location` mutualisé).
- Logger `WARNING bot.cogs.exploration MOVE deprecated_command_used user={...}`.

### Étape 6 — Tests

- `tests/test_world_navigation.py` (nouveau) — unit tests sur `change_location` avec mocks (DB existante, DB vide → génération, persistance vérifiée).
- `tests/scenarios/test_beat_advance.py` (nouveau) — scénario e2e :
  1. Setup : campagne avec 3 beats, `current_beat_index=0`, `current_location=lieu_A`.
  2. Action : `change_location(session, "lieu_B")` où le beat 1 a `location_hint="lieu_B"`.
  3. Action : appeler `session.advance_beat_if_ready()`.
  4. Assert : retourne le beat 1, `session.story_arc.current_beat_index == 1`, le repo a été appelé.
- Étendre [`tests/scenarios/test_free_text_exploration.py`](../../tests/scenarios/test_free_text_exploration.py) (existe déjà) avec un cas « MOVE → location change persistée + embed scène posté ».

## Critère de succès

- `uv run pytest tests/test_world_navigation.py tests/scenarios/test_beat_advance.py tests/scenarios/test_free_text_exploration.py -v` vert.
- `uv run pytest` global vert.
- `uv run ruff check . && uv run mypy .` verts.
- Test live tester bot :
  1. Lancer une campagne, attendre l'embed scène (Lot A).
  2. `@bot on entre dans le donjon` → vérifier dans le canal : narrative embed + nouvel embed scène + (si beat 1 a un location_hint qui match) embed « Nouveau chapitre ».
  3. Vérifier en DB que `Campaign.current_location` a changé et que `StoryArc.current_beat_index` a été incrémenté.

## Hors scope

- **Ne pas** ajouter de critères d'avancement narratif autres que location-matching (« beat avancé après N tours » ou « beat avancé après tel PNJ tué » : à voir plus tard).
- **Ne pas** régénérer toute la story arc — juste avancer l'index.
- **Ne pas** retirer `/move` complètement, juste deprecated.
- **Ne pas** toucher à la génération des beats (`ai/arc_generator.py`).
- **Ne pas** modifier le combat ni le resolver (Lots B/C/E).

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Helper partagé** : `bot/world_navigation.py` (`change_location` + `LocationChangeError`). Utilisé par le pipeline (texte libre MOVE) et `/move` deprecated. Persiste location générée, met à jour `Campaign.current_location`, recharge `session.npcs` via `NPCRepository.list_by_location`.
- **Pipeline** : `_resolve_mechanics` rendu `async` (seul appelant : `_continue_from_resolution` ligne 272). Branche MOVE appelle `change_location` quand `session` + `db_factory` sont présents et synchronise `self.location`/`self.npcs` après. `ActionPipelineResult` gagne un champ `new_beat: StoryBeat | None`. Hook beat advance ajouté juste avant `return ActionPipelineResult(...)` : appelle `session.advance_beat_if_ready()`, persiste via `_persist_story_arc` (helper module-level → `StoryArcRepository.update` + commit). Garde `isinstance(candidate, StoryBeat)` pour ne pas casser les tests qui mockent `session`.
- **GameSession** : nouvelle méthode `advance_beat_if_ready()`. Fuzzy = `difflib.SequenceMatcher` après normalisation (lowercase + strip accents) — seuil 0.7 (un peu plus permissif que l'entity-resolver à 0.75 car `location_hint` est freeform LLM). Helper `_normalize_location` privé au module.
- **Embed** : `bot/embeds/beat_embed.py` → `build_beat_advance_embed(beat, total_beats, language)`. Couleur violet (`0x9B59B6`), title `"✨ Nouveau chapitre — Beat n/total"`, fields Type / PNJ attendus / Twist (conditionnel). Hook côté `action_handler.py` : envoyé après le scene embed quand `result.new_beat is not None`.
- **`/move` deprecated** : warning ephemeral en tête + log `WARNING bot.cogs.exploration MOVE deprecated_command_used`. Réutilise `change_location`, fallback `Location` minimale en cas d'`LocationChangeError`. Imports nettoyés (`LocationRepository` retiré).
- **Tests ajoutés** :
  - `tests/test_world_navigation.py` : 3 cas (DB hit, génération via WorldGenerator mocké, erreur sans Ollama). Verts.
  - `tests/scenarios/test_beat_advance.py` : 6 cas (match, mismatch, accent-insensible, dernier beat, pas d'arc, pas de location). Verts.
- **Tests adaptés** : `tests/test_cog_exploration.py::TestMove` patchait `bot.cogs.exploration.LocationRepository` → maintenant `bot.world_navigation.LocationRepository`. Assertion `followup.send.assert_called_once` → `>= 1` pour absorber le message de deprecation.
- **Vérifs** : `uv run pytest` → 1191 passed, 1 failed/1 error sur `test_scenario_unknown_entity_dragon` (pré-existant, Ollama timeout, documenté dans Lot A). `uv run ruff check` sur les fichiers touchés → clean. `uv run mypy` sur les fichiers touchés → clean (les 2 erreurs `var-annotated` sont dans `bot/views/character_create_view.py`, hors scope).
- **Pour Lot E** : la branche MOVE du pipeline récupère désormais correctement `session.npcs` après changement de lieu — quand Lot E filtrera par `location_name` côté repo, `session.npcs` sera déjà correct. Le hook `advance_beat_if_ready` s'exécute pour **toutes** les actions résolues, pas seulement MOVE — utile si Lot E veut déclencher un beat sur un kill (à voir, hors scope ici).
- **Smoke MCP discord-test** : non exécuté faute de bot live dans cette session.

