"""Tests for the Arc Recipe Engine."""

import random

import pytest

from engine.arc_recipes import (
    BEAT_SUBTYPES,
    ArcRecipe,
    Archetype,
    BeatType,
    Tone,
    generate_recipe,
)


class TestGenerateRecipeBasics:
    """Basic correctness of generate_recipe."""

    def test_returns_valid_arc_recipe(self) -> None:
        """generate_recipe returns an ArcRecipe instance."""
        recipe = generate_recipe(theme="dark fantasy")
        assert isinstance(recipe, ArcRecipe)

    def test_num_beats_matches_sequences(self) -> None:
        """num_beats == len(beat_sequence) == len(beat_subtypes)."""
        for _ in range(50):
            recipe = generate_recipe(theme="test")
            assert recipe.num_beats == len(recipe.beat_sequence)
            assert recipe.num_beats == len(recipe.beat_subtypes)

    def test_num_beats_in_range(self) -> None:
        """num_beats is between 10 and 15."""
        for _ in range(50):
            recipe = generate_recipe(theme="test")
            assert 10 <= recipe.num_beats <= 15


class TestConstraints:
    """Structural constraints enforced by the recipe."""

    def test_no_three_consecutive_same_type(self) -> None:
        """Never 3+ consecutive beats of the same type."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            seq = recipe.beat_sequence
            for i in range(len(seq) - 2):
                assert not (seq[i] == seq[i + 1] == seq[i + 2] and seq[i] != BeatType.boss), (
                    f"Found 3 consecutive '{seq[i]}' at index {i} in {seq}"
                )

    def test_at_least_one_puzzle(self) -> None:
        """Arc must contain at least 1 puzzle beat."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            puzzle_count = sum(
                1 for b in recipe.beat_sequence if b == BeatType.puzzle
            )
            assert puzzle_count >= 1

    def test_at_least_two_social_beats(self) -> None:
        """Arc must contain at least 2 social beats."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            social_count = sum(
                1 for b in recipe.beat_sequence if b == BeatType.social
            )
            assert social_count >= 2

    def test_last_beat_is_boss(self) -> None:
        """Last beat is always boss."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            assert recipe.beat_sequence[-1] == BeatType.boss


class TestArchetypeSelection:
    """Archetype selection logic."""

    def test_previous_archetype_excluded(self) -> None:
        """previous_archetype is never selected."""
        for archetype in Archetype:
            for _ in range(20):
                recipe = generate_recipe(
                    theme="test",
                    previous_archetype=archetype.value,
                )
                assert recipe.archetype != archetype

    def test_all_archetypes_can_be_generated(self) -> None:
        """Every archetype appears if we generate enough recipes."""
        seen: set[Archetype] = set()
        random.seed(42)
        for _ in range(500):
            recipe = generate_recipe(theme="test")
            seen.add(recipe.archetype)
        assert seen == set(Archetype)


class TestSubtypes:
    """Beat subtype validation."""

    def test_subtypes_valid_for_beat_type(self) -> None:
        """Each subtype belongs to the valid set for its beat type."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            for beat, subtype in zip(
                recipe.beat_sequence, recipe.beat_subtypes, strict=True,
            ):
                assert subtype in BEAT_SUBTYPES[beat], (
                    f"Subtype '{subtype}' not valid for beat type "
                    f"'{beat}'. Valid: {BEAT_SUBTYPES[beat]}"
                )


class TestComplications:
    """Complication selection."""

    def test_has_one_or_two_complications(self) -> None:
        """Recipe has 1 or 2 complications."""
        for _ in range(50):
            recipe = generate_recipe(theme="test")
            assert 1 <= len(recipe.complications) <= 2


class TestTwistPosition:
    """Twist position validity."""

    def test_twist_within_bounds(self) -> None:
        """Twist position is a valid beat index (not the last boss)."""
        for _ in range(100):
            recipe = generate_recipe(theme="test")
            assert 0 <= recipe.twist_position < recipe.num_beats - 1


class TestModelValidation:
    """Pydantic model-level validation."""

    def test_rejects_mismatched_lengths(self) -> None:
        """Reject recipe where beat_sequence length != num_beats."""
        with pytest.raises(ValueError, match="beat_sequence length"):
            ArcRecipe(
                archetype=Archetype.mystery,
                beat_sequence=[BeatType.social, BeatType.boss],
                beat_subtypes=["negotiation", "boss", "riddle"],
                complications=["Fausse piste"],
                tone=Tone.sombre,
                twist_position=0,
                num_beats=3,
            )

    def test_rejects_three_consecutive_same_type(self) -> None:
        """Reject recipe with 3 consecutive beats of the same type."""
        with pytest.raises(ValueError, match="consecutive beats of the same type"):
            ArcRecipe(
                archetype=Archetype.siege,
                beat_sequence=[
                    BeatType.social, BeatType.social,
                    BeatType.puzzle,
                    BeatType.combat, BeatType.combat, BeatType.combat,
                    BeatType.exploration,
                    BeatType.social,
                    BeatType.exploration,
                    BeatType.boss,
                ],
                beat_subtypes=[
                    "negotiation", "deception",
                    "riddle",
                    "ambush", "duel", "chase",
                    "tracking",
                    "ceremony",
                    "navigation",
                    "boss",
                ],
                complications=["Fausse piste"],
                tone=Tone.sombre,
                twist_position=4,
                num_beats=10,
            )

    def test_rejects_three_consecutive_puzzles(self) -> None:
        """Reject recipe with 3 consecutive puzzle beats."""
        with pytest.raises(ValueError, match="consecutive beats of the same type"):
            ArcRecipe(
                archetype=Archetype.mystery,
                beat_sequence=[
                    BeatType.social, BeatType.social,
                    BeatType.puzzle, BeatType.puzzle, BeatType.puzzle,
                    BeatType.combat,
                    BeatType.exploration,
                    BeatType.social,
                    BeatType.exploration,
                    BeatType.boss,
                ],
                beat_subtypes=[
                    "negotiation", "deception",
                    "riddle", "mechanism", "cipher",
                    "ambush",
                    "tracking",
                    "ceremony",
                    "navigation",
                    "boss",
                ],
                complications=["Fausse piste"],
                tone=Tone.sombre,
                twist_position=4,
                num_beats=10,
            )

    def test_rejects_no_puzzle(self) -> None:
        """Reject recipe with zero puzzle beats."""
        with pytest.raises(ValueError, match="at least 1 puzzle"):
            ArcRecipe(
                archetype=Archetype.mystery,
                beat_sequence=[
                    BeatType.social, BeatType.social,
                    BeatType.combat, BeatType.exploration,
                    BeatType.combat, BeatType.social,
                    BeatType.exploration, BeatType.combat,
                    BeatType.exploration, BeatType.boss,
                ],
                beat_subtypes=[
                    "negotiation", "deception",
                    "ambush", "tracking",
                    "duel", "ceremony",
                    "navigation", "chase",
                    "discovery", "boss",
                ],
                complications=["Otage"],
                tone=Tone.tendu,
                twist_position=3,
                num_beats=10,
            )

    def test_rejects_last_beat_not_boss(self) -> None:
        """Reject recipe where last beat is not boss."""
        with pytest.raises(ValueError, match="Last beat must be 'boss'"):
            ArcRecipe(
                archetype=Archetype.mystery,
                beat_sequence=[
                    BeatType.social, BeatType.social,
                    BeatType.puzzle,
                    BeatType.combat, BeatType.exploration,
                    BeatType.social, BeatType.exploration,
                    BeatType.combat, BeatType.exploration,
                    BeatType.social,
                ],
                beat_subtypes=[
                    "negotiation", "deception",
                    "riddle",
                    "ambush", "tracking",
                    "ceremony", "navigation",
                    "duel", "discovery",
                    "interrogation",
                ],
                complications=["Otage"],
                tone=Tone.tendu,
                twist_position=3,
                num_beats=10,
            )
