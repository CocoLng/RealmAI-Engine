"""Tests for engine/npc_archetypes.py — narrative NPC archetype library.

Spec: 2026-07-20-npc-archetypes-and-quest-retirement-design.md §1 (from
world-generation-variety §3): 20 archetypes, 5 categories × 4, each with
2-3 contradictory traits, one playable narrative hook, one performable
dialogue pattern. Category-balanced draw without replacement.
"""

import random

from engine.npc_archetypes import (
    ARCHETYPES,
    ArchetypeCategory,
    NPCArchetype,
    draw_archetypes,
    format_archetype_context,
)


class TestLibraryContent:
    """The library itself — structure guaranteed by the spec."""

    def test_twenty_archetypes(self) -> None:
        assert len(ARCHETYPES) == 20

    def test_five_categories_of_four(self) -> None:
        by_category: dict[ArchetypeCategory, int] = {}
        for arch in ARCHETYPES:
            by_category[arch.category] = by_category.get(arch.category, 0) + 1
        assert set(by_category) == set(ArchetypeCategory)
        assert all(count == 4 for count in by_category.values())

    def test_ids_are_unique(self) -> None:
        ids = [arch.id for arch in ARCHETYPES]
        assert len(set(ids)) == len(ids)

    def test_labels_are_unique(self) -> None:
        labels = [arch.label for arch in ARCHETYPES]
        assert len(set(labels)) == len(labels)

    def test_each_archetype_has_two_or_three_traits(self) -> None:
        for arch in ARCHETYPES:
            assert 2 <= len(arch.traits) <= 3, arch.id
            assert all(t.strip() for t in arch.traits), arch.id

    def test_hooks_and_patterns_are_substantial(self) -> None:
        """A hook is a playable scene, not a tag — it needs real text."""
        for arch in ARCHETYPES:
            assert len(arch.hook.strip()) >= 20, arch.id
            assert len(arch.dialogue_pattern.strip()) >= 20, arch.id

    def test_hooks_are_unique(self) -> None:
        hooks = [arch.hook for arch in ARCHETYPES]
        assert len(set(hooks)) == len(hooks)


class TestDrawArchetypes:
    """Category-balanced draw without replacement."""

    def test_draw_returns_requested_count(self) -> None:
        assert len(draw_archetypes(3)) == 3

    def test_draw_has_no_duplicate_ids(self) -> None:
        for _ in range(100):
            drawn = draw_archetypes(5)
            ids = [a.id for a in drawn]
            assert len(set(ids)) == len(ids)

    def test_draw_of_five_covers_five_categories(self) -> None:
        """A location's cast never doubles a category while others remain."""
        for _ in range(100):
            drawn = draw_archetypes(5)
            assert len({a.category for a in drawn}) == 5

    def test_draw_of_four_has_four_distinct_categories(self) -> None:
        for _ in range(100):
            drawn = draw_archetypes(4)
            assert len({a.category for a in drawn}) == 4

    def test_exclude_is_respected(self) -> None:
        excluded = {arch.id for arch in ARCHETYPES[:15]}
        for _ in range(50):
            drawn = draw_archetypes(3, exclude=excluded)
            assert all(a.id not in excluded for a in drawn)

    def test_overdraw_recycles_without_crashing(self) -> None:
        """count > pool size must not raise — the pool recycles."""
        drawn = draw_archetypes(25)
        assert len(drawn) == 25
        # The first 20 draws exhaust the pool without duplicates.
        assert len({a.id for a in drawn[:20]}) == 20

    def test_full_exclusion_recycles(self) -> None:
        """Everything excluded → still returns content rather than nothing."""
        all_ids = {arch.id for arch in ARCHETYPES}
        drawn = draw_archetypes(2, exclude=all_ids)
        assert len(drawn) == 2

    def test_zero_count_returns_empty(self) -> None:
        assert draw_archetypes(0) == []

    def test_seeded_rng_is_deterministic(self) -> None:
        a = draw_archetypes(5, rng=random.Random(42))
        b = draw_archetypes(5, rng=random.Random(42))
        assert [x.id for x in a] == [x.id for x in b]

    def test_selection_covers_the_whole_pool(self) -> None:
        """No archetype is unreachable."""
        seen: set[str] = set()
        for _ in range(500):
            seen.update(a.id for a in draw_archetypes(1))
        assert seen == {arch.id for arch in ARCHETYPES}


class TestFormatArchetypeContext:
    """The prompt block handed to the NPC generator."""

    def test_contains_all_authored_content(self) -> None:
        arch = ARCHETYPES[0]
        block = format_archetype_context(arch)
        assert arch.label in block
        assert arch.hook in block
        assert arch.dialogue_pattern in block
        for trait in arch.traits:
            assert trait in block

    def test_block_matches_prompt_vocabulary(self) -> None:
        """system_npc_generator.txt announces these three field names."""
        block = format_archetype_context(ARCHETYPES[0])
        assert "traits" in block.lower()
        assert "hook" in block.lower()
        assert "dialogue pattern" in block.lower()


class TestModelValidation:
    """NPCArchetype is a strict Pydantic model."""

    def test_model_rejects_empty_hook(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            NPCArchetype(
                id="x",
                category=ArchetypeCategory.FOLK,
                label="X",
                traits=["a", "b"],
                hook="",
                dialogue_pattern="parle beaucoup trop vite",
            )
