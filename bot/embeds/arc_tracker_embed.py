"""Builder for the Arc Tracker pinned embed.

Pure function: takes data, returns ``discord.Embed``. No Discord I/O.
"""

from __future__ import annotations

import discord


def _progress_bar(score: int, width: int = 10) -> str:
    """Render an ASCII progress bar."""
    filled = int(round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def build_arc_tracker_embed(
    *,
    chapter_title: str,
    current_objective: str,
    recent_beats: list[str],
    active_quests: list[str],
    last_updated_relative: str,
    progress_score: int = 0,
    objective_status_lines: list[str] | None = None,
    relevant_locations: list[str] | None = None,
    relevant_npcs: list[str] | None = None,
) -> discord.Embed:
    """Build the Arc Tracker pinned embed for a campaign channel.

    Layout (player-facing):
      📖 <chapter_title>  ·  Progression <bar> <pct>%
      🎯 Objectif courant: <current_objective>
      État des objectifs: <checklist>
      🗺️ Lieux pertinents
      👥 Vivants pertinents
      📜 Beats récents
      📋 Quêtes actives
      Footer: Mise à jour : <last_updated_relative>
    """
    objective_status_lines = objective_status_lines or []
    relevant_locations = relevant_locations or []
    relevant_npcs = relevant_npcs or []

    title = (
        f"📖 {chapter_title}  ·  Progression {_progress_bar(progress_score)} {progress_score}%"
        if chapter_title
        else "📖 Campagne en cours"
    )

    embed = discord.Embed(
        title=title[:256],  # Discord title limit
        description=(
            f"🎯 **Objectif courant**\n{current_objective}"
            if current_objective
            else "_Aucun objectif clair pour l'instant._"
        ),
        color=discord.Color.dark_gold(),
    )

    if objective_status_lines:
        embed.add_field(
            name="État des objectifs",
            value="\n".join(line[:200] for line in objective_status_lines)[:1024] or "—",
            inline=False,
        )

    if relevant_locations:
        embed.add_field(
            name="🗺️ Lieux pertinents",
            value=", ".join(relevant_locations)[:1024],
            inline=True,
        )

    if relevant_npcs:
        embed.add_field(
            name="👥 Vivants pertinents",
            value=", ".join(relevant_npcs)[:1024],
            inline=True,
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
            value="\n".join(f"• {q[:200]}" for q in active_quests[-5:]) or "—",
            inline=False,
        )

    embed.set_footer(text=f"Mise à jour : {last_updated_relative}")
    return embed
