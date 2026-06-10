"""Deterministic post-narration checks (audit H17 + anti-monotony).

Holds per-campaign guard state in a module-level registry — the same
pattern as the pipeline's DriftTracker — because the narration call
site (``narrate.call_narrator``) only knows the campaign_id.

State is refreshed at the end of every turn by
``narrate.update_memory_after_turn``: dead NPCs are registered BEFORE
the next turn runs, so a death narrated on the turn it happens is never
flagged, while a dead NPC speaking on any later turn is.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

REPETITION_MIN_WORDS = 8
"""Contiguous-word overlap threshold — mirrors the simulator's
R2.repetition rule (≥ 8 identical consecutive words)."""

_RECENT_NARRATIONS_KEPT = 2


@dataclass
class _GuardState:
    """Per-campaign guard data."""

    dead_npcs: set[str] = field(default_factory=set)
    recent_narrations: deque[str] = field(
        default_factory=lambda: deque(maxlen=_RECENT_NARRATIONS_KEPT),
    )


_STATES: dict[str, _GuardState] = {}


def _state(campaign_id: str) -> _GuardState:
    return _STATES.setdefault(campaign_id, _GuardState())


def reset(campaign_id: str) -> None:
    """Drop all guard state for a campaign (tests, campaign end)."""
    _STATES.pop(campaign_id, None)


def set_dead_npcs(campaign_id: str, names: list[str]) -> None:
    """Replace the set of NPCs known to be dead for this campaign."""
    _state(campaign_id).dead_npcs = set(names)


def record_narration(campaign_id: str, text: str) -> None:
    """Remember a delivered narration (last 2 kept) for monotony checks."""
    if text and text.strip():
        _state(campaign_id).recent_narrations.append(text)


def find_repetition(campaign_id: str, narrative: str) -> str | None:
    """Repeated snippet when ``narrative`` near-verbatim repeats a recent one.

    Returns the longest contiguous overlap (≥ REPETITION_MIN_WORDS words)
    against any of the last 2 recorded narrations, or None when the
    narration is fresh enough.
    """
    state = _STATES.get(campaign_id)
    if state is None or not narrative or not state.recent_narrations:
        return None
    words = narrative.split()
    for prev in state.recent_narrations:
        matcher = SequenceMatcher(a=prev.split(), b=words, autojunk=False)
        match = matcher.find_longest_match()
        if match.size >= REPETITION_MIN_WORDS:
            return " ".join(words[match.b: match.b + match.size])
    return None


def _name_patterns(name: str) -> list[re.Pattern[str]]:
    """Word-boundary patterns for a name and its short form.

    Multi-word names also match their longest word (≥ 4 chars) so
    « Père Aldric » catches a narration that says just « Aldric » —
    mirrors the simulator's canonical-name convention.
    """
    candidates = {name}
    words = [w for w in name.split() if len(w) >= 4]
    if len(name.split()) > 1 and words:
        candidates.add(max(words, key=len))
    return [
        re.compile(rf"\b{re.escape(c)}\b", re.IGNORECASE)
        for c in candidates
    ]


def find_dead_npc_violations(
    campaign_id: str,
    *,
    narrative: str,
    npcs_mentioned: list[str],
) -> list[str]:
    """Names of dead NPCs that the narration brings back to life.

    A violation is a dead NPC whose name appears in the narrative text
    (word-boundary, case-insensitive, short form included) or in the
    narrator's self-reported ``npcs_mentioned``.
    """
    state = _STATES.get(campaign_id)
    if state is None or not state.dead_npcs:
        return []

    mentioned_lower = {m.lower() for m in npcs_mentioned}
    violations: list[str] = []
    for name in sorted(state.dead_npcs):
        if name.lower() in mentioned_lower:
            violations.append(name)
            continue
        if any(p.search(narrative) for p in _name_patterns(name)):
            violations.append(name)
    return violations
