# Cycle de vie d'une campagne

De `/start_campaign` jusqu'à `/end_campaign`, avec onboarding multijoueur, sauvegarde, reprise et archivage.

## 1. `/start_campaign <theme> [name] [players]`

Défini dans [bot/cogs/session.py](../../bot/cogs/session.py) (`SessionCog.start_campaign`).

### Étapes

1. **Parse des arguments**
   - `theme` — texte libre, injecté tel quel dans le prompt d'arc generation.
   - `name` — nom de campagne optionnel (défaut : le thème).
   - `players` — chaîne de `@mentions` optionnelle (`<@123>` / `<@!123>`). Elle ne **gate que la visibilité du salon** : les joueurs mentionnés devront quand même cliquer **Rejoindre** dans le lobby pour créer leur personnage.
2. **Persistance initiale**
   - `Campaign(uuid4(), name=campaign_name, player_names=[])` → `CampaignRepository.save()` + `flush()`. `player_names` est rempli au lancement, depuis le roster des joueurs READY.
3. **Création du salon dédié** via [bot/utils/channel_manager.py](../../bot/utils/channel_manager.py) (`create_session_channel`)
   - Slug du nom de campagne, max 100 chars, accents strippés.
   - Catégorie issue de `GuildConfig` (défaut `RealmAI Sessions`), récupérée ou créée.
   - Permissions : `@everyone` DENY, host + joueurs invités READ/SEND, bot READ/SEND/MANAGE.
   - Mapping `CampaignChannel(channel_id, campaign_id, guild_id)` persisté, puis `commit()`.
   - En cas d'échec : `rollback()` + suppression du salon orphelin.
4. **Ouverture du lobby**
   - `LobbyState(creator_id, language, campaign_name, theme)` stocké dans `bot.lobbies[channel_id]`.
   - Message de lobby posté dans le salon : `build_lobby_embed()` + [`LobbyView`](../../bot/views/lobby_view.py) (boutons **Rejoindre** / **Quitter** / **Démarrer l'aventure**). Les joueurs invités sont pingés via une `AllowedMentions` whitelistée.
5. **Pré-génération en tâche de fond** — `_pregenerate_campaign_world()` démarre immédiatement, en parallèle de la création des personnages.
6. **Watcher d'expiration** — `_expire_lobby_after()` purge un lobby abandonné après `_LOBBY_TTL_SECONDS` (2 h).

`/add_member <user>` permet au host d'ajouter quelqu'un après coup : en phase lobby le nouveau venu est slotté `JOINED` et peut cliquer Rejoindre ; après le lancement il devient simple spectateur (l'`ActionHandlerCog` ignore ses pings).

## 2. Onboarding — le lobby

Le `CampaignLauncher` a été supprimé. L'état d'onboarding vit désormais dans
[bot/lobby_state.py](../../bot/lobby_state.py) (`LobbyState`, `LobbyPlayer`,
`LobbyPlayerStatus`, `GenerationPhase`) — une structure de données passive —
et toute l'orchestration (callbacks des boutons, pré-génération, lancement)
est assurée par `SessionCog` dans [bot/cogs/session.py](../../bot/cogs/session.py).

`LobbyState` : `creator_id` (host), `players: dict[user_id, LobbyPlayer]`
(6 joueurs max, `MAX_PLAYERS_PER_LOBBY`), références au message et à la vue de
lobby pour re-render, plus le résultat de la pré-génération
(`pregen_phase`, `pregen_task`, `story_arc`, `current_location`, `pregen_error`).

Statuts joueur : `JOINED → CREATING → READY`. `LobbyPlayerStatus.CANCELLED`
existe et est rendu par `lobby_embed`, mais aucun chemin de code ne l'assigne
aujourd'hui — un abandon de création laisse le joueur en `CREATING` jusqu'à
ce qu'il re-clique **Rejoindre** (ou **Quitter**, qui le retire du roster).

### 2.a Génération IA (background task)

`_pregenerate_campaign_world()` — lancée dès `/start_campaign`, elle avance
`lobby.pregen_phase` : `PENDING → ARC → LOCATION → READY` (ou `FAILED`, avec
`pregen_error` remonté au host au moment du lancement). Chaque transition
rafraîchit le statut affiché dans l'embed de lobby. Séquentielle :

1. **Arc generation** — `ai.arc_generator.ArcGenerator.generate(theme, player_count, language, recipe)`
   - Recette d'archétype tirée en amont par `engine.arc_recipes.generate_recipe(theme=…)` (variété / anti-répétition).
   - `player_count` vaut 1 par défaut — le lobby n'est pas encore rempli et le générateur ne s'en sert que comme indice de difficulté dans le prompt.
   - Modèle : `qwen3.5:9b` **avec `think=True`**.
   - Retourne un `StoryArc` : `theme`, `premise`, `villain_name`, `villain_motivation`, `beats[]` (10-15 beats).
   - Chaque `StoryBeat` contient : `beat_number`, `title`, `description`, `location_hint`, `npc_names[]`, `encounter_type` (`social`/`combat`/`exploration`/`puzzle`/`boss`), `is_twist`.
   - Contrainte enforced par le prompt : dernier beat = `boss`.
2. **Location generation** — `ai.world_generator.WorldGenerator.generate(campaign_context, location_type="starting_area", language, location_hints)`
   - Modèle : `qwen3.5:9b` **avec `think=True`**.
   - Contexte construit à partir de l'arc (nom de campagne, villain, description du beat 0) ; `location_hints` agrège les `location_hint` de tous les beats.
   - Retourne un `Location` : `name`, `description`, `connections[]`, `npcs_present[]` (strings), `items_available[]`, `item_descriptions{}` (validation stricte : clés doivent matcher les items, le reste est silencieusement filtré).

En cas d'indisponibilité d'Ollama, la phase passe `FAILED` et le message d'erreur est stocké dans `lobby.pregen_error` — le lancement est alors refusé avec un message explicite dans le salon.

### 2.b Création des personnages (par joueur)

Chaque joueur clique sur **Rejoindre** (`LobbyView.join`) → `lobby.add_player(user_id)` (statut `JOINED`, refusé si le lobby est plein), puis le statut passe `CREATING` et le flow de setup s'ouvre.

1. **[`CharacterSetupFlow`](../../bot/views/character_setup_flow.py)** — vue éphémère unique éditée en place, 6 étapes (`SetupStep`) :
   `IDENTITY` (via `IdentityModal` : nom + concept) → `RACE_CLASS` → `STATS` → `SKILLS` → `KIT_MOTIV` → `REVIEW`.
   - `STATS` : bouton « Optimisé pour \<Classe\> » (`CLASS_STAT_PRESETS`) ou « Aléatoire » (`roll_4d6_drop_lowest` + `auto_assign_random`).
   - `SKILLS` : choix parmi `CLASS_SKILL_CHOICES`.
   - `KIT_MOTIV` : kit de départ ([engine/starter_gear.py](../../engine/starter_gear.py)) + motivation narrative.
   - `REVIEW` : **Confirmer** / **Recommencer** (retour à `RACE_CLASS`, nom et concept conservés) / **Annuler** (abandonne le flow sans appeler `on_complete` — le joueur peut re-cliquer **Rejoindre**).
2. À la confirmation, le callback `on_setup_complete` du cog :
   - `create_inventory()` puis `apply_starter_kit(kit, inventory)` pour le kit choisi.
   - `create_spellcaster_state(char_class, level=1)` (None si non-caster).
   - `PlayerCharacterRepository.save(user_id, campaign_id, character, inventory, spellcaster)` + `commit()` — le personnage survit à un redémarrage du bot dès la fin de la création.
   - Renseigne `LobbyPlayer` (character / inventory / spellcaster / kit_name / motivation_key) et passe le statut à `READY`.
3. L'embed public du lobby est re-render à chaque transition (sous `asyncio.Lock`, plusieurs joueurs pouvant finir au même instant).

### 2.c Lancement (host-only)

Il n'y a **pas de launch automatique** : le host clique **Démarrer l'aventure** (`LobbyView.launch`).

- Réservé au `creator_id` — tout autre clic reçoit un refus éphémère (vérifié dans la vue *et* dans le callback du cog).
- Requiert `lobby.has_any_ready()` : au moins un joueur avec un personnage complet. Les joueurs non-READY sont simplement exclus de la session.
- Garde de ré-entrance (`launch_in_flight`) posée de façon synchrone : un double-clic ne déroule pas deux fois la séquence. Le flag est remis à `False` uniquement si le lancement échoue, pour que le host puisse réessayer.
- Les trois boutons du lobby sont désactivés pendant le lancement.
- Si la pré-génération tourne encore, un message de statut public est posté (« Préparation de l'aventure en cours… », avec la phase) puis le lancement `await` la tâche et reprend automatiquement.

### 2.d Launch immersion

`_launch_campaign_from_lobby()` exécute dans l'ordre :

1. Construit `GameSession(campaign, creator_id, characters, inventories, spellcasters, story_arc, current_location, character_kits, character_motivations, language)` à partir des seuls joueurs READY (`campaign.player_names` est renseigné ici).
2. `create_ai_services(session)` — instancie `OllamaClient` + `Narrator`, `Interpreter`, `NPCAgent`, `NPCGenerator`, `StoryDirector`, `SemanticMemory`. Chaque instanciation est tolérante.
3. Persiste `StoryArc` via `StoryArcRepository` et `Location` via `LocationRepository`, crée les stubs de sorties (`bot.world_navigation.create_exit_stubs`) puis `CampaignRepository.update()`.
4. `story_bible.write_header(...)` — écrit un en-tête Markdown statique. Voir [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md).
5. Promotion : `bot.sessions[channel.id] = session` ; `bot.lobbies.pop(channel_id)` ; annulation du watcher TTL ; `lobby_view.stop()`.
6. **Purge du channel** — supprime les messages d'onboarding (`channel.purge(limit=200)`). Non-bloquant.
7. AI warnings re-postés après la purge (survivent au nettoyage).
8. **Countdown immersif** — `build_countdown_embed()` affiche `3…`, `2…`, `1…` (pause de 1,5 s), puis supprime le message. Non-bloquant.
9. **Cartes de groupe** — `build_party_card_embed()` pour chaque personnage, puis un séparateur.
10. **Opening crawl embed** — `build_opening_crawl_embed()` poste un embed riche : titre avec 📜, premise de l'arc, lieu de départ, premier chapitre.
11. `hydrate_scene(session)` ([bot/scene_hydration.py](../../bot/scene_hydration.py)) — crée des `NPCRow` minimaux pour chaque nom dans `location.npcs_present`.
12. `scene_embed` (nom + description + PNJs + sorties + items de la location).
13. **Arc Tracker** épinglé dans le salon (`ArcTrackerManager.ensure_pinned`), best-effort.

À partir de ce moment, toute `@mention` dans ce salon est interceptée par `ActionHandlerCog`.

## 3. Phase de jeu

Pour chaque tour :

- Le joueur `@Realm <action libre>` (ou utilise une slash command structurée).
- `session.action_lock.acquire()` — un seul pipeline à la fois par campagne.
- Exécution des 6 phases — voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md).
- Persistance immédiate quand il y a mutation concrète : move (update location courante), kill (update NPC), pickup (update inventory).
- Progression de beat : `BeatProgressionEngine.evaluate()` sur les `BeatObjective` structurés du beat courant, avec arbitrage `BeatJudge` (confidence ≥ 0.7) quand l'engine hésite — `advance_beat_if_ready()` (Lot D) n'existe plus.
- **Auto-checkpoint** : après chaque action résolue, `persist_session()` sauvegarde l'intégralité de la session (campagne + combat_state_json, personnages, PNJs, quêtes, arc). Un crash ne perd plus que l'action en cours de traitement.
- Exchange sauvé en Layer 2 (`ExchangeRepository`).
- `Summarizer.summarize()` (Layer 3) tourne en tâche de fond dès que ~20 exchanges non-résumés ont quitté la fenêtre glissante ; `StoryDirector.check_coherence()` est auto-planifié par l'orchestrateur (toutes les 6 interactions + fin de combat + drift + force), avec un chemin legacy à 20 tours dans `story_bible_logger`.

## 4. `/save`

Défini dans [bot/cogs/session.py](../../bot/cogs/session.py).

- Flush atomique de tous les modèles in-memory vers la DB.
- `CampaignRepository.update()`, `PlayerCharacterRepository.save()` pour chaque joueur, `NPCRepository.update()` pour chaque NPC, `LocationRepository.update()` pour la location courante, `StoryArcRepository.update()` pour l'arc (beat index avancé).
- Sauvegarde le `CombatState` via `campaigns.combat_state_json`. Depuis le fix B1, le même flush est aussi déclenché automatiquement après chaque action (auto-checkpoint).

## 5. `/resume <campaign_id>`

- Charge la `Campaign`, la `Location` courante, les `NPCs`, `Quests`, `StoryArc`, `PlayerCharacters`, **et le `CombatState` actif** (`campaigns.combat_state_json` — zones des combattants réalignées via `_sanitize_combat_zones` ; un blob illisible est droppé avec warning plutôt que de bloquer la reprise).
- Reconstruit un `GameSession` identique.
- Ré-instancie les services IA.
- La sliding window n'est pas rechargée en RAM au `/resume` (`session.memory_context` repart à `None`), mais dès le tour suivant `assemble_memory_prefix` relit les exchanges persistés depuis la DB — la continuité narrative est conservée (chantier G).

## 6. `/end_campaign`

- `archive_channel()` : déplace le salon dans la catégorie `RealmAI Archives`, retire les droits d'écriture pour les joueurs.
- Nettoyage ChromaDB : supprime la collection `campaign_<id>` via `SemanticMemory.delete_campaign()` (fix L5).
- Ne supprime rien en DB — tout reste accessible pour `/resume` ultérieur ou analyse post-mortem.
- `bot.sessions.pop(channel.id)`.

## 7. Reset dev

[scripts/reset_dev_data.py](../../scripts/reset_dev_data.py) supprime toutes les campagnes et leurs descendants (cascade SQL) tout en préservant les `guild_configs`. Utile pour repartir d'un state propre. `uv run python scripts/reset_dev_data.py`.

## État critique en mémoire uniquement

Les objets suivants **ne sont pas répliqués en DB** pendant la phase de jeu :

- `bot.sessions: dict[channel_id, GameSession]`
- `bot.lobbies: dict[channel_id, LobbyState]` (les personnages, eux, sont persistés dès la fin de leur création)
- `session.action_lock`

Depuis le fix B1, `persist_session()` est appelé automatiquement après chaque action résolue (auto-checkpoint dans `ActionPipeline`). Un crash ne perd plus que l'action en cours de traitement. Le `combat_state` est inclus dans le checkpoint via `campaigns.combat_state_json`.
