# Beat Progression, Environment State & Question Detection

**Date:** 2026-04-11
**Status:** Draft
**Approach:** Hybrid — deterministic triggers + LLM fallback

## Context

During playtesting (campaign `582d2dec`), the player got stuck at Beat 1 ("Le Mur qui Soupire") with no way to progress. Three interconnected issues surfaced:

1. **Questions treated as actions** — "Est-ce qu'il y a des PNJ ?" gets classified as `Search` or `Talk` and narrated as an in-game action instead of receiving a factual answer.
2. **No environment state persistence** — The player "balances the mechanism" and the narrator describes a breach opening, but nothing changes in the world model. Next action, the world is back to square one.
3. **Beat progression locked to location only** — `advance_beat_if_ready()` only checks if `current_location.name` fuzzy-matches `next_beat.location_hint`. No puzzle completion, no objective tracking.

**Principle:** The LLM narrates, the code arbitrates. The LLM fallback is a safety net for creative solutions, not the primary progression mechanism.

---

## 1. Question Detection

### Problem

The interpreter has no `Question` action type. All player input must map to an action (Look, Search, Talk, Interact, Improvise...). Questions about game state get forced into actions, producing confusing narrative responses.

### Solution

Add `QUESTION` to `ActionType` enum. The interpreter classifies meta-questions (clarifications, state queries, requests for information about the environment) as `Question`.

### Changes

**`engine/validators.py`** — Add to `ActionType`:
```python
QUESTION = "Question"
```

Add to `EXPLORATION_ACTION_TYPES` frozenset.

**`ai/prompts/system_interpreter.txt`** — Add a new category:
```
Meta:
- Question — The player asks about the game state, their environment, 
  requests clarification about what happened, or asks what they can do.
  Examples: "What do I see?", "Did I succeed?", "Are there NPCs nearby?",
  "What can I interact with?"
```

Priority rule: If the input is phrased as a question about the world state (not a roleplay question to an NPC), classify as `Question`.

**`bot/action_pipeline.py`** — Short-circuit for `QUESTION`:
- Skip phases 2-4 (entity resolution, validation, mechanics)
- Build a factual `MechanicsOutcome` with:
  - `summary`: "JeanTesty asks about the surroundings."
  - `outcome_facts`: structured state dump (location, items, NPCs, exits, current beat objective, completed flags)
  - `player_intent`: the original question
- The narrator receives this and reformulates as an in-character but informative response
- `public_effects` stays empty (no mutation)

**`bot/embeds/narrative_embed.py`** — New embed builder:
```python
def build_state_embed(
    narrative: str,
    *,
    location_name: str,
    items: list[str],
    npcs: list[str],
    exits: list[str],
    beat_title: str | None = None,
) -> discord.Embed:
```
- Color: `0x4A90D9` (blue — distinct from narrative tones)
- Title: "État du jeu" / "Game State"
- Description: narrator's reformulated answer
- Fields: items, NPCs, exits (compact inline fields)
- No footer effects (nothing changed)

This makes it visually obvious that the response is meta-information, not part of the story.

---

## 2. Environment State Persistence

### Problem

`Location` has no concept of mutable state. `_resolve_mechanics()` returns empty `public_effects` for INTERACT, SEARCH, LOOK. The narrator describes changes, but nothing persists.

### Solution

Add lightweight state tracking to `Location`. Beat completion triggers (section 3) define what mutations to apply.

### Changes

**`world/location.py`** — Extend `Location`:
```python
class Location(BaseModel):
    name: str
    description: str = ""
    connections: list[str] = []
    npcs_present: list[str] = []
    items_available: list[str] = []
    item_descriptions: dict[str, str] = {}
    # NEW: mutable environment state
    state_flags: dict[str, bool] = {}
    unlocked_exits: list[str] = []
```

`connections` = always-available exits. `unlocked_exits` = exits revealed by beat completion.

Both are shown to the player. The interpreter/entity resolver treats both as valid MOVE targets.

**`bot/action_pipeline.py`** — New method `_apply_beat_effects()`:
- Called when a beat completion trigger fires (section 3)
- Mutates `Location` in-place: sets `state_flags`, extends `unlocked_exits`, adds/removes from `items_available`, adds to `npcs_present`
- Returns updated `outcome_facts` string for the narrator

**`bot/scene_hydration.py`** — `describe_scene_for_narrator()` includes:
- `state_flags` as natural language hints (e.g., "The mechanism has been balanced. A breach is open in the wall.")
- `unlocked_exits` alongside `connections`
- Current beat title and description (from `GameSession.story_arc`)

**`db/repositories/location_repo.py`** — Persist `state_flags` and `unlocked_exits` on save. Restore on load.

---

## 3. Beat Progression — Deterministic Triggers + LLM Fallback

### Problem

`advance_beat_if_ready()` in `bot/game_session.py` only checks location name fuzzy-match. Beat 1's puzzle can be "solved" narratively 10 times and nothing progresses.

### Solution

Two-tier progression:
1. **Deterministic trigger** — defined per beat, checked by the engine after each action
2. **LLM fallback** — when the deterministic trigger doesn't match but the player's action is plausibly solving the beat's objective

### New Models

**`world/story_arc.py`** — Add to `StoryBeat`:
```python
class CompletionTrigger(BaseModel):
    """Deterministic condition for beat completion."""
    type: Literal["interact", "defeat", "talk", "arrive", "search", "pickup"]
    target: str  # name of the object/NPC/location to match (fuzzy)

class BeatEffects(BaseModel):
    """World mutations applied when a beat is completed."""
    unlock_exits: list[str] = []
    add_npcs: list[str] = []
    remove_items: list[str] = []
    add_items: list[str] = []
    state_flags: dict[str, bool] = {}
    narrative_hint: str = ""  # hint for narrator, e.g. "A breach opens in the wall"

class StoryBeat(BaseModel):
    beat_number: int = Field(ge=1, le=20)
    title: str = Field(min_length=1)
    description: str
    location_hint: str
    npc_names: list[str] = Field(default_factory=list)
    encounter_type: Literal["social", "combat", "exploration", "puzzle", "boss"]
    encounter_subtype: str | None = None
    is_twist: bool = False
    # NEW
    completion_trigger: CompletionTrigger | None = None  # None = arrival-based (location match only)
    on_complete: BeatEffects = Field(default_factory=BeatEffects)
```

### Arc Generator Changes

**`ai/prompts/system_arc_generator.txt`** — The LLM must now produce for each beat:
```json
{
  "completion_trigger": {"type": "interact", "target": "Le levier de l'Échiquier"},
  "on_complete": {
    "unlock_exits": ["La cour intérieure"],
    "state_flags": {"mechanism_balanced": true, "breach_open": true},
    "narrative_hint": "Le mécanisme s'équilibre, une brèche s'ouvre dans le mur d'ossements"
  }
}
```

**`ai/arc_generator.py`** — Parse and validate the new fields.

### Deterministic Check

**`bot/action_pipeline.py`** — After `_resolve_mechanics()`, new method `_check_beat_completion()`:

```python
def _check_beat_completion(self, action: InterpretedAction) -> bool:
    beat = current_beat(self.session)
    if beat is None or beat.completion_trigger is None:
        return False
    trigger = beat.completion_trigger
    # Type match: action type must match trigger type
    if action.action_type.lower() != trigger.type:
        return False
    # Target match: fuzzy match action target to trigger target
    if trigger.target and action.target_name:
        ratio = SequenceMatcher(None, 
            normalize(action.target_name), 
            normalize(trigger.target)
        ).ratio()
        return ratio >= 0.6
    return False
```

When this returns `True`:
1. Apply `beat.on_complete` → mutate Location
2. Add `narrative_hint` to `outcome_facts`
3. Mark beat as completed (advance `current_beat_index`)
4. Add beat completion info to `public_effects` (e.g., new exit in footer)

### LLM Fallback

When deterministic check returns `False`, check if fallback should fire:

**Guard conditions** (all must be true):
- Player is at the location matching current beat's `location_hint`
- Action is non-trivial (not LOOK, not QUESTION)
- Deterministic trigger exists but didn't match
- Beat is not already completed

**LLM call** — Uses interpreter model (4b, fast, ~10s):
```
System: You judge whether a player's action has completed a story beat objective.
Respond with JSON: {"completed": bool, "confidence": float}

User:
Beat objective: "{beat.description}"
Expected trigger: {trigger.type} on "{trigger.target}"
Player action: {action.action_type} on "{action.target_name}"
Action summary: "{mechanics_outcome.summary}"

Has the player achieved the beat objective through a creative approach?
```

**Decision:**
- `completed == true AND confidence >= 0.85` → apply `on_complete`, advance beat
- Otherwise → no progression

**Logging:** Always log fallback invocations and results for debugging.

### Updated `advance_beat_if_ready()`

The existing location-based check in `game_session.py` becomes a secondary trigger. Priority:
1. `_check_beat_completion()` in action pipeline (primary, per-action)
2. Location fuzzy-match in `advance_beat_if_ready()` (secondary, for `arrive` type beats)

---

## 4. Full Action Flow (Updated)

```
Player sends message
  │
  ├─ Phase 1: INTERPRET (LLM 4b)
  │   └─ Returns InterpretedAction with action_type
  │
  ├─ action_type == QUESTION?
  │   ├─ YES → Build state summary → Narrator reformulates → State embed (blue)
  │   └─ NO ↓
  │
  ├─ Phase 2: RESOLVE ENTITIES (Python)
  ├─ Phase 3: VALIDATE (Python)
  ├─ Phase 4: RESOLVE MECHANICS (Python)
  │   └─ Returns MechanicsOutcome
  │
  ├─ Phase 4b: CHECK BEAT COMPLETION (Python + optional LLM fallback)
  │   ├─ Deterministic trigger match? → Apply on_complete, advance beat
  │   ├─ No match + guard conditions met? → LLM fallback (4b)
  │   │   └─ completed + confidence ≥ 0.85? → Apply on_complete, advance beat
  │   └─ Otherwise → No progression
  │
  ├─ Phase 5: ASSEMBLE CONTEXT (Python)
  │   └─ Now includes: state_flags, unlocked_exits, beat info, narrative_hint
  │
  ├─ Phase 6: NARRATE (LLM 9b)
  │   └─ Narrator sees outcome_facts with beat completion info
  │
  └─ Discord embed
      ├─ Narrative embed (gold/red/green/purple) + footer with effects
      └─ If beat completed: footer shows "🔓 Nouvelle sortie: X" or similar
```

---

## 5. Files to Modify

| File | Change |
|------|--------|
| `engine/validators.py` | Add `QUESTION` to `ActionType` |
| `ai/models.py` | No changes needed (existing models sufficient) |
| `ai/prompts/system_interpreter.txt` | Add `Question` category with examples |
| `ai/prompts/system_arc_generator.txt` | Add `completion_trigger` + `on_complete` to beat schema |
| `ai/arc_generator.py` | Parse new beat fields |
| `world/story_arc.py` | Add `CompletionTrigger`, `BeatEffects`, new fields on `StoryBeat` |
| `world/location.py` | Add `state_flags`, `unlocked_exits` |
| `bot/action_pipeline.py` | Question short-circuit + `_check_beat_completion()` + `_apply_beat_effects()` + LLM fallback |
| `bot/game_session.py` | Update `advance_beat_if_ready()` to respect new trigger system |
| `bot/scene_hydration.py` | Include state_flags, unlocked_exits, beat info in narrator context |
| `bot/embeds/narrative_embed.py` | Add `build_state_embed()` for question responses |
| `db/repositories/location_repo.py` | Persist new Location fields |
| `tests/ai/test_arc_generator.py` | Test new beat fields generation |
| `tests/bot/test_action_pipeline.py` | Test question short-circuit, beat completion, LLM fallback |
| `tests/test_db_repos.py` | Test Location persistence with new fields |

---

## 6. Verification Plan

### Unit Tests
- `ActionType.QUESTION` exists and is in `EXPLORATION_ACTION_TYPES`
- `CompletionTrigger` and `BeatEffects` serialize/deserialize correctly
- `_check_beat_completion()` matches various action/trigger combinations (exact, fuzzy, no match)
- Question action short-circuits pipeline (no entity resolution, no validation)
- `_apply_beat_effects()` correctly mutates Location
- `build_state_embed()` produces correct color and fields

### Integration Tests
- Arc generator produces valid `completion_trigger` for each beat
- Full pipeline: player interacts with trigger target → beat advances → location mutates → new exits visible
- Full pipeline: player asks question → receives state embed (blue, not narrative)
- LLM fallback: creative action → fallback fires → beat advances (mock LLM for determinism)

### Live Discord Test
- Start campaign → play through Beat 1 puzzle → verify beat advances to Beat 2
- Ask "What do I see?" → verify blue state embed with items/NPCs/exits
- Try creative solution (not matching trigger exactly) → verify LLM fallback fires
