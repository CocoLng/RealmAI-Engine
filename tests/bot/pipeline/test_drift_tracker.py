"""Unit tests for DriftTracker."""

import pytest

from bot.pipeline.drift_tracker import DriftTracker


@pytest.fixture
def tracker() -> DriftTracker:
    return DriftTracker()


def test_initial_state_no_drift(tracker: DriftTracker) -> None:
    assert tracker.is_drifting("cmp_1") is False


def test_single_stale_record_no_drift(tracker: DriftTracker) -> None:
    tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is False


def test_three_stale_in_three_drifts(tracker: DriftTracker) -> None:
    """3 of last 3 are stale → drift."""
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True


def test_three_stale_in_five_drifts(tracker: DriftTracker) -> None:
    """3 of last 5 are stale → drift."""
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True


def test_two_stale_in_five_no_drift(tracker: DriftTracker) -> None:
    """Only 2 of last 5 are stale → no drift."""
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    tracker.record("cmp_1", beat_advanced=False)
    tracker.record("cmp_1", beat_advanced=True)
    assert tracker.is_drifting("cmp_1") is False


def test_window_only_keeps_last_five(tracker: DriftTracker) -> None:
    """Old stale records age out of the window."""
    for _ in range(5):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True
    for _ in range(5):
        tracker.record("cmp_1", beat_advanced=True)
    assert tracker.is_drifting("cmp_1") is False


def test_campaigns_isolated(tracker: DriftTracker) -> None:
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True
    assert tracker.is_drifting("cmp_2") is False


def test_reset_clears_history(tracker: DriftTracker) -> None:
    for _ in range(3):
        tracker.record("cmp_1", beat_advanced=False)
    assert tracker.is_drifting("cmp_1") is True
    tracker.reset("cmp_1")
    assert tracker.is_drifting("cmp_1") is False
