"""Tests for world/combat_zone.py and the Location zone graph validator."""

import pytest
from pydantic import ValidationError

from world.combat_zone import Zone, ZoneTag
from world.location import Location


# ---------------------------------------------------------------------------
# Zone
# ---------------------------------------------------------------------------


class TestZone:
    def test_basic_construction(self) -> None:
        z = Zone(name="Central Plaza")
        assert z.name == "Central Plaza"
        assert z.description == ""
        assert z.adjacent_zone_names == []
        assert z.tags == []

    def test_zone_with_tags(self) -> None:
        z = Zone(
            name="Rooftop",
            tags=[ZoneTag.ELEVATED, ZoneTag.COVER],
        )
        assert z.has_tag(ZoneTag.ELEVATED)
        assert z.has_tag(ZoneTag.COVER)
        assert not z.has_tag(ZoneTag.HAZARD)

    def test_zone_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            Zone(name="")


# ---------------------------------------------------------------------------
# Location.combat_zones validator
# ---------------------------------------------------------------------------


def _make_triangle() -> list[Zone]:
    return [
        Zone(name="A", adjacent_zone_names=["B", "C"]),
        Zone(name="B", adjacent_zone_names=["A", "C"]),
        Zone(name="C", adjacent_zone_names=["A", "B"]),
    ]


class TestLocationZones:
    def test_location_without_zones_still_valid(self) -> None:
        loc = Location(name="Empty Road")
        assert loc.has_combat_zones() is False
        assert loc.combat_zones == []

    def test_location_with_valid_triangle_graph(self) -> None:
        loc = Location(name="Mine Entrance", combat_zones=_make_triangle())
        assert loc.has_combat_zones() is True
        assert loc.get_zone("A") is not None
        assert loc.get_zone("B") is not None
        assert loc.get_zone("ghost") is None

    def test_rejects_unknown_adjacency(self) -> None:
        zones = [
            Zone(name="A", adjacent_zone_names=["B"]),
            Zone(name="B", adjacent_zone_names=["A", "Ghost"]),
        ]
        with pytest.raises(ValidationError):
            Location(name="Broken", combat_zones=zones)

    def test_rejects_asymmetric_adjacency(self) -> None:
        zones = [
            Zone(name="A", adjacent_zone_names=["B"]),
            Zone(name="B", adjacent_zone_names=[]),  # A says B, B doesn't say A
        ]
        with pytest.raises(ValidationError):
            Location(name="Broken", combat_zones=zones)

    def test_rejects_self_adjacency(self) -> None:
        zones = [Zone(name="A", adjacent_zone_names=["A"])]
        with pytest.raises(ValidationError):
            Location(name="Loop", combat_zones=zones)

    def test_rejects_duplicate_zone_names(self) -> None:
        zones = [Zone(name="A"), Zone(name="A")]
        with pytest.raises(ValidationError):
            Location(name="Dupes", combat_zones=zones)

    def test_are_adjacent_positive(self) -> None:
        loc = Location(name="Triangle", combat_zones=_make_triangle())
        assert loc.are_adjacent("A", "B") is True
        assert loc.are_adjacent("B", "A") is True

    def test_are_adjacent_unknown_zone(self) -> None:
        loc = Location(name="Triangle", combat_zones=_make_triangle())
        assert loc.are_adjacent("A", "Ghost") is False
        assert loc.are_adjacent("Ghost", "A") is False

    def test_get_zone_returns_none_if_missing(self) -> None:
        loc = Location(name="Triangle", combat_zones=_make_triangle())
        assert loc.get_zone("nowhere") is None

    def test_isolated_pair_is_valid(self) -> None:
        """Two zones mutually adjacent — simplest valid non-trivial graph."""
        zones = [
            Zone(name="A", adjacent_zone_names=["B"]),
            Zone(name="B", adjacent_zone_names=["A"]),
        ]
        loc = Location(name="Duo", combat_zones=zones)
        assert loc.has_combat_zones()
        assert loc.are_adjacent("A", "B")

    def test_single_zone_is_valid(self) -> None:
        """A single zone with no adjacencies is valid (trivial)."""
        zones = [Zone(name="Only")]
        loc = Location(name="Solo", combat_zones=zones)
        assert loc.has_combat_zones()
        assert loc.get_zone("Only") is not None
