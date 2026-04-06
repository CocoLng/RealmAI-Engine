"""Narrative embed builder — displays narrator output in a Discord embed."""

import discord


_TONE_COLORS: dict[str, int] = {
    "dramatic": 0xDAA520,
    "tense": 0xCC0000,
    "humorous": 0x339933,
    "somber": 0x663399,
}

_DEFAULT_COLOR = 0xDAA520


def build_narrative_embed(
    narrative: str,
    mechanics: str,
    tone: str = "dramatic",
) -> discord.Embed:
    """Build a Discord embed for a narrative response with mechanics.

    Args:
        narrative: The narrative text from the narrator LLM.
        mechanics: The raw mechanics summary (dice rolls, damage, etc.).
        tone: The narrative tone — affects embed color.
              Valid values: dramatic, tense, humorous, somber.

    Returns:
        A discord.Embed with narrative as description and mechanics as a field.
    """
    color = _TONE_COLORS.get(tone, _DEFAULT_COLOR)

    embed = discord.Embed(
        description=narrative,
        color=color,
    )

    embed.add_field(
        name="Mecaniques",
        value=mechanics,
        inline=False,
    )

    return embed
