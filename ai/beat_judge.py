"""BeatJudge — LLM 4b judge for partial-match beat objectives.

Fired by the orchestrator when BeatProgressionEngine returns NEEDS_JUDGE.
Returns a structured JSON verdict; the orchestrator applies the >=0.7
confidence threshold and updates objective states accordingly.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
from engine.beat_progression import JudgeRequest

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    Path(__file__).parent / "prompts" / "system_beat_judge.txt"
).read_text()


class JudgeResponse(BaseModel):
    """Structured output from the BeatJudge LLM."""

    passed: bool
    confidence: float = Field(ge=0.0, le=1.0)
    objectives_satisfied: list[str] = Field(default_factory=list)
    reasoning: str = ""
    suggested_next_action: str | None = None


class BeatJudge:
    """LLM-backed judge for ambiguous beat-objective matches.

    One instance per pipeline run is fine; per-turn cooldown is tracked via
    ``begin_turn(turn_id)``. The judge is stateless across turns.
    """

    MODEL = "qwen3.5:4b"
    TIMEOUT_SECONDS = 5.0

    def __init__(self, client: OllamaClient) -> None:
        self._client = client
        self._current_turn_id: str | None = None
        self._calls_this_turn: int = 0

    def begin_turn(self, *, turn_id: str) -> None:
        """Mark a new player turn — resets the per-turn call counter."""
        self._current_turn_id = turn_id
        self._calls_this_turn = 0

    def evaluate(self, request: JudgeRequest) -> JudgeResponse:
        """Judge whether the action satisfies any partial-match objective.

        Returns a JudgeResponse. Failures (timeout, parse error, hallucinated
        ids) all degrade gracefully to passed=False.
        """
        # Cooldown: at most 1 LLM call per turn.
        if self._calls_this_turn >= 1:
            logger.info(
                "JUDGE skipped (cooldown reached for turn %s)", self._current_turn_id
            )
            return JudgeResponse(
                passed=False,
                confidence=0.0,
                reasoning="judge_cooldown",
            )
        self._calls_this_turn += 1

        valid_ids = {pm.id for pm in request.objectives}
        user_msg = self._format_user_message(request)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        start = time.monotonic()
        try:
            data: dict[str, Any] = self._client.chat_json(
                self.MODEL, messages, temperature=0.3, think=False,
                timeout=self.TIMEOUT_SECONDS,
            )
        except LLMParseError:
            logger.warning("JUDGE parse error for beat=%r", request.beat_title)
            return JudgeResponse(
                passed=False, confidence=0.0, reasoning="judge_error",
            )
        except (TimeoutError, OllamaUnavailableError):
            # httpx timeouts surface as OllamaUnavailableError (ai/client.py
            # converts httpx.TimeoutException); the judge must degrade fast
            # and silently rather than crash the pipeline.
            logger.warning("JUDGE timeout for beat=%r", request.beat_title)
            return JudgeResponse(
                passed=False, confidence=0.0, reasoning="judge_timeout",
            )
        except Exception:
            logger.exception("JUDGE unexpected error for beat=%r", request.beat_title)
            return JudgeResponse(
                passed=False, confidence=0.0, reasoning="judge_error",
            )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Whitelist the objective ids — strip hallucinated ones.
        raw_ids = list(data.get("objectives_satisfied") or [])
        filtered = [oid for oid in raw_ids if oid in valid_ids]
        if len(filtered) != len(raw_ids):
            logger.warning(
                "JUDGE stripped hallucinated ids: %s → %s",
                raw_ids,
                filtered,
            )

        try:
            response = JudgeResponse(
                passed=bool(data.get("passed", False)),
                confidence=float(data.get("confidence", 0.0)),
                objectives_satisfied=filtered,
                reasoning=str(data.get("reasoning", "")),
                suggested_next_action=data.get("suggested_next_action"),
            )
        except (ValueError, TypeError) as exc:
            logger.warning("JUDGE response coercion failed: %s", exc)
            return JudgeResponse(
                passed=False, confidence=0.0, reasoning="judge_error",
            )

        logger.info(
            "JUDGE beat=%r passed=%s confidence=%.2f satisfied=%s latency_ms=%d",
            request.beat_title,
            response.passed,
            response.confidence,
            response.objectives_satisfied,
            elapsed_ms,
        )
        return response

    def _format_user_message(self, request: JudgeRequest) -> str:
        """Format the JudgeRequest into a single user message for the LLM."""
        lines: list[str] = []
        lines.append(f"## Beat: {request.beat_title}")
        lines.append(request.beat_description)
        if request.beat_judge_rubric:
            lines.append(f"\nRubric: {request.beat_judge_rubric}")
        lines.append("\n## Partially matched objectives:")
        for pm in request.objectives:
            gate_note = (
                f" [gate failed: {pm.gate_kind.value}]"
                if pm.gate_failed and pm.gate_kind
                else ""
            )
            lines.append(
                f"- {pm.id} ({pm.kind.value}, target={pm.target}, "
                f"score={pm.match_score:.2f}{gate_note}): {pm.description}"
            )
        lines.append(f"\n## Player action: {request.player_action_text}")
        if request.outcome_summary:
            lines.append(f"## Outcome: {request.outcome_summary}")
        if request.location_name:
            lines.append(f"## Location: {request.location_name}")
        if request.npcs_present:
            lines.append(f"## NPCs present: {', '.join(request.npcs_present)}")
        return "\n".join(lines)
