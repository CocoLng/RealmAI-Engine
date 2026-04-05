# Memory System Design — Phase 2b

> Date: 2026-04-05
> Status: Approved
> Depends on: Phase 2a (World Models & DB Persistence)
> Unblocks: Phase 2c (AI Core)

---

## 1. Overview

The memory system feeds context to the Narrator LLM so it can produce coherent narratives across long game sessions. It assembles ~1500-2500 tokens from 4 layers into a single prompt string.

**Core constraint**: The LLM narrates, the code arbitrates. The memory system provides *read-only context* — it never modifies game state.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────┐
│                Context Assembler                │
│  Orchestrates 4 layers → single prompt string   │
│  Budget: ~1500-2500 tokens total                │
├────────────┬────────────┬───────────┬───────────┤
│  Layer 1   │  Layer 2   │  Layer 3  │  Layer 4  │
│  State     │  Sliding   │ Summaries │ Semantic  │
│  (SQLite)  │  Window    │ (Ollama)  │  (RAG)    │
│ 300-500tok │ 500-800tok │ 300-500tok│ 200-400tok│
├────────────┼────────────┼───────────┼───────────┤
│ Existing   │ exchanges  │ summaries │ ChromaDB  │
│ repos      │ table (new)│ table(new)│ collection│
└────────────┴────────────┴───────────┴───────────┘
```

### Data Flow (per Narrator call)

1. Caller records player exchange via `assembler.record_exchange()`
2. Caller calls `assembler.assemble(campaign_id, player_input, ...)`
3. Assembler checks if summarization is needed (>= 20 unsummarized exchanges)
4. If yes: Summarizer calls Ollama, persists `CompressedSummary`
5. Each layer builds its text within its token budget
6. Assembler combines all layers, respecting total budget
7. Returns a single prompt string for the Narrator

---

## 3. File Structure

```
memory/
├── __init__.py              # Public exports
├── models.py                # All Pydantic domain models
├── token_utils.py           # Token estimation + truncation
├── state.py                 # Layer 1 — Structured state builder
├── sliding_window.py        # Layer 2 — Sliding window manager
├── summarizer.py            # Layer 3 — Compressed summaries (Ollama)
├── semantic.py              # Layer 4 — Semantic RAG (ChromaDB)
└── context_assembler.py     # Combines all 4 layers

db/
├── models.py                # ADD: ExchangeRow, SummaryRow
├── mappers.py               # ADD: exchange/summary mappers
└── repositories/
    ├── __init__.py           # ADD exports
    ├── exchange_repo.py      # NEW
    └── summary_repo.py       # NEW

tests/
├── test_memory_models.py    # Pydantic model validation
├── test_memory_state.py     # Layer 1
├── test_sliding_window.py   # Layer 2
├── test_summarizer.py       # Layer 3 (mocked Ollama)
├── test_semantic.py         # Layer 4 (EphemeralClient)
├── test_context_assembler.py # Integration
└── test_memory_repos.py     # Exchange + Summary repos
```

---

## 4. Pydantic Models (`memory/models.py`)

### Layer 1 — Structured State

```python
class CharacterSummary(BaseModel):
    name: str
    race: str
    char_class: str
    level: int
    hp: int
    max_hp: int
    ac: int
    conditions: list[str] = []

class CombatSummary(BaseModel):
    is_active: bool = False
    round_number: int = 0
    current_turn: str | None = None
    combatants: list[CharacterSummary] = []

class GameStateSummary(BaseModel):
    campaign_name: str
    current_location: str | None = None
    location_description: str = ""
    player_characters: list[CharacterSummary] = []
    nearby_npcs: list[str] = []
    active_quests: list[str] = []
    combat: CombatSummary | None = None
    inventory_highlights: list[str] = []
```

### Layer 2 — Narrative Exchanges

```python
class ExchangeRole(StrEnum):
    PLAYER = "player"
    NARRATOR = "narrator"
    SYSTEM = "system"

class NarrativeExchange(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    role: ExchangeRole
    content: str
    interaction_number: int
    created_at: datetime = Field(default_factory=datetime.now)
```

### Layer 3 — Compressed Summaries

```python
class CompressedSummary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    summary_text: str
    start_interaction: int
    end_interaction: int
    created_at: datetime = Field(default_factory=datetime.now)
```

### Layer 4 — Semantic Documents

```python
class SemanticDocumentType(StrEnum):
    WORLD_LORE = "world_lore"
    NPC_SHEET = "npc_sheet"
    PAST_EVENT = "past_event"
    LOCATION_DETAIL = "location_detail"
    QUEST_DETAIL = "quest_detail"

class SemanticDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    campaign_id: str
    doc_type: SemanticDocumentType
    content: str
    metadata: dict[str, str] = {}
```

### Context Budget

```python
class ContextBudget(BaseModel):
    layer1_max: int = 450
    layer2_max: int = 700
    layer3_max: int = 400
    layer4_max: int = 350
    total_max: int = 2500
```

---

## 5. New DB Tables

### `exchanges` table

| Column             | Type     | Constraints                              |
|--------------------|----------|------------------------------------------|
| id                 | String   | PK                                       |
| campaign_id        | String   | FK → campaigns.id, CASCADE, NOT NULL     |
| role               | String   | NOT NULL ("player"/"narrator"/"system")   |
| content            | String   | NOT NULL                                 |
| interaction_number | Integer  | NOT NULL                                 |
| created_at         | DateTime | NOT NULL                                 |

### `summaries` table

| Column            | Type     | Constraints                              |
|-------------------|----------|------------------------------------------|
| id                | String   | PK                                       |
| campaign_id       | String   | FK → campaigns.id, CASCADE, NOT NULL     |
| summary_text      | String   | NOT NULL                                 |
| start_interaction | Integer  | NOT NULL                                 |
| end_interaction   | Integer  | NOT NULL                                 |
| created_at        | DateTime | NOT NULL                                 |

Both follow existing patterns: FK cascade delete, scoped by campaign_id.

---

## 6. Module APIs

### `memory/token_utils.py`

```python
def estimate_tokens(text: str) -> int:
    """Approximate token count: words * 1.3, rounded up."""

def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text at word boundaries to fit within token budget."""
```

### `memory/state.py` — Layer 1

```python
class StateBuilder:
    def __init__(self, session: Session) -> None: ...

    def build(
        self,
        campaign_id: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> GameStateSummary: ...

    def render(self, summary: GameStateSummary, max_tokens: int = 450) -> str: ...
```

Reads from existing repos (Campaign, NPC, Location, Quest). Accepts in-memory `Character`, `CombatState`, `Inventory` as parameters since those are not persisted.

Render output format:
```
[GAME STATE]
Campaign: Lost Mines of Phandelver
Location: Neverwinter — A bustling coastal city
Players: Thorin (Dwarf Fighter L5, HP 35/40, AC 16)
Nearby NPCs: Gundren Rockseeker (friendly)
Active Quests: Find the Lost Mine (active)
Combat: Round 3, Thorin's turn. Enemies: Goblin (HP 4/7)
```

### `memory/sliding_window.py` — Layer 2

```python
class SlidingWindow:
    def __init__(self, session: Session, window_size: int = 12) -> None: ...

    def add_exchange(
        self, campaign_id: str, role: ExchangeRole, content: str, interaction_number: int
    ) -> NarrativeExchange: ...

    def get_window(self, campaign_id: str) -> list[NarrativeExchange]: ...

    def render(self, exchanges: list[NarrativeExchange], max_tokens: int = 700) -> str: ...
```

Render output format:
```
[RECENT NARRATIVE]
Player: I approach the tavern door and knock.
Narrator: The heavy oak door creaks open...
System: Perception check: rolled 15 + 2 = 17 (success).
```

### `memory/summarizer.py` — Layer 3

```python
class Summarizer:
    SUMMARY_INTERVAL: int = 20

    def __init__(
        self,
        session: Session,
        ollama_base_url: str = "http://localhost:11434/v1",
        model: str = "qwen3.5:9b",
    ) -> None: ...

    def should_summarize(self, campaign_id: str) -> bool: ...

    def summarize(self, campaign_id: str) -> CompressedSummary | None: ...

    def get_recent_summaries(self, campaign_id: str, limit: int = 4) -> list[CompressedSummary]: ...

    def render(self, summaries: list[CompressedSummary], max_tokens: int = 400) -> str: ...
```

**Ollama integration**:
- Client: `OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")`
- Model: `qwen3.5:9b`
- `response_format={"type": "json_object"}` (NOT tool calling)
- `temperature=0.3`
- Expected JSON: `{"summary": "2-4 sentences..."}`
- Graceful degradation: if Ollama is unreachable or returns invalid JSON, return `None`

Render output format:
```
[SESSION HISTORY]
[Interactions 1-20] The party arrived at Neverwinter and met Gundren...
[Interactions 21-40] On the road, the party was ambushed by goblins...
```

### `memory/semantic.py` — Layer 4

```python
class SemanticMemory:
    def __init__(
        self,
        persist_directory: str = "data/chromadb",
        client: chromadb.ClientAPI | None = None,
    ) -> None: ...

    def add_document(self, document: SemanticDocument) -> None: ...
    def add_documents(self, documents: list[SemanticDocument]) -> None: ...

    def query(
        self, campaign_id: str, query_text: str,
        n_results: int = 3, doc_type: SemanticDocumentType | None = None,
    ) -> list[SemanticDocument]: ...

    def render(self, documents: list[SemanticDocument], max_tokens: int = 350) -> str: ...

    def delete_campaign(self, campaign_id: str) -> None: ...
```

**ChromaDB config**:
- Collection name: `campaign_{campaign_id}`
- Embedding: default all-MiniLM-L6-v2 (no configuration needed)
- Distance: cosine
- `client` parameter allows injecting `EphemeralClient` for tests

Render output format:
```
[RELEVANT LORE]
- Gundren Rockseeker is a dwarf prospector who discovered Wave Echo Cave...
- Neverwinter is a bustling port city on the Sword Coast...
```

### `memory/context_assembler.py` — Orchestrator

```python
class ContextAssembler:
    def __init__(
        self,
        session: Session,
        semantic_memory: SemanticMemory,
        budget: ContextBudget | None = None,
        ollama_base_url: str = "http://localhost:11434/v1",
        summarizer_model: str = "qwen3.5:9b",
    ) -> None: ...

    def assemble(
        self,
        campaign_id: str,
        player_input: str,
        player_characters: list[Character] | None = None,
        combat_state: CombatState | None = None,
        inventories: dict[str, Inventory] | None = None,
    ) -> str: ...

    def record_exchange(
        self, campaign_id: str, role: ExchangeRole, content: str, interaction_number: int,
    ) -> NarrativeExchange: ...
```

**Truncation priority** (when total exceeds budget): Layer 4 > Layer 3 > Layer 2 > Layer 1. Game state (Layer 1) is never truncated as it's the source of truth.

**Assembled output format**:
```
[GAME STATE]
...

[SESSION HISTORY]
...

[RECENT NARRATIVE]
...

[RELEVANT LORE]
...
```

---

## 7. Repository APIs

### `ExchangeRepository`

```python
class ExchangeRepository:
    def save(self, exchange: NarrativeExchange) -> None: ...
    def get_recent(self, campaign_id: str, limit: int = 12) -> list[NarrativeExchange]: ...
    def get_range(self, campaign_id: str, start: int, end: int) -> list[NarrativeExchange]: ...
    def get_unsummarized(self, campaign_id: str, last_summarized: int) -> list[NarrativeExchange]: ...
    def count_unsummarized(self, campaign_id: str, last_summarized: int) -> int: ...
    def delete_before(self, campaign_id: str, interaction_number: int) -> None: ...
```

### `SummaryRepository`

```python
class SummaryRepository:
    def save(self, summary: CompressedSummary) -> None: ...
    def get_recent(self, campaign_id: str, limit: int = 4) -> list[CompressedSummary]: ...
    def get_latest(self, campaign_id: str) -> CompressedSummary | None: ...
    def list_by_campaign(self, campaign_id: str) -> list[CompressedSummary]: ...
```

---

## 8. Test Strategy

| Test file | What | Mocks |
|-----------|------|-------|
| `test_memory_models.py` | Pydantic validation, serialization | None |
| `test_memory_state.py` | StateBuilder build + render | In-memory SQLite |
| `test_sliding_window.py` | Add, get_window, render, overflow | In-memory SQLite |
| `test_summarizer.py` | should_summarize, summarize, render | In-memory SQLite + mocked OpenAI client |
| `test_semantic.py` | add, query, filter, render, delete | ChromaDB EphemeralClient |
| `test_context_assembler.py` | Full assembly, record_exchange, auto-summarization | In-memory SQLite + mocked OpenAI + EphemeralClient |
| `test_memory_repos.py` | ExchangeRepo + SummaryRepo CRUD | In-memory SQLite |

**Key testing patterns**:
- Monkeypatch `engine.<module>.roll` for dice (existing pattern)
- `unittest.mock.patch` for the OpenAI client in summarizer tests
- ChromaDB `EphemeralClient` injected via constructor parameter
- Existing `conftest.py` fixtures extended with `sample_exchange`, `sample_summary`, `ephemeral_chromadb`

---

## 9. Implementation Order

1. `memory/token_utils.py` — no dependencies
2. `memory/models.py` — Pydantic models only
3. `db/models.py` additions — ExchangeRow, SummaryRow
4. `db/mappers.py` additions — exchange/summary mappers
5. `db/repositories/exchange_repo.py` + `summary_repo.py`
6. `tests/test_memory_repos.py` — validate DB layer
7. `memory/state.py` — Layer 1 (depends on existing repos)
8. `memory/sliding_window.py` — Layer 2 (depends on exchange repo)
9. `memory/semantic.py` — Layer 4 (depends on ChromaDB + models)
10. `memory/summarizer.py` — Layer 3 (depends on repos + Ollama)
11. `memory/context_assembler.py` — ties all layers together
12. `memory/__init__.py` — public exports
13. Tests for each layer (alongside each step)
14. Quality gates: pytest, ruff, mypy
