"""Unit tests for bot/embeds/scene_embed.py."""

from __future__ import annotations

from typing import Any

import discord

from bot.embeds.scene_embed import build_scene_embed
from world.location import Location


def _field_by_name(embed: discord.Embed, name_substring: str) -> Any:
    for field in embed.fields:
        if field.name and name_substring in field.name:
            return field
    return None


def _make_location(**overrides: Any) -> Location:
    base: dict[str, Any] = {
        "name": "Le Village",
        "description": "Un hameau brumeux au pied de la colline.",
        "connections": ["forêt", "rivière"],
        "npcs_present": [
            "Jeanne, la Villageoise Terrifiée",
            "Père Thomas, le Moine Loyal",
        ],
        "items_available": ["puits"],
    }
    base.update(overrides)
    return Location(**base)


def test_title_contains_location_name() -> None:
    embed = build_scene_embed(_make_location())
    assert embed.title is not None
    assert "Le Village" in embed.title


def test_description_uses_location_description() -> None:
    embed = build_scene_embed(_make_location())
    assert embed.description == "Un hameau brumeux au pied de la colline."


def test_npcs_field_lists_all_strings() -> None:
    embed = build_scene_embed(_make_location())
    field = _field_by_name(embed, "Personnages")
    assert field is not None
    assert "Jeanne, la Villageoise Terrifiée" in field.value
    assert "Père Thomas, le Moine Loyal" in field.value


def test_npcs_field_truncates_above_five() -> None:
    npcs = [f"PNJ {i}" for i in range(7)]
    embed = build_scene_embed(_make_location(npcs_present=npcs))
    field = _field_by_name(embed, "Personnages")
    assert field is not None
    assert "PNJ 0" in field.value
    assert "PNJ 4" in field.value
    assert "PNJ 5" not in field.value
    assert "2 autre" in field.value


def test_npcs_field_fallback_when_empty() -> None:
    embed = build_scene_embed(_make_location(npcs_present=[]))
    field = _field_by_name(embed, "Personnages")
    assert field is not None
    assert "Aucun" in field.value


def test_exits_field_renders_connections() -> None:
    embed = build_scene_embed(_make_location())
    field = _field_by_name(embed, "Sorties")
    assert field is not None
    assert "forêt" in field.value
    assert "rivière" in field.value


def test_exits_field_fallback_when_empty() -> None:
    embed = build_scene_embed(_make_location(connections=[]))
    field = _field_by_name(embed, "Sorties")
    assert field is not None
    assert "Aucune" in field.value


def test_items_field_omitted_when_empty() -> None:
    embed = build_scene_embed(_make_location(items_available=[]))
    assert _field_by_name(embed, "Objets") is None


def test_npcs_present_override_takes_precedence() -> None:
    embed = build_scene_embed(
        _make_location(npcs_present=["should not appear"]),
        npcs_present=["explicit override"],
    )
    field = _field_by_name(embed, "Personnages")
    assert field is not None
    assert "explicit override" in field.value
    assert "should not appear" not in field.value


def test_language_en_renders_english_labels() -> None:
    embed = build_scene_embed(_make_location(), language="en")
    assert _field_by_name(embed, "Characters") is not None
    assert _field_by_name(embed, "Exits") is not None


def test_emoji_heuristic_matches_forest() -> None:
    embed = build_scene_embed(_make_location(name="Forêt Sombre"))
    assert embed.title is not None
    assert embed.title.startswith("🌲")
