# Director's Cut — Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Densify the semantic memory layer so the Narrator + Story Director can ground their output in canonical world content. Currently `memory/semantic.py` is queried by `context_assembler` but only `PAST_EVENT` documents are ever indexed (by the Story Director). The 4 other `SemanticDocumentType`s (`WORLD_LORE`, `NPC_SHEET`, `LOCATION_DETAIL`, `QUEST_DETAIL`) are defined but never populated.

**Architecture:** Add a single `SemanticIndexer` helper module with idempotent `index_X` methods. Wire it into the generators (`arc_generator`, `world_generator`, `npc_generator`) and into the beat-completion path (where `locked_facts` are revealed). Improve the retrieval query to use the rolling window of recent player actions, not just the current input.

**Tech Stack:** Pydantic v2, ChromaDB (already configured), pytest.

**Spec:** [`docs/superpowers/specs/2026-04-20-directors-cut-design.md`](../specs/2026-04-20-directors-cut-design.md) — Section 4 (RAG Densification).

**Builds on:** Phase A (`bot/pipeline/` package) + Phase B (`DirectorNote` direction fields, narrator meta).

---

## File Structure

### New files

| File | Responsibility | Approx. lines |
|------|----------------|---------------|
| `memory/indexer.py` | `SemanticIndexer` helper centralizing all `add_document` calls | ~150 |
| `tests/memory/test_indexer.py` | Unit tests for the indexer | ~120 |

### Modified files

| File | Change |
|------|--------|
| `ai/arc_generator.py` | After `arc = StoryArc.model_validate(data)`, index beats + NPCs + villain via `SemanticIndexer`. Optional `indexer` constructor param. |
| `ai/world_generator.py` | After `location = ...`, index location detail + lore via `SemanticIndexer`. Optional `indexer` constructor param. |
| `ai/npc_generator.py` | After `sheet = NPCSheet(...)`, index the sheet via `SemanticIndexer`. Optional `indexer` constructor param. |
| `bot/pipeline/orchestrator.py` (or `resolve.py`) | When a beat completes, index the `narrative_hint` + locked-fact additions as `PAST_EVENT`s via `SemanticIndexer`. |
| `memory/context_assembler.py` | Use the rolling window (last 2-3 actions) as the RAG query text instead of just the current input. Optionally bias retrieval by `doc_type`. |
| `tests/ai/test_arc_generator.py` | Verify the indexer is invoked on generation. |
| `tests/ai/test_world_generator.py` | Same. |
| `tests/memory/test_context_assembler.py` (if exists; otherwise create) | Verify rolling-window query construction. |

---

## Tasks Overview

| # | Task | Est. effort |
|---|------|-------------|
| C0 | Baseline verification | 5 min |
| C1 | `SemanticIndexer` module + tests | 1h |
| C2 | Wire indexer into `ArcGenerator` (beats + NPCs + villain) | 45 min |
| C3 | Wire indexer into `WorldGenerator` (locations + lore) | 30 min |
| C4 | Wire indexer into `NPCGenerator` (sheet) | 30 min |
| C5 | Wire indexer into beat-completion path (revealed facts) | 45 min |
| C6 | Improve `context_assembler` RAG query (rolling window) | 30 min |

Total: ~4 hours.

---

## Task C0: Baseline Verification

- [ ] **Step 1: Confirm clean tree on worktree**

```bash
git status
```

Expected: `On branch feat/directors-cut`, `nothing to commit, working tree clean`.

- [ ] **Step 2: Full test suite**

```bash
uv run pytest -q --tb=no 2>&1 | tail -3
```

Expected: 2122 passed (post-Phase B baseline).

- [ ] **Step 3: Lint**

```bash
uv run ruff check .
```

Expected: clean.

No commit — gate only.

---

## Task C1: `SemanticIndexer` Module

**Goal:** Single helper class with idempotent `index_X` methods. Centralizes content formatting + deterministic ID generation + metadata conventions. Idempotent IDs prevent duplicate documents on re-indexing.

**Files:**
- Create: `memory/indexer.py`
- Create: `tests/memory/test_indexer.py`
- Create: `tests/memory/__init__.py` (if not present — check first)

### Step 1: Failing tests

Create `tests/memory/__init__.py` (empty) if it does not exist.

Create `tests/memory/test_indexer.py`:

```python
"""Unit tests for SemanticIndexer."""

import pytest
from unittest.mock import MagicMock

from ai.models import NPCSheet
from memory.indexer import SemanticIndexer
from memory.models import SemanticDocument, SemanticDocumentType
from memory.semantic import SemanticMemory
from world.story_arc import StoryBeat


@pytest.fixture
def fake_semantic() -> MagicMock:
    return MagicMock(spec=SemanticMemory)


@pytest.fixture
def indexer(fake_semantic: MagicMock) -> SemanticIndexer:
    return SemanticIndexer(fake_semantic)


class TestIndexBeat:
    def test_index_beat_adds_past_event_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        beat = StoryBeat(
            beat_number=1,
            title="Le Mur qui Soupire",
            description="The party finds an ancient breathing wall.",
            location_hint="Old Ruins",
            npc_names=["Aldric"],
            encounter_type="puzzle",
        )
        indexer.index_beat("cmp_1", beat)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert isinstance(doc, SemanticDocument)
        assert doc.campaign_id == "cmp_1"
        assert doc.doc_type == SemanticDocumentType.PAST_EVENT
        assert "Le Mur qui Soupire" in doc.content
        assert "breathing wall" in doc.content

    def test_index_beat_id_is_idempotent(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        """Same campaign + beat_number → same document ID."""
        beat = StoryBeat(
            beat_number=3,
            title="Test Beat",
            description="A description.",
            location_hint="Somewhere",
            encounter_type="exploration",
        )
        indexer.index_beat("cmp_1", beat)
        first_id = fake_semantic.add_document.call_args.args[0].id
        fake_semantic.reset_mock()
        indexer.index_beat("cmp_1", beat)
        second_id = fake_semantic.add_document.call_args.args[0].id
        assert first_id == second_id


class TestIndexNPC:
    def test_index_npc_adds_npc_sheet_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        sheet = NPCSheet(
            personality="Stoic and watchful",
            description="An old elven mage with silver hair.",
            secrets=["Knows the location of the lost tome."],
            knowledge=["Has lived in this region for centuries."],
        )
        indexer.index_npc("cmp_1", "Aldric", sheet)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.NPC_SHEET
        assert "Aldric" in doc.content
        assert "Stoic" in doc.content
        assert doc.metadata.get("npc_name") == "Aldric"

    def test_index_npc_id_is_idempotent(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        sheet = NPCSheet(
            personality="P", description="D",
            secrets=["S"], knowledge=["K"],
        )
        indexer.index_npc("cmp_1", "Aldric", sheet)
        first_id = fake_semantic.add_document.call_args.args[0].id
        fake_semantic.reset_mock()
        indexer.index_npc("cmp_1", "Aldric", sheet)
        second_id = fake_semantic.add_document.call_args.args[0].id
        assert first_id == second_id


class TestIndexLocation:
    def test_index_location_adds_location_detail_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        from world.location import Location
        loc = Location(
            name="Goblin Cave",
            description="A dank cave with dripping water.",
        )
        indexer.index_location("cmp_1", loc)
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.LOCATION_DETAIL
        assert "Goblin Cave" in doc.content
        assert "dank" in doc.content


class TestIndexLore:
    def test_index_lore_adds_world_lore_document(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_lore(
            "cmp_1",
            content="The kingdom of Eldoria fell three centuries ago.",
            metadata={"topic": "history"},
        )
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.WORLD_LORE
        assert "Eldoria" in doc.content
        assert doc.metadata.get("topic") == "history"


class TestIndexRevealedFact:
    def test_index_revealed_fact_adds_past_event(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_revealed_fact(
            "cmp_1", fact="The wall breaks open, revealing a passage east.",
        )
        fake_semantic.add_document.assert_called_once()
        doc = fake_semantic.add_document.call_args.args[0]
        assert doc.doc_type == SemanticDocumentType.PAST_EVENT
        assert "wall breaks" in doc.content


class TestIndexerHandlesEmpty:
    def test_indexing_empty_lore_string_is_a_no_op(
        self, indexer: SemanticIndexer, fake_semantic: MagicMock,
    ) -> None:
        indexer.index_lore("cmp_1", content="", metadata={})
        fake_semantic.add_document.assert_not_called()
```

### Step 2: Verify failure

```bash
uv run pytest tests/memory/test_indexer.py -v
```

Expected: ModuleNotFoundError.

### Step 3: Implement `SemanticIndexer`

Create `memory/indexer.py`:

```python
"""SemanticIndexer — central helper for adding documents to ChromaDB.

Wraps :class:`memory.semantic.SemanticMemory` with one method per
``SemanticDocumentType`` and a deterministic ID strategy so re-indexing
the same source produces no duplicates.

ID format: ``"<doc_type>:<source_key>"`` — e.g. ``"npc_sheet:Aldric"``,
``"past_event:beat_3"``, ``"location_detail:Goblin_Cave"``. The source key
is sluggified (lowercase, spaces → underscores) so ChromaDB IDs stay
consistent across re-runs.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import TYPE_CHECKING

from memory.models import SemanticDocument, SemanticDocumentType

if TYPE_CHECKING:
    from ai.models import NPCSheet
    from memory.semantic import SemanticMemory
    from world.location import Location
    from world.story_arc import StoryBeat

logger = logging.getLogger(__name__)


def _slug(text: str) -> str:
    """Normalize a string into a stable slug for ChromaDB IDs."""
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_") or "_"


def _hash(text: str) -> str:
    """Short hash for content where slugging would collide (e.g. raw lore)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class SemanticIndexer:
    """Centralized add-document helper for the semantic memory layer.

    Each method is idempotent on its source key — calling ``index_beat``
    twice with the same beat does not create two documents in ChromaDB.
    """

    def __init__(self, semantic: "SemanticMemory") -> None:
        self._semantic = semantic

    def index_beat(self, campaign_id: str, beat: "StoryBeat") -> None:
        """Index a story beat as a PAST_EVENT-tagged document."""
        content = (
            f"Beat {beat.beat_number} — {beat.title}\n"
            f"{beat.description}\n"
            f"Location hint: {beat.location_hint}"
        )
        if beat.npc_names:
            content += f"\nNPCs involved: {', '.join(beat.npc_names)}"
        doc = SemanticDocument(
            id=f"past_event:beat_{campaign_id}_{beat.beat_number}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.PAST_EVENT,
            content=content,
            metadata={"beat_number": str(beat.beat_number), "title": beat.title},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX beat campaign=%s beat=%d", campaign_id, beat.beat_number)

    def index_npc(self, campaign_id: str, npc_name: str, sheet: "NPCSheet") -> None:
        """Index an NPC sheet as an NPC_SHEET-tagged document."""
        content_parts = [
            f"NPC: {npc_name}",
            f"Personality: {sheet.personality}",
            f"Description: {sheet.description}",
        ]
        if sheet.knowledge:
            content_parts.append("Knowledge: " + "; ".join(sheet.knowledge))
        if sheet.secrets:
            content_parts.append("Secrets: " + "; ".join(sheet.secrets))
        content = "\n".join(content_parts)
        doc = SemanticDocument(
            id=f"npc_sheet:{campaign_id}:{_slug(npc_name)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.NPC_SHEET,
            content=content,
            metadata={"npc_name": npc_name},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX npc campaign=%s name=%s", campaign_id, npc_name)

    def index_location(self, campaign_id: str, location: "Location") -> None:
        """Index a location as a LOCATION_DETAIL-tagged document."""
        content = f"Location: {location.name}\nDescription: {location.description or '(no description)'}"
        if location.npcs_present:
            content += f"\nNPCs present: {', '.join(location.npcs_present)}"
        if location.items_available:
            content += f"\nItems: {', '.join(location.items_available)}"
        doc = SemanticDocument(
            id=f"location_detail:{campaign_id}:{_slug(location.name)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.LOCATION_DETAIL,
            content=content,
            metadata={"location_name": location.name},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX location campaign=%s name=%s", campaign_id, location.name)

    def index_lore(self, campaign_id: str, *, content: str, metadata: dict[str, str]) -> None:
        """Index world lore as a WORLD_LORE-tagged document. No-op if content is empty."""
        if not content.strip():
            return
        doc = SemanticDocument(
            id=f"world_lore:{campaign_id}:{_hash(content)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.WORLD_LORE,
            content=content,
            metadata=dict(metadata),
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX lore campaign=%s len=%d", campaign_id, len(content))

    def index_revealed_fact(self, campaign_id: str, *, fact: str) -> None:
        """Index a newly-revealed fact as a PAST_EVENT-tagged document."""
        if not fact.strip():
            return
        doc = SemanticDocument(
            id=f"past_event:fact_{campaign_id}_{_hash(fact)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.PAST_EVENT,
            content=fact,
            metadata={"source": "beat_completion"},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX fact campaign=%s len=%d", campaign_id, len(fact))

    def index_quest(self, campaign_id: str, quest_id: str, description: str) -> None:
        """Index a quest description as a QUEST_DETAIL-tagged document."""
        if not description.strip():
            return
        doc = SemanticDocument(
            id=f"quest_detail:{campaign_id}:{_slug(quest_id)}",
            campaign_id=campaign_id,
            doc_type=SemanticDocumentType.QUEST_DETAIL,
            content=description,
            metadata={"quest_id": quest_id},
        )
        self._semantic.add_document(doc)
        logger.debug("INDEX quest campaign=%s quest=%s", campaign_id, quest_id)
```

### Step 4: Run tests

```bash
uv run pytest tests/memory/test_indexer.py -v
```

Expected: all pass.

### Step 5: Lint + full suite regression

```bash
uv run ruff check memory/indexer.py tests/memory/test_indexer.py
uv run pytest -q --tb=no 2>&1 | tail -3
```

### Step 6: Commit

```bash
git add memory/indexer.py tests/memory/__init__.py tests/memory/test_indexer.py
git commit -m "feat(memory): add SemanticIndexer — centralized doc indexing for RAG"
```

---

## Task C2: Wire `SemanticIndexer` into `ArcGenerator`

**Goal:** When `ArcGenerator.generate(...)` returns a `StoryArc`, index each beat as `PAST_EVENT`, each named NPC as `NPC_SHEET` (placeholder), the villain as `NPC_SHEET` (with role metadata), and the arc theme as `WORLD_LORE`.

**Files:**
- Modify: `ai/arc_generator.py`
- Modify: `tests/ai/test_arc_generator.py`

### Step 0: Read

```bash
grep -n "def __init__\|def generate\|return arc" ai/arc_generator.py | head
```

Confirm constructor signature (likely `def __init__(self, client: OllamaClient)`).

### Step 1: Failing test

Append to `tests/ai/test_arc_generator.py`:

```python
class TestArcGeneratorIndexing:
    def test_arc_generator_indexes_beats_and_villain_when_indexer_provided(
        self, monkeypatch, ollama_client, ...,
    ) -> None:
        from unittest.mock import MagicMock
        from memory.indexer import SemanticIndexer

        indexer = MagicMock(spec=SemanticIndexer)
        gen = ArcGenerator(ollama_client, indexer=indexer)
        # Mock the LLM call to return a minimal valid arc payload
        # ...  (use existing fixture fake_chat_json or monkeypatch)
        arc = gen.generate(campaign_id="cmp_1", ...)

        assert indexer.index_beat.call_count == len(arc.beats)
        assert indexer.index_npc.called  # at least the villain
```

(Adapt to real test fixtures — read existing tests in this file first.)

### Step 2: Verify failure

### Step 3: Update `ArcGenerator`

In `ai/arc_generator.py`:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from memory.indexer import SemanticIndexer


class ArcGenerator:
    def __init__(
        self,
        client: OllamaClient,
        indexer: "SemanticIndexer | None" = None,
    ) -> None:
        self._client = client
        self._indexer = indexer

    def generate(
        self, *, campaign_id: str, ..., # existing args
    ) -> StoryArc:
        # ... existing body ending with `arc = StoryArc.model_validate(data)` ...

        # NEW: index when indexer present
        if self._indexer is not None:
            for beat in arc.beats:
                self._indexer.index_beat(campaign_id, beat)
            if arc.villain_name and arc.villain_stat_block is not None:
                # Construct a synthetic NPCSheet for the villain from the stat block
                from ai.models import NPCSheet
                villain_sheet = NPCSheet(
                    personality=getattr(arc.villain_stat_block, "personality", "Antagonist"),
                    description=f"Villain: {arc.villain_name}. Archetype: {arc.villain_stat_block.archetype}.",
                    secrets=["[Stat block hidden — for the engine.]"],
                    knowledge=[f"Knows the campaign theme: {arc.theme}"],
                )
                self._indexer.index_npc(campaign_id, arc.villain_name, villain_sheet)
            self._indexer.index_lore(
                campaign_id,
                content=f"Campaign theme: {arc.theme}",
                metadata={"source": "arc_generator", "category": "theme"},
            )

        return arc
```

If `arc.villain_stat_block` does not have a `personality` field, use a sensible default string. Check the `NPCStatBlock` model first to see what fields exist.

### Step 4-6: Tests + lint + commit

```bash
uv run pytest tests/ai/test_arc_generator.py -v
uv run pytest -q --tb=no 2>&1 | tail -3
git add ai/arc_generator.py tests/ai/test_arc_generator.py
git commit -m "feat(arc-generator): index beats + villain + theme via SemanticIndexer"
```

---

## Task C3: Wire `SemanticIndexer` into `WorldGenerator`

**Goal:** Same pattern — when `WorldGenerator.generate(...)` returns a `Location`, index it as `LOCATION_DETAIL`. Index the world setting/lore as `WORLD_LORE`.

**Files:**
- Modify: `ai/world_generator.py`
- Modify: `tests/ai/test_world_generator.py`

### Step 1-6

Mirror the structure of C2:
1. Failing test asserting `indexer.index_location.called` and `indexer.index_lore.called` (if there's a setting/lore field on the world generator output).
2. Add `indexer` kwarg to `__init__`.
3. After `return location`, replace with:
   ```python
   if self._indexer is not None:
       self._indexer.index_location(campaign_id, location)
       # If the generator also produces a setting/lore string, index it too:
       # self._indexer.index_lore(campaign_id, content=setting, metadata={"source": "world_generator"})
   return location
   ```
4. Tests + lint + commit:
   ```bash
   git commit -m "feat(world-generator): index locations + lore via SemanticIndexer"
   ```

If the WorldGenerator returns multiple locations or a richer structure, index each location individually.

---

## Task C4: Wire `SemanticIndexer` into `NPCGenerator`

**Goal:** Index the generated `NPCSheet`.

**Files:**
- Modify: `ai/npc_generator.py`
- Modify: `tests/ai/test_npc_generator.py`

### Steps

1. Failing test asserting `indexer.index_npc.called` after `generate(...)`.
2. Add `indexer` kwarg to `__init__`.
3. After `sheet = NPCSheet(...)`, before `return sheet`:
   ```python
   if self._indexer is not None and npc_name:  # npc_name may need to be added as a parameter
       self._indexer.index_npc(campaign_id, npc_name, sheet)
   return sheet
   ```
4. **Verify** the `NPCGenerator.generate(...)` signature has access to a `campaign_id` and the NPC's name. If not, the indexer kwarg is a no-op for now and the wiring is done at the caller's side instead — note this in the commit.
5. Tests + lint + commit:
   ```bash
   git commit -m "feat(npc-generator): index NPCSheet via SemanticIndexer"
   ```

---

## Task C5: Wire `SemanticIndexer` into Beat-Completion Path

**Goal:** When a beat completes (deterministic trigger fires or LLM fallback succeeds), index the `narrative_hint` from `BeatEffects` as a `PAST_EVENT` (revealed fact). Also index any newly-revealed locked facts.

**Files:**
- Modify: `bot/pipeline/orchestrator.py` — the `_apply_beat_effects` method (or wherever `BeatEffects` are applied)
- Modify: `tests/bot/test_action_pipeline.py` — add a test verifying `index_revealed_fact` is called

### Step 0: Find the beat-completion path

```bash
grep -n "_apply_beat_effects\|narrative_hint\|BeatEffects" bot/pipeline/orchestrator.py bot/pipeline/resolve.py
```

### Step 1: Failing test

```python
class TestBeatCompletionIndexing:
    def test_beat_completion_indexes_narrative_hint(
        self, ..., monkeypatch,
    ) -> None:
        # Mock SemanticIndexer
        # Trigger a beat completion in the pipeline
        # Assert indexer.index_revealed_fact called with the narrative_hint
        ...
```

### Step 2-6

Decide where to inject the indexer. Options:
- **A) Add `indexer` field to `PipelineRunner`** (consistent with the `narrator`/`interpreter` injection pattern).
- **B) Use a module-level singleton** like `DriftTracker`.

**Choose A** — explicit injection is cleaner. Update `PipelineRunner`:

```python
    semantic_indexer: Any = None  # SemanticIndexer | None
```

In `_apply_beat_effects`, after applying mutations:

```python
        if self.semantic_indexer is not None and effects.narrative_hint:
            self.semantic_indexer.index_revealed_fact(
                self.campaign_id, fact=effects.narrative_hint,
            )
        if self.semantic_indexer is not None and effects.state_flags:
            for flag, value in effects.state_flags.items():
                if value:
                    self.semantic_indexer.index_revealed_fact(
                        self.campaign_id,
                        fact=f"State flag set: {flag}",
                    )
```

Update the Facade (`bot/action_pipeline.py`) `__init__` to accept an optional `semantic_indexer` and pass through to `PipelineRunner`.

Wire in any caller that constructs `ActionPipeline` to pass `semantic_indexer=session.semantic_indexer` (this requires `GameSession` to hold one — see C5b below).

**C5b: Add `semantic_indexer` to `GameSession`**

In `bot/game_session.py`:

```python
    semantic_indexer: SemanticIndexer | None = None
```

When the session is created (look at `bot/cogs/session.py:_resume` or wherever the session is built), instantiate alongside `semantic_memory`:

```python
    if session.semantic_memory is not None:
        from memory.indexer import SemanticIndexer
        session.semantic_indexer = SemanticIndexer(session.semantic_memory)
```

Then in `ActionHandlerCog._handle_action_message` (where `ActionPipeline` is constructed), pass it through:

```python
    pipeline = ActionPipeline(
        ...,
        semantic_indexer=session.semantic_indexer,
    )
```

Commit:
```bash
git commit -m "feat(pipeline): index revealed facts on beat completion"
```

---

## Task C6: Improve `context_assembler` RAG Query

**Goal:** Currently `context_assembler.assemble(...)` queries semantic memory with just `player_input`. Use the rolling window (last 2-3 narrative exchanges) as the query text — gives ChromaDB more signal.

**Files:**
- Modify: `memory/context_assembler.py`
- Modify: `tests/memory/test_context_assembler.py` (or create if missing)

### Step 1: Find the existing query call

In `memory/context_assembler.py:76`:

```python
        relevant_docs = self._semantic.query(campaign_id, player_input)
```

### Step 2: Failing test

Append to `tests/memory/test_context_assembler.py`:

```python
class TestRagQueryUsesRollingWindow:
    def test_query_combines_player_input_with_recent_window(
        self, ..., monkeypatch,
    ) -> None:
        # Set up a context_assembler with a sliding window containing
        # 3 recent NarrativeExchange entries.
        # Mock self._semantic.query to capture the query_text it receives.
        # Call context_assembler.assemble(...).
        # Assert the captured query_text contains snippets from the recent
        # window AND the current player_input.
        ...
```

(Adapt to real fixtures.)

### Step 3: Update `context_assembler.assemble`

Add a private helper:

```python
    def _build_rag_query(
        self,
        player_input: str,
        recent_exchanges: list[NarrativeExchange],
    ) -> str:
        """Combine the current input with the last 2-3 narrative excerpts."""
        snippets = [
            ex.content[:120] for ex in recent_exchanges[-3:]
        ]
        return "\n".join(snippets + [player_input])
```

In `assemble(...)`, replace:
```python
        relevant_docs = self._semantic.query(campaign_id, player_input)
```
with:
```python
        rag_query = self._build_rag_query(player_input, window)
        relevant_docs = self._semantic.query(campaign_id, rag_query)
```

(`window` is the result of `self._sliding_window.get_window(campaign_id)` from a few lines above.)

### Step 4-6: Tests + lint + commit

```bash
uv run pytest tests/memory/test_context_assembler.py -v
uv run pytest -q --tb=no 2>&1 | tail -3
git add memory/context_assembler.py tests/memory/test_context_assembler.py
git commit -m "feat(memory): use rolling-window RAG query for richer retrieval"
```

---

## Out of Scope (Phase C)

Deferred to Phase D:
- Arc Tracker pinned message + `Campaign.arc_tracker_message_id`

Future work (post-D):
- ChromaDB soft cap + eviction policy
- Filtered retrieval by `doc_type` (e.g. narrator prefers FACT + NPC docs)
