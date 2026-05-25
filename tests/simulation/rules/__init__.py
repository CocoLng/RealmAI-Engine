"""Exposes the canonical ALL_RULES list — order is checker invocation order."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules.drift import (
    check_condition_phantom,
    check_disposition_silent_change,
    check_quest_silent_progress,
)
from tests.simulation.rules.hard import (
    check_hp_mismatch,
    check_item_use_without_owning,
    check_location_mismatch,
    check_locked_fact_violation,
    check_npc_status,
    check_phantom_npc,
    check_zone_violation,
)
from tests.simulation.rules.soft import (
    check_npc_name_drift,
    check_repetition,
    check_tense_drift,
    check_unknown_proper_noun,
)

Rule = Callable[[str, Any, dict[str, list[Any]], list[Any]], list[IncoherenceAlert]]

ALL_RULES: list[Rule] = [
    # Hard
    check_npc_status,
    check_phantom_npc,
    check_item_use_without_owning,
    check_hp_mismatch,
    check_location_mismatch,
    check_zone_violation,
    check_locked_fact_violation,
    # Soft
    check_repetition,
    check_npc_name_drift,
    check_tense_drift,
    check_unknown_proper_noun,
    # Drift
    check_disposition_silent_change,
    check_quest_silent_progress,
    check_condition_phantom,
]

__all__ = ["ALL_RULES", "Rule"]
