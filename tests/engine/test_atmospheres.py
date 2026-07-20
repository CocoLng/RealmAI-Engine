"""Tests for engine/atmospheres.py — deterministic atmosphere pool (spec §2.1)."""

from engine.atmospheres import ATMOSPHERES, Atmosphere, pick_atmosphere


class TestAtmospherePool:
    """The pool itself."""

    def test_pool_has_at_least_twelve_options(self) -> None:
        """Spec §2.1 asks for ~12 atmospheres."""
        assert len(ATMOSPHERES) >= 12

    def test_pool_has_no_duplicates(self) -> None:
        assert len(set(ATMOSPHERES)) == len(ATMOSPHERES)

    def test_values_are_french(self) -> None:
        """Values match the French vocabulary listed in the spec."""
        values = {a.value for a in ATMOSPHERES}
        assert {
            "oppressante", "féerique", "délabrée", "vivante",
            "silencieuse", "chaotique", "sacrée", "industrielle",
            "souterraine", "maritime", "aérienne", "volcanique",
        } <= values


class TestPickAtmosphere:
    """Selection logic."""

    def test_returns_a_pool_member(self) -> None:
        assert pick_atmosphere() in ATMOSPHERES

    def test_never_returns_previous(self) -> None:
        """Two identical atmospheres in a row must not happen."""
        for _ in range(200):
            assert pick_atmosphere(previous="oppressante") != Atmosphere.oppressante

    def test_unknown_previous_is_ignored(self) -> None:
        """A stale/foreign value must not break selection."""
        for _ in range(50):
            assert pick_atmosphere(previous="not-an-atmosphere") in ATMOSPHERES

    def test_none_previous_is_accepted(self) -> None:
        assert pick_atmosphere(previous=None) in ATMOSPHERES

    def test_selection_covers_the_whole_pool(self) -> None:
        """No option is unreachable."""
        seen = {pick_atmosphere() for _ in range(2000)}
        assert seen == set(ATMOSPHERES)
