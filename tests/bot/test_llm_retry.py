"""Tests for bot.llm_retry — focused on parse-failure persistence (Lot F)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai.client import LLMParseError, OllamaUnavailableError
from bot import llm_retry
from bot.llm_retry import retry_llm_call


@pytest.fixture
def failures_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "narrator_failures"
    monkeypatch.setattr(llm_retry, "NARRATOR_FAILURES_DIR", target)
    return target


@pytest.mark.asyncio
async def test_parse_failure_is_persisted(failures_dir: Path) -> None:
    """A LLMParseError raised by the wrapped call writes a dump file."""
    raw = "this is not json — model leaked prose"
    messages = [
        {"role": "system", "content": "SYS PROMPT BODY"},
        {"role": "user", "content": "USER MSG BODY"},
    ]

    def boom() -> dict:
        raise LLMParseError(
            "non-JSON",
            raw_response=raw,
            model="qwen3.5:9b",
            messages=messages,
        )

    with pytest.raises(LLMParseError):
        await retry_llm_call(
            boom,
            max_retries=0,
            delays=(0.0,),
            log_label="ACTION test narrate",
        )

    files = list(failures_dir.iterdir())
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert raw in body
    assert "SYS PROMPT BODY" in body
    assert "USER MSG BODY" in body
    assert "qwen3.5:9b" in body
    assert "ACTION test narrate" in body


@pytest.mark.asyncio
async def test_connectivity_error_is_not_persisted(failures_dir: Path) -> None:
    """OllamaUnavailableError must NOT trigger a parse-failure dump."""

    def boom() -> dict:
        raise OllamaUnavailableError("nope")

    with pytest.raises(OllamaUnavailableError):
        await retry_llm_call(boom, max_retries=0, delays=(0.0,), log_label="x")

    assert not failures_dir.exists() or list(failures_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_success_does_not_persist(failures_dir: Path) -> None:
    def ok() -> dict:
        return {"narrative": "fine", "tone": "dramatic"}

    result = await retry_llm_call(ok, max_retries=0, delays=(0.0,), log_label="x")
    assert result == {"narrative": "fine", "tone": "dramatic"}
    assert not failures_dir.exists() or list(failures_dir.iterdir()) == []
