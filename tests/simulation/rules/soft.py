"""Soft incoherence rules (R2.*) — thin adapters over the shared prod core."""

from __future__ import annotations

from typing import Any

from memory.coherence_rules import check_npc_name_drift as _core_name_drift
from memory.coherence_rules import check_repetition as _core_repetition
from memory.coherence_rules import check_tense_drift as _core_tense_drift
from memory.coherence_rules import (
    check_unknown_proper_noun as _core_unknown_noun,
)
from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.hard import _to_alerts, snapshot_from_sim


def check_repetition(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.repetition — ≥ 8 consecutive words shared with the last 5 turns."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_repetition(narration, snap), state, "repetition")


def check_npc_name_drift(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.npc_name_drift — proper noun ≤ 2 edits from a known NPC name."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_name_drift(narration, snap), state, "npc_name_drift")


def check_tense_drift(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.tense_drift — passé composé and present verbs in one sentence."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_tense_drift(narration, snap), state, "tense_drift")


def check_unknown_proper_noun(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R2.unknown_proper_noun — capitalized word matching no known entity."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_unknown_noun(narration, snap), state, "unknown_proper_noun")
