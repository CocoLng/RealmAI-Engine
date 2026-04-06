"""Shared fixtures for ai/ tests."""

import json

import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"
CHAT_URL = f"{OLLAMA_BASE}/api/chat"


def make_ollama_response(
    content: str | dict, thinking: str = "",
) -> dict[str, object]:
    """Build a mock Ollama native API chat response.

    Args:
        content: Response content (dict auto-serialised to JSON string).
        thinking: Optional thinking trace (populated when think=True).
    """
    if isinstance(content, dict):
        content = json.dumps(content)
    msg: dict[str, str] = {"role": "assistant", "content": content}
    if thinking:
        msg["thinking"] = thinking
    return {
        "model": "qwen3.5:9b",
        "message": msg,
        "done": True,
        "total_duration": 1000000000,
        "eval_count": 20,
    }


@pytest.fixture()
def ollama_client(httpx_mock: HTTPXMock) -> OllamaClient:
    """Create an OllamaClient with the /api/tags health check mocked."""
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})
    return OllamaClient()
