# Save/Restore Completeness — Design Spec

> Date: 2026-04-06
> Scope: Complete `/save` and `/resume` so all session state survives a bot crash
> Dependencies: Phase 3c (cogs) — complete

---

## 1. Problem

`/save` persists only campaign metadata + player characters. `/resume` restores only those. If the bot crashes, NPCs, quests, and combat state are lost.

## 2. Goals

1. `/resume` reloads NPCs, quests, and combat state from DB
2. `/save` persists combat state, NPCs, and quests to DB
3. Zero new tables or repositories — reuse existing infra
4. Round-trip correctness: save → crash → resume → identical session state

## 3. Design

### 3.1 Combat State Persistence

`CombatState` is a Pydantic BaseModel (as are `Combatant`, `DeathSaves`, `ActiveCondition`). Full JSON round-trip via `model_dump_json()` / `model_validate_json()`.

**Storage:** New nullable column on `CampaignRow`:

```python
# db/models.py — CampaignRow
combat_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Save path** (`_persist_session`):
```python
if session.combat_state is not None:
    campaign_row.combat_state_json = session.combat_state.model_dump_json()
else:
    campaign_row.combat_state_json = None
```

**Restore path** (`/resume`):
- Load `CampaignRow`, if `combat_state_json` is not None → `CombatState.model_validate_json(row.combat_state_json)`
- Set on `GameSession.combat_state`

**Why a column, not a table?** 1:1 with campaign, blob-only (no queries on combat internals), no joins needed.

**DB migration:** Since the DB already exists, `create_all` won't add the column to an existing table. Two options:
- Delete `data/realmai.db` (dev-only, acceptable)
- Or run `ALTER TABLE campaigns ADD COLUMN combat_state_json TEXT` on startup

We'll use the ALTER TABLE approach in `init_db` to be non-destructive: check if the column exists, add it if missing. This is a one-time migration.

### 3.2 NPCs & Quests on GameSession

Add two fields to the `GameSession` dataclass:

```python
@dataclass
class GameSession:
    ...
    npcs: dict[str, NPC] = field(default_factory=dict)       # name → NPC
    quests: list[Quest] = field(default_factory=list)
```

### 3.3 `/resume` — Load NPCs + Quests

After loading player characters and location, add:

```python
npc_repo = NPCRepository(db_session)
npcs = npc_repo.list_by_campaign(campaign_id)

quest_repo = QuestRepository(db_session)
quests = quest_repo.list_by_campaign(campaign_id)
```

Then populate session:
```python
session.npcs = {npc.name: npc for npc in npcs}
session.quests = quests
```

### 3.4 `/save` — Persist NPCs + Quests + Combat State

Extend `_persist_session`:

```python
# Combat state
campaign.combat_state_json = (
    session.combat_state.model_dump_json() if session.combat_state else None
)
# Campaign update already calls camp_repo.update(session.campaign)

# NPCs
npc_repo = NPCRepository(db_session)
for npc in session.npcs.values():
    try:
        npc_repo.update(npc, session.campaign.id)
    except ValueError:
        npc_repo.save(npc, session.campaign.id)

# Quests
quest_repo = QuestRepository(db_session)
for quest in session.quests:
    try:
        quest_repo.update(quest, session.campaign.id)
    except ValueError:
        quest_repo.save(quest, session.campaign.id)
```

### 3.5 Campaign Model + Mapper Changes

The `Campaign` Pydantic model (world/campaign.py) needs a new optional field:

```python
combat_state_json: str | None = None
```

The `campaign_to_db` / `campaign_from_db` mappers need to pass this field through.

### 3.6 DB Migration Helper

In `db/database.py`, after `create_all`, check for missing columns:

```python
def _migrate_schema(engine: Engine) -> None:
    """Add columns introduced after initial schema creation."""
    with engine.connect() as conn:
        # Check if combat_state_json exists on campaigns
        result = conn.execute(text("PRAGMA table_info(campaigns)"))
        columns = {row[1] for row in result}
        if "combat_state_json" not in columns:
            conn.execute(text("ALTER TABLE campaigns ADD COLUMN combat_state_json TEXT"))
            conn.commit()
```

Called from `init_db` after `create_all`.

---

## 4. Files Modified

| File | Change |
|------|--------|
| `bot/game_session.py` | Add `npcs: dict[str, NPC]`, `quests: list[Quest]` fields |
| `db/models.py` | Add `combat_state_json` column to `CampaignRow` |
| `db/database.py` | Add `_migrate_schema()` for ALTER TABLE migration |
| `db/mappers.py` | Pass `combat_state_json` in campaign mappers |
| `world/campaign.py` | Add `combat_state_json: str | None = None` field |
| `bot/cogs/session.py` | `/resume`: load NPCs, quests, combat state; `_persist_session`: save them |

## 5. Files Created

None.

## 6. Testing Strategy

| Test | What it verifies |
|------|-----------------|
| `test_campaign_mapper_combat_state_roundtrip` | Campaign with combat_state_json survives to_db/from_db |
| `test_persist_session_saves_combat_state` | `_persist_session` writes combat JSON to DB |
| `test_persist_session_saves_npcs` | `_persist_session` upserts NPCs |
| `test_persist_session_saves_quests` | `_persist_session` upserts quests |
| `test_resume_loads_combat_state` | `/resume` restores CombatState from DB |
| `test_resume_loads_npcs` | `/resume` populates session.npcs |
| `test_resume_loads_quests` | `/resume` populates session.quests |
| `test_resume_no_combat` | `/resume` with no combat → session.combat_state is None |
| `test_migrate_schema_adds_column` | `_migrate_schema` adds missing column idempotently |
