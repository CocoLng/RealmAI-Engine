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
from bot.i18n import COUNTDOWN_LABELS

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
    npc_name: str | None = None,
    npc_dialogue: str | None = None,
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
        npc_name: When an NPC is speaking, their name — displayed as
            embed author and dialogue field header.
        npc_dialogue: The NPC's spoken words, displayed in a dedicated
            field visually separated from the narrator prose.

    Returns:
        A discord.Embed with narrative as description and an optional footer.
    """
    color = _TONE_COLORS.get(tone, _DEFAULT_COLOR)

    embed = discord.Embed(
        description=narrative,
        color=color,
    )

    # NPC dialogue: author header + dedicated field for spoken words.
    if npc_name and npc_dialogue:
        embed.set_author(name=f"\U0001f5e3\ufe0f {npc_name}")
        embed.add_field(
            name=f"\u00ab {npc_name} \u00bb",
            value=f"*\u00ab {npc_dialogue} \u00bb*",
            inline=False,
        )

    footer_text: str | None = None
    if footer_override is not None:
        footer_text = footer_override
    elif public_effects is not None:
        footer_text = public_effects.to_footer_text()

    if footer_text:
        embed.set_footer(text=footer_text)

    return embed


_STATE_COLOR = 0x4A90D9  # Blue — distinct from narrative tones


def build_state_embed(
    narrative: str,
    *,
    location_name: str,
    items: list[str],
    npcs: list[str],
    exits: list[str],
    beat_title: str | None = None,
    language: str = "fr",
) -> discord.Embed:
    """Build a blue state-info embed for question responses.

    Visually distinct from narrative embeds to signal meta-info.
    """
    embed = discord.Embed(
        title=f"\U0001f4cb {location_name}",
        description=narrative,
        color=_STATE_COLOR,
    )

    if items:
        label = "Objets visibles" if language == "fr" else "Visible items"
        embed.add_field(name=label, value="\n".join(f"- {i}" for i in items), inline=True)

    if npcs:
        label = "PNJ présents" if language == "fr" else "NPCs present"
        embed.add_field(name=label, value="\n".join(f"- {n}" for n in npcs), inline=True)

    if exits:
        label = "Sorties" if language == "fr" else "Exits"
        embed.add_field(name=label, value="\n".join(f"- {e}" for e in exits), inline=True)

    if beat_title:
        label = "Chapitre en cours" if language == "fr" else "Current chapter"
        embed.add_field(name=label, value=f"*{beat_title}*", inline=False)

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


# Step → color progression: gold → orange → red
_COUNTDOWN_COLORS: dict[int, int] = {
    3: 0xDAA520,
    2: 0xCC7000,
    1: 0xCC0000,
}


def build_countdown_embed(
    step: int,
    campaign_name: str,
    language: str = "fr",
) -> discord.Embed:
    """Build an animated countdown embed for a given step (3, 2, or 1).

    Args:
        step: The countdown number (3, 2, or 1).
        campaign_name: Campaign name shown in the footer.
        language: Language code for the description text.

    Returns:
        A discord.Embed with styled title, description, and color.
    """
    color = _COUNTDOWN_COLORS.get(step, _DEFAULT_COLOR)
    labels = COUNTDOWN_LABELS.get(language, COUNTDOWN_LABELS["fr"])
    description = labels.get(step, "...")

    embed = discord.Embed(
        title=f"\u300c {step} \u300d",
        description=f"*{description}*",
        color=color,
    )
    embed.set_footer(text=campaign_name)

    return embed
