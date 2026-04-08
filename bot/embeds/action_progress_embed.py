"""Progress embed for the @mention action pipeline.

The cog edits this embed in place as the pipeline advances through its
phases, then either replaces it with the final narrative embed or attaches
a clarification view.
"""

from __future__ import annotations

import discord

from bot.action_pipeline import PipelinePhase

_PROGRESS_COLOR = 0x4A90E2

_PHASE_LABELS: list[tuple[PipelinePhase, str]] = [
    (PipelinePhase.INTERPRETING,       "Interprétation de l'action"),
    (PipelinePhase.RESOLVING_ENTITIES, "Vérification du contexte"),
    (PipelinePhase.VALIDATING,         "Application des règles"),
    (PipelinePhase.RESOLVING_ACTION,   "Résolution mécanique"),
    (PipelinePhase.ASSEMBLING_CONTEXT, "Préparation du contexte"),
    (PipelinePhase.NARRATING,          "Narration"),
]


def build_action_progress_embed(
    *,
    actor_name: str,
    raw_text: str,
    current_phase: PipelinePhase,
    elapsed_seconds: float,
) -> discord.Embed:
    """Render the progress embed for one player action.

    Each known phase becomes a one-line field with a status indicator:
    ✅ done, 🔄 in progress, ⚪ pending.
    """
    truncated = raw_text if len(raw_text) <= 200 else raw_text[:197] + "..."
    embed = discord.Embed(
        title=f"En cours — {actor_name}",
        description=f"> {truncated}",
        color=_PROGRESS_COLOR,
    )

    for phase, label in _PHASE_LABELS:
        if current_phase == PipelinePhase.FAILED:
            indicator = "❌"
        elif current_phase >= phase:
            indicator = "🔄" if current_phase == phase else "✅"
        else:
            indicator = "⚪"
        embed.add_field(
            name=f"{indicator} {label}",
            value="\u200b",
            inline=False,
        )

    embed.set_footer(text=f"Temps écoulé : {elapsed_seconds:.1f}s")
    return embed
