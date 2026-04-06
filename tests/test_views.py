"""Tests for bot/views — Discord UI components (sync-only, no event loop)."""

from __future__ import annotations

from unittest.mock import MagicMock

import discord
from discord import SelectOption

from bot.views.character_create_view import CharacterCreateView, CharacterNameModal
from bot.views.combat_view import CombatView
from bot.views.spell_select import SpellSelectView
from bot.views.target_select import TargetSelectView
from engine.character import Ability, Alignment, CharacterClass, Race
from engine.spells import SpellcasterState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    *, spellcasters: dict[int, SpellcasterState | None] | None = None
) -> MagicMock:
    """Build a minimal mock GameSession for CombatView tests."""
    session = MagicMock()
    session.spellcasters = spellcasters or {}
    return session


# ---------------------------------------------------------------------------
# CombatView
# ---------------------------------------------------------------------------


class TestCombatView:
    """CombatView initialisation and attribute tests."""

    def test_action_starts_none(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=1)
        assert view.action is None

    def test_cast_spell_disabled_without_spellcaster(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=1)
        assert view.cast_spell.disabled is True

    def test_cast_spell_enabled_with_spellcaster(self) -> None:
        state = SpellcasterState(spellcasting_ability=Ability.INT)
        session = _make_session(spellcasters={42: state})
        view = CombatView(session=session, active_user_id=42)
        assert view.cast_spell.disabled is False

    def test_active_user_id_stored(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=99)
        assert view.active_user_id == 99

    def test_timeout_is_five_minutes(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=1)
        assert view.timeout == 300.0

    def test_has_four_buttons(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=1)
        buttons = [
            child
            for child in view.children
            if isinstance(child, discord.ui.Button)
        ]
        assert len(buttons) == 4

    def test_button_labels(self) -> None:
        view = CombatView(session=_make_session(), active_user_id=1)
        labels = {
            child.label
            for child in view.children
            if isinstance(child, discord.ui.Button)
        }
        assert labels == {"Attaquer", "Lancer sort", "Defendre", "Fuir"}


# ---------------------------------------------------------------------------
# TargetSelectView
# ---------------------------------------------------------------------------


class TestTargetSelectView:
    """TargetSelectView initialisation tests."""

    def test_selected_target_starts_none(self) -> None:
        targets = [("Goblin A", "HP 7/7"), ("Goblin B", "HP 5/7")]
        view = TargetSelectView(targets=targets)
        assert view.selected_target is None

    def test_options_populated(self) -> None:
        targets = [("Goblin A", "HP 7/7"), ("Goblin B", "HP 5/7")]
        view = TargetSelectView(targets=targets)
        options: list[SelectOption] = view.select_target.options  # type: ignore[assignment]
        labels = [opt.label for opt in options]
        assert labels == ["Goblin A", "Goblin B"]

    def test_timeout_is_sixty_seconds(self) -> None:
        view = TargetSelectView(targets=[("Wolf", "HP 10/10")])
        assert view.timeout == 60.0


# ---------------------------------------------------------------------------
# SpellSelectView
# ---------------------------------------------------------------------------


class TestSpellSelectView:
    """SpellSelectView initialisation tests."""

    def test_selected_spell_starts_none(self) -> None:
        spells = [("Fireball", "3rd level, 8d6 fire"), ("Shield", "1st level, +5 AC")]
        view = SpellSelectView(spells=spells)
        assert view.selected_spell is None

    def test_options_populated(self) -> None:
        spells = [("Fireball", "3rd level, 8d6 fire"), ("Shield", "1st level, +5 AC")]
        view = SpellSelectView(spells=spells)
        options: list[SelectOption] = view.select_spell.options  # type: ignore[assignment]
        labels = [opt.label for opt in options]
        assert labels == ["Fireball", "Shield"]

    def test_timeout_is_sixty_seconds(self) -> None:
        view = SpellSelectView(spells=[("Magic Missile", "1st level, 3 darts")])
        assert view.timeout == 60.0


# ---------------------------------------------------------------------------
# CharacterCreateView
# ---------------------------------------------------------------------------


class TestCharacterCreateView:
    """CharacterCreateView initialisation and progressive-enable tests."""

    def test_initial_state(self) -> None:
        view = CharacterCreateView()
        assert view.race is None
        assert view.char_class is None
        assert view.alignment is None
        assert view.character_name is None
        assert view.completed is False

    def test_class_select_disabled_initially(self) -> None:
        view = CharacterCreateView()
        assert view.select_class.disabled is True

    def test_alignment_select_disabled_initially(self) -> None:
        view = CharacterCreateView()
        assert view.select_alignment.disabled is True

    def test_race_select_enabled_initially(self) -> None:
        view = CharacterCreateView()
        assert view.select_race.disabled is False

    def test_timeout_is_two_minutes(self) -> None:
        view = CharacterCreateView()
        assert view.timeout == 120.0

    def test_race_option_values_match_enum(self) -> None:
        view = CharacterCreateView()
        options: list[SelectOption] = view.select_race.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {r.value for r in Race}

    def test_class_option_values_match_enum(self) -> None:
        view = CharacterCreateView()
        options: list[SelectOption] = view.select_class.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {c.value for c in CharacterClass}

    def test_alignment_option_values_match_enum(self) -> None:
        view = CharacterCreateView()
        options: list[SelectOption] = view.select_alignment.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {a.value for a in Alignment}


# ---------------------------------------------------------------------------
# CharacterNameModal
# ---------------------------------------------------------------------------


class TestCharacterNameModal:
    """CharacterNameModal initialisation tests."""

    def test_stores_parent_view(self) -> None:
        view = CharacterCreateView()
        modal = CharacterNameModal(parent_view=view)
        assert modal.parent_view is view

    def test_title(self) -> None:
        view = CharacterCreateView()
        modal = CharacterNameModal(parent_view=view)
        assert modal.title == "Nom du personnage"


# ---------------------------------------------------------------------------
# CharacterCreateView — French labels
# ---------------------------------------------------------------------------


class TestCharacterCreateViewFrench:
    """CharacterCreateView with language='fr' shows translated labels."""

    def test_race_labels_are_french(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_race.options  # type: ignore[assignment]
        labels = {opt.label for opt in options}
        assert "Humain" in labels
        assert "Elfe" in labels
        assert "Nain" in labels

    def test_race_values_remain_english(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_race.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {r.value for r in Race}

    def test_class_labels_are_french(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_class.options  # type: ignore[assignment]
        labels = {opt.label for opt in options}
        assert "Guerrier" in labels
        assert "Mage" in labels
        assert "Roublard" in labels

    def test_class_values_remain_english(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_class.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {c.value for c in CharacterClass}

    def test_alignment_labels_are_french(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_alignment.options  # type: ignore[assignment]
        labels = {opt.label for opt in options}
        assert "Loyal Bon" in labels
        assert "Chaotique Mauvais" in labels

    def test_alignment_values_remain_english(self) -> None:
        view = CharacterCreateView(language="fr")
        options: list[SelectOption] = view.select_alignment.options  # type: ignore[assignment]
        values = {opt.value for opt in options}
        assert values == {a.value for a in Alignment}
