"""Token estimation and truncation utilities.

The old heuristic (``words × 1.3``) systematically undercounts on text
containing punctuation, accents, numbers, or short words — qwen3.5
tokenises closer to one token per 3-4 characters for French/English.
The audit on 2026-05-25 flagged the resulting 5-10% undercount as a
silent cause of context-window overflows.

This module now uses the max of two heuristics and biases toward
**over-estimation** so callers stay safely under their token budget.
No external tokenizer dependency: the estimator is a fast Python
function suitable for the hot path of context assembly.
"""

import math
from typing import Literal

# Empirically, qwen3.5 tokenises French/English text at roughly
# one token per 3.5 characters and one token per ~1.5 words. We take
# the max of both to stay conservative.
_CHARS_PER_TOKEN = 3.5
_TOKENS_PER_WORD = 1.5


def estimate_tokens(text: str) -> int:
    """Approximate token count from text, biased toward over-estimation.

    Returns the max of two estimates:
    - character-based (``chars / 3.5``) — catches punctuation, accents, numbers
    - word-based (``words × 1.5``) — catches long words that tokenise into pieces

    Returns 0 for empty/whitespace-only strings.
    """
    if not text or not text.strip():
        return 0
    char_estimate = math.ceil(len(text) / _CHARS_PER_TOKEN)
    word_estimate = math.ceil(len(text.split()) * _TOKENS_PER_WORD)
    return max(char_estimate, word_estimate)


def truncate_to_tokens(
    text: str, max_tokens: int, keep: Literal["start", "end"] = "start"
) -> str:
    """Truncate text at word boundaries to fit within a token budget.

    Returns the original text if it fits, otherwise removes words until
    the estimate is within budget. ``keep`` selects WHICH side survives:
    ``"start"`` (default) drops words from the end; ``"end"`` drops words
    from the start — use it for chronological blocks where the most
    recent content is at the end and must be preserved.
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
        kept = words[:mid] if keep == "start" else words[len(words) - mid:]
        if estimate_tokens(" ".join(kept)) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1

    if lo == 0:
        return ""
    kept = words[:lo] if keep == "start" else words[len(words) - lo:]
    return " ".join(kept)


def truncate_lines_keep_recent(
    text: str, max_tokens: int, header_lines: int = 1
) -> str:
    """Truncate a chronological line block, dropping the OLDEST lines first.

    The first ``header_lines`` lines (e.g. ``[RECENT NARRATIVE]``) are
    always preserved. Content lines are dropped from the top (oldest)
    until the block fits. If even the newest line alone exceeds the
    remaining budget, it is word-truncated keeping its end.
    """
    if max_tokens <= 0:
        return ""
    if not text or not text.strip():
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    lines = text.split("\n")
    header = lines[:header_lines]
    content = lines[header_lines:]

    while content:
        candidate = "\n".join(header + content)
        if estimate_tokens(candidate) <= max_tokens:
            return candidate
        content = content[1:]

    # Nothing fits whole: word-truncate the newest line (keeping its end)
    # into whatever budget remains after the header.
    header_text = "\n".join(header)
    newest = lines[-1] if len(lines) > header_lines else ""
    remaining = max_tokens - estimate_tokens(header_text)
    tail = truncate_to_tokens(newest, remaining, keep="end") if newest else ""
    if tail:
        return f"{header_text}\n{tail}"
    return truncate_to_tokens(header_text, max_tokens)
