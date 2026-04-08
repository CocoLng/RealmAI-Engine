"""Unit tests for GameSession.advance_beat_if_ready (Lot D)."""

from __future__ import annotations

from bot.game_session import GameSession
from world.campaign import Campaign
from world.location import Location
from world.story_arc import StoryArc, StoryBeat


def _make_arc(current_index: int = 0) -> StoryArc:
    beats = [
        StoryBeat(
            beat_number=i + 1,
            title=f"Beat {i + 1}",
            description=f"Description {i + 1}",
            location_hint="Village" if i == 0 else (
                "Donjon de Malphas" if i == 1 else f"Lieu {i + 1}"
            ),
            encounter_type="exploration",
        )
        for i in range(8)
    ]
    return StoryArc(
        campaign_id="c1",
        theme="t",
        premise="A simple premise that is long enough.",
        beats=beats,
        current_beat_index=current_index,
        villain_name="V",
        villain_motivation="m",
    )


def _make_session(arc: StoryArc, location_name: str) -> GameSession:
    return GameSession(
        campaign=Campaign(name="C"),
        current_location=Location(name=location_name),
        story_arc=arc,
    )


def test_advance_beat_when_location_matches() -> None:
    arc = _make_arc(current_index=0)
    session = _make_session(arc, "Donjon de Malphas")

    new_beat = session.advance_beat_if_ready()

    assert new_beat is not None
    assert new_beat.beat_number == 2
    assert session.story_arc.current_beat_index == 1


def test_no_advance_when_location_does_not_match() -> None:
    arc = _make_arc(current_index=0)
    session = _make_session(arc, "Forêt enchantée")

    assert session.advance_beat_if_ready() is None
    assert session.story_arc.current_beat_index == 0


def test_advance_beat_accent_insensitive() -> None:
    arc = _make_arc(current_index=0)
    session = _make_session(arc, "donjon de malphas")  # different case

    assert session.advance_beat_if_ready() is not None
    assert session.story_arc.current_beat_index == 1


def test_no_advance_at_last_beat() -> None:
    arc = _make_arc(current_index=7)  # last beat
    session = _make_session(arc, "Lieu 8")

    assert session.advance_beat_if_ready() is None
    assert session.story_arc.current_beat_index == 7


def test_no_advance_when_no_arc() -> None:
    session = GameSession(
        campaign=Campaign(name="C"),
        current_location=Location(name="X"),
        story_arc=None,
    )
    assert session.advance_beat_if_ready() is None


def test_no_advance_when_no_location() -> None:
    arc = _make_arc(current_index=0)
    session = GameSession(
        campaign=Campaign(name="C"),
        current_location=None,
        story_arc=arc,
    )
    assert session.advance_beat_if_ready() is None
