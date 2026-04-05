"""Token estimation and truncation utilities.

Uses a simple word-based approximation: tokens ~ words x 1.3.
No external tokenizer dependency needed.
"""

import math


def estimate_tokens(text: str) -> int:
    """Approximate token count from text.

    Uses the heuristic: tokens ~ words x 1.3 (rounded up).
    Returns 0 for empty strings.
    """
    if not text or not text.strip():
        return 0
    word_count = len(text.split())
    return math.ceil(word_count * 1.3)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text at word boundaries to fit within a token budget.

    Returns the original text if it fits, otherwise removes words
    from the end until the estimate is within budget.
    """
    if max_tokens <= 0:
        return ""
    if not text or not text.strip():
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    words = text.split()
    lo, hi = 0, len(words)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        candidate = " ".join(words[:mid])
        if estimate_tokens(candidate) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1

    if lo == 0:
        return ""
    return " ".join(words[:lo])
