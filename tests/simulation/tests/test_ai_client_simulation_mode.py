"""Tests for ai.client.OllamaClient.simulation_mode flag.

Verifies the flag is wired correctly and forces temperature=0 when set.
Test by inspecting the actual request body sent to /api/chat.
"""

from __future__ import annotations

import json

from pytest_httpx import HTTPXMock

from ai.client import OllamaClient

OLLAMA_BASE = "http://localhost:11434"
TAGS_URL = f"{OLLAMA_BASE}/api/tags"
CHAT_URL = f"{OLLAMA_BASE}/api/chat"


def _setup_health(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=TAGS_URL, json={"models": []})


def _setup_chat_response(httpx_mock: HTTPXMock) -> None:
    # A minimal valid /api/chat response: streaming Ollama responses often end with done=True.
    httpx_mock.add_response(
        url=CHAT_URL,
        json={"message": {"content": '{"ok": true}'}, "done": True},
    )


def test_simulation_mode_forces_temperature_zero(httpx_mock: HTTPXMock) -> None:
    _setup_health(httpx_mock)
    _setup_chat_response(httpx_mock)

    client = OllamaClient(simulation_mode=True)
    client.chat_json(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.9,  # should be overridden to 0.0
    )

    # Inspect the request that was actually sent
    requests = [r for r in httpx_mock.get_requests() if r.url == CHAT_URL]
    assert len(requests) == 1, f"expected exactly 1 chat request, got {len(requests)}"
    body = json.loads(requests[0].content)
    # Look up where temperature is in the body — typically under "options"
    options = body.get("options") or {}
    assert options.get("temperature") == 0.0, (
        f"expected temperature=0.0, got {options.get('temperature')} (full body: {body})"
    )


def test_default_mode_passes_explicit_temperature(httpx_mock: HTTPXMock) -> None:
    _setup_health(httpx_mock)
    _setup_chat_response(httpx_mock)

    client = OllamaClient(simulation_mode=False)
    client.chat_json(
        model="qwen3.5:4b",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
    )
    requests = [r for r in httpx_mock.get_requests() if r.url == CHAT_URL]
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    options = body.get("options") or {}
    assert options.get("temperature") == 0.7
