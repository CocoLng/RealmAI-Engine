"""IncoherenceChecker — aggregates rule outputs into a single alert list."""

from __future__ import annotations

from typing import Any

from tests.simulation.records import IncoherenceAlert
from tests.simulation.rules import ALL_RULES, Rule


class IncoherenceChecker:
    """Runs each rule in order, returns the combined alerts."""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: list[Rule] = list(rules) if rules is not None else list(ALL_RULES)

    def check(
        self,
        narration: str,
        state: Any,
        diff: dict[str, list[Any]],
        history: list[Any],
    ) -> list[IncoherenceAlert]:
        alerts: list[IncoherenceAlert] = []
        for rule in self._rules:
            alerts.extend(rule(narration, state, diff, history))
        return alerts
