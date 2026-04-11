# Persistance — `db/` et `world/`

## Séparation domaine / stockage

- `world/` contient les **modèles de domaine** en Pydantic v2 (`Campaign`, `NPC`, `Location`, `Quest`, `StoryArc`). Utilisés dans toute la codebase en in-memory.
- `db/models.py` contient les **modèles SQLAlchemy** (suffixe `Row` : `CampaignRow`, `NPCRow`, …). Utilisés uniquement par les repos/mappers.
- `db/mappers.py` fournit `{entity}_to_db()` / `{entity}_from_db()` bidirectionnels.
- `db/repositories/` wrap la DB avec une API CRUD orientée domaine.

## Moteur

Défini dans [db/database.py](../../db/database.py).

- **SQLite** (`data/realm.db` par défaut, paramétrable par env).
- `PRAGMA foreign_keys=ON` forcé à chaque connexion.
- **Migrations versionnées** via `PRAGMA user_version`. Chaque version correspond à une fonction `_migrate_vN_to_vN+1(raw)` exécutée dans une transaction avec rollback automatique en cas d'échec.
  - **V0→V1** : colonnes historiques (`combat_state_json`, `language`, `aliases`, `secrets`, `knowledge`, `dialogue_history`, `item_descriptions`). Gardes d'existence conservées pour compatibilité avec les BDD existantes.
  - **V1→V2** : extraction de `current_beat_index` en colonne dédiée sur `story_arcs` (auparavant enfoui dans le blob JSON).
  - **V2→V3** : colonnes `state_flags` (JSON) et `unlocked_exits` (JSON) sur `locations`. State flags track l'état mutable de l'environnement (puzzles résolus, mécanismes activés). Unlocked exits stocke les sorties débloquées par complétion de beats.
- Pattern d'ajout de migration : créer `_migrate_vN_to_vN+1(raw)` et l'ajouter à la liste `_MIGRATIONS`. Le système exécute automatiquement les migrations manquantes au startup.

## Schéma — 10 tables

| Table | PK | FK | Rôle |
|---|---|---|---|
| `campaigns` | `id` (uuid) | — | Métadonnées campagne |
| `npcs` | `id` (auto) | `campaign_id` CASCADE | PNJs, UNIQUE(campaign_id, name) |
| `locations` | `id` (auto) | `campaign_id` CASCADE | Locations, UNIQUE(campaign_id, name) |
| `quests` | `id` (auto) | `campaign_id` CASCADE | Quêtes, UNIQUE(campaign_id, title) |
| `exchanges` | `id` (uuid) | `campaign_id` CASCADE | Layer 2 sliding window |
| `summaries` | `id` (uuid) | `campaign_id` CASCADE | Layer 3 résumés compressés |
| `story_arcs` | `campaign_id` | `campaign_id` CASCADE | Arc 1:1 avec campagne, stocké en JSON blob |
| `player_characters` | `(user_id, campaign_id)` | `campaign_id` CASCADE | Personnages joueurs |
| `campaign_channels` | `channel_id` | `campaign_id` CASCADE | Mapping Discord channel → campagne |
| `guild_configs` | `guild_id` | — | Préférences par serveur Discord |

### Détails des tables

#### `campaigns`
```
id, name, created_at, player_names (JSON), current_location,
interaction_count, combat_state_json (TEXT), language
```

`combat_state_json` est le dump Pydantic (`CombatState.model_dump_json()`) roundtrip via `bot/persistence.py`. Inclut `combat_id` (UUID généré auto), `end_reason` (CombatEndReason | None), `pending_phase_narrations: list[PhaseTransitionEvent]`, et pour chaque `Combatant` les champs Phase 2 (`stat_block`, `fled`, `current_zone`, `action_budget`, `legendary_points_remaining`, `phase_save_bonus`). Les tests `test_combat_state_roundtrips_with_new_fields` (tests/test_combat_phase2.py) et `test_roundtrip_with_combat_state` (tests/test_mappers.py) couvrent la ronde-trip.

#### `npcs`
```
id, campaign_id, name, race, char_class, level,
ability_scores (JSON), hp, max_hp, ac, disposition, is_alive,
description, personality, location_name,
aliases (JSON), secrets (JSON), knowledge (JSON), dialogue_history (JSON),
stat_block_json (TEXT nullable)
```
`NPCRepository.update()` persiste tous les champs, y compris `aliases/secrets/knowledge/dialogue_history` et `stat_block_json`.

`stat_block_json` est la sérialisation Pydantic (`model_dump_json`) d'un `NPCStatBlock` — `NULL` pour les commoners purement narratifs, non-NULL pour les minions/elites/boss. Le mapper `npc_from_db` reconstruit le modèle via `NPCStatBlock.model_validate_json` quand le champ est présent.

#### `locations`
```
id, campaign_id, name, description,
connections (JSON), npcs_present (JSON),
items_available (JSON), item_descriptions (JSON),
state_flags (JSON), unlocked_exits (JSON), combat_zones (JSON)
```

`combat_zones` est une liste JSON de `Zone` sérialisés (`name`, `description`, `adjacent_zone_names`, `tags`). Vide `[]` pour les locations sans combat. Le graphe d'adjacence est re-validé à la reconstruction via le `model_validator` de `Location`.

#### `quests`
```
id, campaign_id, title, description, status,
objectives (JSON), reward_xp, reward_gold, giver_npc
```

#### `exchanges`
```
id, campaign_id, role (PLAYER/NARRATOR/SYSTEM),
content, interaction_number, created_at
```
`campaign_id` indexé. Index composite `(campaign_id, interaction_number)` reste un nice-to-have.

#### `summaries`
```
id, campaign_id, summary_text,
start_interaction, end_interaction, created_at
```

#### `story_arcs`
```
campaign_id (PK, FK), arc_json (TEXT), current_beat_index (INTEGER DEFAULT 0)
```
`arc_json` contient le blob complet de `StoryArc`. `current_beat_index` est extrait en colonne dédiée (V2) pour permettre des updates partiels efficaces. À la lecture, la colonne est autoritaire (la valeur dans le JSON est ignorée).

#### `player_characters`
```
discord_user_id, campaign_id,
character_json (TEXT), inventory_json (TEXT), spellcaster_json (TEXT nullable)
```
Composite PK `(discord_user_id, campaign_id)`. JSON blob complet pour chaque sous-modèle.

#### `campaign_channels`
```
channel_id, campaign_id, guild_id
```

#### `guild_configs`
```
guild_id, category_name, language
```

## Modèles de domaine (`world/`)

### `Campaign` ([world/campaign.py](../../world/campaign.py))
```python
Campaign(id: str, name: str, created_at: datetime,
         player_names: list[str], current_location: str,
         interaction_count: int, combat_state_json: str | None)
```

### `NPC` ([world/npc.py](../../world/npc.py))
```python
NPC(name, race: Race, char_class: CharacterClass | None, level: 1-20,
    ability_scores: AbilityScores, hp, max_hp, ac,
    disposition: Disposition, is_alive: bool,
    description, personality, location_name,
    aliases: list[str], secrets: list[str], knowledge: list[str],
    dialogue_history: list[DialogueExchange])

DialogueExchange(player_said, npc_said, revealed: list[str])
Disposition ∈ {HOSTILE, UNFRIENDLY, NEUTRAL, FRIENDLY, ALLIED}
```
Méthode `kill()` → set `is_alive=False`, `hp=0`.

### `Location` ([world/location.py](../../world/location.py))
```python
Location(name, description,
         connections: list[str], npcs_present: list[str],
         items_available: list[str], item_descriptions: dict[str, str],
         state_flags: dict[str, bool], unlocked_exits: list[str])
```
`state_flags` : état mutable (ex: `{"breach_open": true}`). `unlocked_exits` : sorties débloquées dynamiquement par les `BeatEffects`, distinctes des `connections` toujours disponibles.

⚠ `npcs_present` est `list[str]` — résolu en vrais PNJs par `scene_hydration.hydrate_scene()`.

### `Quest` ([world/quest.py](../../world/quest.py))
```python
Quest(title, description, status: QuestStatus,
      objectives: list[QuestObjective],
      reward_xp, reward_gold, giver_npc)

QuestObjective(description, is_complete)
QuestStatus ∈ {AVAILABLE, ACTIVE, COMPLETED, FAILED}
```

### `StoryArc` ([world/story_arc.py](../../world/story_arc.py))
```python
StoryArc(campaign_id, theme, premise,
         beats: list[StoryBeat] (8-20),
         current_beat_index: int (0-19),
         villain_name, villain_motivation)

StoryBeat(beat_number (1-20), title, description,
          location_hint, npc_names: list[str],
          encounter_type: Literal["social","combat","exploration","puzzle","boss"],
          encounter_subtype: str | None, is_twist: bool,
          completion_trigger: CompletionTrigger | None,
          on_complete: BeatEffects)

CompletionTrigger(type: Literal["interact","defeat","talk","arrive","search","pickup"],
                  target: str)

BeatEffects(unlock_exits: list[str], add_npcs: list[str],
            remove_items: list[str], add_items: list[str],
            state_flags: dict[str, bool], narrative_hint: str)
```
`completion_trigger` définit la condition déterministe de complétion du beat. `on_complete` décrit les mutations à appliquer sur la `Location` quand le beat est complété.

Helper : `advance_beat(arc)` retourne une nouvelle arc avec `current_beat_index+1` (idempotent à la fin).

## Mappers ([db/mappers.py](../../db/mappers.py))

Convention : `{entity}_to_db(model) -> Row` et `{entity}_from_db(row) -> Model`.

Particularités :
- `NPC.dialogue_history` : sérialisé en `[exch.model_dump() for exch in ...]` puis re-validé au from_db.
- `StoryArc` : sérialisé en un seul JSON blob (`arc_json`) via `model_dump_json` / `model_validate_json`.
- `PlayerCharacter_from_db` retourne un tuple `(user_id, Character, Inventory, SpellcasterState | None)` — pas un modèle dédié.
- `CampaignChannel_from_db` retourne un tuple `(channel_id, campaign_id, guild_id)`.

## Repositories (`db/repositories/`)

11 repos, tous suivent le pattern `__init__(session: Session)` et ne commit pas eux-mêmes — le caller doit `db_session.commit()`.

| Repo | Méthodes principales |
|---|---|
| `CampaignRepository` | save, get_by_id, list_all, update, delete |
| `NPCRepository` | save, get_by_name, list_by_campaign, list_by_location, update ⚠, delete |
| `LocationRepository` | save, get_by_name, list_by_campaign, update, delete |
| `QuestRepository` | save, get_by_title, list_by_campaign, update, delete |
| `ExchangeRepository` | save, get_recent(limit=12), get_range, get_unsummarized, count_unsummarized, delete_before |
| `SummaryRepository` | save, get_recent(limit=4), get_latest, list_by_campaign |
| `StoryArcRepository` | save, get_by_campaign, update, delete |
| `PlayerCharacterRepository` | save, get_by_user_campaign, delete |
| `CampaignChannelRepository` | save, get_by_channel, get_by_campaign, delete |
| `GuildConfigRepository` | save, get_by_id, update, delete |

## Relations

```
Campaign (PK=id)
  ├─ NPCs (UNIQUE(campaign_id, name))
  ├─ Locations (UNIQUE(campaign_id, name))
  ├─ Quests (UNIQUE(campaign_id, title))
  ├─ Exchanges (Layer 2)
  ├─ Summaries (Layer 3)
  ├─ StoryArc (1:1)
  ├─ PlayerCharacters (composite PK with discord_user_id)
  └─ CampaignChannels (1:N Discord channels possible)

SemanticMemory (ChromaDB, 1 collection "campaign_<id>", pas de FK SQL)
```

Tout est `CASCADE` delete : supprimer une `campaigns` row nettoie tout l'état SQL associé. ⚠ La collection ChromaDB correspondante **n'est pas supprimée** — fuite de storage.

## Test coverage

- [test_database.py](../../tests/test_database.py) — engine init, foreign keys, migration
- [test_db_repos.py](../../tests/test_db_repos.py) — CRUD pour 4 repos principaux (Campaign, NPC, Location, Quest)
- [test_mappers.py](../../tests/test_mappers.py) — bidirectionnel pour toutes les entités
- [test_player_character_repo.py](../../tests/test_player_character_repo.py)
- [test_campaign_channel_repo.py](../../tests/test_campaign_channel_repo.py)
- [test_world_models.py](../../tests/test_world_models.py) — validation Pydantic
- [test_world_navigation.py](../../tests/test_world_navigation.py)
- [tests/world/](../../tests/world/) — NPC et Location edge cases

## Anomalies

Voir [ISSUES.md](ISSUES.md). Extraits :

- 🟠 `NPCRepository.update()` perd `aliases/secrets/knowledge/dialogue_history`.
- 🟡 Pas d'index sur `(campaign_id, interaction_number)` dans `exchanges`.
- ~~🟡 Migrations manuelles via `ALTER TABLE` sans rollback.~~ ✅ Corrigé — migrations versionnées via `PRAGMA user_version`.
- ~~🟡 `StoryArc` en JSON blob unique → pas d'update partiel possible.~~ ✅ Corrigé — `current_beat_index` extrait en colonne dédiée.
- 🟢 Orphan ChromaDB collections si `/delete campaign` (non implémenté actuellement).
- 🟢 `guild_configs.language` stocké mais i18n dynamique incomplète.
