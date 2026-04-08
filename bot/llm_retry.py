"""Shared retry helper for blocking LLM calls.

Wraps a synchronous callable in ``asyncio.to_thread`` and retries on
``OllamaUnavailableError`` or ``ValueError`` (empty / malformed LLM content).
Used by both the campaign launcher (onboarding generation) and the action
pipeline (in-game player actions).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from ai.client import LLMParseError, OllamaUnavailableError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Default retry configuration shared by callers that don't override it.
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0)

# Where raw LLM parse failures get dumped for offline diagnosis.
# Override-able for tests.
NARRATOR_FAILURES_DIR = Path("logs/narrator_failures")

_LABEL_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize_label(label: str) -> str:
    safe = _LABEL_SANITIZE_RE.sub("_", label).strip("_")
    return safe[:80] or "llm"


def _persist_parse_failure(label: str, exc: LLMParseError) -> None:
    """Dump a raw LLM parse failure to ``logs/narrator_failures/`` for diagnosis."""
    try:
        NARRATOR_FAILURES_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = NARRATOR_FAILURES_DIR / f"{ts}_{_sanitize_label(label)}.txt"

        system_msg = ""
        user_msg = ""
        for m in exc.messages:
            role = str(m.get("role", ""))
            content = str(m.get("content", ""))
            if role == "system" and not system_msg:
                system_msg = content
            elif role == "user":
                user_msg = content  # keep the last user message

        body = (
            f"# LLM parse failure\n"
            f"Time: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Label: {label}\n"
            f"Model: {exc.model}\n"
            f"Reason: {exc.reason}\n"
            f"---\n"
            f"SYSTEM PROMPT:\n{system_msg}\n"
            f"---\n"
            f"USER MESSAGE:\n{user_msg}\n"
            f"---\n"
            f"RAW RESPONSE:\n{exc.raw_response}\n"
        )
        path.write_text(body, encoding="utf-8")
        logger.info("LLM parse failure dumped to %s", path)
    except Exception:  # noqa: BLE001 — diagnostics must never break retry flow
        logger.exception("Failed to persist LLM parse failure")


async def retry_llm_call(
    fn: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    delays: tuple[float, ...] = DEFAULT_RETRY_DELAYS,
    on_retry: Callable[[int], Awaitable[None]] | None = None,
    log_label: str = "LLM",
) -> T:
    """Run a blocking LLM call with retry-on-failure.

    Args:
        fn: Synchronous callable that performs the LLM call.
        max_retries: How many extra attempts to make after the initial call.
        delays: Backoff delays in seconds, indexed by attempt number
            (0 = first retry).
        on_retry: Optional async callback invoked once per retry attempt
            (with the 1-indexed attempt number) before sleeping.
        log_label: Short tag included in log messages for traceability.

    Returns:
        The successful return value of ``fn``.

    Raises:
        OllamaUnavailableError | ValueError: The exception from the final
        failed attempt is re-raised.
    """
    last_exc: OllamaUnavailableError | ValueError | None = None
    for attempt in range(1 + max_retries):
        if attempt > 0:
            delay = delays[min(attempt - 1, len(delays) - 1)]
            logger.info(
                "%s retry attempt=%d/%d delay=%.0fs",
                log_label, attempt, max_retries, delay,
            )
            if on_retry is not None:
                await on_retry(attempt)
            await asyncio.sleep(delay)
        try:
            return await asyncio.to_thread(fn)
        except (OllamaUnavailableError, ValueError) as exc:
            last_exc = exc
            logger.warning(
                "%s attempt_failed attempt=%d reason=%s",
                log_label, attempt + 1, exc,
            )
            if isinstance(exc, LLMParseError):
                _persist_parse_failure(log_label, exc)
    assert last_exc is not None  # loop always sets it on failure
    raise last_exc
