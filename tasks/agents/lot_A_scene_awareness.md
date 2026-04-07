# Lot A — Scene Awareness

> Index : [`README.md`](README.md) · Statut : **TODO** · Pré-requis : aucun

## Pourquoi ce lot existe

Lors de la première campagne live (2026-04-07), les joueurs n'ont **jamais su quels PNJ étaient présents**. Les PNJ s'appelaient « Jeanne, la Villageoise Terrifiée » et « Père Thomas, le Moine Loyal », mais les joueurs ont tapé « le villageur », « les villageurs », « le fou ». Aucun embed n'a été posté à `/start_campaign` pour leur dire qui était là. Résultat : 0 interaction PNJ réussie sur 8 actions.

Pire : quand le resolver échoue, le narrateur reçoit un prompt « X n'existe pas, décris la réalisation » et **hallucine librement** — il a inventé qu'il n'y avait personne au village (alors qu'il y a 2 PNJ), puis a inventé un « vieil homme » au tour suivant. Il contredit le monde parce qu'il n'a pas la liste des vrais PNJ dans son contexte.

## Mission

Faire que (1) à `/start_campaign` et après chaque MOVE résolu, un **embed « scène »** soit posté dans le canal de campagne avec : nom et description du lieu, PNJ visibles avec descriptions courtes, sorties, objets visibles, et une suggestion d'action ; (2) le narrateur, quand il narre un refus d'entité introuvable, **reçoive la vraie liste** des PNJ/exits présents et ait l'instruction « ne jamais inventer, propose au joueur de reformuler ».

## Contexte technique

### Code à lire avant de commencer
- [`bot/campaign_launcher.py`](../../bot/campaign_launcher.py) — la méthode `LAUNCH starting` qui finalise la création de campagne. C'est là qu'il faut hooker l'envoi du premier embed scène.
- [`bot/action_pipeline.py`](../../bot/action_pipeline.py) lignes **393-410** (`_narrate_unknown`) — le prompt actuel envoyé au narrateur en cas d'entité inconnue. C'est très pauvre : « X tried to talk 'foo', but it does not exist in Le Village. Describe their realisation. » → narrateur libre d'halluciner.
- [`bot/embeds/`](../../bot/embeds/) — voir le style des autres embeds existants (character_embed, combat_embed, narrative_embed) pour cohérence visuelle.
- [`world/location.py`](../../world/location.py) — schéma `Location` (name, description, connections, npcs_present, items_available).
- [`world/npc.py`](../../world/npc.py) — schéma `NPC` (name, description, disposition, location_name).
- [`bot/game_session.py`](../../bot/game_session.py) — comment accéder à `session.current_location` et `session.npcs`.

### Données dispo dans la session
À l'instant T, on a accès à :
- `session.current_location: Location | None`
- `session.npcs: dict[str, NPC]` — filtrer par `npc.location_name == session.current_location.name`
- `session.story_arc.beats[session.story_arc.current_beat_index]` — beat actuel pour suggérer une action

## Plan d'implémentation

1. **Créer `bot/embeds/scene_embed.py`** avec une fonction `build_scene_embed(location: Location, npcs_present: list[NPC], language: str = "fr") -> discord.Embed` :
   - Titre : nom du lieu (avec emoji selon type — donjon ⚔️, village 🏘️, forêt 🌲, etc., heuristique simple sur mots-clés du nom).
   - Description : la description du lieu.
   - Field « 👥 Personnages présents » : pour chaque PNJ, `**{name}** — {description courte}` (ou « Aucun » s'il n'y en a pas). Limiter à 5 PNJ pour éviter le débordement.
   - Field « 🚪 Sorties » : liste comma-separated des `connections`, ou « Aucune » sinon.
   - Field « 🔍 Objets visibles » : `items_available` ou rien si vide.
   - Footer : suggestion d'action contextuelle (« Tape `@bot ` suivi de ce que tu veux faire »).
   - Tests unitaires dans [`tests/bot/`](../../tests/bot/) — fixture avec une location + 2 NPC + assertions sur les fields.

2. **Hook au lancement de campagne** dans [`bot/campaign_launcher.py`](../../bot/campaign_launcher.py) :
   - Repérer le bloc qui poste actuellement le message de bienvenue après `LAUNCH starting`.
   - Juste après ce post, appeler `build_scene_embed(...)` et l'envoyer dans le canal de campagne.
   - Logger `INFO bot.campaign_launcher SCENE posted location=<name>`.

3. **Hook après chaque MOVE résolu** :
   - Le Lot D va modifier le pipeline pour que MOVE change vraiment la location. Pour A, on prévoit le hook **dans le rendu de succès** côté `bot/cogs/action_handler.py` (méthode `_render_success` autour de la ligne 220) : si `result.interpreted_action.action_type == ActionType.MOVE`, poster un nouvel embed scène avec la nouvelle location.
   - **Important** : si Lot D n'est pas encore fait, ce hook ne se déclenchera pas (la location n'aura pas changé). C'est OK — Lot D activera le comportement.

4. **Durcir `_narrate_unknown`** dans [`bot/action_pipeline.py`](../../bot/action_pipeline.py) lignes 393-410 :
   - Construire la liste des vrais PNJ présents (depuis `self.npcs` filtré par `self.location.name`) et la liste des sorties (`self.location.connections`).
   - Nouveau prompt :
     ```
     {actor} a tenté de {verb} '{raw_value}', mais cette cible n'existe pas dans {location_name}.
     
     Personnages réellement présents : {liste_pnj_avec_descriptions} ou "aucun"
     Sorties réelles : {liste_sorties} ou "aucune"
     
     Décris en UN court paragraphe la réalisation du personnage et propose-lui de reformuler en mentionnant un de ces personnages/sorties s'il y en a. **N'invente AUCUN autre personnage, lieu ou objet.** Reste strictement dans le monde décrit.
     ```
   - Idem pour `_narrate_rule_failure` lignes 412-427 — y injecter le contexte scène quand pertinent.

5. **Tests** :
   - `tests/bot/test_scene_embed.py` — unit tests sur la construction de l'embed (présence des fields, troncature, fallbacks « aucun »).
   - `tests/test_campaign_launcher_observability.py` (existe déjà, voir git status) — ajouter une assertion qu'un embed scène est posté au launch.
   - Pas besoin de test e2e Discord live ici, ça sera fait dans la vérification globale.

## Critère de succès

- `uv run pytest tests/bot/test_scene_embed.py tests/test_campaign_launcher_observability.py` vert.
- `uv run ruff check . && uv run mypy .` verts sur les fichiers touchés.
- Lancer une campagne sur le tester bot (MCP `discord-test`) :
  ```
  mcp__discord-test__discord_send_command "/start_campaign theme:test_scene_awareness"
  ```
  Compléter l'onboarding, attendre le launch, et vérifier dans le canal qu'un embed apparaît avec les vrais noms de PNJ générés (et **pas** « les villageurs »).
- Tester un cas d'échec : `@bot je parle à un dragon` → vérifier que le narrateur dit quelque chose comme « Tu ne vois aucun dragon ici. Tu vois plutôt {vrais_PNJ}. » et **n'invente pas** un dragon.

## Hors scope

- **Ne pas** modifier la logique d'entity resolution (Lot B).
- **Ne pas** changer `current_location` en DB (Lot D).
- **Ne pas** toucher aux cogs combat ou aux validateurs.
- **Ne pas** modifier les system prompts du narrateur (`ai/prompts/system_narrator.txt`) — seul le prompt **utilisateur** dans `_narrate_unknown` change. Le system prompt narrateur est traité par le Lot F.
- **Ne pas** créer de message épinglé / sticky qui suit la scène en temps réel — c'est une feature future, pas pour ce lot.

## Notes de l'agent

> À remplir avant la fin de session : commit hash, blocages, observations utiles pour les lots suivants.

- **Commits livrés** :
  - `e9ff9a1` feat(embeds): add scene embed builder for location awareness
  - `3b9caab` feat(launcher): post scene embed after campaign launch
  - `d9c4cbc` feat(action-handler): re-display scene after MOVE actions
  - `4bd2cb7` refactor(narrator): ground refusal prompts in real scene context

- **Déviation actée vs. spec** : la spec présuppose `session.npcs: dict[str, NPC]` peuplé au launch — c'est faux. Vérifié : aucun pipeline ne crée d'objets `NPC` en production (seuls `db/mappers.py` et les fixtures de test instancient `NPC(...)`). Le world generator n'émet que `Location.npcs_present: list[str]`. Le scene embed et les nouveaux prompts narrateur travaillent donc directement avec ces strings. La signature `build_scene_embed(location, npcs_present=None, language="fr")` accepte un override `list[str] | None` pour que **Lot E** puisse y injecter des NPC objets enrichis sans changer les call-sites.

- **Hook post-MOVE** : posé dans `_run_pipeline` (action_handler.py) **après** `_render_success`, gated sur `result.interpreted_action.action_type == ActionType.MOVE`. Sous Lot A seul, `session.current_location` ne change pas encore — l'embed re-poste la même scène. **Lot D** activera le vrai changement de location (DB persistence) et le hook se déclenchera alors avec la nouvelle scène.

- **`_render_success` non touché** : la nouvelle responsabilité (re-poster la scène) est ajoutée au call site, pas dans le rendu, pour garder la méthode étroite.

- **Scope narrateur** : seul le **prompt utilisateur** dans `_narrate_unknown` et `_narrate_rule_failure` a été réécrit. Le system prompt narrateur (`ai/prompts/system_narrator.txt`) reste intouché — Lot F. Les nouveaux prompts injectent `npcs_present` (cap à 8 entrées), `connections`, et la clause **« N'invente AUCUN autre personnage, lieu ou objet »**.

- **Embed `bot/embeds/scene_embed.py`** : self-contained — dépend uniquement de `discord` et `world.location`. Heuristique d'emoji par mot-clé (donjon, village, forêt, temple, château, taverne, port, montagne, marais), normalisation accent-stripped pour matcher "Forêt" ↔ "foret". Tronque à 5 PNJ avec « … et N autre(s) ». Labels FR par défaut, EN dispo via `language="en"`.

- **Tests** :
  - `tests/bot/test_scene_embed.py` (nouveau) — 11 tests couvrant titre, description, fields, troncature, fallbacks, override `npcs_present`, language=en, heuristique emoji forêt.
  - `tests/test_campaign_launcher_observability.py` (étendu) — 2 nouveaux tests : scene embed posté au launch, scene embed skippé sans `current_location`.
  - `tests/bot/test_action_handler_cog.py` (étendu) — 2 nouveaux tests : MOVE post-scene, non-MOVE pas de scene.
  - `tests/bot/test_action_pipeline.py` (étendu) — 2 nouveaux tests `TestRefusalGrounding` : `_narrate_unknown` et `_narrate_rule_failure` injectent les vrais PNJ + sorties + clause anti-hallucination.

- **Vérification** :
  - `uv run pytest tests/bot/test_scene_embed.py tests/test_campaign_launcher_observability.py tests/bot/test_action_pipeline.py tests/bot/test_action_handler_cog.py` : 51/51 ✅
  - `uv run ruff check` sur les 8 fichiers touchés : ✅
  - `uv run mypy` sur les 4 sources touchées (avec `--follow-imports=silent` pour isoler des erreurs pré-existantes dans `bot/views/character_create_view.py`) : ✅
  - `uv run pytest` (suite complète) : 1173 passed, **3 échecs pré-existants non liés à Lot A** :
    - `tests/scenarios/test_free_text_exploration.py::test_scenario_unknown_entity_dragon` — bug de mock httpx : le test enregistre 2 réponses chat (interpreter + narrator) mais le resolver `_resolve_npc` fait un **3ème appel** via `interpreter.disambiguate_entity` (LLM fallback Lot B) qui n'a pas de mock. Mes changements ne touchent NI le resolver NI le nombre d'appels LLM — seul le contenu du prompt narrateur. Pré-existant ; à corriger côté test (ajouter une 3ème `httpx_mock.add_response`) ou côté Lot B.
    - `tests/bot/test_entity_resolver.py::TestResolveCombatTarget::test_attack_falls_back_to_present_npc` et `tests/scenarios/test_attack_bootstrap_combat.py::test_attack_bootstraps_combat_against_present_npc` — passent en isolation, échouent dans la suite complète : pollution de fixture httpx_mock entre tests scenario. Pré-existant.
  - **Smoke test live MCP discord-test** : tester bot offline au moment du run (`tester_connected=false`). Non-bloquant — les tests unitaires couvrent les cas d'usage. À rejouer manuellement quand le tester bot remontera.
