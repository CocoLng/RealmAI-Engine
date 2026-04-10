"""Narrative embed builder — displays narrator output in a Discord embed.

The embed is deliberately minimal: narration as the description, and an
optional discreet footer listing *public* player-facing effects only
(HP deltas, items, movement, XP/level up). Hidden stats — NPC disposition,
rolls, DCs, secrets — NEVER surface here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from ai.models import PublicEffects

if TYPE_CHECKING:
    from world.location import Location
    from world.story_arc import StoryArc

_TONE_COLORS: dict[str, int] = {
    "dramatic": 0xDAA520,
    "tense": 0xCC0000,
    "humorous": 0x339933,
    "somber": 0x663399,
}

_DEFAULT_COLOR = 0xDAA520


def build_narrative_embed(
    narrative: str,
    *,
    public_effects: PublicEffects | None = None,
    tone: str = "dramatic",
    footer_override: str | None = None,
) -> discord.Embed:
    """Build a Discord embed for a narrative response.

    Args:
        narrative: The narrative text from the narrator LLM.
        public_effects: Player-safe effects to summarise in the footer.
            When ``None`` or empty, no footer is rendered.
        tone: The narrative tone — affects embed color.
              Valid values: dramatic, tense, humorous, somber.
        footer_override: Raw text to use as footer instead of ``public_effects``.
            Used for system messages (e.g. unknown-entity refusals).

    Returns:
        A discord.Embed with narrative as description and an optional footer.
    """
    color = _TONE_COLORS.get(tone, _DEFAULT_COLOR)

    embed = discord.Embed(
        description=narrative,
        color=color,
    )

    footer_text: str | None = None
    if footer_override is not None:
        footer_text = footer_override
    elif public_effects is not None:
        footer_text = public_effects.to_footer_text()

    if footer_text:
        embed.set_footer(text=footer_text)

    return embed


def build_opening_crawl_embed(
    campaign_name: str,
    story_arc: StoryArc | None,
    location: Location | None,
    language: str = "fr",
) -> discord.Embed:
    """Build an immersive opening embed from arc and location data."""
    premise = "Votre aventure commence..."
    if story_arc and story_arc.premise:
        premise = story_arc.premise

    embed = discord.Embed(
        title=f"\U0001f4dc {campaign_name}",
        description=premise,
        color=_DEFAULT_COLOR,
    )

    if location:
        loc_desc = location.description or location.name
        embed.add_field(
            name="Lieu de départ" if language == "fr" else "Starting Location",
            value=f"**{location.name}**\n{loc_desc}",
            inline=False,
        )

    if story_arc and story_arc.beats:
        first_beat = story_arc.beats[0]
        embed.add_field(
            name="Premier chapitre" if language == "fr" else "First Chapter",
            value=f"*{first_beat.description}*",
            inline=False,
        )

    return embed
