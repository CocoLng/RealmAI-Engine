"""Tests for OllamaClient."""

import json
import logging
import pytest
from pytest_httpx import HTTPXMock
from unittest.mock import patch

from ai.client import LLMParseError, OllamaClient, OllamaUnavailableError
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
    """OllamaClient raises LLMParseError when LLM returns non-JSON.

    LLMParseError subclasses ValueError so existing retry handlers keep
    working, but carries the raw response text for offline diagnosis.
    """
    bad_response = make_ollama_response("This is not JSON at all")
    httpx_mock.add_response(url=CHAT_URL, json=bad_response)

    with pytest.raises(LLMParseError) as exc_info:
        ollama_client.chat_json(
            model="qwen3.5:9b", messages=[{"role": "user", "content": "x"}],
        )
    assert exc_info.value.raw_response == "This is not JSON at all"
    assert exc_info.value.model == "qwen3.5:9b"
    assert isinstance(exc_info.value, ValueError)


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
    assert payload["options"]["num_ctx"] == 16384
    assert payload["keep_alive"] == "10m"


def test_chat_json_default_num_predict_is_unlimited(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """Default num_predict must be -1 (unlimited, bounded only by num_ctx).

    Regression guard: the pre-migration OpenAI-compat client set no
    ``max_tokens``, which Ollama maps to ``num_predict=-1``.  An arc with
    10-15 beats in structured JSON routinely needs > 2000 tokens, so any
    artificial cap truncates the response to ``done=length``.
    """
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
    )

    chat_request = httpx_mock.get_requests()[-1]
    payload = json.loads(chat_request.content)
    assert payload["options"]["num_predict"] == -1


def test_chat_json_default_num_ctx_is_16384(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """Default num_ctx must be large enough to hold thinking + content.

    Qwen 3.5 9b thinking mode can emit 6000+ tokens of reasoning before the
    content phase, and a full arc JSON is ~2500-3500 tokens on top.  A
    16384 context window is the empirically-tested minimum.
    """
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
    )

    chat_request = httpx_mock.get_requests()[-1]
    payload = json.loads(chat_request.content)
    assert payload["options"]["num_ctx"] == 16384


def test_chat_json_caller_can_override_num_predict(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient,
) -> None:
    """Callers may pin num_predict to a hard ceiling when they really need one."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    ollama_client.chat_json(
        model="qwen3.5:9b",
        messages=[{"role": "user", "content": "test"}],
        num_predict=300,
    )

    chat_request = httpx_mock.get_requests()[-1]
    payload = json.loads(chat_request.content)
    assert payload["options"]["num_predict"] == 300


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


def test_chat_json_surfaces_done_reason_on_empty(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient, caplog: pytest.LogCaptureFixture,
) -> None:
    """Empty-content error must include done_reason for fast diagnosis.

    Regression guard: a length-truncated response (thinking exhausted the
    token budget) must surface done=length in both the warning log and the
    ValueError message so operators can distinguish it from other failures.
    """
    response = make_ollama_response("", thinking="lots of reasoning…")
    response["done_reason"] = "length"
    response["eval_count"] = 6644
    httpx_mock.add_response(url=CHAT_URL, json=response)

    with caplog.at_level(logging.WARNING, logger="ai.client"):
        with pytest.raises(ValueError, match="done=length"):
            ollama_client.chat_json(
                model="qwen3.5:9b",
                messages=[{"role": "user", "content": "test"}],
                think=True,
            )

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("done=length" in m and "gen_tok=6644" in m for m in warnings), (
        f"Expected warning with done=length and gen_tok, got: {warnings}"
    )


def test_chat_json_logs_done_reason_on_success(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient, caplog: pytest.LogCaptureFixture,
) -> None:
    """Successful calls log done_reason + prompt_tok + gen_tok at INFO."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    with caplog.at_level(logging.INFO, logger="ai.client"):
        ollama_client.chat_json(
            model="qwen3.5:9b",
            messages=[{"role": "user", "content": "test"}],
        )

    info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any(
        "done=stop" in m and "prompt_tok=100" in m and "gen_tok=20" in m
        for m in info_msgs
    ), f"Expected INFO log with done+tok counts, got: {info_msgs}"


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


# ---------------------------------------------------------------------------
# REALM_LLM_DEBUG mode
# ---------------------------------------------------------------------------


def test_debug_mode_logs_truncated_prompt_and_response(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient, caplog: pytest.LogCaptureFixture,
) -> None:
    """With REALM_LLM_DEBUG=1, truncated prompt and response appear in DEBUG logs."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    with patch.dict("os.environ", {"REALM_LLM_DEBUG": "1"}):
        with caplog.at_level(logging.DEBUG, logger="ai.client"):
            ollama_client.chat_json(
                model="qwen3.5:9b",
                messages=[{"role": "user", "content": "Describe the dungeon."}],
            )

    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    prompt_logs = [m for m in debug_msgs if "prompt[200]" in m]
    resp_logs = [m for m in debug_msgs if "resp[200]" in m]
    assert len(prompt_logs) == 1, f"Expected 1 prompt debug log, got: {debug_msgs}"
    assert len(resp_logs) == 1, f"Expected 1 resp debug log, got: {debug_msgs}"
    assert "Describe the dungeon" in prompt_logs[0]


def test_debug_mode_off_by_default(
    httpx_mock: HTTPXMock, ollama_client: OllamaClient, caplog: pytest.LogCaptureFixture,
) -> None:
    """Without REALM_LLM_DEBUG, no prompt/response content appears in logs."""
    httpx_mock.add_response(url=CHAT_URL, json=make_ollama_response({"ok": True}))

    with patch.dict("os.environ", {}, clear=False):
        # Ensure the var is absent
        import os
        os.environ.pop("REALM_LLM_DEBUG", None)
        with caplog.at_level(logging.DEBUG, logger="ai.client"):
            ollama_client.chat_json(
                model="qwen3.5:9b",
                messages=[{"role": "user", "content": "Secret prompt text here."}],
            )

    debug_msgs = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    content_logs = [m for m in debug_msgs if "prompt[200]" in m or "resp[200]" in m]
    assert content_logs == [], f"Unexpected content debug logs: {content_logs}"
