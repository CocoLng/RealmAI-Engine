"""Tests for the NPC archetype library."""

from engine.npc_archetypes import (
    CATEGORIES,
    NPCArchetype,
    get_all_archetypes,
    pick_archetype,
    pick_archetypes_for_location,
)


class TestArchetypeData:
    """Validate the archetype definitions themselves."""

    def test_total_count_is_twenty(self) -> None:
        assert len(get_all_archetypes()) == 20

    def test_five_categories(self) -> None:
        assert set(CATEGORIES) == {
            "authority",
            "commerce",
            "knowledge",
            "trouble",
            "commoner",
        }

    def test_four_per_category(self) -> None:
        archetypes = get_all_archetypes()
        for cat in CATEGORIES:
            count = sum(1 for a in archetypes if a.category == cat)
            assert count == 4, f"Category '{cat}' has {count} archetypes, expected 4"

    def test_all_names_unique(self) -> None:
        names = [a.name for a in get_all_archetypes()]
        assert len(names) == len(set(names))

    def test_all_fields_populated(self) -> None:
        for a in get_all_archetypes():
            assert a.name, f"Empty name on archetype {a}"
            assert a.category, f"Empty category on {a.name}"
            assert a.label_fr, f"Empty label_fr on {a.name}"
            assert len(a.contradictory_traits) >= 2, (
                f"{a.name} needs at least 2 traits"
            )
            assert a.narrative_hook, f"Empty narrative_hook on {a.name}"
            assert a.dialogue_pattern, f"Empty dialogue_pattern on {a.name}"

    def test_archetype_is_pydantic_model(self) -> None:
        a = get_all_archetypes()[0]
        assert isinstance(a, NPCArchetype)


class TestPickArchetype:
    """Tests for pick_archetype()."""

    def test_returns_valid_archetype(self) -> None:
        result = pick_archetype()
        assert isinstance(result, NPCArchetype)
        assert result.name

    def test_exclude_is_respected(self) -> None:
        all_names = [a.name for a in get_all_archetypes()]
        # Exclude all but one
        keep = all_names[0]
        exclude = all_names[1:]
        for _ in range(20):
            result = pick_archetype(exclude=exclude)
            assert result.name == keep

    def test_exclude_none_works(self) -> None:
        result = pick_archetype(exclude=None)
        assert isinstance(result, NPCArchetype)

    def test_graceful_fallback_when_all_excluded(self) -> None:
        all_names = [a.name for a in get_all_archetypes()]
        result = pick_archetype(exclude=all_names)
        # Should still return something from the full pool
        assert isinstance(result, NPCArchetype)
        assert result.name in all_names


class TestPickArchetypesForLocation:
    """Tests for pick_archetypes_for_location()."""

    def test_returns_correct_count(self) -> None:
        result = pick_archetypes_for_location(3)
        assert len(result) == 3

    def test_no_duplicates(self) -> None:
        result = pick_archetypes_for_location(5)
        names = [a.name for a in result]
        assert len(names) == len(set(names))

    def test_category_variety_with_three_or_more(self) -> None:
        """When picking >= 3, should have at least 2 different categories."""
        # Run multiple times to account for randomness
        variety_seen = False
        for _ in range(30):
            result = pick_archetypes_for_location(4)
            categories = {a.category for a in result}
            if len(categories) >= 2:
                variety_seen = True
                break
        assert variety_seen, "Expected category variety across 30 attempts"

    def test_respects_exclude_list(self) -> None:
        exclude = ["maire_corrompu", "capitaine_use"]
        for _ in range(10):
            result = pick_archetypes_for_location(5, exclude=exclude)
            names = {a.name for a in result}
            assert not names.intersection(exclude)

    def test_count_larger_than_pool(self) -> None:
        """When count exceeds available archetypes, return all candidates."""
        result = pick_archetypes_for_location(100)
        assert len(result) == 20

    def test_all_excluded_fallback(self) -> None:
        all_names = [a.name for a in get_all_archetypes()]
        result = pick_archetypes_for_location(3, exclude=all_names)
        assert len(result) == 3
