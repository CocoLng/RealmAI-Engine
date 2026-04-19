# Director's Cut — Narrative Anti-Drift Architecture

**Date:** 2026-04-20
**Status:** Draft
**Approach:** Pipeline split + structured Narrator contract + Story Director cadence + RAG densification + Arc Tracker UI + Narrator fallback

## Context

A real session with multiple players was abandoned mid-campaign. The reported failure mode:

> "On s'est retrouvé bloqué car pas d'idée de direction, puis le narrateur divaguait, et on a abandonné."

Three coupled problems:

1. **Narrator drift** — the Narrator produces literary text that doesn't reference the current arc, doesn't advance the plot, and circles around descriptive filler. After 3-5 such turns, players lose the thread.
2. **Player orientation** — there is no Discord-visible representation of "where you are in the story." Players must scroll back through chat to remember the current objective.
3. **Architectural fragility** — `bot/action_pipeline.py` is 1898 lines mixing interpretation, validation, mechanics, narration, persistence, and Discord rendering. Bug isolation is hard. Adding the structured Narrator contract or Story Director changes safely requires splitting first.

A separate latent bug: [`ai/narrator.py:78`](../../../ai/narrator.py:78) calls `chat_json` without protection. A bad LLM response (malformed JSON, Ollama timeout, empty response) raises `LLMParseError` uncaught. One bad turn can break a session.

## Principles (non-negotiable)

- **Free-form text input only.** Players continue to describe actions in natural language via `@bot` mention. **No predefined choices, buttons, or selects** for narrative actions. (Combat already uses buttons — that's mechanics, not narrative; out of scope here.)
- **The LLM narrates, the code arbitrates.** All structured outputs from the Narrator are *meta* (drift telemetry), never *prescriptive* (forced choices).
- **Backward compatibility.** Existing tests must pass with import-only updates. Existing slash commands behave the same.
- **Phased shippable.** Each phase leaves the bot in a working, tested state.

## Non-Goals

Explicitly out of scope for this spec (deferred to future cycles):

- SQLite concurrency hardening (WAL mode, timeout pragmas)
- i18n beyond current FR/EN dict
- Observability (Prometheus metrics, OpenTelemetry traces)
- Catalogue extension (backgrounds, feats, multiclass, shop)

These are real issues but addressing them now would dilute the focus on the player-abandonment failure mode.

---

## 1. Pipeline Split — `bot/action_pipeline.py` → `bot/pipeline/*`

### Problem

[`bot/action_pipeline.py`](../../../bot/action_pipeline.py) is 1898 lines mixing seven distinct concerns:

1. Interpretation (text → `InterpretedAction`)
2. Entity resolution (matching names to game objects)
3. Validation (legality checks)
4. Mechanics resolution (dispatch combat vs OOC; engine calls)
5. Beat completion check (deterministic + LLM fallback)
6. Context assembly + narration
7. Persistence + Discord rendering

This violates single-responsibility and makes downstream changes (Narrator contract, Story Director cadence) risky to land safely.

### Solution

Split into a `bot/pipeline/` package with stateless stage modules and a thin orchestrator. **No logic changes** in this phase — pure displacement.

```
bot/pipeline/
├── __init__.py          # Re-exports for callers
├── types.py             # PipelineContext, StageOutput dataclasses
├── interpret.py         # Phase 1-3 — interpret + resolve entities + validate
├── resolve.py           # Phase 4-4b — mechanics + beat completion + combat dispatch
├── narrate.py           # Phase 5-6 — context assembly + narrator
└── orchestrator.py      # Chain stages + persistence + embed rendering
```

### Module Contracts

**`bot/pipeline/types.py`**
```python
class PipelineContext(BaseModel):
    """Carried through every stage. Each stage adds fields, never mutates earlier ones."""
    campaign_id: str
    player_message_id: int
    player_input: str
    actor_name: str
    language: str = "fr"

    # Set by interpret stage
    interpreted: InterpretedAction | None = None
    validation_error: str | None = None

    # Set by resolve stage
    mechanics_outcome: MechanicsOutcome | None = None
    beat_advanced: bool = False
    combat_state_after: CombatState | None = None

    # Set by narrate stage
    assembled_context: str | None = None
    narrative_result: NarrativeResult | None = None
```

**Stage signature** (all stages):
```python
async def run(ctx: PipelineContext, *, deps: PipelineDeps) -> PipelineContext
```

`PipelineDeps` is a frozen dataclass holding injected services (LLM clients, repositories, semantic memory). This keeps stages pure: same input + same deps → same output.

### Backward Compatibility

The existing `ActionPipeline` class becomes a Facade in `bot/pipeline/orchestrator.py`:

```python
class ActionPipeline:
    """Backward-compatible facade. New code should use `pipeline.orchestrator.run()`."""
    def __init__(self, ...):
        self._deps = build_pipeline_deps(...)

    async def process_action(self, ...) -> ActionPipelineResult:
        ctx = PipelineContext(...)
        ctx = await orchestrator.run(ctx, deps=self._deps)
        return self._adapt_to_legacy_result(ctx)
```

Cogs (`bot/cogs/action_handler.py`, etc.) require **zero changes** in this phase. Tests update only the import path for direct unit tests of pipeline internals.

### Acceptance Criteria

- All existing tests pass with no logic modifications
- Each new module is < 500 lines
- `bot/action_pipeline.py` reduced to a Facade (< 200 lines) or deleted entirely if all consumers migrate

---

## 2. Narrator Contract — Structured Meta, Free-Form Narrative

### Problem

The Narrator output ([`ai/models.py:32`](../../../ai/models.py:32)) is `{narrative: str, tone: Literal[...]}`. There is no signal back to the Story Director about whether the narration referenced the current arc, advanced the beat, or contradicted established facts. Drift detection is impossible.

### Solution

Extend `NarrativeResult` with **meta telemetry fields** that are *invisible to the player* and consumed only by the Story Director's drift detector. The literary `narrative` field stays unchanged and is the only thing rendered in Discord.

### Changes

**`ai/models.py`** — Extend `NarrativeResult`:

```python
class NarrativeResult(BaseModel):
    narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"]
    # NEW — meta telemetry for drift detection
    scene_goal_touched: bool = False         # Did the narration reference current_objective?
    beat_advanced: bool = False              # Did the scene move forward (vs circle)?
    npcs_mentioned: list[str] = Field(default_factory=list)
    locked_facts_used: list[str] = Field(default_factory=list)
```

All new fields have defaults — old narrator outputs and tests remain valid.

**`ai/prompts/system_narrator.txt`** — Update JSON schema instruction:

```
You MUST respond with JSON containing:
{
  "narrative": "<the literary description, in the player's language>",
  "tone": "dramatic" | "tense" | "humorous" | "somber",
  "scene_goal_touched": true if you referenced the current scene objective,
  "beat_advanced": true if the scene moved forward (new info, new location, NPC reaction, decision point),
  "npcs_mentioned": [list of NPC names you used in the narrative],
  "locked_facts_used": [list of locked-fact IDs from the context that you incorporated]
}

Critical rules:
- ONLY `narrative` is shown to the player. The other fields are internal telemetry.
- Set `beat_advanced=false` if you only described atmosphere/scenery without progressing.
- Set `scene_goal_touched=true` if your narrative references or advances toward the current objective listed in the [STORY DIRECTION] block.
```

**`ai/narrator.py`** — Parse new fields:

```python
result = NarrativeResult(
    narrative=str(data.get("narrative", "")),
    tone=data.get("tone", "dramatic"),
    scene_goal_touched=bool(data.get("scene_goal_touched", False)),
    beat_advanced=bool(data.get("beat_advanced", False)),
    npcs_mentioned=list(data.get("npcs_mentioned", []) or []),
    locked_facts_used=list(data.get("locked_facts_used", []) or []),
)
```

### Drift Detection (consumer)

A new `bot/pipeline/drift_tracker.py` (or method on the orchestrator) maintains a rolling window per campaign:

- Last 5 `NarrativeResult.beat_advanced` flags
- Last 5 `NarrativeResult.scene_goal_touched` flags

If 3 of the last 5 narrations have `beat_advanced=False`, set `drift_detected=True` on the `PipelineContext`. The next call to Story Director (Section 3) treats this as a high-priority trigger.

### Acceptance Criteria

- `NarrativeResult` accepts old payloads (no meta fields → defaults applied)
- New fields populated when LLM returns them
- Drift tracker correctly fires on synthetic 3/5 stale narrations

---

## 3. Story Director — Cadence + Structured `StoryDirection` Output

### Problem

[`ai/story_director.py`](../../../ai/story_director.py) is triggered every 20 interactions (per the docstring on line 22). That is too rare to catch and rectify drift within a session. The output (`DirectorNote` with `coherence_issues`, `suggested_hooks`, `priority`) is consumed only as semantic memory injection — it never directs the *next* narration explicitly.

### Solution

Two changes:

1. **Triggers** — fire more often, and on signal events (combat end, drift detected).
2. **Output** — extend `DirectorNote` (or add a sibling `StoryDirection` model) with explicit direction fields fed into the next Narrator prompt.

### Trigger Logic

The Story Director runs **before** the Narrator on the next turn when ANY of:

- `interaction_count` is a multiple of 6 (down from 20)
- The previous turn ended a combat (`combat_state_after.is_resolved == True` and the prior turn had `is_active=True`)
- The drift tracker reports `drift_detected=True` (Section 2)
- Forced via `/story_catch_up` slash command (debug/recovery escape hatch)

Implementation lives in `bot/pipeline/narrate.py` (or `orchestrator.py`):

```python
def should_run_director(ctx: PipelineContext, history: PipelineHistory) -> bool:
    if history.interaction_count % 6 == 0:
        return True
    if history.combat_just_ended:
        return True
    if history.drift_detected:
        return True
    if ctx.force_director_run:  # set by /story_catch_up
        return True
    return False
```

### Structured Output

Extend [`ai/models.py:39`](../../../ai/models.py:39) `DirectorNote`:

```python
class DirectorNote(BaseModel):
    # Existing
    coherence_issues: list[str]
    suggested_hooks: list[str]
    priority: Literal["low", "medium", "high"]
    # NEW — explicit direction for the next narration
    current_objective: str = ""           # 1-sentence phrasing for player UI + narrator prompt
    next_beat_hint: str = ""              # What the narrator should set up next (internal)
    forbidden_topics: list[str] = Field(default_factory=list)  # Already-revealed facts
    required_mentions: list[str] = Field(default_factory=list) # NPCs/indices to weave in
    stale_quest_ids: list[str] = Field(default_factory=list)
```

All new fields have defaults — backward compatible.

### Prompt Update

**`ai/prompts/system_story_director.txt`** — Add to the JSON schema:

```
{
  ...existing fields...
  "current_objective": "Short sentence describing what the players are pursuing right now",
  "next_beat_hint": "What the next narration should set up (a clue, an NPC, a complication)",
  "forbidden_topics": ["List of facts the narrator must NOT re-reveal (already known)"],
  "required_mentions": ["NPCs or items the next narration should weave back in"],
  "stale_quest_ids": ["IDs of quests that have been ignored too long"]
}
```

### Narrator Prompt Injection

When `DirectorNote` is fresh (this turn or last turn), inject into the Narrator's user message:

```
[STORY DIRECTION — written by Story Director]
Current objective: {current_objective}
Next beat hint: {next_beat_hint}
Re-mention if natural: {required_mentions}
Do NOT re-reveal: {forbidden_topics}
```

This block is **opaque to the player** — only the Narrator sees it.

### Async Execution

The Story Director call adds 5-15s to the turn (9B model + thinking). To avoid blocking the player's perceived narration latency:

- Run Story Director **in parallel** with the mechanics resolution stage when triggered
- If it finishes before the Narrator stage starts, inject its output
- If it doesn't finish in time, use the previous `DirectorNote` (cached per campaign) and let the new one land for the *next* turn

Implementation: `asyncio.create_task` in `orchestrator.run()`.

### `/story_catch_up` Slash Command

New command in `bot/cogs/session.py` (or a new `narrative.py` cog):

- Forces a Story Director run on demand
- Posts a brief recap embed to the channel: "📖 Le MJ recadre la scène..." + the `current_objective`
- Useful when players feel lost — manual escape hatch

### Acceptance Criteria

- Story Director triggers on each of the 4 conditions (cadence, combat end, drift, command)
- New `DirectorNote` fields are populated and serializable
- Narrator prompt receives `[STORY DIRECTION]` block when present
- Async execution does not increase per-turn latency by more than 1-2s on average

---

## 4. RAG Densification — Populate Underutilized Document Types

### Problem (corrected from audit)

The audit incorrectly claimed "RAG is dead code." [`memory/context_assembler.py:76`](../../../memory/context_assembler.py:76) does call `self._semantic.query(...)` and renders Layer 4. The real issue:

[`memory/models.py:98`](../../../memory/models.py:98) defines 5 `SemanticDocumentType`s:
- `WORLD_LORE`
- `NPC_SHEET`
- `PAST_EVENT`
- `LOCATION_DETAIL`
- `QUEST_DETAIL`

Only `PAST_EVENT` is ever indexed (by the Story Director). The other four types are defined but **never populated** anywhere in the codebase. The semantic store is therefore mostly empty for any retrieval that would benefit from world canon (NPC personalities, location lore, quest details).

### Solution

Add indexation hooks at the points where canonical content is generated or mutated.

### Indexation Sources

**On arc generation** — [`ai/arc_generator.py`](../../../ai/arc_generator.py):
- Each `StoryBeat` description → `SemanticDocument(doc_type=PAST_EVENT, content=beat_summary)`
- Each named NPC in the arc → `SemanticDocument(doc_type=NPC_SHEET, content=...)`
- Villain context → `SemanticDocument(doc_type=NPC_SHEET, metadata={"role": "villain"})`

**On world generation** — [`ai/world_generator.py`](../../../ai/world_generator.py):
- World lore (setting, factions) → `SemanticDocument(doc_type=WORLD_LORE)`
- Each location description → `SemanticDocument(doc_type=LOCATION_DETAIL)`

**On NPC sheet generation** — [`ai/npc_generator.py`](../../../ai/npc_generator.py) (if exists; check):
- Generated NPCSheet → `SemanticDocument(doc_type=NPC_SHEET, metadata={"name": npc_name})`

**On quest creation** — wherever quests are created (likely in arc generator or session bootstrap):
- Quest description → `SemanticDocument(doc_type=QUEST_DETAIL, metadata={"quest_id": ...})`

**On beat completion** — [`bot/action_pipeline.py`](../../../bot/action_pipeline.py) `_apply_beat_effects`:
- New locked facts revealed → `SemanticDocument(doc_type=PAST_EVENT, content="<fact>")`
- Beat completion summary → `SemanticDocument(doc_type=PAST_EVENT, content="<beat_title> completed: <narrative_hint>")`

### Helper Module

To avoid duplicating boilerplate, create `memory/indexer.py`:

```python
class SemanticIndexer:
    """Single entry point for adding documents to semantic memory.
    Centralizes content formatting, dedup logic, and metadata conventions.
    """
    def __init__(self, semantic: SemanticMemory) -> None: ...

    def index_beat(self, campaign_id: str, beat: StoryBeat) -> None: ...
    def index_npc(self, campaign_id: str, npc_name: str, sheet: NPCSheet) -> None: ...
    def index_location(self, campaign_id: str, location: Location) -> None: ...
    def index_quest(self, campaign_id: str, quest: Quest) -> None: ...
    def index_lore(self, campaign_id: str, content: str, metadata: dict[str, str]) -> None: ...
    def index_revealed_fact(self, campaign_id: str, fact: str) -> None: ...
```

Each method:
- Formats the document content for retrieval (concise, context-rich)
- Generates a deterministic ID from `(campaign_id, doc_type, source_key)` to allow safe re-indexing without duplicates
- Attaches consistent metadata for filtered queries

### Retrieval Improvements

In [`memory/context_assembler.py:76`](../../../memory/context_assembler.py:76):

```python
# Before
relevant_docs = self._semantic.query(campaign_id, player_input)

# After
query_text = self._build_rag_query(player_input, recent_actions=window[-3:])
relevant_docs = self._semantic.query(
    campaign_id, query_text, n_results=5,
)
```

Use the last 2-3 actions (not just the current input) as query context — gives ChromaDB more signal for similarity matching.

### Acceptance Criteria

- All 5 `SemanticDocumentType`s are populated by at least one source after a fresh campaign creation
- A query for a known NPC name returns the relevant `NPC_SHEET` document
- Re-indexing the same beat does not duplicate documents (idempotent IDs)
- ChromaDB collection size is bounded (no infinite growth) — soft cap per campaign with eviction policy if needed

---

## 5. Arc Tracker — Pinned Discord Message (Zero Buttons)

### Problem

Players have no Discord-visible artifact telling them where they are in the story. They scroll back through chat to remember objectives. This is a UX failure that triggers abandonment.

### Solution

A pinned message per campaign channel, updated automatically by Story Director runs, showing the current arc context. **No buttons. No selects. Pure information.**

### Layout

```
📖 CHAPITRE 2 — Le Repaire de Vlaxos

🎯 Objectif actuel
  Retrouver la carte du donjon avant que Vlaxos ne l'utilise.

📜 Beats récents
  • Vous avez libéré le vieux mage Aldric
  • La garde royale vous recherche désormais
  • Vous êtes entrés dans les égouts sous la forge

📋 Quêtes actives
  • [Principale] Carte du donjon — en cours
  • [Secondaire] Message pour Elena — à livrer

—
Mise à jour : il y a 4 actions
```

### Data Sources

- **Chapter title**: `current_beat.title` from active `StoryBeat` (or higher-level chapter if defined)
- **Current objective**: `DirectorNote.current_objective` (Section 3)
- **Recent beats**: last 3 completed beats from `GameSession.story_arc`
- **Active quests**: from `GameStateSummary.active_quests` (or directly from quest store)
- **Updated timestamp**: relative ("il y a N actions" or "il y a Xm")

### Update Triggers

- After each Story Director run
- After each beat completion
- After each combat end (state changes are visible)

### New Files

- `bot/embeds/arc_tracker_embed.py` — `build_arc_tracker_embed(...)` returning `discord.Embed`
- `bot/utils/arc_tracker.py` — `ArcTrackerManager` class:
  ```python
  class ArcTrackerManager:
      async def ensure_pinned(self, channel: TextChannel, campaign_id: str) -> Message
      async def update(self, campaign_id: str, *, deps: ...) -> None
      async def remove(self, channel: TextChannel) -> None  # on /end_campaign
  ```

### Pinning Strategy

- Store the pinned message ID on the campaign record (new column on `Campaign` model: `arc_tracker_message_id: int | None`)
- On `/start_campaign`, create the pin
- On update, edit the existing message; if it has been unpinned/deleted by a user, recreate
- On `/end_campaign`, unpin and delete

### Discord API Considerations

- Pinned messages have a per-channel limit (50). Single pin per campaign channel — no risk.
- `Message.edit()` is rate-limited to ~5/s per channel — well below our update frequency.
- If the message is too old to edit (rare), create a new one and update the stored ID.

### Acceptance Criteria

- A pin appears in the campaign channel within 5s of `/start_campaign`
- Pin updates after Story Director runs without sending notifications to players
- Pin survives bot restarts (ID persisted)
- `/end_campaign` removes the pin

---

## 6. Narrator Fallback — Robust Failure Handling

### Problem

[`ai/narrator.py:78`](../../../ai/narrator.py:78) calls `self._client.chat_json(...)` without try/except. Failure modes:
- `LLMParseError` from `OllamaClient` (malformed JSON, timeout, connection error)
- Empty narrative (`data.get("narrative", "")` → `""`)
- Very short narrative (< 50 chars, often a sign the LLM gave up)

Any of these breaks the session. The Interpreter has a fallback ([`ai/interpreter.py:66`](../../../ai/interpreter.py:66)) — the Narrator should too.

### Solution

Three-tier fallback:

1. **Primary call** — full prompt as today
2. **Retry with simplified prompt** — strip optional sections, keep only `action_result_text` + minimal context
3. **Template fallback** — hardcoded text variants describing the mechanical outcome

### Implementation

```python
def narrate(self, action_result_text: str, ...) -> NarrativeResult:
    try:
        result = self._call_narrator(full_prompt=True, ...)
        if len(result.narrative) < 50:
            raise LLMParseError(f"Narrative too short: {len(result.narrative)} chars")
        return result
    except LLMParseError as exc:
        logger.warning("Narrator primary call failed, retrying simplified: %s", exc)
        try:
            return self._call_narrator(full_prompt=False, ...)
        except LLMParseError as exc2:
            logger.error("Narrator simplified retry failed, using template: %s", exc2)
            return self._template_fallback(action_result_text, outcome_facts)

def _template_fallback(self, action_result_text: str, outcome_facts: str) -> NarrativeResult:
    """Hardcoded narrative variants. Never throws."""
    # Pick variant based on action verb (attack/move/talk/search/look)
    template = self._pick_template(action_result_text)
    return NarrativeResult(
        narrative=template.format(action=action_result_text, outcome=outcome_facts),
        tone="dramatic",
        scene_goal_touched=False,
        beat_advanced=False,
    )
```

### Template Examples

```python
TEMPLATES = {
    "attack": [
        "Le combat se poursuit dans la confusion. {action}.",
        "Les coups pleuvent. {action}.",
    ],
    "move": [
        "Le décor change. {action}.",
        "Les pas vous portent ailleurs. {action}.",
    ],
    "talk": [
        "Les mots échangés résonnent encore. {action}.",
    ],
    "default": [
        "Le MJ rassemble ses idées. {action}.",
    ],
}
```

Templates are **short, in-universe, and acknowledge the mechanical outcome**. They are *not* high-quality narrative — that is intentional. Their job is to keep the session alive, not to be invisible.

### Acceptance Criteria

- Forcing `LLMParseError` in tests (mock client) returns a non-empty `NarrativeResult` from template
- Template fallback is logged at ERROR level for visibility
- Player sees text — never an exception, never an empty embed
- Story Director, when it next runs after a fallback, marks the turn for special attention (`coherence_issues` includes "narrator fallback used at turn N")

---

## 7. Files to Modify

| File | Phase | Change |
|------|-------|--------|
| `bot/pipeline/__init__.py` | A | New — re-exports |
| `bot/pipeline/types.py` | A | New — `PipelineContext`, `PipelineDeps` |
| `bot/pipeline/interpret.py` | A | New — interpret + entity resolution + validation (extracted from action_pipeline) |
| `bot/pipeline/resolve.py` | A | New — mechanics + beat completion + combat dispatch (extracted) |
| `bot/pipeline/narrate.py` | A | New — context assembly + narrator + drift tracking (extracted) |
| `bot/pipeline/orchestrator.py` | A | New — chain stages + persistence + rendering + Facade |
| `bot/action_pipeline.py` | A | Becomes thin Facade or removed |
| `ai/narrator.py` | A | Add try/except + retry + template fallback |
| `ai/models.py` | B | Extend `NarrativeResult` (meta fields) + `DirectorNote` (direction fields) |
| `ai/prompts/system_narrator.txt` | B | New JSON schema with meta fields + instructions |
| `ai/prompts/system_story_director.txt` | B | New JSON schema with direction fields |
| `ai/story_director.py` | B | Parse new fields |
| `bot/pipeline/drift_tracker.py` | B | New — rolling window + drift detection |
| `bot/pipeline/orchestrator.py` | B | Add Story Director cadence + async parallel execution |
| `bot/cogs/session.py` (or new `narrative.py`) | B | Add `/story_catch_up` slash command |
| `memory/indexer.py` | C | New — `SemanticIndexer` helper |
| `ai/arc_generator.py` | C | Index beats, NPCs, villain on generation |
| `ai/world_generator.py` | C | Index world lore, locations on generation |
| `ai/npc_generator.py` (if exists) | C | Index NPC sheets |
| `bot/pipeline/resolve.py` | C | Index revealed facts on beat completion |
| `memory/context_assembler.py` | C | Use rolling-window query text instead of raw player_input |
| `bot/embeds/arc_tracker_embed.py` | D | New — embed builder |
| `bot/utils/arc_tracker.py` | D | New — `ArcTrackerManager` |
| `db/models.py` | D | Add `arc_tracker_message_id` to `Campaign` |
| `db/repositories/campaign_repo.py` | D | Persist new field |
| `bot/cogs/session.py` | D | Wire pin lifecycle (`/start_campaign`, `/end_campaign`) |
| `bot/pipeline/orchestrator.py` | D | Trigger arc tracker update after Story Director run |

Test files mirror these changes (mostly in `tests/bot/test_pipeline_*.py`, `tests/ai/test_narrator.py`, `tests/ai/test_story_director.py`, `tests/memory/test_indexer.py`).

---

## 8. Verification Plan

### Phase A — Pipeline Split + Narrator Fallback

**Unit tests:**
- `PipelineContext` carries fields through stages without mutation of earlier fields
- Each stage runs independently with mocked deps
- Narrator returns template fallback when `chat_json` raises `LLMParseError` twice
- Narrator returns template fallback when first call returns < 50 chars

**Integration tests:**
- Existing `tests/bot/test_action_pipeline.py` passes with import-only updates
- Existing scenario tests (Discord pytest scenarios) pass

**Live Discord test:**
- Start a campaign, take 5 actions, verify no behavior change vs pre-split
- Inject an Ollama outage (stop the daemon for 30s) mid-session — verify session continues with template fallbacks

### Phase B — Narrator Contract + Story Director Cadence

**Unit tests:**
- `NarrativeResult` accepts old payloads (no meta fields) — defaults applied
- Drift tracker fires when 3 of last 5 narrations have `beat_advanced=False`
- `should_run_director` returns True for each trigger condition
- Story Director output (`DirectorNote`) populates new direction fields when LLM provides them

**Integration tests:**
- Mocked Story Director output is injected into next Narrator prompt as `[STORY DIRECTION]` block
- `/story_catch_up` triggers Story Director and posts recap embed

**Live Discord test:**
- Start a campaign, take 8-10 exploration actions
- Verify Story Director ran at least once (check logs)
- Use `/story_catch_up` mid-session — verify it produces a recap

### Phase C — RAG Densification

**Unit tests:**
- `SemanticIndexer.index_beat` produces idempotent IDs (re-index same beat → no duplicates)
- All 5 `SemanticDocumentType`s have at least one indexer method
- `_build_rag_query` combines current input + recent actions

**Integration tests:**
- Fresh campaign generation populates ChromaDB with NPC, location, beat docs
- Query for an NPC name returns the `NPC_SHEET` document

**Live Discord test:**
- Start a campaign, play 30 minutes of exploration
- Inspect ChromaDB collection: ≥ 10 docs across ≥ 3 types
- Mention an NPC by name → narrator references the canonical NPC personality (no contradictions vs the indexed sheet)

### Phase D — Arc Tracker

**Unit tests:**
- `build_arc_tracker_embed` produces correct field structure
- `ArcTrackerManager.ensure_pinned` creates a pin if none exists, returns existing if present
- `update` edits the existing pin (no new message)

**Integration tests:**
- `/start_campaign` triggers pin creation
- Story Director run triggers pin update with new objective
- `/end_campaign` removes pin

**Live Discord test:**
- Start a campaign — verify pinned message appears
- Take actions — verify "Mise à jour" timestamp updates after Story Director runs
- Verify pinned message is readable on mobile (no overflow, fields fit)

---

## 9. Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Pipeline split introduces a subtle behavior regression | Medium | Phase A is pure displacement, no logic change. Existing test suite must stay green before merging. |
| Extending `NarrativeResult` breaks downstream consumers | Low | All new fields have defaults; old payloads parse cleanly. Verified by existing narrator tests. |
| Story Director async execution races with the Narrator stage | Medium | Use `asyncio.wait_for` with a timeout (e.g., 8s). If Story Director hasn't returned, fall back to cached `DirectorNote` from last run. |
| More frequent Story Director increases LLM token spend | High | Acceptable on local Ollama (no $ cost). On hosted models, cap to once per N seconds with cooldown. |
| RAG indexing creates an unbounded ChromaDB collection | Medium | Add a soft cap (~500 docs per campaign). Eviction policy: remove oldest `PAST_EVENT` first when over cap. |
| Template fallback is jarring narratively | High (visible) | Keep templates short, in-universe, and rare. Each template invocation is logged at ERROR level — investigate Ollama health if frequent. |
| Pinned message exceeds Discord embed length (~6000 chars total, ~1024 per field) | Low | Truncate `Recent beats` to last 3, `Active quests` to top 5. Hard-cap each field's character count. |
| Drift detector triggers too eagerly on legitimate slow scenes (e.g., long dialogue) | Medium | Tune thresholds: 3-of-5 stale narrations is a starting point; allow per-campaign override. Don't trigger drift during active combat. |

---

## 10. Phased Delivery

Each phase is **independently shippable** — leaves the bot in a working state with a complete test suite.

| Phase | Duration | Deliverable | User-visible Benefit |
|-------|----------|-------------|----------------------|
| **A** | ~1 week | Pipeline split + Narrator fallback | Bot survives bad LLM responses; codebase becomes maintainable |
| **B** | ~2 weeks | Narrator meta contract + Story Director cadence + `StoryDirection` output + `/story_catch_up` | Drift detected and rectified within 2-3 turns; player can ask for a recap on demand |
| **C** | ~2 weeks | `SemanticIndexer` + indexation hooks across arc/world/NPC/quest/beat sources + improved query text | NPCs stay in character across long sessions; locations remember their lore |
| **D** | ~1 week | Arc Tracker pinned message + `Campaign.arc_tracker_message_id` + lifecycle wiring | Players always see current chapter, objective, recent beats, active quests |

**Total: ~6 weeks** of focused work, single-developer cadence.

**Inter-phase gate**: each phase requires a green test suite (`uv run pytest`), `ruff check .`, `mypy .`, and a successful live Discord scenario test before the next phase begins.

---

## 11. Open Questions

These do not block the spec but should be answered during Phase B/C:

1. **Story Director cadence tuning** — is 6 actions the right cadence, or should it scale with session length (e.g., every 8 in long sessions, every 4 in short ones)? Empirical tuning during live tests.
2. **RAG eviction policy** — when the soft cap (500 docs/campaign) is hit, is "oldest PAST_EVENT first" the right default, or should we score by retrieval frequency? Defer until we see real campaign sizes.
3. **Arc Tracker on mobile** — Discord mobile renders pinned messages differently. Does the layout hold up? Verify in live test.
4. **`/story_catch_up` cooldown** — should this command have a cooldown to prevent abuse? Probably 30s per campaign. Add if observed.

---

## 12. Out of Scope (Restated)

To keep the scope honest, these explicitly do NOT belong to this design:

- Bug fixes in combat UI/flow (separate spec, separate plan)
- SQLite hardening (WAL mode, timeouts) — addressed in a future "scale" cycle
- i18n improvements beyond the existing FR/EN dict
- Observability infrastructure (Prometheus, OpenTelemetry)
- Phase 4 polish (CI/CD, marketing artifacts, demo GIFs)
- D&D mechanics extensions (backgrounds, feats, multiclass, shop, expanded spell catalogue)

These are tracked in `tasks/todo.md` (existing) and will be addressed in subsequent design cycles.
