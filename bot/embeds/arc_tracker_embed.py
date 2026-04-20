"""Builder for the Arc Tracker pinned embed.

Pure function: takes data, returns ``discord.Embed``. No Discord I/O.
"""

from __future__ import annotations

import discord


def build_arc_tracker_embed(
    *,
    chapter_title: str,
    current_objective: str,
    recent_beats: list[str],
    active_quests: list[str],
    last_updated_relative: str,
) -> discord.Embed:
    """Build the Arc Tracker pinned embed for a campaign channel.

    Layout (player-facing):
      📖 <chapter_title>
      Description: <current_objective>
      📜 Beats récents (last 3)
      📋 Quêtes actives (last 5)
      Footer: Mise à jour : <last_updated_relative>
    """
    embed = discord.Embed(
        title=f"📖 {chapter_title}" if chapter_title else "📖 Campagne en cours",
        description=current_objective or "_Aucun objectif clair pour l'instant._",
        color=discord.Color.dark_gold(),
    )

    if recent_beats:
        embed.add_field(
            name="📜 Beats récents",
            value="\n".join(f"• {b[:200]}" for b in recent_beats[-3:]) or "—",
            inline=False,
        )

    if active_quests:
        embed.add_field(
            name="📋 Quêtes actives",
            value="\n".join(f"• {q[:200]}" for q in active_quests[:5]) or "—",
            inline=False,
        )

    embed.set_footer(text=f"Mise à jour : {last_updated_relative}")
    return embed
