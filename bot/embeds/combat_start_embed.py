"""Combat-start embed builder (task 61).

Builds the big "⚔️ Combat commence" banner the TurnManager posts **once**
when a :class:`~engine.combat.CombatState` is bootstrapped. Shows the
initiative order with an active-turn marker, a surprise announcement tuned
to the three SRD 5e cases (``PLAYERS`` / ``NPCS`` / ``BOTH_READY``), and a
short command-example block to guide players who haven't yet discovered
the combat hub buttons.

Pure: no Discord side effects, no engine mutations.
"""

from __future__ import annotations

import discord

from engine.combat import CombatSide, CombatState
from engine.combat_trigger import CombatTrigger, InitiativeSide
from engine.conditions import is_surprised


_COLOR = 0xCC0000  # same red as the combat state embed — visual continuity
_MARKER_ACTIVE = "➡️"
_MARKER_IDLE = "  "
_ICON_PLAYER = "🧛"
_ICON_ENEMY = "👹"

_FALLBACK_HINT = (
    "Préparez vos armes — le combat engagé s'impose à vous."
)

_COMMAND_EXAMPLES = (
    "```\n"
    "@bot je frappe <cible>\n"
    "@bot je lance <sort> sur <cible>\n"
    "@bot je tente de fuir\n"
    "```"
)


def build_combat_start_embed(
    state: CombatState,
    trigger: CombatTrigger,
    language: str = "fr",
) -> discord.Embed:
    """Build the combat-start embed.

    Args:
        state: The freshly-built :class:`~engine.combat.CombatState`. Used
            for the initiative order and active combatant detection.
        trigger: The :class:`~engine.combat_trigger.CombatTrigger` that
            caused the bootstrap. Drives the description, the surprise
            announcement, and the list of surprised combatants.
        language: Reserved for future i18n. Only ``"fr"`` is supported
            today; non-French values silently fall back to French copy.

    Returns:
        A :class:`discord.Embed` ready to post in the campaign channel.
    """
    del language  # French-only MVP; parameter preserved for the task 61 API.

    description = trigger.narrative_hint or _FALLBACK_HINT

    embed = discord.Embed(
        title="⚔️ Combat commence",
        description=description,
        color=_COLOR,
    )

    embed.add_field(
        name="Ordre d'initiative",
        value=_render_initiative_order(state),
        inline=False,
    )

    surprise_text = _render_surprise_field(trigger)
    if surprise_text is not None:
        embed.add_field(
            name="Surprise",
            value=surprise_text,
            inline=False,
        )

    embed.add_field(
        name="À votre tour",
        value=_COMMAND_EXAMPLES,
        inline=False,
    )

    return embed


def _render_initiative_order(state: CombatState) -> str:
    """Render the turn order as a bullet list with active/surprise markers."""
    lines: list[str] = []
    for idx, c in enumerate(state.combatants):
        marker = _MARKER_ACTIVE if idx == state.current_turn_index else _MARKER_IDLE
        icon = _ICON_PLAYER if c.side == CombatSide.PLAYER else _ICON_ENEMY
        line = f"{marker} {icon} **{c.name}** — init {c.initiative}"
        if is_surprised(c.conditions):
            line += " *(surpris)*"
        lines.append(line)
    return "\n".join(lines)


def _render_surprise_field(trigger: CombatTrigger) -> str | None:
    """Return the French surprise announcement or ``None`` for BOTH_READY."""
    if trigger.surprise_side == InitiativeSide.BOTH_READY:
        return None

    names_bold = ", ".join(f"**{name}**" for name in trigger.enemy_names)

    if trigger.surprise_side == InitiativeSide.PLAYERS:
        return (
            f"Vous avez surpris {names_bold}. "
            "Ils ne peuvent pas agir à leur premier tour."
        )

    # NPCS surprise the players
    return (
        f"Vous êtes surpris par l'attaque. {names_bold} "
        "agissent avant que vous ne puissiez réagir."
    )
