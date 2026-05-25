"""MockOllamaClient — drop-in replacement for OllamaClient that returns scripted responses.

Used by the simulator's --mock-llm CLI flag to run without an Ollama server.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _looks_like_narrator(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = (m.get("content") or "").lower()
        if "narrator" in content or "narration" in content or "scene" in content:
            return True
    return False


def _looks_like_interpreter(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = (m.get("content") or "").lower()
        if "interpreter" in content or "parse the player" in content or "parse this" in content:
            return True
    return False


def _looks_like_agent(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        content = (m.get("content") or "").lower()
        if "autonomous player" in content or "you play:" in content or "you are an autonomous" in content:
            return True
    return False


class MockOllamaClient:
    """API-compatible mock of ai.client.OllamaClient.

    Returns scripted JSON responses based on heuristic inspection of the prompt.
    Intentionally minimal — just enough to keep the simulator pipeline running.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: float = 120.0,
        simulation_mode: bool = False,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._simulation_mode = simulation_mode
        self._call_count = 0

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """Return a scripted JSON response.

        Inspects the messages to guess role (agent / interpreter / narrator) and
        returns a plausible payload. Caller code that does Pydantic validation
        will see well-formed shapes.
        """
        self._call_count += 1
        if _looks_like_agent(messages):
            return {
                "reasoning": "mock: look around to see what's here",
                "action": "look",
                "args": {},
                "raw_text": None,
            }
        if _looks_like_narrator(messages):
            return {
                "narration": "Vous avancez prudemment dans le décor.",
                "text": "Vous avancez prudemment dans le décor.",
                "content": "Vous avancez prudemment dans le décor.",
            }
        if _looks_like_interpreter(messages):
            return {
                "action": "look",
                "args": {},
                "target": None,
                "raw": "mock interpreter response",
            }
        # Default: minimal JSON with a generic acknowledgement
        return {"ok": True, "mock": True, "call_count": self._call_count}
