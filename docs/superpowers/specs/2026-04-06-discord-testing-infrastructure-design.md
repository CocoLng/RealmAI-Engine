# Discord Testing Infrastructure — Design Spec

**Date**: 2026-04-06
**Status**: Draft
**Goal**: Allow Claude Code to systematically test and interactively debug the RealmAI Discord bot through two complementary layers: automated scenario tests (pytest) and a live Discord MCP server.

---

## Context

The bot has 852 unit/integration tests covering individual cogs, engine modules, embeds, and views. All tests mock Discord interactions (`AsyncMock`) and test components in isolation. What's missing:

1. **Multi-step scenario tests** — no test chains commands into full gameplay sequences (create character → explore → combat → save → resume → verify state integrity)
2. **Live interaction capability** — Claude Code cannot interact with the running bot to explore edge cases, reproduce bugs, or verify the real Discord UX

This spec defines two layers that together give Claude Code full testing power over the bot.

---

## Layer 1: ScenarioRunner (pytest)

### Purpose

Automated, deterministic, multi-step integration tests that exercise the full pipeline (cog handlers → engine → DB → response capture) without a real Discord connection. Claude Code runs these via `uv run pytest tests/scenarios/`.

### File Structure

```
tests/scenarios/
├── conftest.py                    # ScenarioRunner fixture, helpers
├── scenario_runner.py             # ScenarioRunner class
├── test_campaign_lifecycle.py     # Create → play → save → resume → verify
├── test_combat_scenarios.py       # Full combat, death, flee, edge cases
├── test_multiplayer_scenarios.py  # Multiple players, alternating turns
├── test_persistence_integrity.py  # Save/resume preserves all state
└── test_edge_cases.py             # 0 HP, no mana, empty inventory, etc.
```

### ScenarioRunner Class

Wraps all cog handlers behind a clean API. Manages mock Discord context internally.

```python
class ScenarioRunner:
    """Orchestrates multi-step gameplay scenarios against real engine + DB."""

    # ── Internal state ──
    bot: RealmBot               # Real bot with in-memory SQLite
    session: GameSession        # Active game session
    channel: MagicMock          # Mock Discord channel
    players: list[MockMember]   # Virtual players (auto-created)
    responses: list[EmbedCapture]  # All captured bot responses

    # ── Campaign lifecycle ──
    async def start_campaign(theme: str, players: int) -> EmbedCapture
    async def save() -> EmbedCapture
    async def resume() -> EmbedCapture
    async def end_campaign() -> EmbedCapture

    # ── Character ──
    async def add_player(
        name: str, race: str, class_: str, player_idx: int = 0
    ) -> EmbedCapture

    # ── Exploration ──
    async def look(player_idx: int = 0) -> EmbedCapture
    async def move(direction: str, player_idx: int = 0) -> EmbedCapture
    async def search(target: str, player_idx: int = 0) -> EmbedCapture
    async def talk(npc: str, player_idx: int = 0) -> EmbedCapture

    # ── Combat ──
    async def start_combat(enemies: list[Combatant]) -> EmbedCapture
    async def attack(target: str, player_idx: int = 0) -> EmbedCapture
    async def cast_spell(
        spell: str, target: str, player_idx: int = 0
    ) -> EmbedCapture
    async def defend(player_idx: int = 0) -> EmbedCapture
    async def flee(player_idx: int = 0) -> EmbedCapture

    # ── Inventory ──
    async def equip(item: str, slot: str, player_idx: int = 0) -> EmbedCapture
    async def unequip(slot: str, player_idx: int = 0) -> EmbedCapture
    async def use_item(item: str, player_idx: int = 0) -> EmbedCapture

    # ── Rolls ──
    async def roll(expression: str, player_idx: int = 0) -> EmbedCapture

    # ── Assertions ──
    def get_character(player_idx: int) -> Character
    def get_inventory(player_idx: int) -> Inventory
    def assert_hp(player_idx: int, expected: int)
    def assert_in_combat()
    def assert_not_in_combat()
    def assert_has_item(player_idx: int, item_name: str)
    def assert_condition(player_idx: int, condition: str)

    @property
    def last_response(self) -> EmbedCapture
```

### EmbedCapture

Captures what the bot would send to Discord:

```python
@dataclass
class EmbedCapture:
    content: str | None            # Text message (if any)
    embed: discord.Embed | None    # The embed sent
    view: discord.ui.View | None   # Buttons/selects attached
    ephemeral: bool                # Whether it was ephemeral

    def has_field(self, name: str) -> bool
    def get_field_value(self, name: str) -> str | None
    def button_labels(self) -> list[str]
    def select_options(self) -> list[str]
```

### Mock Strategy

| Component | Strategy |
|-----------|----------|
| Discord interactions | `AsyncMock` with `TestInteraction` wrapper that captures embeds/views |
| Discord guild, channel, members | `MagicMock` with consistent IDs |
| Database | Real in-memory SQLite (reuses existing `db_engine` fixture) |
| Engine (dice, combat, etc.) | Real — no mocking of game mechanics |
| AI services | `None` — cogs degrade gracefully, test only mechanics |
| Channel creation/archival | `MagicMock` (no-op) |

### TestInteraction

A lightweight wrapper that looks like `discord.Interaction` to cog handlers but captures the response:

```python
class TestInteraction:
    """Fake interaction that captures responses instead of sending to Discord."""

    def __init__(self, bot, guild, channel, user, namespace=None):
        self.client = bot
        self.guild = guild
        self.channel = channel
        self.user = user
        self.namespace = namespace  # Slash command arguments
        self.response = TestResponse()
        self.followup = TestFollowup()
        self._capture = EmbedCapture(...)  # Stores the response

class TestResponse:
    async def send_message(self, content=None, embed=None, view=None, ephemeral=False):
        # Store in EmbedCapture instead of sending to Discord

    async def defer(self, ephemeral=False):
        pass  # No-op
```

### Scenario Example

```python
@pytest.mark.asyncio
async def test_save_resume_preserves_combat_state(scenario: ScenarioRunner):
    """Save mid-combat, resume, verify combat continues correctly."""
    await scenario.start_campaign(theme="donjon", players=2)
    await scenario.add_player("Guerrier", race="human", class_="fighter", player_idx=0)
    await scenario.add_player("Mage", race="elf", class_="wizard", player_idx=1)

    # Start combat
    await scenario.start_combat(enemies=[goblin_combatant()])
    scenario.assert_in_combat()
    hp_before = scenario.get_character(0).hp

    # Player 1 attacks
    await scenario.attack(target="Gobelin", player_idx=0)

    # Save mid-combat
    await scenario.save()

    # Simulate restart (clear in-memory state)
    scenario.clear_session()

    # Resume
    await scenario.resume()

    # Verify combat state preserved
    scenario.assert_in_combat()
    assert scenario.get_character(0).hp == hp_before
```

---

## Layer 2: MCP Discord Server

### Purpose

Interactive, live connection between Claude Code and the running Discord bot. Claude Code uses MCP tools to send commands, read responses, click buttons, and observe game state on a real test Discord server.

### Process Architecture

```
Claude Code
    │
    ├── MCP tools (stdio transport)
    │
    ▼
MCP Discord Server (Python process)
    │
    ├── discord.py client (Tester Bot)
    │
    ▼
Discord Test Server
    │
    ├── Tester Bot (reads/writes messages)
    ├── Game Bot (RealmBot + TestBridge cog)
    │
    ▼
Game Engine + DB (running locally)
```

### Components

#### 2.1 TestBridge Cog (added to game bot)

**File**: `bot/cogs/test_bridge.py`

A cog loaded **only** when `TEST_MODE=true` in the environment. Listens for `!test` messages from an authorized tester bot and translates them into cog handler calls.

```python
class TestBridge(commands.Cog):
    """Test bridge — translates !test commands into cog calls.

    Only active when TEST_MODE=true. Only accepts commands from
    the authorized tester bot (TESTER_BOT_ID in env).
    """

    TESTER_BOT_ID: int  # Loaded from env

    # Virtual players: player_idx → MockMember
    virtual_players: dict[int, MockMember]

    async def on_message(self, message):
        # Ignore if not from tester bot
        if message.author.id != self.TESTER_BOT_ID:
            return
        # Ignore if not a !test command
        if not message.content.startswith("!test "):
            return

        # Parse: !test [player=N] <command> <key=value ...>
        command, args, player_idx = self._parse(message.content)

        # Create TestInteraction for the virtual player
        interaction = TestInteraction(
            bot=self.bot,
            guild=message.guild,
            channel=message.channel,
            user=self.virtual_players[player_idx],
            namespace=args,
        )

        # Route to the appropriate cog handler
        await self._dispatch(command, interaction)
```

**Command routing**:

| `!test` command | Routes to |
|-----------------|-----------|
| `start_campaign theme=X players=N` | `SessionCog.start_campaign()` |
| `create_character name=X race=Y class_=Z` | Bypasses the multi-step view flow. Calls `engine.character.create_character()` directly with the given params, then registers in session. Sends confirmation embed. |
| `attack target=X` | `CombatCog._resolve_player_action()` with action=attack |
| `cast_spell spell=X target=Y` | `CombatCog._resolve_player_action()` with action=cast_spell |
| `defend` | `CombatCog._resolve_player_action()` with action=defend |
| `flee` | `CombatCog._resolve_player_action()` with action=flee |
| `look` | `ExplorationCog.look()` |
| `move direction=X` | `ExplorationCog.move()` |
| `search target=X` | `ExplorationCog.search()` |
| `talk npc=X` | `ExplorationCog.talk()` |
| `equip item=X slot=Y` | `InventoryCog.equip()` |
| `unequip slot=X` | `InventoryCog.unequip()` |
| `use_item item=X` | `InventoryCog.use_item()` |
| `roll expression=X` | `RollsCog.roll()` |
| `save` | `SessionCog.save()` |
| `resume` | `SessionCog.resume()` |
| `character` | `CharacterCog.character()` |
| `inventory` | `InventoryCog.inventory()` |
| `game_state` | Serializes the active GameSession to JSON and posts it to the channel |
| `click_button msg=X button=Y` | Finds the active view in `TestBridge.active_views[msg_id]` and triggers the matching button callback |
| `select_option msg=X value=Y` | Finds the active view in `TestBridge.active_views[msg_id]` and triggers the matching select callback |

**View tracking for button/select interactions**:

The TestBridge maintains `active_views: dict[int, discord.ui.View]` — a mapping from message ID to the View object that was sent with that message. When `ChannelTestInteraction.response.send_message()` sends a message with a `view`, it stores the view in `active_views[msg.id]`. When a `!test click_button` or `!test select_option` command arrives, the TestBridge looks up the view, finds the matching component, and calls its callback with a new `ChannelTestInteraction`.

**TestInteraction (channel-posting variant)**:

Unlike the ScenarioRunner's TestInteraction (which captures responses), this one **posts to the real Discord channel** so Claude Code can read the results:

```python
class ChannelTestInteraction:
    """Fake interaction that posts responses to the real channel."""

    async def response.send_message(self, content=None, embed=None, view=None, **kw):
        kwargs = {}
        if content: kwargs["content"] = content
        if embed: kwargs["embed"] = embed
        if view: kwargs["view"] = view
        await self.channel.send(**kwargs)

    async def response.defer(self, **kw):
        pass  # No-op
```

#### 2.2 Tester Bot (inside MCP server)

**File**: `mcp_discord/discord_client.py`

A minimal discord.py client that:
- Connects to the test server with its own token
- Sends `!test` messages to the game channel
- Listens for the game bot's responses
- Provides async methods for the MCP server to call

```python
class TesterBot(discord.Client):
    """Lightweight bot that acts as test player on Discord."""

    game_bot_id: int       # Game bot's Discord user ID
    test_channel_id: int   # The channel to test in

    async def send_test_command(self, command: str) -> discord.Message:
        """Send !test command and wait for game bot response."""
        channel = self.get_channel(self.test_channel_id)
        await channel.send(f"!test {command}")
        # Wait for response from game bot
        response = await self.wait_for(
            "message",
            check=lambda m: m.author.id == self.game_bot_id and m.channel.id == self.test_channel_id,
            timeout=15.0,
        )
        return response

    async def read_recent_messages(self, limit: int = 10) -> list[dict]:
        """Read recent messages with embed/component data."""
        channel = self.get_channel(self.test_channel_id)
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append(self._serialize_message(msg))
        return messages

    def _serialize_message(self, msg: discord.Message) -> dict:
        """Convert Discord message to serializable dict."""
        return {
            "id": msg.id,
            "author": msg.author.name,
            "content": msg.content,
            "embeds": [self._serialize_embed(e) for e in msg.embeds],
            "components": self._serialize_components(msg.components),
            "timestamp": msg.created_at.isoformat(),
        }
```

#### 2.3 MCP Server

**File**: `mcp_discord/server.py`

Uses the official MCP Python SDK (`mcp` package) with stdio transport.

**MCP Tools**:

```python
@mcp.tool()
async def discord_status() -> dict:
    """Check if game bot is online and responsive.

    Returns:
        online: bool — whether the game bot is connected
        latency_ms: float — bot latency
        test_channel: str — name of the test channel
    """

@mcp.tool()
async def discord_send_command(
    command: str,
    args: dict[str, str] | None = None,
    player: int = 1,
) -> dict:
    """Send a game command to the bot via TestBridge.

    Args:
        command: The command name (e.g. "attack", "look", "start_campaign")
        args: Command arguments as key-value pairs (e.g. {"target": "Gobelin"})
        player: Virtual player index (1-based, default 1)

    Returns:
        success: bool
        response: {embeds: [...], components: [...], content: str}
    """

@mcp.tool()
async def discord_read_messages(limit: int = 10) -> list[dict]:
    """Read recent messages from the test channel.

    Args:
        limit: Number of messages to read (max 50)

    Returns:
        List of messages with embeds, components, timestamps
    """

@mcp.tool()
async def discord_click_button(
    message_id: int,
    button_label: str,
    player: int = 1,
) -> dict:
    """Click a button on a game bot message.

    Sends !test click_button msg=<id> button=<label> player=<N>.
    The TestBridge finds the view and triggers the callback.

    Args:
        message_id: Discord message ID containing the button
        button_label: Label text of the button to click
        player: Virtual player index

    Returns:
        success: bool
        response: The bot's response after clicking
    """

@mcp.tool()
async def discord_select_option(
    message_id: int,
    value: str,
    player: int = 1,
) -> dict:
    """Select an option from a dropdown on a game bot message.

    Args:
        message_id: Discord message ID containing the select
        value: The value to select
        player: Virtual player index

    Returns:
        success: bool
        response: The bot's response after selecting
    """

@mcp.tool()
async def discord_wait_for_response(timeout: float = 10.0) -> dict:
    """Wait for the next message from the game bot.

    Args:
        timeout: Max seconds to wait

    Returns:
        The game bot's message (embeds, components, content)
    """

@mcp.tool()
async def discord_get_game_state() -> dict:
    """Get current game state by sending !test game_state.

    The TestBridge serializes the active GameSession and returns it.

    Returns:
        campaign: {name, theme}
        characters: [{name, race, class, hp, max_hp, ac, xp, level}]
        combat_active: bool
        combat_state: {turn, combatants} | null
        location: {name, description} | null
        npcs: [{name, disposition}]
        quests: [{title, status}]
    """
```

### File Structure

```
mcp_discord/
├── __init__.py
├── server.py              # MCP server entry point (stdio transport)
├── discord_client.py      # TesterBot (discord.py client)
├── tools.py               # MCP tool implementations
└── config.py              # Configuration (env vars, channel IDs)
```

### Configuration

**Environment variables** (in `.env`):

```env
# Game bot (existing)
DISCORD_BOT_TOKEN=...

# Testing infrastructure (new)
TEST_MODE=true
TESTER_BOT_TOKEN=...          # Separate Discord application
TESTER_BOT_ID=123456789       # Bot user ID (for TestBridge auth)
TEST_CHANNEL_ID=987654321     # Channel to run tests in
GAME_BOT_ID=111222333         # Game bot's user ID (for response filtering)
```

**Claude Code MCP config** (in `~/.claude/settings.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "discord-test": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_discord.server"],
      "cwd": "/path/to/RealmAI-Engine"
    }
  }
}
```

### Setup Steps

1. Create a new Discord application at https://discord.com/developers
2. Create a bot user → copy token → add to `.env` as `TESTER_BOT_TOKEN`
3. Invite both bots (game + tester) to the test server with appropriate permissions
4. Set `TEST_MODE=true` and `TESTER_BOT_ID` in `.env`
5. Start the game bot: `uv run python -m bot`
6. Add the MCP server config to Claude Code settings
7. Claude Code can now use `discord_*` tools

---

## Dependencies

### New packages

| Package | Purpose | Install |
|---------|---------|---------|
| `mcp` | MCP Python SDK | `uv add --dev mcp` |

No other new dependencies — discord.py and pytest are already installed.

### Existing packages reused

- `discord.py` — TesterBot client + TestBridge cog
- `pytest` + `pytest-asyncio` — ScenarioRunner tests
- `sqlalchemy` — Real in-memory DB for scenarios

---

## Testing Strategy

### ScenarioRunner tests

Run via `uv run pytest tests/scenarios/ -v`.

**Planned scenario suites**:

| Suite | Scenarios | Focus |
|-------|-----------|-------|
| `test_campaign_lifecycle` | 5-8 | Create → play → save → resume → end |
| `test_combat_scenarios` | 8-12 | Full combat, multi-target, death, flee, spells, XP |
| `test_multiplayer_scenarios` | 4-6 | 2+ players, turn alternation, shared combat |
| `test_persistence_integrity` | 5-8 | Save/resume preserves HP, inventory, combat, quests |
| `test_edge_cases` | 6-10 | 0 HP, no mana, empty inventory, invalid actions |

**Target**: ~30-45 scenario tests, all passing.

### MCP Server testing

The MCP server itself is tested via:
1. Unit tests for command parsing (`TestBridge._parse`)
2. Unit tests for message serialization (`TesterBot._serialize_message`)
3. Manual integration test: Claude Code runs through a full session on the test server

---

## Verification

### Automated (CI-compatible)

```bash
# ScenarioRunner tests
uv run pytest tests/scenarios/ -v

# Existing tests still pass
uv run pytest --ignore=tests/scenarios/ -v

# Linting + types
uv run ruff check .
uv run mypy .
```

### Manual (MCP server)

1. Start game bot with `TEST_MODE=true`
2. Claude Code uses `discord_status()` → confirms game bot is online
3. Claude Code runs a full session:
   - `discord_send_command("start_campaign", {"theme": "donjon", "players": "1"})`
   - `discord_send_command("create_character", {"name": "Test", "race": "human", "class_": "fighter"})`
   - `discord_send_command("look")`
   - `discord_send_command("roll", {"expression": "1d20+5"})`
   - `discord_get_game_state()` → verify state consistency
4. Verify responses are posted in the Discord test channel
