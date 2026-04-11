# Discord Bot — `bot/`

Couche de présentation et orchestration. Discord est **l'unique UI utilisateur**. Le bot ne contient **aucune règle de jeu** ; il orchestre `engine/`, `ai/`, `memory/`, `db/`.

## Démarrage

```
main.py  →  bot.bot.run_bot()
              │
              ├─ setup_logging()         (bot/logging_config.py)
              ├─ load_dotenv()
              └─ RealmBot(intents).run(token)
                     │
                     ├─ setup_hook()
                     │    ├─ load 7 cogs : rolls, session, character, inventory, combat, exploration, action_handler
                     │    ├─ load test_bridge cog si TEST_MODE=true
                     │    └─ tree.sync()
                     │
                     ├─ on_ready()  — log guilds + synced commands
                     ├─ on_app_command_completion()  — structured log
                     └─ on_app_command_error()
```

`RealmBot.__init__` crée :
- intents (default + message_content + members)
- engine SQLAlchemy + session factory (`get_engine`, `get_session_factory`)
- `sessions: dict[channel_id, GameSession]` (in-memory)
- `launchers: dict[channel_id, CampaignLauncher]` (in-memory, temporaire pour onboarding)

## Cogs

Tous dans [bot/cogs/](../../bot/cogs/).

### `session.py` — `SessionCog`

| Commande | Description |
|---|---|
| `/start_campaign <theme> <@players>` | Crée campagne, channel dédié, lance `CampaignLauncher` |
| `/resume <campaign_id>` | Recharge campagne depuis DB, re-instancie services IA |
| `/save` | Flush session in-memory → DB (atomique) |
| `/end_campaign` | Archive le channel, pop la session |
| `/settings` | Configure `GuildConfig` (catégorie, langue) |

Voir [CAMPAIGN_LIFECYCLE.md](CAMPAIGN_LIFECYCLE.md) pour le flux détaillé.

### `character.py` — `CharacterCog`

| Commande | Description |
|---|---|
| `/create_character` | Lance `CharacterCreateView` (race → class → align → name) |
| `/character [public]` | Affiche la fiche du personnage (ephemeral par défaut) |
| `/levelup` | Avance d'un niveau (check XP threshold) |

### `inventory.py` — `InventoryCog`

| Commande | Description |
|---|---|
| `/inventory [public]` | Embed avec équipement + backpack + gold |
| `/equip <item> <slot>` | Déplace vers slot, recomputent AC |
| `/unequip <slot>` | Déplace vers backpack |
| `/use <item>` | Consomme consommable |

### `combat.py` — `CombatCog`

Pas de slash commands directes pour le joueur (combat piloté par buttons via `CombatView`). Fournit des helpers utilisés par `ActionHandlerCog` :
- `start_combat_encounter(session, enemies)` — initie un fight
- `_end_combat(session)` — award XP, check level-up

### `exploration.py` — `ExplorationCog`

Slash commands `/look`, `/search`, `/talk`, `/move` — **deprecated** en faveur des `@mentions` libres traitées par `ActionHandlerCog`. Maintenues pour rétrocompat et tests.

### `rolls.py` — `RollsCog`

`/roll <expression>` — wrapper autour de `engine.dice.roll`.

### `action_handler.py` — `ActionHandlerCog` ⭐

**Cœur de l'UX**. Pas de slash command — écoute `on_message` et intercepte les `@Realm <action>` dans les salons de campagne.

1. Filtre : message court (<4 chars), interjections OOC, non-joueurs.
2. `async with session.action_lock:` — un seul pipeline à la fois.
3. Poste un embed de progression.
4. Instancie `ActionPipeline(session, interpreter, narrator, npc_agent, entity_resolver, …)`.
5. Exécute `pipeline.run(player_text, actor_name, progress_callback)`.
6. Dispatch selon retour : `ActionPipelineResult` → embed narratif ; `AmbiguityResult` → `ClarificationView` ; `UnknownEntityResult` → refus in-character.
   - `_render_success` route vers `build_state_embed` (bleu, 0x4A90D9) pour les actions `QUESTION`, affichant items/PNJs/sorties/beat inline.
7. Log dans `story_bible`, persiste `exchanges`, check `advance_beat_if_ready()`, déclenche Story Director tous les 20 tours.

### `test_bridge.py` — `TestBridgeCog`

Chargé uniquement si `TEST_MODE=true`. Écoute `!test <command>` depuis le `TesterBot` (voir `mcp_discord/`). Simule des messages joueurs, des créations de personnages, des actions de combat pour les scénarios pytest et tests live via MCP.

## Views (`bot/views/`)

Toutes héritent de `LoggedView(ui.View)` pour un logging uniforme des erreurs.

| View | Flow | Timeout |
|---|---|---|
| `CharacterCreateView` | Race select → Class select → Align select → Name modal | — |
| `StarterGearView` | Boutons de kits (2-3) + détails | — |
| `CombatView` | Attaquer / Lancer sort / Défendre / Fuir | 5 min |
| `TargetSelectView` | Dropdown des cibles (combat) | — |
| `SpellSelectView` | Dropdown des sorts disponibles (filtrés par slots) | — |
| `ClarificationView` | Jusqu'à 4 candidats + Annuler (Lot B) | 2 min |
| `StartOnboardingView` | Bouton « Créer Personnage » (re-cliquable pour recommencer) | — |
| `ForceLaunchView` | Bouton « Lancer la partie » réservé au créateur (exclut joueurs non-ready) | 10 min |

`ClarificationView` vérifie via `interaction_check` que seul l'acteur original peut cliquer.
`ForceLaunchView` vérifie que seul le créateur de la campagne (`creator_id`) peut cliquer.

## Embeds (`bot/embeds/`)

Toutes les couleurs sont pilotées par le `tone` renvoyé par le Narrator.

| Embed | Rôle |
|---|---|
| `narrative_embed.py` | Post le narratif + footer des `PublicEffects` ; `build_opening_crawl_embed()` pour l'intro au launch |
| `action_progress_embed.py` | Statut live des 6 phases du pipeline (⚪ / 🔄 / ✅ / ❌) |
| `scene_embed.py` | Scène : location, PNJs, exits, items (post au launch et après MOVE) |
| `beat_embed.py` | Annonce d'avancée de beat (Lot D) |
| `character_embed.py` | Fiche perso complète |
| `combat_embed.py` | Round, current turn, combatants HP |
| `inventory_embed.py` | Équipement + backpack + gold |
| `state_embed.py` | Embed d'état bleu (0x4A90D9) pour les actions QUESTION : items, PNJs, sorties, objectif de beat actif |

Les titres de scène utilisent un emoji thématique choisi par keywords bilingues FR/EN (donjon/dungeon, château/castle, forêt/forest, etc.).

## `campaign_launcher.py` — `CampaignLauncher`

Orchestrateur temporaire qui vit dans `bot.launchers[channel_id]` pendant l'onboarding. Voir [CAMPAIGN_LIFECYCLE.md](CAMPAIGN_LIFECYCLE.md#2-onboarding--campaignlauncher).

## `game_session.py` — `GameSession`

Conteneur in-memory d'une campagne active :

```python
@dataclass
class GameSession:
    campaign: Campaign
    characters: dict[int, Character]
    inventories: dict[int, Inventory]
    spellcasters: dict[int, SpellcasterState | None]
    combat_state: CombatState | None
    current_location: Location | None
    npcs: dict[str, NPC]
    quests: list[Quest]
    story_arc: StoryArc | None
    language: str

    # AI services (optional, None si Ollama down)
    ollama_client: OllamaClient | None
    narrator: Narrator | None
    interpreter: Interpreter | None
    npc_agent: NPCAgent | None
    npc_generator: NPCGenerator | None
    story_director: StoryDirector | None
    semantic_memory: SemanticMemory | None

    # Audit et contrôle
    story_bible: StoryBibleLogger
    action_lock: asyncio.Lock
```

Méthodes clés :
- `advance_beat_if_ready()` — fuzzy match location vs prochain beat (Lot D)
- `get_actor_character(user_id)`, `get_inventory(user_id)`, `get_spellcaster(user_id)`

## `action_pipeline.py` — `ActionPipeline`

Voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md). Point d'entrée : `pipeline.run(player_text, actor_name, progress_callback)`.

## `scene_hydration.py` — `hydrate_scene()`

Résout le problème « le WorldGenerator émet des NPC en `list[str]`, le résolveur d'entités a besoin de vrais objets NPC » :

1. Pour chaque nom dans `location.npcs_present`, si pas de `NPCRow` correspondant → crée un PNJ minimal (HP 4, AC 10, stats 10).
2. Marque chaque PNJ avec `location_name` pour le lookup.
3. Recharge `session.npcs` depuis la DB.

Appelé au launch et après chaque `MOVE`.

Le helper `describe_scene_for_narrator(session)` construit une description markdown de la scène (location, exits, items avec descriptions, PNJs avec disposition + personnalité) pour injection dans le contexte du narrator.

## `story_bible_logger.py` — `StoryBibleLogger`

Voir [NARRATIVE_COHERENCE.md](NARRATIVE_COHERENCE.md#5-story-bible--audit-append-only).

## `llm_retry.py` — `retry_llm_call()`

Voir [AI_LAYER.md](AI_LAYER.md#retry-et-résilience-botllm_retrypy).

## `logging_config.py` — `setup_logging()`

Double output :
- **Console** : format concis (time only, `%H:%M:%S`)
- **Fichier** : `logs/realm_YYYYMMDD_HHMMSS.log` par session, format complet

Custom formatter `_JsonExtraFormatter` ajoute `extra_payload` en JSON compact. Suppression des libs verbeuses (discord, httpx, chromadb).

## `world_navigation.py` — `change_location()`

Helper appelé par le pipeline pour les actions `MOVE`. Si la nouvelle location n'existe pas en DB, appelle `WorldGenerator.generate()` à la volée. Update `session.current_location` + `campaign.current_location` + persistance. Appelle `hydrate_scene()`.

## `i18n.py` — Labels statiques

Dicts nested `{language: {key: translation}}`. Couvre races, classes, alignments, starter kits. **Pas de traduction de contenu dynamique** — seuls les labels statiques sont traduits. Les prompts LLM reçoivent la langue comme directive.

## `config.py` — `GuildConfig`

```python
class GuildConfig(BaseModel):
    guild_id: int
    category_name: str = "RealmAI Sessions"
    language: str = "fr"
```

Persisté via `GuildConfigRepository` (table `guild_configs`). Fetché à chaque `/start_campaign`.

## `utils/channel_manager.py`

- `_slugify(name)` — slug Discord-safe (accents strippés, `campagne-` prefix, ≤100 chars)
- `get_or_create_category(guild, category_name)` — find/create (case-insensitive)
- `create_session_channel(guild, campaign_name, players, bot_member, category_name)` — permissions overrides
- `archive_channel(channel, guild)` — déplace vers `RealmAI Archives`, retire l'écriture

## Anomalies et limitations connues

Extraits — voir [ISSUES.md](ISSUES.md) pour le détail.

- Pas de persistance à chaud de `bot.sessions` : crash = perte de session.
- Pas d'initiative rolls complets — attacker agit en premier (traité comme surprise).
- PNJs en bootstrap combat fight bare-handed (pas de weapon attached).
- Dialogue state non maintenu entre tours non-TALK.
- Emoji selection de scène par keyword anglais — fragile.
- Beat advancement par fuzzy 0.7 — peut rater si noms divergent.
- Trivial kill détection : `max_hp < 10` hardcodé.
- Pas de spell slot recovery on rest implémenté.

## Test coverage (`tests/bot/` et `tests/test_cog_*.py`)

- `test_action_pipeline*.py` — orchestration + TALK + interaction + ambiguity
- `test_action_handler_cog.py` — filtres OOC
- `test_scene_embed.py`, `test_scene_hydration.py`
- `test_cog_character.py`, `test_cog_combat.py`, `test_cog_inventory.py`, `test_cog_exploration.py`, `test_cog_rolls.py`, `test_cog_session.py`
- `test_views.py`, `test_embeds.py`, `test_bot_config.py`, `test_bot.py`
- `test_game_session.py`, `test_campaign_launcher_observability.py`, `test_test_bridge.py`, `test_tester_bot.py`, `test_channel_manager.py`, `test_i18n.py`, `test_llm_retry.py`

Tests majoritairement unitaires avec heavy mocking. Couverture scenario end-to-end via `tests/scenarios/` (voir [TESTING.md](TESTING.md)).
