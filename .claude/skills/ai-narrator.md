---
name: ai-narrator
description: >
  Reference for building the AI narration layer (ai/ and memory/ directories). Use this skill whenever
  working on the Narrator, Interpreter, NPC agents, Story Director, quest/world generators, prompts,
  4-layer memory system, Context Assembler, ChromaDB/RAG integration, or Ollama/LLM configuration.
  Covers system prompt templates, JSON structured outputs, Pydantic response models, memory
  architecture, and model switching. Trigger on: ai/, memory/, narrator, interpreter, NPC dialogue,
  story director, quest generator, world generator, prompts/, system prompt, Ollama, LLM, qwen,
  response_format, JSON mode, structured output, RAG, ChromaDB, semantic search, sliding window,
  context assembler, memory layers, narration, or any AI/LLM integration work.
---

# AI Narrator Skill

## Core Principle

**The LLM narrates. The code arbitrates. No exceptions.**

Everything in `ai/` produces narrative text or parses player intent. It never decides dice rolls,
damage values, loot drops, or combat outcomes. The `engine/` owns all mechanics — the AI layer
receives `ActionResult` objects and describes what happened.

---

## Ollama API via OpenAI SDK

Ollama exposes an OpenAI-compatible API at `localhost:11434/v1`. Use the `openai` Python SDK
directly — no LangChain, no Ollama-specific client.

### Client Setup

```python
import os
from openai import OpenAI

def get_llm_client() -> OpenAI:
    """Create Ollama LLM client. All inference is local-only."""
    return OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
```

### Model Configuration

```python
from pydantic import BaseModel


class ModelConfig(BaseModel):
    """LLM model configuration for a specific role."""

    model: str
    temperature: float
    max_tokens: int
    json_mode: bool = False


# Models are NEVER loaded simultaneously in Ollama — ~10-12GB memory budget
NARRATOR_CONFIG = ModelConfig(
    model="qwen3.5:9b",       # 6.6GB, ~25-35 tok/s on M3 Pro
    temperature=0.8,           # Creative, varied prose
    max_tokens=512,            # Narrative paragraphs
)

INTERPRETER_CONFIG = ModelConfig(
    model="qwen3.5:4b",       # ~3GB, ~50-70 tok/s on M3 Pro
    temperature=0.1,           # Deterministic parsing
    max_tokens=256,            # Short JSON responses
    json_mode=True,
)

STORY_DIRECTOR_CONFIG = ModelConfig(
    model="qwen3.5:9b",       # Reuses narrator model
    temperature=0.5,           # Analytical, balanced
    max_tokens=1024,           # Longer analysis
    json_mode=True,
)

NPC_AGENT_CONFIG = ModelConfig(
    model="qwen3.5:9b",       # Personality-driven dialogue
    temperature=0.7,           # Creative but consistent
    max_tokens=384,            # Dialogue + internal state
    json_mode=True,
)
```

### Making LLM Calls

```python
from openai import OpenAI
from pydantic import BaseModel


def call_llm(
    client: OpenAI,
    config: ModelConfig,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> str:
    """Make an LLM call with the given config. Returns raw response text."""
    kwargs: dict = {
        "model": config.model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    }
    if config.json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = client.chat.completions.create(**kwargs)
    return response.choices[0].message.content
```

### JSON Mode — NOT Tool Calling

Ollama's native tool calling is broken with Qwen 3.5 (unclosed XML tags, wrong format pipeline).
Always use `response_format={"type": "json_object"}` for structured outputs.

When using JSON mode, the system prompt must explicitly describe the expected JSON schema.
The LLM responds with valid JSON which gets parsed into a Pydantic model.

```python
# CORRECT — JSON mode
response = client.chat.completions.create(
    model="qwen3.5:4b",
    messages=[...],
    response_format={"type": "json_object"},  # Forces valid JSON output
)
parsed = InterpreterResponse.model_validate_json(response.choices[0].message.content)

# WRONG — never use native tool calling with Ollama/Qwen
# response = client.chat.completions.create(
#     tools=[...],  # BROKEN — do not use
# )
```

---

## Pydantic Response Models

Every LLM output gets parsed into a typed Pydantic model. No raw dicts. No regex parsing.

### Interpreter Response

```python
from typing import Literal
from pydantic import BaseModel, Field


class InterpreterResponse(BaseModel):
    """Structured action parsed from player's free text."""

    action_type: Literal["attack", "cast_spell", "move", "use_item", "talk", "examine", "rest"]
    actor_id: str
    target_id: str | None = None
    weapon_id: str | None = None
    spell_name: str | None = None
    spell_level: int | None = Field(default=None, ge=0)
    item_id: str | None = None
    destination: str | None = None
    dialogue: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, description="How confident the parse is")
    clarification_needed: str | None = Field(
        default=None, description="Question to ask player if parse is ambiguous"
    )
```

### Narrator Response

The Narrator returns plain text (not JSON) — it's the only role that doesn't use JSON mode.
Its output goes directly into the Discord embed as narrative.

```python
# Narrator returns raw text, not structured JSON
narrative: str = call_llm(client, NARRATOR_CONFIG, system_prompt, messages)
```

### NPC Agent Response

```python
class NPCResponse(BaseModel):
    """NPC dialogue and internal state update."""

    dialogue: str = Field(description="What the NPC says out loud")
    inner_thought: str = Field(description="NPC's private reasoning — never shown to players")
    disposition_change: int = Field(
        ge=-20, le=20,
        description="How much this interaction shifts disposition toward the player",
    )
    reveals_secret: bool = Field(
        default=False, description="Whether the NPC reveals a secret in this response"
    )
    secret_id: str | None = Field(
        default=None, description="ID of the secret revealed, if any"
    )
```

### Story Director Response

```python
class StoryDirectorResponse(BaseModel):
    """Periodic coherence check and story management."""

    contradictions: list[str] = Field(
        default_factory=list, description="World facts contradicted in recent narrative"
    )
    stale_quests: list[str] = Field(
        default_factory=list, description="Quest IDs with no progress in 10+ interactions"
    )
    abandoned_threads: list[str] = Field(
        default_factory=list, description="Plot threads that were dropped"
    )
    suggested_hooks: list[str] = Field(
        default_factory=list, description="Story hooks to rekindle engagement"
    )
    tension_level: Literal["low", "medium", "high"] = Field(
        description="Current dramatic tension — use to pace encounters"
    )
    summary: str = Field(description="2-3 sentence session state summary for compressed memory")
```

### Parsing LLM Responses Safely

```python
from pydantic import ValidationError


def parse_llm_response(response_text: str, model_class: type[BaseModel]) -> BaseModel:
    """Parse LLM JSON response into a Pydantic model. Raises on invalid output."""
    try:
        return model_class.model_validate_json(response_text)
    except ValidationError as e:
        # Log the raw response for debugging, then re-raise
        # The caller decides whether to retry or return an error to the player
        raise ValueError(
            f"LLM returned invalid {model_class.__name__}: {e.error_count()} errors"
        ) from e
```

---

## System Prompt Templates

Each AI role has a system prompt that defines its personality, constraints, and output format.
The Context Assembler injects dynamic state into these templates at call time.

### Narrator System Prompt

```python
NARRATOR_SYSTEM_PROMPT = """\
You are the Game Master narrator for a tabletop RPG session. Your job is to describe what \
happens in vivid, immersive prose based on the mechanical results you receive.

## Your role
- Transform ActionResult data into engaging narrative
- Maintain consistent tone, atmosphere, and pacing
- Reference the environment, NPCs, and lore from the context provided
- Keep responses to 2-4 paragraphs — concise but evocative

## Rules (non-negotiable)
- NEVER decide dice results, damage values, or mechanical outcomes
- NEVER add or remove items from a character's inventory
- NEVER modify HP, AC, spell slots, or any stat
- NEVER spawn enemies, items, or events not in the game state
- You DESCRIBE what the engine already decided — nothing more

## What you receive
- ActionResult: what mechanically happened (hit/miss, damage, conditions, etc.)
- Character state: current HP, conditions, equipment
- Scene context: location description, NPCs present, recent events
- World facts: established truths you must not contradict

## Tone
- Fantasy prose, second person ("You swing your blade...")
- Adjust intensity to match the action (combat is visceral, exploration is atmospheric)
- Include sensory details: sounds, smells, light, weather
- Name NPCs and reference their established personalities\
"""
```

### Interpreter System Prompt

```python
INTERPRETER_SYSTEM_PROMPT = """\
You are an action parser for a tabletop RPG. Convert the player's free text into a \
structured JSON action.

## Your job
- Parse natural language into a game action
- Identify the action type, target, and any relevant parameters
- If the input is ambiguous, set confidence low and provide a clarification question

## Output format (JSON)
{{
    "action_type": "attack" | "cast_spell" | "move" | "use_item" | "talk" | "examine" | "rest",
    "actor_id": "<player character ID>",
    "target_id": "<target ID or null>",
    "weapon_id": "<weapon ID or null>",
    "spell_name": "<spell name or null>",
    "spell_level": <level int or null>,
    "item_id": "<item ID or null>",
    "destination": "<location ID or null>",
    "dialogue": "<what the player says in-character or null>",
    "confidence": <0.0 to 1.0>,
    "clarification_needed": "<question to ask if ambiguous, or null>"
}}

## Context provided
- Character sheet: name, class, inventory, known spells
- Current scene: location, NPCs present, combat state
- Recent exchanges: last few player/GM interactions

## Rules
- If the player says something clearly non-actionable ("lol", "brb"), return action_type "examine" \
with confidence 0.0 and a clarification question
- Match weapon/spell names to what's in the character's inventory/spell list
- Default to the character's equipped weapon for attack actions
- For ambiguous targets, pick the most contextually likely one but set confidence < 0.7\
"""
```

### NPC Agent System Prompt

```python
NPC_AGENT_SYSTEM_PROMPT_TEMPLATE = """\
You are {npc_name}, a {npc_role} in the world of {world_name}.

## Your personality
{personality_prompt}

## Your secrets
{secrets}

## Your disposition toward {player_name}: {disposition}/100
(-100 = hostile, 0 = neutral, 100 = devoted)

## Current situation
{scene_context}

## Rules
- Stay in character — respond as {npc_name} would, given your personality and disposition
- Your dialogue should reflect your disposition: hostile NPCs are curt or threatening, \
friendly ones are warm and helpful
- You may hint at secrets if disposition > 60, but never reveal them fully unless disposition > 80
- NEVER break character to discuss game mechanics
- NEVER decide combat outcomes, give items, or change game state

## Output format (JSON)
{{
    "dialogue": "<what you say out loud>",
    "inner_thought": "<your private reasoning>",
    "disposition_change": <-20 to +20>,
    "reveals_secret": <true/false>,
    "secret_id": "<secret ID if revealed, else null>"
}}\
"""
```

### Story Director System Prompt

```python
STORY_DIRECTOR_SYSTEM_PROMPT = """\
You are the Story Director — a behind-the-scenes agent that monitors narrative coherence \
and story pacing. You run periodically (every ~20 interactions) to ensure the game world \
stays consistent and engaging.

## Your job
- Detect contradictions between recent narrative and established world facts
- Identify quests that have gone stale (no progress in 10+ interactions)
- Flag plot threads that were introduced but never followed up
- Suggest story hooks to rekindle engagement when tension drops
- Produce a compressed summary of the current session state

## What you receive
- World facts: locked truths about the setting
- Active quests: objectives, progress, last interaction count
- NPC registry: status, disposition, last seen
- Recent narrative summaries: compressed history of the session
- Interaction count: how many player actions since session start

## Output format (JSON)
{{
    "contradictions": ["<fact X was contradicted when Y happened>", ...],
    "stale_quests": ["<quest_id>", ...],
    "abandoned_threads": ["<thread description>", ...],
    "suggested_hooks": ["<hook description>", ...],
    "tension_level": "low" | "medium" | "high",
    "summary": "<2-3 sentence session state for compressed memory>"
}}

## Guidance
- A healthy session alternates tension levels: high (combat/crisis) → low (rest/social) → build
- If tension has been "low" for 5+ interactions, suggest an escalation hook
- Contradictions are bugs — always flag them so the engine can correct the record
- Stale quests aren't always bad — players may return to them. Only flag after 10+ interactions\
"""
```

---

## 4-Layer Memory Architecture

The memory system keeps LLM context manageable (~1500-2500 tokens total) while preserving
continuity across long sessions.

### Layer 1 — Structured State (SQLite)

Source of truth. Character sheets, inventories, combat state, positions. Serialized into a
compact text summary for the LLM prompt.

```python
from pydantic import BaseModel, Field


class StructuredStateSummary(BaseModel):
    """Compact state snapshot for LLM context injection. ~300-500 tokens."""

    character_summary: str = Field(
        description="Name, class, level, HP/max, AC, key conditions"
    )
    inventory_summary: str = Field(
        description="Equipped weapon, armor, notable items (skip mundane)"
    )
    location: str = Field(description="Current location name and brief description")
    npcs_present: list[str] = Field(
        default_factory=list, description="Names of NPCs in the scene"
    )
    combat_state: str | None = Field(
        default=None, description="Initiative order and current turn, if in combat"
    )
    active_quests: list[str] = Field(
        default_factory=list, description="One-line summaries of active quests"
    )


def build_state_summary(game_state) -> StructuredStateSummary:
    """Build a compact state summary from the full game state.

    Keep this under ~500 tokens. Include only what the LLM needs to
    maintain coherent narration — skip internal IDs, raw stats tables,
    and anything the player wouldn't perceive in-world.
    """
    ...
```

### Layer 2 — Sliding Window

Last 10-12 narrative exchanges for immediate conversational continuity.

```python
class NarrativeExchange(BaseModel):
    """A single player-action + narrator-response pair."""

    interaction_id: int
    player_input: str
    action_summary: str  # One-line mechanical summary: "Attacked goblin, hit, 8 damage"
    narrative: str       # The narrator's response
    timestamp: str


class SlidingWindow:
    """Maintains the last N exchanges. Oldest are evicted to the summarizer."""

    def __init__(self, max_size: int = 12) -> None:
        self._exchanges: list[NarrativeExchange] = []
        self._max_size = max_size

    def add(self, exchange: NarrativeExchange) -> list[NarrativeExchange]:
        """Add an exchange. Returns evicted exchanges (for summarization)."""
        self._exchanges.append(exchange)
        evicted: list[NarrativeExchange] = []
        while len(self._exchanges) > self._max_size:
            evicted.append(self._exchanges.pop(0))
        return evicted

    def to_messages(self) -> list[dict[str, str]]:
        """Convert to OpenAI message format for the LLM prompt."""
        messages: list[dict[str, str]] = []
        for ex in self._exchanges:
            messages.append({"role": "user", "content": ex.player_input})
            messages.append({"role": "assistant", "content": ex.narrative})
        return messages

    def token_estimate(self) -> int:
        """Rough token count (~4 chars per token). Target: 500-800 tokens."""
        total_chars = sum(
            len(ex.player_input) + len(ex.narrative) for ex in self._exchanges
        )
        return total_chars // 4
```

### Layer 3 — Compressed Summaries

Auto-generated every ~20 interactions by the Story Director. Keeps a rolling history
without blowing up the context window.

```python
class CompressedSummary(BaseModel):
    """A compressed summary covering ~20 interactions."""

    summary_id: int
    interaction_range: tuple[int, int]  # (from_id, to_id)
    text: str = Field(description="2-3 sentence summary of events")
    key_decisions: list[str] = Field(
        default_factory=list, description="Player choices that affected the world"
    )
    timestamp: str


class SummaryStore:
    """Stores compressed summaries. Injects the last 3-4 into LLM context."""

    def __init__(self, max_injected: int = 4) -> None:
        self._summaries: list[CompressedSummary] = []
        self._max_injected = max_injected

    def add(self, summary: CompressedSummary) -> None:
        self._summaries.append(summary)

    def get_recent(self) -> list[CompressedSummary]:
        """Return the most recent summaries for context injection. ~300-500 tokens."""
        return self._summaries[-self._max_injected :]

    def to_context_string(self) -> str:
        """Format summaries for injection into the system prompt."""
        recent = self.get_recent()
        if not recent:
            return "No prior session history."
        parts = [f"[Interactions {s.interaction_range[0]}-{s.interaction_range[1]}] {s.text}"
                 for s in recent]
        return "\n".join(parts)
```

### Layer 4 — Semantic RAG (ChromaDB)

World lore, detailed NPC sheets, past events. Queried by semantic similarity only when
relevant — not injected on every call.

```python
import chromadb


class SemanticMemory:
    """ChromaDB-backed semantic search over world lore and NPC data."""

    def __init__(self, persist_dir: str = "./data/chromadb") -> None:
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._collection = self._client.get_or_create_collection(
            name="world_lore",
            metadata={"hnsw:space": "cosine"},
        )

    def add_document(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        """Add a lore document, NPC sheet, or event record."""
        self._collection.add(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata or {}],
        )

    def query(self, query_text: str, n_results: int = 3) -> list[str]:
        """Retrieve the most relevant documents for a given context.

        Call this with the player's action or current scene description.
        Returns ~200-400 tokens of relevant lore.
        """
        results = self._collection.query(
            query_texts=[query_text],
            n_results=n_results,
        )
        return results["documents"][0] if results["documents"] else []

    def to_context_string(self, query_text: str) -> str:
        """Format retrieved documents for injection into the prompt."""
        docs = self.query(query_text)
        if not docs:
            return ""
        return "## Relevant lore\n" + "\n---\n".join(docs)
```

---

## Context Assembler

The Context Assembler builds the final LLM prompt from all 4 memory layers. It enforces a
total budget of ~1500-2500 tokens and prioritizes layers by importance.

### Token Budget

| Layer | Budget | Priority | Content |
|-------|--------|----------|---------|
| System prompt | ~200-300 | Fixed | Role definition + rules |
| Layer 1: Structured state | ~300-500 | High | Character, location, combat |
| Layer 2: Sliding window | ~500-800 | High | Recent exchanges |
| Layer 3: Compressed summaries | ~300-500 | Medium | Session history |
| Layer 4: Semantic RAG | ~200-400 | Low | Lore (only when relevant) |
| **Total** | **~1500-2500** | | |

### Assembly Pattern

```python
class AssembledContext(BaseModel):
    """The fully assembled context ready for an LLM call."""

    system_prompt: str
    messages: list[dict[str, str]]
    estimated_tokens: int


class ContextAssembler:
    """Builds the final LLM prompt from all 4 memory layers.

    Priority-based truncation: if the total exceeds budget, trim lower-priority
    layers first (RAG → summaries → window). Structured state and system prompt
    are never truncated.
    """

    TOKEN_BUDGET = 2500

    def __init__(
        self,
        sliding_window: SlidingWindow,
        summary_store: SummaryStore,
        semantic_memory: SemanticMemory,
    ) -> None:
        self._window = sliding_window
        self._summaries = summary_store
        self._semantic = semantic_memory

    def assemble_narrator_context(
        self,
        state_summary: StructuredStateSummary,
        action_result,  # engine ActionResult
        player_input: str,
    ) -> AssembledContext:
        """Assemble context for a Narrator call."""
        # Layer 1: structured state (always included, high priority)
        state_block = self._format_state(state_summary)

        # Layer 3: compressed summaries (medium priority)
        history_block = self._summaries.to_context_string()

        # Layer 4: semantic RAG (low priority, query-dependent)
        lore_block = self._semantic.to_context_string(player_input)

        # Build system prompt with injected context
        system_prompt = NARRATOR_SYSTEM_PROMPT + "\n\n" + "\n\n".join(
            block for block in [
                f"## Current state\n{state_block}",
                f"## Session history\n{history_block}" if history_block else "",
                lore_block,
                f"## Action result\n{action_result.model_dump_json(indent=2)}",
            ] if block
        )

        # Layer 2: sliding window as message history
        messages = self._window.to_messages()
        messages.append({"role": "user", "content": player_input})

        # Estimate tokens and truncate if needed
        estimated = self._estimate_tokens(system_prompt, messages)
        if estimated > self.TOKEN_BUDGET:
            system_prompt, messages = self._truncate(
                system_prompt, messages, lore_block, history_block
            )
            estimated = self._estimate_tokens(system_prompt, messages)

        return AssembledContext(
            system_prompt=system_prompt,
            messages=messages,
            estimated_tokens=estimated,
        )

    def assemble_interpreter_context(
        self,
        state_summary: StructuredStateSummary,
        player_input: str,
    ) -> AssembledContext:
        """Assemble context for an Interpreter call. Lighter than Narrator."""
        state_block = self._format_state(state_summary)
        system_prompt = INTERPRETER_SYSTEM_PROMPT + f"\n\n## Current state\n{state_block}"

        # Interpreter only needs last 2-3 exchanges for context
        recent = self._window.to_messages()[-6:]  # 3 exchanges = 6 messages
        recent.append({"role": "user", "content": player_input})

        return AssembledContext(
            system_prompt=system_prompt,
            messages=recent,
            estimated_tokens=self._estimate_tokens(system_prompt, recent),
        )

    def _format_state(self, state: StructuredStateSummary) -> str:
        """Format structured state for prompt injection."""
        parts = [
            f"Character: {state.character_summary}",
            f"Equipment: {state.inventory_summary}",
            f"Location: {state.location}",
        ]
        if state.npcs_present:
            parts.append(f"NPCs present: {', '.join(state.npcs_present)}")
        if state.combat_state:
            parts.append(f"Combat: {state.combat_state}")
        if state.active_quests:
            parts.append("Active quests: " + "; ".join(state.active_quests))
        return "\n".join(parts)

    def _estimate_tokens(
        self, system_prompt: str, messages: list[dict[str, str]]
    ) -> int:
        """Rough token estimate: ~4 chars per token."""
        total_chars = len(system_prompt) + sum(len(m["content"]) for m in messages)
        return total_chars // 4

    def _truncate(
        self,
        system_prompt: str,
        messages: list[dict[str, str]],
        lore_block: str,
        history_block: str,
    ) -> tuple[str, list[dict[str, str]]]:
        """Truncate by priority: remove RAG first, then summaries, then old messages."""
        # Step 1: remove RAG lore
        if lore_block:
            system_prompt = system_prompt.replace(lore_block, "")
        if self._estimate_tokens(system_prompt, messages) <= self.TOKEN_BUDGET:
            return system_prompt, messages

        # Step 2: remove compressed summaries
        if history_block:
            system_prompt = system_prompt.replace(
                f"## Session history\n{history_block}", ""
            )
        if self._estimate_tokens(system_prompt, messages) <= self.TOKEN_BUDGET:
            return system_prompt, messages

        # Step 3: trim oldest messages from sliding window
        while (
            len(messages) > 2
            and self._estimate_tokens(system_prompt, messages) > self.TOKEN_BUDGET
        ):
            messages.pop(0)

        return system_prompt, messages
```

---

## File Structure

```
ai/
├── __init__.py
├── client.py           # get_llm_client(), call_llm(), ModelConfig
├── narrator.py          # Narrator class, NARRATOR_SYSTEM_PROMPT
├── interpreter.py       # Interpreter class, INTERPRETER_SYSTEM_PROMPT
├── npc_agent.py         # NPCAgent class, NPC_AGENT_SYSTEM_PROMPT_TEMPLATE
├── story_director.py    # StoryDirector class, STORY_DIRECTOR_SYSTEM_PROMPT
├── quest_generator.py   # Quest generation from Story Director hooks
├── world_generator.py   # World/location generation
├── responses.py         # All Pydantic response models
└── prompts/             # Long prompt templates (if they outgrow inline strings)
    └── __init__.py

memory/
├── __init__.py
├── state.py             # StructuredStateSummary, build_state_summary()
├── sliding_window.py    # SlidingWindow, NarrativeExchange
├── summarizer.py        # CompressedSummary, SummaryStore
├── semantic.py          # SemanticMemory (ChromaDB)
└── context_assembler.py # ContextAssembler, AssembledContext
```

---

## Testing AI Components

AI modules are harder to test than the deterministic engine, but mechanical behavior
(parsing, assembly, truncation) must still be tested.

### What to Test

| Component | Testable behavior | How |
|-----------|-------------------|-----|
| `ContextAssembler` | Token budgets, truncation priority, state formatting | Unit test with mock data |
| `SlidingWindow` | Eviction, message formatting, size limits | Unit test |
| `SummaryStore` | Recency selection, formatting | Unit test |
| `SemanticMemory` | Add/query round-trip, empty results | Integration test with temp ChromaDB |
| `InterpreterResponse` | Pydantic validation, edge cases | Unit test with sample JSON |
| `parse_llm_response` | Valid JSON, invalid JSON, missing fields | Unit test |
| `call_llm` | JSON mode flag, message assembly | Mock OpenAI client |

### Test Pattern for LLM Integration

```python
from unittest.mock import MagicMock, patch


def test_interpreter_parses_attack() -> None:
    """Interpreter correctly parses a simple attack action."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = '{"action_type":"attack","actor_id":"player1","target_id":"goblin1","weapon_id":"longsword","confidence":0.95,"clarification_needed":null,"spell_name":null,"spell_level":null,"item_id":null,"destination":null,"dialogue":null}'

    with patch.object(OpenAI, "chat") as mock_chat:
        mock_chat.completions.create.return_value = mock_response
        # ... call interpreter, assert result
```

---

## Key Constraints Checklist

When building or modifying AI components, verify:

- [ ] No mechanical decisions in AI code (dice, damage, loot, HP changes)
- [ ] All LLM structured outputs use `response_format={"type": "json_object"}`
- [ ] All LLM responses parsed into Pydantic models (no raw dicts)
- [ ] System prompts include anti-cheat rules (never decide mechanics)
- [ ] Context Assembler stays within ~1500-2500 token budget
- [ ] Models are never loaded simultaneously (Ollama memory constraint)
- [ ] All LLM inference is local-only via Ollama (no cloud fallback)
- [ ] ChromaDB queries are conditional (only when semantically relevant)
- [ ] Narrator output is plain text; all other roles output JSON
