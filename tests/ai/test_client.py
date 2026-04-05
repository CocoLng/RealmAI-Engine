"""Tests for OllamaClient."""

import json
import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient, OllamaUnavailableError
from tests.ai.conftest import make_ollama_response

OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


def test_chat_json_returns_parsed_dict(httpx_mock: HTTPXMock) -> None:
    """OllamaClient.chat_json() returns a parsed dict from valid JSON response."""
    expected = {"narrative": "The goblin falls.", "tone": "dramatic"}
    httpx_mock.add_response(url=OLLAMA_URL, json=make_ollama_response(expected))

    client = OllamaClient()
    result = client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "Narrate the combat."}],
    )

    assert result == expected


def test_chat_json_raises_on_connection_error(httpx_mock: HTTPXMock) -> None:
    """OllamaClient raises OllamaUnavailableError on connection failure."""
    import httpx
    httpx_mock.add_exception(httpx.ConnectError("Connection refused"))

    client = OllamaClient()
    with pytest.raises(OllamaUnavailableError):
        client.chat_json(model="qwen3.5:4b", messages=[{"role": "user", "content": "hi"}])


def test_chat_json_raises_on_invalid_json(httpx_mock: HTTPXMock) -> None:
    """OllamaClient propagates JSONDecodeError when LLM returns non-JSON."""
    bad_response = make_ollama_response("This is not JSON at all")
    httpx_mock.add_response(url=OLLAMA_URL, json=bad_response)

    client = OllamaClient()
    with pytest.raises(json.JSONDecodeError):
        client.chat_json(model="qwen3.5:9b", messages=[{"role": "user", "content": "x"}])


def test_chat_json_uses_custom_base_url(httpx_mock: HTTPXMock) -> None:
    """OllamaClient respects custom base_url."""
    custom_url = "http://custom-ollama:8080/v1/chat/completions"
    expected = {"key": "value"}
    httpx_mock.add_response(url=custom_url, json=make_ollama_response(expected))

    client = OllamaClient(base_url="http://custom-ollama:8080/v1")
    result = client.chat_json(model="qwen3.5:4b", messages=[{"role": "user", "content": "hi"}])
    assert result == expected
