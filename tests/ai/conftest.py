"""Shared fixtures for ai/ tests."""

import json
import pytest


def make_ollama_response(content: str | dict) -> dict:
    """Build a mock Ollama chat completion response."""
    if isinstance(content, dict):
        content = json.dumps(content)
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }
