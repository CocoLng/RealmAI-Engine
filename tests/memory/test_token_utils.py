"""Tests for memory/token_utils.py — token estimation and truncation."""

from memory.token_utils import estimate_tokens, truncate_to_tokens


class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 0

    def test_single_word(self) -> None:
        assert estimate_tokens("hello") == 2  # ceil(1 * 1.3) = 2

    def test_ten_words(self) -> None:
        text = "one two three four five six seven eight nine ten"
        assert estimate_tokens(text) == 13

    def test_multiline(self) -> None:
        text = "line one\nline two\nline three"
        assert estimate_tokens(text) == 8


class TestTruncateToTokens:
    def test_within_budget(self) -> None:
        text = "short text"
        assert truncate_to_tokens(text, 100) == text

    def test_exact_budget(self) -> None:
        text = "one two three"
        result = truncate_to_tokens(text, 4)
        assert result == text

    def test_over_budget_truncates(self) -> None:
        text = "one two three four five six seven eight nine ten"
        result = truncate_to_tokens(text, 5)
        assert len(result.split()) < 10
        assert estimate_tokens(result) <= 5

    def test_empty_string(self) -> None:
        assert truncate_to_tokens("", 10) == ""

    def test_zero_budget(self) -> None:
        assert truncate_to_tokens("some text", 0) == ""
