"""Drift rules (R3.*) — informational alerts on state changes without obvious cause."""

from __future__ import annotations

from typing import Any

from tests.simulation.records import IncoherenceAlert

# Actions that are expected to plausibly cause certain state changes.
_DISPOSITION_CAUSING_ACTIONS = {"talk", "attack", "free_form", "cast_spell"}
_CONDITION_CAUSING_ACTIONS = {"attack", "cast_spell", "use_item", "defend", "free_form"}


def _last_intent_action(history: list[Any]) -> str | None:
    if not history:
        return None
    last = history[-1]
    return last.get("intent_action") if isinstance(last, dict) else None


def check_disposition_silent_change(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R3.disposition_silent_change — NPC.disposition changed but no plausible action."""
    alerts: list[IncoherenceAlert] = []
    last_action = _last_intent_action(history)
    if last_action in _DISPOSITION_CAUSING_ACTIONS:
        return []
    for path, change in diff.items():
        if path.startswith("npc.") and path.endswith(".disposition"):
            alerts.append(
                IncoherenceAlert(
                    severity="drift",
                    category="disposition_silent_change",
                    turn=getattr(state, "current_turn", 0),
                    rule="R3.disposition_silent_change",
                    narration_snippet=narration[:200],
                    expected=(
                        f"{path}: {change[0]} → {change[1]} but last action was "
                        f"'{last_action}' (no plausible cause)"
                    ),
                )
            )
    return alerts



def check_condition_phantom(
    narration: str,
    state: Any,
    diff: dict[str, list[Any]],
    history: list[Any],
) -> list[IncoherenceAlert]:
    """R3.condition_phantom — a condition appeared/disappeared without an action."""
    alerts: list[IncoherenceAlert] = []
    last_action = _last_intent_action(history)
    if last_action in _CONDITION_CAUSING_ACTIONS:
        return []
    for path, change in diff.items():
        if path.endswith(".conditions"):
            alerts.append(
                IncoherenceAlert(
                    severity="drift",
                    category="condition_phantom",
                    turn=getattr(state, "current_turn", 0),
                    rule="R3.condition_phantom",
                    narration_snippet=narration[:200],
                    expected=(
                        f"{path}: {change[0]} → {change[1]} but last action was "
                        f"'{last_action}'"
                    ),
                )
            )
    return alerts
