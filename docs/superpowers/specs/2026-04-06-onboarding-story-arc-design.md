# Design Spec: Campaign Onboarding & Story Arc Generation

**Date**: 2026-04-06
**Status**: Draft
**Approach**: B — Orchestrator Pattern (CampaignLauncher)

---

## Context

When `/start_campaign` is executed, the bot creates a Discord channel and immediately drops players in with a narrator message. Two critical gaps exist:

1. **No onboarding**: Players receive zero guidance — no character creation instructions, no explanation of available commands, no starter equipment. Characters start with empty inventories, making combat immediately fail ("Aucune arme equipee !").

2. **No story arc**: The AI generates a starting location but has no overarching plot, encounters, or narrative thread. The Narrator operates beat-by-beat with no sense of where the story is going. A real Game Master has a planned story outline from the start.

This spec adds an interactive onboarding phase and background story arc generation so that campaigns start properly and the AI has narrative direction.

---

## 1. StoryArc Model

**New file: `world/story_arc.py`**

A `StoryArc` is a structured narrative plan for the entire campaign, generated once at campaign start.

```python
class StoryBeat(BaseModel):
    """One narrative step in the campaign arc."""
    beat_number: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    description: str                    # 2-3 sentences describing what happens
    location_hint: str                  # type of location (forest, temple, dungeon...)
    npc_names: list[str] = Field(default_factory=list)
    encounter_type: Literal["social", "combat", "exploration", "puzzle", "boss"]
    is_twist: bool = False

class StoryArc(BaseModel):
    """Complete narrative arc for a campaign."""
    campaign_id: str
    theme: str
    premise: str = Field(min_length=10)   # 2-3 sentence campaign summary
    beats: list[StoryBeat] = Field(min_length=8, max_length=20)
    current_beat_index: int = Field(default=0, ge=0)
    villain_name: str
    villain_motivation: str
```

**Pure function for beat advancement:**
```python
def advance_beat(arc: StoryArc) -> StoryArc:
    """Return a new StoryArc with current_beat_index incremented."""
    if arc.current_beat_index >= len(arc.beats) - 1:
        return arc  # already at final beat
    return arc.model_copy(update={"current_beat_index": arc.current_beat_index + 1})
```

Beat advancement is a code decision (triggered by exploration/combat cogs or StoryDirector), not an LLM decision.

---

## 2. ArcGenerator (AI Module)

**New file: `ai/arc_generator.py`**

Follows the exact pattern of `WorldGenerator` and `QuestGenerator`:

```python
class ArcGenerator:
    def __init__(self, client: OllamaClient) -> None:
        self._client = client
        self._system_prompt = _load_prompt("system_arc_generator.txt")

    def generate(self, theme: str, player_count: int) -> StoryArc: ...
```

- **Model**: `qwen3.5:9b` (same as WorldGenerator, Narrator)
- **Temperature**: 0.8 (creative generation)
- **Output**: `response_format=json_object` parsed into `StoryArc`
- **Execution**: Called via `asyncio.to_thread()` (blocking Ollama call)

**New file: `ai/prompts/system_arc_generator.txt`**

System prompt instructs the LLM to:
- Generate 10-15 story beats following a dramatic structure (introduction, rising action, climax, falling action, resolution)
- Create a villain with clear motivation
- Vary encounter types across beats (mix of combat, social, exploration, puzzle)
- Include at least one twist beat (`is_twist: true`)
- End with a boss encounter
- Keep descriptions flexible enough to adapt to player choices
- Return valid JSON matching the StoryArc schema

---

## 3. Starter Gear Kits

**New file: `engine/starter_gear.py`** (pure Python, no LLM — follows engine/ convention)

```python
class StarterKit(BaseModel):
    """A pre-built equipment set for a character class."""
    name: str
    description: str
    items: list[str]       # names matching keys in ITEM_CATALOG
    gold: int = Field(default=10, ge=0)

STARTER_KITS: dict[CharacterClass, list[StarterKit]]

def get_starter_kits(char_class: CharacterClass) -> list[StarterKit]: ...
def apply_starter_kit(kit: StarterKit, inventory: Inventory) -> None: ...
```

`apply_starter_kit` uses existing `add_item()` and `equip()` functions from `engine/inventory.py`. It auto-equips the first weapon and first armor in the kit.

### Kit Definitions

| Class | Kit 1 | Kit 2 | Kit 3 |
|-------|-------|-------|-------|
| **Fighter** | Sword & Shield (Longsword, Shield, Chain Mail, 10gp) | Two-Handed Warrior (Greataxe, Chain Mail, 10gp) | Archer (Longbow, Leather, Shortsword, 15gp) |
| **Wizard** | Classic Arcanist (Quarterstaff, Padded, 15gp) | War Scholar (Dagger, Leather, 20gp) | — |
| **Rogue** | Shadow Blade (Shortsword, Dagger, Leather, 15gp) | Scout (Shortbow, Dagger, Leather, 10gp) | — |
| **Cleric** | Battle Priest (Longsword, Chain Mail, Shield, 5gp) | Healer (Quarterstaff, Leather, 15gp) | — |
| **Ranger** | Woodland Archer (Longbow, Shortsword, Leather, 10gp) | Dual Wielder (Shortsword, Dagger, Leather, 15gp) | — |
| **Barbarian** | Berserker (Greataxe, Leather, 10gp) | Savage Fighter (Handaxe x2, Leather, 15gp) | — |

All item names reference existing entries in `ITEM_CATALOG` (verified: Longsword, Greataxe, Longbow, Shortbow, Shortsword, Dagger, Handaxe, Quarterstaff, Shield, Chain Mail, Leather, Padded).

---

## 4. StarterGearView (Discord View)

**New file: `bot/views/starter_gear_view.py`**

A `discord.ui.View` with 2-3 buttons (one per kit for the player's class). Sent ephemerally to each player after character creation.

**Flow:**
1. Player completes `CharacterCreateView` → callback notifies `CampaignLauncher`
2. Launcher sends `StarterGearView` ephemerally to that player
3. Player clicks a kit button
4. Bot calls `apply_starter_kit(kit, inventory)` — populates inventory and auto-equips main weapon + armor
5. Bot sends an ephemeral confirmation embed showing the kit contents
6. Callback notifies `CampaignLauncher` that this player's gear is done

**Timeout**: 300 seconds (5 minutes). If expired, auto-apply Kit 1 as default.

---

## 5. CampaignLauncher (Orchestrator)

**New file: `bot/campaign_launcher.py`**

A temporary coordinator that manages the onboarding phase. Exists only during the 2-5 minute window between `/start_campaign` and the first narrative.

```python
class PlayerProgress(StrEnum):
    PENDING = "pending"
    CHARACTER_DONE = "character_done"
    GEAR_DONE = "gear_done"

@dataclass
class CampaignLauncher:
    campaign: Campaign
    channel: discord.TextChannel
    player_ids: list[int]
    player_progress: dict[int, PlayerProgress]  # user_id -> progress
    arc_task: asyncio.Task | None = None
    story_arc: StoryArc | None = None
    db_session_factory: Callable  # for DB access
```

### Onboarding Flow (messages sent in the campaign channel)

**Step 1 — Welcome embed** (sent immediately after channel creation):
```
Title: "Campagne: {theme}"
Description:
  "Bienvenue, aventuriers ! Avant de commencer votre quête, chaque joueur
   doit créer son personnage et choisir son équipement de départ.

   Joueurs attendus : @Player1, @Player2, @Player3

   Cliquez sur le bouton ci-dessous pour commencer !"
```
Followed by a `StartOnboardingView` with a single button: "Créer mon personnage"

**Step 2 — Character creation** (per player, ephemeral):
- Player clicks button → receives existing `CharacterCreateView` ephemerally
- Uses current progressive flow: Race → Class → Alignment → Name modal
- On completion, the view's `on_complete` callback saves the character to DB and calls `launcher.on_character_created(user_id, character)`
- The `CharacterCreateView` gains an optional `on_complete: Callable | None` parameter (default `None` for backward compatibility with `/create_character`)
- Launcher marks player as `CHARACTER_DONE`

**Step 3 — Starter gear selection** (per player, ephemeral):
- Launcher sends `StarterGearView` ephemerally based on the player's chosen class
- Player picks a kit → inventory populated and equipment auto-equipped
- Launcher marks player as `GEAR_DONE`
- Public message in channel: "**Thorin** (Nain Guerrier) est prêt ! [kit summary]"

**Step 4 — Campaign launch** (when all conditions met):
- All players are `GEAR_DONE` AND `arc_task` is done
- Launcher creates the `GameSession` (moves from `bot.launchers` to `bot.sessions`)
- Sends the opening narrative based on StoryArc beat #1 + starting location
- Deletes itself from `bot.launchers`

### Key methods

```python
async def on_character_created(self, user_id: int, character: Character) -> None:
    """Called when a player completes character creation."""

async def on_gear_selected(self, user_id: int, kit: StarterKit) -> None:
    """Called when a player selects a starter gear kit."""

async def _check_ready(self) -> None:
    """Check if all players are GEAR_DONE and arc is generated. If so, launch."""

async def _launch_campaign(self) -> None:
    """Create GameSession, send opening narrative, clean up launcher."""
```

### In-memory storage

`bot.launchers: dict[int, CampaignLauncher]` (channel_id → launcher) — added to `RealmBot` alongside existing `bot.sessions`.

### Edge cases

- **Bot restart during onboarding**: Campaign and channel exist in DB. Players use `/resume` then `/create_character` manually. Story arc regenerated. This is acceptable for a 2-5 min window.
- **Player doesn't create character**: After 10 minutes, launcher sends a reminder. After 20 minutes, auto-cancels with a message explaining the campaign was aborted.
- **Ollama unavailable at start**: `start_background_tasks()` catches the error, logs a WARNING, and returns. `_arc_task` stays `None`, so `_check_ready()` treats arc as done. Campaign launches without arc — degraded but functional.
- **Ollama slow / hanging**: When all players are `GEAR_DONE` but the arc task is still running, the launcher sends a feedback message ("Generation de l'histoire en cours...") and waits up to `ARC_WAIT_TIMEOUT` (180s). If the timeout expires, the arc task is cancelled and the campaign launches without arc.
- **Arc generation fails mid-flight**: `_on_arc_done` catches the exception, logs a WARNING, and calls `_check_ready()`. The campaign launches without arc.

### Timeout and fallback

| Timeout | Value | Effect |
|---------|-------|--------|
| OllamaClient HTTP timeout | 120s (connect: 10s) | Ollama call raises `APITimeoutError` → caught as generation failure |
| Arc wait timeout | 180s | If arc not done 180s after all players ready → cancel and launch without arc |

### Observability

All onboarding steps are logged to `realm.log`:

```
ONBOARD click user=cocolng campaign=abc-123
ONBOARD character user=cocolng name=Thorin race=Dwarf class=Fighter campaign=abc-123
ONBOARD gear user=cocolng kit=Sword & Shield campaign=abc-123
ONBOARD check all_ready=True arc_done=False campaign=abc-123
LAUNCH starting campaign=abc-123
LAUNCH campaign=abc-123 players=2 arc_beats=12 location=Dark Forest
```

All Discord view interaction errors are logged via `LoggedView.on_error()` (base class in `bot/views/__init__.py`) instead of going to stderr.

---

## 6. StoryArc Persistence (DB Layer)

**New SQLAlchemy model in `db/models.py`:**

```python
class StoryArcRow(Base):
    __tablename__ = "story_arcs"
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True
    )
    arc_json: Mapped[str] = mapped_column(Text, nullable=False)
```

One row per campaign (1:1 relationship). Stored as JSON-serialized `StoryArc.model_dump_json()`.

**Mappers in `db/mappers.py`:**
- `story_arc_to_row(arc: StoryArc) -> StoryArcRow`
- `story_arc_from_row(row: StoryArcRow) -> StoryArc`

**Repository in `db/repositories/story_arc_repo.py`:**
- `save(arc: StoryArc) -> None`
- `get_by_campaign(campaign_id: str) -> StoryArc | None`
- `update(arc: StoryArc) -> None` (for beat advancement)

---

## 7. ContextAssembler Integration

### Changes to `memory/models.py`

Add three optional fields to `GameStateSummary`:

```python
current_story_beat: str = ""     # "Beat 3: The Ambush — bandits attack on the forest road"
upcoming_story_beat: str = ""    # "Beat 4: The Hidden Cave"
villain_context: str = ""        # "Villain: Morag the Shadow — seeks the ancient artifact"
```

### Changes to `memory/state.py`

`StateBuilder.build()` gains an optional parameter: `story_arc: StoryArc | None = None`.

If present, it populates the three new fields from the arc's current and next beats.

`StateBuilder.render()` appends a `[STORY ARC]` section after active quests:

```
[STORY ARC]
Current: The Ambush — Bandits attack the party on the forest road
Next: The Hidden Cave
Villain: Morag the Shadow — seeks the ancient artifact of power
```

**Token cost**: ~40-60 tokens. Well within Layer 1's 450-token budget.

### Effect on Narrator

The Narrator's system prompt already instructs it to describe mechanical outcomes in context. With the story arc injected into Layer 1, the Narrator naturally weaves its descriptions around the current beat's themes and location. No changes to the Narrator are needed.

---

## 8. Changes to `/start_campaign` Flow

### Current flow (session.py lines 52-170):
1. Parse players, validate
2. Create Campaign + save to DB
3. Create Discord channel
4. Save channel mapping
5. Create GameSession + AI services
6. Generate starting location
7. Send narrative embed

### New flow:
1. Parse players, validate (unchanged)
2. Create Campaign + save to DB (unchanged)
3. Create Discord channel (unchanged)
4. Save channel mapping (unchanged)
5. Create `CampaignLauncher` instead of `GameSession`
6. Store in `bot.launchers[channel_id]`
7. **Background**: launch `arc_generator.generate(theme, len(players))` as `asyncio.Task`
8. **Background**: launch `world_generator.generate(...)` for starting location (unchanged, concurrent with arc)
9. Send welcome embed + `StartOnboardingView` in channel
10. Reply to invoker: "Campagne lancée dans {channel.mention} !"

The `GameSession` is now created later, by `CampaignLauncher._launch_campaign()`, once all players are ready and the arc is generated.

### Changes to `/resume`

Add loading of `StoryArc` from DB via `StoryArcRepository.get_by_campaign()`. Set `session.story_arc` on the `GameSession`.

### Changes to `_persist_session`

Save/update `StoryArc` to DB if present on the session.

---

## 9. Changes to `GameSession`

Add one field:

```python
story_arc: StoryArc | None = None
```

Import `StoryArc` from `world/story_arc`.

---

## 10. Files Modified and Created

### New files
| File | Purpose |
|------|---------|
| `world/story_arc.py` | StoryArc + StoryBeat Pydantic models, `advance_beat()` |
| `engine/starter_gear.py` | StarterKit model, STARTER_KITS data, `get_starter_kits()`, `apply_starter_kit()` |
| `ai/arc_generator.py` | ArcGenerator class |
| `ai/prompts/system_arc_generator.txt` | System prompt for arc generation |
| `bot/campaign_launcher.py` | CampaignLauncher orchestrator |
| `bot/views/starter_gear_view.py` | StarterGearView (kit selection buttons) |
| `bot/views/start_onboarding_view.py` | StartOnboardingView (single "Create Character" button) |
| `db/repositories/story_arc_repo.py` | StoryArcRepository |
| `tests/test_starter_gear.py` | Tests for engine/starter_gear.py |
| `tests/test_story_arc.py` | Tests for world/story_arc.py |
| `tests/test_arc_generator.py` | Tests for ai/arc_generator.py |

### Modified files
| File | Change | Scope |
|------|--------|-------|
| `bot/cogs/session.py` | Refactor `start_campaign` to use CampaignLauncher; update `resume` and `_persist_session` | Medium |
| `bot/game_session.py` | Add `story_arc: StoryArc \| None = None` field | 1 line |
| `bot/bot.py` | Add `launchers: dict[int, CampaignLauncher] = {}` to RealmBot | 2 lines |
| `memory/models.py` | Add 3 fields to `GameStateSummary` | 3 lines |
| `memory/state.py` | Add `story_arc` param to `build()`, extend `render()` | ~25 lines |
| `db/models.py` | Add `StoryArcRow` | ~8 lines |
| `db/mappers.py` | Add `story_arc_to_row`, `story_arc_from_row` | ~15 lines |
| `db/database.py` | Migration: create `story_arcs` table | Small |

---

## 11. Verification Plan

### Unit tests
1. `tests/test_story_arc.py`:
   - StoryArc model validates min/max beats, beat_number range, enum values
   - `advance_beat()` increments correctly, is idempotent at last beat
2. `tests/test_starter_gear.py`:
   - All 6 classes return 2-3 kits
   - All item names in kits exist in `ITEM_CATALOG`
   - `apply_starter_kit()` populates inventory with correct items
   - `apply_starter_kit()` auto-equips weapon in MAIN_HAND and armor in ARMOR slot
3. `tests/test_arc_generator.py`:
   - Mock `OllamaClient.chat_json()` → valid StoryArc
   - Missing fields → sensible defaults or validation error
   - Ollama unavailable → `OllamaUnavailableError`

### Integration tests
4. `tests/test_story_arc_repo.py`:
   - Save/load roundtrip with in-memory SQLite
   - Update beat index persists correctly
5. `tests/test_state.py` (extend existing):
   - `build()` with `story_arc` populates new fields
   - `render()` includes `[STORY ARC]` section
   - `build()` without `story_arc` → no arc section (backward compatible)

### Manual verification
6. Start a campaign with 2+ test players
7. Verify welcome embed appears with "Create Character" button
8. Create characters for each player, verify starter gear view appears
9. Select gear kits, verify public "ready" messages
10. Verify campaign launches with opening narrative referencing the first story beat
11. Check that the Narrator's output reflects story arc context

### Quality gates
- `uv run pytest` — all tests pass
- `uv run ruff check .` — no linting errors
- `uv run mypy .` — no type errors
