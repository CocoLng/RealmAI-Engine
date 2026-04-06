# Phase 3c+3d+3e — Cogs, Views & Embeds Design Spec

> Date: 2026-04-06
> Scope: Slash commands (6 cogs), combat views (3+1 views), embed builders (4 embeds)
> Dependencies: Phase 3a (bot foundation) + Phase 3b (channel manager) — both complete

---

## 1. Design Decisions Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | 3c + 3d + 3e combined | Cogs without views/embeds aren't functional |
| Player↔Character DB | `PlayerCharacterRow` (user_id + campaign_id + JSON) | One player = one character per campaign |
| Session state | `GameSession` in-memory dict on bot | Simple, sufficient for <100 sessions |
| Character creation UX | Multi-step: select menus → modal for name | Modals don't support selects; selects prevent typos |
| AI integration | Full pipeline with graceful degradation | Phase 2 complete; fallback to raw mechanics if Ollama down |
| Campaign↔Channel DB | `CampaignChannelRow` (channel_id → campaign_id) | Find campaign from any command's channel |

---

## 2. New DB Layer

### 2.1 PlayerCharacterRow (`db/models.py`)

```python
class PlayerCharacterRow(Base):
    __tablename__ = "player_characters"

    discord_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    character_json: Mapped[str] = mapped_column(Text)       # Character.model_dump_json()
    inventory_json: Mapped[str] = mapped_column(Text)       # Inventory.model_dump_json()
    spellcaster_json: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Composite PK `(discord_user_id, campaign_id)` — one character per player per campaign.

### 2.2 CampaignChannelRow (`db/models.py`)

```python
class CampaignChannelRow(Base):
    __tablename__ = "campaign_channels"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("campaigns.id", ondelete="CASCADE"),
        unique=True,
    )
    guild_id: Mapped[int] = mapped_column(BigInteger)
```

### 2.3 PlayerCharacterRepository (`db/repositories/player_character_repo.py`)

| Method | Signature | Description |
|--------|-----------|-------------|
| `save` | `(user_id: int, campaign_id: str, character: Character, inventory: Inventory, spellcaster: SpellcasterState \| None)` | Insert new player character |
| `get` | `(user_id: int, campaign_id: str) → tuple[Character, Inventory, SpellcasterState \| None] \| None` | Load one player's character |
| `get_all_for_campaign` | `(campaign_id: str) → list[tuple[int, Character, Inventory, SpellcasterState \| None]]` | All characters in campaign |
| `update` | `(user_id: int, campaign_id: str, character: Character, inventory: Inventory, spellcaster: SpellcasterState \| None)` | Update existing |
| `delete` | `(user_id: int, campaign_id: str)` | Remove player character |

### 2.4 CampaignChannelRepository (`db/repositories/campaign_channel_repo.py`)

| Method | Signature | Description |
|--------|-----------|-------------|
| `save` | `(channel_id: int, campaign_id: str, guild_id: int)` | Map channel to campaign |
| `get_by_channel` | `(channel_id: int) → tuple[str, int] \| None` | Get (campaign_id, guild_id) from channel |
| `get_by_campaign` | `(campaign_id: str) → int \| None` | Get channel_id from campaign |
| `delete` | `(channel_id: int)` | Remove mapping |

### 2.5 Mappers (`db/mappers.py`)

```python
def player_character_to_db(user_id: int, campaign_id: str, character: Character,
                           inventory: Inventory, spellcaster: SpellcasterState | None) -> PlayerCharacterRow

def player_character_from_db(row: PlayerCharacterRow) -> tuple[int, Character, Inventory, SpellcasterState | None]

def campaign_channel_to_db(channel_id: int, campaign_id: str, guild_id: int) -> CampaignChannelRow

def campaign_channel_from_db(row: CampaignChannelRow) -> tuple[int, str, int]
```

---

## 3. Game Session Manager (`bot/game_session.py`)

In-memory state for an active campaign channel.

```python
@dataclass
class GameSession:
    campaign: Campaign
    characters: dict[int, Character]          # discord_user_id → Character
    inventories: dict[int, Inventory]         # discord_user_id → Inventory
    spellcasters: dict[int, SpellcasterState | None]  # discord_user_id → SpellcasterState
    combat_state: CombatState | None = None
    current_location: Location | None = None
    ollama_client: OllamaClient | None = None  # None if Ollama unavailable
    narrator: Narrator | None = None
    interpreter: Interpreter | None = None
    npc_agent: NPCAgent | None = None
```

**Why `dataclass` not `BaseModel`?** GameSession holds mutable runtime state + service references (OllamaClient). Not serialized.

### 3.1 Session Management on RealmBot

```python
class RealmBot(commands.Bot):
    sessions: dict[int, GameSession]  # channel_id → GameSession

    def get_session(self, channel_id: int) -> GameSession | None
```

Sessions are:
- **Created** by `/start_campaign` or `/resume`
- **Persisted** by `/save` (writes characters, inventories, campaign to DB)
- **Destroyed** by `/end_campaign` (save + archive + remove from dict)

### 3.2 AI Service Initialization

When creating a GameSession, try to create OllamaClient + Narrator + Interpreter. If Ollama is unreachable, set them to `None`. Cogs check `session.narrator is not None` before narrating; fallback to raw mechanics.

```python
def create_ai_services(session: GameSession) -> None:
    """Attempt to initialize AI services. Silent failure if Ollama is down."""
    try:
        client = OllamaClient()
        session.ollama_client = client
        session.narrator = Narrator(client)
        session.interpreter = Interpreter(client)
        session.npc_agent = NPCAgent(client)
    except Exception:
        logger.warning("Ollama unavailable — AI features disabled")
```

---

## 4. Cogs — Slash Commands

All cogs extend `commands.Cog` and receive `bot: RealmBot` in `__init__`. Each cog is loaded via the `EXTENSIONS` list in `bot/bot.py`.

### Common Pattern: Session Lookup

```python
def _get_session(self, interaction: discord.Interaction) -> GameSession | None:
    return self.bot.sessions.get(interaction.channel_id)
```

Commands that require an active session check this and respond ephemeral if None.

### 4.1 Session Cog (`bot/cogs/session.py`)

#### `/start_campaign theme:str players:str`

1. Parse `players` string to extract mentioned user IDs
2. Create `Campaign` (UUID, name from theme, player_names)
3. Load or create `GuildConfig` for category name
4. Call `channel_manager.create_session_channel(guild, theme, player_members, bot_member, category)`
5. Save `Campaign` to DB via `CampaignRepository`
6. Save `CampaignChannelRow` mapping
7. Create `GameSession` (empty characters — players use `/create_character` next)
8. Initialize AI services on session
9. If Ollama available: use `WorldGenerator` to create initial `Location`, save to DB
10. Store session in `bot.sessions[channel.id]`
11. Post welcome embed in new channel (narrative_embed with location description)
12. Respond in original channel: "Campagne lancee dans #campagne-xxx"

#### `/resume`

1. Look up `CampaignChannelRow` for current channel
2. Load `Campaign` from DB
3. Load all `PlayerCharacterRow` for campaign
4. Load current `Location` from DB
5. Create `GameSession` with loaded state
6. Initialize AI services
7. Store in `bot.sessions`
8. Post "Session resumed" message

#### `/save`

1. Get session from `bot.sessions`
2. For each player in session: update `PlayerCharacterRow`
3. Update `Campaign` (interaction_count, current_location)
4. Respond ephemeral: "Partie sauvegardee"

#### `/end_campaign`

1. Call `/save` logic
2. Call `channel_manager.archive_channel(channel, guild)`
3. Remove from `bot.sessions`
4. Post summary message before archiving

#### `/settings category:str`

1. Requires `manage_channels` permission (`@app_commands.checks.has_permissions(manage_channels=True)`)
2. Upsert `GuildConfig` with new category name
3. Respond ephemeral: "Category mise a jour"

### 4.2 Character Cog (`bot/cogs/character.py`)

#### `/create_character`

Multi-step flow:
1. Check: session exists, user doesn't already have a character
2. Send ephemeral message with `CharacterCreateView` (3 select menus: race, class, alignment)
3. User selects race → class → alignment
4. On final selection: open `CharacterNameModal` (TextInput for name)
5. On modal submit:
   - `roll_ability_scores()` → `apply_racial_bonuses(scores, race)`
   - `create_character(name, race, char_class, scores, alignment)`
   - Create `Inventory` (empty, 0 gold)
   - `create_spellcaster_state(char_class, level)` (may be None)
   - Save to `PlayerCharacterRow`
   - Add to `GameSession`
   - Respond with character_embed showing the new character + rolled stats

#### `/character public:bool=False`

1. Get character from session
2. Build `character_embed`
3. Respond `ephemeral=(not public)`

#### `/level_up public:bool=False`

1. Get character from session
2. `check_level_up(character)` — if False, respond "Not enough XP"
3. `level_up(character)` — updates HP, proficiency
4. Update session + DB
5. Respond with updated character_embed

### 4.3 Inventory Cog (`bot/cogs/inventory.py`)

#### `/inventory public:bool=False`

1. Get inventory from session
2. Build `inventory_embed(inventory, character)`
3. Respond `ephemeral=(not public)`

#### `/equip item:str slot:str`

1. Validate item exists in inventory, slot is valid `EquipmentSlot`
2. Call `equip_item(inventory, item_name, slot)`
3. Recompute AC if armor/shield changed
4. Update session
5. Respond ephemeral with result

#### `/unequip slot:str`

1. Validate slot has item
2. Call `unequip_item(inventory, slot)`
3. Recompute AC
4. Update session
5. Respond ephemeral

#### `/use_item item:str`

1. Validate item exists
2. Call `remove_item(inventory, item_name)`
3. Apply effect if applicable (e.g., healing potion → `apply_healing`)
4. Update session
5. Respond ephemeral with result

### 4.4 Rolls Cog (`bot/cogs/rolls.py`)

#### `/roll expression:str`

1. Try `roll(expression)` from `engine.dice`
2. On success: respond **public** with `expression → [rolls] + modifier = total`
3. On error (bad expression): respond ephemeral with error message

Simplest cog — stateless, no session needed, always public.

### 4.5 Combat Cog (`bot/cogs/combat.py`)

Not triggered by slash commands. Provides methods called by other cogs (exploration encounters) or direct trigger.

#### `start_combat(channel, session, enemies: list[Combatant])`

1. Build combatant list: player Characters → Combatants (PLAYER side) + enemies (ENEMY side)
2. Each player combatant needs: Character, Inventory (for weapons), SpellcasterState
3. Call `engine.combat.start_combat(combatants)` → `CombatState`
4. Store in `session.combat_state`
5. Post `combat_embed(state)` + `CombatView(session, state)`
6. Mention active player

#### Turn Resolution (called from CombatView button callbacks)

1. Receive action (attack/spell/defend/flee) + target from views
2. Build `Action` object
3. `validate_action(action, combat_state)` → if invalid, respond ephemeral error
4. Resolve: `resolve_attack()` or `resolve_spell()` → result
5. If narrator available: assemble context + narrate → `NarrativeResult`
6. Post `narrative_embed(narrative, raw_mechanics)` + updated `combat_embed(state)`
7. `advance_turn(state)`
8. Check `is_combat_over(state)` → if yes, distribute XP, end combat
9. If not over: post new `CombatView` mentioning next player

#### Combat End

1. Calculate XP per player
2. `add_xp(character, amount)` for each surviving player
3. Check `check_level_up()` — notify if eligible
4. Clear `session.combat_state`
5. Post combat summary embed

### 4.6 Exploration Cog (`bot/cogs/exploration.py`)

All exploration commands require: active session, no active combat, AI services available.

#### `/look`

1. Get current location from session
2. If narrator available: narrate location description with context
3. List exits (connections), NPCs present, items available
4. Post narrative_embed

#### `/search target:str`

1. Check `target` in location items/NPCs/features
2. If interpreter available: interpret search intent
3. Engine resolves (may find items, trigger traps, etc.)
4. Narrate result
5. Post narrative_embed

#### `/talk npc:str`

1. Find NPC by name in current location (via `NPCRepository`)
2. If not found: respond "No NPC named X here"
3. Call `npc_agent.respond(npc, player_input="initiates conversation", context)`
4. Apply `disposition_change` to NPC
5. Update NPC in DB
6. Post NPC dialogue in narrative_embed

#### `/move direction:str`

1. Get connections from current location
2. If direction matches a connection name:
   - Load destination location from DB
   - If not in DB: generate via `WorldGenerator`
   - Update `session.current_location`
   - Update `campaign.current_location` in DB
3. If no match: respond "No path in that direction"
4. Narrate arrival at new location
5. Post narrative_embed
6. Check for random encounters (future: probability based on location type)

---

## 5. Views — Interactive Components

### 5.1 CombatView (`bot/views/combat_view.py`)

```python
class CombatView(ui.View):
    """Four combat action buttons. Only the active player can interact."""

    timeout: float = 300.0  # 5 minutes

    def __init__(self, session: GameSession, active_user_id: int):
        super().__init__(timeout=self.timeout)
        self.session = session
        self.active_user_id = active_user_id
        # Disable "Cast Spell" if not a spellcaster
        spellcaster = session.spellcasters.get(active_user_id)
        if spellcaster is None:
            self.cast_spell.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the active player can use buttons."""
        if interaction.user.id != self.active_user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour !", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Attaquer", style=discord.ButtonStyle.danger, emoji="⚔️")
    async def attack(self, interaction, button): ...
        # → Send TargetSelectView with enemy combatants

    @ui.button(label="Lancer sort", style=discord.ButtonStyle.primary, emoji="✨")
    async def cast_spell(self, interaction, button): ...
        # → Send SpellSelectView

    @ui.button(label="Defendre", style=discord.ButtonStyle.secondary, emoji="🛡️")
    async def defend(self, interaction, button): ...
        # → Resolve defend immediately

    @ui.button(label="Fuir", style=discord.ButtonStyle.secondary, emoji="🏃")
    async def flee(self, interaction, button): ...
        # → Resolve flee attempt

    async def on_timeout(self):
        """Auto-defend on timeout."""
        # Resolve defend for active player, advance turn
```

### 5.2 TargetSelectView (`bot/views/target_select.py`)

```python
class TargetSelectView(ui.View):
    """Select menu to pick a target from living combatants."""

    def __init__(self, session: GameSession, action_type: str,
                 active_user_id: int, weapon_name: str | None = None,
                 spell_name: str | None = None):
        super().__init__(timeout=60.0)
        self.session = session
        # Build options from living enemy combatants
        options = [
            discord.SelectOption(label=c.name, description=f"HP: {c.character.hp}/{c.character.max_hp}")
            for c in session.combat_state.combatants
            if c.is_alive and c.side == CombatSide.ENEMY
        ]
        self.select_target.options = options

    @ui.select(placeholder="Choisis ta cible...")
    async def select_target(self, interaction, select):
        target_name = select.values[0]
        # Resolve the action via combat cog
```

### 5.3 SpellSelectView (`bot/views/spell_select.py`)

```python
class SpellSelectView(ui.View):
    """Select menu to pick a spell from known spells with available slots."""

    def __init__(self, session: GameSession, active_user_id: int):
        super().__init__(timeout=60.0)
        spellcaster = session.spellcasters[active_user_id]
        # Build options from castable spells
        options = [
            discord.SelectOption(
                label=spell.name,
                description=f"Lvl {spell.level} — {spell.damage_dice or spell.healing_dice}"
            )
            for spell in spellcaster.spells_known
            if can_cast_spell(spellcaster, spell)
        ]
        self.select_spell.options = options

    @ui.select(placeholder="Choisis ton sort...")
    async def select_spell(self, interaction, select):
        spell_name = select.values[0]
        # → Send TargetSelectView if spell needs a target
        # → Resolve directly if self-only spell
```

### 5.4 CharacterCreateView (`bot/views/character_create_view.py`)

```python
class CharacterCreateView(ui.View):
    """Multi-step character creation: race → class → alignment → name modal."""

    def __init__(self, user_id: int):
        super().__init__(timeout=120.0)
        self.user_id = user_id
        self.race: Race | None = None
        self.char_class: CharacterClass | None = None
        self.alignment: Alignment | None = None
        # Initially only race select is enabled
        self.select_class.disabled = True
        self.select_alignment.disabled = True

    @ui.select(placeholder="Choisis ta race...", options=[
        discord.SelectOption(label=r.value, value=r.value) for r in Race
    ])
    async def select_race(self, interaction, select): ...
        # Store race, enable class select, update message

    @ui.select(placeholder="Choisis ta classe...", options=[
        discord.SelectOption(label=c.value, value=c.value) for c in CharacterClass
    ])
    async def select_class(self, interaction, select): ...
        # Store class, enable alignment select, update message

    @ui.select(placeholder="Choisis ton alignement...", options=[
        discord.SelectOption(label=a.value, value=a.value) for a in Alignment
    ])
    async def select_alignment(self, interaction, select): ...
        # Store alignment, send CharacterNameModal


class CharacterNameModal(ui.Modal, title="Nom du personnage"):
    name = ui.TextInput(label="Nom", placeholder="Ex: Thorin", min_length=1, max_length=50)

    async def on_submit(self, interaction: discord.Interaction): ...
        # Roll stats, create character, save, respond with embed
```

---

## 6. Embeds

All embed builders are pure functions: domain model in → `discord.Embed` out.

### 6.1 Character Embed (`bot/embeds/character_embed.py`)

```python
def build_character_embed(character: Character) -> discord.Embed:
```

| Field | Content |
|-------|---------|
| Title | `{name} — {race} {class} (Niv. {level})` |
| Color | Class-based color (Fighter=red, Wizard=blue, etc.) |
| Ability Scores | Inline fields: `STR: 16 (+3)`, `DEX: 14 (+2)`, etc. |
| Combat Stats | `HP: {hp}/{max_hp}`, `AC: {ac}`, `Proficiency: +{prof}` |
| Saving Throws | List of proficient saves |
| Footer | `XP: {xp}/{next_threshold} — {class} Hit Die: {hit_die}` |

### 6.2 Inventory Embed (`bot/embeds/inventory_embed.py`)

```python
def build_inventory_embed(inventory: Inventory, character: Character) -> discord.Embed:
```

| Field | Content |
|-------|---------|
| Title | `Inventaire de {name}` |
| Gold | `{gold} po` |
| Weight | `{current}/{capacity} lb` (warning if encumbered) |
| Equipped | One line per slot: `Main Hand: Longsword (1d8 slashing)` |
| Attuned | `{count}/3 — {item names}` |
| Backpack | List of unequipped items (name × quantity) |

### 6.3 Combat Embed (`bot/embeds/combat_embed.py`)

```python
def build_combat_embed(combat_state: CombatState) -> discord.Embed:
```

| Field | Content |
|-------|---------|
| Title | `Combat — Round {round_number}` |
| Color | Red |
| Initiative Order | Ordered list: `> Thorin (18) — 24/24 HP` (arrow on active) |
| HP Display | Text bar: `[████████░░] 24/30` |
| Conditions | Active conditions per combatant |
| Footer | `Tour de: {active_combatant_name}` |

### 6.4 Narrative Embed (`bot/embeds/narrative_embed.py`)

```python
def build_narrative_embed(narrative: str, mechanics: str, tone: str = "dramatic") -> discord.Embed:
```

| Field | Content |
|-------|---------|
| Description | Narrative text (LLM-generated or fallback) |
| Mechanics | Inline field with raw dice/damage/effects |
| Color | Tone-based (dramatic=gold, tense=red, humorous=green, somber=purple) |

---

## 7. AI Integration Pattern

### 7.1 Narration Helper

Shared across cogs that need narration:

```python
async def narrate_action(session: GameSession, action_result_text: str,
                         player_input: str) -> tuple[str, str]:
    """Return (narrative, tone). Falls back to raw text if AI unavailable."""
    if session.narrator is None:
        return action_result_text, "dramatic"
    try:
        context = session.context_assembler.assemble(
            campaign_id=session.campaign.id,
            player_input=player_input,
        )
        result = session.narrator.narrate(action_result_text, context)
        return result.narrative, result.tone
    except OllamaUnavailableError:
        return action_result_text, "dramatic"
```

### 7.2 Context Assembler on Session

The `ContextAssembler` is created when the session is loaded/created:

```python
from memory.context_assembler import ContextAssembler
from memory.semantic import SemanticMemory

def create_context_assembler(db_session, campaign_id: str) -> ContextAssembler:
    semantic = SemanticMemory(campaign_id=campaign_id)
    return ContextAssembler(session=db_session, semantic_memory=semantic)
```

The assembler uses DB session for Layers 1-3 (state, sliding window, summaries) and ChromaDB for Layer 4 (semantic RAG). It is stored on `GameSession` and recreated on `/resume`.

---

## 8. Modified Files

### `bot/bot.py`
- Add `sessions: dict[int, GameSession] = {}` to `RealmBot.__init__`
- Add `get_session(channel_id)` method
- Uncomment and populate `EXTENSIONS` list

### `db/models.py`
- Add `PlayerCharacterRow` (imports: `BigInteger`, `Text`)
- Add `CampaignChannelRow`

### `db/mappers.py`
- Add 4 mapper functions for new tables

### `db/repositories/__init__.py`
- Export `PlayerCharacterRepository`, `CampaignChannelRepository`

---

## 9. New Files Summary

| File | Purpose |
|------|---------|
| `bot/game_session.py` | `GameSession` dataclass + `create_ai_services()` |
| `bot/cogs/__init__.py` | Empty (package marker) |
| `bot/cogs/session.py` | `/start_campaign`, `/resume`, `/save`, `/end_campaign`, `/settings` |
| `bot/cogs/character.py` | `/create_character`, `/character`, `/level_up` |
| `bot/cogs/inventory.py` | `/inventory`, `/equip`, `/unequip`, `/use_item` |
| `bot/cogs/rolls.py` | `/roll` |
| `bot/cogs/combat.py` | Combat lifecycle: start, resolve turns, end |
| `bot/cogs/exploration.py` | `/look`, `/search`, `/talk`, `/move` |
| `bot/views/__init__.py` | Empty (package marker) |
| `bot/views/combat_view.py` | Attack/Cast/Defend/Flee buttons |
| `bot/views/target_select.py` | Target selection dropdown |
| `bot/views/spell_select.py` | Spell selection dropdown |
| `bot/views/character_create_view.py` | Race/class/alignment selects + name modal |
| `bot/embeds/__init__.py` | Empty (package marker) |
| `bot/embeds/character_embed.py` | Character sheet embed builder |
| `bot/embeds/inventory_embed.py` | Inventory embed builder |
| `bot/embeds/combat_embed.py` | Combat status embed builder |
| `bot/embeds/narrative_embed.py` | Narrative + mechanics embed builder |
| `db/repositories/player_character_repo.py` | PlayerCharacterRepository |
| `db/repositories/campaign_channel_repo.py` | CampaignChannelRepository |

---

## 10. Extension Loading Order

```python
EXTENSIONS: list[str] = [
    "bot.cogs.rolls",        # Stateless, no dependencies
    "bot.cogs.session",      # Creates/loads sessions
    "bot.cogs.character",    # Requires session
    "bot.cogs.inventory",    # Requires session + character
    "bot.cogs.combat",       # Requires session + character
    "bot.cogs.exploration",  # Requires session + AI
]
```

Each cog file has a `setup(bot)` function at module level:
```python
async def setup(bot: RealmBot) -> None:
    await bot.add_cog(SessionCog(bot))
```

---

## 11. Testing Strategy

### Unit Tests (no Discord, no Ollama)
- **Embeds**: Build embed from fixture data → assert title, fields, color
- **Repositories**: CRUD with in-memory SQLite (same pattern as existing repos)
- **Mappers**: Round-trip equality tests
- **GameSession**: Create, add character, save/load cycle

### Integration Tests (mocked Discord)
- **Cogs**: Mock `interaction`, `interaction.response`, `interaction.followup`
- Test each command path: happy path + error cases
- Mock `bot.sessions` and `bot.db_factory`

### View Tests
- Test `interaction_check` (active player guard)
- Test button callbacks with mocked interaction
- Test timeout behavior

### Quality Gates
- `uv run pytest` — all tests pass
- `uv run ruff check .` — clean
- `uv run mypy .` — clean on source files
- Target: ~80+ new tests

---

## 12. Data Flow Diagrams

### /start_campaign
```
User: /start_campaign theme:"Dark Forest" players:@Alice @Bob
  → session.py: parse mentions, create Campaign(UUID)
  → CampaignRepository.save(campaign)
  → channel_manager.create_session_channel(guild, theme, [alice, bob], bot)
  → CampaignChannelRepository.save(channel.id, campaign.id, guild.id)
  → WorldGenerator.generate(context, "forest") → Location
  → LocationRepository.save(location, campaign.id)
  → GameSession(campaign, ...) → bot.sessions[channel.id]
  → channel.send(welcome_embed)
```

### Combat Turn
```
CombatView: Player clicks "Attaquer"
  → TargetSelectView: Player selects "Goblin"
  → combat.py: build Action(ATTACK, actor, target, weapon)
  → validate_action(action, combat_state) → OK
  → resolve_attack(attacker, defender, weapon) → AttackResult
  → narrate_action(session, result_text) → (narrative, tone)
  → channel.send(narrative_embed + combat_embed)
  → advance_turn(combat_state)
  → New CombatView for next player
```

### /create_character
```
User: /create_character
  → character.py: send CharacterCreateView (ephemeral)
  → User selects: Elf → Wizard → Chaotic Good
  → CharacterNameModal opens
  → User types: "Elara"
  → roll_ability_scores() → AbilityScores
  → apply_racial_bonuses(scores, Elf) → +2 DEX
  → create_character("Elara", Elf, Wizard, scores, Chaotic Good) → Character
  → create_spellcaster_state(Wizard, 1) → SpellcasterState
  → Inventory(items=[], equipped={}, attuned=[], gold=0)
  → PlayerCharacterRepository.save(user_id, campaign_id, char, inv, spell)
  → session.characters[user_id] = character
  → respond(character_embed)
```
