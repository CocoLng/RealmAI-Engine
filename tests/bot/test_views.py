"""Tests for bot/views — Discord UI components (sync-only, no event loop).

Note: Combat-related view tests moved to tests/bot/test_combat_action_view.py
as part of task 63's wholesale rewrite of the combat UI (see
bot/views/combat_action_view.py and the three select_view modules).
"""

from __future__ import annotations

import discord
from discord import SelectOption

from bot.views.character_create_view import CharacterCreateView, CharacterNameModal
from bot.views.motivation_view import MotivationView
from bot.views.starter_gear_view import StarterGearView
from engine.character import Alignment, CharacterClass, Race
from engine.starter_gear import STARTER_KITS


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
        assert view.ability_assignments is None
        assert view.skill_proficiencies is None
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

    def test_timeout_is_five_minutes(self) -> None:
        view = CharacterCreateView()
        assert view.timeout == 300.0

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


# ---------------------------------------------------------------------------
# StarterGearView
# ---------------------------------------------------------------------------


class TestStarterGearViewFrench:
    """StarterGearView with language='fr' shows translated button labels."""

    def test_button_label_is_translated(self) -> None:
        from unittest.mock import AsyncMock
        kits = STARTER_KITS[CharacterClass.FIGHTER]
        view = StarterGearView(kits=kits, on_selected=AsyncMock(), language="fr")
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        assert "Épée & Bouclier" in labels

    def test_kit_object_preserved(self) -> None:
        from unittest.mock import AsyncMock
        kits = STARTER_KITS[CharacterClass.FIGHTER]
        view = StarterGearView(kits=kits, on_selected=AsyncMock(), language="fr")
        button = view.children[0]
        assert button.kit.name == "Sword & Shield"  # type: ignore[attr-defined]

    def test_default_language_uses_original_name(self) -> None:
        from unittest.mock import AsyncMock
        kits = STARTER_KITS[CharacterClass.FIGHTER]
        view = StarterGearView(kits=kits, on_selected=AsyncMock())
        labels = [child.label for child in view.children if isinstance(child, discord.ui.Button)]
        assert "Sword & Shield" in labels


# ---------------------------------------------------------------------------
# MotivationView
# ---------------------------------------------------------------------------


class TestMotivationView:
    def test_exposes_four_buttons_one_per_motivation(self) -> None:
        from unittest.mock import AsyncMock
        view = MotivationView(on_selected=AsyncMock(), language="fr")
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        assert len(buttons) == 4

    def test_french_labels_are_localized(self) -> None:
        from unittest.mock import AsyncMock
        view = MotivationView(on_selected=AsyncMock(), language="fr")
        labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        assert "Contrat / Payé" in labels
        assert "Conviction / Foi" in labels

    def test_button_stores_english_key_not_display_label(self) -> None:
        from unittest.mock import AsyncMock
        view = MotivationView(on_selected=AsyncMock(), language="fr")
        keys = [c.key for c in view.children if isinstance(c, discord.ui.Button)]  # type: ignore[attr-defined]
        assert set(keys) == {"Contract", "Personal", "Curiosity", "Conviction"}

    def test_english_fallback_when_language_unknown(self) -> None:
        from unittest.mock import AsyncMock
        view = MotivationView(on_selected=AsyncMock(), language="xx")
        labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
        # Unknown language → falls back to English canonical keys.
        assert "Contract" in labels
