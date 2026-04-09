# Cycle de vie d'une campagne

De `/start_campaign` jusqu'à `/end_campaign`, avec onboarding multijoueur, sauvegarde, reprise et archivage.

## 1. `/start_campaign <theme> <@player1> <@player2>…`

Défini dans [bot/cogs/session.py](../../bot/cogs/session.py).

### Étapes

1. **Parse des arguments**
   - Thème libre (texte) — injecté tel quel dans le prompt d'arc generation.
   - Liste de `@mentions` → `list[int]` d'IDs Discord joueurs.
2. **Persistance initiale**
   - `Campaign(uuid4(), name=theme, player_ids=[…])` → `CampaignRepository.save()`.
   - `db_session.commit()` (le cog commit, pas le repo).
3. **Création du salon dédié** via `bot/utils/channel_manager.py`
   - `_slugify("campagne-<theme>")`, max 100 chars, accents strippés.
   - Catégorie par défaut `RealmAI Sessions` (récupérée ou créée).
   - Permissions : `@everyone` DENY, joueurs READ/SEND, bot READ/SEND/MANAGE.
   - Mapping `CampaignChannel(channel_id, campaign_id, guild_id)` persisté.
4. **Lancement de `CampaignLauncher`**
   - Stocké dans `bot.launchers[channel_id]` (orchestrateur temporaire, remplacé par `GameSession` au lancement effectif).
   - Démarre une coroutine background pour la génération procédurale.
5. **Post du message d'onboarding** dans le nouveau salon :
   - Bouton « Créer Personnage » (`StartOnboardingView`).
   - Pendant ce temps l'IA génère l'arc et la 1ʳᵉ location.

## 2. Onboarding — `CampaignLauncher`

Défini dans [bot/campaign_launcher.py](../../bot/campaign_launcher.py). Orchestre en parallèle : génération IA + création de personnages.

### 2.a Génération IA (background task)

Séquentielle :

1. **Arc generation** — `ai.arc_generator.ArcGenerator.generate(theme, player_count, language)`
   - Modèle : `qwen3.5:9b` **avec `think=True`**.
   - Retourne un `StoryArc` : `theme`, `premise`, `villain_name`, `villain_motivation`, `beats[]` (10-15 beats).
   - Chaque `StoryBeat` contient : `beat_number`, `title`, `description`, `location_hint`, `npc_names[]`, `encounter_type` (`social`/`combat`/`exploration`/`puzzle`/`boss`), `is_twist`.
   - Contrainte enforced par le prompt : dernier beat = `boss`.
2. **Location generation** — `ai.world_generator.WorldGenerator.generate(campaign_context, location_type, …)`
   - Modèle : `qwen3.5:9b` **avec `think=True`**.
   - Contexte construit à partir du beat 0 (`location_hint`).
   - Retourne un `Location` : `name`, `description`, `connections[]`, `npcs_present[]` (strings), `items_available[]`, `item_descriptions{}` (validation stricte : clés doivent matcher les items, le reste est silencieusement filtré).

Statut émis au salon : *« Univers en cours de génération… »* → *« Univers prêt ! »*.

### 2.b Création des personnages (par joueur)

Chaque joueur clique sur « Créer Personnage » :

1. **`CharacterCreateView`** (cascade de `ui.Select` + `ui.Modal`)
   - Sélection de race → classe → alignement.
   - `CharacterNameModal` → nom libre.
2. À la soumission :
   - `roll_ability_scores()` (4d6 drop lowest × 6).
   - `apply_racial_bonuses()`.
   - `create_character(…)` → `Character` Pydantic.
   - `create_inventory()` + `create_spellcaster_state(char_class, level)` (None si non-caster).
3. **`StarterGearView`** : 2-3 kits proposés pour la classe ([engine/starter_gear.py](../../engine/starter_gear.py)).
   - Au choix → `apply_starter_kit(kit, inventory)` (auto-équipe 1ʳᵉ arme + armure + shield).
4. Persistance via `PlayerCharacterRepository.save((user_id, character, inventory, spellcaster_state))`.
5. État `player_progress[user_id]` : `PENDING → CHARACTER_DONE → GEAR_DONE`.

### 2.c Launch check

`_maybe_launch()` est appelé à chaque transition. Le launch effectif requiert :
- Tous les joueurs en `GEAR_DONE`.
- Arc et location générés.

Quand les 2 conditions sont vraies → `_launch_campaign()` :

1. Construit `GameSession(campaign, characters, inventories, spellcasters, story_arc, current_location, language)`.
2. `create_ai_services(session)` — instancie `OllamaClient` + `Narrator`, `Interpreter`, `NPCAgent`, `NPCGenerator`, `StoryDirector`, `SemanticMemory`. Chaque instanciation est tolérante : si Ollama est down, le service correspondant est `None` mais la session démarre quand même.
3. Persiste `StoryArc` via `StoryArcRepository` et `Location` via `LocationRepository`.
4. `story_bible.write_header(...)` — écrit un en-tête Markdown statique dans `logs/campaigns/<id>.md` (arc complet, joueurs, location de départ). Voir [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md).
5. `hydrate_scene(session)` ([bot/scene_hydration.py](../../bot/scene_hydration.py)) — crée des `NPCRow` minimaux (HP=4, AC=10, stats=10) pour chaque nom dans `location.npcs_present` si absent en DB. Indispensable pour que le résolveur d'entités les trouve.
6. Poste :
   - Embed narratif d'introduction (Narrator narre l'arrivée du groupe).
   - `scene_embed` (nom + description + PNJs + sorties + items de la location).
7. Promotion finale : `bot.sessions[channel.id] = session` ; `bot.launchers.pop(channel_id)`.

À partir de ce moment, toute `@mention` dans ce salon est interceptée par `ActionHandlerCog`.

## 3. Phase de jeu

Pour chaque tour :

- Le joueur `@Realm <action libre>` (ou utilise une slash command structurée).
- `session.action_lock.acquire()` — un seul pipeline à la fois par campagne.
- Exécution des 6 phases — voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md).
- Persistance immédiate quand il y a mutation concrète : move (update location courante), kill (update NPC), pickup (update inventory).
- `session.advance_beat_if_ready()` — fuzzy match (`difflib ratio ≥ 0.7`) entre la location courante et le `location_hint` du prochain beat (Lot D).
- **Auto-checkpoint** : après chaque action résolue, `persist_session()` sauvegarde l'intégralité de la session (campagne + combat_state_json, personnages, PNJs, quêtes, arc). Un crash ne perd plus que l'action en cours de traitement.
- Exchange sauvé en Layer 2 (`ExchangeRepository`).
- Tous les 20 tours : `Summarizer.summarize()` (Layer 3) + `StoryDirector.check_coherence()` (si déclenché par le cog).

## 4. `/save`

Défini dans [bot/cogs/session.py](../../bot/cogs/session.py).

- Flush atomique de tous les modèles in-memory vers la DB.
- `CampaignRepository.update()`, `PlayerCharacterRepository.save()` pour chaque joueur, `NPCRepository.update()` pour chaque NPC, `LocationRepository.update()` pour la location courante, `StoryArcRepository.update()` pour l'arc (beat index avancé).
- Sauvegarde le `CombatState` via `campaigns.combat_state_json`. Depuis le fix B1, le même flush est aussi déclenché automatiquement après chaque action (auto-checkpoint).

## 5. `/resume <campaign_id>`

- Charge la `Campaign`, la `Location` courante, les `NPCs`, `Quests`, `StoryArc`, `PlayerCharacters`.
- Reconstruit un `GameSession` identique.
- Ré-instancie les services IA.
- Ne ré-hydrate PAS la sliding window en contexte : les prochains tours repartent sur des exchanges neufs, mais les anciens restent en DB pour le Layer 2 et le `context_assembler`.

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
- `bot.launchers: dict[channel_id, CampaignLauncher]`
- `session.action_lock`

Depuis le fix B1, `persist_session()` est appelé automatiquement après chaque action résolue (auto-checkpoint dans `ActionPipeline`). Un crash ne perd plus que l'action en cours de traitement. Le `combat_state` est inclus dans le checkpoint via `campaigns.combat_state_json`.
