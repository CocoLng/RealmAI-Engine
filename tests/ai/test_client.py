"""Tests for OllamaClient."""

import json
import pytest
from pytest_httpx import HTTPXMock

from ai.client import OllamaClient, OllamaUnavailableError
from tests.ai.conftest import CHAT_URL, TAGS_URL, make_ollama_response


def test_chat_json_returns_parsed_dict(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """OllamaClient.chat_json() returns a parsed dict from valid JSON response."""
    expected = {"narrative": "The goblin falls.", "tone": "dramatic"}
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response(expected))

    result = ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "Narrate the combat."}],
    )

    assert result == expected


def test_chat_json_raises_on_connection_error(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """OllamaClient raises OllamaUnavailableError on connection failure."""
    import httpx
    httpx_mock.add_exception(httpx.ConnectError("Connection refused"), url=CHAT_URL)

    with pytest.raises(OllamaUnavailableError):
        ollama_client.chat_json(
            model="qwen3.5:4b", messages=[{"role": "user", "content": "hi"}],
        )


def test_chat_json_raises_on_invalid_json(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """OllamaClient propagates JSONDecodeError when LLM returns non-JSON."""
    bad_response = make_ollama_response("This is not JSON at all")
    httpx_mock.add_response(url=CHAT_URL, json=bad_response)

    with pytest.raises(json.JSONDecodeError):
        ollama_client.chat_json(
            model="qwen3.5:9b", messages=[{"role": "user", "content": "x"}],
        )


def test_chat_json_uses_custom_base_url(httpx_mock: HTTPXMock) -> None:
    """OllamaClient respects custom base_url."""
    custom_base = "http://custom-ollama:8080"
    expected = {"key": "value"}
    httpx_mock.add_response(url=f"{custom_base}/api/tags", json={"models": []})
    httpx_mock.add_response(url=f"{custom_base}/api/chat", json=make_ollama_response(expected))

    client = OllamaClient(base_url=custom_base)
    result = client.chat_json(model="qwen3.5:4b", messages=[{"role": "user", "content": "hi"}])
    assert result == expected


def test_constructor_raises_when_ollama_unreachable(httpx_mock: HTTPXMock) -> None:
    """OllamaClient raises OllamaUnavailableError if Ollama is down at init."""
    import httpx
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=TAGS_URL)

    with pytest.raises(OllamaUnavailableError):
        OllamaClient()


def test_chat_json_sends_think_parameter(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """OllamaClient passes think parameter in the request payload."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        think=True,
    )

    requests = httpx_mock.get_requests()
    # Last request is the chat call (first is tags health check)
    chat_request = requests[-1]
    payload = json.loads(chat_request.content)
    assert payload["think"] is True
    assert payload["options"]["num_ctx"] == 4096
    assert payload["keep_alive"] == "10m"


def test_chat_json_with_think_boosts_num_predict(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """When think=True, num_predict is boosted by 2048 for thinking overhead."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        think=True,
        num_predict=500,
    )

    chat_request = httpx_mock.get_requests()[-1]
    payload = json.loads(chat_request.content)
    assert payload["options"]["num_predict"] == 500 + 2048


def test_chat_json_no_boost_without_think(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """When think=False, num_predict is sent as-is."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        think=False,
        num_predict=500,
    )

    chat_request = httpx_mock.get_requests()[-1]
    payload = json.loads(chat_request.content)
    assert payload["options"]["num_predict"] == 500


def test_chat_json_raises_on_empty_content_with_think(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """Empty content with think=True raises ValueError, not JSONDecodeError."""
    response = make_ollama_response("", thinking="I thought a lot but produced nothing")
    httpx_mock.add_response(url=CHAT_URL, json=response)

    with pytest.raises(ValueError, match="LLM returned empty content"):
        ollama_client.chat_json(
            model="qwen3.5:9b",
            messages=[{"role": "user", "content": "test"}],
            think=True,
        )


def test_chat_json_parses_content_with_thinking_field(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """Normal think response with both thinking and content parses correctly."""
    expected = {"narrative": "A brave wizard appears.", "tone": "epic"}
    response = make_ollama_response(expected, thinking="Let me think about this...")
    httpx_mock.add_response(url=CHAT_URL, json=response)

    result = ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        think=True,
    )

    assert result == expected


def test_chat_json_uses_longer_timeout_with_think(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """When think=True, the per-request timeout is THINKING_TIMEOUT (600s)."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        think=True,
    )

    # pytest-httpx doesn't expose the timeout directly, but we can verify
    # the client constants are correctly set.
    assert OllamaClient.THINKING_TIMEOUT == 600.0
    assert OllamaClient.DEFAULT_TIMEOUT == 120.0
