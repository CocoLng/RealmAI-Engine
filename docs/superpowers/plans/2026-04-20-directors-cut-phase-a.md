# Director's Cut — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot survive bad LLM responses (Narrator fallback chain), and split `bot/action_pipeline.py` (1898 lines) into a maintainable `bot/pipeline/*` package — without changing observable behavior.

**Architecture:** Pure-displacement refactor. Extract module-level stage functions from `ActionPipeline` into four focused modules (`interpret.py`, `resolve.py`, `narrate.py`, `orchestrator.py`). Introduce `PipelineContext` (per-action data carrier) and `PipelineDeps` (frozen services bundle) types. The legacy `ActionPipeline` class becomes a Facade in `bot/action_pipeline.py` that builds context+deps and delegates to the orchestrator — preserving its current public API (constructor signature, `process()`, `resume_with_resolution()`, `process_interpreted_action()`). Plus a three-tier Narrator fallback (primary call → simplified retry → hardcoded template) so a single bad LLM response never breaks a session.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, pytest-httpx (HTTP mocking for Ollama), asyncio, dataclasses.

**Spec:** [`docs/superpowers/specs/2026-04-20-directors-cut-design.md`](../specs/2026-04-20-directors-cut-design.md) — Sections 1 (Pipeline Split) + 6 (Narrator Fallback).

---

## File Structure

### New files (Phase A)

| File | Responsibility | Approx. lines |
|------|----------------|---------------|
| `bot/pipeline/__init__.py` | Re-exports for callers | ~20 |
| `bot/pipeline/types.py` | `PipelineContext`, `PipelineDeps`, stage protocol | ~80 |
| `bot/pipeline/interpret.py` | Stage 1 — interpret + entity resolution + validation | ~350 |
| `bot/pipeline/resolve.py` | Stage 2 — mechanics dispatch + combat helpers + beat completion check | ~700-900 |
| `bot/pipeline/narrate.py` | Stage 3 — context assembly + Narrator call + refusal narrators | ~300 |
| `bot/pipeline/orchestrator.py` | Top-level flow, persistence, beat application, progress emission | ~350 |
| `tests/bot/pipeline/__init__.py` | Test package init | 0 |
| `tests/bot/pipeline/test_types.py` | Unit tests for PipelineContext / PipelineDeps construction | ~80 |
| `tests/bot/pipeline/test_pipeline_facade.py` | Facade contract tests (input/output shape preserved) | ~120 |

### Modified files (Phase A)

| File | Change |
|------|--------|
| `bot/action_pipeline.py` | Becomes thin Facade (~150 lines): re-exports types, holds `ActionPipeline` class that builds ctx+deps and calls `orchestrator.run`. |
| `ai/narrator.py` | Add try/except + retry with simplified prompt + template fallback (~80 lines added). |
| `tests/ai/test_narrator.py` | Add tests for fallback chain (3 new test cases). |
| `tests/bot/test_action_pipeline.py` | Imports unchanged — Facade preserves API. May need `from bot.pipeline.X import Y` for new direct unit tests in later tasks. |

### Naming conventions inside the new package

- Stage functions: `async def run(ctx: PipelineContext, *, deps: PipelineDeps) -> PipelineContext`
- Helper functions: lowercase, snake_case, module-level (no classes unless needed)
- Each stage module exposes a top-level `run` plus any helper functions called only from within the orchestrator/Facade
- The legacy mutable side-channel state on `ActionPipeline` (`_trivial_kill_mechanics`, `_pending_flee_destination`, `_pending_combat_start_embed`, `_pending_dice_embeds`) moves onto `PipelineContext` as explicit optional fields. Callers that previously read those attributes off `ActionPipeline` (e.g. `ActionHandlerCog`) keep working through a passthrough on the Facade.

---

## Tasks Overview

| # | Task | Type | Estimated effort |
|---|------|------|------------------|
| A0 | Baseline verification (gate) | Verify | 5 min |
| A1 | Narrator template fallback (`_template_fallback`) | TDD | ~30 min |
| A2 | Narrator try/except + retry chain wired to A1 | TDD | ~45 min |
| A3 | Create `bot/pipeline/` package skeleton + `PipelineContext` / `PipelineDeps` | TDD | ~45 min |
| A4 | Extract narrate stage to `bot/pipeline/narrate.py` | Refactor | ~1h |
| A5 | Extract resolve stage to `bot/pipeline/resolve.py` | Refactor | ~1.5h |
| A6 | Extract interpret stage to `bot/pipeline/interpret.py` | Refactor | ~1h |
| A7 | Move orchestration to `bot/pipeline/orchestrator.py` + collapse `bot/action_pipeline.py` to Facade | Refactor | ~1.5h |

Total: ~6-7 hours of focused implementation. Spread over ~1 calendar week with breaks, code review, and live Discord sanity tests after each task.

**TDD cycle for refactor tasks (A4–A7):** baseline tests pass → move code with explicit param substitution → run full test suite → fix any breakage → commit. The "failing test" in those tasks is the existing test suite *staying* green; we are not writing new behavior.

---

## Task A0: Baseline Verification (Gate)

**Files:**
- Touch: none
- Run: full test suite

**Purpose:** Establish a green baseline. If anything is red here, fix that *first* — the refactor must not introduce new failures, but it can't fix pre-existing ones either.

- [ ] **Step 1: Confirm working tree is clean (or only spec/plan-related changes are staged)**

```bash
git status
```

Expected: only `docs/superpowers/specs/2026-04-20-directors-cut-design.md` and `docs/superpowers/plans/2026-04-20-directors-cut-phase-a.md` should appear in recent commits/staging. Any unrelated work-in-progress should be stashed before starting (`git stash push -m "WIP before Phase A"`).

- [ ] **Step 2: Run the full pytest suite**

```bash
uv run pytest -q
```

Expected: green or a known set of pre-existing failures noted explicitly. Record the count of passing/failing tests in the commit message of A1 as the baseline ("Baseline: X passed, Y failed (pre-existing)").

- [ ] **Step 3: Run lint and type checks**

```bash
uv run ruff check .
uv run mypy .
```

Expected: zero new errors introduced by us. Pre-existing issues are tolerated but recorded.

- [ ] **Step 4: Skim the live Ollama daemon health (sanity)**

```bash
curl -fsS http://localhost:11434/api/tags | head -c 200
```

Expected: a JSON list of installed models. If Ollama is down, narrator-fallback live testing in A2 will be skipped.

No commit at this gate — it's a check.

---

## Task A1: Narrator Template Fallback

**Goal:** Add a `_template_fallback()` method to `Narrator` that returns a valid `NarrativeResult` from a hardcoded short template. Never throws. Used by A2 as the third tier of the fallback chain.

**Files:**
- Modify: `ai/narrator.py:14-86` (Narrator class — add `_template_fallback` method + `_TEMPLATES` constant)
- Modify: `tests/ai/test_narrator.py` (add 1 new test class `TestTemplateFallback`)

- [ ] **Step 1: Write the failing test**

Append to `tests/ai/test_narrator.py`:

```python
class TestTemplateFallback:
    """Template fallback returns a valid NarrativeResult without calling LLM."""

    def test_template_fallback_returns_narrative_result(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
            outcome_facts="",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative
        assert len(result.narrative) >= 30
        assert result.tone in {"dramatic", "tense", "humorous", "somber"}

    def test_template_fallback_picks_attack_variant(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage dealt.",
            outcome_facts="",
        )
        # The "attack" template family includes the action verb in some way.
        assert "attaque" in result.narrative.lower() or "coup" in result.narrative.lower() \
            or "combat" in result.narrative.lower()

    def test_template_fallback_picks_default_for_unknown_verb(self, narrator: Narrator) -> None:
        result = narrator._template_fallback(
            action_result_text="Some unrecognized mechanical phrase.",
            outcome_facts="",
        )
        # Default template is the "MJ regroups" line.
        assert "rassemble" in result.narrative.lower() or "MJ" in result.narrative

    def test_template_fallback_does_not_call_llm(
        self, httpx_mock: HTTPXMock, narrator: Narrator
    ) -> None:
        # No httpx_mock.add_response calls — any HTTP would fail the test.
        result = narrator._template_fallback("Some action.", "Some outcome.")
        assert isinstance(result, NarrativeResult)
        assert len(httpx_mock.get_requests()) == 1  # Only the health check on init.
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ai/test_narrator.py::TestTemplateFallback -v
```

Expected: All 4 tests fail with `AttributeError: 'Narrator' object has no attribute '_template_fallback'`.

- [ ] **Step 3: Implement `_template_fallback` and `_TEMPLATES` in `ai/narrator.py`**

Add to `ai/narrator.py`, after the existing class body (inside the `Narrator` class):

```python
    _TEMPLATES: dict[str, list[str]] = {
        "attack": [
            "Le combat se poursuit dans la confusion. {action}.",
            "Les coups pleuvent autour de toi. {action}.",
            "L'affrontement reprend de plus belle. {action}.",
        ],
        "move": [
            "Le décor change autour de toi. {action}.",
            "Tes pas te portent ailleurs. {action}.",
        ],
        "talk": [
            "Les mots échangés résonnent encore dans l'air. {action}.",
            "La conversation suit son cours. {action}.",
        ],
        "search": [
            "Tu fouilles avec attention les environs. {action}.",
            "Tes mains parcourent l'endroit. {action}.",
        ],
        "default": [
            "Le MJ rassemble ses idées un instant. {action}.",
            "L'instant se prolonge avant la suite. {action}.",
        ],
    }

    def _template_fallback(
        self, action_result_text: str, outcome_facts: str
    ) -> NarrativeResult:
        """Return a hardcoded short narrative. Never raises.

        Used as the last-resort fallback when both the primary LLM call and
        the simplified retry have failed. The narrative is intentionally
        short and in-universe — its job is to keep the session alive, not to
        be invisible.
        """
        category = self._pick_template_category(action_result_text)
        variants = self._TEMPLATES.get(category, self._TEMPLATES["default"])
        # Deterministic-ish pick: use the length of action_result_text mod len(variants).
        # Avoids importing random for a 3-element list.
        template = variants[len(action_result_text) % len(variants)]
        narrative = template.format(action=action_result_text.rstrip("."))
        if outcome_facts:
            narrative = f"{narrative} {outcome_facts}"
        return NarrativeResult(narrative=narrative, tone="dramatic")

    @staticmethod
    def _pick_template_category(action_result_text: str) -> str:
        """Map the mechanical action verb to a template category."""
        lower = action_result_text.lower()
        if "attack" in lower or "attaque" in lower or "damage" in lower or "dégât" in lower:
            return "attack"
        if "move" in lower or "déplace" in lower or "go to" in lower:
            return "move"
        if "talk" in lower or "parle" in lower or "dialogue" in lower:
            return "talk"
        if "search" in lower or "fouille" in lower or "look" in lower:
            return "search"
        return "default"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/ai/test_narrator.py::TestTemplateFallback -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full narrator test file to ensure no regression**

```bash
uv run pytest tests/ai/test_narrator.py -v
```

Expected: all existing narrator tests still pass.

- [ ] **Step 6: Commit**

```bash
git add ai/narrator.py tests/ai/test_narrator.py
git commit -m "feat(narrator): add template fallback for LLM failure recovery"
```

---

## Task A2: Narrator Try/Except + Retry Chain

**Goal:** Wrap the primary `chat_json` call in `Narrator.narrate()` with a three-tier fallback: primary → simplified-prompt retry → template fallback. Catches `LLMParseError` (subclass of `ValueError`) and `OllamaUnavailableError` (from `ai.client`).

**Files:**
- Modify: `ai/narrator.py` (`narrate` method body)
- Modify: `tests/ai/test_narrator.py` (add `TestNarratorFallbackChain` class)

**Failure modes the chain must handle:**
- `LLMParseError` raised from `OllamaClient.chat_json` (empty content or non-JSON)
- `OllamaUnavailableError` raised from `OllamaClient.chat_json` (connection or timeout)
- Returned `narrative` shorter than 50 characters (treated as failure)

- [ ] **Step 1: Write the failing tests**

Append to `tests/ai/test_narrator.py`:

```python
from ai.client import LLMParseError, OllamaUnavailableError


class TestNarratorFallbackChain:
    """Narrator.narrate() never throws — falls back to template on repeated failure."""

    def test_narrate_returns_template_on_double_parse_error(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}

        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            raise LLMParseError(
                "boom", raw_response="", model="qwen3.5:9b", messages=[],
            )

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Thorin attacks Goblin. Hit! 8 damage.",
            context_prompt="Context.",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative  # Template returned, non-empty
        assert call_count["n"] == 2  # Primary + simplified retry, then template

    def test_narrate_returns_template_on_ollama_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        def fake_chat_json(*args, **kwargs):
            raise OllamaUnavailableError("Ollama down")

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Some action.",
            context_prompt="Some context.",
        )
        assert isinstance(result, NarrativeResult)
        assert result.narrative

    def test_narrate_retries_with_simplified_prompt_when_first_call_too_short(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}
        responses = [
            {"narrative": "Short.", "tone": "dramatic"},  # Too short → retry
            {"narrative": "A much longer second narrative that will pass the 50-char threshold.", "tone": "tense"},
        ]

        def fake_chat_json(*args, **kwargs):
            return responses[call_count["n"]] if call_count["n"] < len(responses) else responses[-1]
            # noqa: defensive

        # Wrap to track calls and advance index
        def chat_json_advance(*args, **kwargs):
            r = responses[call_count["n"]]
            call_count["n"] += 1
            return r

        monkeypatch.setattr(narrator._client, "chat_json", chat_json_advance)
        result = narrator.narrate(
            action_result_text="Some action.",
            context_prompt="Some context.",
        )
        assert call_count["n"] == 2  # Primary failed (too short) + simplified retry succeeded
        assert "longer second narrative" in result.narrative
        assert result.tone == "tense"

    def test_narrate_succeeds_first_call_no_retry(
        self, monkeypatch: pytest.MonkeyPatch, narrator: Narrator
    ) -> None:
        call_count = {"n": 0}

        def fake_chat_json(*args, **kwargs):
            call_count["n"] += 1
            return {
                "narrative": "A perfectly valid first-call narrative that exceeds fifty characters in length easily.",
                "tone": "dramatic",
            }

        monkeypatch.setattr(narrator._client, "chat_json", fake_chat_json)
        result = narrator.narrate(
            action_result_text="Action.", context_prompt="Context.",
        )
        assert call_count["n"] == 1  # Only the primary call
        assert "perfectly valid first-call narrative" in result.narrative
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/ai/test_narrator.py::TestNarratorFallbackChain -v
```

Expected: `test_narrate_returns_template_on_double_parse_error` fails with `LLMParseError` propagating uncaught (current behavior); other tests fail similarly or with mismatched call counts.

- [ ] **Step 3: Refactor `Narrator.narrate()` with the fallback chain**

Replace the body of `narrate()` in `ai/narrator.py` (current lines ~57–85) with this:

```python
    def narrate(
        self,
        action_result_text: str,
        context_prompt: str,
        language: str = "fr",
        player_intent: str = "",
        outcome_facts: str = "",
        has_npc_dialogue: bool = False,
    ) -> NarrativeResult:
        """Generate an immersive narrative description of a resolved action.

        Three-tier fallback chain — never throws:
          1. Primary call with full prompt
          2. Retry with a simplified prompt (only action + context)
          3. Hardcoded template fallback (always succeeds)
        """
        logger.info("NARRATE input=%r intent=%r", action_result_text[:100], player_intent[:100])

        # --- Tier 1: primary call ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent=player_intent,
                outcome_facts=outcome_facts,
                has_npc_dialogue=has_npc_dialogue,
                simplified=False,
            )
            if len(result.narrative) >= 50:
                return result
            logger.warning(
                "Narrator primary returned short narrative (%d chars), retrying simplified",
                len(result.narrative),
            )
        except (LLMParseError, OllamaUnavailableError) as exc:
            logger.warning("Narrator primary call failed (%s), retrying simplified", exc)

        # --- Tier 2: simplified retry ---
        try:
            result = self._call_llm(
                action_result_text=action_result_text,
                context_prompt=context_prompt,
                language=language,
                player_intent="",
                outcome_facts="",
                has_npc_dialogue=False,
                simplified=True,
            )
            if len(result.narrative) >= 50:
                return result
            logger.error(
                "Narrator simplified retry returned short narrative (%d chars), using template",
                len(result.narrative),
            )
        except (LLMParseError, OllamaUnavailableError) as exc:
            logger.error("Narrator simplified retry failed (%s), using template", exc)

        # --- Tier 3: template fallback (never raises) ---
        return self._template_fallback(action_result_text, outcome_facts)

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
    ) -> NarrativeResult:
        """Issue one LLM call and parse the response. May raise.

        ``simplified=True`` strips optional sections from the user message —
        useful when the primary call failed and we suspect the prompt may
        have confused the model.
        """
        sections: list[str] = [context_prompt, f"## What happened\n{action_result_text}"]
        if not simplified:
            if player_intent:
                sections.append(f"## Player framing\n{player_intent}")
            if outcome_facts:
                sections.append(f"## State changes\n{outcome_facts}")
            if has_npc_dialogue:
                sections.append(
                    "## Important\n"
                    "NPC dialogue will be displayed separately. "
                    "Describe ONLY atmosphere and body language around the "
                    "speech. Do NOT write any spoken words."
                )
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

Add the import at the top of `ai/narrator.py`:

```python
from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
```

(Replacing the current `from ai.client import OllamaClient` line.)

- [ ] **Step 4: Run the new fallback tests**

```bash
uv run pytest tests/ai/test_narrator.py::TestNarratorFallbackChain -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the full narrator test file**

```bash
uv run pytest tests/ai/test_narrator.py -v
```

Expected: all existing tests still green (the refactor preserves behavior on success path).

- [ ] **Step 6: Run the broader pipeline test surface to catch any indirect regression**

```bash
uv run pytest tests/bot/test_action_pipeline.py -q
```

Expected: same pass/fail count as the A0 baseline.

- [ ] **Step 7: Commit**

```bash
git add ai/narrator.py tests/ai/test_narrator.py
git commit -m "feat(narrator): three-tier fallback chain — primary, simplified retry, template"
```

---

## Task A3: Create `bot/pipeline/` Package + `PipelineContext` / `PipelineDeps`

**Goal:** Lay the package skeleton and define the data carrier types used by all subsequent stage extractions. No behavior change; nothing is wired in yet.

**Files:**
- Create: `bot/pipeline/__init__.py`
- Create: `bot/pipeline/types.py`
- Create: `tests/bot/pipeline/__init__.py` (empty)
- Create: `tests/bot/pipeline/test_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/bot/pipeline/__init__.py`:

```python
```

Create `tests/bot/pipeline/test_types.py`:

```python
"""Unit tests for PipelineContext and PipelineDeps."""

from unittest.mock import MagicMock

import pytest

from ai.interpreter import Interpreter
from ai.models import InterpretedAction, MechanicsOutcome
from ai.narrator import Narrator
from bot.pipeline.types import PipelineContext, PipelineDeps
from engine.validators import ActionType


class TestPipelineContext:
    def test_minimal_construction(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp_test",
            player_message_id=42,
            player_input="Je fouille la pièce.",
            actor_name="Thorin",
        )
        assert ctx.campaign_id == "cmp_test"
        assert ctx.language == "fr"
        assert ctx.interpreted is None
        assert ctx.mechanics_outcome is None
        assert ctx.beat_advanced is False

    def test_optional_stage_fields_default_none(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        assert ctx.validation_error is None
        assert ctx.combat_state_after is None
        assert ctx.assembled_context is None
        assert ctx.narrative_result is None

    def test_can_attach_interpreted_action(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        action = InterpretedAction(
            action_type=ActionType.SEARCH,
            actor_name="Thorin",
            raw_input="Je fouille.",
        )
        ctx2 = ctx.model_copy(update={"interpreted": action})
        assert ctx2.interpreted is action

    def test_pending_side_channels_default_empty(self) -> None:
        ctx = PipelineContext(
            campaign_id="cmp", player_message_id=1, player_input="x", actor_name="X",
        )
        assert ctx.pending_flee_destination is None
        assert ctx.pending_combat_start_embed is None
        assert ctx.pending_dice_embeds == []
        assert ctx.trivial_kill_mechanics is None


class TestPipelineDeps:
    def test_construction(self) -> None:
        interpreter = MagicMock(spec=Interpreter)
        narrator = MagicMock(spec=Narrator)
        deps = PipelineDeps(interpreter=interpreter, narrator=narrator)
        assert deps.interpreter is interpreter
        assert deps.narrator is narrator

    def test_deps_is_frozen(self) -> None:
        interpreter = MagicMock(spec=Interpreter)
        narrator = MagicMock(spec=Narrator)
        deps = PipelineDeps(interpreter=interpreter, narrator=narrator)
        with pytest.raises((AttributeError, TypeError)):
            deps.interpreter = MagicMock(spec=Interpreter)  # type: ignore[misc]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/bot/pipeline/test_types.py -v
```

Expected: `ModuleNotFoundError: No module named 'bot.pipeline'`.

- [ ] **Step 3: Create the package files**

Create `bot/pipeline/__init__.py`:

```python
"""Action pipeline package — splits the legacy ActionPipeline into stages.

Stages:
- ``interpret`` — text → InterpretedAction + entity resolution + validation
- ``resolve``   — mechanics dispatch + combat helpers + beat completion check
- ``narrate``   — context assembly + Narrator call + refusal narrators

The orchestrator wires them together and the Facade in
``bot.action_pipeline.ActionPipeline`` preserves the legacy public API.
"""

from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = ["PipelineContext", "PipelineDeps"]
```

Create `bot/pipeline/types.py`:

```python
"""Shared pipeline data types.

``PipelineContext`` carries per-action data through the stages.
``PipelineDeps`` carries long-lived service references (LLM clients, etc.).

Stages follow this signature:

    async def run(ctx: PipelineContext, *, deps: PipelineDeps) -> PipelineContext

Each stage builds on the previous one's output by calling
``ctx.model_copy(update={...})`` — never mutates fields set earlier.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ai.interpreter import Interpreter
    from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult
    from ai.narrator import Narrator
    from engine.combat import CombatState
    from engine.inventory import Inventory
    from world.location import Location
    from world.npc import NPC

    from bot.combat_entry import CombatTrigger
    from bot.game_session import GameSession


class PipelineContext(BaseModel):
    """Carried through every stage. Stages add fields, never mutate earlier ones.

    The "pending_*" / "trivial_kill_mechanics" fields exist to preserve the
    legacy side-channel state previously held on ``ActionPipeline`` instance
    attributes. They are read by the Facade adapter and by some downstream
    callers (e.g. ``ActionHandlerCog`` reads ``pending_combat_start_embed``).
    """

    model_config = {"arbitrary_types_allowed": True}

    # --- Per-action input ---
    campaign_id: str
    player_message_id: int
    player_input: str
    actor_name: str
    language: str = "fr"

    # --- Set by interpret stage ---
    interpreted: "InterpretedAction | None" = None
    validation_error: str | None = None

    # --- Set by resolve stage ---
    mechanics_outcome: "MechanicsOutcome | None" = None
    beat_advanced: bool = False
    new_beat: Any = None  # StoryBeat | None — typed as Any to avoid heavy import
    combat_state_after: "CombatState | None" = None

    # --- Set by narrate stage ---
    assembled_context: str | None = None
    narrative_result: "NarrativeResult | None" = None

    # --- Side-channel state (legacy compatibility) ---
    pending_flee_destination: str | None = None
    pending_combat_start_embed: Any = None  # tuple[CombatState, CombatTrigger] | None
    pending_dice_embeds: list[Any] = Field(default_factory=list)
    trivial_kill_mechanics: str | None = None


@dataclass(frozen=True)
class PipelineDeps:
    """Long-lived services injected into stage functions.

    Frozen so that stages cannot mutate the dependency graph mid-pipeline.
    """

    interpreter: "Interpreter"
    narrator: "Narrator"
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest tests/bot/pipeline/test_types.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Confirm full suite still green (no regression from new package)**

```bash
uv run pytest -q
```

Expected: same pass/fail as A0 baseline + 5 new passes.

- [ ] **Step 6: Commit**

```bash
git add bot/pipeline/__init__.py bot/pipeline/types.py tests/bot/pipeline/__init__.py tests/bot/pipeline/test_types.py
git commit -m "feat(pipeline): add bot/pipeline/ package skeleton with PipelineContext + PipelineDeps"
```

---

## Task A4: Extract Narrate Stage to `bot/pipeline/narrate.py`

**Goal:** Move the context-assembly + narrator-call code out of `ActionPipeline` into module-level functions in `bot/pipeline/narrate.py`. The Facade keeps calling them via thin wrapper methods. Tests that exercise the full pipeline must stay green without modification.

**Methods to extract from `bot/action_pipeline.py`** (per the cartography in the spec discussion):

- `_assemble_context(action, current_outcome_summary=None, ongoing_dialogue_with=None)` — line ~1752
- `_build_player_intent(action)` — line ~1427
- `_call_narrator(outcome, context_prompt)` — line ~597
- `_narrate_unknown(action, resolution)` — line ~1792
- `_narrate_rule_failure(action, validation)` — line ~1843

**Files:**
- Create: `bot/pipeline/narrate.py`
- Modify: `bot/action_pipeline.py` (delete the moved methods, replace internal call sites with module-function calls)
- Modify: `bot/pipeline/__init__.py` (re-export `narrate` module)

- [ ] **Step 1: Establish the green baseline before moving code**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_pipeline_dialogue.py tests/bot/test_action_pipeline_interaction.py -q
```

Record the result. Any new failure introduced in subsequent steps is on us.

- [ ] **Step 2: Create `bot/pipeline/narrate.py` with the extracted functions**

Create the file with this skeleton — copy each function body verbatim from `bot/action_pipeline.py` at the line numbers indicated, applying these mechanical substitutions:

- `self.actor_name` → `actor_name` (parameter)
- `self.location` → `location` (parameter)
- `self.npcs` → `npcs` (parameter)
- `self.session` → `session` (parameter)
- `self.combat_state` → `combat_state` (parameter)
- `self.inventory` → `inventory` (parameter)
- `self.language` → `language` (parameter)
- `self.campaign_id` → `campaign_id` (parameter)
- `self.narrator` → `narrator` (parameter)

```python
"""Narrate stage — context assembly + Narrator call.

Pure-function module: takes explicit parameters, returns values. No state.
Extracted from bot/action_pipeline.py during the Phase A pipeline split.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from ai.entity_resolver import ResolutionResult
from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult
from ai.narrator import Narrator
from ai.scene_context import SceneContext, build_scene_context
from bot.llm_retry import retry_llm_call
from engine.character import Character
from engine.combat import CombatState
from engine.inventory import Inventory
from engine.validators import ValidationResult
from world.location import Location
from world.npc import NPC

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


def assemble_context(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    session: "GameSession | None",
    combat_state: CombatState | None,
    inventory: Inventory | None,
    campaign_id: str,
    current_outcome_summary: str | None = None,
    ongoing_dialogue_with: str | None = None,
) -> str:
    """Assemble the narrator context prompt.

    [paste body from bot/action_pipeline.py:1752–~1791, with the substitutions above]
    """
    # ... moved verbatim ...


def build_player_intent(action: InterpretedAction) -> str:
    """Compose a short framing string from the InterpretedAction.

    [paste body from bot/action_pipeline.py:1427–~1442]
    """
    # ... moved verbatim ...


async def call_narrator(
    *,
    narrator: Narrator,
    outcome: MechanicsOutcome,
    context_prompt: str,
    language: str,
    has_npc_dialogue: bool = False,
) -> NarrativeResult:
    """Invoke the Narrator LLM with retry. Returns NarrativeResult.

    [paste body from bot/action_pipeline.py:597–~618]
    """
    # ... moved verbatim ...


async def narrate_unknown(
    *,
    narrator: Narrator,
    action: InterpretedAction,
    resolution: ResolutionResult,
    actor_name: str,
    location: Location | None,
    language: str,
) -> tuple[str, Literal["dramatic", "tense", "humorous", "somber"]]:
    """Generate an in-character refusal for an unresolved entity.

    [paste body from bot/action_pipeline.py:1792–~1842]
    """
    # ... moved verbatim ...


async def narrate_rule_failure(
    *,
    narrator: Narrator,
    action: InterpretedAction,
    validation: ValidationResult,
    actor_name: str,
    location: Location | None,
    language: str,
) -> tuple[str, Literal["dramatic", "tense", "humorous", "somber"]]:
    """Generate an in-character narration for a rules failure.

    [paste body from bot/action_pipeline.py:1843–~1883]
    """
    # ... moved verbatim ...
```

When pasting each body, **resolve every `self.` reference** to either:
- A keyword-only parameter on the new function (preferred), or
- An import inside the function (e.g. `retry_llm_call` is already imported at module top)

If a method body references *another* method that is also moving (e.g. `_call_narrator` calls `_build_player_intent`), call the new module function: `build_player_intent(action)` instead of `self._build_player_intent(action)`.

- [ ] **Step 3: Update `bot/pipeline/__init__.py`**

```python
"""Action pipeline package — splits the legacy ActionPipeline into stages."""

from bot.pipeline import narrate
from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = ["PipelineContext", "PipelineDeps", "narrate"]
```

- [ ] **Step 4: Replace the moved methods on `ActionPipeline` with thin wrappers**

In `bot/action_pipeline.py`, delete the bodies of the five extracted methods. Replace each with a one-line wrapper that calls the module function:

```python
    def _assemble_context(self, action, current_outcome_summary=None, ongoing_dialogue_with=None):
        from bot.pipeline import narrate
        return narrate.assemble_context(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            session=self.session,
            combat_state=self.combat_state,
            inventory=self.inventory,
            campaign_id=self.campaign_id,
            current_outcome_summary=current_outcome_summary,
            ongoing_dialogue_with=ongoing_dialogue_with,
        )

    def _build_player_intent(self, action):
        from bot.pipeline import narrate
        return narrate.build_player_intent(action)

    async def _call_narrator(self, outcome, context_prompt, has_npc_dialogue=False):
        from bot.pipeline import narrate
        return await narrate.call_narrator(
            narrator=self.narrator,
            outcome=outcome,
            context_prompt=context_prompt,
            language=self.language,
            has_npc_dialogue=has_npc_dialogue,
        )

    async def _narrate_unknown(self, action, resolution):
        from bot.pipeline import narrate
        return await narrate.narrate_unknown(
            narrator=self.narrator, action=action, resolution=resolution,
            actor_name=self.actor_name, location=self.location, language=self.language,
        )

    async def _narrate_rule_failure(self, action, validation):
        from bot.pipeline import narrate
        return await narrate.narrate_rule_failure(
            narrator=self.narrator, action=action, validation=validation,
            actor_name=self.actor_name, location=self.location, language=self.language,
        )
```

The `from bot.pipeline import narrate` is local to each method to keep the module-level import graph free of cycles.

- [ ] **Step 5: Run the pipeline test files to verify no regression**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_pipeline_dialogue.py tests/bot/test_action_pipeline_interaction.py -q
```

Expected: same pass count as Step 1 baseline.

- [ ] **Step 6: Run the full test suite**

```bash
uv run pytest -q
```

Expected: same pass/fail as A0 baseline.

- [ ] **Step 7: Run lint + type check**

```bash
uv run ruff check bot/pipeline/ bot/action_pipeline.py
uv run mypy bot/pipeline/ bot/action_pipeline.py
```

Expected: zero new errors.

- [ ] **Step 8: Commit**

```bash
git add bot/pipeline/narrate.py bot/pipeline/__init__.py bot/action_pipeline.py
git commit -m "refactor(pipeline): extract narrate stage to bot/pipeline/narrate.py"
```

---

## Task A5: Extract Resolve Stage to `bot/pipeline/resolve.py`

**Goal:** Move the mechanics dispatch and per-action-type resolvers, plus combat helpers and trivial-resolve logic, out of `ActionPipeline` into `bot/pipeline/resolve.py`. Same Facade pattern as A4.

**Methods to extract from `bot/action_pipeline.py`** (heavy task — this is the largest stage):

| Method | Approx. line | Notes |
|--------|-------------|-------|
| `_resolve_mechanics(action)` | 1033 | Top-level dispatcher by action_type |
| `_resolve_equip(action)` | 949 | EQUIP — free action |
| `_resolve_use_item(action)` | 977 | USE_ITEM (potions, etc.) |
| `_resolve_flee(action)` | 1186 | FLEE with DEX check + dice embed append |
| `_resolve_talk(action)` | 1443 | TALK out-of-combat |
| `_resolve_talk_in_combat(action)` | 1586 | TALK in active combat |
| `_resolve_pc_attack(action)` | 1643 | PC weapon attack with d20 + dice embed |
| `_resolve_pickup(action)` | 1716 | PICKUP item from location |
| `_should_trivial_resolve(npc)` | 748 | Combat-bypass guard |
| `_trivial_kill(target_npc)` | 791 | Auto-kill weak NPCs |
| `_find_attacker_character()` | 815 | Helper for trivial kill |
| `_find_attacker_weapon(attacker_pc)` | 824 | Helper |
| `_handle_npc_death(npc)` | 839 | Apply death side effects |
| `_persist_death(npc, killer_pc)` | 909 | DB write for kill |
| `_append_world_fact(fact)` | 933 | World fact persistence |

**Side-channel state** that this stage produces:
- `_pending_flee_destination` (set by `_resolve_flee`) → carry on `PipelineContext.pending_flee_destination`
- `_pending_dice_embeds` (appended by `_resolve_flee`, `_resolve_pc_attack`) → carry on `PipelineContext.pending_dice_embeds`
- `_trivial_kill_mechanics` (set by `_trivial_kill`) → carry on `PipelineContext.trivial_kill_mechanics`

For the extraction, these become **return values** alongside the `MechanicsOutcome`. The Facade adapter merges them back onto the legacy state.

**Files:**
- Create: `bot/pipeline/resolve.py`
- Modify: `bot/action_pipeline.py` (delete moved methods, install thin wrappers)
- Modify: `bot/pipeline/__init__.py` (re-export `resolve`)

- [ ] **Step 1: Re-establish baseline**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_pipeline_dialogue.py tests/bot/test_action_pipeline_interaction.py -q
uv run pytest tests/scenarios/ -q
```

Record counts.

- [ ] **Step 2: Create `bot/pipeline/resolve.py` skeleton with extracted functions**

Match the same substitution rules as A4 (`self.X` → keyword parameter `X`).

For functions that previously mutated `self._pending_*` or `self._trivial_kill_mechanics`, change them to return a small dataclass alongside the `MechanicsOutcome`:

```python
"""Resolve stage — engine mechanics dispatch + combat helpers.

Pure-function module. Stage helpers return (MechanicsOutcome, ResolveSideChannel)
so the Facade can rehydrate the legacy ``ActionPipeline._pending_*`` attributes.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai.models import InterpretedAction, MechanicsOutcome, PublicEffects
from bot.combat_entry import CombatTrigger
from bot.persistence import persist_session
from engine.character import Character, compute_modifier
from engine.combat import (
    CombatEndReason,
    CombatSide,
    CombatState,
    TrivialResolveResult,
    check_combat_end,
    record_combat_event,
    start_combat,
    trivial_resolve,
)
from engine.conditions import ActiveCondition, ConditionType
from engine.dice import RollOutcome, roll_check
from engine.inventory import EquipmentSlot, Inventory, Weapon, equip_item, remove_item, unequip_item
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC, NPCDisposition

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


@dataclass
class ResolveSideChannel:
    """Side-channel outputs from resolve stage.

    Originally set as ActionPipeline._pending_* attributes. Returned
    alongside the MechanicsOutcome so the Facade can hand them off to
    the right caller.
    """
    pending_flee_destination: str | None = None
    pending_dice_embeds: list[Any] = field(default_factory=list)
    trivial_kill_mechanics: str | None = None


async def resolve_mechanics(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: CombatState | None,
    inventory: Inventory | None,
    session: "GameSession | None",
    campaign_id: str,
    db_factory: Any | None,
) -> tuple[MechanicsOutcome, ResolveSideChannel]:
    """Dispatch action_type → resolver → return outcome + side channel.

    [paste body from bot/action_pipeline.py:1033–~1185, with substitutions]
    Replace each ``self._resolve_X(action)`` with the new module function:
    ``resolve_X(action=action, ..., side=side)`` where ``side`` is the
    accumulating ResolveSideChannel.
    """
    # ... extracted ...


# Per-action-type resolvers (each takes the new ResolveSideChannel and
# may mutate it; returns the MechanicsOutcome).
def resolve_equip(*, action, actor_name, inventory, side: ResolveSideChannel) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:949–~976]"""

def resolve_use_item(*, action, actor_name, inventory, side: ResolveSideChannel) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:977–~1032]"""

async def resolve_flee(*, action, actor_name, location, combat_state, side: ResolveSideChannel) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:1186–~1275]"""

async def resolve_talk(*, action, actor_name, location, npcs, session, campaign_id, side) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:1443–~1585]"""

async def resolve_talk_in_combat(*, action, actor_name, npcs, combat_state, side) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:1586–~1642]"""

async def resolve_pc_attack(*, action, actor_name, location, npcs, combat_state, inventory, side) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:1643–~1715]"""

def resolve_pickup(*, action, actor_name, location, inventory, side) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:1716–~1751]"""


# Combat helpers
def should_trivial_resolve(*, npc: NPC, combat_state: CombatState | None) -> bool:
    """[paste body from bot/action_pipeline.py:748–~790]"""

def trivial_kill(*, target_npc, actor_name, location, npcs, side: ResolveSideChannel) -> MechanicsOutcome:
    """[paste body from bot/action_pipeline.py:791–~814]"""

def find_attacker_character(*, actor_name, session) -> Character | None:
    """[paste body from bot/action_pipeline.py:815–~823]"""

def find_attacker_weapon(*, attacker_pc, inventory) -> Weapon | None:
    """[paste body from bot/action_pipeline.py:824–~838]"""

def handle_npc_death(*, npc, location, npcs, session, campaign_id, db_factory) -> None:
    """[paste body from bot/action_pipeline.py:839–~908]"""

def persist_death(*, npc, killer_pc, db_factory) -> None:
    """[paste body from bot/action_pipeline.py:909–~932]"""

def append_world_fact(*, fact: str, session, db_factory) -> None:
    """[paste body from bot/action_pipeline.py:933–~948]"""
```

When pasting bodies, search for `self._pending_flee_destination = X` and replace with `side.pending_flee_destination = X`. Same for the other side-channel fields.

- [ ] **Step 3: Update `bot/pipeline/__init__.py` to re-export resolve**

```python
from bot.pipeline import narrate, resolve
from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = ["PipelineContext", "PipelineDeps", "narrate", "resolve"]
```

- [ ] **Step 4: Replace moved methods on `ActionPipeline` with thin wrappers**

For each extracted method, replace its body with a wrapper that:
1. Builds a `ResolveSideChannel` (or reuses an instance attribute holding one)
2. Calls the module function
3. Copies any side-channel updates back to `self._pending_*` (legacy)

Example for `_resolve_mechanics`:

```python
    async def _resolve_mechanics(self, action: InterpretedAction) -> MechanicsOutcome:
        from bot.pipeline import resolve
        side = resolve.ResolveSideChannel(
            pending_flee_destination=self._pending_flee_destination,
            pending_dice_embeds=list(self._pending_dice_embeds),
            trivial_kill_mechanics=self._trivial_kill_mechanics,
        )
        outcome, side = await resolve.resolve_mechanics(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            campaign_id=self.campaign_id,
            db_factory=self.db_factory,
        )
        self._pending_flee_destination = side.pending_flee_destination
        self._pending_dice_embeds = side.pending_dice_embeds
        self._trivial_kill_mechanics = side.trivial_kill_mechanics
        return outcome
```

Apply the same pattern for the other moved methods. The `_resolve_*` per-action-type resolvers are now called directly by `resolve.resolve_mechanics` inside the module, so the `ActionPipeline` does not need wrappers for them — they can be deleted from the class entirely.

- [ ] **Step 5: Run pipeline + scenario tests**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_pipeline_dialogue.py tests/bot/test_action_pipeline_interaction.py tests/scenarios/ -q
```

Expected: same pass count as Step 1 baseline.

- [ ] **Step 6: Run combat-adjacent tests (resolve touches combat heavily)**

```bash
uv run pytest tests/bot/test_combat_action_view.py tests/bot/test_combat_truce.py tests/bot/test_turn_manager.py -q
```

Expected: green (or same baseline).

- [ ] **Step 7: Full suite**

```bash
uv run pytest -q
```

Expected: same as A0.

- [ ] **Step 8: Lint + type**

```bash
uv run ruff check bot/pipeline/ bot/action_pipeline.py
uv run mypy bot/pipeline/ bot/action_pipeline.py
```

- [ ] **Step 9: Commit**

```bash
git add bot/pipeline/resolve.py bot/pipeline/__init__.py bot/action_pipeline.py
git commit -m "refactor(pipeline): extract resolve stage to bot/pipeline/resolve.py"
```

**Note on file size:** if `bot/pipeline/resolve.py` exceeds ~900 lines after extraction, add a follow-up commit splitting per-action-type resolvers into a sub-package (`bot/pipeline/resolve/`). This is a judgment call — make it during the extraction, not after. Treat ≤700 lines as ideal.

---

## Task A6: Extract Interpret Stage to `bot/pipeline/interpret.py`

**Goal:** Move interpretation, weapon-name auto-resolution, and validation out of `ActionPipeline` into `bot/pipeline/interpret.py`.

**Methods to extract:**
- `_call_interpreter(player_text, scene)` — line ~579
- `_auto_resolve_weapon_name(weapon_name, inventory)` — line ~619
- `_validate(action)` — line ~659

`_validate` is the large one — it includes the MOVE→FLEE auto-conversion logic that mutates `self._pending_flee_destination` and the combat-bootstrap logic that mutates `self._pending_combat_start_embed`. These side channels follow the same pattern as A5 (return on a small dataclass; Facade copies back).

**Files:**
- Create: `bot/pipeline/interpret.py`
- Modify: `bot/action_pipeline.py` (replace bodies with wrappers)
- Modify: `bot/pipeline/__init__.py` (re-export)

- [ ] **Step 1: Baseline**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_handler_cog.py tests/bot/test_action_handler_resilience.py -q
```

Record.

- [ ] **Step 2: Create `bot/pipeline/interpret.py`**

```python
"""Interpret stage — interpreter call + weapon resolution + validation.

Pure-function module. The validation step may set side-channel state
(MOVE→FLEE auto-conversion, combat bootstrap detection) — returned
on InterpretSideChannel.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ai.entity_resolver import EntityCandidate, EntityResolver, ResolutionResult
from ai.interpreter import Interpreter
from ai.models import InterpretedAction
from ai.scene_context import SceneContext, build_scene_context
from bot.combat_entry import CombatTrigger, detect_combat_trigger, enter_combat
from bot.llm_retry import retry_llm_call
from engine.combat import CombatSide, CombatState, start_combat
from engine.inventory import Inventory, Weapon
from engine.validators import (
    Action,
    ActionType,
    EXPLORATION_ACTION_TYPES,
    ValidationResult,
    validate_action,
    validate_exploration_action,
)
from world.location import Location
from world.npc import NPC

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


@dataclass
class InterpretSideChannel:
    """Side-channel outputs from interpret stage."""
    pending_flee_destination: str | None = None
    pending_combat_start_embed: Any = None  # tuple[CombatState, CombatTrigger] | None


async def call_interpreter(
    *,
    interpreter: Interpreter,
    player_text: str,
    scene: SceneContext,
    actor_name: str,
    language: str,
) -> InterpretedAction:
    """[paste body from bot/action_pipeline.py:579–~596]"""


def auto_resolve_weapon_name(*, weapon_name: str | None, inventory: Inventory | None) -> str | None:
    """[paste body from bot/action_pipeline.py:619–~658]"""


def validate(
    *,
    action: InterpretedAction,
    actor_name: str,
    location: Location | None,
    npcs: dict[str, NPC],
    combat_state: CombatState | None,
    inventory: Inventory | None,
    session: "GameSession | None",
    side: InterpretSideChannel,
) -> ValidationResult:
    """Validate the action under current rules.

    [paste body from bot/action_pipeline.py:659–~747, with substitutions]
    Side-channel writes:
      - MOVE→FLEE conversion: ``side.pending_flee_destination = X``
      - Combat bootstrap: ``side.pending_combat_start_embed = (state, trigger)``
    """
```

- [ ] **Step 3: Update `bot/pipeline/__init__.py`**

```python
from bot.pipeline import interpret, narrate, resolve
from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = ["PipelineContext", "PipelineDeps", "interpret", "narrate", "resolve"]
```

- [ ] **Step 4: Replace bodies on `ActionPipeline` with wrappers**

```python
    async def _call_interpreter(self, player_text: str, scene: SceneContext) -> InterpretedAction:
        from bot.pipeline import interpret
        return await interpret.call_interpreter(
            interpreter=self.interpreter, player_text=player_text, scene=scene,
            actor_name=self.actor_name, language=self.language,
        )

    def _auto_resolve_weapon_name(self, weapon_name, inventory):
        from bot.pipeline import interpret
        return interpret.auto_resolve_weapon_name(weapon_name=weapon_name, inventory=inventory)

    def _validate(self, action: InterpretedAction) -> ValidationResult:
        from bot.pipeline import interpret
        side = interpret.InterpretSideChannel(
            pending_flee_destination=self._pending_flee_destination,
            pending_combat_start_embed=self._pending_combat_start_embed,
        )
        result = interpret.validate(
            action=action,
            actor_name=self.actor_name,
            location=self.location,
            npcs=self.npcs,
            combat_state=self.combat_state,
            inventory=self.inventory,
            session=self.session,
            side=side,
        )
        self._pending_flee_destination = side.pending_flee_destination
        self._pending_combat_start_embed = side.pending_combat_start_embed
        return result
```

- [ ] **Step 5: Run pipeline + cog tests**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_handler_cog.py tests/bot/test_action_handler_resilience.py -q
```

Expected: same pass count as Step 1 baseline.

- [ ] **Step 6: Full suite + lint + type**

```bash
uv run pytest -q
uv run ruff check bot/pipeline/ bot/action_pipeline.py
uv run mypy bot/pipeline/ bot/action_pipeline.py
```

- [ ] **Step 7: Commit**

```bash
git add bot/pipeline/interpret.py bot/pipeline/__init__.py bot/action_pipeline.py
git commit -m "refactor(pipeline): extract interpret stage to bot/pipeline/interpret.py"
```

---

## Task A7: Move Orchestration to `bot/pipeline/orchestrator.py` + Collapse Facade

**Goal:** Move the top-level flow methods (`process`, `resume_with_resolution`, `process_interpreted_action`, `_continue_from_resolution`, `_check_beat_completion`, `_apply_beat_effects`, `_llm_beat_fallback`, `_emit`) into `bot/pipeline/orchestrator.py`. The remaining `bot/action_pipeline.py` is a thin Facade re-exporting types and forwarding the three public methods.

**Files:**
- Create: `bot/pipeline/orchestrator.py`
- Modify: `bot/action_pipeline.py` — strip down to a Facade (~150 lines)
- Modify: `bot/pipeline/__init__.py` — re-export orchestrator
- Add: `tests/bot/pipeline/test_pipeline_facade.py` — pin the Facade's public surface

- [ ] **Step 1: Baseline**

```bash
uv run pytest -q
```

Record.

- [ ] **Step 2: Create `bot/pipeline/orchestrator.py`**

The orchestrator holds the flow logic but operates on data passed in via parameters. The Facade in `bot/action_pipeline.py` wraps it.

```python
"""Pipeline orchestrator — top-level flow chaining the three stages.

Offers a class ``PipelineRunner`` whose ``run_*`` methods accept the same
arguments the legacy ``ActionPipeline`` did. The Facade in
``bot.action_pipeline.ActionPipeline`` instantiates one of these per
action.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ai.entity_resolver import EntityCandidate, EntityResolver, ResolutionResult
from ai.interpreter import Interpreter
from ai.models import InterpretedAction, MechanicsOutcome, NarrativeResult, PublicEffects
from ai.narrator import Narrator
from ai.scene_context import build_scene_context
from bot.persistence import persist_session
from bot.pipeline import interpret, narrate, resolve
from engine.combat import CombatState
from engine.inventory import Inventory
from engine.validators import ActionType
from world.location import Location
from world.npc import NPC
from world.story_arc import BeatEffects, StoryBeat

if TYPE_CHECKING:
    from bot.game_session import GameSession

logger = logging.getLogger(__name__)


# Re-export types that the Facade re-exposes (kept here for now so the
# Facade in bot/action_pipeline.py stays a true thin shell).
class PipelinePhase(IntEnum):
    """Observability for the action pipeline progress."""
    PENDING            = 0
    INTERPRETING       = 1
    RESOLVING_ENTITIES = 2
    VALIDATING         = 3
    RESOLVING_ACTION   = 4
    ASSEMBLING_CONTEXT = 5
    NARRATING          = 6
    DONE               = 7
    FAILED             = 8


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
    is_free_action: bool = False


class AmbiguityResult(BaseModel):
    field_name: Literal["target_name", "item_name"]
    raw_value: str
    candidates: list[EntityCandidate] = Field(default_factory=list)
    partial_action: InterpretedAction
    model_config = {"arbitrary_types_allowed": True}


class UnknownEntityResult(BaseModel):
    field_name: str
    raw_value: str
    partial_action: InterpretedAction
    refusal_narrative: str
    tone: Literal["dramatic", "tense", "humorous", "somber"] = "somber"


PipelineOutput = ActionPipelineResult | AmbiguityResult | UnknownEntityResult
ProgressCallback = Callable[[PipelinePhase], Awaitable[None]]


@dataclass
class PipelineRunner:
    """Per-action runner. Holds the per-action state previously kept on
    ``ActionPipeline`` and chains the three stage modules.
    """

    interpreter: Interpreter
    narrator: Narrator
    location: Location | None
    npcs: dict[str, NPC]
    actor_name: str
    language: str = "fr"
    campaign_id: str = ""
    combat_state: CombatState | None = None
    inventory: Inventory | None = None
    session: "GameSession | None" = None
    db_factory: Callable[[], Any] | None = None

    # Side-channel state (kept for backward-compatibility access by callers
    # that previously read these attributes off ActionPipeline).
    _pending_flee_destination: str | None = None
    _pending_combat_start_embed: Any = None
    _pending_dice_embeds: list[Any] = field(default_factory=list)
    _trivial_kill_mechanics: str | None = None

    async def process(
        self, player_text: str, progress_callback: ProgressCallback | None = None
    ) -> PipelineOutput:
        """[paste body from bot/action_pipeline.py:264–~281, calling
        interpret.call_interpreter via wrappers]"""

    async def resume_with_resolution(
        self, ambiguity: AmbiguityResult, chosen_entity_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """[paste body from bot/action_pipeline.py:282–~299]"""

    async def process_interpreted_action(
        self, action: InterpretedAction, progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """[paste body from bot/action_pipeline.py:300–~318]"""

    async def _continue_from_resolution(
        self, interpreted: InterpretedAction,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        """The full Phase 2-6 pipeline body.

        [paste body from bot/action_pipeline.py:319–~578]
        Replace ``self._call_interpreter`` etc. with the new module functions:
          - interpret.call_interpreter / auto_resolve_weapon_name / validate
          - resolve.resolve_mechanics
          - narrate.assemble_context / call_narrator / narrate_unknown / narrate_rule_failure
        For side-channel reads/writes use the runner's own ``_pending_*`` attrs.
        """

    def _check_beat_completion(self, action, outcome) -> StoryBeat | None:
        """[paste from bot/action_pipeline.py:1276–~1355]"""

    def _apply_beat_effects(self, effects: BeatEffects) -> None:
        """[paste from bot/action_pipeline.py:1356–~1380]"""

    async def _llm_beat_fallback(self, action, beat, outcome) -> bool:
        """[paste from bot/action_pipeline.py:1381–~1426]"""

    async def _emit(self, callback: ProgressCallback | None, phase: PipelinePhase) -> None:
        """[paste from bot/action_pipeline.py:1884–~1898]"""
```

When pasting `_continue_from_resolution`, replace each call:
- `await self._call_interpreter(...)` → `await interpret.call_interpreter(interpreter=self.interpreter, ...)`
- `self._auto_resolve_weapon_name(...)` → `interpret.auto_resolve_weapon_name(...)`
- `self._validate(action)` → call helper that wires `InterpretSideChannel` (see A6 wrapper pattern)
- `await self._resolve_mechanics(action)` → call helper that wires `ResolveSideChannel` (see A5 pattern)
- `self._assemble_context(...)` → `narrate.assemble_context(...)`
- `await self._call_narrator(...)` → `await narrate.call_narrator(...)`
- `await self._narrate_unknown(...)` → `await narrate.narrate_unknown(...)`
- `await self._narrate_rule_failure(...)` → `await narrate.narrate_rule_failure(...)`

- [ ] **Step 3: Collapse `bot/action_pipeline.py` into a Facade**

Replace the entire content of `bot/action_pipeline.py` with:

```python
"""Backward-compatible Facade for the action pipeline.

The actual implementation lives in :mod:`bot.pipeline`. This module exists
to preserve imports of the form ``from bot.action_pipeline import ActionPipeline``
that exist throughout the codebase (cogs, views, tests).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from bot.pipeline.orchestrator import (
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    PipelineRunner,
    ProgressCallback,
    UnknownEntityResult,
)
# Re-export module-level helpers some callers still import from here.
from bot.pipeline.resolve import (
    is_trivially_defeatable,
)

if TYPE_CHECKING:
    from ai.interpreter import Interpreter
    from ai.models import InterpretedAction
    from ai.narrator import Narrator
    from engine.combat import CombatState
    from engine.inventory import Inventory
    from world.location import Location
    from world.npc import NPC

    from bot.game_session import GameSession


__all__ = [
    "ActionPipeline",
    "ActionPipelineResult",
    "AmbiguityResult",
    "PipelineOutput",
    "PipelinePhase",
    "ProgressCallback",
    "UnknownEntityResult",
    "is_trivially_defeatable",
]


class ActionPipeline:
    """Legacy facade — instantiates a ``PipelineRunner`` per action.

    Kept dataclass-like in interface but no longer a dataclass — the
    constructor explicitly mirrors the historical signature.
    """

    def __init__(
        self,
        *,
        interpreter: "Interpreter",
        narrator: "Narrator",
        location: "Location | None",
        npcs: "dict[str, NPC]",
        actor_name: str,
        language: str = "fr",
        campaign_id: str = "",
        combat_state: "CombatState | None" = None,
        inventory: "Inventory | None" = None,
        session: "GameSession | None" = None,
        db_factory: "Callable[[], Any] | None" = None,
    ) -> None:
        self._runner = PipelineRunner(
            interpreter=interpreter,
            narrator=narrator,
            location=location,
            npcs=npcs,
            actor_name=actor_name,
            language=language,
            campaign_id=campaign_id,
            combat_state=combat_state,
            inventory=inventory,
            session=session,
            db_factory=db_factory,
        )

    async def process(
        self, player_text: str, progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        return await self._runner.process(player_text, progress_callback)

    async def resume_with_resolution(
        self, ambiguity: AmbiguityResult, chosen_entity_id: str,
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        return await self._runner.resume_with_resolution(
            ambiguity, chosen_entity_id, progress_callback,
        )

    async def process_interpreted_action(
        self, action: "InterpretedAction",
        progress_callback: ProgressCallback | None = None,
    ) -> PipelineOutput:
        return await self._runner.process_interpreted_action(action, progress_callback)

    # --- Passthrough properties for callers that read side-channel state ---
    @property
    def _pending_combat_start_embed(self) -> Any:
        return self._runner._pending_combat_start_embed

    @_pending_combat_start_embed.setter
    def _pending_combat_start_embed(self, value: Any) -> None:
        self._runner._pending_combat_start_embed = value

    @property
    def _pending_dice_embeds(self) -> list[Any]:
        return self._runner._pending_dice_embeds

    @property
    def _pending_flee_destination(self) -> str | None:
        return self._runner._pending_flee_destination

    @property
    def _trivial_kill_mechanics(self) -> str | None:
        return self._runner._trivial_kill_mechanics

    # --- Passthrough properties for state that callers may read ---
    @property
    def location(self) -> "Location | None":
        return self._runner.location

    @property
    def npcs(self) -> "dict[str, NPC]":
        return self._runner.npcs

    @property
    def session(self) -> "GameSession | None":
        return self._runner.session

    @property
    def combat_state(self) -> "CombatState | None":
        return self._runner.combat_state

    @combat_state.setter
    def combat_state(self, value: "CombatState | None") -> None:
        self._runner.combat_state = value

    @property
    def inventory(self) -> "Inventory | None":
        return self._runner.inventory

    @property
    def actor_name(self) -> str:
        return self._runner.actor_name
```

If the original `ActionPipeline` was instantiated *positionally* anywhere in the codebase (no `**kwargs`), the Facade's `__init__` must accept positional args too. Audit: `grep -rn "ActionPipeline(" --include='*.py'` and confirm callers use keyword args. If not, switch the new `__init__` to accept positional and keyword (current dataclass behavior is keyword-and-positional).

- [ ] **Step 4: Update `bot/pipeline/__init__.py`**

```python
"""Action pipeline package — splits the legacy ActionPipeline into stages."""

from bot.pipeline import interpret, narrate, orchestrator, resolve
from bot.pipeline.orchestrator import (
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    PipelineRunner,
    ProgressCallback,
    UnknownEntityResult,
)
from bot.pipeline.types import PipelineContext, PipelineDeps

__all__ = [
    "ActionPipelineResult",
    "AmbiguityResult",
    "PipelineContext",
    "PipelineDeps",
    "PipelineOutput",
    "PipelinePhase",
    "PipelineRunner",
    "ProgressCallback",
    "UnknownEntityResult",
    "interpret",
    "narrate",
    "orchestrator",
    "resolve",
]
```

- [ ] **Step 5: Add `tests/bot/pipeline/test_pipeline_facade.py`**

This pins the Facade's public surface so future refactors can't quietly break it.

```python
"""Facade contract tests — pin the public surface of bot.action_pipeline."""

import inspect

from bot.action_pipeline import (
    ActionPipeline,
    ActionPipelineResult,
    AmbiguityResult,
    PipelineOutput,
    PipelinePhase,
    UnknownEntityResult,
    is_trivially_defeatable,
)


def test_facade_exports_action_pipeline_class() -> None:
    assert inspect.isclass(ActionPipeline)


def test_facade_exports_result_types() -> None:
    assert inspect.isclass(ActionPipelineResult)
    assert inspect.isclass(AmbiguityResult)
    assert inspect.isclass(UnknownEntityResult)


def test_facade_pipeline_output_alias_is_union() -> None:
    # PipelineOutput is a typing.Union — assert membership.
    members = getattr(PipelineOutput, "__args__", ())
    assert ActionPipelineResult in members
    assert AmbiguityResult in members
    assert UnknownEntityResult in members


def test_facade_phase_enum_has_expected_phases() -> None:
    expected = {"PENDING", "INTERPRETING", "RESOLVING_ENTITIES", "VALIDATING",
                "RESOLVING_ACTION", "ASSEMBLING_CONTEXT", "NARRATING", "DONE", "FAILED"}
    assert {p.name for p in PipelinePhase} >= expected


def test_facade_action_pipeline_has_three_public_methods() -> None:
    method_names = {m for m in dir(ActionPipeline) if not m.startswith("_")}
    assert {"process", "resume_with_resolution", "process_interpreted_action"} <= method_names


def test_facade_is_trivially_defeatable_callable() -> None:
    assert callable(is_trivially_defeatable)


def test_facade_action_pipeline_constructor_accepts_legacy_kwargs() -> None:
    """The Facade must accept the same kwargs the dataclass version did."""
    sig = inspect.signature(ActionPipeline)
    params = sig.parameters
    expected = {"interpreter", "narrator", "location", "npcs", "actor_name",
                "language", "campaign_id", "combat_state", "inventory",
                "session", "db_factory"}
    assert expected <= set(params)
```

- [ ] **Step 6: Run the new Facade tests**

```bash
uv run pytest tests/bot/pipeline/test_pipeline_facade.py -v
```

Expected: 7 passed.

- [ ] **Step 7: Run the full pipeline test surface**

```bash
uv run pytest tests/bot/test_action_pipeline.py tests/bot/test_action_pipeline_dialogue.py tests/bot/test_action_pipeline_interaction.py tests/bot/test_action_handler_cog.py tests/bot/test_action_handler_resilience.py -q
```

Expected: same pass count as A0 baseline.

- [ ] **Step 8: Run scenarios**

```bash
uv run pytest tests/scenarios/ -q
```

Expected: same as A0.

- [ ] **Step 9: Full suite**

```bash
uv run pytest -q
```

Expected: same as A0.

- [ ] **Step 10: Lint + type**

```bash
uv run ruff check .
uv run mypy bot/pipeline/ bot/action_pipeline.py
```

- [ ] **Step 11: Live Discord scenario test (the gate)**

The discord-test MCP exists in this project (`mcp__discord-test__*`). Run a smoke test:

```bash
# 1. Confirm test bot is online
# (use mcp__discord-test__discord_status via the agent harness)

# 2. Manually start a campaign in the test guild and play 5 actions:
#    - One exploration action ("Je fouille la pièce.")
#    - One movement ("Je sors par la porte est.")
#    - One dialogue ("Je salue le garde.")
#    - One attack-attempt to confirm combat bootstrap
#    - One question ("Que vois-je ici ?")
```

Expected: the bot processes each, returns narrative + footers as before. Visually identical to pre-refactor behavior.

If the live test fails, revert the orchestrator commit (`git revert HEAD`) and investigate before retrying.

- [ ] **Step 12: Commit**

```bash
git add bot/pipeline/orchestrator.py bot/pipeline/__init__.py bot/action_pipeline.py tests/bot/pipeline/test_pipeline_facade.py
git commit -m "refactor(pipeline): collapse ActionPipeline into Facade — orchestrator now owns flow"
```

---

## Self-Review Checklist (run after the plan is written)

1. **Spec coverage:** Section 1 of the spec (Pipeline Split) and Section 6 (Narrator Fallback) are both covered. Sections 2-5 (Narrator contract, Story Director, RAG, Arc Tracker) are explicitly out of scope for Phase A — they're Phase B-D plans.

2. **Placeholder scan:** none. Each `[paste body from ...]` is a directive backed by exact line numbers and explicit substitution rules — not a "TBD" handoff.

3. **Type consistency:**
   - `PipelineContext` defined in A3, referenced everywhere consistently
   - `ResolveSideChannel` defined in A5, used in A5 wrappers
   - `InterpretSideChannel` defined in A6, used in A6 wrappers
   - `PipelineRunner` defined in A7 with the same constructor surface as the legacy `ActionPipeline`

4. **Facade compatibility:** A7's Facade exposes the same `ActionPipeline` constructor kwargs and the same 3 public methods. Side-channel attributes (`_pending_*`, `_trivial_kill_mechanics`) are exposed as properties for backward-compatible reads.

5. **Test discipline:** every new public function has a unit test. Refactor tasks (A4-A7) verify the existing suite stays green at multiple checkpoints. Phase A ends with a live Discord smoke test as the final gate.

---

## Out of Scope (Phase A)

These are explicitly deferred to Phases B-D, per the spec:

- Narrator structured contract (`scene_goal_touched`, `beat_advanced`, `npcs_mentioned`, `locked_facts_used`) — **Phase B**
- Story Director cadence + `StoryDirection` model + `/story_catch_up` — **Phase B**
- RAG densification (`SemanticIndexer`, populating WORLD_LORE/NPC_SHEET/LOCATION_DETAIL/QUEST_DETAIL) — **Phase C**
- Arc Tracker pinned message + `Campaign.arc_tracker_message_id` — **Phase D**

A separate plan file (`2026-04-20-directors-cut-phase-b.md`, etc.) will be written once Phase A lands and the team is comfortable with the new package structure.
