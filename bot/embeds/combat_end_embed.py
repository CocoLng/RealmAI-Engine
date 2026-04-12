"""End-of-combat recap embed.

Rendered by :meth:`bot.combat_turn_manager.TurnManager._finalize` once
:func:`bot.combat_end.finalize_combat` has produced a
:class:`~bot.combat_end.CombatEndSummary`. The embed surfaces the four
outcomes (victory, defeat, fled, truce) with distinct colors and French
titles, plus optional fields that only appear when populated (loot,
killed PCs, fled PCs, XP).

No LLM — the ``narrative`` field on the summary is consumed verbatim if
set, otherwise a deterministic French fallback is substituted.
"""

from __future__ import annotations

import discord

from bot.combat_end import CombatEndSummary
from engine.combat import CombatEndReason


# Green / red / gray / purple — distinct enough that players recognise the
# outcome at a glance. No orange (no TIMEOUT — combat is persistent).
_COLORS: dict[CombatEndReason, int] = {
    CombatEndReason.VICTORY: 0x2ECC71,
    CombatEndReason.DEFEAT: 0xE74C3C,
    CombatEndReason.FLED: 0x95A5A6,
    CombatEndReason.TRUCE: 0x9B59B6,
}

_TITLES: dict[CombatEndReason, str] = {
    CombatEndReason.VICTORY: "🏆 Victoire",
    CombatEndReason.DEFEAT: "💀 Défaite",
    CombatEndReason.FLED: "🏃 Fuite réussie",
    CombatEndReason.TRUCE: "🕊️ Trêve",
}


def build_combat_end_embed(summary: CombatEndSummary) -> discord.Embed:
    """Build the end-of-combat recap embed from a :class:`CombatEndSummary`.

    Fields are only added when they carry data — a clean FLED (no killed
    enemies, no loot) only shows the fled PCs and the round count.
    """
    embed = discord.Embed(
        title=_TITLES[summary.reason],
        description=summary.narrative or _default_narrative(summary),
        color=_COLORS[summary.reason],
    )

    if summary.killed_enemies:
        embed.add_field(
            name="Ennemis vaincus",
            value="\n".join(f"• {n}" for n in summary.killed_enemies),
            inline=True,
        )
    if summary.killed_pcs:
        embed.add_field(
            name="Tombés au combat",
            value="\n".join(f"• {n}" for n in summary.killed_pcs),
            inline=True,
        )
    if summary.fled_pcs:
        embed.add_field(
            name="Ayant fui",
            value="\n".join(f"• {n}" for n in summary.fled_pcs),
            inline=True,
        )

    if summary.loot_items:
        embed.add_field(
            name="Butin",
            value=", ".join(summary.loot_items),
            inline=False,
        )

    if summary.xp_earned > 0 and summary.survivors_pc:
        embed.add_field(
            name="Expérience gagnée",
            value=f"**{summary.xp_earned}** XP par survivant",
            inline=True,
        )

    if summary.level_ups:
        embed.add_field(
            name="Niveau disponible",
            value=", ".join(summary.level_ups) + " — utilisez `/level_up`",
            inline=False,
        )

    embed.add_field(
        name="Durée",
        value=f"{summary.rounds_taken} round{'s' if summary.rounds_taken > 1 else ''}",
        inline=True,
    )
    return embed


def _default_narrative(summary: CombatEndSummary) -> str:
    """Deterministic French fallback when the summary has no narration."""
    if summary.reason == CombatEndReason.VICTORY:
        return "Les derniers ennemis tombent. Le silence revient sur le champ de bataille."
    if summary.reason == CombatEndReason.DEFEAT:
        return "Le groupe s'effondre sous les coups. L'aventure s'arrête ici."
    if summary.reason == CombatEndReason.FLED:
        return "Le groupe parvient à s'échapper, haletant."
    if summary.reason == CombatEndReason.TRUCE:
        return "Une trêve improbable met fin à l'affrontement."
    return "Le combat se termine."
