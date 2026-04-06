"""Tests for world/story_arc.py — StoryBeat, StoryArc, and advance_beat."""

import pytest
from pydantic import ValidationError

from world.story_arc import StoryArc, StoryBeat, advance_beat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_beat(
    beat_number: int = 1,
    *,
    title: str = "The Call",
    encounter_type: str = "social",
    is_twist: bool = False,
) -> StoryBeat:
    """Helper to build a StoryBeat with sensible defaults."""
    return StoryBeat(
        beat_number=beat_number,
        title=title,
        description=f"Beat {beat_number} description",
        location_hint="Tavern",
        encounter_type=encounter_type,  # type: ignore[arg-type]
        is_twist=is_twist,
    )


def _make_beats(count: int = 10) -> list[StoryBeat]:
    """Return *count* sequential StoryBeats."""
    types = ["social", "combat", "exploration", "puzzle", "boss"]
    return [
        _make_beat(i + 1, encounter_type=types[i % len(types)])
        for i in range(count)
    ]


@pytest.fixture
def sample_arc() -> StoryArc:
    """A valid 10-beat StoryArc."""
    return StoryArc(
        campaign_id="campaign-001",
        theme="Redemption",
        premise="A fallen knight seeks to reclaim honour lost in a forgotten war.",
        beats=_make_beats(10),
        villain_name="Lord Malachar",
        villain_motivation="Eternal dominion over the realm",
    )


# ---------------------------------------------------------------------------
# StoryBeat validation
# ---------------------------------------------------------------------------


class TestStoryBeat:
    """StoryBeat model tests."""

    def test_valid_creation(self) -> None:
        beat = _make_beat()
        assert beat.beat_number == 1
        assert beat.title == "The Call"
        assert beat.encounter_type == "social"
        assert beat.is_twist is False
        assert beat.npc_names == []

    def test_beat_number_min(self) -> None:
        with pytest.raises(ValidationError):
            _make_beat(beat_number=0)

    def test_beat_number_max(self) -> None:
        with pytest.raises(ValidationError):
            _make_beat(beat_number=21)

    def test_beat_number_boundaries_valid(self) -> None:
        assert _make_beat(beat_number=1).beat_number == 1
        assert _make_beat(beat_number=20).beat_number == 20

    def test_title_min_length(self) -> None:
        with pytest.raises(ValidationError):
            _make_beat(title="")

    def test_encounter_type_invalid(self) -> None:
        with pytest.raises(ValidationError):
            StoryBeat(
                beat_number=1,
                title="Bad",
                description="d",
                location_hint="x",
                encounter_type="invalid",  # type: ignore[arg-type]
            )

    def test_all_encounter_types(self) -> None:
        for etype in ("social", "combat", "exploration", "puzzle", "boss"):
            beat = _make_beat(encounter_type=etype)
            assert beat.encounter_type == etype

    def test_npc_names_populated(self) -> None:
        beat = StoryBeat(
            beat_number=1,
            title="Meet the guide",
            description="d",
            location_hint="x",
            npc_names=["Elara", "Brom"],
            encounter_type="social",
        )
        assert beat.npc_names == ["Elara", "Brom"]

    def test_is_twist_flag(self) -> None:
        beat = _make_beat(is_twist=True)
        assert beat.is_twist is True

    def test_model_roundtrip(self) -> None:
        beat = _make_beat()
        data = beat.model_dump()
        restored = StoryBeat.model_validate(data)
        assert restored == beat


# ---------------------------------------------------------------------------
# StoryArc validation
# ---------------------------------------------------------------------------


class TestStoryArc:
    """StoryArc model tests."""

    def test_valid_creation(self, sample_arc: StoryArc) -> None:
        assert sample_arc.campaign_id == "campaign-001"
        assert sample_arc.theme == "Redemption"
        assert len(sample_arc.beats) == 10
        assert sample_arc.villain_name == "Lord Malachar"

    def test_current_beat_index_default(self, sample_arc: StoryArc) -> None:
        assert sample_arc.current_beat_index == 0

    def test_beats_min_length(self) -> None:
        with pytest.raises(ValidationError):
            StoryArc(
                campaign_id="c",
                theme="t",
                premise="A premise that is long enough.",
                beats=_make_beats(7),
                villain_name="v",
                villain_motivation="m",
            )

    def test_beats_max_length(self) -> None:
        with pytest.raises(ValidationError):
            StoryArc(
                campaign_id="c",
                theme="t",
                premise="A premise that is long enough.",
                beats=_make_beats(21),
                villain_name="v",
                villain_motivation="m",
            )

    def test_beats_boundary_8_valid(self) -> None:
        arc = StoryArc(
            campaign_id="c",
            theme="t",
            premise="A premise that is long enough.",
            beats=_make_beats(8),
            villain_name="v",
            villain_motivation="m",
        )
        assert len(arc.beats) == 8

    def test_beats_boundary_20_valid(self) -> None:
        arc = StoryArc(
            campaign_id="c",
            theme="t",
            premise="A premise that is long enough.",
            beats=_make_beats(20),
            villain_name="v",
            villain_motivation="m",
        )
        assert len(arc.beats) == 20

    def test_premise_min_length(self) -> None:
        with pytest.raises(ValidationError):
            StoryArc(
                campaign_id="c",
                theme="t",
                premise="Short",
                beats=_make_beats(10),
                villain_name="v",
                villain_motivation="m",
            )

    def test_current_beat_index_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StoryArc(
                campaign_id="c",
                theme="t",
                premise="A premise that is long enough.",
                beats=_make_beats(10),
                current_beat_index=-1,
                villain_name="v",
                villain_motivation="m",
            )

    def test_model_roundtrip(self, sample_arc: StoryArc) -> None:
        data = sample_arc.model_dump()
        restored = StoryArc.model_validate(data)
        assert restored == sample_arc


# ---------------------------------------------------------------------------
# advance_beat()
# ---------------------------------------------------------------------------


class TestAdvanceBeat:
    """Tests for the advance_beat function."""

    def test_increments_from_zero(self, sample_arc: StoryArc) -> None:
        assert sample_arc.current_beat_index == 0
        advanced = advance_beat(sample_arc)
        assert advanced.current_beat_index == 1

    def test_original_unchanged(self, sample_arc: StoryArc) -> None:
        advance_beat(sample_arc)
        assert sample_arc.current_beat_index == 0

    def test_increments_sequentially(self, sample_arc: StoryArc) -> None:
        arc = sample_arc
        for expected in range(1, 10):
            arc = advance_beat(arc)
            assert arc.current_beat_index == expected

    def test_idempotent_at_last_beat(self, sample_arc: StoryArc) -> None:
        arc = sample_arc.model_copy(
            update={"current_beat_index": len(sample_arc.beats) - 1}
        )
        result = advance_beat(arc)
        assert result.current_beat_index == len(sample_arc.beats) - 1
        assert result is arc  # same object returned, not a copy

    def test_idempotent_repeated_at_last_beat(self, sample_arc: StoryArc) -> None:
        arc = sample_arc.model_copy(
            update={"current_beat_index": len(sample_arc.beats) - 1}
        )
        result1 = advance_beat(arc)
        result2 = advance_beat(result1)
        assert result1.current_beat_index == result2.current_beat_index
