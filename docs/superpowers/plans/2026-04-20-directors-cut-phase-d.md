# Director's Cut — Phase D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Give players a permanent, glanceable view of where they are in the story. A pinned Discord message per campaign channel showing the current chapter, current objective, last 3 beats, and active quests. Updated automatically by Story Director runs. **Zero buttons, zero selects** — pure information; the player still describes actions in free text via `@bot`.

**Architecture:** A new `ArcTrackerEmbed` builder + an `ArcTrackerManager` that owns the message lifecycle (create/update/remove). The pinned message ID persists on `CampaignChannelRow`. Triggered after each Story Director run (background) and on `/start_campaign` / `/end_campaign`.

**Tech Stack:** discord.py 2.4+, SQLAlchemy + SQLite, Pydantic v2, pytest.

**Spec:** [`docs/superpowers/specs/2026-04-20-directors-cut-design.md`](../specs/2026-04-20-directors-cut-design.md) — Section 5 (Arc Tracker).

**Builds on:** Phases A, B, C (in particular `cached_note_for(campaign_id)` from B7 — the Arc Tracker reads `current_objective` from the cached `DirectorNote`).

---

## File Structure

### New files

| File | Responsibility | Approx. lines |
|------|----------------|---------------|
| `bot/embeds/arc_tracker_embed.py` | `build_arc_tracker_embed(...)` returning `discord.Embed` | ~100 |
| `bot/utils/arc_tracker.py` | `ArcTrackerManager` — `ensure_pinned`, `update`, `remove` | ~150 |
| `tests/bot/test_arc_tracker_embed.py` | Embed builder tests | ~80 |
| `tests/bot/test_arc_tracker_manager.py` | Manager lifecycle tests (mocked Discord channel) | ~120 |

### Modified files

| File | Change |
|------|--------|
| `db/models.py` | Add `arc_tracker_message_id: int \| None` column to `CampaignChannelRow` |
| `db/repositories/campaign_channel_repo.py` | Add `update_arc_tracker_message_id(channel_id, msg_id)` method |
| `bot/cogs/session.py` | On `/start_campaign`: call `arc_tracker.ensure_pinned(channel, campaign_id)`. On `/end_campaign`: `arc_tracker.remove(channel, campaign_id)`. |
| `bot/pipeline/orchestrator.py` | After successful `_schedule_story_director` (or after the next director run completes), call `arc_tracker.update(channel_id, campaign_id)` if a hook is provided. |
| `tests/db/test_mappers.py` (or `test_db_repos.py`) | Test the new column persistence |

---

## Tasks Overview

| # | Task | Est. effort |
|---|------|-------------|
| D0 | Baseline verification | 5 min |
| D1 | `ArcTrackerEmbed` builder + tests | 45 min |
| D2 | `ArcTrackerManager` lifecycle + tests (mocked Discord) | 1.5h |
| D3 | DB column + repo method | 30 min |
| D4 | Wire into `/start_campaign` + `/end_campaign` lifecycle | 45 min |

Total: ~3.5 hours.

---

## Task D0: Baseline Verification

```bash
git status                            # clean tree on feat/directors-cut
uv run pytest -q --tb=no 2>&1 | tail -3   # 2146 passed (post-Phase C)
uv run ruff check .                   # clean
```

No commit — gate only.

---

## Task D1: `ArcTrackerEmbed` Builder

**Goal:** Pure function `build_arc_tracker_embed(...)` that returns a `discord.Embed` ready to be sent. No Discord I/O — just data → embed.

**Files:**
- Create: `bot/embeds/arc_tracker_embed.py`
- Create: `tests/bot/test_arc_tracker_embed.py`

### Step 1: Failing tests

Create `tests/bot/test_arc_tracker_embed.py`:

```python
"""Tests for build_arc_tracker_embed."""

import pytest
import discord

from bot.embeds.arc_tracker_embed import build_arc_tracker_embed


class TestBuildArcTrackerEmbed:
    def test_returns_discord_embed(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="Chapter 1 — The Beginning",
            current_objective="Find the lost map.",
            recent_beats=["Found a clue.", "Met the elder."],
            active_quests=["Main: Lost Map", "Side: Help Elena"],
            last_updated_relative="il y a 2 minutes",
        )
        assert isinstance(embed, discord.Embed)
        assert embed.title is not None
        assert "Chapter 1" in embed.title

    def test_objective_in_description(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Find the map.",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="now",
        )
        assert embed.description is not None
        assert "Find the map." in embed.description

    def test_recent_beats_field_includes_top_three(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=["A", "B", "C", "D", "E"],
            active_quests=[],
            last_updated_relative="now",
        )
        # Find the Beats field
        beat_field = next((f for f in embed.fields if "beat" in f.name.lower()), None)
        assert beat_field is not None
        # Should only include last 3
        assert "C" in beat_field.value
        assert "D" in beat_field.value
        assert "E" in beat_field.value
        assert "A" not in beat_field.value
        assert "B" not in beat_field.value

    def test_active_quests_field_includes_top_five(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=[],
            active_quests=[f"Quest {i}" for i in range(7)],
            last_updated_relative="now",
        )
        quest_field = next((f for f in embed.fields if "quête" in f.name.lower() or "quest" in f.name.lower()), None)
        assert quest_field is not None
        assert "Quest 0" in quest_field.value
        assert "Quest 4" in quest_field.value
        assert "Quest 5" not in quest_field.value
        assert "Quest 6" not in quest_field.value

    def test_empty_objective_uses_fallback(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="now",
        )
        # Should not crash; should display some placeholder
        assert embed.description is not None
        assert len(embed.description) > 0

    def test_footer_shows_last_updated(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="il y a 4 actions",
        )
        # Check footer or a dedicated field
        if embed.footer.text:
            assert "il y a 4 actions" in embed.footer.text
        else:
            updated_field = next(
                (f for f in embed.fields if "mise à jour" in f.name.lower() or "updated" in f.name.lower()),
                None,
            )
            assert updated_field is not None
            assert "il y a 4 actions" in updated_field.value
```

### Step 2: Verify failure

```bash
uv run pytest tests/bot/test_arc_tracker_embed.py -v
```

Expected: ModuleNotFoundError.

### Step 3: Implement the embed builder

Create `bot/embeds/arc_tracker_embed.py`:

```python
"""Builder for the Arc Tracker pinned embed.

Pure function: takes data, returns ``discord.Embed``. No Discord I/O.
"""

from __future__ import annotations

import discord


def build_arc_tracker_embed(
    *,
    chapter_title: str,
    current_objective: str,
    recent_beats: list[str],
    active_quests: list[str],
    last_updated_relative: str,
) -> discord.Embed:
    """Build the Arc Tracker pinned embed for a campaign channel.

    Layout (player-facing):
      📖 <chapter_title>
      🎯 Objectif actuel
        <current_objective>
      📜 Beats récents (last 3)
        • <beat>
        • <beat>
        • <beat>
      📋 Quêtes actives (last 5)
        • <quest>
        • ...
      Footer: Mise à jour : <last_updated_relative>
    """
    embed = discord.Embed(
        title=f"📖 {chapter_title}" if chapter_title else "📖 Campagne en cours",
        description=current_objective or "_Aucun objectif clair pour l'instant._",
        color=discord.Color.dark_gold(),
    )

    if recent_beats:
        embed.add_field(
            name="📜 Beats récents",
            value="\n".join(f"• {b[:200]}" for b in recent_beats[-3:]) or "—",
            inline=False,
        )

    if active_quests:
        embed.add_field(
            name="📋 Quêtes actives",
            value="\n".join(f"• {q[:200]}" for q in active_quests[:5]) or "—",
            inline=False,
        )

    embed.set_footer(text=f"Mise à jour : {last_updated_relative}")

    return embed
```

### Step 4-6: Tests + lint + commit

```bash
uv run pytest tests/bot/test_arc_tracker_embed.py -v
uv run pytest -q --tb=no 2>&1 | tail -3
uv run ruff check bot/embeds/arc_tracker_embed.py tests/bot/test_arc_tracker_embed.py

git add bot/embeds/arc_tracker_embed.py tests/bot/test_arc_tracker_embed.py
git commit -m "feat(embeds): add ArcTrackerEmbed builder for pinned-message UI"
```

---

## Task D2: `ArcTrackerManager` Lifecycle

**Goal:** Class managing the pinned message lifecycle: create on first call, edit on subsequent calls, remove on `/end_campaign`. Persists message ID on `CampaignChannelRow` (added in D3 — for D2, use a stub interface so we can develop in parallel).

**Files:**
- Create: `bot/utils/arc_tracker.py`
- Create: `tests/bot/test_arc_tracker_manager.py`

### Step 1: Failing tests

Create `tests/bot/test_arc_tracker_manager.py`:

```python
"""Tests for ArcTrackerManager — pinned message lifecycle."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.utils.arc_tracker import ArcTrackerManager, ArcTrackerData


class TestArcTrackerManager:
    @pytest.mark.asyncio
    async def test_ensure_pinned_creates_new_pin_when_no_existing_id(self) -> None:
        channel = MagicMock()
        channel.send = AsyncMock(return_value=MagicMock(id=12345, pin=AsyncMock()))
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=None)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="Ch.1",
            current_objective="X",
            recent_beats=[], active_quests=[],
            last_updated_relative="now",
        )
        msg_id = await manager.ensure_pinned(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        assert msg_id == 12345
        channel.send.assert_awaited_once()
        sent_msg = channel.send.return_value
        sent_msg.pin.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, 12345)

    @pytest.mark.asyncio
    async def test_ensure_pinned_returns_existing_id_when_present(self) -> None:
        channel = MagicMock()
        channel.send = AsyncMock()
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=99999)

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        msg_id = await manager.ensure_pinned(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        assert msg_id == 99999
        channel.send.assert_not_awaited()  # Don't create when one exists

    @pytest.mark.asyncio
    async def test_update_edits_existing_pin(self) -> None:
        existing_msg = MagicMock()
        existing_msg.edit = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=existing_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=12345)

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        await manager.update(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        existing_msg.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_no_existing_pin_creates_one(self) -> None:
        channel = MagicMock()
        channel.send = AsyncMock(return_value=MagicMock(id=55555, pin=AsyncMock()))
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=None)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        data = ArcTrackerData(
            chapter_title="X", current_objective="Y",
            recent_beats=[], active_quests=[], last_updated_relative="now",
        )
        await manager.update(
            channel=channel, campaign_id="cmp_1", channel_id=999, data=data,
        )
        channel.send.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, 55555)

    @pytest.mark.asyncio
    async def test_remove_unpins_and_clears_id(self) -> None:
        existing_msg = MagicMock()
        existing_msg.unpin = AsyncMock()
        existing_msg.delete = AsyncMock()
        channel = MagicMock()
        channel.fetch_message = AsyncMock(return_value=existing_msg)
        store = MagicMock()
        store.get_message_id = MagicMock(return_value=12345)
        store.set_message_id = MagicMock()

        manager = ArcTrackerManager(store=store)
        await manager.remove(channel=channel, channel_id=999)
        existing_msg.unpin.assert_awaited_once()
        store.set_message_id.assert_called_once_with(999, None)
```

### Step 2: Verify failure

```bash
uv run pytest tests/bot/test_arc_tracker_manager.py -v
```

Expected: ModuleNotFoundError.

### Step 3: Implement manager

Create `bot/utils/arc_tracker.py`:

```python
"""ArcTrackerManager — owns the pinned Arc Tracker message lifecycle.

Operates against a generic ``store`` interface so tests can mock without
touching the DB. In production the store is wired to
:class:`db.repositories.campaign_channel_repo.CampaignChannelRepository`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import discord

from bot.embeds.arc_tracker_embed import build_arc_tracker_embed

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class ArcTrackerData:
    """Plain-data payload for the Arc Tracker embed."""
    chapter_title: str
    current_objective: str
    recent_beats: list[str] = field(default_factory=list)
    active_quests: list[str] = field(default_factory=list)
    last_updated_relative: str = "à l'instant"


class ArcTrackerStore(Protocol):
    """Storage interface for the Arc Tracker message ID per channel."""
    def get_message_id(self, channel_id: int) -> int | None: ...
    def set_message_id(self, channel_id: int, message_id: int | None) -> None: ...


class ArcTrackerManager:
    """Manages the pinned Arc Tracker message for a campaign channel."""

    def __init__(self, *, store: ArcTrackerStore) -> None:
        self._store = store

    async def ensure_pinned(
        self,
        *,
        channel: discord.abc.Messageable,
        campaign_id: str,
        channel_id: int,
        data: ArcTrackerData,
    ) -> int:
        """Create the pinned message if none exists; return its ID."""
        existing = self._store.get_message_id(channel_id)
        if existing is not None:
            return existing

        embed = build_arc_tracker_embed(
            chapter_title=data.chapter_title,
            current_objective=data.current_objective,
            recent_beats=data.recent_beats,
            active_quests=data.active_quests,
            last_updated_relative=data.last_updated_relative,
        )
        msg = await channel.send(embed=embed)
        try:
            await msg.pin()
        except discord.Forbidden:
            logger.warning(
                "Cannot pin Arc Tracker message in channel=%s — missing permissions",
                channel_id,
            )
        self._store.set_message_id(channel_id, msg.id)
        return msg.id

    async def update(
        self,
        *,
        channel: discord.abc.Messageable,
        campaign_id: str,
        channel_id: int,
        data: ArcTrackerData,
    ) -> None:
        """Edit the existing pinned message in-place. If absent, create one."""
        existing = self._store.get_message_id(channel_id)
        embed = build_arc_tracker_embed(
            chapter_title=data.chapter_title,
            current_objective=data.current_objective,
            recent_beats=data.recent_beats,
            active_quests=data.active_quests,
            last_updated_relative=data.last_updated_relative,
        )
        if existing is None:
            msg = await channel.send(embed=embed)
            try:
                await msg.pin()
            except discord.Forbidden:
                logger.warning(
                    "Cannot pin Arc Tracker message in channel=%s", channel_id,
                )
            self._store.set_message_id(channel_id, msg.id)
            return

        try:
            msg = await channel.fetch_message(existing)
            await msg.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden):
            logger.warning(
                "Arc Tracker message %s not found in channel=%s — recreating",
                existing, channel_id,
            )
            new_msg = await channel.send(embed=embed)
            try:
                await new_msg.pin()
            except discord.Forbidden:
                pass
            self._store.set_message_id(channel_id, new_msg.id)

    async def remove(
        self,
        *,
        channel: discord.abc.Messageable,
        channel_id: int,
    ) -> None:
        """Unpin and delete the Arc Tracker message; clear the stored ID."""
        existing = self._store.get_message_id(channel_id)
        if existing is None:
            return
        try:
            msg = await channel.fetch_message(existing)
            await msg.unpin()
            try:
                await msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
        except (discord.NotFound, discord.Forbidden):
            logger.warning(
                "Arc Tracker message %s already gone in channel=%s",
                existing, channel_id,
            )
        self._store.set_message_id(channel_id, None)
```

### Step 4-6: Tests + lint + commit

```bash
uv run pytest tests/bot/test_arc_tracker_manager.py -v
uv run pytest -q --tb=no 2>&1 | tail -3
uv run ruff check bot/utils/arc_tracker.py tests/bot/test_arc_tracker_manager.py

git add bot/utils/arc_tracker.py tests/bot/test_arc_tracker_manager.py
git commit -m "feat(utils): add ArcTrackerManager — pinned message lifecycle"
```

---

## Task D3: Persist `arc_tracker_message_id` on `CampaignChannelRow`

**Goal:** Add a nullable `arc_tracker_message_id` column to `CampaignChannelRow`. Provide a repository method `update_arc_tracker_message_id(channel_id, msg_id)` that the manager's store wraps.

**Files:**
- Modify: `db/models.py` (add column to `CampaignChannelRow`)
- Modify: `db/repositories/campaign_channel_repo.py` (add update method)
- Modify: `tests/db/test_db_repos.py` or `tests/db/test_mappers.py` (verify persistence)

### Step 0: Read

```bash
grep -n "class CampaignChannelRow\|class CampaignChannelRepository\|update\|set_" db/models.py db/repositories/campaign_channel_repo.py | head -30
```

### Step 1: Failing test

Append to the appropriate test file (or create `tests/db/test_arc_tracker_persistence.py`):

```python
"""Tests for CampaignChannelRow.arc_tracker_message_id persistence."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db.models import Base, CampaignChannelRow
from db.repositories.campaign_channel_repo import CampaignChannelRepository


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_arc_tracker_message_id_defaults_to_none(session: Session) -> None:
    row = CampaignChannelRow(channel_id=999, campaign_id="cmp_1", guild_id=42)
    session.add(row)
    session.commit()
    fetched = session.get(CampaignChannelRow, 999)
    assert fetched is not None
    assert fetched.arc_tracker_message_id is None


def test_repository_can_set_and_get_arc_tracker_message_id(session: Session) -> None:
    row = CampaignChannelRow(channel_id=999, campaign_id="cmp_1", guild_id=42)
    session.add(row)
    session.commit()

    repo = CampaignChannelRepository(session)
    repo.update_arc_tracker_message_id(999, 12345)
    session.commit()
    assert repo.get_arc_tracker_message_id(999) == 12345

    repo.update_arc_tracker_message_id(999, None)
    session.commit()
    assert repo.get_arc_tracker_message_id(999) is None
```

(Inspect the actual `CampaignChannelRow` constructor — required fields may differ. Adapt the row fixture accordingly.)

### Step 2: Verify failure

```bash
uv run pytest tests/db/test_arc_tracker_persistence.py -v
```

Expected: AttributeError on `arc_tracker_message_id`.

### Step 3: Update `CampaignChannelRow` in `db/models.py`

Find the `CampaignChannelRow` class and add the column:

```python
    arc_tracker_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, default=None,
    )
```

### Step 4: Add repository methods

In `db/repositories/campaign_channel_repo.py`, add:

```python
    def update_arc_tracker_message_id(
        self, channel_id: int, message_id: int | None,
    ) -> None:
        row = self._session.get(CampaignChannelRow, channel_id)
        if row is None:
            return
        row.arc_tracker_message_id = message_id

    def get_arc_tracker_message_id(self, channel_id: int) -> int | None:
        row = self._session.get(CampaignChannelRow, channel_id)
        return row.arc_tracker_message_id if row is not None else None
```

### Step 5: Run tests + regression

```bash
uv run pytest tests/db/ -q --tb=short
uv run pytest -q --tb=no 2>&1 | tail -3
```

If existing tests break because the SQLite schema is created fresh per test (no migration needed for in-memory engines), they should pass without migration. If a real DB is used, document a migration step in the commit message.

### Step 6: Commit

```bash
git add db/models.py db/repositories/campaign_channel_repo.py tests/db/test_arc_tracker_persistence.py
git commit -m "feat(db): add arc_tracker_message_id column to CampaignChannelRow"
```

---

## Task D4: Wire Lifecycle into `/start_campaign` + `/end_campaign`

**Goal:** Create the pinned Arc Tracker message at campaign start. Remove it on campaign end. Update it after each Story Director run.

**Files:**
- Modify: `bot/cogs/session.py` — `/start_campaign` and `/end_campaign`
- Modify: `bot/pipeline/orchestrator.py` — after `_schedule_story_director` completes, call `arc_tracker.update(...)` if a hook is provided
- Modify: `tests/bot/test_cog_session.py` — verify lifecycle calls happen

### Step 0: Read

Find the existing `/start_campaign` and `/end_campaign` flows in `bot/cogs/session.py`. Note where the campaign channel is created (start) and archived (end).

### Step 1: Compose dependencies

The `ArcTrackerManager` needs:
- A `store` (wraps `CampaignChannelRepository`)
- A discord channel object

Easiest: instantiate the manager + store inline at the call site. Or keep a singleton on `RealmBot` (matches the `self.bot.sessions` pattern).

For simplicity, instantiate inline:

```python
class _RepoBackedStore:
    """Adapts CampaignChannelRepository to the ArcTrackerStore Protocol."""
    def __init__(self, db_factory):
        self._db_factory = db_factory

    def get_message_id(self, channel_id: int) -> int | None:
        with self._db_factory() as session:
            from db.repositories.campaign_channel_repo import CampaignChannelRepository
            return CampaignChannelRepository(session).get_arc_tracker_message_id(channel_id)

    def set_message_id(self, channel_id: int, message_id: int | None) -> None:
        with self._db_factory() as session:
            from db.repositories.campaign_channel_repo import CampaignChannelRepository
            CampaignChannelRepository(session).update_arc_tracker_message_id(channel_id, message_id)
            session.commit()
```

(The `db_factory` returns a context-managed session — confirm by reading existing usages. If it returns a non-context-managed session, adapt.)

### Step 2: `/start_campaign` — create the pin

In `bot/cogs/session.py`, after the campaign channel is created and the `GameSession` is registered, call:

```python
        from bot.utils.arc_tracker import ArcTrackerManager, ArcTrackerData
        store = _RepoBackedStore(self.bot.db_factory)
        manager = ArcTrackerManager(store=store)
        try:
            await manager.ensure_pinned(
                channel=channel,
                campaign_id=campaign.id,
                channel_id=channel.id,
                data=ArcTrackerData(
                    chapter_title="Chapitre 1 — Début de la campagne",
                    current_objective="Découvrez le monde et le pourquoi de votre quête.",
                    recent_beats=[],
                    active_quests=[],
                    last_updated_relative="à l'instant",
                ),
            )
        except Exception:
            logger.warning("Failed to pin Arc Tracker on /start_campaign", exc_info=True)
```

### Step 3: `/end_campaign` — remove the pin

Before the channel is archived/deleted (find the existing flow), call:

```python
        from bot.utils.arc_tracker import ArcTrackerManager
        store = _RepoBackedStore(self.bot.db_factory)
        manager = ArcTrackerManager(store=store)
        try:
            await manager.remove(channel=channel, channel_id=channel.id)
        except Exception:
            logger.warning("Failed to remove Arc Tracker on /end_campaign", exc_info=True)
```

### Step 4: Update on Story Director runs (optional in this phase)

Optional for D4: after the background Story Director runs (in `_schedule_story_director` or via a callback), call `arc_tracker.update(...)`. To keep this phase focused, you may skip this auto-update — the pin will be refreshed manually via a future `/refresh_tracker` command or on the next `/start_campaign`. Note this in the commit if skipped.

If you do wire the auto-update: pass the channel object and a `db_factory` callback into `_schedule_story_director` and have it call `arc_tracker.update(...)` after `check_coherence` succeeds. This is an additional ~30 lines of code.

### Step 5: Tests

Add to `tests/bot/test_cog_session.py`:

```python
class TestStartCampaignArcTracker:
    @pytest.mark.asyncio
    async def test_start_campaign_creates_arc_tracker_pin(self, ..., monkeypatch) -> None:
        # Mock the channel.send + .pin
        # Invoke /start_campaign
        # Assert channel.send was called with an embed containing the chapter title
        ...


class TestEndCampaignArcTracker:
    @pytest.mark.asyncio
    async def test_end_campaign_removes_arc_tracker_pin(self, ..., monkeypatch) -> None:
        # Set up an existing pin (mock the store)
        # Invoke /end_campaign
        # Assert msg.unpin was called
        ...
```

(Adapt to the actual SessionCog test fixtures.)

### Step 6: Run + commit

```bash
uv run pytest tests/bot/test_cog_session.py -v --tb=short
uv run pytest -q --tb=no 2>&1 | tail -3
uv run ruff check bot/cogs/session.py

git add bot/cogs/session.py tests/bot/test_cog_session.py
git commit -m "feat(session): wire ArcTracker pin lifecycle into /start_campaign + /end_campaign"
```

---

## Out of Scope (Phase D)

- Auto-update of Arc Tracker after each Story Director run (optional add-on; can be a follow-up task).
- A refresh-tracker slash command (would call `arc_tracker.update(...)` on demand). Defer.
- Mobile-layout hardening (truncation, char limits per field) beyond the current 200-char per-line cap.

---

## Final Self-Review Checklist (after all D tasks)

1. **Spec coverage:** Section 5 of the spec (Arc Tracker pinned message) → D1 + D2 + D3 + D4 covers it ✓
2. **Player UX preserved:** the pin is information-only; players still describe actions via `@bot` text ✓
3. **DB schema:** `arc_tracker_message_id` is nullable — campaigns from before this change still work ✓
4. **Resilience:** if Discord forbids pinning (channel permissions), the failure is logged but doesn't break campaign creation ✓
5. **Tests:** embed builder, manager lifecycle, DB persistence, cog integration — all have at least one test ✓
