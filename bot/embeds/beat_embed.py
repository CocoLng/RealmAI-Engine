"""Beat advancement embed — celebrates moving to the next story beat.

Posted right after the scene embed when ``GameSession.advance_beat_if_ready``
returns a new beat (Lot D — story progression).
"""

from __future__ import annotations

import discord

from world.story_arc import StoryBeat

_BEAT_COLOR = 0x9B59B6  # purple — distinct from scene gold

_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "title": "✨ Nouveau chapitre — Beat {n}/{total}",
        "type": "Type",
        "npcs": "PNJ attendus",
        "twist": "🎭 Coup de théâtre",
        "no_npcs": "—",
    },
    "en": {
        "title": "✨ New chapter — Beat {n}/{total}",
        "type": "Type",
        "npcs": "Expected NPCs",
        "twist": "🎭 Twist",
        "no_npcs": "—",
    },
}


def build_beat_advance_embed(
    beat: StoryBeat,
    total_beats: int,
    language: str = "fr",
) -> discord.Embed:
    """Build the embed announcing a story beat advancement."""
    labels = _LABELS.get(language, _LABELS["fr"])
    embed = discord.Embed(
        title=labels["title"].format(n=beat.beat_number, total=total_beats),
        description=f"**{beat.title}**\n\n{beat.description}",
        color=_BEAT_COLOR,
    )
    embed.add_field(
        name=labels["type"],
        value=beat.encounter_type.capitalize(),
        inline=True,
    )
    npcs_value = ", ".join(beat.npc_names) if beat.npc_names else labels["no_npcs"]
    embed.add_field(name=labels["npcs"], value=npcs_value, inline=True)
    if beat.is_twist:
        embed.add_field(name=labels["twist"], value="⚠️", inline=False)
    return embed
