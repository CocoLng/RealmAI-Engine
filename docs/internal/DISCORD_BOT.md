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
                     │    ├─ load 7 cogs : rolls, session, character, inventory, combat, action_handler, hint
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

Shell minimal géré par la turn manager. Le cog ne fait plus que charger un factory-only attribut exposé aux autres cogs :

- `build_turn_manager(channel, session) → TurnManager` — crée l'orchestrateur de l'encounter et le rattache à la session.

Toute la logique de tour (prompt PC, brain NPC par tier, timeout 5 min, finalize) vit dans [bot/combat_turn_manager.py](../../bot/combat_turn_manager.py) — voir la section _TurnManager_ plus bas. L'ancien flow (`start_combat_encounter`, `_prompt_turn`, `CombatView`) a été supprimé : incompatible avec le moteur combat multi-ennemi (zones, NPC tier AI, legendary actions, phase transitions).

Les helpers `build_pc_combatants(session)` et `build_npc_combatant(npc)` vivent désormais dans [bot/combat_entry.py](../../bot/combat_entry.py) pour garder un unique point d'entrée combat-bootstrap.

### `exploration.py` — supprimé

L'ancien `ExplorationCog` (`/look`, `/search`, `/talk`, `/move`) a été **supprimé** — remplacé par les actions free-text via `ActionHandlerCog` (`@Realm <action>`).

### `hint.py` — `HintCog`

`/hint` — trois niveaux progressifs d'indice sur le beat courant : niveau 1 déterministe (indice vague, illimité), niveau 2 déterministe (liste des objectifs, 1 fois par beat), niveau 3 via BeatJudge LLM (actions concrètes, cooldown 5 tours). L'usage est persisté par beat dans la table `hint_usage` et reset à l'avancement du beat.

### `rolls.py` — `RollsCog`

`/roll <expression>` — wrapper autour de `engine.dice.roll`.

### `action_handler.py` — `ActionHandlerCog` ⭐

**Cœur de l'UX**. Pas de slash command — écoute `on_message` et intercepte les `@Realm <action>` dans les salons de campagne.

1. Filtre : message court (<4 chars), interjections OOC, non-joueurs.
2. `async with session.action_lock:` — un seul pipeline à la fois.
3. Poste un embed de progression.
4. Instancie `ActionPipeline(actor_name=…, interpreter=…, narrator=…, session=…, …)` — façade sur `bot/pipeline/orchestrator.py`, `actor_name` au constructeur.
5. Exécute `pipeline.process(player_text, progress_callback)`.
6. Dispatch selon retour : `ActionPipelineResult` → embed narratif ; `AmbiguityResult` → `ClarificationView` ; `UnknownEntityResult` → refus in-character.
   - `_render_success` route vers `build_state_embed` (bleu, 0x4A90D9) pour les actions `QUESTION`, affichant items/PNJs/sorties/beat inline.
7. Log dans `story_bible`, persiste `exchanges` ; la progression de beat (`BeatProgressionEngine`) et la planification du Story Director (6 interactions + triggers) sont gérées dans l'orchestrateur.

### `test_bridge.py` — `TestBridgeCog`

Chargé uniquement si `TEST_MODE=true`. Écoute `!test <command>` depuis le `TesterBot` (voir `mcp_discord/`). Simule des messages joueurs, des créations de personnages, des actions de combat pour les scénarios pytest et tests live via MCP. Commandes notables : `!test lobby` ouvre le **vrai** lobby de campagne sur le canal de test (production code — LobbyView, CharacterSetupFlow, pregen — pilotable via `click_button`/`submit_modal`), `!test hint` invoque le `HintCog` réel.

## Views (`bot/views/`)

Toutes héritent de `LoggedView(ui.View)` pour un logging uniforme des erreurs.

| View | Flow | Timeout |
|---|---|---|
| `CharacterCreateView` | Race select → Class select → Align select → Name modal | — |
| `StarterGearView` | Boutons de kits (2-3) + détails | — |
| `CombatActionView` | Attaquer / Sort / Défendre / Fuir / Se déplacer — hub PC édité en place | — (TurnManager timeout) |
| `TargetSelectView` | Ephemeral followup après Attaquer / Sort — enemies vivants ≤ 25 | 60 s |
| `SpellSelectView` | Ephemeral followup après Sort — sorts castables (slots dispo) | 60 s |
| `ZoneSelectView` | Ephemeral followup après Se déplacer — zones adjacentes | 60 s |
| `ClarificationView` | Jusqu'à 4 candidats + Annuler (Lot B) | 2 min |
| `StartOnboardingView` | Bouton « Créer Personnage » (re-cliquable pour recommencer) | — |
| `ForceLaunchView` | Bouton « Lancer la partie » réservé au créateur (exclut joueurs non-ready) | 10 min |

`CombatActionView` et les trois selects secondaires vérifient `interaction.user.id == acting_user_id` dans `interaction_check` (tout autre clic reçoit un message éphémère « Ce n'est pas ton tour. »). Les boutons sont désactivés quand leur pré-condition est vide (pas de cible vivante → Attaquer grisé, pas de sort jetable → Sort grisé, pas de zone adjacente → Se déplacer grisé). La vue elle-même a `timeout=None` — c'est le `TurnManager` qui arme une task asyncio de 5 min qui poste **un rappel unique** (« c'est toujours ton tour »). Aucune action n'est jouée à la place du joueur (décision 2026-07-19) : le tour reste ouvert indéfiniment.

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
| `combat_embed.py` | Hub d'état : round, combatant actif, HP bars, conditions FR, zones |
| `combat_start_embed.py` | Bannière « ⚔️ Combat commence » avec ordre d'initiative et surprise 5e |
| `combat_end_embed.py` | Embed de récap fin de combat : 4 couleurs (🏆 Victoire/💀 Défaite/🏃 Fuite/🕊️ Trêve), champs optionnels (killed_enemies, killed_pcs, fled_pcs, loot, XP, level_ups, durée). Consommé par `TurnManager._finalize`. |
| `dice_embed.py` | Jets d20 : `build_attack_roll_embed`, `build_save_check_embed`, `build_damage_roll_embed`, `build_generic_check_embed` — couleurs hit/miss/crit, français |
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

`GameSession` est désormais une pure dataclass sans méthode : l'accès se fait directement par les dicts (`characters[user_id]`, `inventories[user_id]`, `spellcasters[user_id]`). La progression de beat ne vit plus ici — `advance_beat_if_ready()` (Lot D) a été remplacée par `BeatProgressionEngine` côté orchestrateur.

## `combat_turn_manager.py` — `TurnManager`

Drive le cycle de vie d'une encounter de combat à l'intérieur d'un canal Discord. Une instance par combat actif, stockée sur `session.combat_turn_manager` par `ActionHandlerCog` juste après le bootstrap par le pipeline.

Responsabilités :

1. **Bannière de démarrage** : `start(trigger)` poste `build_combat_start_embed(state, trigger)` une seule fois.
2. **Hub édité en place** : un seul `discord.Message` long-lived par combat (`self.hub_message`). Chaque tour édite `content`/`embed`/`view` via `discord.abc.Messageable.edit`. Les résultats (narration, dice embed) sont postés en dessous pour garder l'historique lisible.
3. **Tour PC** : `_prompt_pc_turn` pose un `CombatActionView` avec `<@user_id>` en ping, start un watcher `asyncio.create_task(self._timeout_watcher(…))` de 5 min. À l'expiration, le watcher poste un rappel unique puis attend — le jeu ne joue jamais à la place du joueur (décision 2026-07-19).
4. **Tour NPC** : `_prompt_npc_turn` passe la main à `_resolve_npc_turn` qui dispatch par tier :
   - `MINION` → `engine.npc_ai.scripted.decide_minion_action`
   - `ELITE` → `engine.npc_ai.elite.decide_elite_action`
   - `BOSS` → `engine.npc_ai.boss_brain.decide_boss_action(tactician=…)` avec `NPCTactician(session.ollama_client)` si disponible, fallback sur `decide_elite_action` sinon.
   Exécute le plan via `execute_action_plan`, poste un `build_attack_roll_embed` pour les attaques, puis appelle `advance_turn` + `on_action_resolved` en récursion jusqu'au prochain PC ou à la fin du combat.
5. **Dispatch boutons** : `dispatch_action(interpreted)` acquiert `session.action_lock`, construit un `ActionPipeline` frais, appelle la méthode publique `pipeline.process_interpreted_action(action)` (bypass interpreter), rend la narration + dice embeds, puis délègue à `on_action_resolved`.
6. **Cues off-turn** : `_flush_pending_cues` vide `state.pending_legendary_summaries` et `state.pending_phase_narrations` en messages compacts (⚡ / 🔥), puis enrichit chaque transition de phase via `narrate_phase_transition`.
7. **Fin de combat** : `_finalize` délègue à `bot.combat_end.finalize_combat(session, end_reason)` (idempotent via flag `_finalized` sur `CombatState`), poste le `build_combat_end_embed(summary)`, fige le hub avec le label emoji correspondant, et clear `session.combat_turn_manager`. **Ne nettoie plus `session.combat_state`** : l'état reste posé avec `is_active=False` pour l'historique / tests / inspection ; le reset à `None` arrive à la prochaine entrée en combat via `bot/combat_entry.py`.
8. **Persistance post-tour** : `TurnManager` reçoit `db_factory` (via `CombatCog.build_turn_manager`) et appelle `_persist_state` (async, off-thread `persist_session`) après `advance_turn` et après `_finalize`. Les actions PC sont déjà auto-checkpointées par le pipeline ; cet ajout couvre les tours NPC et l'état terminal, garantissant qu'une déconnexion Discord n'efface pas la progression du combat.

Points d'extension / non-négociables :

- Le TurnManager **ne touche jamais aux dés** — chaque résolution mécanique passe par le pipeline ou `resolve_npc_attack`. C'est la règle d'or du chantier combat.
- Le pipeline n'avance pas le tour lui-même ; c'est `on_action_resolved` qui appelle `advance_turn(state)`.
- Les free-text actions (`@bot je frappe X`) passent par `ActionHandlerCog._run_pipeline` → `pipeline.process` → `on_action_resolved` (pas par `dispatch_action`). Les deux chemins convergent sur la même méthode pour flusher les cues et prompter le tour suivant.

## `action_pipeline.py` — `ActionPipeline`

Voir [ACTION_PIPELINE.md](ACTION_PIPELINE.md). Façade de compatibilité sur `bot/pipeline/orchestrator.py::PipelineRunner` — `actor_name` passe au constructeur. Point d'entrée : `pipeline.process(player_text, progress_callback)`. Pour le combat, la méthode publique supplémentaire `pipeline.process_interpreted_action(action)` permet aux boutons du hub de sauter la phase d'interprétation avec un `InterpretedAction` déjà structuré.

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
- PNJs en bootstrap combat fight bare-handed (pas de weapon attached).
- Dialogue state non maintenu entre tours non-TALK.
- Emoji selection de scène par keyword anglais — fragile.
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
