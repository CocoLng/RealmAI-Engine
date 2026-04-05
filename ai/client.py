"""OllamaClient — thin wrapper around the OpenAI SDK for local Ollama inference."""

import json
import logging
from typing import Any

import httpx
from openai import APIConnectionError, APITimeoutError, OpenAI

logger = logging.getLogger(__name__)


class OllamaUnavailableError(Exception):
    """Raised when the Ollama server is unreachable."""


class OllamaClient:
    """Shared HTTP client for all Ollama LLM calls.

    Uses the OpenAI-compatible API exposed by Ollama.
    All responses use response_format=json_object — no tool calling.
    """

    DEFAULT_URL = "http://localhost:11434/v1"

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        api_key: str = "ollama",
    ) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Call the model in JSON mode and return the parsed response dict.

        Args:
            model: Ollama model name (e.g. "qwen3.5:9b").
            messages: OpenAI-format message list.
            temperature: Sampling temperature (0.0-1.0).

        Returns:
            Parsed JSON dict from the model response.

        Raises:
            OllamaUnavailableError: If the Ollama server is unreachable.
            json.JSONDecodeError: If the model returns non-JSON content.
        """
        try:
            response = self._client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=temperature,
            )
        except (httpx.ConnectError, APIConnectionError, APITimeoutError) as exc:
            raise OllamaUnavailableError(f"Cannot connect to Ollama at {self._client.base_url}") from exc

        content = response.choices[0].message.content or ""
        return json.loads(content)
