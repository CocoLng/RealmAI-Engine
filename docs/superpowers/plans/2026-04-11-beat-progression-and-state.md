# Beat Progression, Environment State & Question Detection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken gameplay loop where players cannot progress through story beats because (1) questions are treated as actions, (2) environment state doesn't persist, and (3) beat advancement only checks location names.

**Architecture:** Add `QUESTION` action type with pipeline short-circuit and blue state embed. Extend `Location` with `state_flags` and `unlocked_exits`. Add `CompletionTrigger` and `BeatEffects` to `StoryBeat` with deterministic trigger checking + LLM fallback for creative solutions.

**Tech Stack:** Pydantic v2, SQLAlchemy + SQLite, discord.py embeds, Ollama (qwen3.5:4b for fallback judge)

**Spec:** `docs/superpowers/specs/2026-04-11-beat-progression-and-state-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `world/story_arc.py` | Modify | Add `CompletionTrigger`, `BeatEffects`, new fields on `StoryBeat` |
| `world/location.py` | Modify | Add `state_flags`, `unlocked_exits` |
| `engine/validators.py` | Modify | Add `QUESTION` to `ActionType` + `EXPLORATION_ACTION_TYPES` |
| `ai/prompts/system_interpreter.txt` | Modify | Add `Question` category |
| `ai/prompts/system_arc_generator.txt` | Modify | Add `completion_trigger` + `on_complete` to beat schema |
| `ai/prompts/system_narrator.txt` | Modify | Add beat context awareness |
| `ai/entity_resolver.py` | Modify | Handle `QUESTION` as `not_applicable` + MOVE checks `unlocked_exits` |
| `ai/arc_generator.py` | Modify | No code change needed — StoryArc validation handles new fields |
| `bot/action_pipeline.py` | Modify | Question short-circuit, `_check_beat_completion()`, `_apply_beat_effects()`, LLM fallback |
| `bot/game_session.py` | Modify | Update `advance_beat_if_ready()` for trigger-based advancement |
| `bot/scene_hydration.py` | Modify | Include `state_flags`, `unlocked_exits`, beat info in narrator context |
| `bot/embeds/narrative_embed.py` | Modify | Add `build_state_embed()` |
| `bot/cogs/action_handler.py` | Modify | Route question results to `build_state_embed()` |
| `db/models.py` | Modify | Add `state_flags`, `unlocked_exits` columns to `LocationRow` |
| `db/mappers.py` | Modify | Map new Location fields |
| `db/database.py` | Modify | Add V2→V3 migration |
| `db/repositories/location_repo.py` | Modify | Persist new fields in `update()` |
| `tests/ai/test_arc_generator.py` | Modify | Test new beat fields |
| `tests/bot/test_action_pipeline.py` | Modify | Test question short-circuit, beat completion |
| `tests/test_embeds.py` | Modify | Test `build_state_embed()` |
| `tests/test_db_repos.py` | Modify | Test Location persistence with new fields |

---

### Task 1: Domain Models — `CompletionTrigger`, `BeatEffects`, extended `StoryBeat`

**Files:**
- Modify: `world/story_arc.py:1-44`
- Test: `tests/ai/test_arc_generator.py`

- [ ] **Step 1: Write failing test for CompletionTrigger and BeatEffects**

In `tests/ai/test_arc_generator.py`, add a test class at the end of the file:

```python
class TestBeatCompletionModels:
    """Tests for CompletionTrigger and BeatEffects on StoryBeat."""

    def test_story_beat_with_completion_trigger(self):
        from world.story_arc import CompletionTrigger, BeatEffects

        beat = StoryBeat(
            beat_number=1,
            title="The Wall That Sighs",
            description="Balance the mechanism.",
            location_hint="The bone barrier",
            npc_names=["Barnabé"],
            encounter_type="puzzle",
            completion_trigger=CompletionTrigger(
                type="interact",
                target="Le levier de l'Échiquier",
            ),
            on_complete=BeatEffects(
                unlock_exits=["La cour intérieure"],
                state_flags={"breach_open": True},
                narrative_hint="A breach opens in the bone wall.",
            ),
        )
        assert beat.completion_trigger is not None
        assert beat.completion_trigger.type == "interact"
        assert beat.completion_trigger.target == "Le levier de l'Échiquier"
        assert beat.on_complete.unlock_exits == ["La cour intérieure"]
        assert beat.on_complete.state_flags == {"breach_open": True}

    def test_story_beat_without_trigger_defaults_none(self):
        beat = StoryBeat(
            beat_number=1,
            title="Arrival",
            description="Arrive at the village.",
            location_hint="Village entrance",
            npc_names=[],
            encounter_type="exploration",
        )
        assert beat.completion_trigger is None
        assert beat.on_complete.unlock_exits == []

    def test_completion_trigger_types(self):
        from world.story_arc import CompletionTrigger

        for t in ("interact", "defeat", "talk", "arrive", "search", "pickup"):
            trigger = CompletionTrigger(type=t, target="some target")
            assert trigger.type == t

    def test_beat_effects_serialization(self):
        from world.story_arc import BeatEffects

        effects = BeatEffects(
            unlock_exits=["Exit A"],
            add_npcs=["Guard"],
            remove_items=["Key"],
            add_items=["Reward"],
            state_flags={"door_open": True},
            narrative_hint="The door swings open.",
        )
        data = effects.model_dump()
        restored = BeatEffects.model_validate(data)
        assert restored == effects
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ai/test_arc_generator.py::TestBeatCompletionModels -v`
Expected: ImportError — `CompletionTrigger` and `BeatEffects` do not exist yet.

- [ ] **Step 3: Implement CompletionTrigger, BeatEffects, extend StoryBeat**

In `world/story_arc.py`, add the new models before `StoryBeat` and extend it:

```python
"""Story arc domain model.

Represents the narrative plan for a campaign — a sequence of story beats
from introduction through resolution.
"""

from typing import Literal

from pydantic import BaseModel, Field


class CompletionTrigger(BaseModel):
    """Deterministic condition for beat completion."""

    type: Literal["interact", "defeat", "talk", "arrive", "search", "pickup"]
    target: str


class BeatEffects(BaseModel):
    """World mutations applied when a beat is completed."""

    unlock_exits: list[str] = Field(default_factory=list)
    add_npcs: list[str] = Field(default_factory=list)
    remove_items: list[str] = Field(default_factory=list)
    add_items: list[str] = Field(default_factory=list)
    state_flags: dict[str, bool] = Field(default_factory=dict)
    narrative_hint: str = ""


class StoryBeat(BaseModel):
    """One narrative step in the campaign arc."""

    beat_number: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    description: str
    location_hint: str
    npc_names: list[str] = Field(default_factory=list)
    encounter_type: Literal["social", "combat", "exploration", "puzzle", "boss"]
    encounter_subtype: str | None = None
    is_twist: bool = False
    completion_trigger: CompletionTrigger | None = None
    on_complete: BeatEffects = Field(default_factory=BeatEffects)


class StoryArc(BaseModel):
    """Complete narrative arc for a campaign."""

    campaign_id: str
    theme: str
    premise: str = Field(min_length=10)
    beats: list[StoryBeat] = Field(min_length=8, max_length=20)
    current_beat_index: int = Field(default=0, ge=0)
    villain_name: str
    villain_motivation: str


def advance_beat(arc: StoryArc) -> StoryArc:
    """Return a new StoryArc with current_beat_index incremented.

    Idempotent at the last beat — returns unchanged arc.
    """
    if arc.current_beat_index >= len(arc.beats) - 1:
        return arc
    return arc.model_copy(update={"current_beat_index": arc.current_beat_index + 1})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ai/test_arc_generator.py::TestBeatCompletionModels -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Run full arc generator test suite**

Run: `uv run pytest tests/ai/test_arc_generator.py -v`
Expected: All existing tests still pass (new fields are optional with defaults).

- [ ] **Step 6: Commit**

```bash
git add world/story_arc.py tests/ai/test_arc_generator.py
git commit -m "feat(world): add CompletionTrigger and BeatEffects to StoryBeat"
```

---

### Task 2: Location State — `state_flags` and `unlocked_exits`

**Files:**
- Modify: `world/location.py:1-18`
- Modify: `db/models.py:54-69` (LocationRow)
- Modify: `db/mappers.py:128-150` (location_to_db, location_from_db)
- Modify: `db/database.py:88-96` (add V2→V3 migration)
- Modify: `db/repositories/location_repo.py:39-53` (update method)
- Test: `tests/test_db_repos.py`

- [ ] **Step 1: Write failing test for Location with new fields**

In `tests/test_db_repos.py`, add a test at the end of the Location test class (find the class that tests LocationRepository):

```python
def test_location_state_flags_persist(self, session, repo):
    loc = Location(
        name="Bone Barrier",
        description="A wall of bones.",
        connections=["Village"],
        state_flags={"lever_activated": True, "breach_open": True},
        unlocked_exits=["Inner Court"],
    )
    repo.save(loc, "camp-1")
    session.commit()

    loaded = repo.get_by_name("Bone Barrier", "camp-1")
    assert loaded is not None
    assert loaded.state_flags == {"lever_activated": True, "breach_open": True}
    assert loaded.unlocked_exits == ["Inner Court"]

def test_location_update_persists_state_flags(self, session, repo):
    loc = Location(name="Barrier", description="Desc", connections=["A"])
    repo.save(loc, "camp-1")
    session.commit()

    loc.state_flags["puzzle_solved"] = True
    loc.unlocked_exits.append("Secret Exit")
    repo.update(loc, "camp-1")
    session.commit()

    loaded = repo.get_by_name("Barrier", "camp-1")
    assert loaded is not None
    assert loaded.state_flags == {"puzzle_solved": True}
    assert loaded.unlocked_exits == ["Secret Exit"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db_repos.py -k "state_flags" -v`
Expected: FAIL — `state_flags` not a valid field on Location.

- [ ] **Step 3: Extend Location model**

In `world/location.py`, add the new fields:

```python
"""Location domain model.

Represents places in the game world that players can visit.
"""

from pydantic import BaseModel, Field


class Location(BaseModel):
    """A location in the game world."""

    name: str
    description: str = ""
    connections: list[str] = Field(default_factory=list)
    npcs_present: list[str] = Field(default_factory=list)
    items_available: list[str] = Field(default_factory=list)
    item_descriptions: dict[str, str] = Field(default_factory=dict)
    state_flags: dict[str, bool] = Field(default_factory=dict)
    unlocked_exits: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Add columns to LocationRow**

In `db/models.py`, add after line 69 (after `item_descriptions`):

```python
    state_flags: Mapped[dict] = mapped_column(JSON, default=dict)  # type: ignore[type-arg]
    unlocked_exits: Mapped[list] = mapped_column(JSON, default=list)  # type: ignore[type-arg]
```

- [ ] **Step 5: Update mappers**

In `db/mappers.py`, update `location_to_db` (around line 128):

```python
def location_to_db(location: Location, campaign_id: str) -> LocationRow:
    """Convert a Location domain model to a DB row."""
    return LocationRow(
        campaign_id=campaign_id,
        name=location.name,
        description=location.description,
        connections=location.connections,
        npcs_present=location.npcs_present,
        items_available=location.items_available,
        item_descriptions=location.item_descriptions,
        state_flags=location.state_flags,
        unlocked_exits=location.unlocked_exits,
    )
```

Update `location_from_db` (around line 141):

```python
def location_from_db(row: LocationRow) -> Location:
    """Convert a LocationRow to a Location domain model."""
    return Location(
        name=row.name,
        description=row.description,
        connections=list(row.connections) if row.connections else [],
        npcs_present=list(row.npcs_present) if row.npcs_present else [],
        items_available=list(row.items_available) if row.items_available else [],
        item_descriptions=dict(row.item_descriptions) if row.item_descriptions else {},
        state_flags=dict(row.state_flags) if row.state_flags else {},
        unlocked_exits=list(row.unlocked_exits) if row.unlocked_exits else [],
    )
```

- [ ] **Step 6: Add V2→V3 migration**

In `db/database.py`, add after `_migrate_v1_to_v2` (around line 92):

```python
def _migrate_v2_to_v3(raw: sqlite3.Connection) -> None:
    """V2 → V3: add state_flags and unlocked_exits to locations."""
    _add_column_if_missing(raw, "locations", "state_flags", "JSON DEFAULT '{}'")
    _add_column_if_missing(raw, "locations", "unlocked_exits", "JSON DEFAULT '[]'")
```

Update `_MIGRATIONS` list (around line 96):

```python
_MIGRATIONS = [_migrate_v0_to_v1, _migrate_v1_to_v2, _migrate_v2_to_v3]
```

- [ ] **Step 7: Update LocationRepository.update()**

In `db/repositories/location_repo.py`, add the new fields to the update method (around line 49-53):

```python
        row.description = location.description
        row.connections = location.connections  # type: ignore[assignment]
        row.npcs_present = location.npcs_present  # type: ignore[assignment]
        row.items_available = location.items_available  # type: ignore[assignment]
        row.item_descriptions = location.item_descriptions  # type: ignore[assignment]
        row.state_flags = location.state_flags  # type: ignore[assignment]
        row.unlocked_exits = location.unlocked_exits  # type: ignore[assignment]
```

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_db_repos.py -k "location" -v`
Expected: All location tests PASS including new ones.

- [ ] **Step 9: Commit**

```bash
git add world/location.py db/models.py db/mappers.py db/database.py db/repositories/location_repo.py tests/test_db_repos.py
git commit -m "feat(db): add state_flags and unlocked_exits to Location"
```

---

### Task 3: QUESTION Action Type

**Files:**
- Modify: `engine/validators.py:24-58`
- Modify: `ai/prompts/system_interpreter.txt`
- Modify: `ai/entity_resolver.py:106-113`
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Write failing test for QUESTION action type**

In `tests/bot/test_action_pipeline.py`, add a new test class at the end:

```python
class TestQuestionAction:
    """QUESTION action type short-circuits the pipeline."""

    @pytest.mark.asyncio
    async def test_question_skips_entity_resolution(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="What do I see?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="You see a cathedral.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("What do I see?", "Aldric")
        assert isinstance(result, ActionPipelineResult)
        assert result.interpreted_action.action_type == ActionType.QUESTION
        # Narrator should have received context about the scene
        assert len(narrator.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestQuestionAction -v`
Expected: FAIL — `ActionType` has no `QUESTION` member.

- [ ] **Step 3: Add QUESTION to ActionType enum**

In `engine/validators.py`, add after `IMPROVISE = "Improvise"` (line 47):

```python
    # Meta
    QUESTION = "Question"
```

Update `EXPLORATION_ACTION_TYPES` (lines 50-58) to include QUESTION:

```python
EXPLORATION_ACTION_TYPES: frozenset[ActionType] = frozenset({
    ActionType.LOOK,
    ActionType.SEARCH,
    ActionType.TALK,
    ActionType.MOVE,
    ActionType.INTERACT,
    ActionType.PICKUP,
    ActionType.IMPROVISE,
    ActionType.QUESTION,
})
```

- [ ] **Step 4: Handle QUESTION in entity resolver**

In `ai/entity_resolver.py`, add `ActionType.QUESTION` to the `not_applicable` group (around line 107-113):

```python
        if at in (
            ActionType.LOOK,
            ActionType.DEFEND,
            ActionType.FLEE,
            ActionType.IMPROVISE,
            ActionType.QUESTION,
        ):
            return ResolutionResult(status="not_applicable")
```

- [ ] **Step 5: Update interpreter prompt**

In `ai/prompts/system_interpreter.txt`, add before the "Rules:" section (after line 29):

```
- "Question"   — the player asks about the game state, requests clarification, or wants information about their environment. Examples: "What do I see?", "Are there NPCs nearby?", "Did I succeed?", "What can I interact with?", "What happened to the breach?". Do NOT classify roleplay questions directed at an NPC as Question — those are "Talk".
```

- [ ] **Step 6: Add QUESTION to validate_exploration_action**

In `engine/validators.py`, check that `validate_exploration_action()` does not reject QUESTION. Read the function body — QUESTION needs no target, no item, no destination. If the function has special checks for specific types (e.g. PICKUP requires item, MOVE requires target), ensure QUESTION falls through to the default valid path. QUESTION is similar to LOOK — it always passes validation.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestQuestionAction -v`
Expected: PASS (entity resolution returns `not_applicable`, validation passes, mechanics resolves).

Run: `uv run pytest tests/ -k "validator" -v`
Expected: All validator tests still pass.

- [ ] **Step 8: Commit**

```bash
git add engine/validators.py ai/prompts/system_interpreter.txt ai/entity_resolver.py tests/bot/test_action_pipeline.py
git commit -m "feat(engine): add QUESTION action type with interpreter + resolver support"
```

---

### Task 4: Question Pipeline Short-Circuit + State Embed

**Files:**
- Modify: `bot/action_pipeline.py:696-801` (_resolve_mechanics)
- Modify: `bot/embeds/narrative_embed.py`
- Modify: `bot/cogs/action_handler.py:290-302` (_render_success)
- Test: `tests/test_embeds.py`
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Write failing test for state embed**

In `tests/test_embeds.py`, add a new test class after `TestNarrativeEmbed`:

```python
class TestStateEmbed:
    """Tests for build_state_embed (question responses)."""

    def test_state_embed_color_is_blue(self):
        from bot.embeds.narrative_embed import build_state_embed

        embed = build_state_embed(
            narrative="You see a cathedral.",
            location_name="Place de la Cathédrale",
            items=["Autel de pierre"],
            npcs=["Père Aldric"],
            exits=["Ruelle nord"],
        )
        assert embed.color == discord.Color(0x4A90D9)

    def test_state_embed_has_title(self):
        from bot.embeds.narrative_embed import build_state_embed

        embed = build_state_embed(
            narrative="You see a cathedral.",
            location_name="Place de la Cathédrale",
            items=[],
            npcs=[],
            exits=[],
        )
        assert embed.title is not None
        assert "Place de la Cathédrale" in embed.title

    def test_state_embed_has_fields(self):
        from bot.embeds.narrative_embed import build_state_embed

        embed = build_state_embed(
            narrative="You observe your surroundings.",
            location_name="Barrier",
            items=["Lever", "Sand bag"],
            npcs=["Guard"],
            exits=["North gate", "South gate"],
        )
        field_names = [f.name for f in embed.fields]
        assert any("Objets" in n or "Items" in n for n in field_names)
        assert any("PNJ" in n or "NPC" in n for n in field_names)
        assert any("Sorties" in n or "Exits" in n for n in field_names)

    def test_state_embed_omits_empty_sections(self):
        from bot.embeds.narrative_embed import build_state_embed

        embed = build_state_embed(
            narrative="Nothing here.",
            location_name="Empty Room",
            items=[],
            npcs=[],
            exits=["Door"],
        )
        field_names = [f.name for f in embed.fields]
        assert not any("Objets" in n or "Items" in n for n in field_names)
        assert not any("PNJ" in n or "NPC" in n for n in field_names)

    def test_state_embed_shows_beat_info(self):
        from bot.embeds.narrative_embed import build_state_embed

        embed = build_state_embed(
            narrative="You look around.",
            location_name="Barrier",
            items=[],
            npcs=[],
            exits=[],
            beat_title="Le Mur qui Soupire",
        )
        assert any(
            "Mur qui Soupire" in (f.value or "")
            for f in embed.fields
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_embeds.py::TestStateEmbed -v`
Expected: FAIL — `build_state_embed` does not exist.

- [ ] **Step 3: Implement build_state_embed**

In `bot/embeds/narrative_embed.py`, add after `build_narrative_embed` (after line 84):

```python
_STATE_COLOR = 0x4A90D9  # Blue — distinct from narrative tones


def build_state_embed(
    narrative: str,
    *,
    location_name: str,
    items: list[str],
    npcs: list[str],
    exits: list[str],
    beat_title: str | None = None,
    language: str = "fr",
) -> discord.Embed:
    """Build a blue state-info embed for question responses.

    Visually distinct from narrative embeds to signal "this is meta-info,
    not part of the story".
    """
    embed = discord.Embed(
        title=f"\U0001f4cb {location_name}",
        description=narrative,
        color=_STATE_COLOR,
    )

    if items:
        label = "Objets visibles" if language == "fr" else "Visible items"
        embed.add_field(name=label, value="\n".join(f"- {i}" for i in items), inline=True)

    if npcs:
        label = "PNJ présents" if language == "fr" else "NPCs present"
        embed.add_field(name=label, value="\n".join(f"- {n}" for n in npcs), inline=True)

    if exits:
        label = "Sorties" if language == "fr" else "Exits"
        embed.add_field(name=label, value="\n".join(f"- {e}" for e in exits), inline=True)

    if beat_title:
        label = "Chapitre en cours" if language == "fr" else "Current chapter"
        embed.add_field(name=label, value=f"*{beat_title}*", inline=False)

    return embed
```

- [ ] **Step 4: Run state embed tests**

Run: `uv run pytest tests/test_embeds.py::TestStateEmbed -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Add QUESTION handler in _resolve_mechanics**

In `bot/action_pipeline.py`, add a QUESTION branch in `_resolve_mechanics()` after the LOOK handler (after line 721). Insert before the `if at == ActionType.SEARCH:` block:

```python
        if at == ActionType.QUESTION:
            # Short-circuit: build factual state summary, no mechanics.
            loc = self.location
            parts: list[str] = []
            if loc:
                parts.append(f"Location: {loc.name}. {loc.description}")
                all_exits = loc.connections + loc.unlocked_exits
                if all_exits:
                    parts.append(f"Exits: {', '.join(all_exits)}.")
                if loc.items_available:
                    parts.append(f"Visible items: {', '.join(loc.items_available)}.")
                if loc.npcs_present:
                    parts.append(f"NPCs present: {', '.join(loc.npcs_present)}.")
                if loc.state_flags:
                    active = [k for k, v in loc.state_flags.items() if v]
                    if active:
                        parts.append(f"Environment state: {', '.join(active)}.")
            # Include beat objective if available.
            if self.session and self.session.story_arc:
                arc = self.session.story_arc
                beat = arc.beats[arc.current_beat_index]
                parts.append(f"Current objective: {beat.title} — {beat.description}")
            summary = f"{action.actor_name} asks about the surroundings."
            return MechanicsOutcome(
                summary=summary,
                player_intent=intent,
                outcome_facts=" ".join(parts),
            )
```

- [ ] **Step 6: Mark QUESTION results in ActionPipelineResult**

In `bot/action_pipeline.py`, add a field to `ActionPipelineResult` (around line 132-142):

```python
class ActionPipelineResult(BaseModel):
    """Successful pipeline run."""

    narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"]
    mechanics_text: str
    public_effects: PublicEffects = Field(default_factory=PublicEffects)
    interpreted_action: InterpretedAction
    new_beat: StoryBeat | None = None
    npc_name: str | None = None
    npc_dialogue: str | None = None
    is_question: bool = False
```

Set `is_question=True` in the result construction (around line 381-390). Add after line 389:

```python
        is_question = interpreted.action_type == ActionType.QUESTION
```

Then update the result construction:

```python
        result = ActionPipelineResult(
            narrative=narration.narrative,
            tone=narration.tone,
            mechanics_text=outcome.summary,
            public_effects=outcome.public_effects,
            interpreted_action=interpreted,
            new_beat=new_beat,
            npc_name=outcome.npc_name,
            npc_dialogue=outcome.npc_dialogue,
            is_question=is_question,
        )
```

- [ ] **Step 7: Route question results to state embed in action_handler**

In `bot/cogs/action_handler.py`, update `_render_success` (around line 290-302). Replace the method:

```python
    async def _render_success(
        self,
        progress_msg: discord.Message,
        result: ActionPipelineResult,
        session: "GameSession | None" = None,
    ) -> None:
        if result.is_question and session is not None:
            loc = session.current_location
            beat_title = None
            if session.story_arc:
                arc = session.story_arc
                beat_title = arc.beats[arc.current_beat_index].title

            embed = build_state_embed(
                narrative=result.narrative,
                location_name=loc.name if loc else "???",
                items=list(loc.items_available) if loc else [],
                npcs=list(loc.npcs_present) if loc else [],
                exits=(list(loc.connections) + list(loc.unlocked_exits)) if loc else [],
                beat_title=beat_title,
                language=session.language,
            )
        else:
            embed = build_narrative_embed(
                narrative=result.narrative,
                public_effects=result.public_effects,
                tone=result.tone,
                npc_name=result.npc_name,
                npc_dialogue=result.npc_dialogue,
            )
        await progress_msg.edit(embed=embed, view=None)
```

Update the call site at line 219 to pass session:

```python
            await self._render_success(progress_msg, result, session=session)
```

Add the import at the top of `bot/cogs/action_handler.py`:

```python
from bot.embeds.narrative_embed import build_narrative_embed, build_state_embed
```

- [ ] **Step 8: Update test to verify full pipeline flow**

Update `TestQuestionAction` in `tests/bot/test_action_pipeline.py`:

```python
    @pytest.mark.asyncio
    async def test_question_returns_is_question_flag(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="Are there NPCs here?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="You see priests.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("Are there NPCs here?", "Aldric")
        assert isinstance(result, ActionPipelineResult)
        assert result.is_question is True

    @pytest.mark.asyncio
    async def test_question_outcome_facts_contain_state(self, cathedral, aldric, corin):
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.QUESTION,
                actor_name="Aldric",
                raw_input="What's around me?",
                confidence=0.9,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Cathedral.", tone="dramatic")],
        )
        pipeline = _make_pipeline(
            interp, narrator, cathedral,
            {"Père Aldric": aldric, "Frère Corin": corin},
        )
        result = await pipeline.process("What's around me?", "Aldric")
        assert isinstance(result, ActionPipelineResult)
        # outcome_facts should have been passed to narrator
        call = narrator.calls[0]
        assert "Place de la Cathédrale" in call["outcome_facts"]
        assert "Autel de pierre" in call["outcome_facts"]
```

- [ ] **Step 9: Run all tests**

Run: `uv run pytest tests/test_embeds.py tests/bot/test_action_pipeline.py -v`
Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add bot/action_pipeline.py bot/embeds/narrative_embed.py bot/cogs/action_handler.py tests/test_embeds.py tests/bot/test_action_pipeline.py
git commit -m "feat(bot): add QUESTION pipeline short-circuit with blue state embed"
```

---

### Task 5: Scene Context Enhancement — Beat Info + State Flags

**Files:**
- Modify: `bot/scene_hydration.py:212-263`
- Modify: `ai/entity_resolver.py:391-431` (_resolve_exit)

- [ ] **Step 1: Update describe_scene_for_narrator to include beat info and unlocked exits**

In `bot/scene_hydration.py`, update `describe_scene_for_narrator` (lines 212-263):

After the exits section (line 231), add unlocked exits:

```python
        all_exits = location.connections + location.unlocked_exits
        if all_exits:
            lines.append("## Exits\n" + ", ".join(all_exits))
```

Replace the existing exits block (lines 230-231) with the above.

After the NPCs section (after line 260), add state flags and beat info:

```python
        if location.state_flags:
            active = [k.replace("_", " ") for k, v in location.state_flags.items() if v]
            if active:
                lines.append("## Environment state\n" + ", ".join(active))

    # Story beat context for the narrator.
    if session.story_arc is not None:
        arc = session.story_arc
        beat = arc.beats[arc.current_beat_index]
        lines.append(f"## Current story beat\n{beat.title} — {beat.description}")
```

Insert this before the final `lines.append(f"## Acting character\n{actor_name}")`.

- [ ] **Step 2: Update entity resolver to check unlocked_exits for MOVE**

In `ai/entity_resolver.py`, update `_resolve_exit` (around line 411):

Replace:
```python
    matches = _match_candidates(raw, list(location.connections))
```

With:
```python
    all_exits = list(location.connections) + list(location.unlocked_exits)
    matches = _match_candidates(raw, all_exits)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/ -k "entity_resolver or scene" -v`
Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add bot/scene_hydration.py ai/entity_resolver.py
git commit -m "feat(bot): include beat info, state flags, and unlocked exits in scene context"
```

---

### Task 6: Arc Generator — Produce Triggers and Effects

**Files:**
- Modify: `ai/prompts/system_arc_generator.txt`
- Test: `tests/ai/test_arc_generator.py`

- [ ] **Step 1: Write failing test for trigger presence in generated arcs**

In `tests/ai/test_arc_generator.py`, update `_make_arc_data()` to include the new fields in the sample beat, then add a test:

```python
def test_generated_beats_have_completion_triggers(self, httpx_mock, generator):
    """Beats should include completion_trigger and on_complete."""
    arc_data = _make_arc_data()
    # Add triggers to all beats in test data
    for beat in arc_data["beats"]:
        beat["completion_trigger"] = {"type": "interact", "target": "some object"}
        beat["on_complete"] = {
            "unlock_exits": ["Next Area"],
            "state_flags": {"puzzle_solved": True},
            "narrative_hint": "Something changes.",
        }
    httpx_mock.add_response(json=arc_data)
    arc = generator.generate("test theme", 1)
    for beat in arc.beats:
        assert beat.completion_trigger is not None
        assert beat.on_complete.unlock_exits == ["Next Area"]
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/ai/test_arc_generator.py -k "completion_triggers" -v`
Expected: PASS — the fields are optional on StoryBeat and Pydantic validates them when present. No code change needed in `ai/arc_generator.py` since it uses `StoryArc.model_validate(data)` which handles the new fields.

- [ ] **Step 3: Update arc generator prompt**

In `ai/prompts/system_arc_generator.txt`, update the JSON schema (lines 40-54). Replace the beat object in the schema:

```json
    {
      "beat_number": 1,
      "title": "<beat title>",
      "description": "<2-3 sentence description>",
      "location_hint": "<suggested location>",
      "npc_names": ["<NPC name>"],
      "encounter_type": "social",
      "encounter_subtype": "negotiation",
      "is_twist": false,
      "completion_trigger": {
        "type": "<interact|defeat|talk|arrive|search|pickup>",
        "target": "<name of the key object, NPC, or location>"
      },
      "on_complete": {
        "unlock_exits": ["<exit name unlocked by completing this beat>"],
        "add_npcs": [],
        "remove_items": [],
        "add_items": [],
        "state_flags": {"<flag_name>": true},
        "narrative_hint": "<one sentence describing what changes when beat is completed>"
      }
    }
```

Add a new rule in the requirements section:

```
- Each beat MUST include a completion_trigger: what action type and target object/NPC completes this beat
- Each beat MUST include on_complete: what changes in the world when the beat is completed (new exits, NPCs, items, flags)
- completion_trigger.type must be one of: interact, defeat, talk, arrive, search, pickup
- For puzzle beats: trigger is typically "interact" with the key puzzle object
- For combat beats: trigger is "defeat" with the enemy name
- For social beats: trigger is "talk" with the key NPC
- For exploration beats: trigger is "arrive" (target = location name)
- on_complete.unlock_exits should list the exit(s) that open when this beat is completed, connecting to the next beat's location
```

- [ ] **Step 4: Run full arc generator tests**

Run: `uv run pytest tests/ai/test_arc_generator.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ai/prompts/system_arc_generator.txt tests/ai/test_arc_generator.py
git commit -m "feat(ai): arc generator prompt produces completion triggers and beat effects"
```

---

### Task 7: Beat Completion — Deterministic Triggers

**Files:**
- Modify: `bot/action_pipeline.py:326-369`
- Modify: `bot/game_session.py:79-114`
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Write failing test for deterministic beat completion**

In `tests/bot/test_action_pipeline.py`, add a new test class. This test requires a session mock with a story arc. Add the needed imports and a helper:

```python
from world.story_arc import StoryArc, StoryBeat, CompletionTrigger, BeatEffects


def _make_session_with_arc(location, story_arc):
    """Create a minimal mock session for beat advancement tests."""
    from unittest.mock import MagicMock

    session = MagicMock()
    session.current_location = location
    session.story_arc = story_arc
    session.npcs = {}
    session.language = "fr"
    session.combat_state = None
    session.inventory = None
    return session


class TestBeatCompletion:
    """Deterministic beat completion via triggers."""

    @pytest.mark.asyncio
    async def test_interact_trigger_completes_beat(self):
        loc = Location(
            name="Bone Barrier",
            description="A wall of bones.",
            connections=[],
            items_available=["Le levier de l'Échiquier"],
        )
        arc = StoryArc(
            campaign_id="test",
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                StoryBeat(
                    beat_number=i + 1,
                    title=f"Beat {i + 1}",
                    description=f"Description {i + 1}",
                    location_hint="Bone Barrier" if i == 0 else f"Area {i + 1}",
                    encounter_type="puzzle" if i == 0 else "exploration",
                    completion_trigger=CompletionTrigger(type="interact", target="Le levier de l'Échiquier") if i == 0 else None,
                    on_complete=BeatEffects(
                        unlock_exits=["Inner Court"],
                        state_flags={"breach_open": True},
                        narrative_hint="A breach opens.",
                    ) if i == 0 else BeatEffects(),
                )
                for i in range(10)
            ],
            villain_name="Thaumiel",
            villain_motivation="Purify humanity.",
        )
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.INTERACT,
                actor_name="Hero",
                target_name="Le levier de l'Échiquier",
                raw_input="I pull the lever",
                confidence=0.95,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="The lever moves.", tone="tense")],
        )
        session = _make_session_with_arc(loc, arc)
        pipeline = _make_pipeline(
            interp, narrator, loc,
            {},
            actor_name="Hero",
        )
        pipeline.session = session

        result = await pipeline.process("I pull the lever", "Hero")
        assert isinstance(result, ActionPipelineResult)
        # Beat should have advanced
        assert result.new_beat is not None
        assert result.new_beat.beat_number == 2
        # Location should have been mutated
        assert "Inner Court" in loc.unlocked_exits
        assert loc.state_flags.get("breach_open") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestBeatCompletion -v`
Expected: FAIL — no beat completion logic exists yet in the pipeline.

- [ ] **Step 3: Implement _check_beat_completion and _apply_beat_effects**

In `bot/action_pipeline.py`, add two new methods to `ActionPipeline`. Add them after `_resolve_mechanics` (after line 801):

```python
    def _check_beat_completion(
        self, action: InterpretedAction,
    ) -> bool:
        """Check if the action satisfies the current beat's completion trigger.

        Uses fuzzy matching on the target name to accommodate LLM-generated
        trigger targets that may not exactly match entity-resolved names.
        """
        if self.session is None or self.session.story_arc is None:
            return False
        arc = self.session.story_arc
        if arc.current_beat_index >= len(arc.beats):
            return False
        beat = arc.beats[arc.current_beat_index]
        trigger = beat.completion_trigger
        if trigger is None:
            return False

        # Type match: action type must correspond to trigger type.
        type_map: dict[str, set[str]] = {
            "interact": {ActionType.INTERACT},
            "defeat": {ActionType.ATTACK},
            "talk": {ActionType.TALK},
            "arrive": {ActionType.MOVE},
            "search": {ActionType.SEARCH},
            "pickup": {ActionType.PICKUP},
        }
        allowed = type_map.get(trigger.type, set())
        if action.action_type not in allowed:
            return False

        # Target match: fuzzy comparison.
        if trigger.target and action.target_name:
            from bot.game_session import _normalize_location
            ratio = difflib.SequenceMatcher(
                None,
                _normalize_location(action.target_name),
                _normalize_location(trigger.target),
            ).ratio()
            return ratio >= 0.6
        return False

    def _apply_beat_effects(self, effects: BeatEffects) -> str:
        """Apply beat completion effects to the current location.

        Returns a narrative hint string for the narrator.
        """
        loc = self.location
        if loc is None:
            return effects.narrative_hint

        for exit_name in effects.unlock_exits:
            if exit_name not in loc.unlocked_exits:
                loc.unlocked_exits.append(exit_name)
        for npc_name in effects.add_npcs:
            if npc_name not in loc.npcs_present:
                loc.npcs_present.append(npc_name)
        for item_name in effects.remove_items:
            if item_name in loc.items_available:
                loc.items_available.remove(item_name)
        for item_name in effects.add_items:
            if item_name not in loc.items_available:
                loc.items_available.append(item_name)
        loc.state_flags.update(effects.state_flags)

        return effects.narrative_hint
```

Add needed imports at the top of `bot/action_pipeline.py`:

```python
import difflib
from world.story_arc import BeatEffects
```

(`difflib` may already be imported — check first.)

- [ ] **Step 4: Wire beat completion into _continue_from_resolution**

In `bot/action_pipeline.py`, modify `_continue_from_resolution` (around lines 326-369). After `outcome = await self._resolve_mechanics(interpreted)` (line 327) and before `context_prompt = self._assemble_context(interpreted)` (line 330), insert:

```python
        # Beat completion check — deterministic trigger.
        beat_completed = False
        if (
            self.session is not None
            and interpreted.action_type != ActionType.QUESTION
            and self._check_beat_completion(interpreted)
        ):
            beat_completed = True
            arc = self.session.story_arc
            beat = arc.beats[arc.current_beat_index]
            hint = self._apply_beat_effects(beat.on_complete)
            if hint:
                outcome = outcome.model_copy(update={
                    "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                })
            # Advance the beat index.
            from world.story_arc import advance_beat
            self.session.story_arc = advance_beat(arc)
            logger.info(
                "BEAT trigger-complete campaign=%s beat=%d title=%r",
                self.campaign_id,
                beat.beat_number,
                beat.title,
            )
```

Then modify the existing beat advancement block (lines 338-369). The existing `advance_beat_if_ready()` call should still run as a fallback for `arrive` type beats, but skip if the beat was already advanced:

```python
        # Lot D — beat advancement check (fallback for arrival-based beats).
        new_beat: StoryBeat | None = None
        if beat_completed and self.session and self.session.story_arc:
            # Beat was already advanced by trigger — grab the new beat.
            new_beat = self.session.story_arc.beats[
                self.session.story_arc.current_beat_index
            ]
        elif self.session is not None and hasattr(
            self.session, "advance_beat_if_ready",
        ):
            try:
                candidate = self.session.advance_beat_if_ready()
            except Exception:
                logger.exception(
                    "BEAT advance check failed campaign=%s", self.campaign_id,
                )
                candidate = None
            if isinstance(candidate, StoryBeat):
                new_beat = candidate
```

Keep the existing persistence block (lines 352-369) as-is.

- [ ] **Step 5: Run test**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestBeatCompletion -v`
Expected: PASS.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "feat(bot): deterministic beat completion via trigger matching"
```

---

### Task 8: LLM Fallback for Creative Solutions

**Files:**
- Modify: `bot/action_pipeline.py`
- Test: `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Write failing test for LLM fallback**

In `tests/bot/test_action_pipeline.py`, add to `TestBeatCompletion`:

```python
    @pytest.mark.asyncio
    async def test_llm_fallback_fires_on_creative_solution(self):
        """When deterministic trigger doesn't match but player is creative, LLM fallback fires."""
        loc = Location(
            name="Bone Barrier",
            description="A wall of bones.",
            connections=[],
            items_available=["Le levier de l'Échiquier", "Sac de sable"],
        )
        arc = StoryArc(
            campaign_id="test",
            theme="dungeon",
            premise="A dungeon adventure with many challenges ahead.",
            beats=[
                StoryBeat(
                    beat_number=i + 1,
                    title=f"Beat {i + 1}",
                    description="Balance the mechanism to open a breach." if i == 0 else f"Desc {i + 1}",
                    location_hint="Bone Barrier" if i == 0 else f"Area {i + 1}",
                    encounter_type="puzzle" if i == 0 else "exploration",
                    completion_trigger=CompletionTrigger(type="interact", target="Le levier de l'Échiquier") if i == 0 else None,
                    on_complete=BeatEffects(
                        unlock_exits=["Inner Court"],
                        state_flags={"breach_open": True},
                        narrative_hint="A breach opens.",
                    ) if i == 0 else BeatEffects(),
                )
                for i in range(10)
            ],
            villain_name="Thaumiel",
            villain_motivation="Purify humanity.",
        )
        # Player uses IMPROVISE instead of INTERACT — deterministic trigger won't match
        interp = FakeInterpreter(
            response=InterpretedAction(
                action_type=ActionType.IMPROVISE,
                actor_name="Hero",
                target_name=None,
                raw_input="I use the sand to balance the mechanism",
                improvise_description="Hero uses sand to balance the mechanism",
                confidence=0.8,
            ),
        )
        narrator = FakeNarrator(
            responses=[NarrativeResult(narrative="Sand balances it.", tone="tense")],
        )
        session = _make_session_with_arc(loc, arc)
        pipeline = _make_pipeline(
            interp, narrator, loc,
            {},
            actor_name="Hero",
        )
        pipeline.session = session

        # Mock the LLM fallback judge to return completed=True
        from unittest.mock import AsyncMock, patch

        mock_judge = AsyncMock(return_value={"completed": True, "confidence": 0.9})
        with patch.object(pipeline, "_llm_beat_fallback", mock_judge):
            result = await pipeline.process("I use the sand", "Hero")

        assert isinstance(result, ActionPipelineResult)
        assert result.new_beat is not None
        assert result.new_beat.beat_number == 2
        assert "Inner Court" in loc.unlocked_exits
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestBeatCompletion::test_llm_fallback_fires_on_creative_solution -v`
Expected: FAIL — `_llm_beat_fallback` does not exist.

- [ ] **Step 3: Implement LLM fallback method**

In `bot/action_pipeline.py`, add to `ActionPipeline` after `_apply_beat_effects`:

```python
    async def _llm_beat_fallback(
        self,
        action: InterpretedAction,
        beat: StoryBeat,
        outcome: MechanicsOutcome,
    ) -> dict:
        """Ask the 4b model if the player's creative action completes the beat.

        Returns {"completed": bool, "confidence": float}.
        Falls back to {"completed": False, "confidence": 0.0} on any error.
        """
        if self.interpreter is None:
            return {"completed": False, "confidence": 0.0}
        trigger_desc = ""
        if beat.completion_trigger:
            trigger_desc = f"{beat.completion_trigger.type} on \"{beat.completion_trigger.target}\""
        prompt = (
            f"Beat objective: \"{beat.description}\"\n"
            f"Expected trigger: {trigger_desc}\n"
            f"Player action: {action.action_type.value} on \"{action.target_name or 'nothing'}\"\n"
            f"Action summary: \"{outcome.summary}\"\n\n"
            f"Has the player achieved the beat objective through a creative approach?\n"
            f"Return JSON: {{\"completed\": true/false, \"confidence\": 0.0-1.0}}"
        )
        try:
            from ai.client import OllamaClient
            client = self.interpreter._client
            result = client.chat_json(
                "qwen3.5:4b",
                [
                    {"role": "system", "content": "You judge whether a player's action has completed a story beat objective. Respond with JSON only: {\"completed\": bool, \"confidence\": float}"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                think=False,
            )
            return {
                "completed": bool(result.get("completed", False)),
                "confidence": float(result.get("confidence", 0.0)),
            }
        except Exception:
            logger.warning(
                "BEAT LLM fallback failed campaign=%s", self.campaign_id,
                exc_info=True,
            )
            return {"completed": False, "confidence": 0.0}
```

- [ ] **Step 4: Wire LLM fallback into _continue_from_resolution**

In the beat completion block added in Task 7 Step 4, add the fallback after the deterministic check. Modify the inserted block to add an `elif` for the fallback:

```python
        # Beat completion check — deterministic trigger.
        beat_completed = False
        if (
            self.session is not None
            and interpreted.action_type != ActionType.QUESTION
            and self._check_beat_completion(interpreted)
        ):
            beat_completed = True
            arc = self.session.story_arc
            beat = arc.beats[arc.current_beat_index]
            hint = self._apply_beat_effects(beat.on_complete)
            if hint:
                outcome = outcome.model_copy(update={
                    "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                })
            from world.story_arc import advance_beat
            self.session.story_arc = advance_beat(arc)
            logger.info(
                "BEAT trigger-complete campaign=%s beat=%d title=%r",
                self.campaign_id, beat.beat_number, beat.title,
            )
        elif (
            self.session is not None
            and self.session.story_arc is not None
            and interpreted.action_type not in (
                ActionType.QUESTION, ActionType.LOOK,
            )
        ):
            # LLM fallback — only when at the right location and action is non-trivial.
            arc = self.session.story_arc
            beat = arc.beats[arc.current_beat_index]
            if (
                beat.completion_trigger is not None
                and self.location is not None
            ):
                from bot.game_session import _normalize_location
                loc_ratio = difflib.SequenceMatcher(
                    None,
                    _normalize_location(self.location.name),
                    _normalize_location(beat.location_hint),
                ).ratio()
                if loc_ratio >= 0.5:
                    judge = await self._llm_beat_fallback(interpreted, beat, outcome)
                    logger.info(
                        "BEAT fallback campaign=%s completed=%s confidence=%.2f",
                        self.campaign_id,
                        judge.get("completed"), judge.get("confidence"),
                    )
                    if judge.get("completed") and judge.get("confidence", 0) >= 0.85:
                        beat_completed = True
                        hint = self._apply_beat_effects(beat.on_complete)
                        if hint:
                            outcome = outcome.model_copy(update={
                                "outcome_facts": (outcome.outcome_facts + " " + hint).strip(),
                            })
                        from world.story_arc import advance_beat
                        self.session.story_arc = advance_beat(arc)
                        logger.info(
                            "BEAT fallback-complete campaign=%s beat=%d title=%r",
                            self.campaign_id, beat.beat_number, beat.title,
                        )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/bot/test_action_pipeline.py::TestBeatCompletion -v`
Expected: All tests PASS.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
git add bot/action_pipeline.py tests/bot/test_action_pipeline.py
git commit -m "feat(bot): LLM fallback for creative beat completion"
```

---

### Task 9: Narrator Prompt Enhancement

**Files:**
- Modify: `ai/prompts/system_narrator.txt`

- [ ] **Step 1: Add beat context awareness to narrator prompt**

In `ai/prompts/system_narrator.txt`, add after the canon faithfulness rules (after line 30):

```
Beat awareness:
- When a "Current story beat" section is present in the context, be aware of the narrative purpose of this scene. Let it subtly color your descriptions without explicitly stating the beat title.
- When "Environment state" flags are present (e.g., "breach open", "mechanism balanced"), reflect these in your descriptions. Do NOT describe a breach as closed if the state says it's open.
- When the outcome facts mention a beat completion ("A breach opens", "The door swings open"), describe this as a significant narrative moment — the world is changing around the character.
- When "State changes" mentions new exits being unlocked, work this into the description naturally (e.g., "Beyond the crumbling wall, a passage reveals itself...").
```

- [ ] **Step 2: Commit**

```bash
git add ai/prompts/system_narrator.txt
git commit -m "feat(ai): narrator prompt gains beat and environment state awareness"
```

---

### Task 10: Final Integration Verification

- [ ] **Step 1: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: ALL tests PASS.

- [ ] **Step 2: Run type checker**

Run: `uv run mypy .`
Expected: No new errors.

- [ ] **Step 3: Run linter**

Run: `uv run ruff check .`
Expected: No new errors.

- [ ] **Step 4: Verify full pipeline flow manually**

Create a quick integration test or use the existing scenario runner to verify:
1. Player asks a question → blue state embed
2. Player interacts with trigger target → beat advances → location mutates
3. Player can MOVE to newly unlocked exit

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address linting and type errors from beat progression feature"
```
