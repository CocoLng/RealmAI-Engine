---
name: discord-live-testing
description: Use this skill when implementing features, fixing bugs, debugging issues, or verifying changes to the Discord bot. Covers both automated scenario tests (pytest ScenarioRunner) and live Discord testing via the tester bot. Trigger whenever the task involves bot cogs, views, embeds, combat flow, session management, or any Discord-visible behavior. Also use when the user asks to "test on Discord", "run a scenario", "check if the bot works", or "debug the bot".
---

# Discord Live Testing

Two tools to verify bot behavior: fast automated scenarios (pytest) and live Discord interaction (tester bot). Use both — scenarios for systematic coverage, live testing for exploratory debugging and UX verification.

## When to use which

| Situation | Tool |
|-----------|------|
| Implementing a new cog method or modifying engine logic | ScenarioRunner first, then live test |
| Fixing a bug reported by a user | Live test to reproduce, ScenarioRunner to add regression test |
| Verifying save/resume integrity | ScenarioRunner (persistence tests) |
| Checking embed formatting, message content, UX flow | Live test (see real Discord output) |
| Pre-commit verification | `uv run pytest tests/scenarios/ -v` |
| Exploring edge cases interactively | Live test with improvised commands |

## Tool 1: ScenarioRunner (pytest)

Fast, deterministic, no Discord needed. Tests chain cog handlers into full gameplay sequences with real engine + real in-memory DB.

### Running existing scenarios

```bash
uv run pytest tests/scenarios/ -v           # All 32 scenario tests
uv run pytest tests/scenarios/test_combat_scenarios.py -v  # Just combat
```

### Writing a new scenario test

Create or edit a file in `tests/scenarios/`. The `scenario` fixture is auto-available.

```python
@pytest.mark.asyncio
async def test_my_scenario(scenario: ScenarioRunner) -> None:
    await scenario.start_campaign(theme="Test", players=1)
    await scenario.add_player("Hero", race="Human", class_="Fighter", player_idx=0)
    # ... chain commands, then assert
    scenario.assert_hp(0, expected=10)
```

### ScenarioRunner API

**Campaign lifecycle:**
- `await scenario.start_campaign(theme="...", players=N)`
- `await scenario.save()` / `await scenario.resume()` / `await scenario.end_campaign()`
- `scenario.clear_session()` — simulates bot restart (clears in-memory state)

**Character** (bypasses the multi-step Discord view, creates directly):
- `await scenario.add_player("Name", race="Human", class_="Fighter", player_idx=0)`
- `await scenario.character(player_idx=0)` — view character sheet
- Race values: Human, Elf, Dwarf, Halfling, Half-Orc, Gnome, Tiefling
- Class values: Fighter, Wizard, Rogue, Cleric, Ranger, Barbarian

**Combat** (enemies auto-resolve their turns after each player action):
- `await scenario.start_combat(enemies=[...])` — takes list of `Combatant`
- `await scenario.attack(target="EnemyName", player_idx=0)`
- `await scenario.cast_spell(spell="SpellName", target="...", player_idx=0)`
- `await scenario.defend(player_idx=0)` / `await scenario.flee(player_idx=0)`

**Exploration:**
- `await scenario.look()` / `await scenario.move(direction="...")` 
- `await scenario.search(target="...")` / `await scenario.talk(npc="...")`

**Inventory:**
- `await scenario.equip(item="...", slot="...", player_idx=0)`
- `await scenario.unequip(slot="...", player_idx=0)`
- `await scenario.use_item(item="...", player_idx=0)`
- `await scenario.inventory(player_idx=0)`

**Rolls:**
- `await scenario.roll("2d6+3", player_idx=0)`

**Assertions:**
- `scenario.get_character(idx)` → Character (hp, max_hp, xp, level, name, race, char_class)
- `scenario.get_inventory(idx)` → Inventory (items, equipped)
- `scenario.assert_hp(idx, expected)` / `scenario.assert_in_combat()` / `scenario.assert_not_in_combat()`
- `scenario.assert_has_item(idx, "item_name")` / `scenario.assert_condition(idx, "condition")`
- `scenario.last_response` → EmbedCapture (content, embed, view, ephemeral)
- `scenario.session` → GameSession (campaign, characters, combat_state, current_location, npcs, quests)

**Enemy helpers** (from `tests/scenarios/conftest.py`):
- `make_enemy("Name", hp=10, ac=12)` — standard enemy with weapon
- `make_weak_enemy("Name")` — 1 HP, 5 AC (guaranteed kill on hit)
- `make_strong_enemy("Name")` — 50 HP, 16 AC
- `give_starter_weapon(scenario, player_idx=0)` — equips "Epee longue" (1d8)

**Combat test pattern** — natural 1 always misses, so always loop attacks against weak enemies:
```python
for _ in range(10):
    if scenario.session is None or scenario.session.combat_state is None:
        break
    await scenario.attack(target="Rat", player_idx=0)
scenario.assert_not_in_combat()
```

## Tool 2: Live Discord Testing (tester bot)

Connects a second Discord bot to the test server, sends `!test` commands to the game bot's TestBridge cog, and reads the real Discord responses.

### Always check the bot run logs

The MCP discord-test tools only see what comes back through Discord. They do **not** see the game bot's stdout or file logs. The bot writes a fresh log file to `logs/realm_<timestamp>.log` for every run (newest file = current run).

**Rule:** On ANY unexpected live-test result — timeout, "no response", missing embed, weird content, tester bot error — you MUST read the latest log file **before** concluding anything. Do not assume "the bot didn't respond"; the log is authoritative and often shows the response was produced (or an exception was raised) even when Discord delivery looked broken.

Quick workflow:
```bash
ls -t logs/realm_*.log | head -1   # find current run
```
Then `Read` that file (tail the last ~200 lines) and grep for `ERROR`, `Traceback`, or the command you just sent.

### Prerequisites

1. Game bot must be running with `TEST_MODE=true`:
   ```bash
   TEST_MODE=true uv run python -c "from bot.bot import run_bot; run_bot()"
   ```
2. `.env` must have: `TESTER_BOT_TOKEN`, `TESTER_BOT_ID`, `TEST_CHANNEL_ID`, `GAME_BOT_ID`
3. Both bots must be invited to the test Discord server

### Running a live test script

Write a Python script that connects the tester bot, sends commands, and reads responses:

```python
import asyncio, os, discord
from dotenv import load_dotenv
load_dotenv()

TESTER_TOKEN = os.environ["TESTER_BOT_TOKEN"]
GAME_BOT_ID = int(os.environ["GAME_BOT_ID"])
TEST_CHANNEL_ID = int(os.environ["TEST_CHANNEL_ID"])

class Tester(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)

    async def on_ready(self):
        channel = self.get_channel(TEST_CHANNEL_ID)
        # Send command and wait for game bot response
        await channel.send("!test game_state")
        msg = await self.wait_for(
            "message",
            check=lambda m: m.author.id == GAME_BOT_ID and m.channel.id == TEST_CHANNEL_ID,
            timeout=15.0,
        )
        print(f"Response: {msg.content}")
        for embed in msg.embeds:
            print(f"Embed: {embed.title} — {embed.description}")
        await self.close()

Tester().run(TESTER_TOKEN, log_handler=None)
```

### Available !test commands

All commands go through the TestBridge cog. Format: `!test [player=N] <command> [key=value ...]`

| Command | Args | What it does |
|---------|------|--------------|
| `start_campaign` | `theme=X players=N` | Creates campaign on the test channel (no new channel) |
| `create_character` | *(none)* → full view flow, or `quick=1 name=X race=Y class_=Z` → shortcut | Default: sends the real multi-step view so the tester drives it like a player. Shortcut skips the view and rolls stats. |
| `character` | | Shows character embed |
| `save` | | Saves session to DB |
| `resume` | | Resumes saved session |
| `look` | | Describes current location |
| `move` | `direction=X` | Move to connected location |
| `search` | `target=X` | Search for something |
| `talk` | `npc=X` | Talk to NPC |
| `roll` | `expression=X` | Roll dice |
| `inventory` | | Show inventory |
| `equip` | `item=X slot=Y` | Equip item |
| `unequip` | `slot=X` | Unequip slot |
| `use_item` | `item=X` | Use consumable |
| `game_state` | | Returns full session state as JSON |
| `click_button` | `msg=<id> button=<label>` | Clicks a Button in the view attached to the given message |
| `select_option` | `msg=<id> value=<v>` or `value=v1,v2,...` for multi-select | Picks option(s) on the matching Select |
| `submit_modal` | `field_<Label>=<value> ...` | Submits the modal pending for this player (from `send_modal`) |

### Drive a real view (full fidelity)

When testing a slash command that opens a multi-step UI (e.g. `/create_character` → race → class → alignment → stats → skills → name modal), run the full flow so bugs in select transitions / button state / modal wiring are caught:

1. `discord_send_command("!test create_character")` *(no args — sends the real view)*
2. `discord_read_messages(1)` — note the `id` of the bot's message; `components` shows the available select options
3. `discord_select_option(message_id=<id>, value="Elf")`
4. `discord_read_messages(1)` — class select is now enabled
5. `discord_select_option(message_id=<id>, value="Wizard")`
6. `discord_select_option(message_id=<id>, value="Lawful Good")` — transitions to StatAssignmentView
7. Pick each stat value: `discord_select_option(message_id=<id>, value="15")`, `"14"`, … — then `discord_click_button(message_id=<id>, button_label="Confirmer")`
8. Skill selection: `discord_select_option(message_id=<id>, value="Arcana,History")` (comma = multi-select) → `discord_click_button(message_id=<id>, button_label="Confirmer")`
9. Name modal is now pending: `discord_submit_modal(fields={"Nom": "Elrond"})`
10. Verify: `discord_send_command("!test character")` returns an embed with race=Elf, class=Wizard, name=Elrond

Use the shortcut `!test create_character quick=1 name=X race=Y class_=Z` only when the view is not what you're testing — e.g. when you need a character on the session before exercising combat.

### MCP Server (alternative)

The MCP server at `mcp_discord/` exposes the same functionality as Claude Code tools. Configure in `.mcp.json`:
```json
{"mcpServers": {"discord-test": {"command": "uv", "args": ["run", "python", "-m", "mcp_discord"]}}}
```

Tools: `discord_status`, `discord_send_command`, `discord_read_messages`, `discord_click_button`, `discord_select_option`, `discord_submit_modal`, `discord_wait_for_response`, `discord_get_game_state`.

## Development workflow

When implementing or fixing something in the bot:

1. **Understand the change** — read the relevant cog/engine code
2. **Write a scenario test first** — add a test in `tests/scenarios/` that captures the expected behavior
3. **Implement the change** — modify cog/engine code
4. **Run scenario tests** — `uv run pytest tests/scenarios/ -v` to verify
5. **Live test on Discord** — start the bot with `TEST_MODE=true`, run a test script to verify real Discord output (embeds, messages, formatting)
6. **Check bot logs** — read `logs/realm_*.log` (newest file = current run) for errors, warnings, or the actual response. Do this immediately whenever a live test appears to timeout or misbehave — MCP tools can't see the bot's stdout, so the log file is the only source of truth.
7. **Run full test suite** — `uv run pytest` to ensure no regressions

### Key files

| File | Purpose |
|------|---------|
| `tests/scenarios/scenario_runner.py` | ScenarioRunner class |
| `tests/scenarios/conftest.py` | Fixtures + enemy helpers |
| `tests/scenarios/test_*.py` | Scenario test suites |
| `bot/cogs/test_bridge.py` | TestBridge cog (opt-in via TEST_MODE) |
| `mcp_discord/server.py` | MCP server for Claude Code tools |
| `mcp_discord/discord_client.py` | TesterBot client |
| `.mcp.json` | MCP server config |