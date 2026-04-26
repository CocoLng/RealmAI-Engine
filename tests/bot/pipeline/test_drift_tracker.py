"""Tests for the decision-based DriftTracker."""

from bot.pipeline.drift_tracker import DriftTracker


def test_drift_after_5_consecutive_stay() -> None:
    t = DriftTracker()
    for _ in range(5):
        t.record("c1", decision="STAY")
    assert t.is_drifting("c1") is True


def test_no_drift_with_advance_in_window() -> None:
    t = DriftTracker()
    t.record("c1", decision="STAY")
    t.record("c1", decision="STAY")
    t.record("c1", decision="ADVANCE")
    t.record("c1", decision="STAY")
    t.record("c1", decision="STAY")
    assert t.is_drifting("c1") is False  # ADVANCE breaks the streak


def test_drift_resets_on_advance() -> None:
    t = DriftTracker()
    for _ in range(5):
        t.record("c1", decision="STAY")
    assert t.is_drifting("c1") is True
    t.record("c1", decision="ADVANCE")
    assert t.is_drifting("c1") is False


def test_drift_per_campaign() -> None:
    t = DriftTracker()
    for _ in range(5):
        t.record("c1", decision="STAY")
        t.record("c2", decision="ADVANCE")
    assert t.is_drifting("c1") is True
    assert t.is_drifting("c2") is False


def test_no_drift_with_fewer_than_window_size() -> None:
    t = DriftTracker()
    for _ in range(3):
        t.record("c1", decision="STAY")
    assert t.is_drifting("c1") is False  # window not yet full


def test_initial_state_no_drift() -> None:
    t = DriftTracker()
    assert t.is_drifting("c1") is False


def test_needs_judge_does_not_count_as_stay() -> None:
    t = DriftTracker()
    for _ in range(5):
        t.record("c1", decision="NEEDS_JUDGE")
    assert t.is_drifting("c1") is False  # only STAY triggers drift


def test_reset_clears_history() -> None:
    t = DriftTracker()
    for _ in range(5):
        t.record("c1", decision="STAY")
    assert t.is_drifting("c1") is True
    t.reset("c1")
    assert t.is_drifting("c1") is False
