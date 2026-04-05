# Phase 3a — Bot Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Discord bot foundation — `RealmBot` class with cog loading, `GuildConfig` Pydantic model, and per-guild config persistence in SQLite.

**Architecture:** `RealmBot(commands.Bot)` subclass carries a `db_factory` (SQLAlchemy session factory) as shared dependency. `GuildConfig` is a minimal Pydantic model persisted via `GuildConfigRow` + `GuildConfigRepository`, following the existing mapper/repo pattern. Token loaded from `.env` via `os.environ`.

**Tech Stack:** discord.py 2.7+, Pydantic v2, SQLAlchemy 2.0, pytest, ruff, mypy

**Spec:** `docs/superpowers/specs/2026-04-06-phase3a-bot-foundation-design.md`

---

### Task 1: GuildConfig Pydantic Model

**Files:**
- Create: `bot/config.py`
- Test: `tests/test_bot_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_config.py`:

```python
"""Tests for bot/config.py — GuildConfig model."""

from pydantic import ValidationError
import pytest

from bot.config import GuildConfig


class TestGuildConfig:
    """GuildConfig Pydantic model tests."""

    def test_default_category_name(self) -> None:
        config = GuildConfig(guild_id=123456789)
        assert config.category_name == "RealmAI Sessions"

    def test_custom_category_name(self) -> None:
        config = GuildConfig(guild_id=123456789, category_name="My Category")
        assert config.category_name == "My Category"

    def test_empty_category_name_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GuildConfig(guild_id=123456789, category_name="")

    def test_category_name_too_long_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GuildConfig(guild_id=123456789, category_name="x" * 101)

    def test_serialization_round_trip(self) -> None:
        config = GuildConfig(guild_id=987654321, category_name="Test")
        data = config.model_dump()
        restored = GuildConfig.model_validate(data)
        assert restored == config
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.config'` (or ImportError for GuildConfig)

- [ ] **Step 3: Write minimal implementation**

Create `bot/config.py`:

```python
"""Per-guild bot configuration model."""

from pydantic import BaseModel, Field


class GuildConfig(BaseModel):
    """Per-guild bot configuration."""

    guild_id: int
    category_name: str = Field(
        default="RealmAI Sessions",
        min_length=1,
        max_length=100,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bot_config.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add bot/config.py tests/test_bot_config.py
git commit -m "feat(bot): add GuildConfig Pydantic model"
```

---

### Task 2: GuildConfigRow DB Model

**Files:**
- Modify: `db/models.py` (add GuildConfigRow after SummaryRow)
- Test: `tests/test_bot_config.py` (add table creation test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bot_config.py`:

```python
from sqlalchemy.orm import Session

from db.models import GuildConfigRow


class TestGuildConfigRow:
    """GuildConfigRow SQLAlchemy model tests."""

    def test_row_creation(self, db_session: Session) -> None:
        row = GuildConfigRow(guild_id=123456789, category_name="Test Category")
        db_session.add(row)
        db_session.commit()

        result = db_session.get(GuildConfigRow, 123456789)
        assert result is not None
        assert result.guild_id == 123456789
        assert result.category_name == "Test Category"

    def test_default_category_name(self, db_session: Session) -> None:
        row = GuildConfigRow(guild_id=999)
        db_session.add(row)
        db_session.commit()

        result = db_session.get(GuildConfigRow, 999)
        assert result is not None
        assert result.category_name == "RealmAI Sessions"

    def test_duplicate_guild_id_rejected(self, db_session: Session) -> None:
        from sqlalchemy.exc import IntegrityError

        db_session.add(GuildConfigRow(guild_id=111))
        db_session.commit()
        db_session.add(GuildConfigRow(guild_id=111))
        with pytest.raises(IntegrityError):
            db_session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_config.py::TestGuildConfigRow -v`
Expected: FAIL — `ImportError: cannot import name 'GuildConfigRow'`

- [ ] **Step 3: Write minimal implementation**

Add to the end of `db/models.py`:

```python
class GuildConfigRow(Base):
    """Per-guild bot configuration."""

    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(primary_key=True)
    category_name: Mapped[str] = mapped_column(String(100), default="RealmAI Sessions")
```

Note: Use `Mapped[int]` (not `BigInteger`) — SQLite has no distinction and `BigInteger` would need an extra import. The Python `int` type handles 64-bit Discord snowflakes fine.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bot_config.py -v`
Expected: 8 passed (5 from Task 1 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add db/models.py tests/test_bot_config.py
git commit -m "feat(db): add GuildConfigRow table"
```

---

### Task 3: GuildConfig Mappers

**Files:**
- Modify: `db/mappers.py` (add guild_config_to_db / guild_config_from_db)
- Test: `tests/test_bot_config.py` (add mapper round-trip tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bot_config.py`:

```python
from db.mappers import guild_config_from_db, guild_config_to_db


class TestGuildConfigMappers:
    """GuildConfig mapper round-trip tests."""

    def test_to_db(self) -> None:
        config = GuildConfig(guild_id=123456789, category_name="Custom")
        row = guild_config_to_db(config)
        assert row.guild_id == 123456789
        assert row.category_name == "Custom"

    def test_from_db(self) -> None:
        row = GuildConfigRow(guild_id=987654321, category_name="Test")
        config = guild_config_from_db(row)
        assert config.guild_id == 987654321
        assert config.category_name == "Test"

    def test_round_trip(self) -> None:
        original = GuildConfig(guild_id=555, category_name="Round Trip")
        row = guild_config_to_db(original)
        restored = guild_config_from_db(row)
        assert restored == original

    def test_default_category_round_trip(self) -> None:
        original = GuildConfig(guild_id=777)
        row = guild_config_to_db(original)
        restored = guild_config_from_db(row)
        assert restored.category_name == "RealmAI Sessions"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_config.py::TestGuildConfigMappers -v`
Expected: FAIL — `ImportError: cannot import name 'guild_config_from_db'`

- [ ] **Step 3: Write minimal implementation**

Add to the end of `db/mappers.py`:

```python
from bot.config import GuildConfig
from db.models import GuildConfigRow  # already imported above — just add GuildConfigRow to the existing import


# ---------------------------------------------------------------------------
# GuildConfig
# ---------------------------------------------------------------------------


def guild_config_to_db(config: GuildConfig) -> GuildConfigRow:
    """Convert a GuildConfig domain model to a DB row."""
    return GuildConfigRow(
        guild_id=config.guild_id,
        category_name=config.category_name,
    )


def guild_config_from_db(row: GuildConfigRow) -> GuildConfig:
    """Convert a GuildConfigRow to a GuildConfig domain model."""
    return GuildConfig(
        guild_id=row.guild_id,
        category_name=row.category_name,
    )
```

Important: update the existing `GuildConfigRow` import — add it to the existing `from db.models import ...` line at the top of the file:
```python
from db.models import CampaignRow, ExchangeRow, GuildConfigRow, LocationRow, NPCRow, QuestRow, SummaryRow
```

And add the `GuildConfig` import near the other domain imports:
```python
from bot.config import GuildConfig
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bot_config.py -v`
Expected: 12 passed (5 + 3 + 4)

- [ ] **Step 5: Commit**

```bash
git add db/mappers.py tests/test_bot_config.py
git commit -m "feat(db): add GuildConfig bidirectional mappers"
```

---

### Task 4: GuildConfigRepository

**Files:**
- Create: `db/repositories/guild_config_repo.py`
- Modify: `db/repositories/__init__.py` (add export)
- Test: `tests/test_bot_config.py` (add repo CRUD tests)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_bot_config.py`:

```python
from db.repositories.guild_config_repo import GuildConfigRepository


class TestGuildConfigRepository:
    """GuildConfigRepository CRUD tests."""

    def test_save_and_get(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        config = GuildConfig(guild_id=123456789, category_name="Test")
        repo.save(config)
        db_session.commit()

        result = repo.get(123456789)
        assert result is not None
        assert result.guild_id == 123456789
        assert result.category_name == "Test"

    def test_get_missing_returns_none(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        assert repo.get(999999) is None

    def test_upsert_insert(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        config = GuildConfig(guild_id=111, category_name="New")
        repo.upsert(config)
        db_session.commit()

        result = repo.get(111)
        assert result is not None
        assert result.category_name == "New"

    def test_upsert_update(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.save(GuildConfig(guild_id=222, category_name="Original"))
        db_session.commit()

        repo.upsert(GuildConfig(guild_id=222, category_name="Updated"))
        db_session.commit()

        result = repo.get(222)
        assert result is not None
        assert result.category_name == "Updated"

    def test_delete(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.save(GuildConfig(guild_id=333))
        db_session.commit()

        repo.delete(333)
        db_session.commit()

        assert repo.get(333) is None

    def test_delete_missing_is_noop(self, db_session: Session) -> None:
        repo = GuildConfigRepository(db_session)
        repo.delete(999)  # should not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot_config.py::TestGuildConfigRepository -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.repositories.guild_config_repo'`

- [ ] **Step 3: Write the repository**

Create `db/repositories/guild_config_repo.py`:

```python
"""Persistence operations for GuildConfig entities."""

from sqlalchemy.orm import Session

from bot.config import GuildConfig
from db.mappers import guild_config_from_db, guild_config_to_db
from db.models import GuildConfigRow


class GuildConfigRepository:
    """CRUD operations for per-guild bot configuration."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, guild_id: int) -> GuildConfig | None:
        """Fetch config for a guild, or None if not found."""
        row = self._session.get(GuildConfigRow, guild_id)
        if row is None:
            return None
        return guild_config_from_db(row)

    def save(self, config: GuildConfig) -> None:
        """Insert a new guild config."""
        row = guild_config_to_db(config)
        self._session.add(row)

    def upsert(self, config: GuildConfig) -> None:
        """Insert or update a guild config."""
        row = guild_config_to_db(config)
        self._session.merge(row)

    def delete(self, guild_id: int) -> None:
        """Delete a guild config. No-op if not found."""
        row = self._session.get(GuildConfigRow, guild_id)
        if row is not None:
            self._session.delete(row)
```

- [ ] **Step 4: Update repository exports**

Edit `db/repositories/__init__.py` — add `GuildConfigRepository` to imports and `__all__`:

```python
"""Repository classes for CRUD operations."""

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.guild_config_repo import GuildConfigRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from db.repositories.summary_repo import SummaryRepository

__all__ = [
    "CampaignRepository",
    "ExchangeRepository",
    "GuildConfigRepository",
    "LocationRepository",
    "NPCRepository",
    "QuestRepository",
    "SummaryRepository",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_bot_config.py -v`
Expected: 18 passed (5 + 3 + 4 + 6)

- [ ] **Step 6: Commit**

```bash
git add db/repositories/guild_config_repo.py db/repositories/__init__.py tests/test_bot_config.py
git commit -m "feat(db): add GuildConfigRepository with upsert support"
```

---

### Task 5: RealmBot Class

**Files:**
- Create: `bot/bot.py`
- Test: `tests/test_bot.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot.py`:

```python
"""Tests for bot/bot.py — RealmBot setup and lifecycle."""

import logging
from unittest.mock import AsyncMock, patch

import discord
import pytest

from bot.bot import EXTENSIONS, RealmBot


class TestRealmBot:
    """RealmBot instantiation and configuration tests."""

    def test_intents_message_content_enabled(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        assert bot.intents.message_content is True

    def test_intents_members_enabled(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        assert bot.intents.members is True

    def test_db_factory_is_set(self) -> None:
        with patch("bot.bot.get_engine") as mock_engine, \
             patch("bot.bot.init_db"), \
             patch("bot.bot.get_session_factory", return_value="fake_factory") as mock_sf:
            bot = RealmBot()
        assert bot.db_factory == "fake_factory"
        mock_sf.assert_called_once_with(mock_engine.return_value)

    def test_extensions_list_is_defined(self) -> None:
        assert isinstance(EXTENSIONS, list)


class TestRealmBotSetupHook:
    """setup_hook extension loading tests."""

    @pytest.mark.asyncio()
    async def test_setup_hook_loads_extensions(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        bot.load_extension = AsyncMock()
        bot.tree.sync = AsyncMock()

        with patch("bot.bot.EXTENSIONS", ["bot.cogs.fake_cog"]):
            await bot.setup_hook()

        bot.load_extension.assert_called_once_with("bot.cogs.fake_cog")

    @pytest.mark.asyncio()
    async def test_setup_hook_syncs_tree(self) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        bot.load_extension = AsyncMock()
        bot.tree.sync = AsyncMock()

        await bot.setup_hook()

        bot.tree.sync.assert_called_once()


class TestRealmBotOnReady:
    """on_ready logging tests."""

    @pytest.mark.asyncio()
    async def test_on_ready_logs_info(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch("bot.bot.get_engine"), patch("bot.bot.init_db"), patch("bot.bot.get_session_factory"):
            bot = RealmBot()
        bot._connection = AsyncMock()
        bot._connection.user = "TestBot#1234"
        bot.guilds = [AsyncMock(), AsyncMock()]

        with caplog.at_level(logging.INFO, logger="bot.bot"):
            await bot.on_ready()

        assert "TestBot#1234" in caplog.text
        assert "2 guilds" in caplog.text


class TestRunBot:
    """run_bot() entry point tests."""

    def test_run_bot_requires_token_env(self) -> None:
        from bot.bot import run_bot

        with patch.dict("os.environ", {}, clear=True), \
             pytest.raises(KeyError, match="DISCORD_BOT_TOKEN"):
            run_bot()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bot.bot'`

- [ ] **Step 3: Check pytest-asyncio is installed**

Run: `uv run python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If it fails, install it:
```bash
uv add --dev pytest-asyncio
```

- [ ] **Step 4: Write minimal implementation**

Create `bot/bot.py`:

```python
"""RealmAI Discord bot — setup, cog loading, lifecycle."""

import logging
import os

import discord
from discord.ext import commands

from db.database import get_engine, get_session_factory, init_db

logger = logging.getLogger(__name__)

EXTENSIONS: list[str] = [
    # Phase 3c will populate this list:
    # "bot.cogs.session",
    # "bot.cogs.character",
    # "bot.cogs.rolls",
]


class RealmBot(commands.Bot):
    """RealmAI Discord bot — AI-powered RPG Game Master."""

    db_factory: object  # sessionmaker[Session], typed loosely to avoid import in type position

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        engine = get_engine()
        init_db(engine)
        self.db_factory = get_session_factory(engine)

    async def setup_hook(self) -> None:
        """Load cog extensions and sync the command tree."""
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            logger.info("Loaded extension: %s", ext)
        await self.tree.sync()

    async def on_ready(self) -> None:
        """Log bot startup information."""
        logger.info("%s connected (%d guilds)", self.user, len(self.guilds))


def run_bot() -> None:
    """Entry point — read token from environment and start the bot."""
    token = os.environ["DISCORD_BOT_TOKEN"]
    bot = RealmBot()
    bot.run(token, log_handler=None)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_bot.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add bot/bot.py tests/test_bot.py
git commit -m "feat(bot): add RealmBot class with cog loading and lifecycle"
```

---

### Task 6: Bot Module Exports & Env Template

**Files:**
- Modify: `bot/__init__.py`
- Create: `.env.example`

- [ ] **Step 1: Update bot/__init__.py**

Replace the content of `bot/__init__.py`:

```python
"""Discord bot for RealmAI Engine."""

from bot.bot import RealmBot, run_bot
from bot.config import GuildConfig

__all__ = ["GuildConfig", "RealmBot", "run_bot"]
```

- [ ] **Step 2: Create .env.example**

Create `.env.example`:

```
# Discord bot token (from Developer Portal → Bot → Reset Token)
DISCORD_BOT_TOKEN=
```

- [ ] **Step 3: Verify .gitignore has .env**

Run: `grep -n "^\.env" .gitignore`
Expected: should show `.env` and `.env.*` entries (already present)

- [ ] **Step 4: Commit**

```bash
git add bot/__init__.py .env.example
git commit -m "feat(bot): add module exports and .env.example template"
```

---

### Task 7: Rewrite main.py as Bot Entry Point

**Files:**
- Rewrite: `main.py`

The old terminal REPL combat demo is no longer needed. Replace `main.py` entirely with the Discord bot entry point.

- [ ] **Step 1: Rewrite main.py**

Replace the entire content of `main.py` with:

```python
"""RealmAI Engine — Discord bot entry point.

Start the bot:
    uv run python main.py

Requires DISCORD_BOT_TOKEN in environment (or .env file).
"""

from bot.bot import run_bot


def main() -> None:
    """Launch the Discord bot."""
    run_bot()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the import works**

Run: `uv run python -c "from bot.bot import run_bot; print('import OK')"`
Expected: `import OK`

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: rewrite main.py as Discord bot entry point

Removes the Phase 1 terminal REPL combat demo.
The bot is now launched via: uv run python main.py"
```

---

### Task 8: Quality Gates

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: 644+ passed (626 existing + 18 new), 0 failed

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check .`
Expected: All checks passed (0 errors)

If errors, fix them and re-run.

- [ ] **Step 3: Run mypy**

Run: `uv run mypy bot/ db/`
Expected: Success: no issues found

If errors, fix them and re-run.

- [ ] **Step 4: Clean up bot skeleton files**

Remove the old `.gitkeep` files that are no longer needed since `bot/` now has real content:

```bash
rm -f bot/commands/.gitkeep bot/embeds/.gitkeep bot/views/.gitkeep
```

Note: keep the `commands/`, `embeds/`, and `views/` directories only if they already have `__init__.py` files. If they only contained `.gitkeep`, the directories will be removed by git — that's fine, Phase 3c-3e will recreate them.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore(bot): quality gates green, clean up skeleton files"
```

---

### Task 9: Update todo.md

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Mark Phase 3a complete in todo.md**

Update the Phase 3a section in `tasks/todo.md`:

```markdown
## Phase 3a — Bot Foundation ✅ COMPLETE

- [x] **bot/bot.py** — Bot setup, cog loading, intents, on_ready
- [x] **bot/config.py** — GuildConfig Pydantic model (category per guild, stored in SQLite)

### Quality Gates

- [x] pytest: **644+ tests passing** (626 + 18 new), 0 failed
- [x] ruff check: clean
- [x] mypy: clean on bot/ + db/
```

- [ ] **Step 2: Commit**

```bash
git add tasks/todo.md
git commit -m "chore: mark Phase 3a complete in todo.md"
```
