"""Tests for memory/token_utils.py — token estimation and truncation.

The estimator is biased toward over-estimation (see token_utils docstring).
These tests pin behavioural properties (bounds, monotonicity, idempotence)
rather than exact counts, so the heuristic can be tuned later without
rewriting the suite.
"""

from memory.token_utils import (
    estimate_tokens,
    truncate_lines_keep_recent,
    truncate_to_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 0

    def test_whitespace_only(self) -> None:
        assert estimate_tokens("   \n\t  ") == 0

    def test_single_word(self) -> None:
        # 5 chars / 3.5 = ceil(1.43) = 2; 1 word × 1.5 = ceil(1.5) = 2
        assert estimate_tokens("hello") == 2

    def test_ten_short_words(self) -> None:
        text = "one two three four five six seven eight nine ten"
        # New (conservative) estimate >= old (1.3×) estimate
        assert estimate_tokens(text) >= 13

    def test_french_with_accents_overestimates(self) -> None:
        """French text with accents tokenises higher than naive word count."""
        text = "Élara dégaine son épée et frappe le gobelin."
        # 9 words × 1.3 = 12 (old); chars/3.5 should give us more.
        assert estimate_tokens(text) >= 13

    def test_monotonic_in_length(self) -> None:
        a = "short"
        b = "short text that is longer than the first"
        assert estimate_tokens(a) < estimate_tokens(b)

    def test_punctuation_increases_estimate(self) -> None:
        plain = "hello world"
        punct = "hello, world! how are you?"
        assert estimate_tokens(punct) > estimate_tokens(plain)


class TestTruncateToTokens:
    def test_within_budget(self) -> None:
        text = "short text"
        assert truncate_to_tokens(text, 100) == text

    def test_over_budget_truncates_to_fit(self) -> None:
        text = "one two three four five six seven eight nine ten"
        result = truncate_to_tokens(text, 5)
        assert len(result.split()) < 10
        assert estimate_tokens(result) <= 5

    def test_empty_string(self) -> None:
        assert truncate_to_tokens("", 10) == ""

    def test_zero_budget(self) -> None:
        assert truncate_to_tokens("some text", 0) == ""

    def test_idempotent_on_fitting_text(self) -> None:
        """Truncating already-fitting text returns it unchanged."""
        text = "one two three"
        budget = estimate_tokens(text)
        assert truncate_to_tokens(text, budget) == text

    def test_keep_end_drops_oldest_words(self) -> None:
        """keep='end' removes words from the START, preserving the tail."""
        text = "oldest old middle recent newest"
        result = truncate_to_tokens(text, 4, keep="end")
        assert result.endswith("newest")
        assert "oldest" not in result
        assert estimate_tokens(result) <= 4

    def test_keep_end_within_budget_unchanged(self) -> None:
        text = "short text"
        assert truncate_to_tokens(text, 100, keep="end") == text

    def test_keep_start_is_default(self) -> None:
        text = "oldest old middle recent newest"
        assert truncate_to_tokens(text, 4) == truncate_to_tokens(text, 4, keep="start")
        assert truncate_to_tokens(text, 4).startswith("oldest")


class TestTruncateLinesKeepRecent:
    """Line-based truncation that drops the OLDEST lines, keeping the header.

    Used by the sliding window and summary renderers: when over budget,
    the most recent exchanges/summaries (at the END of the block) must
    survive, not the oldest ones.
    """

    def _block(self, n: int = 10) -> str:
        lines = ["[RECENT NARRATIVE]"]
        lines += [f"Narrator: exchange number {i} with some additional words" for i in range(1, n + 1)]
        return "\n".join(lines)

    def test_within_budget_unchanged(self) -> None:
        text = self._block(3)
        assert truncate_lines_keep_recent(text, 1000) == text

    def test_over_budget_drops_oldest_lines_first(self) -> None:
        text = self._block(10)
        result = truncate_lines_keep_recent(text, 60)
        assert estimate_tokens(result) <= 60
        assert result.startswith("[RECENT NARRATIVE]")
        assert "exchange number 10" in result
        assert "exchange number 1 " not in result

    def test_keeps_header_line(self) -> None:
        text = self._block(10)
        result = truncate_lines_keep_recent(text, 30)
        assert result.startswith("[RECENT NARRATIVE]")

    def test_zero_budget(self) -> None:
        assert truncate_lines_keep_recent(self._block(3), 0) == ""

    def test_empty_text(self) -> None:
        assert truncate_lines_keep_recent("", 50) == ""

    def test_oversized_newest_line_word_truncated_keeping_end(self) -> None:
        """If even the newest line alone exceeds budget, keep its tail."""
        huge = "word " * 200 + "FINAL"
        text = "[RECENT NARRATIVE]\nNarrator: old line\n" + huge
        result = truncate_lines_keep_recent(text, 40)
        assert estimate_tokens(result) <= 40
        assert "FINAL" in result
