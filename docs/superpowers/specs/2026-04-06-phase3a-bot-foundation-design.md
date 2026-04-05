# Phase 3a — Bot Foundation Design Spec

**Date:** 2026-04-06
**Status:** Draft
**Scope:** `bot/bot.py`, `bot/config.py`, DB persistence for guild config, tests

---

## Context

Phase 2 (AI Layer) is complete — 626 tests passing, all quality gates green. Phase 3 builds the Discord bot. Phase 3a is the foundation: bot setup, cog loading machinery, and per-guild configuration persistence. No slash commands yet (Phase 3c), no channel management (Phase 3b) — just the skeleton that everything else plugs into.

**Design spec reference:** `docs/superpowers/specs/2026-04-05-discord-bot-ux-design.md`

---

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Bot architecture | `RealmBot(commands.Bot)` subclass | Carries shared dependencies (db_factory), clean setup_hook/on_ready |
| Cog loading | Explicit list (`EXTENSIONS`) | Predictable, < 10 cogs total, no magic |
| GuildConfig scope | Minimal (`guild_id` + `category_name`) | YAGNI — add fields when cogs need them |
| Command sync | Global `tree.sync()` in `setup_hook` | Simple, < 100 servers, no dev guild needed |
| Logging | Minimal on_ready | Bot name, guild count, cogs loaded |
| Token management | `os.environ["DISCORD_BOT_TOKEN"]` via `.env` | Never hardcoded, `.env` in `.gitignore` |
| Intents | `default()` + `message_content` + `members` | Need members for @mention resolution |

---

## 1. `bot/config.py` — GuildConfig Model

Pydantic v2 model for per-guild bot configuration.

```python
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

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `guild_id` | `int` | required | Discord guild (server) snowflake ID |
| `category_name` | `str` | `"RealmAI Sessions"` | Discord category name for campaign channels |

Future phases may add: `archive_category_name`, `locale`, `turn_timeout_seconds`. Not now.

---

## 2. `db/models.py` — GuildConfigRow

New SQLAlchemy table, same patterns as existing rows.

```python
class GuildConfigRow(Base):
    __tablename__ = "guild_configs"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_name: Mapped[str] = mapped_column(String(100), default="RealmAI Sessions")
```

**Notes:**
- `BigInteger` for Discord snowflake IDs (64-bit integers)
- `guild_id` is PK (one config per guild)
- No FK to other tables — guild config is independent

---

## 3. `db/mappers.py` — GuildConfig Mappers

Two functions following the existing bidirectional pattern:

```python
def guild_config_to_db(config: GuildConfig) -> GuildConfigRow:
    """Domain model → DB row."""

def guild_config_from_db(row: GuildConfigRow) -> GuildConfig:
    """DB row → domain model."""
```

---

## 4. `db/repositories/guild_config_repo.py` — GuildConfigRepository

Same pattern as existing repos (`__init__(session)`, private `_session`).

### Methods

| Method | Signature | Description |
|--------|-----------|-------------|
| `get` | `(guild_id: int) -> GuildConfig \| None` | Fetch config for a guild, None if not found |
| `save` | `(config: GuildConfig) -> None` | Insert new config |
| `upsert` | `(config: GuildConfig) -> None` | Insert or update (merge). Main entry point for `/settings` |
| `delete` | `(guild_id: int) -> None` | Remove config (guild removal cleanup) |

**Why `upsert`?** The `/settings` command should work whether or not a config already exists. SQLAlchemy `session.merge()` handles this cleanly.

---

## 5. `bot/bot.py` — RealmBot

### Class Structure

```python
import logging
import os

import discord
from discord.ext import commands

from db.database import get_engine, get_session_factory, init_db

logger = logging.getLogger(__name__)

EXTENSIONS: list[str] = [
    # Phase 3c will add:
    # "bot.cogs.session",
    # "bot.cogs.character",
    # "bot.cogs.rolls",
    # etc.
]


class RealmBot(commands.Bot):
    """RealmAI Discord bot — AI-powered RPG Game Master."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

        engine = get_engine()
        init_db(engine)
        self.db_factory = get_session_factory(engine)

    async def setup_hook(self) -> None:
        """Load cog extensions and sync command tree."""
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

### Key Design Points

- **`db_factory` on bot instance**: Cogs access it via `self.bot.db_factory` — no global state, easy to mock in tests.
- **`init_db()` in `__init__`**: Tables are created/verified at bot startup. Idempotent.
- **`log_handler=None`**: We configure logging ourselves (no discord.py default handler overriding).
- **`command_prefix="!"`**: Required by `commands.Bot` but we use slash commands exclusively. Prefix commands won't be registered.
- **`EXTENSIONS` list is empty**: Cogs are added in Phase 3c. The loading machinery is ready.

---

## 6. `bot/__init__.py` — Public API

```python
"""Discord bot for RealmAI Engine."""

from bot.bot import RealmBot, run_bot
from bot.config import GuildConfig

__all__ = ["GuildConfig", "RealmBot", "run_bot"]
```

---

## 7. `.env.example`

Template for required environment variables:

```
# Discord bot token (from Developer Portal)
DISCORD_BOT_TOKEN=
```

Verify `.gitignore` includes `.env`.

---

## 8. Files Modified / Created

| File | Action | Description |
|------|--------|-------------|
| `bot/bot.py` | Create | RealmBot subclass + run_bot() |
| `bot/config.py` | Create | GuildConfig Pydantic model |
| `bot/__init__.py` | Modify | Add public exports |
| `db/models.py` | Modify | Add GuildConfigRow |
| `db/mappers.py` | Modify | Add guild_config_to_db / guild_config_from_db |
| `db/repositories/guild_config_repo.py` | Create | GuildConfigRepository |
| `db/repositories/__init__.py` | Modify | Export GuildConfigRepository |
| `.env.example` | Create | Token template |
| `.gitignore` | Verify | .env entry exists |
| `tests/test_bot/conftest.py` | Create | Shared fixtures (db session, bot instance) |
| `tests/test_bot/test_config.py` | Create | GuildConfig model tests |
| `tests/test_bot/test_guild_config_repo.py` | Create | Repository CRUD tests |
| `tests/test_bot/test_bot.py` | Create | Bot init, extension loading, on_ready |
| `tests/test_db/test_mappers.py` | Modify | Add guild config mapper tests |

---

## 9. Testing Strategy

### Unit Tests

- **`test_config.py`**: GuildConfig defaults, validation (min_length, max_length), serialization
- **`test_guild_config_repo.py`**: CRUD operations — save, get, upsert, delete. Uses in-memory SQLite.
- **`test_mappers.py`**: Round-trip guild_config_to_db → guild_config_from_db equality

### Bot Tests

- **`test_bot.py`**:
  - `RealmBot` instantiation (intents, db_factory set)
  - `setup_hook` loads extensions from `EXTENSIONS` list
  - `on_ready` logs expected message
  - `run_bot()` raises `KeyError` when `DISCORD_BOT_TOKEN` is not set
  - Mock discord.py client (no real connection in tests)

### Quality Gates

- `uv run pytest` — all tests pass (existing 626 + new)
- `uv run ruff check .` — clean
- `uv run mypy .` — clean on source files

---

## 10. Out of Scope

- Slash commands (Phase 3c)
- Channel creation/archival (Phase 3b)
- Combat views / embeds (Phase 3d/3e)
- Exploration / AI integration in bot (Phase 3c, needs Phase 2)
- Multi-bot sharding (not needed for < 100 servers)
