"""DriftTracker — per-campaign rolling window of narrator beat-advancement flags.

A narration is "stale" when ``NarrativeResult.beat_advanced`` is False (the
scene did not move forward). When ``DRIFT_THRESHOLD`` of the last
``WINDOW_SIZE`` narrations are stale, the campaign is "drifting" and the
Story Director should run on the next turn to reorient the narrator.

Implementation note: in-process state, keyed by campaign_id. A campaign's
history persists for the bot process lifetime — fine for the MVP. For
multi-process deployments later, swap the dict for Redis or similar.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

WINDOW_SIZE = 5
"""Number of recent narrations the tracker considers."""

DRIFT_THRESHOLD = 3
"""Number of stale narrations within the window that trigger a drift signal."""


@dataclass
class DriftTracker:
    """Tracks the last ``WINDOW_SIZE`` ``beat_advanced`` flags per campaign.

    Drift fires when at least ``DRIFT_THRESHOLD`` of the recorded flags are
    False (i.e. the scene has not advanced).
    """

    _windows: dict[str, deque[bool]] = field(default_factory=dict)

    def record(self, campaign_id: str, *, beat_advanced: bool) -> None:
        """Record one narration's beat-advanced flag for ``campaign_id``."""
        window = self._windows.setdefault(
            campaign_id, deque(maxlen=WINDOW_SIZE)
        )
        window.append(beat_advanced)

    def is_drifting(self, campaign_id: str) -> bool:
        """Return True when at least DRIFT_THRESHOLD of the last WINDOW_SIZE
        narrations have ``beat_advanced=False``."""
        window = self._windows.get(campaign_id)
        if window is None:
            return False
        stale = sum(1 for advanced in window if not advanced)
        return stale >= DRIFT_THRESHOLD

    def reset(self, campaign_id: str) -> None:
        """Clear the rolling window for ``campaign_id``."""
        self._windows.pop(campaign_id, None)
