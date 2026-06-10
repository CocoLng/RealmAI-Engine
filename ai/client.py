"""OllamaClient — thin wrapper around httpx for local Ollama inference."""

import json
import logging
import os
import time
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

_THINKING_TOKEN_CAP = 4096
"""Default ``num_predict`` cap when thinking mode is enabled."""

_DEFAULT_KEEP_ALIVE = "10m"

_KEEP_ALIVE_BY_MODEL: dict[str, str] = {
    # The 4b interpreter reloads in seconds — evict it quickly so it never
    # sits resident alongside the 9b narrator (18 GB budget, M8).
    "qwen3.5:4b": "2m",
    "qwen3.5:9b": "10m",
}
"""How long Ollama keeps each model loaded after a request."""


class OllamaUnavailableError(Exception):
    """Raised when the Ollama server is unreachable."""


class LLMParseError(ValueError):
    """Raised when an LLM response cannot be parsed as JSON.

    Carries the raw response text and call metadata so the retry layer can
    persist it for offline diagnosis. Subclasses ``ValueError`` so existing
    ``except ValueError`` retry handlers keep working unchanged.
    """

    def __init__(
        self,
        reason: str,
        *,
        raw_response: str,
        model: str,
        messages: list[dict[str, Any]],
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw_response = raw_response
        self.model = model
        self.messages = messages


class OllamaClient:
    """Shared HTTP client for all Ollama LLM calls.

    Uses the native Ollama API (/api/chat) with JSON mode.
    Supports think=True/False to control Qwen 3.5 thinking mode.
    """

    DEFAULT_URL = "http://localhost:11434"
    DEFAULT_TIMEOUT = 120.0  # seconds
    THINKING_TIMEOUT = 600.0  # seconds — thinking mode needs much longer

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        timeout: float = DEFAULT_TIMEOUT,
        simulation_mode: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._simulation_mode = simulation_mode
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
        )
        # Verify Ollama is reachable
        try:
            self._client.get(f"{self._base_url}/api/tags")
        except httpx.ConnectError as exc:
            raise OllamaUnavailableError(
                f"Cannot connect to Ollama at {self._base_url}"
            ) from exc

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        think: bool = False,
        num_predict: int = -1,
        num_ctx: int = 16384,
        thinking_budget: int | None = None,
        keep_alive: str | None = None,
    ) -> dict[str, Any]:
        """Call the model in JSON mode and return the parsed response dict.

        Args:
            model: Ollama model name (e.g. "qwen3.5:9b").
            messages: OpenAI-format message list.
            temperature: Sampling temperature (0.0-1.0).
            think: Enable Qwen 3.5 thinking mode for deeper reasoning.
                Warning: Qwen 3.5 has no API-level thinking budget cap — on
                some prompts the model enters runaway reasoning.  The only
                safeguard is a large enough ``num_ctx``.
            num_predict: Maximum tokens to generate.  Defaults to ``-1``
                (unlimited, bounded only by ``num_ctx``).  This matches the
                pre-migration OpenAI-compat behaviour where no cap was set.
                Override only if you need a hard ceiling.
            num_ctx: Context window size.  Must be large enough to hold
                prompt + (optional thinking trace) + content JSON.
            thinking_budget: Per-call override for ``_THINKING_TOKEN_CAP``.
                Only used when ``think=True`` and ``num_predict`` is not
                explicitly set.  Allows callers to control thinking budget
                without touching the global default.
            keep_alive: Per-call override for how long Ollama keeps the
                model resident after the request.  Defaults to a per-model
                policy (``_KEEP_ALIVE_BY_MODEL``): short for the 4b so it
                does not stay loaded alongside the 9b — the documented
                budget is 18 GB and the models must not be resident
                simultaneously (M8).

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            OllamaUnavailableError: If the Ollama server is unreachable.
            json.JSONDecodeError: If the model returns non-JSON content.
        """
        if self._simulation_mode:
            temperature = 0.0

        # When thinking is enabled and the caller didn't override
        # num_predict, cap generation to avoid runaway reasoning.
        effective_num_predict = num_predict
        if think and num_predict == -1:
            effective_num_predict = thinking_budget if thinking_budget is not None else _THINKING_TOKEN_CAP

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": "json",
            "think": think,
            "stream": False,
            "keep_alive": keep_alive
            if keep_alive is not None
            else _KEEP_ALIVE_BY_MODEL.get(model, _DEFAULT_KEEP_ALIVE),
            "options": {
                "temperature": temperature,
                "num_predict": effective_num_predict,
                "num_ctx": num_ctx,
            },
        }

        # Thinking mode needs a much longer timeout (model reasons before
        # generating content).  Override per-request instead of raising the
        # global default so non-thinking calls still fail fast.
        request_timeout = (
            httpx.Timeout(self.THINKING_TIMEOUT, connect=10.0)
            if think
            else None  # use client default
        )

        start = time.monotonic()
        try:
            response = self._client.post(
                f"{self._base_url}/api/chat",
                json=payload,
                timeout=request_timeout,
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            logger.error("OLLAMA unreachable at %s", self._base_url)
            raise OllamaUnavailableError(
                f"Cannot connect to Ollama at {self._base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("OLLAMA timeout at %s", self._base_url)
            raise OllamaUnavailableError(
                f"Ollama request timed out at {self._base_url}"
            ) from exc

        elapsed = time.monotonic() - start
        data = response.json()
        msg = data.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")

        # Ollama native API exposes these per response — essential for
        # diagnosing length-limit truncation vs. normal stops.
        done_reason = data.get("done_reason", "unknown")
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)

        if thinking:
            logger.debug("OLLAMA thinking=%d chars", len(thinking))

        logger.info(
            "OLLAMA model=%s think=%s time=%.1fs done=%s prompt_tok=%d gen_tok=%d",
            model, think, elapsed, done_reason, prompt_eval_count, eval_count,
        )

        prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        logger.debug(
            "OLLAMA io model=%s prompt_chars=%d resp_chars=%d",
            model, prompt_chars, len(content),
        )

        if os.getenv("REALM_LLM_DEBUG"):
            prompt_str = " | ".join(str(m.get("content", ""))[:100] for m in messages)
            logger.debug("OLLAMA prompt[200] model=%s: %s…", model, prompt_str[:200])
            logger.debug("OLLAMA resp[200]   model=%s: %s…", model, content[:200])

        if not content.strip():
            logger.warning(
                "OLLAMA empty content model=%s think=%s done=%s gen_tok=%d thinking=%d chars",
                model, think, done_reason, eval_count, len(thinking),
            )
            raise LLMParseError(
                f"LLM returned empty content (model={model}, think={think}, done={done_reason})",
                raw_response=content,
                model=model,
                messages=messages,
            )

        try:
            return cast(dict, json.loads(content))
        except json.JSONDecodeError as exc:
            logger.warning(
                "OLLAMA non-JSON content model=%s done=%s gen_tok=%d reason=%s",
                model, done_reason, eval_count, exc,
            )
            raise LLMParseError(
                f"LLM returned non-JSON content (model={model}, done={done_reason}): {exc}",
                raw_response=content,
                model=model,
                messages=messages,
            ) from exc
