"""DriftTracker — decision-based stagnation detector.

A campaign is "drifting" when ``DRIFT_THRESHOLD`` of the last
``WINDOW_SIZE`` engine decisions are STAY (the beat hasn't moved). When
drift is detected, the Story Director runs on the next turn to reorient
the narrator.

Replaces the legacy narrator-flag-based tracker — that signal was a LLM
opinion, not ground truth. Engine decisions are deterministic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

WINDOW_SIZE = 5
"""Number of recent decisions the tracker considers."""

DRIFT_THRESHOLD = 5
"""Number of STAY decisions in the window that trigger a drift signal.

Set to 5 (== WINDOW_SIZE) so drift fires only on a clean run of stagnation.
"""

Decision = Literal["ADVANCE", "STAY", "NEEDS_JUDGE"]


@dataclass
class DriftTracker:
    """Tracks the last ``WINDOW_SIZE`` engine decisions per campaign."""

    _windows: dict[str, deque[Decision]] = field(default_factory=dict)

    def record(self, campaign_id: str, *, decision: Decision) -> None:
        """Record one engine decision for ``campaign_id``."""
        window = self._windows.setdefault(campaign_id, deque(maxlen=WINDOW_SIZE))
        window.append(decision)

    def is_drifting(self, campaign_id: str) -> bool:
        """Return True when the last WINDOW_SIZE decisions are all STAY."""
        window = self._windows.get(campaign_id)
        if window is None or len(window) < DRIFT_THRESHOLD:
            return False
        stay_streak = sum(1 for d in window if d == "STAY")
        return stay_streak >= DRIFT_THRESHOLD

    def reset(self, campaign_id: str) -> None:
        """Clear the rolling window for ``campaign_id``."""
        self._windows.pop(campaign_id, None)
