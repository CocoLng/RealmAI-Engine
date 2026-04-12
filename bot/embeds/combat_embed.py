"""Combat state embed builder.

Renders the current :class:`~engine.combat.CombatState` as a Discord embed
suitable for the TurnManager hub: active combatant marker, zone grouping
when a :class:`~world.location.Location` with combat zones is provided,
HP bars, French condition list with remaining rounds, and a boss
legendary-points footer when applicable.

The builder is backward-compatible with the flat (zone-less) layout so
legacy tests and non-zoned encounters keep working — callers who omit
``location`` get a single "Combattants" field listing everyone alive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from engine.combat import CombatSide, Combatant, CombatState
from engine.conditions import ActiveCondition, ConditionType
from engine.npc_stat_block import NPCTier

if TYPE_CHECKING:
    from world.location import Location


_COLOR = 0xCC0000
_BAR_LENGTH = 10
_FILLED = "\u2588"  # full block
_EMPTY = "\u2591"  # light shade

_MARKER_ACTIVE = "➡️"
_MARKER_IDLE = "  "
_ICON_PLAYER = "🧛"
_ICON_ENEMY = "👹"
_ICON_BOSS = "👑"
_ICON_ZONE = "📍"

# French translation table for the conditions the combat engine uses most.
_CONDITION_FR: dict[ConditionType, str] = {
    ConditionType.BLINDED: "Aveuglé",
    ConditionType.CHARMED: "Charmé",
    ConditionType.DEAFENED: "Assourdi",
    ConditionType.FRIGHTENED: "Apeuré",
    ConditionType.GRAPPLED: "Agrippé",
    ConditionType.INCAPACITATED: "Incapable",
    ConditionType.INVISIBLE: "Invisible",
    ConditionType.PARALYZED: "Paralysé",
    ConditionType.PETRIFIED: "Pétrifié",
    ConditionType.POISONED: "Empoisonné",
    ConditionType.PRONE: "À terre",
    ConditionType.RESTRAINED: "Entravé",
    ConditionType.STUNNED: "Étourdi",
    ConditionType.UNCONSCIOUS: "Inconscient",
    ConditionType.EXHAUSTION: "Épuisé",
    ConditionType.SURPRISED: "Surpris",
    ConditionType.CONCENTRATING: "Concentration",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hp_bar(hp: int, max_hp: int) -> str:
    """Build a 10-char HP bar: ``[████░░░░░░] 12/30``."""
    if max_hp <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, hp / max_hp))
    filled = round(ratio * _BAR_LENGTH)
    empty = _BAR_LENGTH - filled
    return f"[{_FILLED * filled}{_EMPTY * empty}] {hp}/{max_hp}"


def _format_condition(cond: ActiveCondition) -> str:
    """Render an active condition as ``Apeuré(2r)`` — French label + duration."""
    label = _CONDITION_FR.get(cond.condition_type, cond.condition_type.value)
    if cond.duration_rounds is not None:
        return f"{label}({cond.duration_rounds}r)"
    return label


def _icon_for(combatant: Combatant) -> str:
    """Pick the combatant icon (boss > enemy > player)."""
    if combatant.side == CombatSide.PLAYER:
        return _ICON_PLAYER
    if (
        combatant.stat_block is not None
        and combatant.stat_block.tier == NPCTier.BOSS
    ):
        return _ICON_BOSS
    return _ICON_ENEMY


def _format_combatant_line(c: Combatant, *, is_active: bool) -> str:
    """Render one combatant line for the embed body.

    Format::

        ➡️ 🧛 **Aragorn** — [██████░░░░] 60/80
            *(Apeuré(2r))*
    """
    marker = _MARKER_ACTIVE if is_active else _MARKER_IDLE
    icon = _icon_for(c)
    name_bold = f"**{c.name}**"
    bar = _hp_bar(c.character.hp, c.character.max_hp)
    line = f"{marker} {icon} {name_bold} — {bar}"
    if c.conditions:
        conds = ", ".join(_format_condition(cd) for cd in c.conditions)
        line += f"\n    *({conds})*"
    return line


def _combatant_is_visible(c: Combatant) -> bool:
    """Hide dead and fled combatants from the state panel."""
    return c.is_alive and not c.fled


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_combat_embed(
    combat_state: CombatState,
    location: "Location | None" = None,
) -> discord.Embed:
    """Build the combat state embed (hub panel).

    Args:
        combat_state: Current :class:`~engine.combat.CombatState`.
        location: Optional :class:`~world.location.Location`. When provided
            and ``location.has_combat_zones()``, combatants are grouped by
            ``current_zone`` — one field per zone. Otherwise, a single
            "Combattants" field lists everyone (legacy flat layout).

    Returns:
        A :class:`discord.Embed` ready to post or edit into the hub message.
    """
    embed = discord.Embed(
        title=f"Combat — Round {combat_state.round_number}",
        color=_COLOR,
    )

    active_idx = combat_state.current_turn_index
    active = (
        combat_state.combatants[active_idx]
        if 0 <= active_idx < len(combat_state.combatants)
        else None
    )
    if active is not None:
        embed.description = f"{_MARKER_ACTIVE} **{active.name}** joue"

    visible = [
        (idx, c)
        for idx, c in enumerate(combat_state.combatants)
        if _combatant_is_visible(c)
    ]

    if location is not None and location.has_combat_zones():
        _add_zone_grouped_fields(embed, visible, active_idx, location)
    else:
        _add_flat_field(embed, visible, active_idx)

    _add_boss_field(embed, combat_state)

    if active is not None:
        embed.set_footer(text=f"Tour de : {active.name}")
    else:
        embed.set_footer(text="Combat terminé")

    return embed


def _add_flat_field(
    embed: discord.Embed,
    visible: list[tuple[int, Combatant]],
    active_idx: int,
) -> None:
    """Single "Combattants" field — legacy, no zones."""
    if not visible:
        embed.add_field(name="Combattants", value="(aucun)", inline=False)
        return
    lines = [
        _format_combatant_line(c, is_active=(idx == active_idx))
        for idx, c in visible
    ]
    embed.add_field(name="Combattants", value="\n".join(lines), inline=False)


def _add_zone_grouped_fields(
    embed: discord.Embed,
    visible: list[tuple[int, Combatant]],
    active_idx: int,
    location: "Location",
) -> None:
    """One field per zone, plus an "Hors zone" bucket for stragglers."""
    for zone in location.combat_zones:
        in_zone = [
            (idx, c) for idx, c in visible if c.current_zone == zone.name
        ]
        value = (
            "\n".join(
                _format_combatant_line(c, is_active=(idx == active_idx))
                for idx, c in in_zone
            )
            if in_zone
            else "*(vide)*"
        )
        embed.add_field(
            name=f"{_ICON_ZONE} {zone.name}",
            value=value,
            inline=False,
        )

    unzoned = [(idx, c) for idx, c in visible if c.current_zone is None]
    if unzoned:
        value = "\n".join(
            _format_combatant_line(c, is_active=(idx == active_idx))
            for idx, c in unzoned
        )
        embed.add_field(name="Hors zone", value=value, inline=False)


def _add_boss_field(embed: discord.Embed, state: CombatState) -> None:
    """Append a "Boss" field when a BOSS-tier combatant is alive with legendary points."""
    for c in state.combatants:
        if not c.is_alive or c.fled:
            continue
        if c.stat_block is None or c.stat_block.tier != NPCTier.BOSS:
            continue
        legendary_line = (
            f"⚡ **Actions légendaires** — {c.legendary_points_remaining} "
            "points restants"
        )
        embed.add_field(
            name=f"{_ICON_BOSS} {c.name}",
            value=legendary_line,
            inline=False,
        )
        break  # Only surface one boss to keep the panel compact.
