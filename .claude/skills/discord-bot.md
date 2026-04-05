---
name: discord-bot
description: >
  Reference for building the Discord bot layer (bot/ directory). Use this skill whenever working on
  slash commands, Cogs, combat buttons, character modals, embed formatting, campaign sessions,
  thread-per-campaign, interaction deferral, or any bot/ module. Covers discord.py 2.4+ patterns
  (app_commands, ui.View, ui.Modal, Embed), async interaction lifecycle, multiplayer session
  management, and engine integration. Trigger on: bot/, discord, slash command, Cog, embed, modal,
  View, button, campaign, session, thread, interaction, defer, followup, ephemeral, combat UI,
  discord.py, multiplayer, create_character, start_campaign, save, resume.
---

# Discord Bot Skill

## Core Principle

**The bot is a thin presentation layer. It translates Discord interactions into engine calls and
engine results into embeds. No game logic in `bot/`.**

Pipeline: `Discord interaction -> Interpreter -> Validator -> Engine -> Context Assembler -> Narrator -> Embed -> Discord`

If you're tempted to roll dice, calculate damage, or check action legality inside `bot/` — stop.
That belongs in `engine/`. The bot calls `process_action()` and renders the result.

---

## Module Build Order

Build in this sequence — each module depends only on prior ones:

1. `bot/bot.py` — Bot subclass, intents, `setup_hook`, Cog loading
2. `bot/embeds/` — Embed builders (character sheet, combat result, error, narrative)
3. `bot/views/combat.py` — CombatView with attack/defend/cast/flee buttons
4. `bot/views/character_creation.py` — Modal + followup select for character creation
5. `bot/commands/campaign.py` — Cog: `/start_campaign`, `/resume`, `/save`
6. `bot/commands/character.py` — Cog: `/create_character`, `/character_sheet`
7. `bot/commands/combat.py` — Cog: combat trigger, turn management
8. `bot/commands/admin.py` — Cog: `/sync`, `/shutdown`, debug commands
9. `bot/session.py` — Session manager mapping guild+channel to GameState

---

## Bot Setup (`bot/bot.py`)

```python
import os
import discord
from discord.ext import commands


class RealmBot(commands.Bot):
    """The RPG Game Master bot."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # Required for on_message free-text actions
        intents.members = True          # Required for player tracking
        super().__init__(command_prefix="!", intents=intents)
        self.session_manager = SessionManager()

    async def setup_hook(self) -> None:
        """Async init — load Cogs and sync command tree."""
        await self.load_extension("bot.commands.campaign")
        await self.load_extension("bot.commands.character")
        await self.load_extension("bot.commands.combat")
        await self.load_extension("bot.commands.admin")

        # Dev: sync to a specific guild for instant updates
        # guild = discord.Object(id=int(os.environ["DEV_GUILD_ID"]))
        # self.tree.copy_global_to(guild=guild)
        # await self.tree.sync(guild=guild)

        # Prod: sync globally (takes up to 1 hour to propagate)
        await self.tree.sync()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (ID: {self.user.id})")


def main() -> None:
    bot = RealmBot()
    bot.run(os.environ["DISCORD_TOKEN"])
```

Key points:
- `setup_hook` is the async init, NOT `on_ready`. Load Cogs and sync tree here.
- Use guild sync during development (instant), global sync for production (slow propagation).
- `session_manager` is attached to the bot instance — Cogs access it via `self.bot.session_manager`.

---

## Cog Pattern

Each Cog owns a domain. One file per Cog under `bot/commands/`.

```python
import discord
from discord import app_commands
from discord.ext import commands


class CampaignCog(commands.Cog):
    """Campaign lifecycle commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="start_campaign", description="Start a new campaign")
    @app_commands.describe(theme="Campaign theme (e.g., 'dark forest', 'pirate adventure')")
    async def start_campaign(self, interaction: discord.Interaction, theme: str) -> None:
        await interaction.response.defer()  # LLM narration takes time

        # Create thread for this campaign
        thread = await interaction.channel.create_thread(
            name=f"Campaign: {theme}",
            type=discord.ChannelType.public_thread,
            auto_archive_duration=1440,  # 24 hours
        )

        # Create session, generate intro via narrator...
        session = self.bot.session_manager.create_session(
            guild_id=interaction.guild_id,
            thread_id=thread.id,
            player_discord_ids=[interaction.user.id],
        )

        intro_embed = await self._generate_intro(session, theme)
        await thread.send(embed=intro_embed)
        await interaction.followup.send(f"Campaign started! Head to {thread.mention}")

    async def _generate_intro(self, session, theme: str) -> discord.Embed:
        # Call narrator via asyncio.to_thread (see Engine Integration section)
        ...


async def setup(bot: commands.Bot) -> None:
    """Required for load_extension."""
    await bot.add_cog(CampaignCog(bot))
```

### Cog Ownership

| Cog | Commands | Purpose |
|-----|----------|---------|
| `CampaignCog` | `/start_campaign`, `/resume`, `/save` | Session lifecycle |
| `CharacterCog` | `/create_character`, `/character_sheet` | Character management |
| `CombatCog` | (internal: manages combat flow) | Combat turn management |
| `AdminCog` | `/sync`, `/shutdown` | Bot administration |

---

## Interaction Lifecycle

You get **3 seconds** to respond to an interaction. After that, Discord marks it as failed.
You can only use `interaction.response` **once**. For additional messages, use `interaction.followup`.

### Pattern A — Fast Response (< 3s)

For pure-engine lookups with no LLM calls.

```python
@app_commands.command(name="character_sheet", description="View your character")
async def character_sheet(self, interaction: discord.Interaction) -> None:
    char = self.bot.session_manager.get_character(interaction.user.id)
    embed = build_character_embed(char)
    await interaction.response.send_message(embed=embed, ephemeral=True)
```

### Pattern B — Slow Response with Defer (LLM calls)

For anything that hits the interpreter/narrator pipeline.

```python
@app_commands.command(name="resume", description="Resume a saved campaign")
async def resume(self, interaction: discord.Interaction) -> None:
    await interaction.response.defer()  # Shows "Bot is thinking..."
    # ... load session, build context, call narrator (5-15s)
    embed = build_narrative_embed(result, narrative)
    await interaction.followup.send(embed=embed)
```

### Pattern C — Modal Flow

For collecting structured input from players.

```python
@app_commands.command(name="create_character", description="Create a new character")
async def create_character(self, interaction: discord.Interaction) -> None:
    await interaction.response.send_modal(CharacterCreationModal())
```

### Interaction Rules

| Rule | Detail |
|------|--------|
| 3-second deadline | Must `response.send_message` or `response.defer` within 3s |
| One response only | `interaction.response` can only be used once per interaction |
| Unlimited followups | After defer, use `interaction.followup.send` as many times as needed |
| Ephemeral = private | Only the invoking user sees it — use for character sheets, errors |
| `defer(thinking=True)` | For component interactions (buttons) that need a thinking indicator |

---

## Free-Text Action Handling

Players type actions as regular messages in campaign threads. No slash command needed.

```python
class CampaignCog(commands.Cog):

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        session = self.bot.session_manager.get_by_thread(message.channel.id)
        if session is None:
            return  # Not a campaign thread

        async with message.channel.typing():  # Shows "Bot is typing..."
            embed, view = await process_action(
                raw_text=message.content,
                session=session,
                bot=self.bot,
            )

        await message.reply(embed=embed, view=view)
```

Since `on_message` is not an interaction, there is no `defer`. Use `async with channel.typing()`
to show the typing indicator while the pipeline processes.

---

## Combat View (`bot/views/combat.py`)

Four buttons. Only the active player can use them. Recreated each turn.

```python
import discord
from discord import ui


class CombatView(ui.View):
    """Combat action buttons. Recreated per turn with the active player's ID."""

    def __init__(self, session_id: str, active_player_discord_id: int) -> None:
        super().__init__(timeout=300)  # 5 minutes per turn
        self.session_id = session_id
        self.active_player_discord_id = active_player_discord_id

    async def _check_turn(self, interaction: discord.Interaction) -> bool:
        """Reject if it's not this player's turn."""
        if interaction.user.id != self.active_player_discord_id:
            await interaction.response.send_message(
                "It's not your turn!", ephemeral=True,
            )
            return False
        return True

    @ui.button(label="Attack", style=discord.ButtonStyle.danger, emoji="\u2694\ufe0f")
    async def attack(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_turn(interaction):
            return
        await interaction.response.defer(thinking=True)
        # ... resolve attack through pipeline
        embed, next_view = await process_combat_action("attack", self.session_id)
        await interaction.followup.send(embed=embed, view=next_view)

    @ui.button(label="Defend", style=discord.ButtonStyle.secondary, emoji="\U0001f6e1\ufe0f")
    async def defend(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_turn(interaction):
            return
        await interaction.response.defer(thinking=True)
        embed, next_view = await process_combat_action("defend", self.session_id)
        await interaction.followup.send(embed=embed, view=next_view)

    @ui.button(label="Cast Spell", style=discord.ButtonStyle.primary, emoji="\u2728")
    async def cast_spell(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_turn(interaction):
            return
        await interaction.response.defer(thinking=True)
        embed, next_view = await process_combat_action("cast_spell", self.session_id)
        await interaction.followup.send(embed=embed, view=next_view)

    @ui.button(label="Flee", style=discord.ButtonStyle.success, emoji="\U0001f3c3")
    async def flee(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not await self._check_turn(interaction):
            return
        await interaction.response.defer(thinking=True)
        embed, next_view = await process_combat_action("flee", self.session_id)
        await interaction.followup.send(embed=embed, view=next_view)

    async def on_timeout(self) -> None:
        """Disable all buttons when the turn times out."""
        for child in self.children:
            child.disabled = True
        # Edit the original message to show disabled buttons
        # (requires storing the message reference)
```

Design: the view is **recreated each turn**. When the engine advances the initiative order,
send a new `CombatView` with the next player's Discord ID. Non-active players see the buttons
but get an ephemeral "not your turn" rejection.

---

## Character Creation Modal

Modals only support `TextInput`. Use a two-step flow for race/class selection.

### Step 1 — Modal for Text Fields

```python
class CharacterCreationModal(ui.Modal, title="Create Your Character"):
    name = ui.TextInput(
        label="Character Name",
        placeholder="e.g., Thorn Ironfist",
        min_length=1,
        max_length=64,
    )
    backstory = ui.TextInput(
        label="Backstory (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="A brief backstory for your character...",
        required=False,
        max_length=500,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"Great, **{self.name.value}**! Now choose your race and class:",
            view=RaceClassSelectView(name=self.name.value, backstory=self.backstory.value),
            ephemeral=True,
        )
```

### Step 2 — Select Menus for Race/Class

```python
class RaceClassSelectView(ui.View):
    def __init__(self, name: str, backstory: str) -> None:
        super().__init__(timeout=120)
        self.char_name = name
        self.backstory = backstory
        self.selected_race: str | None = None
        self.selected_class: str | None = None

    @ui.select(
        placeholder="Choose your race...",
        options=[
            discord.SelectOption(label="Human", value="human"),
            discord.SelectOption(label="Elf", value="elf"),
            discord.SelectOption(label="Dwarf", value="dwarf"),
            discord.SelectOption(label="Halfling", value="halfling"),
            # ... remaining races
        ],
    )
    async def race_select(self, interaction: discord.Interaction, select: ui.Select) -> None:
        self.selected_race = select.values[0]
        await interaction.response.defer()
        await self._try_finalize(interaction)

    @ui.select(
        placeholder="Choose your class...",
        options=[
            discord.SelectOption(label="Fighter", value="fighter"),
            discord.SelectOption(label="Wizard", value="wizard"),
            discord.SelectOption(label="Cleric", value="cleric"),
            discord.SelectOption(label="Rogue", value="rogue"),
            # ... remaining classes
        ],
    )
    async def class_select(self, interaction: discord.Interaction, select: ui.Select) -> None:
        self.selected_class = select.values[0]
        await interaction.response.defer()
        await self._try_finalize(interaction)

    async def _try_finalize(self, interaction: discord.Interaction) -> None:
        if self.selected_race and self.selected_class:
            # Create character via engine, send sheet embed
            char = create_character(
                name=self.char_name, race=self.selected_race,
                character_class=self.selected_class, backstory=self.backstory,
            )
            embed = build_character_embed(char)
            await interaction.followup.send(embed=embed, ephemeral=True)
```

---

## Embed Builders (`bot/embeds/`)

Pure functions: `data -> discord.Embed`. No side effects, easy to test.

### Character Sheet Embed

```python
def hp_bar(current: int, maximum: int, length: int = 10) -> str:
    """Visual HP bar: [████████░░] 80/100"""
    filled = round(length * current / maximum) if maximum > 0 else 0
    return f"[{'█' * filled}{'░' * (length - filled)}] {current}/{maximum}"


def build_character_embed(char: Character) -> discord.Embed:
    embed = discord.Embed(
        title=f"{char.name} — Level {char.level} {char.character_class.value.title()}",
        color=CLASS_COLORS.get(char.character_class, discord.Color.greyple()),
    )
    embed.add_field(name="HP", value=hp_bar(char.hit_points, char.max_hit_points), inline=False)
    embed.add_field(name="AC", value=str(char.armor_class), inline=True)
    embed.add_field(name="Race", value=char.race.value.title(), inline=True)

    # Ability scores — 2 per row
    for i, (ability, score) in enumerate(char.ability_scores.items()):
        mod = (score - 10) // 2
        sign = "+" if mod >= 0 else ""
        embed.add_field(name=ability.value[:3].upper(), value=f"{score} ({sign}{mod})", inline=True)

    if char.conditions:
        embed.add_field(
            name="Conditions",
            value=", ".join(c.value for c in char.conditions),
            inline=False,
        )

    embed.set_footer(text=f"ID: {char.id}")
    return embed
```

### Combat Result Embed

```python
def build_combat_embed(result: ActionResult, narrative: str) -> discord.Embed:
    color = discord.Color.green() if result.success else discord.Color.red()
    embed = discord.Embed(
        title=f"{result.actor_id} — {result.action_type.replace('_', ' ').title()}",
        description=narrative,  # LLM-generated narrative text
        color=color,
    )
    for dice in result.dice_results:
        embed.add_field(
            name=f"Roll: {dice.expression}",
            value=f"{dice.rolls} + {dice.modifier} = **{dice.total}**",
            inline=True,
        )
    if result.damage_dealt:
        embed.add_field(name="Damage", value=f"{result.damage_dealt} {result.damage_type.value}", inline=True)
    if result.healing_done:
        embed.add_field(name="Healing", value=str(result.healing_done), inline=True)

    embed.set_footer(text=result.summary)  # Raw mechanics for transparency
    return embed
```

### Error Embed

```python
def build_error_embed(validation: ValidationResult) -> discord.Embed:
    embed = discord.Embed(title="Invalid Action", color=discord.Color.red())
    for error in validation.errors:
        embed.add_field(name=error.code, value=error.message, inline=False)
    return embed
```

---

## Session Manager (`bot/session.py`)

Maps Discord state to game state. One thread = one campaign.

```python
from pydantic import BaseModel, Field


class GameSession(BaseModel):
    """A campaign session tied to a Discord thread."""

    session_id: str
    guild_id: int
    thread_id: int
    player_discord_ids: list[int] = Field(default_factory=list)
    # game_state: GameState  — loaded from DB on demand


class SessionManager:
    """Maps (guild_id, thread_id) -> GameSession."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[int, int], GameSession] = {}

    def create_session(self, guild_id: int, thread_id: int, player_discord_ids: list[int]) -> GameSession:
        session = GameSession(
            session_id=f"{guild_id}-{thread_id}",
            guild_id=guild_id,
            thread_id=thread_id,
            player_discord_ids=player_discord_ids,
        )
        self._sessions[(guild_id, thread_id)] = session
        return session

    def get_by_thread(self, thread_id: int) -> GameSession | None:
        for key, session in self._sessions.items():
            if key[1] == thread_id:
                return session
        return None

    def add_player(self, thread_id: int, discord_id: int) -> None:
        session = self.get_by_thread(thread_id)
        if session and discord_id not in session.player_discord_ids:
            session.player_discord_ids.append(discord_id)
```

### Discord-to-Game Mapping

| Discord Concept | Game Concept | Relationship |
|-----------------|-------------|--------------|
| Guild (server) | Server | One guild can host many campaigns |
| Thread | Campaign | One thread = one campaign session |
| User | Player/Character | One user = one character per campaign |
| Channel (parent) | Campaign hub | Threads are created under this channel |

---

## Thread-Per-Campaign Pattern

Each campaign gets its own Discord thread. This isolates narrative, simplifies history, and
lets multiple campaigns run concurrently in the same server.

**Lifecycle:**
1. `/start_campaign` -> creates thread + session -> sends intro narrative embed
2. Players join by posting in the thread (auto-detected via `on_message`) or `/join_campaign`
3. In-game: free text + combat buttons in the thread
4. `/save` -> serializes GameState to DB -> sends save confirmation embed
5. `/resume` -> loads from DB -> sends thread link + "Session resumed" embed

Thread creation uses `auto_archive_duration=1440` (24 hours). For longer campaigns,
the bot re-activates the thread on `/resume` by sending a message.

---

## Engine Integration

A single async function is the chokepoint between Discord and the engine pipeline. Every
command, button callback, and `on_message` listener calls this.

```python
import asyncio

async def process_action(
    raw_text: str,
    session: GameSession,
    bot: commands.Bot,
) -> tuple[discord.Embed, discord.ui.View | None]:
    """Run the full pipeline: interpret -> validate -> engine -> narrate -> embed."""

    # 1. Interpreter (LLM) — run in executor to avoid blocking the event loop
    action = await asyncio.to_thread(interpreter.parse, raw_text, session.game_state)

    # 2. Validator (pure Python, fast)
    validation = validator.validate(action, session.game_state)
    if not validation.is_valid:
        return build_error_embed(validation), None

    # 3. Engine (pure Python, fast)
    result = engine.resolve(action, session.game_state)

    # 4. Context Assembler + Narrator (LLM) — blocking, run in executor
    context = await asyncio.to_thread(assembler.build, session, result)
    narrative = await asyncio.to_thread(narrator.narrate, result, context)

    # 5. Build embed + optional combat view
    embed = build_combat_embed(result, narrative)
    view = None
    if session.game_state.combat.is_active:
        active_player = session.game_state.combat.current_turn_id
        view = CombatView(session.session_id, active_player)

    return embed, view
```

**Critical**: wrap all blocking calls (LLM inference, heavy computation) in `asyncio.to_thread`.
Ollama calls take 5-15 seconds — blocking the event loop freezes the entire bot.

---

## Error Handling

| Error | Source | Bot Response |
|-------|--------|-------------|
| Validation failure | `ActionValidator` | Error embed listing all `ValidationError.message` strings |
| Interpreter can't parse | `Interpreter` | Ephemeral: "I didn't understand that. Try: attack goblin, cast fireball, move north" |
| Engine exception | `Engine` | Log error, ephemeral: "Something went wrong. Try again." |
| Narrator timeout | `Narrator` | Send raw `ActionResult.summary` without narrative, log warning |
| Not in a campaign | Session lookup | Ephemeral: "No active campaign. Use /start_campaign or /resume." |
| Not your turn | Turn check | Ephemeral: "It's {active_player}'s turn." |

### Global Error Handler

```python
class CampaignCog(commands.Cog):
    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(
                f"Cooldown: try again in {error.retry_after:.0f}s", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Something went wrong. Try again.", ephemeral=True,
            )
            # Log the full error for debugging
```

---

## Testing the Bot Layer

Embed builders are pure functions — test them directly. Mock `discord.Interaction` for commands.

```
tests/
├── test_embeds.py      # Unit tests: ActionResult -> Embed field assertions
├── test_views.py       # Button callback tests with mocked interactions
├── test_session.py     # SessionManager with in-memory state
└── test_commands.py    # Integration: mock pipeline, verify embeds
```

```bash
uv run pytest tests/test_embeds.py   # Fast, no Discord connection needed
uv run pytest tests/test_session.py  # In-memory, no DB needed
```

Focus test effort on embed builders (they're pure functions producing deterministic output)
and session manager logic. Command integration tests can mock `process_action` to return
known `ActionResult` values and verify the correct embed is produced.
