# Memory System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the 4-layer memory system (structured state, sliding window, compressed summaries via Ollama, semantic RAG via ChromaDB) and context assembler that produces ~1500-2500 token prompts for the Narrator LLM.

**Architecture:** Each memory layer is an independent module in `memory/`. Layers 2 and 3 persist to SQLite via new `exchanges` and `summaries` tables. Layer 4 uses ChromaDB with one collection per campaign. The `ContextAssembler` orchestrates all 4 layers, respecting per-layer token budgets, and triggers auto-summarization every ~20 interactions.

**Tech Stack:** Pydantic v2, SQLAlchemy + SQLite, ChromaDB (default all-MiniLM-L6-v2 embeddings), OpenAI client pointed at Ollama localhost:11434/v1 (qwen3.5:9b), pytest

**Spec:** `docs/superpowers/specs/2026-04-05-memory-system-design.md`

---

## File Structure

```
memory/
├── __init__.py              # Public exports
├── models.py                # All Pydantic domain models for memory
├── token_utils.py           # Token estimation + truncation helpers
├── state.py                 # Layer 1 — Structured state builder
├── sliding_window.py        # Layer 2 — Sliding window manager
├── summarizer.py            # Layer 3 — Compressed summaries (Ollama)
├── semantic.py              # Layer 4 — Semantic RAG (ChromaDB)
└── context_assembler.py     # Combines all 4 layers into prompt

db/
├── models.py                # ADD: ExchangeRow, SummaryRow
├── mappers.py               # ADD: exchange_to_db/from_db, summary_to_db/from_db
└── repositories/
    ├── __init__.py           # ADD: ExchangeRepository, SummaryRepository exports
    ├── exchange_repo.py      # NEW
    └── summary_repo.py       # NEW

tests/
├── conftest.py              # ADD: sample_exchange, sample_summary fixtures
├── test_memory_models.py    # Pydantic model validation
├── test_token_utils.py      # Token estimation tests
├── test_memory_repos.py     # Exchange + Summary repo CRUD
├── test_memory_state.py     # Layer 1 build + render
├── test_sliding_window.py   # Layer 2 add/window/render
├── test_summarizer.py       # Layer 3 (mocked Ollama)
├── test_semantic.py         # Layer 4 (EphemeralClient)
└── test_context_assembler.py # Full assembly integration
```

---

### Task 1: Pydantic Domain Models (`memory/models.py`)

**Files:**
- Create: `memory/models.py`
- Test: `tests/test_memory_models.py`

- [ ] **Step 1: Write failing tests for all memory models**

```python
# tests/test_memory_models.py
"""Tests for memory/models.py — Pydantic model validation."""

import pytest

from memory.models import (
    CharacterSummary,
    CombatSummary,
    CompressedSummary,
    ContextBudget,
    ExchangeRole,
    GameStateSummary,
    NarrativeExchange,
    SemanticDocument,
    SemanticDocumentType,
)


class TestCharacterSummary:
    """CharacterSummary model tests."""

    def test_create_minimal(self) -> None:
        cs = CharacterSummary(
            name="Thorin", race="Dwarf", char_class="Fighter",
            level=5, hp=35, max_hp=40, ac=16,
        )
        assert cs.name == "Thorin"
        assert cs.conditions == []

    def test_with_conditions(self) -> None:
        cs = CharacterSummary(
            name="Thorin", race="Dwarf", char_class="Fighter",
            level=5, hp=35, max_hp=40, ac=16,
            conditions=["Poisoned", "Prone"],
        )
        assert cs.conditions == ["Poisoned", "Prone"]


class TestCombatSummary:
    """CombatSummary model tests."""

    def test_defaults(self) -> None:
        cs = CombatSummary()
        assert cs.is_active is False
        assert cs.round_number == 0
        assert cs.current_turn is None
        assert cs.combatants == []

    def test_active_combat(self) -> None:
        char = CharacterSummary(
            name="Goblin", race="Goblin", char_class="",
            level=1, hp=4, max_hp=7, ac=13,
        )
        cs = CombatSummary(
            is_active=True, round_number=3,
            current_turn="Thorin", combatants=[char],
        )
        assert cs.is_active is True
        assert len(cs.combatants) == 1


class TestGameStateSummary:
    """GameStateSummary model tests."""

    def test_minimal(self) -> None:
        gss = GameStateSummary(campaign_name="Lost Mines")
        assert gss.campaign_name == "Lost Mines"
        assert gss.current_location is None
        assert gss.player_characters == []
        assert gss.combat is None

    def test_full(self) -> None:
        gss = GameStateSummary(
            campaign_name="Lost Mines",
            current_location="Neverwinter",
            location_description="A bustling city",
            nearby_npcs=["Gundren"],
            active_quests=["Find the Lost Mine"],
            inventory_highlights=["Healing Potion x3"],
        )
        assert gss.nearby_npcs == ["Gundren"]


class TestNarrativeExchange:
    """NarrativeExchange model tests."""

    def test_create(self) -> None:
        ex = NarrativeExchange(
            campaign_id="c1", role=ExchangeRole.PLAYER,
            content="I attack the goblin.", interaction_number=1,
        )
        assert ex.campaign_id == "c1"
        assert ex.role == ExchangeRole.PLAYER
        assert ex.id  # auto-generated UUID

    def test_all_roles(self) -> None:
        for role in ExchangeRole:
            ex = NarrativeExchange(
                campaign_id="c1", role=role,
                content="test", interaction_number=1,
            )
            assert ex.role == role


class TestCompressedSummary:
    """CompressedSummary model tests."""

    def test_create(self) -> None:
        cs = CompressedSummary(
            campaign_id="c1", summary_text="The party arrived.",
            start_interaction=1, end_interaction=20,
        )
        assert cs.start_interaction == 1
        assert cs.end_interaction == 20
        assert cs.id  # auto-generated UUID


class TestSemanticDocument:
    """SemanticDocument model tests."""

    def test_create(self) -> None:
        sd = SemanticDocument(
            campaign_id="c1", doc_type=SemanticDocumentType.NPC_SHEET,
            content="Gundren is a dwarf prospector.",
            metadata={"npc_name": "Gundren"},
        )
        assert sd.doc_type == SemanticDocumentType.NPC_SHEET
        assert sd.metadata["npc_name"] == "Gundren"

    def test_all_doc_types(self) -> None:
        for dt in SemanticDocumentType:
            sd = SemanticDocument(
                campaign_id="c1", doc_type=dt, content="test",
            )
            assert sd.doc_type == dt


class TestContextBudget:
    """ContextBudget model tests."""

    def test_defaults(self) -> None:
        cb = ContextBudget()
        assert cb.layer1_max == 450
        assert cb.layer2_max == 700
        assert cb.layer3_max == 400
        assert cb.layer4_max == 350
        assert cb.total_max == 2500

    def test_custom(self) -> None:
        cb = ContextBudget(layer1_max=300, total_max=1500)
        assert cb.layer1_max == 300
        assert cb.total_max == 1500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.models'`

- [ ] **Step 3: Implement memory/models.py**

```python
# memory/models.py
"""Pydantic v2 domain models for the 4-layer memory system."""

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Layer 1 — Structured state snapshot
# ---------------------------------------------------------------------------


class CharacterSummary(BaseModel):
    """Compact character representation for context injection."""

    name: str
    race: str
    char_class: str
    level: int
    hp: int
    max_hp: int
    ac: int
    conditions: list[str] = Field(default_factory=list)


class CombatSummary(BaseModel):
    """Compact combat state for context injection."""

    is_active: bool = False
    round_number: int = 0
    current_turn: str | None = None
    combatants: list[CharacterSummary] = Field(default_factory=list)


class GameStateSummary(BaseModel):
    """Layer 1 output: structured snapshot of current game state."""

    campaign_name: str
    current_location: str | None = None
    location_description: str = ""
    player_characters: list[CharacterSummary] = Field(default_factory=list)
    nearby_npcs: list[str] = Field(default_factory=list)
    active_quests: list[str] = Field(default_factory=list)
    combat: CombatSummary | None = None
    inventory_highlights: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer 2 — Narrative exchanges
# ---------------------------------------------------------------------------


class ExchangeRole(StrEnum):
    """Who produced this exchange."""

    PLAYER = "player"
    NARRATOR = "narrator"
    SYSTEM = "system"


class NarrativeExchange(BaseModel):
    """A single narrative exchange (player input or narrator output)."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    role: ExchangeRole
    content: str
    interaction_number: int
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Layer 3 — Compressed summaries
# ---------------------------------------------------------------------------


class CompressedSummary(BaseModel):
    """An auto-generated summary covering a range of interactions."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    summary_text: str
    start_interaction: int
    end_interaction: int
    created_at: datetime = Field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Layer 4 — Semantic memory documents
# ---------------------------------------------------------------------------


class SemanticDocumentType(StrEnum):
    """Categories of documents stored in semantic memory."""

    WORLD_LORE = "world_lore"
    NPC_SHEET = "npc_sheet"
    PAST_EVENT = "past_event"
    LOCATION_DETAIL = "location_detail"
    QUEST_DETAIL = "quest_detail"


class SemanticDocument(BaseModel):
    """A document to store in ChromaDB for semantic retrieval."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    doc_type: SemanticDocumentType
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context assembler config
# ---------------------------------------------------------------------------


class ContextBudget(BaseModel):
    """Token budget per layer. Total should be ~1500-2500."""

    layer1_max: int = 450
    layer2_max: int = 700
    layer3_max: int = 400
    layer4_max: int = 350
    total_max: int = 2500
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory_models.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/models.py tests/test_memory_models.py
git commit -m "feat: add Pydantic domain models for 4-layer memory system"
```

---

### Task 2: Token Utilities (`memory/token_utils.py`)

**Files:**
- Create: `memory/token_utils.py`
- Test: `tests/test_token_utils.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_token_utils.py
"""Tests for memory/token_utils.py — token estimation and truncation."""

from memory.token_utils import estimate_tokens, truncate_to_tokens


class TestEstimateTokens:
    """Token estimation tests."""

    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 0

    def test_single_word(self) -> None:
        # 1 word * 1.3 = 1.3 → rounded to 1
        assert estimate_tokens("hello") == 1

    def test_ten_words(self) -> None:
        text = "one two three four five six seven eight nine ten"
        # 10 * 1.3 = 13
        assert estimate_tokens(text) == 13

    def test_multiline(self) -> None:
        text = "line one\nline two\nline three"
        # 6 words * 1.3 = 7.8 → 8
        assert estimate_tokens(text) == 8


class TestTruncateToTokens:
    """Token truncation tests."""

    def test_within_budget(self) -> None:
        text = "short text"
        assert truncate_to_tokens(text, 100) == text

    def test_exact_budget(self) -> None:
        text = "one two three"
        # 3 words * 1.3 = 3.9 → 4 tokens
        result = truncate_to_tokens(text, 4)
        assert result == text

    def test_over_budget_truncates(self) -> None:
        text = "one two three four five six seven eight nine ten"
        result = truncate_to_tokens(text, 5)
        # 5 tokens / 1.3 ≈ 3.8 → keeps ~3 words
        assert len(result.split()) < 10
        assert estimate_tokens(result) <= 5

    def test_empty_string(self) -> None:
        assert truncate_to_tokens("", 10) == ""

    def test_zero_budget(self) -> None:
        assert truncate_to_tokens("some text", 0) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_token_utils.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement memory/token_utils.py**

```python
# memory/token_utils.py
"""Token estimation and truncation utilities.

Uses a simple word-based approximation: tokens ≈ words × 1.3.
No external tokenizer dependency needed.
"""

import math


def estimate_tokens(text: str) -> int:
    """Approximate token count from text.

    Uses the heuristic: tokens ≈ words × 1.3 (rounded up).
    Returns 0 for empty strings.
    """
    if not text or not text.strip():
        return 0
    word_count = len(text.split())
    return math.ceil(word_count * 1.3)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text at word boundaries to fit within a token budget.

    Returns the original text if it fits, otherwise removes words
    from the end until the estimate is within budget.
    """
    if max_tokens <= 0:
        return ""
    if not text or not text.strip():
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    words = text.split()
    # Binary search for the max number of words that fits
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = " ".join(words[:mid])
        if estimate_tokens(candidate) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1

    if lo == 0:
        return ""
    return " ".join(words[:lo])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_token_utils.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/token_utils.py tests/test_token_utils.py
git commit -m "feat: add token estimation and truncation utilities"
```

---

### Task 3: DB Models + Mappers + Repos (exchanges & summaries)

**Files:**
- Modify: `db/models.py` — add `ExchangeRow`, `SummaryRow`
- Modify: `db/mappers.py` — add exchange/summary mapper functions
- Create: `db/repositories/exchange_repo.py`
- Create: `db/repositories/summary_repo.py`
- Modify: `db/repositories/__init__.py` — add exports
- Modify: `tests/conftest.py` — add fixtures
- Test: `tests/test_memory_repos.py`

- [ ] **Step 1: Write failing tests for Exchange and Summary repos**

```python
# tests/test_memory_repos.py
"""Tests for Exchange and Summary repositories — CRUD with in-memory SQLite."""

import pytest
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from world.campaign import Campaign


class TestExchangeRepository:
    """Exchange CRUD tests."""

    def test_save_and_get_recent(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 4):
            ex = NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Message {i}",
                interaction_number=i,
            )
            repo.save(ex)
        db_session.commit()

        results = repo.get_recent(sample_campaign.id, limit=2)
        assert len(results) == 2
        # Should be in ASC order (oldest first)
        assert results[0].interaction_number == 2
        assert results[1].interaction_number == 3

    def test_get_recent_returns_asc_order(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 6):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.NARRATOR,
                content=f"Narration {i}",
                interaction_number=i,
            ))
        db_session.commit()

        results = repo.get_recent(sample_campaign.id, limit=3)
        assert [r.interaction_number for r in results] == [3, 4, 5]

    def test_get_range(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 11):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Msg {i}",
                interaction_number=i,
            ))
        db_session.commit()

        results = repo.get_range(sample_campaign.id, start=3, end=7)
        assert len(results) == 5
        assert results[0].interaction_number == 3
        assert results[-1].interaction_number == 7

    def test_get_unsummarized(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 26):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Msg {i}",
                interaction_number=i,
            ))
        db_session.commit()

        # Assume last summarized was interaction 10
        results = repo.get_unsummarized(sample_campaign.id, last_summarized=10)
        assert len(results) == 15
        assert results[0].interaction_number == 11

    def test_count_unsummarized(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 26):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Msg {i}",
                interaction_number=i,
            ))
        db_session.commit()

        count = repo.count_unsummarized(sample_campaign.id, last_summarized=10)
        assert count == 15

    def test_delete_before(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = ExchangeRepository(db_session)

        for i in range(1, 6):
            repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Msg {i}",
                interaction_number=i,
            ))
        db_session.commit()

        repo.delete_before(sample_campaign.id, interaction_number=3)
        db_session.commit()

        results = repo.get_recent(sample_campaign.id, limit=10)
        assert len(results) == 3
        assert results[0].interaction_number == 3

    def test_campaign_scoping(self, db_session: Session) -> None:
        CampaignRepository(db_session).save(Campaign(id="c1", name="First"))
        CampaignRepository(db_session).save(Campaign(id="c2", name="Second"))
        repo = ExchangeRepository(db_session)

        repo.save(NarrativeExchange(campaign_id="c1", role=ExchangeRole.PLAYER, content="A", interaction_number=1))
        repo.save(NarrativeExchange(campaign_id="c2", role=ExchangeRole.PLAYER, content="B", interaction_number=1))
        db_session.commit()

        assert len(repo.get_recent("c1", limit=10)) == 1
        assert len(repo.get_recent("c2", limit=10)) == 1

    def test_cascade_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)
        repo = ExchangeRepository(db_session)
        repo.save(NarrativeExchange(
            campaign_id=sample_campaign.id, role=ExchangeRole.PLAYER,
            content="Test", interaction_number=1,
        ))
        db_session.commit()

        camp_repo.delete(sample_campaign.id)
        db_session.commit()

        assert repo.get_recent(sample_campaign.id, limit=10) == []


class TestSummaryRepository:
    """Summary CRUD tests."""

    def test_save_and_get_recent(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)

        for i in range(3):
            repo.save(CompressedSummary(
                campaign_id=sample_campaign.id,
                summary_text=f"Summary {i + 1}",
                start_interaction=i * 20 + 1,
                end_interaction=(i + 1) * 20,
            ))
        db_session.commit()

        results = repo.get_recent(sample_campaign.id, limit=2)
        assert len(results) == 2
        # ASC order: oldest first
        assert results[0].start_interaction == 21
        assert results[1].start_interaction == 41

    def test_get_latest(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)

        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="First",
            start_interaction=1, end_interaction=20,
        ))
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="Second",
            start_interaction=21, end_interaction=40,
        ))
        db_session.commit()

        latest = repo.get_latest(sample_campaign.id)
        assert latest is not None
        assert latest.summary_text == "Second"
        assert latest.end_interaction == 40

    def test_get_latest_empty(self, db_session: Session) -> None:
        repo = SummaryRepository(db_session)
        assert repo.get_latest("nonexistent") is None

    def test_list_by_campaign(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        repo = SummaryRepository(db_session)

        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="S1",
            start_interaction=1, end_interaction=20,
        ))
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="S2",
            start_interaction=21, end_interaction=40,
        ))
        db_session.commit()

        results = repo.list_by_campaign(sample_campaign.id)
        assert len(results) == 2

    def test_cascade_delete(self, db_session: Session, sample_campaign: Campaign) -> None:
        camp_repo = CampaignRepository(db_session)
        camp_repo.save(sample_campaign)
        repo = SummaryRepository(db_session)
        repo.save(CompressedSummary(
            campaign_id=sample_campaign.id, summary_text="Test",
            start_interaction=1, end_interaction=20,
        ))
        db_session.commit()

        camp_repo.delete(sample_campaign.id)
        db_session.commit()

        assert repo.list_by_campaign(sample_campaign.id) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_repos.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.repositories.exchange_repo'`

- [ ] **Step 3: Add ExchangeRow and SummaryRow to db/models.py**

Add after the existing `QuestRow` class at the end of `db/models.py`:

```python
class ExchangeRow(Base):
    """Narrative exchanges table (Layer 2 memory)."""

    __tablename__ = "exchanges"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=False)
    interaction_number: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)


class SummaryRow(Base):
    """Compressed summaries table (Layer 3 memory)."""

    __tablename__ = "summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    summary_text: Mapped[str] = mapped_column(String, nullable=False)
    start_interaction: Mapped[int] = mapped_column(nullable=False)
    end_interaction: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False)
```

- [ ] **Step 4: Add mapper functions to db/mappers.py**

Add imports at top of `db/mappers.py`:
```python
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from db.models import ExchangeRow, SummaryRow
```

Add at end of file:
```python
# ---------------------------------------------------------------------------
# NarrativeExchange
# ---------------------------------------------------------------------------


def exchange_to_db(exchange: NarrativeExchange) -> ExchangeRow:
    """Convert a NarrativeExchange domain model to a DB row."""
    return ExchangeRow(
        id=exchange.id,
        campaign_id=exchange.campaign_id,
        role=exchange.role.value,
        content=exchange.content,
        interaction_number=exchange.interaction_number,
        created_at=exchange.created_at,
    )


def exchange_from_db(row: ExchangeRow) -> NarrativeExchange:
    """Convert an ExchangeRow to a NarrativeExchange domain model."""
    return NarrativeExchange(
        id=row.id,
        campaign_id=row.campaign_id,
        role=ExchangeRole(row.role),
        content=row.content,
        interaction_number=row.interaction_number,
        created_at=(
            row.created_at
            if isinstance(row.created_at, datetime)
            else datetime.fromisoformat(row.created_at)  # type: ignore[arg-type]
        ),
    )


# ---------------------------------------------------------------------------
# CompressedSummary
# ---------------------------------------------------------------------------


def summary_to_db(summary: CompressedSummary) -> SummaryRow:
    """Convert a CompressedSummary domain model to a DB row."""
    return SummaryRow(
        id=summary.id,
        campaign_id=summary.campaign_id,
        summary_text=summary.summary_text,
        start_interaction=summary.start_interaction,
        end_interaction=summary.end_interaction,
        created_at=summary.created_at,
    )


def summary_from_db(row: SummaryRow) -> CompressedSummary:
    """Convert a SummaryRow to a CompressedSummary domain model."""
    return CompressedSummary(
        id=row.id,
        campaign_id=row.campaign_id,
        summary_text=row.summary_text,
        start_interaction=row.start_interaction,
        end_interaction=row.end_interaction,
        created_at=(
            row.created_at
            if isinstance(row.created_at, datetime)
            else datetime.fromisoformat(row.created_at)  # type: ignore[arg-type]
        ),
    )
```

- [ ] **Step 5: Create db/repositories/exchange_repo.py**

```python
# db/repositories/exchange_repo.py
"""Exchange repository — CRUD for narrative exchanges."""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.mappers import exchange_from_db, exchange_to_db
from db.models import ExchangeRow
from memory.models import NarrativeExchange


class ExchangeRepository:
    """Persistence operations for NarrativeExchange entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, exchange: NarrativeExchange) -> None:
        """Insert a new exchange."""
        row = exchange_to_db(exchange)
        self._session.add(row)

    def get_recent(self, campaign_id: str, limit: int = 12) -> list[NarrativeExchange]:
        """Get the last N exchanges, returned in ASC order (oldest first)."""
        subq = (
            select(ExchangeRow)
            .where(ExchangeRow.campaign_id == campaign_id)
            .order_by(ExchangeRow.interaction_number.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(ExchangeRow).join(
            subq, ExchangeRow.id == subq.c.id
        ).order_by(ExchangeRow.interaction_number.asc())
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def get_range(
        self, campaign_id: str, start: int, end: int
    ) -> list[NarrativeExchange]:
        """Get exchanges with interaction_number between start and end inclusive."""
        stmt = (
            select(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number >= start,
                ExchangeRow.interaction_number <= end,
            )
            .order_by(ExchangeRow.interaction_number.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def get_unsummarized(
        self, campaign_id: str, last_summarized: int
    ) -> list[NarrativeExchange]:
        """Get exchanges after the last summarized interaction, in ASC order."""
        stmt = (
            select(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number > last_summarized,
            )
            .order_by(ExchangeRow.interaction_number.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [exchange_from_db(r) for r in rows]

    def count_unsummarized(self, campaign_id: str, last_summarized: int) -> int:
        """Count exchanges after the last summarized interaction."""
        stmt = (
            select(func.count())
            .select_from(ExchangeRow)
            .where(
                ExchangeRow.campaign_id == campaign_id,
                ExchangeRow.interaction_number > last_summarized,
            )
        )
        result = self._session.execute(stmt).scalar()
        return result or 0

    def delete_before(self, campaign_id: str, interaction_number: int) -> None:
        """Delete exchanges older than the given interaction_number."""
        stmt = delete(ExchangeRow).where(
            ExchangeRow.campaign_id == campaign_id,
            ExchangeRow.interaction_number < interaction_number,
        )
        self._session.execute(stmt)
```

- [ ] **Step 6: Create db/repositories/summary_repo.py**

```python
# db/repositories/summary_repo.py
"""Summary repository — CRUD for compressed summaries."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.mappers import summary_from_db, summary_to_db
from db.models import SummaryRow
from memory.models import CompressedSummary


class SummaryRepository:
    """Persistence operations for CompressedSummary entities."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, summary: CompressedSummary) -> None:
        """Insert a new summary."""
        row = summary_to_db(summary)
        self._session.add(row)

    def get_recent(
        self, campaign_id: str, limit: int = 4
    ) -> list[CompressedSummary]:
        """Get the last N summaries, returned in ASC order (oldest first)."""
        subq = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(SummaryRow).join(
            subq, SummaryRow.id == subq.c.id
        ).order_by(SummaryRow.end_interaction.asc())
        rows = self._session.execute(stmt).scalars().all()
        return [summary_from_db(r) for r in rows]

    def get_latest(self, campaign_id: str) -> CompressedSummary | None:
        """Get the most recent summary, or None if none exist."""
        stmt = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        if row is None:
            return None
        return summary_from_db(row)

    def list_by_campaign(self, campaign_id: str) -> list[CompressedSummary]:
        """List all summaries in a campaign."""
        stmt = (
            select(SummaryRow)
            .where(SummaryRow.campaign_id == campaign_id)
            .order_by(SummaryRow.end_interaction.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [summary_from_db(r) for r in rows]
```

- [ ] **Step 7: Update db/repositories/__init__.py**

Replace the entire file with:
```python
"""Repository classes for CRUD operations."""

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from db.repositories.summary_repo import SummaryRepository

__all__ = [
    "CampaignRepository",
    "ExchangeRepository",
    "LocationRepository",
    "NPCRepository",
    "QuestRepository",
    "SummaryRepository",
]
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory_repos.py -v`
Expected: All PASS

- [ ] **Step 9: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_db_repos.py -v`
Expected: All PASS (existing tests unaffected)

- [ ] **Step 10: Commit**

```bash
git add db/models.py db/mappers.py db/repositories/exchange_repo.py db/repositories/summary_repo.py db/repositories/__init__.py tests/test_memory_repos.py
git commit -m "feat: add Exchange and Summary DB models, mappers, and repositories"
```

---

### Task 4: Layer 1 — Structured State Builder (`memory/state.py`)

**Files:**
- Create: `memory/state.py`
- Modify: `tests/conftest.py` — add `sample_character` and `sample_inventory` fixtures
- Test: `tests/test_memory_state.py`

- [ ] **Step 1: Add fixtures to tests/conftest.py**

Add these imports at the top of `tests/conftest.py`:
```python
from engine.character import Alignment, Character, create_character
from engine.inventory import (
    Inventory,
    Item,
    ItemType,
    Rarity,
    Weapon,
    WeaponCategory,
    WeaponProperty,
    DamageType,
    EquipmentSlot,
    create_inventory,
    equip_item,
    add_item,
)
```

Add these fixtures at the end of `tests/conftest.py`:
```python
@pytest.fixture()
def sample_character() -> Character:
    """A test character (Dwarf Fighter level 5)."""
    return create_character(
        name="Thorin",
        race=Race.DWARF,
        char_class=CharacterClass.FIGHTER,
        ability_scores=AbilityScores(STR=16, DEX=12, CON=14, INT=10, WIS=13, CHA=8),
    )


@pytest.fixture()
def sample_inventory() -> Inventory:
    """A test inventory with a weapon equipped."""
    inv = create_inventory()
    sword = Weapon(
        name="Longsword",
        item_type=ItemType.WEAPON,
        weight=3.0,
        value_gp=15,
        rarity=Rarity.COMMON,
        description="A standard longsword",
        damage_dice="1d8",
        damage_type=DamageType.SLASHING,
        weapon_category=WeaponCategory.MARTIAL_MELEE,
        properties=[],
    )
    inv = add_item(inv, sword)
    return inv
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_memory_state.py
"""Tests for memory/state.py — Layer 1 structured state builder."""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from engine.character import Character
from engine.combat import CombatSide, CombatState, Combatant
from engine.inventory import Inventory, create_inventory
from memory.models import GameStateSummary
from memory.state import StateBuilder
from memory.token_utils import estimate_tokens
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest


class TestStateBuilder:
    """StateBuilder tests."""

    def test_build_minimal(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)

        assert isinstance(summary, GameStateSummary)
        assert summary.campaign_name == sample_campaign.name
        assert summary.player_characters == []
        assert summary.combat is None

    def test_build_with_location(
        self, db_session: Session, sample_campaign: Campaign, sample_location: Location,
    ) -> None:
        campaign = sample_campaign.model_copy(update={"current_location": "Neverwinter"})
        CampaignRepository(db_session).save(campaign)
        LocationRepository(db_session).save(sample_location, campaign.id)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(campaign.id)

        assert summary.current_location == "Neverwinter"
        assert summary.location_description == "A bustling coastal city"

    def test_build_with_npcs_at_location(
        self, db_session: Session, sample_campaign: Campaign,
        sample_location: Location, sample_npc: NPC,
    ) -> None:
        campaign = sample_campaign.model_copy(update={"current_location": "Neverwinter"})
        CampaignRepository(db_session).save(campaign)
        LocationRepository(db_session).save(sample_location, campaign.id)
        NPCRepository(db_session).save(sample_npc, campaign.id)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(campaign.id)

        assert "Gundren Rockseeker (friendly)" in summary.nearby_npcs

    def test_build_with_active_quests(
        self, db_session: Session, sample_campaign: Campaign, sample_quest: Quest,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        QuestRepository(db_session).save(sample_quest, sample_campaign.id)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)

        assert "Find the Lost Mine (active)" in summary.active_quests

    def test_build_with_characters(
        self, db_session: Session, sample_campaign: Campaign, sample_character: Character,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id, player_characters=[sample_character])

        assert len(summary.player_characters) == 1
        assert summary.player_characters[0].name == "Thorin"
        assert summary.player_characters[0].race == "Dwarf"

    def test_build_with_combat_state(
        self, db_session: Session, sample_campaign: Campaign,
        sample_character: Character, sample_inventory: Inventory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        combatant = Combatant(
            name="Thorin", side=CombatSide.PLAYER,
            character=sample_character, inventory=sample_inventory,
            initiative=15, is_alive=True,
        )
        combat = CombatState(
            combatants=[combatant], round_number=3,
            current_turn_index=0, is_active=True,
        )

        builder = StateBuilder(db_session)
        summary = builder.build(
            sample_campaign.id, player_characters=[sample_character],
            combat_state=combat,
        )

        assert summary.combat is not None
        assert summary.combat.is_active is True
        assert summary.combat.round_number == 3
        assert summary.combat.current_turn == "Thorin"

    def test_render_basic(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        text = builder.render(summary)

        assert "[GAME STATE]" in text
        assert sample_campaign.name in text

    def test_render_within_token_budget(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        builder = StateBuilder(db_session)
        summary = builder.build(sample_campaign.id)
        text = builder.render(summary, max_tokens=450)

        assert estimate_tokens(text) <= 450
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_memory_state.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.state'`

- [ ] **Step 4: Implement memory/state.py**

```python
# memory/state.py
"""Layer 1 — Structured state builder.

Reads from existing SQLite repositories (Campaign, NPC, Location, Quest)
and accepts in-memory objects (Character, CombatState, Inventory) to build
a compact GameStateSummary for prompt injection.
"""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from engine.character import Character
from engine.combat import CombatState
from engine.conditions import ActiveCondition
from engine.inventory import Inventory
from memory.models import (
    CharacterSummary,
    CombatSummary,
    GameStateSummary,
)
from memory.token_utils import truncate_to_tokens


class StateBuilder:
    """Builds a structured state summary from DB and in-memory state."""

    def __init__(self, session: Session) -> None:
        self._campaign_repo = CampaignRepository(session)
        self._npc_repo = NPCRepository(session)
        self._location_repo = LocationRepository(session)
        self._quest_repo = QuestRepository(session)

    def build(
        self,
        campaign_id: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> GameStateSummary:
        """Build a GameStateSummary from all structured data sources."""
        campaign = self._campaign_repo.get_by_id(campaign_id)
        if campaign is None:
            return GameStateSummary(campaign_name="Unknown")

        # Location
        current_location = campaign.current_location
        location_description = ""
        if current_location:
            loc = self._location_repo.get_by_name(current_location, campaign_id)
            if loc:
                location_description = loc.description

        # NPCs at current location
        nearby_npcs: list[str] = []
        if current_location:
            npcs = self._npc_repo.list_by_location(current_location, campaign_id)
            nearby_npcs = [
                f"{npc.name} ({npc.disposition.value})" for npc in npcs if npc.is_alive
            ]

        # Active quests
        quests = self._quest_repo.list_by_campaign(campaign_id)
        active_quests = [
            f"{q.title} ({q.status.value})"
            for q in quests
            if q.status.value in ("active", "available")
        ]

        # Player characters
        char_summaries: list[CharacterSummary] = []
        if player_characters:
            for char in player_characters:
                char_summaries.append(CharacterSummary(
                    name=char.name,
                    race=char.race.value,
                    char_class=char.char_class.value,
                    level=char.level,
                    hp=char.hp,
                    max_hp=char.max_hp,
                    ac=char.ac,
                ))

        # Combat state
        combat_summary: CombatSummary | None = None
        if combat_state and combat_state.is_active:
            current_combatant = combat_state.combatants[combat_state.current_turn_index]
            combat_chars = [
                CharacterSummary(
                    name=c.name,
                    race=c.character.race.value,
                    char_class=c.character.char_class.value,
                    level=c.character.level,
                    hp=c.character.hp,
                    max_hp=c.character.max_hp,
                    ac=c.character.ac,
                    conditions=[cond.condition_type.value for cond in c.conditions],
                )
                for c in combat_state.combatants
                if c.is_alive
            ]
            combat_summary = CombatSummary(
                is_active=True,
                round_number=combat_state.round_number,
                current_turn=current_combatant.name,
                combatants=combat_chars,
            )

        # Inventory highlights
        inventory_highlights: list[str] = []
        if inventories:
            for name, inv in inventories.items():
                notable = [
                    f"{item.name} x{item.quantity}" if item.quantity > 1 else item.name
                    for item in inv.items
                    if item.magical or item.rarity.value != "Common"
                ]
                inventory_highlights.extend(notable)

        return GameStateSummary(
            campaign_name=campaign.name,
            current_location=current_location,
            location_description=location_description,
            player_characters=char_summaries,
            nearby_npcs=nearby_npcs,
            active_quests=active_quests,
            combat=combat_summary,
            inventory_highlights=inventory_highlights,
        )

    def render(self, summary: GameStateSummary, max_tokens: int = 450) -> str:
        """Render the GameStateSummary into a text block for the prompt."""
        lines: list[str] = ["[GAME STATE]"]
        lines.append(f"Campaign: {summary.campaign_name}")

        if summary.current_location:
            loc_line = f"Location: {summary.current_location}"
            if summary.location_description:
                loc_line += f" — {summary.location_description}"
            lines.append(loc_line)

        if summary.player_characters:
            chars = ", ".join(
                f"{c.name} ({c.race} {c.char_class} L{c.level}, "
                f"HP {c.hp}/{c.max_hp}, AC {c.ac})"
                for c in summary.player_characters
            )
            lines.append(f"Players: {chars}")

        if summary.nearby_npcs:
            lines.append(f"Nearby NPCs: {', '.join(summary.nearby_npcs)}")

        if summary.active_quests:
            lines.append(f"Active Quests: {', '.join(summary.active_quests)}")

        if summary.combat and summary.combat.is_active:
            c = summary.combat
            combatant_strs = ", ".join(
                f"{ch.name} (HP {ch.hp}/{ch.max_hp})"
                for ch in c.combatants
            )
            lines.append(
                f"Combat: Round {c.round_number}, {c.current_turn}'s turn. "
                f"Combatants: {combatant_strs}"
            )

        if summary.inventory_highlights:
            lines.append(f"Notable Items: {', '.join(summary.inventory_highlights)}")

        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_memory_state.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add memory/state.py tests/test_memory_state.py tests/conftest.py
git commit -m "feat: add Layer 1 structured state builder"
```

---

### Task 5: Layer 2 — Sliding Window (`memory/sliding_window.py`)

**Files:**
- Create: `memory/sliding_window.py`
- Test: `tests/test_sliding_window.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sliding_window.py
"""Tests for memory/sliding_window.py — Layer 2 sliding window."""

from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from memory.models import ExchangeRole, NarrativeExchange
from memory.sliding_window import SlidingWindow
from memory.token_utils import estimate_tokens
from world.campaign import Campaign


class TestSlidingWindow:
    """SlidingWindow tests."""

    def test_add_and_get_window(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session, window_size=5)
        for i in range(1, 4):
            sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, f"Msg {i}", i)
        db_session.commit()

        window = sw.get_window(sample_campaign.id)
        assert len(window) == 3
        assert window[0].interaction_number == 1

    def test_window_caps_at_size(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session, window_size=5)
        for i in range(1, 11):
            sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, f"Msg {i}", i)
        db_session.commit()

        window = sw.get_window(sample_campaign.id)
        assert len(window) == 5
        assert window[0].interaction_number == 6
        assert window[-1].interaction_number == 10

    def test_add_returns_exchange(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session)
        result = sw.add_exchange(sample_campaign.id, ExchangeRole.NARRATOR, "A tale begins.", 1)

        assert isinstance(result, NarrativeExchange)
        assert result.role == ExchangeRole.NARRATOR
        assert result.content == "A tale begins."

    def test_render_output_format(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session)
        sw.add_exchange(sample_campaign.id, ExchangeRole.PLAYER, "I enter the tavern.", 1)
        sw.add_exchange(sample_campaign.id, ExchangeRole.NARRATOR, "The door creaks open.", 2)
        sw.add_exchange(sample_campaign.id, ExchangeRole.SYSTEM, "Perception check: 15.", 3)
        db_session.commit()

        window = sw.get_window(sample_campaign.id)
        text = sw.render(window)

        assert "[RECENT NARRATIVE]" in text
        assert "Player: I enter the tavern." in text
        assert "Narrator: The door creaks open." in text
        assert "System: Perception check: 15." in text

    def test_render_within_budget(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session)
        for i in range(1, 13):
            sw.add_exchange(
                sample_campaign.id, ExchangeRole.NARRATOR,
                f"This is a longer narration for exchange number {i} with extra words.", i,
            )
        db_session.commit()

        window = sw.get_window(sample_campaign.id)
        text = sw.render(window, max_tokens=50)
        assert estimate_tokens(text) <= 50

    def test_render_empty(self, db_session: Session, sample_campaign: Campaign) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        sw = SlidingWindow(db_session)
        text = sw.render([])
        assert text == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_sliding_window.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement memory/sliding_window.py**

```python
# memory/sliding_window.py
"""Layer 2 — Sliding window of recent narrative exchanges.

Maintains the last N exchanges for short-term narrative continuity.
Persists exchanges to SQLite via ExchangeRepository.
"""

from sqlalchemy.orm import Session

from db.repositories.exchange_repo import ExchangeRepository
from memory.models import ExchangeRole, NarrativeExchange
from memory.token_utils import truncate_to_tokens

# Capitalize role names for display
_ROLE_DISPLAY = {
    ExchangeRole.PLAYER: "Player",
    ExchangeRole.NARRATOR: "Narrator",
    ExchangeRole.SYSTEM: "System",
}


class SlidingWindow:
    """Manages the last N narrative exchanges (Layer 2)."""

    def __init__(self, session: Session, window_size: int = 12) -> None:
        self._repo = ExchangeRepository(session)
        self._window_size = window_size

    def add_exchange(
        self,
        campaign_id: str,
        role: ExchangeRole,
        content: str,
        interaction_number: int,
    ) -> NarrativeExchange:
        """Record a new exchange. Returns the created exchange."""
        exchange = NarrativeExchange(
            campaign_id=campaign_id,
            role=role,
            content=content,
            interaction_number=interaction_number,
        )
        self._repo.save(exchange)
        return exchange

    def get_window(self, campaign_id: str) -> list[NarrativeExchange]:
        """Get the current sliding window (last N exchanges in ASC order)."""
        return self._repo.get_recent(campaign_id, limit=self._window_size)

    def render(
        self, exchanges: list[NarrativeExchange], max_tokens: int = 700
    ) -> str:
        """Render exchanges into a text block for the prompt."""
        if not exchanges:
            return ""

        lines = ["[RECENT NARRATIVE]"]
        for ex in exchanges:
            role_name = _ROLE_DISPLAY.get(ex.role, ex.role.value.capitalize())
            lines.append(f"{role_name}: {ex.content}")

        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_sliding_window.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/sliding_window.py tests/test_sliding_window.py
git commit -m "feat: add Layer 2 sliding window for narrative exchanges"
```

---

### Task 6: Layer 4 — Semantic Memory (`memory/semantic.py`)

**Files:**
- Create: `memory/semantic.py`
- Test: `tests/test_semantic.py`

Note: Layer 4 is implemented before Layer 3 because the summarizer depends on Ollama (more complex) while semantic memory is self-contained with ChromaDB.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_semantic.py
"""Tests for memory/semantic.py — Layer 4 semantic RAG."""

import chromadb
import pytest

from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory
from memory.token_utils import estimate_tokens


@pytest.fixture()
def ephemeral_chromadb() -> chromadb.ClientAPI:
    """In-memory ChromaDB client for tests."""
    return chromadb.EphemeralClient()


@pytest.fixture()
def semantic_memory(ephemeral_chromadb: chromadb.ClientAPI) -> SemanticMemory:
    """SemanticMemory with ephemeral ChromaDB."""
    return SemanticMemory(client=ephemeral_chromadb)


class TestSemanticMemory:
    """SemanticMemory tests."""

    def test_add_and_query(self, semantic_memory: SemanticMemory) -> None:
        doc = SemanticDocument(
            campaign_id="c1",
            doc_type=SemanticDocumentType.NPC_SHEET,
            content="Gundren Rockseeker is a dwarf prospector who discovered Wave Echo Cave.",
            metadata={"npc_name": "Gundren"},
        )
        semantic_memory.add_document(doc)

        results = semantic_memory.query("c1", "Who is Gundren?", n_results=1)
        assert len(results) == 1
        assert "Gundren" in results[0].content

    def test_query_returns_relevant_results(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
                content="Neverwinter is a bustling port city on the Sword Coast.",
            ),
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.NPC_SHEET,
                content="The Black Spider is a drow mage seeking Wave Echo Cave.",
            ),
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.LOCATION_DETAIL,
                content="Cragmaw Hideout is a goblin cave near the Triboar Trail.",
            ),
        ]
        semantic_memory.add_documents(docs)

        results = semantic_memory.query("c1", "Tell me about the goblins", n_results=1)
        assert len(results) == 1
        assert "goblin" in results[0].content.lower()

    def test_query_with_doc_type_filter(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.NPC_SHEET,
                content="Gundren is a dwarf.",
            ),
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
                content="Dwarves are a sturdy folk.",
            ),
        ]
        semantic_memory.add_documents(docs)

        results = semantic_memory.query(
            "c1", "dwarf", n_results=5,
            doc_type=SemanticDocumentType.NPC_SHEET,
        )
        assert all(r.doc_type == SemanticDocumentType.NPC_SHEET for r in results)

    def test_query_empty_collection(self, semantic_memory: SemanticMemory) -> None:
        results = semantic_memory.query("nonexistent", "anything")
        assert results == []

    def test_campaign_scoping(self, semantic_memory: SemanticMemory) -> None:
        semantic_memory.add_document(SemanticDocument(
            campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
            content="Lore for campaign 1.",
        ))
        semantic_memory.add_document(SemanticDocument(
            campaign_id="c2", doc_type=SemanticDocumentType.WORLD_LORE,
            content="Lore for campaign 2.",
        ))

        results_c1 = semantic_memory.query("c1", "lore", n_results=5)
        results_c2 = semantic_memory.query("c2", "lore", n_results=5)

        assert len(results_c1) == 1
        assert "campaign 1" in results_c1[0].content
        assert len(results_c2) == 1
        assert "campaign 2" in results_c2[0].content

    def test_delete_campaign(self, semantic_memory: SemanticMemory) -> None:
        semantic_memory.add_document(SemanticDocument(
            campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
            content="Some lore.",
        ))

        semantic_memory.delete_campaign("c1")

        results = semantic_memory.query("c1", "lore")
        assert results == []

    def test_render(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.NPC_SHEET,
                content="Gundren is a dwarf prospector.",
            ),
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
                content="Neverwinter is a port city.",
            ),
        ]
        text = semantic_memory.render(docs)

        assert "[RELEVANT LORE]" in text
        assert "Gundren is a dwarf prospector." in text
        assert "Neverwinter is a port city." in text

    def test_render_within_budget(self, semantic_memory: SemanticMemory) -> None:
        docs = [
            SemanticDocument(
                campaign_id="c1", doc_type=SemanticDocumentType.WORLD_LORE,
                content="A very long piece of world lore that goes on and on " * 20,
            ),
        ]
        text = semantic_memory.render(docs, max_tokens=30)
        assert estimate_tokens(text) <= 30

    def test_render_empty(self, semantic_memory: SemanticMemory) -> None:
        assert semantic_memory.render([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement memory/semantic.py**

```python
# memory/semantic.py
"""Layer 4 — Semantic RAG using ChromaDB.

Stores world lore, NPC sheets, past events for retrieval by
semantic similarity. One ChromaDB collection per campaign.
Uses default all-MiniLM-L6-v2 embedding model.
"""

import logging

import chromadb

from memory.models import SemanticDocument, SemanticDocumentType
from memory.token_utils import truncate_to_tokens

logger = logging.getLogger(__name__)


class SemanticMemory:
    """Semantic RAG memory using ChromaDB (Layer 4)."""

    def __init__(
        self,
        persist_directory: str = "data/chromadb",
        client: chromadb.ClientAPI | None = None,
    ) -> None:
        self._client = client or chromadb.PersistentClient(path=persist_directory)

    def _get_collection(self, campaign_id: str) -> chromadb.Collection:
        """Get or create the collection for a campaign."""
        return self._client.get_or_create_collection(
            name=f"campaign_{campaign_id}",
            metadata={"hnsw:space": "cosine"},
        )

    def add_document(self, document: SemanticDocument) -> None:
        """Add a single document to the campaign's collection."""
        collection = self._get_collection(document.campaign_id)
        collection.add(
            ids=[document.id],
            documents=[document.content],
            metadatas=[{"doc_type": document.doc_type.value, **document.metadata}],
        )

    def add_documents(self, documents: list[SemanticDocument]) -> None:
        """Batch add documents. All must belong to the same campaign."""
        if not documents:
            return
        campaign_id = documents[0].campaign_id
        collection = self._get_collection(campaign_id)
        collection.add(
            ids=[d.id for d in documents],
            documents=[d.content for d in documents],
            metadatas=[
                {"doc_type": d.doc_type.value, **d.metadata}
                for d in documents
            ],
        )

    def query(
        self,
        campaign_id: str,
        query_text: str,
        n_results: int = 3,
        doc_type: SemanticDocumentType | None = None,
    ) -> list[SemanticDocument]:
        """Query by semantic similarity. Optionally filter by doc_type."""
        try:
            collection = self._client.get_collection(f"campaign_{campaign_id}")
        except Exception:
            return []

        where_filter = {"doc_type": doc_type.value} if doc_type else None

        # Cap n_results to collection size
        count = collection.count()
        if count == 0:
            return []
        actual_n = min(n_results, count)

        results = collection.query(
            query_texts=[query_text],
            n_results=actual_n,
            where=where_filter,
        )

        documents: list[SemanticDocument] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                meta = results["metadatas"][0][i] if results["metadatas"] else {}  # type: ignore[index]
                doc_type_val = meta.pop("doc_type", "world_lore")
                documents.append(SemanticDocument(
                    id=doc_id,
                    campaign_id=campaign_id,
                    doc_type=SemanticDocumentType(doc_type_val),
                    content=results["documents"][0][i],  # type: ignore[index]
                    metadata=dict(meta),
                ))

        return documents

    def render(
        self, documents: list[SemanticDocument], max_tokens: int = 350
    ) -> str:
        """Render retrieved documents into a text block for the prompt."""
        if not documents:
            return ""

        lines = ["[RELEVANT LORE]"]
        for doc in documents:
            lines.append(f"- {doc.content}")

        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)

    def delete_campaign(self, campaign_id: str) -> None:
        """Delete the entire collection for a campaign."""
        try:
            self._client.delete_collection(f"campaign_{campaign_id}")
        except Exception:
            logger.debug("Collection campaign_%s not found for deletion", campaign_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/semantic.py tests/test_semantic.py
git commit -m "feat: add Layer 4 semantic RAG memory with ChromaDB"
```

---

### Task 7: Layer 3 — Summarizer with Ollama (`memory/summarizer.py`)

**Files:**
- Create: `memory/summarizer.py`
- Test: `tests/test_summarizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_summarizer.py
"""Tests for memory/summarizer.py — Layer 3 compressed summaries.

Ollama is mocked via unittest.mock.patch on the OpenAI client.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, ExchangeRole, NarrativeExchange
from memory.summarizer import Summarizer
from memory.token_utils import estimate_tokens
from world.campaign import Campaign


def _make_mock_openai_response(summary_text: str) -> MagicMock:
    """Create a mock OpenAI chat completion response."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({"summary": summary_text})
    return mock_response


def _seed_exchanges(
    db_session: Session, campaign_id: str, count: int
) -> None:
    """Insert N exchanges into the DB."""
    repo = ExchangeRepository(db_session)
    for i in range(1, count + 1):
        repo.save(NarrativeExchange(
            campaign_id=campaign_id,
            role=ExchangeRole.PLAYER if i % 2 else ExchangeRole.NARRATOR,
            content=f"Exchange content number {i}",
            interaction_number=i,
        ))
    db_session.commit()


class TestSummarizer:
    """Summarizer tests."""

    def test_should_summarize_false_when_few_exchanges(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 10)

        summarizer = Summarizer(db_session)
        assert summarizer.should_summarize(sample_campaign.id) is False

    def test_should_summarize_true_when_enough_exchanges(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 25)

        summarizer = Summarizer(db_session)
        assert summarizer.should_summarize(sample_campaign.id) is True

    def test_should_summarize_accounts_for_existing_summaries(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 30)

        # Add a summary covering interactions 1-20
        SummaryRepository(db_session).save(CompressedSummary(
            campaign_id=sample_campaign.id,
            summary_text="Previous summary",
            start_interaction=1,
            end_interaction=20,
        ))
        db_session.commit()

        summarizer = Summarizer(db_session)
        # Only 10 unsummarized (21-30), not enough
        assert summarizer.should_summarize(sample_campaign.id) is False

    @patch("memory.summarizer.OpenAI")
    def test_summarize_calls_ollama(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 25)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = _make_mock_openai_response(
            "The party explored the dungeon and defeated goblins."
        )

        summarizer = Summarizer(db_session)
        result = summarizer.summarize(sample_campaign.id)

        assert result is not None
        assert "goblins" in result.summary_text
        assert result.start_interaction == 1
        assert result.end_interaction == 25

        # Verify Ollama was called with JSON mode
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["model"] == "qwen3.5:9b"

    @patch("memory.summarizer.OpenAI")
    def test_summarize_returns_none_when_not_enough(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 5)

        summarizer = Summarizer(db_session)
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    @patch("memory.summarizer.OpenAI")
    def test_summarize_graceful_on_invalid_json(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 25)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json"
        mock_client.chat.completions.create.return_value = mock_response

        summarizer = Summarizer(db_session)
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    @patch("memory.summarizer.OpenAI")
    def test_summarize_graceful_on_connection_error(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        _seed_exchanges(db_session, sample_campaign.id, 25)

        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = ConnectionError("Ollama down")

        summarizer = Summarizer(db_session)
        result = summarizer.summarize(sample_campaign.id)
        assert result is None

    def test_get_recent_summaries(
        self, db_session: Session, sample_campaign: Campaign,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        summary_repo = SummaryRepository(db_session)
        for i in range(5):
            summary_repo.save(CompressedSummary(
                campaign_id=sample_campaign.id,
                summary_text=f"Summary {i + 1}",
                start_interaction=i * 20 + 1,
                end_interaction=(i + 1) * 20,
            ))
        db_session.commit()

        summarizer = Summarizer(db_session)
        results = summarizer.get_recent_summaries(sample_campaign.id, limit=3)
        assert len(results) == 3
        assert results[0].start_interaction == 41

    def test_render(self, db_session: Session) -> None:
        summaries = [
            CompressedSummary(
                campaign_id="c1", summary_text="The party arrived at Neverwinter.",
                start_interaction=1, end_interaction=20,
            ),
            CompressedSummary(
                campaign_id="c1", summary_text="They defeated the goblins.",
                start_interaction=21, end_interaction=40,
            ),
        ]
        summarizer = Summarizer(db_session)
        text = summarizer.render(summaries)

        assert "[SESSION HISTORY]" in text
        assert "[Interactions 1-20]" in text
        assert "arrived at Neverwinter" in text
        assert "[Interactions 21-40]" in text

    def test_render_within_budget(self, db_session: Session) -> None:
        summaries = [
            CompressedSummary(
                campaign_id="c1",
                summary_text="A very long summary text that goes on " * 20,
                start_interaction=1, end_interaction=20,
            ),
        ]
        summarizer = Summarizer(db_session)
        text = summarizer.render(summaries, max_tokens=30)
        assert estimate_tokens(text) <= 30

    def test_render_empty(self, db_session: Session) -> None:
        summarizer = Summarizer(db_session)
        assert summarizer.render([]) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_summarizer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement memory/summarizer.py**

```python
# memory/summarizer.py
"""Layer 3 — Compressed summaries via Ollama LLM.

Auto-generates summaries every ~20 interactions using the
OpenAI-compatible API at localhost:11434/v1 (qwen3.5:9b).
Uses JSON mode (response_format), NOT tool calling.
"""

import json
import logging

from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.summary_repo import SummaryRepository
from memory.models import CompressedSummary, NarrativeExchange
from memory.token_utils import truncate_to_tokens

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a concise summarizer for a D&D 5e game session. \
You will receive a sequence of narrative exchanges between players and a narrator. \
Produce a JSON object with a single "summary" field containing a 2-4 sentence \
summary of the key events, decisions, combat outcomes, and discoveries. \
Focus on facts that matter for story continuity. \
Do NOT include mechanical details like exact dice rolls.

Respond ONLY with valid JSON in this format:
{"summary": "your summary text here"}"""


class _SummaryResponse(BaseModel):
    """Expected JSON structure from the LLM."""

    summary: str


class Summarizer:
    """Generates compressed summaries using Ollama (Layer 3)."""

    SUMMARY_INTERVAL: int = 20

    def __init__(
        self,
        session: Session,
        ollama_base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3.5:9b",
    ) -> None:
        self._summary_repo = SummaryRepository(session)
        self._exchange_repo = ExchangeRepository(session)
        self._session = session
        self._client = OpenAI(base_url=ollama_base_url, api_key="ollama")
        self._model = model

    def should_summarize(self, campaign_id: str) -> bool:
        """Check if enough unsummarized exchanges have accumulated."""
        latest = self._summary_repo.get_latest(campaign_id)
        last_summarized = latest.end_interaction if latest else 0
        count = self._exchange_repo.count_unsummarized(campaign_id, last_summarized)
        return count >= self.SUMMARY_INTERVAL

    def summarize(self, campaign_id: str) -> CompressedSummary | None:
        """Generate a summary of unsummarized exchanges via Ollama.

        Returns None if not enough exchanges or if LLM call fails.
        """
        latest = self._summary_repo.get_latest(campaign_id)
        last_summarized = latest.end_interaction if latest else 0

        exchanges = self._exchange_repo.get_unsummarized(campaign_id, last_summarized)
        if len(exchanges) < self.SUMMARY_INTERVAL:
            return None

        # Build exchange text
        exchanges_text = self._format_exchanges(exchanges)

        # Call Ollama
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Summarize these exchanges:\n\n{exchanges_text}"},
                ],
                response_format={"type": "json_object"},
                temperature=0.3,
            )
        except Exception:
            logger.warning("Ollama call failed for summarization", exc_info=True)
            return None

        # Parse response
        raw_content = response.choices[0].message.content
        if not raw_content:
            return None

        try:
            parsed = json.loads(raw_content)
            summary_response = _SummaryResponse.model_validate(parsed)
        except (json.JSONDecodeError, Exception):
            logger.warning("Invalid JSON from summarizer: %s", raw_content[:200])
            return None

        # Create and persist summary
        summary = CompressedSummary(
            campaign_id=campaign_id,
            summary_text=summary_response.summary,
            start_interaction=exchanges[0].interaction_number,
            end_interaction=exchanges[-1].interaction_number,
        )
        self._summary_repo.save(summary)
        return summary

    def get_recent_summaries(
        self, campaign_id: str, limit: int = 4
    ) -> list[CompressedSummary]:
        """Get the N most recent summaries for context injection."""
        return self._summary_repo.get_recent(campaign_id, limit=limit)

    def render(
        self, summaries: list[CompressedSummary], max_tokens: int = 400
    ) -> str:
        """Render summaries into a text block for the prompt."""
        if not summaries:
            return ""

        lines = ["[SESSION HISTORY]"]
        for s in summaries:
            lines.append(
                f"[Interactions {s.start_interaction}-{s.end_interaction}] "
                f"{s.summary_text}"
            )

        text = "\n".join(lines)
        return truncate_to_tokens(text, max_tokens)

    def _format_exchanges(self, exchanges: list[NarrativeExchange]) -> str:
        """Format exchanges for the LLM prompt."""
        lines: list[str] = []
        for ex in exchanges:
            role = ex.role.value.capitalize()
            lines.append(f"{role}: {ex.content}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_summarizer.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/summarizer.py tests/test_summarizer.py
git commit -m "feat: add Layer 3 summarizer with Ollama integration"
```

---

### Task 8: Context Assembler (`memory/context_assembler.py`)

**Files:**
- Create: `memory/context_assembler.py`
- Test: `tests/test_context_assembler.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_context_assembler.py
"""Tests for memory/context_assembler.py — full assembly integration.

Uses in-memory SQLite, mocked OpenAI client, and ChromaDB EphemeralClient.
"""

import json
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from sqlalchemy.orm import Session

from db.repositories.campaign_repo import CampaignRepository
from db.repositories.exchange_repo import ExchangeRepository
from db.repositories.location_repo import LocationRepository
from db.repositories.npc_repo import NPCRepository
from db.repositories.quest_repo import QuestRepository
from memory.context_assembler import ContextAssembler
from memory.models import (
    ContextBudget,
    ExchangeRole,
    NarrativeExchange,
    SemanticDocument,
    SemanticDocumentType,
)
from memory.semantic import SemanticMemory
from memory.token_utils import estimate_tokens
from world.campaign import Campaign
from world.location import Location
from world.npc import NPC
from world.quest import Quest


@pytest.fixture()
def semantic_memory() -> SemanticMemory:
    """SemanticMemory backed by EphemeralClient."""
    return SemanticMemory(client=chromadb.EphemeralClient())


@pytest.fixture()
def campaign_with_data(
    db_session: Session, sample_campaign: Campaign,
    sample_location: Location, sample_npc: NPC, sample_quest: Quest,
) -> Campaign:
    """Set up a campaign with location, NPC, and quest in DB."""
    campaign = sample_campaign.model_copy(update={"current_location": "Neverwinter"})
    CampaignRepository(db_session).save(campaign)
    LocationRepository(db_session).save(sample_location, campaign.id)
    NPCRepository(db_session).save(sample_npc, campaign.id)
    QuestRepository(db_session).save(sample_quest, campaign.id)
    db_session.commit()
    return campaign


class TestContextAssembler:
    """ContextAssembler integration tests."""

    @patch("memory.summarizer.OpenAI")
    def test_assemble_produces_all_sections(
        self, mock_openai_cls: MagicMock,
        db_session: Session, campaign_with_data: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        campaign = campaign_with_data

        # Add some exchanges
        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 4):
            exchange_repo.save(NarrativeExchange(
                campaign_id=campaign.id, role=ExchangeRole.PLAYER,
                content=f"Player action {i}", interaction_number=i,
            ))
        db_session.commit()

        # Add semantic lore
        semantic_memory.add_document(SemanticDocument(
            campaign_id=campaign.id,
            doc_type=SemanticDocumentType.WORLD_LORE,
            content="The Sword Coast is a dangerous region.",
        ))

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(campaign.id, "I look around")

        assert "[GAME STATE]" in result
        assert "[RECENT NARRATIVE]" in result
        assert "[RELEVANT LORE]" in result
        assert campaign.name in result

    @patch("memory.summarizer.OpenAI")
    def test_record_exchange(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        exchange = assembler.record_exchange(
            sample_campaign.id, ExchangeRole.PLAYER, "Hello", 1,
        )
        db_session.commit()

        assert exchange.role == ExchangeRole.PLAYER
        assert exchange.content == "Hello"

        # Verify it appears in assembly
        result = assembler.assemble(sample_campaign.id, "test")
        assert "Hello" in result

    @patch("memory.summarizer.OpenAI")
    def test_auto_summarization_triggered(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {"summary": "The party explored the area."}
        )
        mock_client.chat.completions.create.return_value = mock_response

        # Insert 25 exchanges
        exchange_repo = ExchangeRepository(db_session)
        for i in range(1, 26):
            exchange_repo.save(NarrativeExchange(
                campaign_id=sample_campaign.id,
                role=ExchangeRole.PLAYER,
                content=f"Action {i}",
                interaction_number=i,
            ))
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(sample_campaign.id, "test")

        # Summarization should have been triggered
        assert "[SESSION HISTORY]" in result
        assert "explored" in result

    @patch("memory.summarizer.OpenAI")
    def test_respects_total_budget(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        budget = ContextBudget(
            layer1_max=100, layer2_max=100,
            layer3_max=100, layer4_max=100, total_max=300,
        )
        assembler = ContextAssembler(db_session, semantic_memory, budget=budget)
        result = assembler.assemble(sample_campaign.id, "test")

        assert estimate_tokens(result) <= 300

    @patch("memory.summarizer.OpenAI")
    def test_assemble_without_semantic_results(
        self, mock_openai_cls: MagicMock,
        db_session: Session, sample_campaign: Campaign,
        semantic_memory: SemanticMemory,
    ) -> None:
        CampaignRepository(db_session).save(sample_campaign)
        db_session.commit()

        assembler = ContextAssembler(db_session, semantic_memory)
        result = assembler.assemble(sample_campaign.id, "test")

        # Should still work with just game state
        assert "[GAME STATE]" in result
        assert "[RELEVANT LORE]" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_context_assembler.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement memory/context_assembler.py**

```python
# memory/context_assembler.py
"""Context Assembler — combines all 4 memory layers into a single prompt.

This is the single entry point called before each Narrator LLM call.
Orchestrates Layer 1 (structured state), Layer 2 (sliding window),
Layer 3 (compressed summaries), and Layer 4 (semantic RAG).
"""

from sqlalchemy.orm import Session

from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from memory.models import ContextBudget, ExchangeRole, NarrativeExchange
from memory.semantic import SemanticMemory
from memory.sliding_window import SlidingWindow
from memory.state import StateBuilder
from memory.summarizer import Summarizer
from memory.token_utils import estimate_tokens, truncate_to_tokens


class ContextAssembler:
    """Combines all 4 memory layers into a single prompt string."""

    def __init__(
        self,
        session: Session,
        semantic_memory: SemanticMemory,
        budget: ContextBudget | None = None,
        ollama_base_url: str = "http://localhost:11434/v1",
        summarizer_model: str = "qwen3.5:9b",
    ) -> None:
        self._state_builder = StateBuilder(session)
        self._sliding_window = SlidingWindow(session)
        self._summarizer = Summarizer(session, ollama_base_url, summarizer_model)
        self._semantic = semantic_memory
        self._budget = budget or ContextBudget()

    def assemble(
        self,
        campaign_id: str,
        player_input: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> str:
        """Build the full context prompt for the Narrator LLM.

        Triggers auto-summarization as a side effect when needed.
        Returns a single string prompt ready for the Narrator.
        """
        # 1. Check if summarization is needed (side effect)
        if self._summarizer.should_summarize(campaign_id):
            self._summarizer.summarize(campaign_id)

        # 2. Build each layer
        state_summary = self._state_builder.build(
            campaign_id, player_characters, combat_state, inventories,
        )
        layer1_text = self._state_builder.render(
            state_summary, self._budget.layer1_max,
        )

        window = self._sliding_window.get_window(campaign_id)
        layer2_text = self._sliding_window.render(
            window, self._budget.layer2_max,
        )

        summaries = self._summarizer.get_recent_summaries(campaign_id)
        layer3_text = self._summarizer.render(
            summaries, self._budget.layer3_max,
        )

        relevant_docs = self._semantic.query(campaign_id, player_input)
        layer4_text = self._semantic.render(
            relevant_docs, self._budget.layer4_max,
        )

        # 3. Assemble with priority-based truncation
        return self._assemble_prompt(
            layer1_text, layer2_text, layer3_text, layer4_text,
        )

    def record_exchange(
        self,
        campaign_id: str,
        role: ExchangeRole,
        content: str,
        interaction_number: int,
    ) -> NarrativeExchange:
        """Record a new exchange in the sliding window."""
        return self._sliding_window.add_exchange(
            campaign_id, role, content, interaction_number,
        )

    def _assemble_prompt(
        self, layer1: str, layer2: str, layer3: str, layer4: str,
    ) -> str:
        """Combine layers into a single prompt, respecting total budget.

        Priority order for truncation: Layer 4 > Layer 3 > Layer 2 > Layer 1.
        Layer 1 (game state) is never truncated.
        """
        sections = [
            s for s in [layer1, layer2, layer3, layer4] if s
        ]
        combined = "\n\n".join(sections)

        total = estimate_tokens(combined)
        if total <= self._budget.total_max:
            return combined

        # Truncate from lowest priority (layer4) up
        layers = [layer1, layer2, layer3, layer4]
        # Reverse priority: truncate layer4 first, then layer3, then layer2
        for i in [3, 2, 1]:
            if not layers[i]:
                continue
            excess = total - self._budget.total_max
            if excess <= 0:
                break
            layer_tokens = estimate_tokens(layers[i])
            new_budget = max(0, layer_tokens - excess)
            if new_budget == 0:
                total -= layer_tokens
                layers[i] = ""
            else:
                layers[i] = truncate_to_tokens(layers[i], new_budget)
                total = sum(estimate_tokens(l) for l in layers if l)

        sections = [s for s in layers if s]
        return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_context_assembler.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add memory/context_assembler.py tests/test_context_assembler.py
git commit -m "feat: add context assembler combining 4 memory layers"
```

---

### Task 9: Module Exports + Quality Gates

**Files:**
- Modify: `memory/__init__.py`

- [ ] **Step 1: Update memory/__init__.py**

```python
# memory/__init__.py
"""4-layer memory system for narrative context assembly."""

from memory.context_assembler import ContextAssembler
from memory.models import (
    CharacterSummary,
    CombatSummary,
    CompressedSummary,
    ContextBudget,
    ExchangeRole,
    GameStateSummary,
    NarrativeExchange,
    SemanticDocument,
    SemanticDocumentType,
)
from memory.semantic import SemanticMemory
from memory.sliding_window import SlidingWindow
from memory.state import StateBuilder
from memory.summarizer import Summarizer

__all__ = [
    "CharacterSummary",
    "CombatSummary",
    "CompressedSummary",
    "ContextAssembler",
    "ContextBudget",
    "ExchangeRole",
    "GameStateSummary",
    "NarrativeExchange",
    "SemanticDocument",
    "SemanticDocumentType",
    "SemanticMemory",
    "SlidingWindow",
    "StateBuilder",
    "Summarizer",
]
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass (517 existing + new memory tests)

- [ ] **Step 3: Run ruff check**

Run: `uv run ruff check .`
Expected: Clean (0 errors)

- [ ] **Step 4: Run mypy**

Run: `uv run mypy .`
Expected: Clean (0 errors). If ChromaDB typing issues arise, add `# type: ignore` on the specific lines.

- [ ] **Step 5: Commit**

```bash
git add memory/__init__.py
git commit -m "feat: add memory module exports"
```

---

## Verification

After all tasks are complete:

1. **Run full test suite**: `uv run pytest --tb=short -q` — all tests pass
2. **Run linting**: `uv run ruff check .` — clean
3. **Run type checking**: `uv run mypy .` — clean
4. **Check coverage**: `uv run pytest --cov=memory --cov-report=term-missing` — >90% on memory/
5. **Smoke test**: Write a quick script or test that creates a campaign, adds exchanges, triggers summarization (with mocked Ollama), queries ChromaDB, and assembles context — verify the output contains all 4 section headers
