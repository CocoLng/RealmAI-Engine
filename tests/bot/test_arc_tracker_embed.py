"""Tests for build_arc_tracker_embed."""

import discord

from bot.embeds.arc_tracker_embed import build_arc_tracker_embed


class TestBuildArcTrackerEmbed:
    def test_returns_discord_embed(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="Chapter 1 — The Beginning",
            current_objective="Find the lost map.",
            recent_beats=["Found a clue.", "Met the elder."],
            active_quests=["Main: Lost Map", "Side: Help Elena"],
            last_updated_relative="il y a 2 minutes",
        )
        assert isinstance(embed, discord.Embed)
        assert embed.title is not None
        assert "Chapter 1" in embed.title

    def test_objective_in_description(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Find the map.",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="now",
        )
        assert embed.description is not None
        assert "Find the map." in embed.description

    def test_recent_beats_field_includes_last_three(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=["A", "B", "C", "D", "E"],
            active_quests=[],
            last_updated_relative="now",
        )
        beat_field = next((f for f in embed.fields if "beat" in f.name.lower()), None)
        assert beat_field is not None
        assert "C" in beat_field.value
        assert "D" in beat_field.value
        assert "E" in beat_field.value
        assert "A" not in beat_field.value
        assert "B" not in beat_field.value

    def test_active_quests_field_includes_top_five(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=[],
            active_quests=[f"Quest {i}" for i in range(7)],
            last_updated_relative="now",
        )
        quest_field = next(
            (f for f in embed.fields if "quête" in f.name.lower() or "quest" in f.name.lower()),
            None,
        )
        assert quest_field is not None
        assert "Quest 0" in quest_field.value
        assert "Quest 4" in quest_field.value
        assert "Quest 5" not in quest_field.value
        assert "Quest 6" not in quest_field.value

    def test_empty_objective_uses_fallback(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="now",
        )
        assert embed.description is not None
        assert len(embed.description) > 0

    def test_footer_shows_last_updated(self) -> None:
        embed = build_arc_tracker_embed(
            chapter_title="X",
            current_objective="Y",
            recent_beats=[],
            active_quests=[],
            last_updated_relative="il y a 4 actions",
        )
        # Footer or a dedicated field
        if embed.footer.text:
            assert "il y a 4 actions" in embed.footer.text
        else:
            updated_field = next(
                (f for f in embed.fields if "mise à jour" in f.name.lower() or "updated" in f.name.lower()),
                None,
            )
            assert updated_field is not None
            assert "il y a 4 actions" in updated_field.value
