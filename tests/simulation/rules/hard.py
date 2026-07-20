"""Hard incoherence rules (R1.*) — thin adapters over the shared prod core.

The rule logic lives in ``memory/coherence_rules.py`` (chantier « porte de
cohérence »). These wrappers keep the simulator-facing signature
``(narration, state, diff, history)`` and the ``IncoherenceAlert`` output
so the checker, runner and reports stay untouched.
"""

from __future__ import annotations

from typing import Any

from memory.coherence_rules import (
    CoherenceSnapshot,
    CoherenceViolation,
    LockedFactSnapshot,
)
from memory.coherence_rules import check_hp_mismatch as _core_hp_mismatch
from memory.coherence_rules import (
    check_item_use_without_owning as _core_item_use,
)
from memory.coherence_rules import (
    check_location_mismatch as _core_location_mismatch,
)
from memory.coherence_rules import (
    check_locked_fact_violation as _core_locked_fact,
)
from memory.coherence_rules import check_npc_status as _core_npc_status
from memory.coherence_rules import check_phantom_npc as _core_phantom_npc
from memory.coherence_rules import check_zone_violation as _core_zone_violation
from tests.simulation.records import IncoherenceAlert


def snapshot_from_sim(state: Any, history: list[Any]) -> CoherenceSnapshot:
    """Map the simulator's state + history tail onto the neutral snapshot."""
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    inv = getattr(state, "inventory", None)
    combat_state = getattr(state, "combat_state", None)
    facts = [
        LockedFactSnapshot(id=str(f.get("id", "")), text=str(f.get("text", "")))
        for f in (last.get("locked_facts", []) or [])
        if isinstance(f, dict)
    ]
    recent = [
        h.get("narration", "")
        for h in (history[-5:] if history else [])
        if isinstance(h, dict) and h.get("narration")
    ]
    return CoherenceSnapshot(
        dead_npcs=[
            npc.name for npc in state.npcs.values()
            if getattr(npc, "status", None) == "dead" or getattr(npc, "hp", 1) <= 0
        ],
        known_npc_names=list(state.npcs),
        player_names=list(getattr(state, "player_names", [])),
        current_location=getattr(
            getattr(state, "current_location", None), "name", None,
        ),
        known_locations=list(
            last.get("location_known", [])
            or getattr(state, "locations_known", [])
            or []
        ),
        known_factions=list(getattr(state, "factions_known", []) or []),
        moved_this_turn=bool(last.get("moved_this_turn")),
        actor_inventory=list(getattr(inv, "items", [])) if inv is not None else [],
        player_hp_ratio=float(getattr(state, "player_hp_ratio", 1.0)),
        combat_active=bool(getattr(state, "combat_active", False)),
        combat_zones=(
            list(getattr(combat_state, "zones", []))
            if combat_state is not None else []
        ),
        locked_facts=facts,
        recent_narrations=recent,
    )


def _to_alerts(
    violations: list[CoherenceViolation], state: Any, category: str,
) -> list[IncoherenceAlert]:
    return [
        IncoherenceAlert(
            severity=v.severity,
            category=category,
            turn=getattr(state, "current_turn", 0),
            rule=v.rule,
            narration_snippet=v.snippet,
            expected=v.expected,
        )
        for v in violations
    ]


def check_npc_status(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.npc_status — a dead NPC speaks or acts in the narration."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_npc_status(narration, snap), state, "dead_npc_speaks")


def check_item_use_without_owning(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.item_use_without_owning — item used while missing from inventory."""
    if getattr(state, "inventory", None) is None:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_item_use(narration, snap), state, "item_use_without_owning",
    )


def check_hp_mismatch(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.hp_mismatch — wounded prose while player HP ≥ 80 %."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_hp_mismatch(narration, snap), state, "hp_mismatch")


def check_location_mismatch(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.location_mismatch — another known location described as present."""
    if not history:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_location_mismatch(narration, snap), state, "location_mismatch",
    )


def check_phantom_npc(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.phantom_npc — capitalized proper noun absent from known entities."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_phantom_npc(narration, snap), state, "phantom_npc")


def check_locked_fact_violation(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.locked_fact_violation — narration negates a locked world fact."""
    if not history:
        return []
    snap = snapshot_from_sim(state, history)
    return _to_alerts(
        _core_locked_fact(narration, snap), state, "locked_fact_violation",
    )


def check_zone_violation(
    narration: str, state: Any, diff: dict[str, list[Any]], history: list[Any],
) -> list[IncoherenceAlert]:
    """R1.zone_violation — narration references a nonexistent combat zone."""
    snap = snapshot_from_sim(state, history)
    return _to_alerts(_core_zone_violation(narration, snap), state, "zone_violation")
