# Bot Stability Fixes & Narrator Language Support

**Date:** 2026-04-06
**Status:** Draft
**Scope:** 4 issues discovered from production log analysis

---

## Context

First real `/start_campaign` on Discord revealed 3 bugs and 1 missing feature:

1. **Sync Ollama calls block the Discord event loop** — heartbeat blocked for 110+ seconds, near-disconnect
2. **`start_campaign` lacks transactional safety** — Discord channel created but DB insert can fail, leaving orphan channels
3. **Tests pollute the production log** — MagicMock objects in `logs/realm.log`
4. **Narrator responds in English** — no way to configure language per server

---

## 1. Async Ollama Calls via `asyncio.to_thread()`

### Problem

`OllamaClient.chat_json()` is synchronous (uses `openai.OpenAI`). All AI modules (`Narrator`, `WorldGenerator`, `NPCAgent`) call it synchronously. When invoked from async Discord command handlers, the entire event loop blocks until Ollama responds — observed at 131.9 seconds for `qwen3.5:9b`.

### Design

Wrap each sync AI call at the cog level with `asyncio.to_thread()`. The AI modules themselves stay synchronous (pure, testable without async).

### Files to modify

- `bot/cogs/session.py` — 1 call: `gen.generate()` at line 136
- `bot/cogs/exploration.py` — 4 calls:
  - `session.narrator.narrate()` at line 68 (`/look`)
  - `session.npc_agent.respond()` at line 179 (`/talk`)
  - `gen.generate()` at line 272 (`/move` — location generation)
  - `session.narrator.narrate()` at line 309 (`/move` — arrival narration)
- `bot/cogs/combat.py` — 1 call: `session.narrator.narrate()` at line 389 (`_narrate` helper)

### Pattern

```python
import asyncio

# Before (blocks event loop)
result = session.narrator.narrate(mechanics, "")

# After (runs in thread pool)
result = await asyncio.to_thread(session.narrator.narrate, mechanics, "")
```

For `_narrate` in `combat.py`, the helper method is already `async`, so the change is just adding `asyncio.to_thread()` inside it.

### What does NOT change

- `ai/client.py` — stays sync (`openai.OpenAI`)
- `ai/narrator.py`, `ai/world_generator.py`, `ai/npc_agent.py` — stay sync
- All existing tests — no async changes needed in AI module tests

---

## 2. Atomic Transaction for `start_campaign`

### Problem

The current flow in `bot/cogs/session.py:84-153` does:
1. `campaign_repo.save()` + `commit()` (first DB session)
2. `create_session_channel()` (Discord API)
3. `channel_repo.save()` + `commit()` (second DB session)

If step 2 succeeds but step 3 fails, the Discord channel exists with no DB mapping (orphan). If step 2 fails, the campaign is committed but has no channel.

### Design

Use a single DB session with `flush()` (no `commit()`) until all operations succeed:

```python
async def start_campaign(self, interaction, theme, players):
    db_session = self.bot.db_factory()
    channel = None
    try:
        # Read guild config
        config = GuildConfigRepository(db_session).get(guild.id)
        category_name = config.category_name if config else "RealmAI Sessions"

        # Persist campaign (flush only — not committed yet)
        campaign_repo = CampaignRepository(db_session)
        campaign_repo.save(campaign)
        db_session.flush()

        # Create Discord channel
        channel = await create_session_channel(
            guild, theme, player_members, guild.me, category_name,
        )

        # Persist channel mapping
        channel_repo = CampaignChannelRepository(db_session)
        channel_repo.save(channel.id, campaign.id, guild.id)

        # Atomic commit — both campaign and channel mapping
        db_session.commit()
    except Exception:
        db_session.rollback()
        if channel is not None:
            try:
                await channel.delete(reason="Rollback: start_campaign failed")
            except Exception:
                logger.error("Failed to cleanup orphan channel %s", channel.id)
        raise
    finally:
        db_session.close()
```

### Key behaviors

- **Campaign + channel mapping commit together** — either both exist or neither does
- **Discord channel cleaned up on failure** — if DB commit fails after channel creation, the channel is deleted
- **Single DB session** — no more two separate sessions for one logical operation

### Files to modify

- `bot/cogs/session.py` — rewrite `start_campaign` DB flow (lines 92-124)

---

## 3. Test Log Isolation

### Problem

Tests import modules that use `logging.getLogger()`. When `setup_logging()` is called (directly or transitively), it adds a `TimedRotatingFileHandler` to the root logger pointing at `logs/realm.log`. Test output (with MagicMock objects) then pollutes the production log file.

### Design

Add a session-scoped autouse fixture in `tests/conftest.py` that removes any file handlers from the root logger:

```python
@pytest.fixture(autouse=True, scope="session")
def _suppress_file_logging():
    """Prevent tests from writing to production log files."""
    root = logging.getLogger()
    file_handlers = [
        h for h in root.handlers
        if isinstance(h, logging.FileHandler)
    ]
    for h in file_handlers:
        root.removeHandler(h)
    yield
```

### Files to modify

- `tests/conftest.py` — add fixture (+ `import logging`)

---

## 4. Narrator Language per Server

### Problem

All AI prompts (`system_narrator.txt`, `system_world_generator.txt`, `system_npc_agent.txt`) are in English with no language directive. The narrator generates English text regardless of the server's language preference. The bot UI is hardcoded in French, creating a language mismatch.

### Design

Add a `language` field to the guild config, pass it through to AI modules, and inject a language instruction into prompts.

### 4a. Data model changes

**`bot/config.py`** — add field to `GuildConfig`:
```python
class GuildConfig(BaseModel):
    guild_id: int
    category_name: str = Field(default="RealmAI Sessions", min_length=1, max_length=100)
    language: str = Field(default="fr", pattern="^[a-z]{2}$")
```

**`db/models.py`** — add column to `GuildConfigRow`:
```python
class GuildConfigRow(Base):
    __tablename__ = "guild_configs"
    guild_id: Mapped[int] = mapped_column(primary_key=True)
    category_name: Mapped[str] = mapped_column(String(100), default="RealmAI Sessions")
    language: Mapped[str] = mapped_column(String(2), default="fr")
```

**`db/database.py`** — add migration in `_migrate_schema()`:
```python
# After existing combat_state_json migration
result = conn.execute(text("PRAGMA table_info(guild_configs)"))
columns = {row[1] for row in result}
if columns and "language" not in columns:
    conn.execute(
        text("ALTER TABLE guild_configs ADD COLUMN language TEXT DEFAULT 'fr'")
    )
    conn.commit()
```

**`db/mappers.py`** — update `guild_config_to_db()` and `guild_config_from_db()` to include the `language` field (follow existing pattern).

### 4b. Session propagation

**`bot/game_session.py`** — add field to `GameSession`:
```python
@dataclass
class GameSession:
    campaign: Campaign
    # ... existing fields ...
    language: str = "fr"
```

**`bot/cogs/session.py`** — at `/start_campaign` and `/resume`, read `config.language` and set `session.language`:
```python
config = guild_config_repo.get(guild.id)
language = config.language if config else "fr"
# ... later ...
session = GameSession(campaign=campaign, language=language)
```

### 4c. AI prompt injection

Each AI module gains a `language` parameter. A language instruction is prepended to the system prompt.

**Language map** (in a shared constant, e.g. `ai/constants.py` or inline):
```python
LANGUAGE_NAMES: dict[str, str] = {
    "fr": "French",
    "en": "English",
    "es": "Spanish",
    "de": "German",
    "pt": "Portuguese",
}
```

**Pattern for each AI module** (`narrator.py`, `world_generator.py`, `npc_agent.py`):
```python
def narrate(self, action_result_text: str, context_prompt: str, language: str = "fr") -> NarrativeResult:
    lang_name = LANGUAGE_NAMES.get(language, "French")
    lang_instruction = f"IMPORTANT: You MUST write ALL narrative text in {lang_name}.\n\n"
    messages = [
        {"role": "system", "content": lang_instruction + _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    # ... rest unchanged
```

**Cog call sites** — pass `session.language` to each AI call:
```python
result = await asyncio.to_thread(
    session.narrator.narrate, mechanics, "", session.language,
)
```

### 4d. `/settings` command update

**`bot/cogs/session.py`** — add optional `language` parameter:
```python
@app_commands.command(name="settings", description="Configure le bot pour ce serveur")
@app_commands.describe(
    category="Nom de la categorie Discord pour les sessions",
    language="Langue du narrateur (fr, en, es, de, pt)",
)
@app_commands.checks.has_permissions(manage_channels=True)
async def settings(
    self,
    interaction: discord.Interaction,
    category: str | None = None,
    language: str | None = None,
) -> None:
```

Both parameters become optional (`str | None = None`). This is a breaking change from the current signature where `category` is required. The command reads the existing config (or creates a default), applies only the provided fields, and upserts. If neither parameter is given, it displays the current settings.

### Files to modify

- `bot/config.py` — add `language` field
- `db/models.py` — add `language` column
- `db/database.py` — add migration
- `db/mappers.py` — update guild config mappers
- `bot/game_session.py` — add `language` field
- `bot/cogs/session.py` — read language at start/resume, update `/settings`
- `ai/narrator.py` — add `language` parameter
- `ai/world_generator.py` — add `language` parameter
- `ai/npc_agent.py` — add `language` parameter
- `bot/cogs/exploration.py` — pass `session.language` to AI calls
- `bot/cogs/combat.py` — pass `session.language` to AI calls

---

## Verification

### Async calls
1. Start a campaign with Ollama running
2. Verify heartbeat warnings no longer appear in `logs/realm.log`
3. Use `/roll` during world generation to confirm the bot remains responsive

### Atomic transaction
1. Simulate a DB failure after channel creation (e.g., corrupt the DB file temporarily)
2. Verify the Discord channel is deleted on rollback
3. Confirm no orphan campaigns in the DB

### Log isolation
1. Run `uv run pytest` and verify `logs/realm.log` does not contain MagicMock entries
2. Confirm test output still appears in terminal

### Language
1. Set `/settings language:fr`, start a campaign, verify narrator responds in French
2. Set `/settings language:en`, start a new campaign, verify narrator responds in English
3. Verify `/settings` without `language` parameter preserves existing language setting

### Full suite
```bash
uv run pytest
uv run ruff check .
uv run mypy .
```
