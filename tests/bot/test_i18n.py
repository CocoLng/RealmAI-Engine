"""Tests for bot/i18n — translation tables and get_label helper."""

from __future__ import annotations

from engine.character import CharacterClass, Race
from engine.starter_gear import STARTER_KITS


class TestGetLabel:
    """get_label fallback and lookup behaviour."""

    def test_known_language_known_key(self) -> None:
        from bot.i18n import RACE_LABELS, get_label
        assert get_label(RACE_LABELS, "fr", "Human") == "Humain"

    def test_unknown_language_falls_back_to_key(self) -> None:
        from bot.i18n import RACE_LABELS, get_label
        assert get_label(RACE_LABELS, "es", "Human") == "Human"

    def test_unknown_key_falls_back_to_key(self) -> None:
        from bot.i18n import RACE_LABELS, get_label
        assert get_label(RACE_LABELS, "fr", "Dragon") == "Dragon"


class TestRaceLabels:
    def test_all_races_translated(self) -> None:
        from bot.i18n import RACE_LABELS
        fr = RACE_LABELS["fr"]
        for race in Race:
            assert race.value in fr, f"Missing FR translation for Race.{race.name}"


class TestClassLabels:
    def test_all_classes_translated(self) -> None:
        from bot.i18n import CLASS_LABELS
        fr = CLASS_LABELS["fr"]
        for cls in CharacterClass:
            assert cls.value in fr, f"Missing FR translation for CharacterClass.{cls.name}"


class TestKitLabels:
    def test_all_kits_translated(self) -> None:
        from bot.i18n import KIT_LABELS
        fr = KIT_LABELS["fr"]
        for kits in STARTER_KITS.values():
            for kit in kits:
                assert kit.name in fr, f"Missing FR translation for kit '{kit.name}'"
                assert "name" in fr[kit.name], f"Missing 'name' key for kit '{kit.name}'"
                assert "description" in fr[kit.name], f"Missing 'description' key for kit '{kit.name}'"
