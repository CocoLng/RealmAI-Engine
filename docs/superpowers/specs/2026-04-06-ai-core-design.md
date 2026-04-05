# Phase 2c — AI Core Design

## Context

Phase 1 (deterministic engine, 593 tests) and Phase 2b (4-layer memory system) are complete.
Phase 2c implements the LLM layer: 6 modules that connect the engine to players via Ollama (Qwen 3.5).

## Pipeline

```
Player text → Interpreter (4B, JSON) → Validator (Python) → Engine (resolution)
            → ContextAssembler (4-layer memory) → Narrator (9B, JSON)
            → NarrativeResult → Discord embed
```

Background: Story Director (~20 interactions), NPC Agent (/talk), Quest/World generators.

## Critical Constraints

- LLM NEVER decides mechanics (dice, damage, loot)
- `response_format={"type": "json_object"}` everywhere — no tool calling (broken with Qwen 3.5)
- `ai/__init__.py` docstring-only (avoids circular imports, same rule as memory/)

## File Structure

```
ai/
├── __init__.py          # docstring-only
├── client.py            # OllamaClient wrapper
├── models.py            # AI-specific Pydantic models
├── interpreter.py       # text → InterpretedAction (Qwen 3.5 4B)
├── narrator.py          # ActionResult → NarrativeResult (Qwen 3.5 9B)
├── story_director.py    # periodic narrative coherence (Qwen 3.5 9B)
├── npc_agent.py         # NPC dialogue (Qwen 3.5 4B)
├── quest_generator.py   # dynamic Quest generation (Qwen 3.5 9B)
├── world_generator.py   # dynamic Location generation (Qwen 3.5 9B)
└── prompts/
    ├── system_narrator.txt
    ├── system_interpreter.txt
    ├── system_story_director.txt
    ├── system_npc_agent.txt
    ├── system_quest_generator.txt
    └── system_world_generator.txt
```

## AI Models (ai/models.py)

```python
class InterpretedAction(BaseModel):
    action_type: ActionType          # from engine/validators.py
    actor_name: str
    target_name: str | None = None
    weapon_name: str | None = None
    spell_name: str | None = None
    item_name: str | None = None
    raw_input: str                   # original player text (debug)
    confidence: float = 1.0          # 0.0 if parsing fails

class NarrativeResult(BaseModel):
    narrative: str
    tone: str  # "dramatic" | "tense" | "humorous" | "somber"

class DirectorNote(BaseModel):
    coherence_issues: list[str]
    suggested_hooks: list[str]
    priority: Literal["low", "medium", "high"]

class NPCResponse(BaseModel):
    dialogue: str
    disposition_change: int = 0  # -2 to +2; caller mutates NPC object
    revealed_info: list[str] = Field(default_factory=list)
```

## OllamaClient (ai/client.py)

Single shared wrapper injected into all modules. Raises `OllamaUnavailableError` on connection failure, propagates `json.JSONDecodeError` on invalid JSON.

## Testing Strategy

- `pytest-httpx` to mock Ollama HTTP calls (patches httpx globally)
- Tests in `tests/ai/` mirroring `ai/` structure
- ≥80% coverage on `ai/`
