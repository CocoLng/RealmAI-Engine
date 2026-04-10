"""Tests for SkillSelectionView — pure logic, no Discord needed."""

from __future__ import annotations

import pytest

from bot.views.skill_selection_view import (
    SkillSelectionView,
    build_skill_options,
    get_skill_count,
)
from engine.character import CLASS_SKILL_CHOICES, CharacterClass, Skill


# ---------------------------------------------------------------------------
# build_skill_options
# ---------------------------------------------------------------------------


class TestBuildSkillOptions:
    def test_fighter_options(self) -> None:
        options = build_skill_options(CharacterClass.FIGHTER)
        config = CLASS_SKILL_CHOICES[CharacterClass.FIGHTER]
        assert len(options) == len(config.options)
        values = [o.value for o in options]
        for skill in config.options:
            assert skill.value in values

    def test_rogue_options(self) -> None:
        options = build_skill_options(CharacterClass.ROGUE)
        config = CLASS_SKILL_CHOICES[CharacterClass.ROGUE]
        assert len(options) == len(config.options)

    def test_wizard_options(self) -> None:
        options = build_skill_options(CharacterClass.WIZARD)
        config = CLASS_SKILL_CHOICES[CharacterClass.WIZARD]
        assert len(options) == len(config.options)

    def test_all_classes_produce_options(self) -> None:
        for cls in CharacterClass:
            options = build_skill_options(cls)
            assert len(options) > 0

    def test_option_labels_include_ability(self) -> None:
        """Each option label should include the ability abbreviation."""
        options = build_skill_options(CharacterClass.ROGUE)
        for opt in options:
            # Label format: "Stealth (DEX)"
            assert "(" in opt.label
            assert ")" in opt.label


# ---------------------------------------------------------------------------
# get_skill_count
# ---------------------------------------------------------------------------


class TestGetSkillCount:
    def test_fighter_picks_2(self) -> None:
        assert get_skill_count(CharacterClass.FIGHTER) == 2

    def test_rogue_picks_4(self) -> None:
        assert get_skill_count(CharacterClass.ROGUE) == 4

    def test_ranger_picks_3(self) -> None:
        assert get_skill_count(CharacterClass.RANGER) == 3

    def test_wizard_picks_2(self) -> None:
        assert get_skill_count(CharacterClass.WIZARD) == 2

    def test_cleric_picks_2(self) -> None:
        assert get_skill_count(CharacterClass.CLERIC) == 2

    def test_barbarian_picks_2(self) -> None:
        assert get_skill_count(CharacterClass.BARBARIAN) == 2


# ---------------------------------------------------------------------------
# SkillSelectionView state
# ---------------------------------------------------------------------------


class TestSkillSelectionViewState:
    def test_initial_state(self) -> None:
        view = SkillSelectionView(
            char_class=CharacterClass.FIGHTER,
            on_confirmed=_dummy_callback,
        )
        assert view.selected_skills == []
        assert view.required_count == 2

    def test_skill_selection_stores(self) -> None:
        view = SkillSelectionView(
            char_class=CharacterClass.ROGUE,
            on_confirmed=_dummy_callback,
        )
        view.selected_skills = [
            Skill.STEALTH, Skill.ACROBATICS,
            Skill.DECEPTION, Skill.PERCEPTION,
        ]
        assert len(view.selected_skills) == view.required_count

    def test_wrong_count_detected(self) -> None:
        """Selecting wrong number of skills should not match required_count."""
        view = SkillSelectionView(
            char_class=CharacterClass.FIGHTER,
            on_confirmed=_dummy_callback,
        )
        view.selected_skills = [Skill.ATHLETICS]
        assert len(view.selected_skills) != view.required_count

    def test_correct_count_matches(self) -> None:
        view = SkillSelectionView(
            char_class=CharacterClass.RANGER,
            on_confirmed=_dummy_callback,
        )
        view.selected_skills = [Skill.STEALTH, Skill.PERCEPTION, Skill.SURVIVAL]
        assert len(view.selected_skills) == view.required_count


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dummy_callback(
    interaction: object, skills: list[Skill],
) -> None:
    """No-op callback for testing."""
