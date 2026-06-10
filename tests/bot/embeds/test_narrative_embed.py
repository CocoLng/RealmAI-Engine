"""Tests for the narrative embed builder — Discord limits (M7)."""

from bot.embeds.narrative_embed import build_narrative_embed

_DISCORD_DESCRIPTION_LIMIT = 4096
_DISCORD_FIELD_LIMIT = 1024


def test_short_narrative_untouched() -> None:
    embed = build_narrative_embed("Le gobelin recule.")
    assert embed.description == "Le gobelin recule."


def test_overlong_narrative_truncated_with_ellipsis() -> None:
    narrative = "phrase assez longue pour déborder " * 200  # ~6800 chars
    embed = build_narrative_embed(narrative)
    assert embed.description is not None
    assert len(embed.description) <= _DISCORD_DESCRIPTION_LIMIT
    assert embed.description.endswith("…")


def test_truncation_cuts_at_word_boundary() -> None:
    narrative = "mot " * 1500  # 6000 chars of clean words
    embed = build_narrative_embed(narrative)
    assert embed.description is not None
    # No half-word before the ellipsis: stripping "…" leaves a full "mot".
    assert embed.description.removesuffix("…").rstrip().endswith("mot")


def test_exactly_at_limit_untouched() -> None:
    narrative = "x" * _DISCORD_DESCRIPTION_LIMIT
    embed = build_narrative_embed(narrative)
    assert embed.description == narrative


def test_overlong_npc_dialogue_field_truncated() -> None:
    dialogue = "Je vais te raconter une très longue histoire. " * 50  # ~2300 chars
    embed = build_narrative_embed(
        "Le vieil homme s'installe près du feu.",
        npc_name="Élie",
        npc_dialogue=dialogue,
    )
    field = embed.fields[0]
    assert field.value is not None
    assert len(field.value) <= _DISCORD_FIELD_LIMIT
    assert "…" in field.value


def test_short_npc_dialogue_untouched() -> None:
    embed = build_narrative_embed(
        "Le vieil homme s'installe.",
        npc_name="Élie",
        npc_dialogue="Bienvenue, voyageur.",
    )
    field = embed.fields[0]
    assert field.value == "*« Bienvenue, voyageur. »*"
