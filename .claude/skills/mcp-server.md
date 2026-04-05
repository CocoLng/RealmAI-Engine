---
name: mcp-server
description: >
  Reference for building the RealmAI MCP server (mcp_server/ directory). Use this skill whenever
  working on MCP server setup, tool definitions, resource endpoints, prompt templates, or testing
  the MCP server locally. Trigger on: mcp_server/, MCP, Model Context Protocol, FastMCP, MCPServer,
  @mcp.tool, @mcp.resource, @mcp.prompt, Claude Desktop config, MCP Inspector, mcp_server/server.py,
  expose engine as MCP, or any MCP integration work.
---

# MCP Server Skill

## Core Principle

The MCP server is a thin wrapper over the deterministic engine. It exposes engine functions as
tools, game state as resources, and LLM interaction patterns as prompt templates. The MCP server
itself contains zero game logic — it delegates everything to `engine/`.

## Installation

```bash
uv add "mcp[cli]"
```

---

## Server Creation Pattern

Use `FastMCP` (imported from `mcp.server.fastmcp`) as the high-level entry point. It handles
protocol negotiation, JSON schema generation from type hints, and transport setup automatically.

### mcp_server/server.py

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "RealmAI-Engine",
    json_response=True,  # Tools return JSON-serializable dicts
)
```

### Entry Point (mcp_server/__main__.py)

```python
from mcp_server.server import mcp

def main() -> None:
    mcp.run(transport="stdio")  # stdio for Claude Desktop / CLI

if __name__ == "__main__":
    main()
```

Transport options: `"stdio"` (Claude Desktop/CLI), `"streamable-http"` (web clients),
`"sse"` (legacy Server-Sent Events).

---

## Tool Definitions

Tools are actions the LLM can invoke. Decorators on functions — the function name becomes
the tool ID, the docstring becomes the description, and type hints generate the JSON schema
automatically. No manual schema writing needed.

### Pattern

```python
from mcp_server.server import mcp
from engine.dice import roll
from engine.validators import ActionValidator
from engine.combat import resolve_attack

@mcp.tool()
def roll_dice(expression: str) -> dict:
    """Roll dice using standard notation (e.g. '2d6+3', '1d20').

    Returns the individual rolls, modifier, and total.
    """
    result = roll(expression)
    return result.model_dump()

@mcp.tool()
def validate_action(action_json: str, game_state_json: str) -> dict:
    """Check whether a player action is legal given the current game state.

    Returns {is_valid: bool, errors: [{code, message}]}.
    """
    action = Action.model_validate_json(action_json)
    state = GameState.model_validate_json(game_state_json)
    result = ActionValidator().validate(action, state)
    return result.model_dump()

@mcp.tool()
def resolve_combat_action(action_json: str, game_state_json: str) -> dict:
    """Validate and resolve a combat action. Returns the ActionResult with
    dice rolls, damage, state changes, and a summary string.
    """
    action = Action.model_validate_json(action_json)
    state = GameState.model_validate_json(game_state_json)
    # Validate first
    validation = ActionValidator().validate(action, state)
    if not validation.is_valid:
        return {"success": False, "errors": [e.model_dump() for e in validation.errors]}
    # Resolve mechanics
    result = resolve_attack(action, state)
    return result.model_dump()
```

### Tool Guidelines

- **Sync or async** — both work. Use async only if the underlying operation is truly async
  (e.g., database queries via SQLAlchemy async).
- **Return dicts** — with `json_response=True`, return Pydantic `.model_dump()` output.
- **Accept strings for complex types** — LLMs send JSON strings. Deserialize with
  `Model.model_validate_json(input)` inside the tool, not in the signature.
- **No side effects in validation tools** — validate tools check legality only.
  Separate tools for actions that mutate state.
- **Docstrings matter** — they're the LLM's only guide to what the tool does. Be specific
  about inputs, outputs, and constraints.

---

## Resource Definitions

Resources expose read-only game state. No side effects. The LLM reads these to understand
the current situation before deciding what tools to call.

### Static Resources (fixed URI)

```python
@mcp.resource("game://rules/ability-modifiers")
def get_ability_modifier_table() -> str:
    """Ability score to modifier mapping table."""
    return json.dumps({str(score): (score - 10) // 2 for score in range(1, 21)})

@mcp.resource("game://rules/conditions")
def get_conditions_list() -> str:
    """All available status conditions and their effects."""
    return json.dumps([c.value for c in Condition])

@mcp.resource("world://facts")
def get_locked_facts() -> str:
    """Immutable world facts the LLM cannot contradict."""
    # Load from DB or config
    return json.dumps(load_locked_facts())
```

### Dynamic Resources (URI templates)

Parameters in `{curly_braces}` are extracted from the URI and passed as function arguments.

```python
@mcp.resource("game://character/{character_id}")
def get_character(character_id: str) -> str:
    """Full character state: HP, AC, inventory, conditions, spell slots."""
    character = load_character(character_id)
    return character.model_dump_json()

@mcp.resource("game://character/{character_id}/inventory")
def get_inventory(character_id: str) -> str:
    """Character's current inventory with equipped items marked."""
    character = load_character(character_id)
    return json.dumps({"items": character.inventory, "equipped": character.equipped})

@mcp.resource("game://combat")
def get_combat_state() -> str:
    """Current combat state: initiative order, current turn, round number."""
    combat = load_combat_state()
    return combat.model_dump_json()

@mcp.resource("rules://spell/{spell_name}")
def get_spell(spell_name: str) -> str:
    """Spell mechanics: level, school, damage, range, components."""
    spell = lookup_spell(spell_name)
    return spell.model_dump_json()

@mcp.resource("world://npc/{npc_id}")
def get_npc(npc_id: str) -> str:
    """NPC state: disposition, secrets, personality, location."""
    npc = load_npc(npc_id)
    return npc.model_dump_json()
```

### Resource Guidelines

- **Return strings** — resources return `str` (JSON-encoded). Use `.model_dump_json()` for
  Pydantic models, `json.dumps()` for dicts/lists.
- **URI scheme convention** — use `game://` for live state, `rules://` for static rules,
  `world://` for world/lore data.
- **Keep it lean** — return only what the LLM needs for context. Don't dump entire databases.

---

## Prompt Template Definitions

Prompts are reusable interaction templates. They guide the LLM on how to use the server's
tools and resources for specific tasks. Return a string (single user message) or a list
of messages for multi-turn templates.

```python
@mcp.prompt()
def narrator_prompt(action_result_json: str, character_name: str) -> str:
    """Generate a narrative description of a resolved action."""
    return f"""You are a dark fantasy narrator. Describe this action result as immersive
second-person narrative (2-3 sentences). Be vivid and atmospheric.

Character: {character_name}
Action Result: {action_result_json}

Rules:
- Describe ONLY what the ActionResult says happened — invent nothing mechanical
- Use the dice rolls and damage numbers naturally in the prose
- Reflect conditions applied or removed"""

@mcp.prompt()
def interpreter_prompt(player_text: str) -> str:
    """Parse free-text player input into a structured action JSON."""
    return f"""Parse this player action into a JSON object with these fields:
- action_type: "attack" | "cast_spell" | "move" | "use_item"
- actor_id: string
- target_id: string or null
- Additional fields depending on action_type

Player said: "{player_text}"

Return valid JSON only. No commentary, no markdown fences."""

@mcp.prompt()
def story_director_check(recent_events_json: str, world_facts_json: str) -> str:
    """Periodic check for narrative contradictions and stale quests."""
    return f"""Review recent game events for:
1. Contradictions with locked world facts
2. Abandoned quest threads (no progress in 20+ interactions)
3. NPC behavior inconsistencies

Recent events: {recent_events_json}
World facts: {world_facts_json}

Return JSON: {{"contradictions": [...], "stale_quests": [...], "suggestions": [...]}}"""
```

### Prompt Guidelines

- **Enforce structured output** — always ask for JSON, never free text, when the output
  feeds back into the engine.
- **Include constraints** — remind the LLM what it must NOT do (e.g., invent mechanical
  outcomes).
- **Parameters are strings** — the LLM fills them in. Keep parameter names descriptive.

---

## Server Lifecycle

Use startup/shutdown hooks for database connections, loading configs, etc.

```python
@mcp.on_startup()
async def startup():
    """Initialize DB connection and load world state."""
    await init_database()
    load_world_config()

@mcp.on_shutdown()
async def shutdown():
    """Close DB connections."""
    await close_database()
```

---

## Context Injection

Tools can optionally receive a `Context` object for logging and progress reporting.
Import from `mcp.server.fastmcp`.

```python
from mcp.server.fastmcp import Context

@mcp.tool()
async def long_running_tool(data: str, context: Context) -> dict:
    """Tool that reports progress."""
    context.info(f"Processing: {data}")
    # ... work ...
    return {"result": "done"}
```

---

## File Structure

```
mcp_server/
├── __init__.py
├── __main__.py        # Entry point: mcp.run(transport="stdio")
├── server.py          # FastMCP instance + config
├── tools.py           # @mcp.tool() definitions (imports from engine/)
├── resources.py       # @mcp.resource() definitions (reads from DB)
└── prompts.py         # @mcp.prompt() templates
```

Keep tool/resource/prompt definitions in separate files for clarity. Import the shared
`mcp` instance from `server.py` in each module, and import the modules in `__init__.py`
so decorators register on import.

### Registration Pattern

```python
# mcp_server/__init__.py
from mcp_server.server import mcp  # noqa: F401 — must import first
import mcp_server.tools  # noqa: F401 — registers @mcp.tool decorators
import mcp_server.resources  # noqa: F401 — registers @mcp.resource decorators
import mcp_server.prompts  # noqa: F401 — registers @mcp.prompt decorators
```

---

## Testing MCP Servers Locally

### 1. MCP Inspector (interactive debugging)

Visual web UI for testing tools, resources, and prompts individually.

```bash
# Run the inspector against your server
npx @modelcontextprotocol/inspector uv --directory /path/to/RealmAI-Engine run python -m mcp_server
```

Opens at `http://localhost:6274`. Use the tabs to:
- **Tools tab** — execute tools with sample inputs, inspect JSON output
- **Resources tab** — browse and read resources by URI
- **Prompts tab** — render prompt templates with parameters

### 2. MCP dev mode

```bash
uv run mcp dev mcp_server/server.py
```

### 3. Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "realmai-engine": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/USERNAME/Documents/GitHub/RealmAI-Engine",
        "run",
        "python",
        "-m",
        "mcp_server"
      ]
    }
  }
}
```

After saving: **fully quit Claude Desktop** (Cmd+Q) and relaunch. Look for the MCP server
indicator (hammer icon) in the chat input area.

**Troubleshooting:**
- Check logs: `~/Library/Logs/Claude/mcp*.log`
- JSON syntax errors silently disable all servers
- Paths must be absolute
- Restart Claude Desktop completely for changes to take effect

### 4. Claude Code Configuration

Add to `.claude/settings.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "realmai-engine": {
      "command": "uv",
      "args": ["--directory", ".", "run", "python", "-m", "mcp_server"],
      "type": "stdio"
    }
  }
}
```

### 5. pytest for MCP tools

Since tools are thin wrappers over engine functions, test the engine functions directly
(already covered by `tests/test_*.py`). For integration tests of the MCP layer:

```python
import pytest
from mcp_server.server import mcp

@pytest.mark.anyio
async def test_roll_dice_tool():
    """Test the MCP tool returns valid DiceResult shape."""
    # Call the tool function directly (bypasses transport)
    result = roll_dice("2d6+3")
    assert "total" in result
    assert "rolls" in result
    assert len(result["rolls"]) == 2

@pytest.mark.anyio
async def test_character_resource():
    """Test character resource returns valid JSON."""
    result = get_character("test-fighter-id")
    data = json.loads(result)
    assert "hit_points" in data
    assert "armor_class" in data
```

---

## Anti-Patterns

- **No game logic in MCP layer** — tools call engine functions, they don't implement mechanics.
- **No LLM calls in tools** — tools are deterministic. The LLM is the caller, not the callee.
- **No raw dicts** — deserialize inputs to Pydantic models, serialize outputs with `.model_dump()`.
- **No manual JSON schemas** — let FastMCP generate them from type hints and docstrings.
