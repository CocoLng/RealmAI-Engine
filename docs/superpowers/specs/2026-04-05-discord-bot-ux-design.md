# Discord Bot UX Design — Slash Commands, Channels & Combat

**Date:** 2026-04-05
**Phase:** 3 (Discord bot + multiplayer)
**Status:** Design approved, pending implementation

---

## Context

The engine modules (dice, character, inventory) are functional and tested. Before building the Discord bot (Phase 3), we need a clear UX architecture that defines:

- How players interact with the bot (slash commands, buttons, menus)
- How game sessions map to Discord channels (creation, permissions, archival)
- How combat flows through Discord's interactive components

The bot is the **sole Game Master** — there is no human GM role. All players are equal.

---

## Core Decisions

| Subject | Decision |
|---------|----------|
| Session structure | One shared channel per campaign, players invited by tag |
| Discord category | Configurable per guild, default "RealmAI Sessions" |
| Command visibility | Ephemeral by default, optional `public:` flag |
| Channel lifecycle | Created at `/start_campaign`, archived (read-only) at `/end_campaign` |
| GM role | None — the bot AI is the sole Game Master |
| Combat UX | Buttons + select menus (no slash commands during combat) |

---

## File Structure

```
bot/
├── bot.py                  # Bot setup, cog loading, intents, on_ready
├── config.py               # BotConfig model (category name, defaults per guild)
├── cogs/
│   ├── session.py          # /start_campaign, /resume, /save, /end_campaign
│   ├── character.py        # /create_character, /character, /level_up
│   ├── inventory.py        # /inventory, /equip, /unequip, /use_item
│   ├── combat.py           # Combat flow: posts embeds, attaches views
│   ├── exploration.py      # /look, /search, /talk, /move (Phase 2+ AI needed)
│   └── rolls.py            # /roll (free dice expression)
├── views/
│   ├── combat_view.py      # View with action buttons (Attack, Cast, Defend, Flee)
│   ├── target_select.py    # Select menu for choosing a target
│   └── spell_select.py     # Select menu for choosing a spell
├── embeds/
│   ├── character_embed.py  # Character sheet embed builder
│   ├── inventory_embed.py  # Inventory embed (items, equipped, weight, gold)
│   ├── combat_embed.py     # Combat status embed (initiative, HP, conditions)
│   └── narrative_embed.py  # Narrative + raw mechanics dual embed
└── utils/
    └── channel_manager.py  # Channel creation, permissions, archival
```

Cogs import engine functions directly (e.g., `cogs/inventory.py` calls `engine.inventory.add_item()`). No intermediate abstraction layer.

---

## Channel Management

### Creation (`/start_campaign`)

- Player runs: `/start_campaign theme:"Donjon des ombres" players:@Alice @Bob`
- Bot creates a text channel named `campagne-donjon-des-ombres` in the configured category
- Permissions: only tagged players + the bot have access (channel permission overrides)
- Bot posts an introduction embed with theme, player list, and session start time

### Category Configuration

- Default category: "RealmAI Sessions" (created automatically if missing)
- Configurable per guild via `/settings category:"My Category"` (requires `manage_channels` permission)
- Stored in SQLite per guild ID

### Archival (`/end_campaign`)

- Bot moves the channel to a "RealmAI Archives" category (created if missing)
- Removes write permissions (channel becomes read-only)
- Posts a campaign summary embed (stats, duration, player characters)

### Required Bot Permissions

- `manage_channels` — create, move, modify channels
- `manage_roles` — modify permission overrides on channels
- `send_messages`, `embed_links`, `use_external_emojis`

---

## Slash Commands

### Session Cog (`cogs/session.py`)

| Command | Parameters | Ephemeral | Notes |
|---------|-----------|-----------|-------|
| `/start_campaign` | `theme: str`, `players: str` (mentions) | No (public announcement) | Creates channel, invites players |
| `/resume` | — | No (public announcement) | Loads last saved state |
| `/save` | — | Yes | Checkpoint current session |
| `/end_campaign` | — | No (public announcement) | Archives channel |

### Character Cog (`cogs/character.py`)

| Command | Parameters | Ephemeral | `public:` flag |
|---------|-----------|-----------|----------------|
| `/create_character` | — (opens modal) | Yes (modal interaction) | No |
| `/character` | `public: bool = False` | Yes | Yes |
| `/level_up` | `public: bool = False` | Yes | Yes |

### Inventory Cog (`cogs/inventory.py`)

| Command | Parameters | Ephemeral | `public:` flag |
|---------|-----------|-----------|----------------|
| `/inventory` | `public: bool = False` | Yes | Yes |
| `/equip` | `item: str`, `slot: str` | Yes | No |
| `/unequip` | `slot: str` | Yes | No |
| `/use_item` | `item: str` | Yes | No |

### Rolls Cog (`cogs/rolls.py`)

| Command | Parameters | Ephemeral | Notes |
|---------|-----------|-----------|-------|
| `/roll` | `expression: str` | No (always public) | Fun, everyone wants to see rolls |

### Settings (in `cogs/session.py`)

| Command | Parameters | Ephemeral | Notes |
|---------|-----------|-----------|-------|
| `/settings` | `category: str` | Yes | Requires `manage_channels` perm. Sets session category for this guild |

### Exploration Cog (`cogs/exploration.py`) — Phase 2+ (requires AI)

| Command | Parameters | Ephemeral | Notes |
|---------|-----------|-----------|-------|
| `/look` | — | No (public) | Describe current location |
| `/search` | `target: str` | No (public) | Search area or object |
| `/talk` | `npc: str` | No (public) | Initiate NPC dialogue |
| `/move` | `direction: str` | No (public) | Move to adjacent area |

---

## Combat UX

### Trigger

When the engine detects a combat encounter, the combat cog posts an initiative embed with a `CombatView` attached.

### Turn Flow

1. Bot mentions the active player (`@Player, c'est ton tour !`)
2. Embed displays: initiative order, HP bars, active conditions
3. Four buttons: **Attaquer** | **Lancer sort** | **Defendre** | **Fuir**
4. Click "Attaquer" → `TargetSelect` (select menu with available targets) → engine resolves
5. Click "Lancer sort" → `SpellSelect` (available spells) → `TargetSelect` → engine resolves
6. Bot posts result (narrative embed + raw mechanics) and advances to next turn

### Interaction Guards

- Only the active player can interact with the combat buttons (check `interaction.user.id`)
- Other players see the buttons but get an ephemeral "Ce n'est pas ton tour" if they click

### Timeout

- 2 minutes: bot sends a reminder mention
- 5 minutes: default action (Defend), turn advances

### Combat Embed Content

```
=== COMBAT — Tour 3 ===
Initiative: [Aldric (18)] > [Goblin A (15)] > [Elara (12)] > [Goblin B (8)]

Aldric (Fighter 3)    ████████░░ 24/30 HP
Elara (Wizard 3)      ██████░░░░ 15/22 HP
Goblin A              ███░░░░░░░  4/12 HP   [Poisoned]
Goblin B              ██████████ 12/12 HP

> Tour de: @Aldric
[Attaquer] [Lancer sort] [Defendre] [Fuir]
```

---

## Embed Designs

### Character Sheet (`/character`)

- **Header:** Name, Race, Class, Level, Alignment
- **Field 1 — Ability Scores:** STR/DEX/CON/INT/WIS/CHA with modifiers
- **Field 2 — Combat Stats:** HP, AC, Speed, Proficiency Bonus
- **Field 3 — Saving Throws:** Proficient saves highlighted
- **Field 4 — XP:** Current / next level threshold, progress bar
- **Footer:** Hit die notation

### Inventory (`/inventory`)

- **Header:** Player name, Gold, Weight (current / capacity), Encumbrance status
- **Field 1 — Equipped:** Slot → Item name for each equipped slot
- **Field 2 — Attuned:** List of attuned items (X/3)
- **Field 3 — Backpack:** All carried items with type, quantity, weight
- **Footer:** Total items count

### Narrative + Mechanics (`narrative_embed.py`)

Dual-panel embed shown after every action resolution:
- **Narrative section:** LLM-generated immersive text
- **Mechanics section:** Raw dice rolls, damage dealt, HP changes, conditions applied

---

## Data Flow

```
Player types /inventory
    → Discord dispatches to cogs/inventory.py
    → Cog loads player's Character + Inventory from DB (SQLite)
    → Cog calls engine.inventory functions as needed
    → Cog calls embeds/inventory_embed.py to build the Embed
    → Responds with ephemeral=True (or False if public:True)
```

```
Combat button "Attaquer" clicked
    → views/combat_view.py receives interaction
    → Sends TargetSelect (views/target_select.py)
    → Player selects target
    → Cog calls engine.combat.resolve_attack()
    → Cog calls embeds/combat_embed.py + embeds/narrative_embed.py
    → Posts result, updates initiative embed, advances turn
```

---

## Configuration Model (`bot/config.py`)

```python
class GuildConfig(BaseModel):
    guild_id: int
    session_category_name: str = "RealmAI Sessions"
    archive_category_name: str = "RealmAI Archives"
```

Stored in SQLite, one row per guild. Queried on bot startup and cached.

---

## Scope Boundaries

**In scope (this design):**
- Slash commands for session, character, inventory, rolls
- Channel creation, permissions, archival
- Combat buttons and select menus
- Embed builders for all data types
- Guild-level configuration

**Out of scope (later phases):**
- AI layer integration (narrator, interpreter) — Phase 2
- Exploration commands (need AI) — Phase 2+
- MCP server — Phase 4
- Voice channel support
- Cross-server campaigns
