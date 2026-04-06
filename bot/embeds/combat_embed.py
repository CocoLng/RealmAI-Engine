"""Combat embed builder — displays combat state in a Discord embed."""

import discord

from engine.combat import CombatState


_COLOR = 0xCC0000
_BAR_LENGTH = 10
_FILLED = "\u2588"  # full block
_EMPTY = "\u2591"  # light shade


def _hp_bar(hp: int, max_hp: int) -> str:
    """Build a text-based HP bar.

    Example: ``[████░░░░░░] 12/30``
    """
    if max_hp <= 0:
        ratio = 0.0
    else:
        ratio = max(0.0, min(1.0, hp / max_hp))
    filled = round(ratio * _BAR_LENGTH)
    empty = _BAR_LENGTH - filled
    return f"[{_FILLED * filled}{_EMPTY * empty}] {hp}/{max_hp}"


def build_combat_embed(combat_state: CombatState) -> discord.Embed:
    """Build a Discord embed for the current combat state.

    Args:
        combat_state: The ongoing combat encounter.

    Returns:
        A discord.Embed with initiative order, HP bars, and conditions.
    """
    embed = discord.Embed(
        title=f"Combat — Round {combat_state.round_number}",
        color=_COLOR,
    )

    lines: list[str] = []
    active_name = ""

    for idx, combatant in enumerate(combat_state.combatants):
        is_active = idx == combat_state.current_turn_index
        marker = "> " if is_active else "  "
        hp = combatant.character.hp
        max_hp = combatant.character.max_hp
        bar = _hp_bar(hp, max_hp)

        line = f"{marker}**{combatant.name}** ({combatant.initiative}) — {bar}"

        # Active conditions
        if combatant.conditions:
            cond_names = ", ".join(c.condition_type.value for c in combatant.conditions)
            line += f"\n    *{cond_names}*"

        lines.append(line)

        if is_active:
            active_name = combatant.name

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"Tour de: {active_name}")

    return embed
