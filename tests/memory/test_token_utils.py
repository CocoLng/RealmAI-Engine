"""Tests for memory/token_utils.py — token estimation and truncation.

The estimator is biased toward over-estimation (see token_utils docstring).
These tests pin behavioural properties (bounds, monotonicity, idempotence)
rather than exact counts, so the heuristic can be tuned later without
rewriting the suite.
"""

from memory.token_utils import estimate_tokens, truncate_to_tokens


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
