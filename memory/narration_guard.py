"""Deterministic post-narration checks (audit H17 + anti-monotony).

Holds per-campaign guard state in a module-level registry — the same
pattern as the pipeline's DriftTracker — because the narration call
site (``narrate.call_narrator``) only knows the campaign_id.

State is refreshed at the end of every turn by
``narrate.update_memory_after_turn``: dead NPCs are registered BEFORE
the next turn runs, so a death narrated on the turn it happens is never
flagged, while a dead NPC speaking on any later turn is.

Detection itself is delegated to the shared rule core
(``memory.coherence_rules``) — this module only owns per-campaign state
and orchestrates the registry into a BLOCK/OBSERVE verdict.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from memory.coherence_rules import (
    RULES,
    CoherenceSnapshot,
    CoherenceViolation,
    RuleMode,
    check_npc_status,
)

_COHERENCE_LOGGER = logging.getLogger("memory.coherence")

REPETITION_MIN_WORDS = 8
"""Contiguous-word overlap threshold — mirrors the simulator's
R2.repetition rule (≥ 8 identical consecutive words)."""

_RECENT_NARRATIONS_KEPT = 5
"""5 narrations gardées pour le noyau (R2.repetition, fenêtre simulateur) ;
``find_repetition`` — le check BLOQUANT historique — ne compare qu'aux 2
dernières, comportement inchangé."""


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
    """Remember a delivered narration (last 5 kept) for monotony checks."""
    if text and text.strip():
        _state(campaign_id).recent_narrations.append(text)


def find_repetition(campaign_id: str, narrative: str) -> str | None:
    """Repeated snippet when ``narrative`` near-verbatim repeats a recent one.

    Returns the longest contiguous overlap (≥ REPETITION_MIN_WORDS words)
    against either of the last 2 recorded narrations, or None when the
    narration is fresh enough. This is the historical BLOCKING check —
    unaffected by the core's wider 5-entry window, which only feeds
    R2.repetition/OBSERVE via ``check_narration``.
    """
    state = _STATES.get(campaign_id)
    if state is None or not narrative or not state.recent_narrations:
        return None
    words = narrative.split()
    for prev in list(state.recent_narrations)[-2:]:
        matcher = SequenceMatcher(a=prev.split(), b=words, autojunk=False)
        match = matcher.find_longest_match()
        if match.size >= REPETITION_MIN_WORDS:
            return " ".join(words[match.b: match.b + match.size])
    return None


def find_dead_npc_violations(
    campaign_id: str,
    *,
    narrative: str,
    npcs_mentioned: list[str],
) -> list[str]:
    """Names of dead NPCs that the narration brings back to life.

    Backed by the shared core rule (R1.npc_status): a violation now
    requires an ACTIVE VERB in the same sentence as the name (or a
    self-reported mention) — mentioning the corpse is legitimate.
    """
    state = _STATES.get(campaign_id)
    if state is None or not state.dead_npcs:
        return []
    violations: list[str] = []
    for name in sorted(state.dead_npcs):
        snap = CoherenceSnapshot(dead_npcs=[name], npcs_mentioned=npcs_mentioned)
        if check_npc_status(narrative, snap):
            violations.append(name)
    return violations


@dataclass
class GuardVerdict:
    """Split of one narration's violations by enforcement mode."""

    blocking: list[CoherenceViolation]
    observed: list[CoherenceViolation]


def check_narration(
    campaign_id: str,
    *,
    narrative: str,
    snapshot: CoherenceSnapshot | None,
    npcs_mentioned: list[str],
) -> GuardVerdict:
    """Run every registered coherence rule against one narration.

    Merges the per-campaign guard state (dead set, recent narrations)
    into the caller-provided snapshot, then splits violations by the
    registry's BLOCK/OBSERVE mode. Observed violations are logged on the
    dedicated ``memory.coherence`` logger — the promotion dataset.
    """
    state = _state(campaign_id)
    base = snapshot if snapshot is not None else CoherenceSnapshot()
    effective = base.model_copy(update={
        "dead_npcs": sorted(set(base.dead_npcs) | state.dead_npcs),
        "recent_narrations": list(state.recent_narrations),
        "npcs_mentioned": list(npcs_mentioned),
    })
    blocking: list[CoherenceViolation] = []
    observed: list[CoherenceViolation] = []
    for rule_fn, mode in RULES.values():
        target = blocking if mode is RuleMode.BLOCK else observed
        target.extend(rule_fn(narrative, effective))
    # Both tiers are logged on the dedicated ``memory.coherence`` logger so
    # the first real sessions produce a complete false-positive audit trail:
    # OBSERVE at info, BLOCK at warning (same structured format, tagged).
    for violation in observed:
        _COHERENCE_LOGGER.info(
            "COHERENCE observe campaign=%s rule=%s expected=%r snippet=%r",
            campaign_id, violation.rule, violation.expected, violation.snippet,
        )
    for violation in blocking:
        _COHERENCE_LOGGER.warning(
            "COHERENCE block campaign=%s rule=%s expected=%r snippet=%r",
            campaign_id, violation.rule, violation.expected, violation.snippet,
        )
    return GuardVerdict(blocking=blocking, observed=observed)
