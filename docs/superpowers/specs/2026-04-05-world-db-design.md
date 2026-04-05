# World Models & DB Persistence — Design Spec

**Date:** 2026-04-05
**Phase:** 2a (first sub-system of Phase 2 — AI Layer)
**Scope:** Minimal viable domain models + SQLAlchemy persistence with repository pattern

---

## Context

Phase 1 delivered a complete deterministic game engine (`engine/`) with 444 tests and 98% coverage. Phase 2 adds the AI layer (interpreter, narrator, memory, world state). This spec covers the **foundation**: domain models for the game world and a persistence layer to store them.

The AI layer (interpreter, narrator, story director) and memory system (4-layer context assembly) will consume these models. Without them, there's nothing to persist or remember.

**Why now:** The memory system needs structured state (Layer 1) from a database. The narrator needs NPC/location context. The interpreter needs to know what exists in the world. Everything depends on these models existing first.

---

## Architecture

```
world/                        # Domain models (pure Pydantic, no DB dependency)
├── __init__.py
├── npc.py                   # NPC model
├── location.py              # Location model
├── quest.py                 # Quest + QuestObjective models
└── campaign.py              # Campaign model (groups everything)

db/                           # Persistence layer
├── __init__.py
├── database.py              # SQLAlchemy engine, session factory, init_db()
├── models.py                # SQLAlchemy table models (mirrors world/)
├── mappers.py               # Domain ↔ DB conversion functions
└── repositories/
    ├── __init__.py
    ├── campaign_repo.py     # CampaignRepository
    ├── npc_repo.py          # NPCRepository
    ├── location_repo.py     # LocationRepository
    └── quest_repo.py        # QuestRepository

tests/
├── test_world_models.py     # Pure Pydantic model tests
├── test_db_repos.py         # Repository tests (SQLite in-memory)
└── test_mappers.py          # Domain ↔ DB round-trip tests
```

---

## Domain Models (`world/`)

### `world/npc.py`

```python
from enum import Enum
from pydantic import BaseModel, Field
from engine.character import Race, CharacterClass, AbilityScores

class NPCDisposition(str, Enum):
    HOSTILE = "hostile"
    UNFRIENDLY = "unfriendly"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"
    ALLIED = "allied"

class NPC(BaseModel):
    name: str
    race: Race
    char_class: CharacterClass | None = None
    level: int = Field(default=1, ge=1, le=20)
    ability_scores: AbilityScores
    hp: int = Field(ge=0)
    max_hp: int = Field(ge=1)
    ac: int = Field(ge=0)
    disposition: NPCDisposition = NPCDisposition.NEUTRAL
    is_alive: bool = True
    description: str = ""
    personality: str = ""           # personality prompt for future NPC agent
    location_name: str | None = None
```

### `world/location.py`

```python
from pydantic import BaseModel

class Location(BaseModel):
    name: str
    description: str = ""
    connections: list[str] = []       # names of adjacent locations
    npcs_present: list[str] = []      # names of NPCs currently here
    items_available: list[str] = []   # names of findable items
```

### `world/quest.py`

```python
from enum import Enum
from pydantic import BaseModel, Field

class QuestStatus(str, Enum):
    AVAILABLE = "available"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"

class QuestObjective(BaseModel):
    description: str
    is_complete: bool = False

class Quest(BaseModel):
    title: str
    description: str = ""
    status: QuestStatus = QuestStatus.AVAILABLE
    objectives: list[QuestObjective] = []
    reward_xp: int = Field(default=0, ge=0)
    reward_gold: int = Field(default=0, ge=0)
    giver_npc: str | None = None
```

### `world/campaign.py`

```python
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import uuid4

class Campaign(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    player_names: list[str] = []
    current_location: str | None = None
    interaction_count: int = 0    # counter for Story Director trigger
```

---

## DB Layer (`db/`)

### `db/database.py`

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from pathlib import Path

DB_PATH = Path("data/realmai.db")

class Base(DeclarativeBase):
    pass

def get_engine(db_url: str | None = None):
    url = db_url or f"sqlite:///{DB_PATH}"
    return create_engine(url)

def get_session_factory(engine=None):
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)

def init_db(engine=None):
    if engine is None:
        engine = get_engine()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
```

### `db/models.py`

SQLAlchemy table models mirroring the domain models:

- **`CampaignRow`** — `id` (PK), `name`, `created_at`, `player_names` (JSON), `current_location`, `interaction_count`
- **`NPCRow`** — `id` (auto PK), `campaign_id` (FK), `name`, `race`, `char_class`, `level`, `ability_scores` (JSON), `hp`, `max_hp`, `ac`, `disposition`, `is_alive`, `description`, `personality`, `location_name`
  - Unique constraint: `(campaign_id, name)`
- **`LocationRow`** — `id` (auto PK), `campaign_id` (FK), `name`, `description`, `connections` (JSON), `npcs_present` (JSON), `items_available` (JSON)
  - Unique constraint: `(campaign_id, name)`
- **`QuestRow`** — `id` (auto PK), `campaign_id` (FK), `title`, `description`, `status`, `objectives` (JSON), `reward_xp`, `reward_gold`, `giver_npc`
  - Unique constraint: `(campaign_id, title)`

All rows have `campaign_id` FK → `CampaignRow.id` with cascade delete.

### `db/mappers.py`

Conversion functions:

```python
def npc_to_db(npc: NPC, campaign_id: str) -> NPCRow: ...
def npc_from_db(row: NPCRow) -> NPC: ...

def location_to_db(location: Location, campaign_id: str) -> LocationRow: ...
def location_from_db(row: LocationRow) -> Location: ...

def quest_to_db(quest: Quest, campaign_id: str) -> QuestRow: ...
def quest_from_db(row: QuestRow) -> Quest: ...

def campaign_to_db(campaign: Campaign) -> CampaignRow: ...
def campaign_from_db(row: CampaignRow) -> Campaign: ...
```

JSON fields (`ability_scores`, `connections`, `objectives`, etc.) are serialized via Pydantic's `.model_dump()` and deserialized via `Model.model_validate()`.

### `db/repositories/`

Each repository follows this interface:

```python
class NPCRepository:
    def __init__(self, session: Session): ...

    def save(self, npc: NPC, campaign_id: str) -> None
    def get_by_name(self, name: str, campaign_id: str) -> NPC | None
    def list_by_campaign(self, campaign_id: str) -> list[NPC]
    def list_by_location(self, location_name: str, campaign_id: str) -> list[NPC]
    def update(self, npc: NPC, campaign_id: str) -> None
    def delete(self, name: str, campaign_id: str) -> None
```

Same pattern for `LocationRepository`, `QuestRepository` (without `list_by_location`), and `CampaignRepository` (keyed by `id` instead of `name`).

**Session management:** Repositories receive a `Session` via constructor injection. The caller manages transaction boundaries (commit/rollback). This keeps repos testable and composable.

---

## Integration with Existing Code

- **`engine/` remains unchanged** — zero modifications
- `world/` imports `Race`, `CharacterClass`, `AbilityScores` from `engine/character.py`
- `NPC` reuses the same enums as `Character` for consistency
- `CombatState` is NOT persisted (stays in-memory during combat, as today)
- Player `Character` persistence is NOT in scope (future work)

---

## Explicitly Out of Scope

- Combat state persistence (remains in-memory)
- Player Character persistence (will be added with Discord bot)
- Facts / Locked Facts (will be added with Story Director)
- Factions (future enrichment)
- Schema migrations (v1 uses `create_all()`, migrations when needed later)
- Async DB access (Ollama is the bottleneck, not SQLite)

---

## Testing Strategy

| Test file | What it covers | DB required? |
|---|---|---|
| `test_world_models.py` | Pydantic validation, defaults, enums | No |
| `test_mappers.py` | Domain ↔ DB round-trip fidelity | Yes (in-memory) |
| `test_db_repos.py` | CRUD operations, unique constraints, cascades | Yes (in-memory) |

**Fixture:** Shared `@pytest.fixture` creating an in-memory SQLite engine + session for repo tests.

**Coverage target:** >90% on `world/` and `db/`.

---

## Verification Plan

1. `uv run pytest tests/test_world_models.py tests/test_mappers.py tests/test_db_repos.py -v` — all green
2. `uv run ruff check world/ db/` — clean
3. `uv run mypy world/ db/` — clean
4. Existing engine tests still pass: `uv run pytest tests/ -v` — no regressions
5. Manual smoke test: create a Campaign with NPCs, Locations, Quests → save → reload → verify equality
