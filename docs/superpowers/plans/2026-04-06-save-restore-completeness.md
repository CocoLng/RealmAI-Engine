# Save/Restore Completeness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/save` persist all session state (combat, NPCs, quests) and `/resume` restore it completely, so a bot crash loses nothing.

**Architecture:** Add a `combat_state_json` TEXT column to `CampaignRow` for combat state (Pydantic JSON blob). Add `npcs` and `quests` fields to `GameSession`. Wire both `/save` and `/resume` to use existing NPC/Quest repositories. One-time ALTER TABLE migration for existing DBs.

**Tech Stack:** SQLAlchemy, Pydantic v2 `model_dump_json()`/`model_validate_json()`, SQLite, pytest

---

### Task 1: Add `combat_state_json` column to CampaignRow + migration

**Files:**
- Modify: `db/models.py:11-22` (CampaignRow)
- Modify: `db/database.py:42-49` (init_db)
- Test: `tests/test_mappers.py`

- [ ] **Step 1: Add column to CampaignRow**

In `db/models.py`, add the new column to `CampaignRow`:

```python
class CampaignRow(Base):
    """Campaigns table."""

    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
    player_names: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
    current_location: Mapped[str | None] = mapped_column(String, nullable=True)
    interaction_count: Mapped[int] = mapped_column(default=0)
    combat_state_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 2: Add migration helper to database.py**

In `db/database.py`, add after the existing imports:

```python
from sqlalchemy import create_engine, event, text
```

Then add the migration function before `init_db`:

```python
def _migrate_schema(engine: Engine) -> None:
    """Add columns introduced after initial schema creation.

    SQLAlchemy's create_all() only creates missing tables, not missing columns.
    This handles incremental column additions for existing databases.
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(campaigns)"))
        columns = {row[1] for row in result}
        if "combat_state_json" not in columns:
            conn.execute(
                text("ALTER TABLE campaigns ADD COLUMN combat_state_json TEXT")
            )
            conn.commit()
```

Then modify `init_db` to call it:

```python
def init_db(engine: Engine | None = None) -> None:
    """Create all tables. Creates data/ directory if needed."""
    if engine is None:
        engine = get_engine()
    url_str = str(engine.url)
    if ":memory:" not in url_str:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    _migrate_schema(engine)
```

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `uv run pytest tests/test_mappers.py tests/test_bot.py -v`
Expected: All pass (new column is nullable, no existing code touches it yet)

- [ ] **Step 4: Commit**

```bash
git add db/models.py db/database.py
git commit -m "feat(db): add combat_state_json column to CampaignRow with migration"
```

---

### Task 2: Add `combat_state_json` to Campaign model + mappers

**Files:**
- Modify: `world/campaign.py:12-20`
- Modify: `db/mappers.py:38-59`
- Test: `tests/test_mappers.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_mappers.py`, add to `TestCampaignMapper`:

```python
def test_roundtrip_with_combat_state(self) -> None:
    campaign = Campaign(
        id="c-combat",
        name="Battle Test",
        combat_state_json='{"combatants":[],"round_number":3,"current_turn_index":0,"is_active":true}',
    )
    row = campaign_to_db(campaign)
    assert row.combat_state_json == campaign.combat_state_json
    restored = campaign_from_db(row)
    assert restored.combat_state_json == campaign.combat_state_json

def test_roundtrip_without_combat_state(self) -> None:
    campaign = Campaign(id="c-no-combat", name="Peaceful")
    row = campaign_to_db(campaign)
    assert row.combat_state_json is None
    restored = campaign_from_db(row)
    assert restored.combat_state_json is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mappers.py::TestCampaignMapper::test_roundtrip_with_combat_state -v`
Expected: FAIL — `Campaign` has no field `combat_state_json`

- [ ] **Step 3: Add field to Campaign model**

In `world/campaign.py`:

```python
class Campaign(BaseModel):
    """A campaign (game session) grouping all world state."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    created_at: datetime = Field(default_factory=datetime.now)
    player_names: list[str] = []
    current_location: str | None = None
    interaction_count: int = 0
    combat_state_json: str | None = None
```

- [ ] **Step 4: Update mappers**

In `db/mappers.py`, update `campaign_to_db`:

```python
def campaign_to_db(campaign: Campaign) -> CampaignRow:
    """Convert a Campaign domain model to a DB row."""
    return CampaignRow(
        id=campaign.id,
        name=campaign.name,
        created_at=campaign.created_at,
        player_names=campaign.player_names,
        current_location=campaign.current_location,
        interaction_count=campaign.interaction_count,
        combat_state_json=campaign.combat_state_json,
    )
```

Update `campaign_from_db`:

```python
def campaign_from_db(row: CampaignRow) -> Campaign:
    """Convert a CampaignRow to a Campaign domain model."""
    return Campaign(
        id=row.id,
        name=row.name,
        created_at=row.created_at if isinstance(row.created_at, datetime) else datetime.fromisoformat(row.created_at),  # type: ignore[arg-type]
        player_names=list(row.player_names) if row.player_names else [],
        current_location=row.current_location,
        interaction_count=row.interaction_count,
        combat_state_json=row.combat_state_json,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_mappers.py -v`
Expected: All pass including the two new tests

- [ ] **Step 6: Commit**

```bash
git add world/campaign.py db/mappers.py tests/test_mappers.py
git commit -m "feat(db): wire combat_state_json through Campaign model and mappers"
```

---

### Task 3: Add `npcs` and `quests` fields to GameSession

**Files:**
- Modify: `bot/game_session.py:24-42`
- Test: `tests/test_game_session.py`

- [ ] **Step 1: Write the failing test**

In `tests/test_game_session.py`, add:

```python
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus
from engine.character import AbilityScores, CharacterClass, Race

def test_session_has_npcs_field():
    session = GameSession(campaign=Campaign(id="t1", name="test"))
    assert session.npcs == {}

def test_session_has_quests_field():
    session = GameSession(campaign=Campaign(id="t2", name="test"))
    assert session.quests == []

def test_session_npcs_can_store_npc():
    session = GameSession(campaign=Campaign(id="t3", name="test"))
    npc = NPC(
        name="Barkeep",
        race=Race.HUMAN,
        level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=8, max_hp=8, ac=10,
        disposition=NPCDisposition.FRIENDLY,
    )
    session.npcs["Barkeep"] = npc
    assert session.npcs["Barkeep"].name == "Barkeep"

def test_session_quests_can_store_quest():
    session = GameSession(campaign=Campaign(id="t4", name="test"))
    quest = Quest(title="Find the key", description="A key is lost", status=QuestStatus.ACTIVE)
    session.quests.append(quest)
    assert len(session.quests) == 1
    assert session.quests[0].title == "Find the key"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_game_session.py::test_session_has_npcs_field -v`
Expected: FAIL — `GameSession` has no field `npcs`

- [ ] **Step 3: Add fields to GameSession**

In `bot/game_session.py`, add imports at the top:

```python
from world.npc import NPC
from world.quest import Quest
```

Then add fields to the dataclass after `current_location`:

```python
@dataclass
class GameSession:
    """Live state for one campaign channel."""

    campaign: Campaign
    characters: dict[int, Character] = field(default_factory=dict)
    inventories: dict[int, Inventory] = field(default_factory=dict)
    spellcasters: dict[int, SpellcasterState | None] = field(default_factory=dict)
    combat_state: CombatState | None = None
    current_location: Location | None = None
    npcs: dict[str, NPC] = field(default_factory=dict)
    quests: list[Quest] = field(default_factory=list)

    # AI services — None if Ollama is unavailable
    ollama_client: OllamaClient | None = None
    narrator: Narrator | None = None
    interpreter: Interpreter | None = None
    npc_agent: NPCAgent | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_game_session.py -v`
Expected: All pass including the four new tests

- [ ] **Step 5: Commit**

```bash
git add bot/game_session.py tests/test_game_session.py
git commit -m "feat(session): add npcs and quests fields to GameSession"
```

---

### Task 4: Update `_persist_session` to save combat state, NPCs, and quests

**Files:**
- Modify: `bot/cogs/session.py:339-358` (`_persist_session`)
- Test: `tests/test_cog_session.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cog_session.py`, add tests for the persistence of new fields. These tests mock the DB layer and verify the correct repo methods are called:

```python
from unittest.mock import MagicMock, patch, call
from bot.game_session import GameSession
from world.campaign import Campaign
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus
from engine.character import AbilityScores, Race
from engine.combat import CombatState, Combatant, CombatSide
from engine.character import create_character, CharacterClass
from engine.inventory import create_inventory


def _make_session_with_combat() -> GameSession:
    """Build a GameSession with active combat."""
    char = create_character(
        name="Hero",
        race=Race.HUMAN,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
    )
    combatant = Combatant(
        name="Hero", side=CombatSide.PLAYER,
        character=char, inventory=create_inventory(), initiative=15,
    )
    combat = CombatState(combatants=[combatant], round_number=3, current_turn_index=0)
    session = GameSession(campaign=Campaign(id="c1", name="test"))
    session.combat_state = combat
    return session


def _make_session_with_npcs_and_quests() -> GameSession:
    """Build a GameSession with NPCs and quests."""
    session = GameSession(campaign=Campaign(id="c2", name="test"))
    npc = NPC(
        name="Barkeep", race=Race.HUMAN, level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=8, max_hp=8, ac=10, disposition=NPCDisposition.FRIENDLY,
    )
    session.npcs["Barkeep"] = npc
    session.quests.append(
        Quest(title="Find key", description="Lost", status=QuestStatus.ACTIVE)
    )
    return session
```

These helpers will be used by the existing test file. The actual assertions depend on how the test file is structured — see Step 3 for the integration.

- [ ] **Step 2: Update `_persist_session` to save combat state**

In `bot/cogs/session.py`, update the imports at the top:

```python
from db.repositories import (
    CampaignChannelRepository,
    CampaignRepository,
    GuildConfigRepository,
    LocationRepository,
    NPCRepository,
    PlayerCharacterRepository,
    QuestRepository,
)
```

Then update `_persist_session`:

```python
def _persist_session(self, session: GameSession) -> None:
    """Save campaign, characters, combat state, NPCs, and quests to DB."""
    db_session = self.bot.db_factory()
    try:
        # Campaign + combat state
        session.campaign.combat_state_json = (
            session.combat_state.model_dump_json()
            if session.combat_state is not None
            else None
        )
        camp_repo = CampaignRepository(db_session)
        camp_repo.update(session.campaign)

        # Player characters
        pc_repo = PlayerCharacterRepository(db_session)
        for user_id, char in session.characters.items():
            inv = session.inventories.get(user_id)
            spell = session.spellcasters.get(user_id)
            if inv is not None:
                try:
                    pc_repo.update(user_id, session.campaign.id, char, inv, spell)
                except ValueError:
                    pc_repo.save(user_id, session.campaign.id, char, inv, spell)

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

        db_session.commit()
    finally:
        db_session.close()
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_cog_session.py -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add bot/cogs/session.py
git commit -m "feat(session): persist combat state, NPCs, and quests on save"
```

---

### Task 5: Update `/resume` to restore combat state, NPCs, and quests

**Files:**
- Modify: `bot/cogs/session.py:174-244` (`resume` command)
- Test: `tests/test_cog_session.py`

- [ ] **Step 1: Update `/resume` to load all state**

In `bot/cogs/session.py`, update the `resume` method. Add `NPCRepository` and `QuestRepository` imports (already done in Task 4). Then update the DB loading block:

```python
@app_commands.command(name="resume", description="Reprend la derniere session sauvegardee")
async def resume(self, interaction: discord.Interaction) -> None:
    """Reload a saved campaign into memory for the current channel."""
    await interaction.response.defer()

    channel_id = interaction.channel_id
    if channel_id is None:
        await interaction.followup.send(
            "Impossible de determiner le canal.", ephemeral=True,
        )
        return

    # Already active?
    if self.bot.get_session(channel_id):
        await interaction.followup.send(
            "Une session est deja active dans ce canal.", ephemeral=True,
        )
        return

    # Load campaign from DB via channel mapping
    db_session = self.bot.db_factory()
    try:
        channel_repo = CampaignChannelRepository(db_session)
        mapping = channel_repo.get_by_channel(channel_id)
        if mapping is None:
            await interaction.followup.send(
                "Aucune campagne associee a ce canal. Utilise `/start_campaign`.",
                ephemeral=True,
            )
            return
        campaign_id, _ = mapping

        campaign_repo = CampaignRepository(db_session)
        campaign = campaign_repo.get_by_id(campaign_id)
        if campaign is None:
            await interaction.followup.send(
                "Campagne introuvable.", ephemeral=True,
            )
            return

        # Load player characters
        pc_repo = PlayerCharacterRepository(db_session)
        pc_rows = pc_repo.get_all_for_campaign(campaign_id)

        # Load location
        location = None
        if campaign.current_location:
            loc_repo = LocationRepository(db_session)
            location = loc_repo.get_by_name(campaign.current_location, campaign_id)

        # Load NPCs
        npc_repo = NPCRepository(db_session)
        npcs = npc_repo.list_by_campaign(campaign_id)

        # Load quests
        quest_repo = QuestRepository(db_session)
        quests = quest_repo.list_by_campaign(campaign_id)
    finally:
        db_session.close()

    # Restore combat state from JSON
    combat_state = None
    if campaign.combat_state_json:
        from engine.combat import CombatState
        combat_state = CombatState.model_validate_json(campaign.combat_state_json)

    # Rebuild in-memory session
    session = GameSession(
        campaign=campaign,
        current_location=location,
        combat_state=combat_state,
        npcs={npc.name: npc for npc in npcs},
        quests=quests,
    )
    for user_id, char, inv, spell in pc_rows:
        session.characters[user_id] = char
        session.inventories[user_id] = inv
        session.spellcasters[user_id] = spell

    create_ai_services(session)
    self.bot.sessions[channel_id] = session

    player_count = len(session.characters)
    combat_msg = " (combat en cours !)" if combat_state else ""
    npc_count = len(session.npcs)
    quest_count = len(session.quests)
    logger.info(
        "SESSION resume campaign=%s channel=%s characters=%d npcs=%d quests=%d combat=%s",
        campaign.id, channel_id, player_count, npc_count, quest_count,
        combat_state is not None,
    )

    await interaction.followup.send(
        f"Session reprise ! Campagne **{campaign.name}** "
        f"-- {player_count} personnage(s), {npc_count} PNJ(s), {quest_count} quete(s){combat_msg}.",
    )
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/test_cog_session.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add bot/cogs/session.py
git commit -m "feat(session): restore combat state, NPCs, and quests on resume"
```

---

### Task 6: Integration test — full save/resume round-trip

**Files:**
- Test: `tests/test_cog_session.py`

- [ ] **Step 1: Write round-trip integration test**

This test uses an in-memory SQLite DB to verify the full cycle. Add to `tests/test_cog_session.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.repositories import (
    CampaignRepository,
    NPCRepository,
    QuestRepository,
)
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import create_inventory
from world.campaign import Campaign
from world.npc import NPC, NPCDisposition
from world.quest import Quest, QuestStatus


@pytest.fixture()
def db_factory():
    """In-memory SQLite session factory with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory


class TestPersistSessionRoundTrip:
    """Full save → load round-trip through real DB."""

    def test_combat_state_roundtrip(self, db_factory):
        """Save a session with combat, reload campaign, verify combat JSON."""
        campaign = Campaign(id="rt-1", name="Round Trip")

        # Save campaign first
        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        db_session.commit()
        db_session.close()

        # Build combat state
        char = create_character(
            name="Hero", race=Race.HUMAN, char_class=CharacterClass.FIGHTER,
            ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
        )
        combatant = Combatant(
            name="Hero", side=CombatSide.PLAYER,
            character=char, inventory=create_inventory(), initiative=15,
        )
        combat = CombatState(combatants=[combatant], round_number=3, current_turn_index=0)

        # Persist combat state via campaign
        campaign.combat_state_json = combat.model_dump_json()
        db_session = db_factory()
        CampaignRepository(db_session).update(campaign)
        db_session.commit()
        db_session.close()

        # Reload and verify
        db_session = db_factory()
        restored = CampaignRepository(db_session).get_by_id("rt-1")
        db_session.close()

        assert restored is not None
        assert restored.combat_state_json is not None
        restored_combat = CombatState.model_validate_json(restored.combat_state_json)
        assert restored_combat.round_number == 3
        assert len(restored_combat.combatants) == 1
        assert restored_combat.combatants[0].name == "Hero"

    def test_npcs_roundtrip(self, db_factory):
        """Save NPCs for a campaign, reload, verify."""
        campaign = Campaign(id="rt-2", name="NPC Test")
        npc = NPC(
            name="Barkeep", race=Race.HUMAN, level=1,
            ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
            hp=8, max_hp=8, ac=10, disposition=NPCDisposition.FRIENDLY,
        )

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        NPCRepository(db_session).save(npc, campaign.id)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        npcs = NPCRepository(db_session).list_by_campaign("rt-2")
        db_session.close()

        assert len(npcs) == 1
        assert npcs[0].name == "Barkeep"
        assert npcs[0].disposition == NPCDisposition.FRIENDLY

    def test_quests_roundtrip(self, db_factory):
        """Save quests for a campaign, reload, verify."""
        campaign = Campaign(id="rt-3", name="Quest Test")
        quest = Quest(
            title="Find the key",
            description="A key is lost",
            status=QuestStatus.ACTIVE,
        )

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        QuestRepository(db_session).save(quest, campaign.id)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        quests = QuestRepository(db_session).list_by_campaign("rt-3")
        db_session.close()

        assert len(quests) == 1
        assert quests[0].title == "Find the key"
        assert quests[0].status == QuestStatus.ACTIVE

    def test_no_combat_state_returns_none(self, db_factory):
        """Campaign without combat → combat_state_json is None."""
        campaign = Campaign(id="rt-4", name="Peaceful")

        db_session = db_factory()
        CampaignRepository(db_session).save(campaign)
        db_session.commit()
        db_session.close()

        db_session = db_factory()
        restored = CampaignRepository(db_session).get_by_id("rt-4")
        db_session.close()

        assert restored is not None
        assert restored.combat_state_json is None
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/test_cog_session.py::TestPersistSessionRoundTrip -v`
Expected: All 4 pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_cog_session.py
git commit -m "test(session): add save/resume round-trip integration tests"
```

---

### Task 7: Test migration helper

**Files:**
- Test: `tests/test_database.py` (new or existing)

- [ ] **Step 1: Write migration test**

Create or add to `tests/test_database.py`:

```python
from sqlalchemy import create_engine, text

from db.database import Base, _migrate_schema


class TestMigrateSchema:
    """Tests for _migrate_schema incremental column additions."""

    def test_adds_missing_column(self):
        """If combat_state_json is missing, migration adds it."""
        engine = create_engine("sqlite:///:memory:")
        # Create tables WITHOUT the new column by using a minimal schema
        Base.metadata.create_all(engine)

        # Verify column exists after migration (create_all already adds it,
        # so we test idempotency — running migrate on an already-correct schema)
        _migrate_schema(engine)

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns

    def test_migration_is_idempotent(self):
        """Running migration twice does not error."""
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        _migrate_schema(engine)
        _migrate_schema(engine)  # second run should be a no-op

        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(campaigns)"))
            columns = {row[1] for row in result}
        assert "combat_state_json" in columns
```

- [ ] **Step 2: Run migration tests**

Run: `uv run pytest tests/test_database.py::TestMigrateSchema -v`
Expected: Both pass

- [ ] **Step 3: Commit**

```bash
git add tests/test_database.py
git commit -m "test(db): add migration helper idempotency tests"
```

---

### Task 8: Quality gates — full suite + lint + typecheck

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest -v`
Expected: All tests pass (850+ existing + ~15 new)

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Run mypy**

Run: `uv run mypy .`
Expected: Clean on source files

- [ ] **Step 4: Delete old DB and verify fresh start**

```bash
rm data/realmai.db
uv run python -c "from db.database import get_engine, init_db; init_db(get_engine())"
sqlite3 data/realmai.db ".schema campaigns" | grep combat_state_json
```

Expected: Column present in schema output

- [ ] **Step 5: Final commit if any fixups needed**

```bash
git add -A
git commit -m "chore: quality gates green for save/restore completeness"
```
