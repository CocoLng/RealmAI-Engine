# Rich Interaction Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make non-combat narrations specific to both the player's framing and the canonical scene state, while preventing the narrator from validating false player assumptions about objects/NPCs.

**Architecture:** Replace the lossy `_resolve_mechanics → str` chain with a structured `MechanicsOutcome` Pydantic model carrying `summary` (mechanical), `player_intent` (raw + extras), and `outcome_facts` (state changes). Enrich `_assemble_context` via a new `describe_scene_for_narrator` helper that exposes item descriptions, NPC details, exits, and recent dialogue. Add an explicit anti-hallucination + framing block to the narrator system prompt.

**Tech Stack:** Python 3.12, Pydantic v2, pytest, Ollama (qwen3.5:9b narrator). Plan: Phase 1 engine-style — `engine/` stays untouched; all changes live in `bot/`, `ai/`, and `world/`.

**Spec:** [/Users/cocolng/.claude/plans/spicy-purring-storm.md](../../../../.claude/plans/spicy-purring-storm.md) (also reproduced in `docs/superpowers/specs/` if/when promoted).

---

## Background context (read once before starting)

The bug: in [logs/campaigns/Test2.md](../../../logs/campaigns/Test2.md), the player typed *"inspecte la croix de fer pour voir si c une d'origine de 39-45"*. The interpreter classified it correctly (`SEARCH`, target=`Croix de fer`, conf 0.95), but the narrator received only `"Xavier Dupont de ligonesse searches Croix de fer."` and no canonical info, so the narration is generic.

Two losses, both in [bot/action_pipeline.py](../../../bot/action_pipeline.py):

- `_resolve_mechanics()` (l. 616-676) discards `raw_input`, `search_detail`, `talk_topic`, `improvise_description`.
- `_assemble_context()` (l. 714-728) only sends `loc.name + loc.description + npc names`.

Additional finding from exploration: `Location.items_available` is `list[str]` (names only, no canonical descriptions). We add an optional `item_descriptions: dict[str, str]` so future world-gen passes can populate them; the narrator must work even when empty.

## File map

| File | Action | Responsibility |
|---|---|---|
| [ai/models.py](../../../ai/models.py) | Modify | Add `MechanicsOutcome` Pydantic model |
| [world/location.py](../../../world/location.py) | Modify | Add optional `item_descriptions: dict[str, str]` field |
| [bot/scene_hydration.py](../../../bot/scene_hydration.py) | Modify | Add `describe_scene_for_narrator(session, actor_name) -> str` helper |
| [bot/action_pipeline.py](../../../bot/action_pipeline.py) | Modify | `_resolve_mechanics` returns `MechanicsOutcome`; `_assemble_context` delegates; `ActionPipelineResult.mechanics_text` stays a `str` (= `.summary`) for backward compat |
| [ai/narrator.py](../../../ai/narrator.py) | Modify | `Narrator.narrate` accepts optional `player_intent` + `outcome_facts` kwargs; falls back to legacy single-string mode |
| [ai/prompts/system_narrator.txt](../../../ai/prompts/system_narrator.txt) | Modify | Add anti-hallucination + framing clause |
| [tests/ai/test_narrator.py](../../../tests/ai/test_narrator.py) | Modify or create | Unit tests for new narrator signature |
| [tests/bot/test_action_pipeline_interaction.py](../../../tests/bot/test_action_pipeline_interaction.py) | Create | Regression test: SEARCH with rich framing reaches the narrator |
| [tests/bot/test_scene_hydration.py](../../../tests/bot/test_scene_hydration.py) | Modify or create | Test `describe_scene_for_narrator` |

**Backward compat:** `ActionPipelineResult.mechanics_text: str` is consumed by [bot/cogs/action_handler.py:287](../../../bot/cogs/action_handler.py#L287) and 5 test fixtures. We keep it a plain `str` (set from `MechanicsOutcome.summary`) to avoid touching the cog and existing tests.

---

## Task 1: Add `MechanicsOutcome` model

**Files:**
- Modify: `ai/models.py`
- Test: `tests/ai/test_models.py` (create if missing)

- [ ] **Step 1: Write the failing test**

Add to `tests/ai/test_models.py` (create the file if it doesn't exist; mirror the structure of any sibling test under `tests/ai/`):

```python
from ai.models import MechanicsOutcome


def test_mechanics_outcome_minimal():
    out = MechanicsOutcome(summary="Xavier searches Croix de fer.")
    assert out.summary == "Xavier searches Croix de fer."
    assert out.player_intent == ""
    assert out.outcome_facts == ""


def test_mechanics_outcome_full():
    out = MechanicsOutcome(
        summary="Xavier picks up the Croix de fer.",
        player_intent="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        outcome_facts="Item 'Croix de fer' moved from scene to Xavier's inventory.",
    )
    assert "39-45" in out.player_intent
    assert "inventory" in out.outcome_facts
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/ai/test_models.py -v
```

Expected: ImportError or `AttributeError: module 'ai.models' has no attribute 'MechanicsOutcome'`.

- [ ] **Step 3: Implement the model**

Append to `ai/models.py`:

```python
class MechanicsOutcome(BaseModel):
    """Structured output of `_resolve_mechanics`.

    Carries three layers separately so the narrator can both honor the
    player's intent and stay faithful to canon facts:

    - ``summary``: short mechanical phrase, used for the Discord stats embed
      and for backward-compatible ``ActionPipelineResult.mechanics_text``.
    - ``player_intent``: how the player framed the action (raw_input plus
      any interpreter-extracted detail like ``search_detail`` or
      ``talk_topic``). May be empty for system-driven actions.
    - ``outcome_facts``: what mechanically changed in engine state
      (item moved, location changed, NPC killed). May be empty when no
      state mutation occurred (e.g. LOOK).
    """

    summary: str
    player_intent: str = ""
    outcome_facts: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/ai/test_models.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add ai/models.py tests/ai/test_models.py
git commit -m "feat(ai): add MechanicsOutcome model for layered narrator input"
```

---

## Task 2: Extend `Location` with optional `item_descriptions`

**Files:**
- Modify: `world/location.py`
- Test: `tests/world/test_location.py` (create if missing — check first)

- [ ] **Step 1: Write the failing test**

```python
from world.location import Location


def test_location_item_descriptions_default_empty():
    loc = Location(name="Crypte", description="Sombre.")
    assert loc.item_descriptions == {}


def test_location_item_descriptions_populated():
    loc = Location(
        name="Église",
        description="Vieille paroisse.",
        items_available=["Croix de fer"],
        item_descriptions={"Croix de fer": "Vieille croix de forge, noircie."},
    )
    assert loc.item_descriptions["Croix de fer"].startswith("Vieille")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/world/test_location.py -v
```

Expected: `pydantic.ValidationError` or `AttributeError` on `item_descriptions`.

- [ ] **Step 3: Add the field**

In `world/location.py`, add to the `Location` model after `items_available`:

```python
    item_descriptions: dict[str, str] = {}
```

The field is optional with empty-dict default — fully backward-compatible with any persisted location row that doesn't have it.

- [ ] **Step 4: Run test + full world test suite**

```bash
uv run pytest tests/world/ -v
```

Expected: new tests pass; no regressions.

- [ ] **Step 5: Commit**

```bash
git add world/location.py tests/world/test_location.py
git commit -m "feat(world): add Location.item_descriptions for canon item details"
```

---

## Task 3: Implement `describe_scene_for_narrator` helper

**Files:**
- Modify: `bot/scene_hydration.py`
- Test: `tests/bot/test_scene_hydration.py` (modify or create)

- [ ] **Step 1: Write the failing test**

In `tests/bot/test_scene_hydration.py`, add:

```python
from unittest.mock import MagicMock

from bot.scene_hydration import describe_scene_for_narrator
from world.location import Location
from world.npc import NPC, NPCDisposition
from engine.character import AbilityScores, Race


def _npc(name: str, *, location: str, disposition=NPCDisposition.NEUTRAL,
         description="", personality="") -> NPC:
    return NPC(
        name=name, race=Race.HUMAN, char_class=None, level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4, max_hp=4, ac=10, disposition=disposition, is_alive=True,
        description=description, personality=personality,
        location_name=location, aliases=[],
    )


def test_describe_scene_includes_location_and_exits():
    loc = Location(
        name="Église",
        description="Vieille paroisse silencieuse.",
        connections=["Village", "Crypte"],
    )
    session = MagicMock()
    session.current_location = loc
    session.npcs = {}

    out = describe_scene_for_narrator(session, actor_name="Xavier")
    assert "Église" in out
    assert "Vieille paroisse silencieuse." in out
    assert "Village" in out and "Crypte" in out


def test_describe_scene_includes_items_with_descriptions():
    loc = Location(
        name="Église",
        description="…",
        items_available=["Croix de fer", "Cierge pourri"],
        item_descriptions={"Croix de fer": "Vieille croix de forge, noircie."},
    )
    session = MagicMock()
    session.current_location = loc
    session.npcs = {}

    out = describe_scene_for_narrator(session, actor_name="Xavier")
    assert "Croix de fer" in out
    assert "Vieille croix de forge" in out
    # Item without description still appears, name only
    assert "Cierge pourri" in out


def test_describe_scene_includes_present_npcs_with_disposition():
    loc = Location(name="Église", description="…", npcs_present=["Élie l'Ermite"])
    npc = _npc(
        "Élie l'Ermite",
        location="Église",
        disposition=NPCDisposition.FRIENDLY,
        description="Vieil ermite voûté.",
        personality="Méfiant mais loyal.",
    )
    session = MagicMock()
    session.current_location = loc
    session.npcs = {"Élie l'Ermite": npc}

    out = describe_scene_for_narrator(session, actor_name="Xavier")
    assert "Élie l'Ermite" in out
    assert "FRIENDLY" in out or "friendly" in out.lower()
    assert "Vieil ermite" in out


def test_describe_scene_no_location():
    session = MagicMock()
    session.current_location = None
    session.npcs = {}
    out = describe_scene_for_narrator(session, actor_name="Xavier")
    assert "Acting character" in out
    assert "Xavier" in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/bot/test_scene_hydration.py -v
```

Expected: `ImportError: cannot import name 'describe_scene_for_narrator'`.

- [ ] **Step 3: Implement the helper**

Append to `bot/scene_hydration.py`:

```python
def describe_scene_for_narrator(
    session: "GameSession",
    *,
    actor_name: str,
) -> str:
    """Build a rich, narrator-facing description of the current scene.

    Includes location name + description, exits, items (with canon
    descriptions when available), and present NPCs (with disposition,
    description, and personality). Falls back gracefully when fields are
    empty so it works on freshly hydrated commoner NPCs.
    """
    lines: list[str] = []
    location = session.current_location

    if location is not None:
        lines.append(f"## Location\n{location.name}\n{location.description}")

        if location.connections:
            lines.append("## Exits\n" + ", ".join(location.connections))

        if location.items_available:
            item_lines = []
            descriptions = getattr(location, "item_descriptions", {}) or {}
            for name in location.items_available:
                desc = descriptions.get(name, "").strip()
                if desc:
                    item_lines.append(f"- {name} — {desc}")
                else:
                    item_lines.append(f"- {name}")
            lines.append("## Visible items\n" + "\n".join(item_lines))

        present = [
            npc for npc in (session.npcs or {}).values()
            if npc.location_name == location.name
        ]
        if present:
            npc_lines = []
            for npc in present:
                bits = [npc.name]
                if npc.race is not None:
                    bits.append(f"({npc.race.value})")
                bits.append(f"— disposition: {npc.disposition.value}")
                if npc.description:
                    bits.append(f"— {npc.description}")
                if npc.personality:
                    bits.append(f"— personality: {npc.personality}")
                npc_lines.append(" ".join(bits))
            lines.append("## NPCs present\n" + "\n".join(npc_lines))

    lines.append(f"## Acting character\n{actor_name}")
    return "\n\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/bot/test_scene_hydration.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add bot/scene_hydration.py tests/bot/test_scene_hydration.py
git commit -m "feat(scene): add describe_scene_for_narrator with items and NPC details"
```

---

## Task 4: Switch `_resolve_mechanics` to return `MechanicsOutcome`

**Files:**
- Modify: `bot/action_pipeline.py` (l. 616-712, l. 276, l. 282-285, l. 321-327, l. 351-366)
- Test: `tests/bot/test_action_pipeline.py` (existing — verify no regression)

`ActionPipelineResult.mechanics_text: str` stays a `str` to avoid breaking [bot/cogs/action_handler.py:287](../../../bot/cogs/action_handler.py#L287) and the 5 test fixtures listed in the file map. We populate it from `outcome.summary`.

- [ ] **Step 1: Update `_resolve_mechanics` signature and return type**

Replace the body of `_resolve_mechanics` (l. 616-676) with:

```python
    async def _resolve_mechanics(
        self, action: InterpretedAction,
    ) -> "MechanicsOutcome":
        """Apply mechanical effects and return a layered outcome.

        Returns a :class:`ai.models.MechanicsOutcome` carrying the short
        mechanical summary, the player's framing, and any state-change
        facts. The narrator consumes the three layers separately.
        """
        from ai.models import MechanicsOutcome

        intent = self._build_player_intent(action)

        if self._trivial_kill_mechanics is not None:
            return MechanicsOutcome(
                summary=self._trivial_kill_mechanics,
                player_intent=intent,
                outcome_facts=self._trivial_kill_mechanics,
            )

        at = action.action_type

        if at == ActionType.LOOK:
            loc = self.location
            summary = (
                f"{action.actor_name} observes {loc.name if loc else 'the area'}."
            )
            return MechanicsOutcome(summary=summary, player_intent=intent)

        if at == ActionType.SEARCH:
            summary = (
                f"{action.actor_name} searches "
                f"{action.target_name or 'the surroundings'}."
            )
            return MechanicsOutcome(summary=summary, player_intent=intent)

        if at == ActionType.TALK:
            summary = f"{action.actor_name} approaches {action.target_name} to speak."
            return MechanicsOutcome(summary=summary, player_intent=intent)

        if at == ActionType.MOVE:
            target = action.target_name or ""
            if (
                self.session is not None
                and self.db_factory is not None
                and target
            ):
                from bot.world_navigation import LocationChangeError, change_location
                try:
                    dest = await change_location(
                        self.session, target, db_factory=self.db_factory,
                    )
                except LocationChangeError as exc:
                    logger.warning(
                        "MOVE change_location failed campaign=%s target=%r reason=%s",
                        self.campaign_id, target, exc.reason,
                    )
                    return MechanicsOutcome(
                        summary=f"{action.actor_name} cannot reach {exc.destination}.",
                        player_intent=intent,
                    )
                self.location = dest
                self.npcs = self.session.npcs
                return MechanicsOutcome(
                    summary=f"{action.actor_name} arrives at {dest.name}.",
                    player_intent=intent,
                    outcome_facts=f"{action.actor_name} moved to {dest.name}.",
                )
            return MechanicsOutcome(
                summary=f"{action.actor_name} moves toward {action.target_name}.",
                player_intent=intent,
            )

        if at == ActionType.INTERACT:
            return MechanicsOutcome(
                summary=f"{action.actor_name} interacts with {action.target_name}.",
                player_intent=intent,
            )

        if at == ActionType.PICKUP:
            summary = await asyncio.to_thread(self._resolve_pickup, action)
            facts = ""
            if "picks up" in summary:
                facts = summary
            return MechanicsOutcome(
                summary=summary, player_intent=intent, outcome_facts=facts,
            )

        if at == ActionType.IMPROVISE:
            description = action.improvise_description or action.raw_input
            return MechanicsOutcome(
                summary=(
                    f"{action.actor_name} attempts an improvised action: {description}"
                ),
                player_intent=intent,
            )

        return MechanicsOutcome(
            summary=f"{action.actor_name} performs {at.value}.",
            player_intent=intent,
        )

    def _build_player_intent(self, action: InterpretedAction) -> str:
        """Concatenate raw input with any interpreter-extracted intent extras."""
        parts: list[str] = []
        if action.raw_input:
            parts.append(action.raw_input.strip())
        extras = []
        if action.search_detail:
            extras.append(f"search detail: {action.search_detail}")
        if action.talk_topic:
            extras.append(f"talk topic: {action.talk_topic}")
        if action.improvise_description:
            extras.append(f"improvise: {action.improvise_description}")
        if extras:
            parts.append("; ".join(extras))
        return " | ".join(parts)
```

Note: `_resolve_pickup` itself stays unchanged — it still returns a `str` and the new wrapper detects success via the `"picks up"` substring (matches the existing wording at l. 709-712). If that feels brittle later, refactor in a follow-up; for now it preserves behavior.

- [ ] **Step 2: Update the call site at l. 276 and the narrator dispatch**

Replace l. 276 and l. 282-285 with:

```python
        await self._emit(progress_callback, PipelinePhase.RESOLVING_ACTION)
        outcome = await self._resolve_mechanics(interpreted)

        await self._emit(progress_callback, PipelinePhase.ASSEMBLING_CONTEXT)
        context_prompt = self._assemble_context(interpreted)

        await self._emit(progress_callback, PipelinePhase.NARRATING)
        narration = await self._call_narrator(
            outcome=outcome,
            context_prompt=context_prompt,
        )
```

Replace `ActionPipelineResult` construction at l. 321-327:

```python
        return ActionPipelineResult(
            narrative=narration.narrative,
            tone=narration.tone,
            mechanics_text=outcome.summary,
            interpreted_action=interpreted,
            new_beat=new_beat,
        )
```

Update `_call_narrator` (l. 351-366) to:

```python
    async def _call_narrator(
        self,
        outcome: "MechanicsOutcome",
        context_prompt: str,
    ) -> NarrativeResult:
        def _do() -> NarrativeResult:
            return self.narrator.narrate(
                action_result_text=outcome.summary,
                context_prompt=context_prompt,
                language=self.language,
                player_intent=outcome.player_intent,
                outcome_facts=outcome.outcome_facts,
            )

        return await retry_llm_call(
            _do,
            log_label=f"ACTION campaign={self.campaign_id} narrate",
        )
```

Add the import near the top of the file (near the other `ai.models` imports — search for `from ai.models import`):

```python
from ai.models import MechanicsOutcome
```

(If `MechanicsOutcome` isn't yet exported alongside the others, the local `from ai.models import MechanicsOutcome` inside `_resolve_mechanics` covers the runtime case; the top-level import is for the type annotation in `_call_narrator`.)

- [ ] **Step 3: Run existing pipeline + cog tests**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_handler_cog.py -v
```

Expected: all green. The cog tests pass `mechanics_text="..."` directly to `ActionPipelineResult`, which still accepts a `str` — no change needed there.

If a test mocks `Narrator.narrate` and asserts on its kwargs, update the mock to accept `player_intent` and `outcome_facts` (they're added in Task 5; if Task 5 isn't done yet, those kwargs will fail — implement Task 5 first or in lockstep).

- [ ] **Step 4: Commit**

```bash
git add bot/action_pipeline.py
git commit -m "refactor(pipeline): _resolve_mechanics returns MechanicsOutcome"
```

---

## Task 5: Update `Narrator.narrate` to accept layered context

**Files:**
- Modify: `ai/narrator.py`
- Test: `tests/ai/test_narrator.py`

- [ ] **Step 1: Write the failing test**

In `tests/ai/test_narrator.py` (modify or create):

```python
from unittest.mock import MagicMock

from ai.narrator import Narrator


def test_narrate_includes_player_intent_and_outcome_facts():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "ok", "tone": "tense"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Xavier searches Croix de fer.",
        context_prompt="## Location\nÉglise\nVieille paroisse.",
        language="fr",
        player_intent="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        outcome_facts="",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "39-45" in user_msg
    assert "Église" in user_msg
    assert "Xavier searches" in user_msg


def test_narrate_legacy_signature_still_works():
    client = MagicMock()
    client.chat_json.return_value = {"narrative": "ok", "tone": "dramatic"}
    narrator = Narrator(client)

    narrator.narrate(
        action_result_text="Goblin takes 8 damage.",
        context_prompt="## Location\nForest",
    )

    args, kwargs = client.chat_json.call_args
    messages = args[1] if len(args) > 1 else kwargs["messages"]
    user_msg = messages[-1]["content"]
    assert "Goblin" in user_msg
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/ai/test_narrator.py -v
```

Expected: `TypeError: narrate() got an unexpected keyword argument 'player_intent'`.

- [ ] **Step 3: Update `Narrator.narrate`**

Replace the body of `Narrator.narrate` in `ai/narrator.py` with:

```python
    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Args:
            action_result_text: Mechanical summary (e.g. "Thorin attacks
                Goblin. Hit! 8 damage dealt.").
            context_prompt: Assembled scene context from
                ``describe_scene_for_narrator`` (location, items, NPCs,
                exits) plus the acting character.
            language: ISO 639-1 language code for narrative output.
            player_intent: How the player framed the action (raw input
                plus interpreter-extracted detail). Empty string when no
                framing is available.
            outcome_facts: What mechanically changed in engine state.
                Empty string when no mutation occurred.

        Returns:
            NarrativeResult with narrative text and tone classification.
        """
        logger.info("NARRATE input=%r intent=%r", action_result_text[:100], player_intent[:100])

        sections = [context_prompt, f"## What happened\n{action_result_text}"]
        if player_intent:
            sections.append(f"## Player framing\n{player_intent}")
        if outcome_facts:
            sections.append(f"## State changes\n{outcome_facts}")
        user_content = "\n\n".join(sections)

        system_prompt = language_instruction(language) + _SYSTEM_PROMPT
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = self._client.chat_json(self.MODEL, messages, temperature=0.8)
        result = NarrativeResult(
            narrative=str(data.get("narrative", "")),
            tone=data.get("tone", "dramatic"),  # type: ignore[arg-type]
        )
        logger.info("NARRATE tone=%s output=%r", result.tone, result.narrative[:200])
        logger.debug("NARRATE full_output=%s", result.narrative)
        return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/ai/test_narrator.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add ai/narrator.py tests/ai/test_narrator.py
git commit -m "feat(narrator): accept layered player_intent and outcome_facts"
```

---

## Task 6: Add anti-hallucination clause to narrator system prompt

**Files:**
- Modify: `ai/prompts/system_narrator.txt`

- [ ] **Step 1: Edit the prompt**

Insert the following block in `ai/prompts/system_narrator.txt`, immediately after the existing "Your rules:" section (i.e. after line 21 ending in "Match the tone to the outcome tier above."), before the "Output schema" line:

```
Canon faithfulness (critical):
- The "Location", "Visible items", and "NPCs present" sections of the context are absolute canon. Treat them as the unshakeable truth of what the character actually sees.
- The "Player framing" section (when present) is HOW the player phrased their action. Use it to understand intent and respond to what the player cares about.
- If the player's framing assumes facts that contradict canon (wrong era, wrong material, wrong identity), your character sees the object/NPC for what it really is. Describe what is actually there. Do NOT validate the false assumption.
- No flat refusals, no breaking immersion. The character can be surprised, intrigued, skeptical, or dismissive — but never agrees to something the canon doesn't support.
- When canon is silent on a detail (no description provided), stay minimal and grounded — do not invent specific historical, cultural, or material claims.
```

- [ ] **Step 2: Verify file is well-formed**

```bash
uv run python -c "from pathlib import Path; print(Path('ai/prompts/system_narrator.txt').read_text()[:500])"
```

Expected: prints the start of the file with the new block visible after the rules section.

- [ ] **Step 3: Run narrator + pipeline tests (smoke)**

```bash
uv run pytest tests/ai/test_narrator.py tests/bot/test_action_pipeline.py -v
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add ai/prompts/system_narrator.txt
git commit -m "feat(narrator): add canon-faithfulness clause to system prompt"
```

---

## Task 7: Wire `describe_scene_for_narrator` into `_assemble_context`

**Files:**
- Modify: `bot/action_pipeline.py` (l. 714-728)
- Test: existing pipeline tests + `tests/bot/test_action_pipeline.py`

- [ ] **Step 1: Replace `_assemble_context`**

Replace the `_assemble_context` method (l. 714-728) with:

```python
    def _assemble_context(self, action: InterpretedAction) -> str:
        """Build the narrator-facing context.

        Delegates to :func:`bot.scene_hydration.describe_scene_for_narrator`
        when a session is available; falls back to a minimal location-only
        snippet otherwise (used by unit tests that construct the pipeline
        without a full session).
        """
        if self.session is not None:
            from bot.scene_hydration import describe_scene_for_narrator
            return describe_scene_for_narrator(
                self.session, actor_name=action.actor_name,
            )

        loc = self.location
        lines: list[str] = []
        if loc is not None:
            lines.append(f"## Location\n{loc.name}\n{loc.description}")
        lines.append(f"## Acting character\n{action.actor_name}")
        return "\n\n".join(lines)
```

- [ ] **Step 2: Run the full pipeline test suite**

```bash
uv run pytest tests/bot/ -v
```

Expected: green. Some pipeline tests construct a `session` mock — the new helper accesses `session.current_location` and `session.npcs`, both of which are already set by existing fixtures (verified during research). If a test fails because a fixture lacks one of those, add `session.current_location = None; session.npcs = {}` to the fixture.

- [ ] **Step 3: Commit**

```bash
git add bot/action_pipeline.py
git commit -m "feat(pipeline): _assemble_context delegates to describe_scene_for_narrator"
```

---

## Task 8: End-to-end regression test for the "croix de 39-45" scenario

**Files:**
- Create: `tests/bot/test_action_pipeline_interaction.py`

- [ ] **Step 1: Write the regression test**

```python
"""Regression test: rich player framing + canon scene reach the narrator."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult
from bot.action_pipeline import ActionPipeline
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition
from engine.character import AbilityScores, Race


@pytest.mark.asyncio
async def test_search_passes_player_framing_and_canon_to_narrator():
    """The 'croix de 39-45' scenario from logs/campaigns/Test2.md.

    Asserts that when a player searches an item with a richly-framed
    raw_input, the narrator receives BOTH the player's framing AND the
    canonical scene description (location, items, item descriptions).
    """
    location = Location(
        name="La Paroisse de Saint-Michel",
        description="L'église paroissiale semble paisible.",
        items_available=["Croix de fer", "Cierge pourri"],
        item_descriptions={
            "Croix de fer": "Vieille croix de forge médiévale, noircie par les ans.",
        },
        npcs_present=["Élie l'Ermite"],
        connections=["Village de Valombre"],
    )
    npc = NPC(
        name="Élie l'Ermite", race=Race.HUMAN, char_class=None, level=1,
        ability_scores=AbilityScores(STR=10, DEX=10, CON=10, INT=10, WIS=10, CHA=10),
        hp=4, max_hp=4, ac=10, disposition=NPCDisposition.NEUTRAL, is_alive=True,
        description="Vieil ermite voûté.", personality="Méfiant.",
        location_name="La Paroisse de Saint-Michel", aliases=[],
    )

    session = MagicMock()
    session.current_location = location
    session.npcs = {"Élie l'Ermite": npc}
    session.story_arc = None
    session.advance_beat_if_ready = lambda: None

    interpreted = InterpretedAction(
        action_type=ActionType.SEARCH,
        actor_name="Xavier Dupont de ligonesse",
        target_name="Croix de fer",
        raw_input="inspecte la croix de fer pour voir si c une d'origine de 39-45",
        confidence=0.95,
        search_detail="origine 39-45",
    )

    narrator = MagicMock()
    narrator.narrate.return_value = NarrativeResult(
        narrative="…", tone="tense",
    )
    interpreter = MagicMock()
    interpreter.interpret.return_value = interpreted

    pipeline = ActionPipeline(
        campaign_id="test",
        actor_name="Xavier Dupont de ligonesse",
        interpreter=interpreter,
        narrator=narrator,
        session=session,
        language="fr",
    )
    pipeline.location = location
    pipeline.npcs = session.npcs

    outcome = await pipeline._resolve_mechanics(interpreted)
    context = pipeline._assemble_context(interpreted)

    assert isinstance(outcome, MechanicsOutcome)
    assert outcome.summary == (
        "Xavier Dupont de ligonesse searches Croix de fer."
    )
    assert "39-45" in outcome.player_intent
    assert "search detail" in outcome.player_intent.lower()

    assert "La Paroisse de Saint-Michel" in context
    assert "Croix de fer" in context
    assert "forge médiévale" in context  # canon description survives
    assert "Cierge pourri" in context  # item without description still listed
    assert "Élie l'Ermite" in context
    assert "Village de Valombre" in context  # exit
```

- [ ] **Step 2: Run the test**

```bash
uv run pytest tests/bot/test_action_pipeline_interaction.py -v
```

Expected: 1 passed. If the `ActionPipeline.__init__` signature differs from what I assumed, inspect [bot/action_pipeline.py](../../../bot/action_pipeline.py) around `class ActionPipeline` and adjust the constructor call to match the real required args (likely a few more positional/keyword params are needed; preserve the test intent).

- [ ] **Step 3: Commit**

```bash
git add tests/bot/test_action_pipeline_interaction.py
git commit -m "test(pipeline): regression test for rich SEARCH framing reaching narrator"
```

---

## Task 9: Full verification

- [ ] **Step 1: Run the full test suite**

```bash
uv run pytest -v
```

Expected: all green. Investigate and fix any regression before proceeding.

- [ ] **Step 2: Lint + types**

```bash
uv run ruff check .
uv run mypy .
```

Expected: clean. Fix any issue introduced by the new code.

- [ ] **Step 3: Live Discord smoke test**

Use the `discord-live-testing` skill to:

1. Start the bot, `/start_campaign` with theme "sous une église".
2. Wait for the launcher to spawn the church location and the iron cross.
3. As the player, type the exact phrasing from Test2.md:
   `inspecte la croix de fer pour voir si c une d'origine de 39-45`
4. Capture the bot's response.

Acceptance criteria for the response:
- (a) Mentions specific elements of the canonical scene (the cross, the church, the hermit), not generic filler.
- (b) Does NOT validate the WW2 (39-45) framing — the cross is medieval per the world generator's natural descriptions.
- (c) Stays in-character; no meta refusal like "I can't determine that."

If (b) fails (the model still validates the false assumption), iterate on the system prompt clause in Task 6 — strengthen the canon-faithfulness language and re-run the live test. The unit tests will continue to pass; only the live test exercises real LLM behavior.

- [ ] **Step 4: Final commit (if any prompt iterations)**

```bash
git add ai/prompts/system_narrator.txt
git commit -m "fix(narrator): strengthen canon clause based on live test"
```

---

## Out of scope (follow-ups for later plans)

- Populating `Location.item_descriptions` from the world generator. This plan adds the field and consumes it; the generator change is a separate plan because it touches LLM prompting and persistence migration.
- Adding the sliding-window recent-dialogue layer to `describe_scene_for_narrator`. The 4-layer memory system in [memory/](../../../memory/) isn't yet wired into the action pipeline; doing it here would pull in a much larger surface area.
- Replacing the `"picks up"` substring detection in `_resolve_mechanics` PICKUP branch with a proper structured return from `_resolve_pickup`. Works today; refactor when `_resolve_pickup` next changes.
