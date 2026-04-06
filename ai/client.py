"""OllamaClient — thin wrapper around httpx for local Ollama inference."""

import json
import logging
import time
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


class OllamaUnavailableError(Exception):
    """Raised when the Ollama server is unreachable."""


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
    ) -> None:
        self._base_url = base_url.rstrip("/")
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
        num_predict: int = 300,
        num_ctx: int = 4096,
    ) -> dict[str, Any]:
        """Call the model in JSON mode and return the parsed response dict.

        Args:
            model: Ollama model name (e.g. "qwen3.5:9b").
            messages: OpenAI-format message list.
            temperature: Sampling temperature (0.0-1.0).
            think: Enable Qwen 3.5 thinking mode for deeper reasoning.
            num_predict: Maximum tokens to generate.
            num_ctx: Context window size.

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            OllamaUnavailableError: If the Ollama server is unreachable.
            json.JSONDecodeError: If the model returns non-JSON content.
        """
        # When thinking is enabled, the model uses num_predict tokens for
        # BOTH thinking and content.  Add a budget so the caller's
        # num_predict remains the *content* target.
        effective_num_predict = num_predict + 2048 if think else num_predict

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "format": "json",
            "think": think,
            "stream": False,
            "keep_alive": "10m",
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

        if thinking:
            logger.debug("OLLAMA thinking=%d chars", len(thinking))

        logger.info("OLLAMA model=%s think=%s time=%.1fs", model, think, elapsed)

        if not content.strip():
            logger.warning(
                "OLLAMA empty content model=%s think=%s thinking=%d chars",
                model, think, len(thinking),
            )
            raise ValueError(
                f"LLM returned empty content (model={model}, think={think})"
            )

        return cast(dict, json.loads(content))
