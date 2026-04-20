# Director's Cut — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Tame the narrator's drift. Extend `NarrativeResult` with meta-telemetry fields (invisible to player), wire a `DriftTracker` per campaign, upgrade the Story Director's cadence + structured `DirectorNote` output, inject the new direction block into the narrator prompt, and add a `/story_catch_up` escape hatch.

**Architecture:** Two opposing forces — the Narrator now *reports* (via meta fields) whether the scene is advancing, and the Story Director *prescribes* (via direction fields) what the next narration should set up. A `DriftTracker` per campaign watches the meta flags and triggers Story Director runs when the scene stalls. The player never sees any of this — only the literary `narrative` field reaches Discord.

**Tech Stack:** Python 3.11+ (3.14 in this worktree), Pydantic v2, pytest, asyncio, Discord.py 2.4+, Ollama qwen3.5:9b.

**Spec:** [`docs/superpowers/specs/2026-04-20-directors-cut-design.md`](../specs/2026-04-20-directors-cut-design.md) — Sections 2 (Narrator Contract) + 3 (Story Director).

**Builds on:** Phase A (`docs/superpowers/plans/2026-04-20-directors-cut-phase-a.md`) — assumes the `bot/pipeline/` package and Narrator fallback chain are in place.

---

## File Structure

### New files

| File | Responsibility | Approx. lines |
|------|----------------|---------------|
| `bot/pipeline/drift_tracker.py` | Per-campaign rolling window of beat-advanced flags + drift signal | ~80 |
| `tests/bot/pipeline/test_drift_tracker.py` | Unit tests for the tracker | ~80 |

### Modified files

| File | Change |
|------|--------|
| `ai/models.py` | Extend `NarrativeResult` with 4 meta fields (defaults). Extend `DirectorNote` with 5 direction fields (defaults). |
| `ai/prompts/system_narrator.txt` | Update the output schema block to include meta fields. Add explicit instruction that meta is invisible to the player. |
| `ai/narrator.py` | Parse new meta fields in `_call_llm`. |
| `ai/prompts/system_story_director.txt` | Update the output schema block to include direction fields. |
| `ai/story_director.py` | Parse new direction fields in `check_coherence`. Add `cached_note_for(campaign_id)` static cache. |
| `bot/pipeline/orchestrator.py` | Wire `DriftTracker` into `_continue_from_resolution`. Inject `[STORY DIRECTION]` block into narrator context. Add `force_director_run` flag. Add `should_run_director(...)` helper. Trigger Story Director async-in-background. |
| `bot/cogs/session.py` (or new `bot/cogs/narrative.py`) | Add `/story_catch_up` slash command. |
| `tests/ai/test_models.py` | Add tests for new `NarrativeResult` and `DirectorNote` fields. |
| `tests/ai/test_narrator.py` | Add test that meta fields are populated when LLM provides them. |
| `tests/ai/test_story_director.py` | Add test that direction fields populate when LLM provides them. |
| `tests/bot/test_action_pipeline.py` | Add test that drift tracker is consulted in pipeline. Add test for `force_director_run`. |
| `tests/bot/test_cog_session.py` (or `test_cog_narrative.py`) | Test `/story_catch_up` command. |

---

## Tasks Overview

| # | Task | Est. effort |
|---|------|-------------|
| B0 | Baseline verification | 5 min |
| B1 | Extend `NarrativeResult` with meta fields + tests | 30 min |
| B2 | Update narrator prompt + parser to populate meta | 30 min |
| B3 | Create `DriftTracker` + unit tests | 45 min |
| B4 | Extend `DirectorNote` with direction fields + tests | 30 min |
| B5 | Update story director prompt + parser | 30 min |
| B6 | Wire DriftTracker into pipeline + Story Director cadence | 1h |
| B7 | Inject `[STORY DIRECTION]` block into narrator prompt | 30 min |
| B8 | `/story_catch_up` slash command | 45 min |

Total: ~5 hours.

---

## Task B0: Baseline Verification

- [ ] **Step 1: Confirm clean tree on worktree**

```bash
git status
```

Expected: `On branch feat/directors-cut`, `nothing to commit, working tree clean`.

- [ ] **Step 2: Full test suite**

```bash
uv run pytest -q --tb=no 2>&1 | tail -5
```

Expected: 2090 passed (post-Phase A baseline). Record exact count.

- [ ] **Step 3: Lint**

```bash
uv run ruff check .
```

Expected: `All checks passed!`.

No commit. Just a gate.

---

## Task B1: Extend `NarrativeResult` with Meta Fields

**Goal:** Add 4 new fields to `NarrativeResult` (`scene_goal_touched`, `beat_advanced`, `npcs_mentioned`, `locked_facts_used`). All optional with defaults so existing payloads parse cleanly.

**Files:**
- Modify: `ai/models.py:32-37` (`NarrativeResult`)
- Modify: `tests/ai/test_models.py` (add `TestNarrativeResultMeta` class)

- [ ] **Step 1: Write the failing tests**

If `tests/ai/test_models.py` exists, append. Otherwise create it. (Check first — if `test_models.py` exists in `tests/ai/` use it; if not, create it with the standard imports header.)

```python
"""Tests for ai/models.py — extension fields and backward compatibility."""

import pytest

from ai.models import DirectorNote, NarrativeResult


class TestNarrativeResultMeta:
    def test_old_payload_parses_with_defaults(self) -> None:
        """A legacy {narrative, tone} payload still works — meta fields default."""
        result = NarrativeResult(narrative="The blade flashes.", tone="dramatic")
        assert result.scene_goal_touched is False
        assert result.beat_advanced is False
        assert result.npcs_mentioned == []
        assert result.locked_facts_used == []

    def test_new_payload_carries_meta(self) -> None:
        result = NarrativeResult(
            narrative="The blade flashes as Vlaxos parries.",
            tone="dramatic",
            scene_goal_touched=True,
            beat_advanced=True,
            npcs_mentioned=["Vlaxos"],
            locked_facts_used=["map_hidden_in_cellar"],
        )
        assert result.scene_goal_touched is True
        assert result.beat_advanced is True
        assert result.npcs_mentioned == ["Vlaxos"]
        assert result.locked_facts_used == ["map_hidden_in_cellar"]

    def test_meta_fields_serialize_round_trip(self) -> None:
        result = NarrativeResult(
            narrative="x" * 60,
            tone="tense",
            beat_advanced=True,
            npcs_mentioned=["Aldric"],
        )
        dumped = result.model_dump()
        rebuilt = NarrativeResult.model_validate(dumped)
        assert rebuilt == result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ai/test_models.py::TestNarrativeResultMeta -v
```

Expected: tests fail with `ValidationError` (extra fields not permitted) or `AttributeError`.

- [ ] **Step 3: Extend `NarrativeResult` in `ai/models.py`**

Replace the `NarrativeResult` class:

```python
class NarrativeResult(BaseModel):
    """Output of the Narrator: immersive narrative description of resolved action.

    The ``narrative`` and ``tone`` fields drive the Discord embed shown to the
    player. The remaining fields are *meta-telemetry* for the Story Director's
    drift detector — they are NEVER displayed to the player.
    """

    narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"]
    # Meta telemetry — defaults preserve backward compatibility.
    scene_goal_touched: bool = False
    """True if the narration referenced the current scene objective."""
    beat_advanced: bool = False
    """True if the scene moved forward (new info, location change, NPC reaction, decision point)."""
    npcs_mentioned: list[str] = Field(default_factory=list)
    """Names of NPCs the narration explicitly mentioned."""
    locked_facts_used: list[str] = Field(default_factory=list)
    """IDs of locked facts the narration incorporated."""
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/ai/test_models.py::TestNarrativeResultMeta -v
```

Expected: 3 passed.

- [ ] **Step 5: Run narrator + pipeline tests for regression**

```bash
uv run pytest tests/ai/test_narrator.py tests/bot/test_action_pipeline.py -q --tb=no
```

Expected: same pass count as B0.

- [ ] **Step 6: Commit**

```bash
git add ai/models.py tests/ai/test_models.py
git commit -m "feat(narrator): extend NarrativeResult with meta-telemetry fields"
```

---

## Task B2: Narrator Prompt + Parser for Meta Fields

**Goal:** Update the LLM prompt to instruct Qwen to emit the meta fields. Update `Narrator._call_llm` to parse them.

**Files:**
- Modify: `ai/prompts/system_narrator.txt` (output schema block at end)
- Modify: `ai/narrator.py` (`_call_llm` parsing)
- Modify: `tests/ai/test_narrator.py` (add `test_narrate_parses_meta_fields`)

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_narrator.py`:

```python
class TestNarratorMetaParsing:
    """Narrator parses meta fields when LLM emits them, falls back to defaults otherwise."""

    def test_narrate_parses_meta_fields(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "Vlaxos parries with a snarl, pushing you back toward the cellar door.",
                "tone": "tense",
                "scene_goal_touched": True,
                "beat_advanced": True,
                "npcs_mentioned": ["Vlaxos"],
                "locked_facts_used": ["map_hidden_in_cellar"],
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Player attacks Vlaxos. Hit, 8 damage.",
            context_prompt="Context.",
        )
        assert result.scene_goal_touched is True
        assert result.beat_advanced is True
        assert result.npcs_mentioned == ["Vlaxos"]
        assert result.locked_facts_used == ["map_hidden_in_cellar"]

    def test_narrate_defaults_meta_fields_when_llm_omits(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            return {
                "narrative": "A long enough narrative that exceeds fifty characters with ease.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert result.scene_goal_touched is False
        assert result.beat_advanced is False
        assert result.npcs_mentioned == []
        assert result.locked_facts_used == []
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/ai/test_narrator.py::TestNarratorMetaParsing -v
```

Expected: tests fail because `narrator._call_llm` constructs `NarrativeResult` with only `narrative` and `tone`.

- [ ] **Step 3: Update `_call_llm` in `ai/narrator.py`**

In `ai/narrator.py`, in `_call_llm`, replace the construction of `result`:

```python
        result = NarrativeResult(
            narrative=str(data.get("narrative", "")),
            tone=data.get("tone", "dramatic"),  # type: ignore[arg-type]
            scene_goal_touched=bool(data.get("scene_goal_touched", False)),
            beat_advanced=bool(data.get("beat_advanced", False)),
            npcs_mentioned=list(data.get("npcs_mentioned") or []),
            locked_facts_used=list(data.get("locked_facts_used") or []),
        )
```

- [ ] **Step 4: Update narrator system prompt**

Replace the final `Output schema` block in `ai/prompts/system_narrator.txt`:

```
Output schema (the only valid format):
{
  "narrative": "<your 2-4 sentence description, ONLY this is shown to the player>",
  "tone": "<dramatic|tense|humorous|somber>",
  "scene_goal_touched": <true if your narrative referenced the current scene goal from [STORY DIRECTION], else false>,
  "beat_advanced": <true if the scene moved forward (new info, NPC reaction, location change, decision point) — false if you only described atmosphere>,
  "npcs_mentioned": [<list of NPC names you used in the narrative>],
  "locked_facts_used": [<list of locked-fact IDs from the context that you incorporated>]
}

CRITICAL — the player NEVER sees fields other than `narrative`. The `scene_goal_touched`, `beat_advanced`, `npcs_mentioned`, and `locked_facts_used` fields are internal telemetry consumed by the Story Director. Always emit them honestly so the system can detect if the story has stalled.
```

- [ ] **Step 5: Run new tests**

```bash
uv run pytest tests/ai/test_narrator.py -v
```

Expected: 16 (Phase A) + 2 (new) = 18 passed.

- [ ] **Step 6: Run pipeline tests for regression**

```bash
uv run pytest tests/bot/test_action_pipeline.py -q --tb=no
```

Expected: same as baseline.

- [ ] **Step 7: Commit**

```bash
git add ai/narrator.py ai/prompts/system_narrator.txt tests/ai/test_narrator.py
git commit -m "feat(narrator): emit + parse meta-telemetry fields for drift detection"
```

---

## Task B3: `DriftTracker` Module

**Goal:** Create a per-campaign rolling window that tracks the last 5 `beat_advanced` flags. Exposes `record(campaign_id, beat_advanced: bool)` and `is_drifting(campaign_id) -> bool`. Drift is true when 3 of the last 5 records have `beat_advanced=False`.

**Files:**
- Create: `bot/pipeline/drift_tracker.py`
- Create: `tests/bot/pipeline/test_drift_tracker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/bot/pipeline/test_drift_tracker.py`:

```python
"""Unit tests for DriftTracker."""

import pytest

from bot.pipeline.drift_tracker import DriftTracker


@pytest.fixture
def tracker() -> DriftTracker:
    return DriftTracker()


def test_initial_state_no_drift(tracker: DriftTracker) -> None:
    assert tracker.is_drifting("cmp_1") is False


def test_single_stale_record_no_drift(tracker: DriftTracker) -> None:
    tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is False


def test_three_stale_in_three_drifts(tracker: DriftTracker) -> None:
    """3 of last 3 are stale → drift."""
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True


def test_three_stale_in_five_drifts(tracker: DriftTracker) -> None:
    """3 of last 5 are stale → drift."""
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True


def test_two_stale_in_five_no_drift(tracker: DriftTracker) -> None:
    """Only 2 of last 5 are stale → no drift."""
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    assert tracker.is_drifting("cmp_1") is False


def test_window_only_keeps_last_five(tracker: DriftTracker) -> None:
    """Old stale records age out of the window."""
    for _ in range(5):
        tracker.record("cmp_1", beat_advanced=False)  # 5 stale → drift
    assert tracker.is_drifting("cmp_1") is True
    for _ in range(5):
        tracker.record("cmp_1", beat_advanced=True)  # 5 advances → flush
    assert tracker.is_drifting("cmp_1") is False


def test_campaigns_isolated(tracker: DriftTracker) -> None:
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True
    assert tracker.is_drifting("cmp_2") is False


def test_reset_clears_history(tracker: DriftTracker) -> None:
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True
    tracker.reset("cmp_1")
    assert tracker.is_drifting("cmp_1") is False
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/bot/pipeline/test_drift_tracker.py -v
```

Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `DriftTracker`**

Create `bot/pipeline/drift_tracker.py`:

```python
"""DriftTracker — per-campaign rolling window of narrator beat-advancement flags.

A narration is "stale" when ``NarrativeResult.beat_advanced`` is False (the
scene did not move forward). When 3 of the last 5 narrations are stale, the
campaign is "drifting" and the Story Director should run on the next turn
to reorient the narrator.

Implementation note: in-process state, keyed by campaign_id. A campaign's
history persists for the bot process lifetime — this is fine for the MVP.
For multi-process deployments later, swap the dict for Redis.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

WINDOW_SIZE = 5
"""Number of recent narrations the tracker considers."""

DRIFT_THRESHOLD = 3
"""Number of stale narrations within the window that trigger a drift signal."""


@dataclass
class DriftTracker:
    """Tracks the last ``WINDOW_SIZE`` ``beat_advanced`` flags per campaign.

    Drift fires when at least ``DRIFT_THRESHOLD`` of the recorded flags are
    False (i.e. the scene has not advanced).
    """

    _windows: dict[str, deque[bool]] = field(default_factory=dict)

    def record(self, campaign_id: str, *, beat_advanced: bool) -> None:
        """Record one narration's beat-advanced flag for ``campaign_id``."""
        window = self._windows.setdefault(
            campaign_id, deque(maxlen=WINDOW_SIZE)
        )
        window.append(beat_advanced)

    def is_drifting(self, campaign_id: str) -> bool:
        """Return True when at least DRIFT_THRESHOLD of the last
        WINDOW_SIZE narrations have ``beat_advanced=False``.

        An empty or short window cannot drift.
        """
        window = self._windows.get(campaign_id)
        if window is None:
            return False
        stale = sum(1 for advanced in window if not advanced)
        return stale >= DRIFT_THRESHOLD

    def reset(self, campaign_id: str) -> None:
        """Clear the rolling window for ``campaign_id``."""
        self._windows.pop(campaign_id, None)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/bot/pipeline/test_drift_tracker.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Lint + full suite**

```bash
uv run ruff check bot/pipeline/drift_tracker.py tests/bot/pipeline/test_drift_tracker.py
uv run pytest -q --tb=no 2>&1 | tail -3
```

Expected: clean lint, full suite green.

- [ ] **Step 6: Commit**

```bash
git add bot/pipeline/drift_tracker.py tests/bot/pipeline/test_drift_tracker.py
git commit -m "feat(pipeline): add DriftTracker — rolling window for narrator stale-detection"
```

---

## Task B4: Extend `DirectorNote` with Direction Fields

**Goal:** Add 5 new fields (`current_objective`, `next_beat_hint`, `forbidden_topics`, `required_mentions`, `stale_quest_ids`) to `DirectorNote`. All optional with defaults.

**Files:**
- Modify: `ai/models.py` (`DirectorNote`)
- Modify: `tests/ai/test_models.py` (add `TestDirectorNoteDirection` class)

- [ ] **Step 1: Write the failing tests**

Append to `tests/ai/test_models.py`:

```python
class TestDirectorNoteDirection:
    def test_old_payload_parses_with_defaults(self) -> None:
        note = DirectorNote(
            coherence_issues=["NPC contradiction"],
            suggested_hooks=["Bring back Aldric"],
            priority="medium",
        )
        assert note.current_objective == ""
        assert note.next_beat_hint == ""
        assert note.forbidden_topics == []
        assert note.required_mentions == []
        assert note.stale_quest_ids == []

    def test_new_payload_carries_direction(self) -> None:
        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="low",
            current_objective="Retrieve the dungeon map before Vlaxos uses it.",
            next_beat_hint="Encounter the spy who knows the cellar entrance.",
            forbidden_topics=["map_hidden_in_cellar"],
            required_mentions=["Aldric", "Elena"],
            stale_quest_ids=["quest_42"],
        )
        assert note.current_objective.startswith("Retrieve")
        assert note.required_mentions == ["Aldric", "Elena"]

    def test_direction_fields_serialize_round_trip(self) -> None:
        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="high",
            current_objective="Stop the ritual.",
            forbidden_topics=["ritual_target"],
        )
        rebuilt = DirectorNote.model_validate(note.model_dump())
        assert rebuilt == note
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/ai/test_models.py::TestDirectorNoteDirection -v
```

Expected: tests fail (extra fields not allowed or AttributeError).

- [ ] **Step 3: Extend `DirectorNote` in `ai/models.py`**

Replace the `DirectorNote` class:

```python
class DirectorNote(BaseModel):
    """Output of the Story Director: coherence analysis and explicit narrative direction.

    The legacy fields (``coherence_issues``, ``suggested_hooks``, ``priority``) feed
    the semantic memory layer. The newer "direction" fields (``current_objective``,
    ``next_beat_hint``, ``forbidden_topics``, ``required_mentions``, ``stale_quest_ids``)
    feed the Narrator's prompt as an explicit ``[STORY DIRECTION]`` block on the
    next turn — they tell the narrator what to set up, what to avoid re-revealing,
    and which NPCs to weave back in.
    """

    coherence_issues: list[str]
    suggested_hooks: list[str]
    priority: Literal["low", "medium", "high"]
    # Direction — defaults preserve backward compatibility.
    current_objective: str = ""
    """One-sentence phrasing of what the players are pursuing right now."""
    next_beat_hint: str = ""
    """What the next narration should set up — a clue, NPC, complication."""
    forbidden_topics: list[str] = Field(default_factory=list)
    """Already-revealed facts the narrator must NOT re-reveal."""
    required_mentions: list[str] = Field(default_factory=list)
    """NPCs or items the next narration should weave back in."""
    stale_quest_ids: list[str] = Field(default_factory=list)
    """IDs of quests that have been ignored too long."""
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/ai/test_models.py -v
```

Expected: 6 passed (3 NarrativeResult + 3 DirectorNote).

- [ ] **Step 5: Run story director + pipeline regression**

```bash
uv run pytest tests/ai/test_story_director.py tests/bot/test_action_pipeline.py -q --tb=no
```

Expected: same baseline.

- [ ] **Step 6: Commit**

```bash
git add ai/models.py tests/ai/test_models.py
git commit -m "feat(director): extend DirectorNote with explicit direction fields"
```

---

## Task B5: Story Director Prompt + Parser for Direction Fields

**Goal:** Update Story Director's system prompt to ask for the new fields. Update `StoryDirector.check_coherence` to parse them.

**Files:**
- Modify: `ai/prompts/system_story_director.txt` (output schema)
- Modify: `ai/story_director.py` (parsing in `check_coherence`)
- Modify: `tests/ai/test_story_director.py` (add `test_check_coherence_parses_direction`)

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_story_director.py`:

```python
class TestStoryDirectorDirection:
    def test_check_coherence_parses_direction_fields(
        self, monkeypatch, story_director, semantic_memory_mock,
    ) -> None:
        # Mock both calls (brainstorm + generate). Brainstorm can be empty.
        call_count = {"n": 0}
        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Brainstorm call
                return {"options": []}
            # Generate call
            return {
                "coherence_issues": [],
                "suggested_hooks": ["Bring back Elena."],
                "priority": "medium",
                "current_objective": "Retrieve the dungeon map.",
                "next_beat_hint": "Encounter the spy at the well.",
                "forbidden_topics": ["map_in_cellar"],
                "required_mentions": ["Elena"],
                "stale_quest_ids": [],
            }
        monkeypatch.setattr(story_director._client, "chat_json", fake_chat_json)

        note = story_director.check_coherence(
            campaign_id="cmp_1", context_prompt="...",
        )
        assert note.current_objective == "Retrieve the dungeon map."
        assert note.next_beat_hint == "Encounter the spy at the well."
        assert note.forbidden_topics == ["map_in_cellar"]
        assert note.required_mentions == ["Elena"]
```

If `story_director` and `semantic_memory_mock` fixtures don't exist in `tests/ai/test_story_director.py`, scaffold them based on the existing test file's setup (read the file to confirm — likely there's a fixture for `OllamaClient` and `SemanticMemory` already).

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/ai/test_story_director.py::TestStoryDirectorDirection -v
```

Expected: failure (parser doesn't pull new fields).

- [ ] **Step 3: Update `check_coherence` in `ai/story_director.py`**

In `ai/story_director.py`, replace the construction of `note`:

```python
        note = DirectorNote(
            coherence_issues=data.get("coherence_issues", []),
            suggested_hooks=unique_hooks,
            priority=data.get("priority", "low"),
            current_objective=str(data.get("current_objective", "")),
            next_beat_hint=str(data.get("next_beat_hint", "")),
            forbidden_topics=list(data.get("forbidden_topics") or []),
            required_mentions=list(data.get("required_mentions") or []),
            stale_quest_ids=list(data.get("stale_quest_ids") or []),
        )
```

- [ ] **Step 4: Update story director system prompt**

Append to `ai/prompts/system_story_director.txt` (or replace the existing output schema block):

Find the JSON schema instruction in the file. Add to it:

```
Direction fields (fed back into the next Narrator prompt):

- "current_objective": "Short sentence describing what the players are pursuing right now"
- "next_beat_hint": "What the next narration should set up (a clue, an NPC, a complication)"
- "forbidden_topics": ["List of facts the narrator must NOT re-reveal (already known)"]
- "required_mentions": ["NPCs or items the next narration should weave back in"]
- "stale_quest_ids": ["IDs of quests that have been ignored too long"]

These direction fields are NOT shown to the player — they steer the narrator on the next turn.
```

(Read the existing `system_story_director.txt` first to find the right place to insert.)

- [ ] **Step 5: Run new test + regression**

```bash
uv run pytest tests/ai/test_story_director.py -v --tb=short
```

Expected: all pass.

```bash
uv run pytest -q --tb=no 2>&1 | tail -3
```

Expected: full suite green.

- [ ] **Step 6: Commit**

```bash
git add ai/story_director.py ai/prompts/system_story_director.txt tests/ai/test_story_director.py
git commit -m "feat(director): emit + parse explicit direction fields for narrator steering"
```

---

## Task B6: Wire DriftTracker into Pipeline + Story Director Cadence

**Goal:** Plug the `DriftTracker` into `PipelineRunner._continue_from_resolution`. After narrator returns, record the `beat_advanced` flag. Decide whether to run Story Director on the *next* turn based on cadence + drift + combat-end signals. Run Story Director async (background) so it doesn't block this turn's narrator.

**Files:**
- Modify: `bot/pipeline/orchestrator.py` (PipelineRunner — add tracker singleton, record after narrate, schedule director)
- Modify: `bot/action_pipeline.py` (re-export `DriftTracker` if used externally)
- Modify: `tests/bot/test_action_pipeline.py` (add tests verifying tracker is consulted)

### Design

`PipelineRunner` will reference a **module-level singleton** `DriftTracker` for in-process state:

```python
# bot/pipeline/orchestrator.py
from bot.pipeline.drift_tracker import DriftTracker

_DRIFT_TRACKER = DriftTracker()


def get_drift_tracker() -> DriftTracker:
    """Module-level singleton. Test isolation done via .reset(campaign_id)."""
    return _DRIFT_TRACKER
```

Story Director cadence — fire when ANY of:
- `interaction_count % 6 == 0` (every 6 player actions; counter lives on `GameSession`)
- `combat_just_ended` (combat was active last turn, now resolved)
- `tracker.is_drifting(campaign_id)` is True
- `force_director_run=True` (set by `/story_catch_up`)

After narrate, do:
1. `tracker.record(campaign_id, beat_advanced=narrative.beat_advanced)`
2. Decide via `should_run_director(...)`. If yes:
   - `asyncio.create_task(story_director.check_coherence(campaign_id, context))` — fire-and-forget
   - The result lands in semantic memory and is cached for the next turn's prompt assembly

For Phase B, "next turn's prompt assembly" means: the next call to `_continue_from_resolution` reads the latest cached `DirectorNote` (Task B7 wires this in).

### Tasks

- [ ] **Step 1: Write the failing tests**

Append to `tests/bot/test_action_pipeline.py`:

```python
class TestDriftTrackerWiring:
    def test_tracker_records_beat_advanced_after_narrate(
        self, ..., monkeypatch,  # use existing fixtures
    ) -> None:
        from bot.pipeline.orchestrator import get_drift_tracker
        tracker = get_drift_tracker()
        tracker.reset("cmp_test")

        # Construct an ActionPipeline with mocked deps that produce a
        # NarrativeResult with beat_advanced=True. Run process_interpreted_action.
        # ... (use the same fixture pattern as adjacent tests in this file)

        # After process completes:
        assert "cmp_test" in tracker._windows
        assert list(tracker._windows["cmp_test"])[-1] is True

    def test_force_director_run_triggers_story_director(self, ..., monkeypatch) -> None:
        # Set runner.force_director_run = True
        # Verify story_director.check_coherence was called (mocked)
        ...
```

(Adapt these stubs to the actual fixture style of `test_action_pipeline.py` — read the file to find the existing `pipeline` fixture or factory.)

- [ ] **Step 2: Add `get_drift_tracker` + helpers to orchestrator**

In `bot/pipeline/orchestrator.py`, near the top imports, add:

```python
from bot.pipeline.drift_tracker import DriftTracker
```

Below the existing module-level helpers, add:

```python
_DRIFT_TRACKER = DriftTracker()


def get_drift_tracker() -> DriftTracker:
    """Module-level singleton DriftTracker.

    Tests reset state per-campaign via ``tracker.reset(campaign_id)``.
    """
    return _DRIFT_TRACKER


def should_run_director(
    *,
    interaction_count: int,
    combat_just_ended: bool,
    drift_detected: bool,
    force: bool,
) -> bool:
    """Decide whether the Story Director should run after this turn.

    Triggers (any one is sufficient):
    - ``interaction_count`` is a positive multiple of 6
    - the previous turn ended a combat
    - the drift tracker reports a stale narrator
    - the caller forced a run via ``/story_catch_up``
    """
    if force:
        return True
    if drift_detected:
        return True
    if combat_just_ended:
        return True
    if interaction_count > 0 and interaction_count % 6 == 0:
        return True
    return False
```

- [ ] **Step 3: Wire into `PipelineRunner`**

Add to `PipelineRunner` dataclass:

```python
    force_director_run: bool = False
    """When True, the next pipeline run unconditionally schedules the Story Director."""

    _last_combat_active: bool = False
    """Tracks whether the previous turn had an active combat (to detect end)."""
```

In `_continue_from_resolution`, AFTER the `narrate.call_narrator(...)` invocation succeeds and returns a `NarrativeResult`, BEFORE returning the final `ActionPipelineResult`:

```python
        # --- Drift tracking + Story Director scheduling ---
        tracker = get_drift_tracker()
        tracker.record(self.campaign_id, beat_advanced=narrative.beat_advanced)

        combat_active_now = self.combat_state is not None and self.combat_state.is_active
        combat_just_ended = self._last_combat_active and not combat_active_now
        self._last_combat_active = combat_active_now

        interaction_count = (
            self.session.interaction_count if self.session is not None else 0
        )

        if should_run_director(
            interaction_count=interaction_count,
            combat_just_ended=combat_just_ended,
            drift_detected=tracker.is_drifting(self.campaign_id),
            force=self.force_director_run,
        ):
            self.force_director_run = False  # consume
            self._schedule_story_director(context_prompt=context_prompt)
```

And add the helper method on `PipelineRunner`:

```python
    def _schedule_story_director(self, *, context_prompt: str) -> None:
        """Fire-and-forget Story Director run. Result lands in semantic memory."""
        # Story Director is sync (uses sync OllamaClient). Run via asyncio.to_thread.
        from ai.story_director import StoryDirector
        from memory.semantic import SemanticMemory

        async def _run() -> None:
            try:
                # The runner doesn't carry a SemanticMemory instance directly;
                # in production the caller (ActionHandlerCog) instantiates one
                # per-bot. For the runner-internal path, we keep a lazy local
                # instance backed by the default ChromaDB persist directory.
                # Tests should monkeypatch SemanticMemory or this whole method.
                semantic = SemanticMemory()
                director = StoryDirector(
                    self.interpreter._client if hasattr(self.interpreter, "_client") else None,  # safe access
                    semantic,
                )
                await asyncio.to_thread(
                    director.check_coherence, self.campaign_id, context_prompt,
                )
            except Exception:
                logger.warning("Background StoryDirector run failed", exc_info=True)

        asyncio.create_task(_run())
```

**Note:** the `StoryDirector` constructor needs an `OllamaClient` instance. The `PipelineRunner` doesn't directly hold one (it holds `Interpreter` and `Narrator`, which both wrap a client). The cleanest fix: extend `PipelineRunner` with an optional `ollama_client: OllamaClient | None = None` field, set by the Facade, and use it here. Update the Facade in `bot/action_pipeline.py` to pass it through:

In `ActionPipeline.__init__`:
```python
        ollama_client: "OllamaClient | None" = None,  # extracted from interpreter._client if None
        ...
        self._runner = PipelineRunner(
            ...,
            ollama_client=ollama_client,
        )
```

In `PipelineRunner` add field:
```python
    ollama_client: Any = None  # OllamaClient | None — typed Any to avoid heavy import
```

In `_schedule_story_director`, use `self.ollama_client or self.interpreter._client`.

Wait — both `Interpreter` and `Narrator` already use a client. We can just reach in: `self.narrator._client`. That's what the rest of the code does. Skip the `ollama_client` field; use `self.narrator._client`:

```python
                director = StoryDirector(self.narrator._client, semantic)
```

This couples to a private attribute but matches existing patterns in the codebase. Acceptable.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/bot/test_action_pipeline.py::TestDriftTrackerWiring -v
```

Expected: 2 new tests pass.

- [ ] **Step 5: Run regression**

```bash
uv run pytest -q --tb=no 2>&1 | tail -3
```

Expected: 2090 + 5 (B1) + 2 (B2) + 8 (B3) + 3 (B4) + 1 (B5) + 2 (B6) = ~2111 passed (rough estimate; actual pass count depends on test interaction).

- [ ] **Step 6: Commit**

```bash
git add bot/pipeline/orchestrator.py tests/bot/test_action_pipeline.py
git commit -m "feat(pipeline): wire DriftTracker + Story Director cadence into runner"
```

---

## Task B7: Inject `[STORY DIRECTION]` Block into Narrator Prompt

**Goal:** When a fresh `DirectorNote` exists for the campaign (cached from the most recent Story Director run), prepend a `[STORY DIRECTION]` block to the user message of the narrator call. The narrator sees `current_objective`, `next_beat_hint`, `forbidden_topics`, `required_mentions`. The player never sees this.

**Files:**
- Modify: `ai/story_director.py` (add static `cached_note_for(campaign_id)` cache)
- Modify: `bot/pipeline/narrate.py` (`call_narrator` signature gets `director_note: DirectorNote | None` parameter; inject block into context)
- Modify: `bot/pipeline/orchestrator.py` (`_continue_from_resolution` passes the cached note to `call_narrator`)
- Modify: `tests/ai/test_narrator.py` (verify the `[STORY DIRECTION]` block lands in the user message)

### Design

The Story Director runs in the background after a turn (Task B6). Its output (`DirectorNote`) needs to be available for the *next* turn's narrator. Options:
- **A)** Persist to DB (heavy)
- **B)** In-process cache keyed by campaign_id (light, matches the singleton DriftTracker pattern)

Use **B**. Add to `ai/story_director.py`:

```python
_LATEST_NOTES: dict[str, "DirectorNote"] = {}


def cached_note_for(campaign_id: str) -> "DirectorNote | None":
    """Return the most recent DirectorNote for ``campaign_id``, if any."""
    return _LATEST_NOTES.get(campaign_id)


def _store_latest_note(campaign_id: str, note: "DirectorNote") -> None:
    _LATEST_NOTES[campaign_id] = note


def reset_latest_notes() -> None:
    """Test helper — clear the cache."""
    _LATEST_NOTES.clear()
```

In `StoryDirector.check_coherence`, at the end (after `_store_in_memory`), call `_store_latest_note(campaign_id, note)`.

### Tasks

- [ ] **Step 1: Write the failing tests**

Append to `tests/ai/test_narrator.py`:

```python
class TestNarratorDirectionInjection:
    def test_call_narrator_with_director_note_injects_direction_block(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        from ai.models import DirectorNote
        captured_messages: list[dict] = []

        def fake_chat_json(model, messages, *args, **kwargs):
            captured_messages.append(messages[-1])  # user message
            return {"narrative": "x" * 60, "tone": "dramatic"}

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)

        note = DirectorNote(
            coherence_issues=[],
            suggested_hooks=[],
            priority="low",
            current_objective="Find the map.",
            next_beat_hint="Spy at the well.",
            required_mentions=["Aldric"],
            forbidden_topics=["map_in_cellar"],
        )

        # In Task B7, narrate accepts director_note as a kwarg:
        narrator.narrate(
            action_result_text="Player searches.",
            context_prompt="Context.",
            director_note=note,
        )

        user_content = captured_messages[0]["content"]
        assert "[STORY DIRECTION]" in user_content
        assert "Find the map." in user_content
        assert "Spy at the well." in user_content
        assert "Aldric" in user_content
        assert "map_in_cellar" in user_content
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/ai/test_narrator.py::TestNarratorDirectionInjection -v
```

Expected: TypeError on `director_note` kwarg.

- [ ] **Step 3: Add `cached_note_for` and storage to `ai/story_director.py`**

At module top of `ai/story_director.py`:

```python
_LATEST_NOTES: dict[str, DirectorNote] = {}


def cached_note_for(campaign_id: str) -> DirectorNote | None:
    """Most recent DirectorNote for ``campaign_id``, if any."""
    return _LATEST_NOTES.get(campaign_id)


def _store_latest_note(campaign_id: str, note: DirectorNote) -> None:
    _LATEST_NOTES[campaign_id] = note


def reset_latest_notes() -> None:
    """Test helper — clear the cache."""
    _LATEST_NOTES.clear()
```

In `StoryDirector.check_coherence`, at the end, replace:
```python
        self._store_in_memory(campaign_id, note)
        return note
```
with:
```python
        self._store_in_memory(campaign_id, note)
        _store_latest_note(campaign_id, note)
        return note
```

- [ ] **Step 4: Extend `Narrator.narrate` and `_call_llm` with `director_note` kwarg**

In `ai/narrator.py`, update `narrate` signature:

```python
    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
        has_npc_dialogue: bool = False,
        director_note: "DirectorNote | None" = None,
    ) -> NarrativeResult:
        """... (existing docstring + new line:) ``director_note`` is the most
        recent Story Director output; when provided, a [STORY DIRECTION] block
        is prepended to the user message."""
```

Pass `director_note` through to both `_call_llm` calls (Tier 1 and Tier 2). Update `_call_llm`:

```python
    def _call_llm(
        self,
        *,
        action_result_text: str,
        context_prompt: str,
        language: str,
        player_intent: str,
        outcome_facts: str,
        has_npc_dialogue: bool,
        simplified: bool,
        director_note: "DirectorNote | None" = None,
    ) -> NarrativeResult:
        ...
        sections: list[str] = []
        if director_note is not None and (
            director_note.current_objective
            or director_note.next_beat_hint
            or director_note.required_mentions
            or director_note.forbidden_topics
        ):
            direction_block = self._format_direction_block(director_note)
            sections.append(direction_block)
        sections.extend([context_prompt, f"## What happened\n{action_result_text}"])
        if not simplified:
            ...  # existing optional sections
```

Add helper:

```python
    @staticmethod
    def _format_direction_block(note: "DirectorNote") -> str:
        """Format the Story Director's direction fields into the narrator prompt."""
        lines = ["[STORY DIRECTION — written by Story Director]"]
        if note.current_objective:
            lines.append(f"Current objective: {note.current_objective}")
        if note.next_beat_hint:
            lines.append(f"Next beat hint: {note.next_beat_hint}")
        if note.required_mentions:
            lines.append("Re-mention if natural: " + ", ".join(note.required_mentions))
        if note.forbidden_topics:
            lines.append("Do NOT re-reveal: " + ", ".join(note.forbidden_topics))
        return "\n".join(lines)
```

Add the import at the top of `ai/narrator.py`:
```python
from ai.models import DirectorNote, NarrativeResult
```

- [ ] **Step 5: Wire `cached_note_for` into the orchestrator**

In `bot/pipeline/narrate.py`, update `call_narrator` to accept and forward `director_note`:

```python
async def call_narrator(
    *,
    narrator: Narrator,
    outcome: MechanicsOutcome,
    context_prompt: str,
    language: str,
    has_npc_dialogue: bool = False,
    director_note: "DirectorNote | None" = None,
) -> NarrativeResult:
    """... existing docstring ..."""
    # ... existing body, but pass director_note through to narrator.narrate(...)
```

In `bot/pipeline/orchestrator.py`, in `_continue_from_resolution`, BEFORE calling `narrate.call_narrator(...)`:

```python
        from ai.story_director import cached_note_for
        director_note = cached_note_for(self.campaign_id)
```

Pass `director_note=director_note` to `narrate.call_narrator(...)`.

In `bot/action_pipeline.py`, update the `_call_narrator` shim if it exists to forward `director_note` (or add `director_note=None` default).

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/ai/test_narrator.py tests/ai/test_story_director.py tests/bot/test_action_pipeline.py -q --tb=short
```

Expected: green.

- [ ] **Step 7: Run full suite + lint**

```bash
uv run pytest -q --tb=no 2>&1 | tail -3
uv run ruff check ai/ bot/pipeline/
```

Expected: green, clean.

- [ ] **Step 8: Commit**

```bash
git add ai/narrator.py ai/story_director.py bot/pipeline/narrate.py bot/pipeline/orchestrator.py bot/action_pipeline.py tests/ai/test_narrator.py
git commit -m "feat(narrator): inject [STORY DIRECTION] block from cached DirectorNote"
```

---

## Task B8: `/story_catch_up` Slash Command

**Goal:** Add a `/story_catch_up` Discord slash command that forces a Story Director run for the campaign in the current channel and posts a brief recap embed.

**Files:**
- Modify: `bot/cogs/session.py` (add the command — or create `bot/cogs/narrative.py` if cleaner)
- Modify: `bot/embeds/narrative_embed.py` (add `build_catch_up_embed` if not already covered)
- Modify: `bot/bot.py` (cog loader if a new cog file is added)
- Add: `tests/bot/test_cog_session.py` test for the command (or `test_cog_narrative.py`)

### Design

The command flow:
1. User invokes `/story_catch_up` in a campaign channel.
2. Cog identifies the active `GameSession` for that channel.
3. Cog sets `pipeline.force_director_run = True` (so the *next* player action will trigger a director run via the cadence wiring from B6).
4. Cog also synchronously runs the Story Director **right now** to give an immediate recap (use `await asyncio.to_thread(director.check_coherence, ...)`).
5. Cog posts a recap embed: title "📖 Le MJ recadre la scène", description = `note.current_objective` + bullet list of `note.suggested_hooks[:3]`.

This gives the player both immediate feedback AND ensures the next narration uses the fresh direction.

### Tasks

- [ ] **Step 1: Write the failing test**

Add to `tests/bot/test_cog_session.py` (or a new file):

```python
class TestStoryCatchUpCommand:
    @pytest.mark.asyncio
    async def test_story_catch_up_runs_director_and_posts_recap(
        self, ..., monkeypatch,  # use existing cog fixtures
    ) -> None:
        # Mock StoryDirector.check_coherence to return a known DirectorNote.
        # Invoke /story_catch_up.
        # Assert:
        # - StoryDirector was called once
        # - The follow-up message is an embed with the current_objective
        # - The active pipeline's force_director_run was set
        ...
```

(Adapt to actual fixture style of the cog tests.)

- [ ] **Step 2: Implement the command**

Find `bot/cogs/session.py` and add a new slash command. Skeleton:

```python
import asyncio

import discord
from discord import app_commands
from discord.ext import commands

# ... existing imports ...

from ai.story_director import StoryDirector


class SessionCog(commands.Cog):
    # ... existing methods ...

    @app_commands.command(
        name="story_catch_up",
        description="Le MJ recadre la scène — récap de l'objectif actuel et des prochaines pistes.",
    )
    async def story_catch_up(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)

        # Identify the campaign for this channel (existing helper, e.g. self._game_session_for_channel)
        session = self._game_session_for_channel(interaction.channel_id)
        if session is None:
            await interaction.followup.send(
                "Aucune campagne active dans ce canal.", ephemeral=True,
            )
            return

        # Force the next player action to schedule a director run (cadence wiring from B6)
        if session.action_pipeline is not None:
            session.action_pipeline._runner.force_director_run = True

        # Also run synchronously now for immediate feedback
        director = StoryDirector(session.ollama_client, session.semantic_memory)
        # Build a context prompt using existing context assembler
        context_prompt = session.context_assembler.assemble(
            campaign_id=session.campaign_id,
            player_input="(catch-up request)",
        )
        try:
            note = await asyncio.to_thread(
                director.check_coherence, session.campaign_id, context_prompt,
            )
        except Exception:
            await interaction.followup.send(
                "Le MJ n'a pas pu rassembler ses idées. Réessaie dans un instant.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📖 Le MJ recadre la scène",
            description=note.current_objective or "Aucun objectif clair pour l'instant.",
            color=discord.Color.gold(),
        )
        if note.suggested_hooks:
            embed.add_field(
                name="Pistes possibles",
                value="\n".join(f"• {h}" for h in note.suggested_hooks[:3]),
                inline=False,
            )
        await interaction.followup.send(embed=embed)
```

(The exact API surfaces — `self._game_session_for_channel`, `session.action_pipeline`, `session.ollama_client`, `session.semantic_memory`, `session.context_assembler` — depend on the actual project. Read `bot/cogs/session.py` and `bot/game_session.py` first to confirm. Adapt the names to what already exists.)

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/bot/test_cog_session.py::TestStoryCatchUpCommand -v
```

Expected: 1 passed.

- [ ] **Step 4: Run full suite + lint**

```bash
uv run pytest -q --tb=no 2>&1 | tail -3
uv run ruff check bot/
```

- [ ] **Step 5: Commit**

```bash
git add bot/cogs/session.py tests/bot/test_cog_session.py
git commit -m "feat(session): add /story_catch_up — recap on demand via Story Director"
```

---

## Self-Review Checklist (after writing the plan)

1. **Spec coverage:**
   - Section 2 (Narrator Contract) → B1 + B2 ✓
   - Section 3 (Story Director cadence + structured output) → B3 + B4 + B5 + B6 + B8 ✓
   - `[STORY DIRECTION]` block injection → B7 ✓
2. **Type consistency:** `DirectorNote`, `NarrativeResult`, `DriftTracker` referenced consistently across tasks.
3. **Backward compatibility:** all model field additions are defaulted; old payloads parse cleanly.
4. **Test discipline:** every new behavior has at least one test. Existing behavior preserved by full-suite checks.
5. **Hard-coded names:** when the plan refers to `session.ollama_client`, `session.semantic_memory`, etc. (Task B8), the implementer must verify these exist on `GameSession` before using them. If they don't, the implementer should adapt to the real API and report DONE_WITH_CONCERNS noting the adaptation.

---

## Out of Scope (Phase B)

Deferred to Phases C–D:

- RAG densification — `SemanticIndexer` + populating `WORLD_LORE`/`NPC_SHEET`/`LOCATION_DETAIL`/`QUEST_DETAIL` (**Phase C**)
- Arc Tracker pinned message + `Campaign.arc_tracker_message_id` (**Phase D**)
