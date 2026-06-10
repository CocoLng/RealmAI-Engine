"""Tests for ai/language — explicit fallback on unknown codes (M10)."""

import logging

import pytest

from ai.language import LANGUAGE_NAMES, language_instruction


@pytest.mark.parametrize("code", list(LANGUAGE_NAMES))
def test_known_codes_emit_no_warning(
    code: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="ai.language"):
        instruction = language_instruction(code)
    assert LANGUAGE_NAMES[code] in instruction
    assert not caplog.records


def test_unknown_code_warns_and_falls_back_to_french(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="ai.language"):
        instruction = language_instruction("xx")
    assert "French" in instruction
    assert any("xx" in r.getMessage() for r in caplog.records)
