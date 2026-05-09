"""Dice roll embed builders (task 60).

A small family of builders that turn engine-level dice results into Discord
embeds the TurnManager, action pipeline, and views can post whenever a
mechanical roll resolves.

The builders are pure: they do not touch the engine, they do not send to
Discord, they just map a :class:`~engine.combat.AttackResult` /
:class:`~engine.dice.D20CheckResult` / :class:`~engine.dice.DiceResult` into
a :class:`discord.Embed`. Consumers (task 31 for flee, task 64 for NPC turn
resolution, etc.) wire the embeds into their flows — this module has no
ambient side effects.

Color conventions
-----------------
- Hit / success → green (0x2ECC71)
- Miss / failure → red   (0xE74C3C)
- Critical success → gold (0xF1C40F) — overrides the green on nat-20

French outcome labels and damage type names are module-local tables so the
embeds stay legible without leaking engine enum values into the UI.
"""

from __future__ import annotations

from typing import Any

import discord

from engine.character import SKILL_ABILITY, Skill
from engine.combat import AttackResult
from engine.dice import D20CheckResult, DiceResult, RollOutcome
from engine.inventory import DamageType


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

_COLOR_HIT = 0x2ECC71
_COLOR_MISS = 0xE74C3C
_COLOR_CRIT = 0xF1C40F


def _color_for_outcome(outcome: RollOutcome, *, hit: bool | None = None) -> int:
    """Pick the embed color for a given outcome.

    ``hit`` is only consulted when the outcome is one of the ambiguous
    middle tiers (``NEAR_SUCCESS``, ``NEAR_FAILURE``) that can mean either
    hit or miss depending on whether we are rolling an attack (vs AC) or
    a generic check (vs DC).
    """
    if outcome == RollOutcome.CRITICAL_SUCCESS:
        return _COLOR_CRIT
    if outcome == RollOutcome.CRITICAL_FAILURE:
        return _COLOR_MISS
    if outcome in (RollOutcome.SUCCESS, RollOutcome.NEAR_SUCCESS):
        return _COLOR_HIT
    if outcome in (RollOutcome.FAILURE, RollOutcome.NEAR_FAILURE):
        return _COLOR_MISS
    if hit is True:
        return _COLOR_HIT
    return _COLOR_MISS


# ---------------------------------------------------------------------------
# French labels
# ---------------------------------------------------------------------------

_OUTCOME_FR: dict[RollOutcome, tuple[str, str]] = {
    RollOutcome.CRITICAL_SUCCESS: ("⭐", "Succès critique"),
    RollOutcome.SUCCESS: ("✔", "Succès"),
    RollOutcome.NEAR_SUCCESS: ("✔", "Succès de justesse"),
    RollOutcome.NEAR_FAILURE: ("✖", "Échec de peu"),
    RollOutcome.FAILURE: ("✖", "Échec"),
    RollOutcome.CRITICAL_FAILURE: ("💀", "Échec critique"),
}

_DAMAGE_TYPE_FR: dict[DamageType, str] = {
    DamageType.SLASHING: "tranchant",
    DamageType.PIERCING: "perforant",
    DamageType.BLUDGEONING: "contondant",
    DamageType.FIRE: "feu",
    DamageType.COLD: "froid",
    DamageType.LIGHTNING: "foudre",
    DamageType.THUNDER: "tonnerre",
    DamageType.POISON: "poison",
    DamageType.RADIANT: "radiant",
    DamageType.NECROTIC: "nécrotique",
    DamageType.FORCE: "force",
}


def _format_outcome(outcome: RollOutcome) -> str:
    """Return ``"{emoji} {label}"`` for a RollOutcome."""
    emoji, label = _OUTCOME_FR[outcome]
    return f"{emoji} {label}"


def _format_damage_type(damage_type: DamageType) -> str:
    """Return the French noun for a damage type (fallback: lowercase enum value)."""
    return _DAMAGE_TYPE_FR.get(damage_type, damage_type.value.lower())


# ---------------------------------------------------------------------------
# Attack roll embed
# ---------------------------------------------------------------------------


def build_attack_roll_embed(
    result: AttackResult,
    attacker_name: str,
) -> discord.Embed:
    """Render an attack roll result as a Discord embed.

    Shows the natural d20 roll, the attack total, target AC, hit/miss
    verdict, and damage on a hit. Color follows the hit/miss/crit
    convention.
    """
    if result.critical and result.hit:
        color = _COLOR_CRIT
        verdict = "⭐ Coup critique"
    elif result.hit:
        color = _COLOR_HIT
        verdict = "⚔️ Touché"
    else:
        color = _COLOR_MISS
        verdict = "🛡️ Raté"

    modifier = result.attack_total - result.attack_roll
    sign = "+" if modifier >= 0 else ""
    formula = f"`1d20{sign}{modifier}`"

    lines = [
        f"{formula} → **{result.attack_total}** vs AC **{result.ac}**",
        verdict,
    ]
    if result.hit and result.damage > 0:
        dtype_fr = _format_damage_type(result.damage_type)
        lines.append(f"💥 **{result.damage}** dégâts ({dtype_fr})")

    embed = discord.Embed(
        title=f"⚔️ {attacker_name} → {result.defender}",
        description="\n".join(lines),
        color=color,
    )

    margin = result.attack_total - result.ac
    embed.set_footer(text=f"Nat {result.attack_roll} — marge {margin:+d}")
    return embed


# ---------------------------------------------------------------------------
# D20 check embeds (saves, generic checks)
# ---------------------------------------------------------------------------


def build_save_check_embed(
    check: D20CheckResult,
    label: str,
    actor_name: str,
    ability: str,
) -> discord.Embed:
    """Render a d20 check evaluated against a DC (saves, skill checks, …).

    ``label`` is the human-readable context (e.g. ``"Jet de sauvegarde"``
    or ``"Tentative de fuite"``). ``ability`` is the short code shown in
    the title (``"DEX"``, ``"WIS"``, …).
    """
    color = _color_for_outcome(check.outcome)
    natural_roll = check.rolls[0] if check.rolls else 0
    modifier = check.total - natural_roll
    sign = "+" if modifier >= 0 else ""
    formula = f"`1d20{sign}{modifier}`" if modifier else "`1d20`"

    ability_label = f" ({ability})" if ability and ability != "-" else ""
    title = f"🎲 {actor_name} — {label}{ability_label}"

    lines = [
        f"{formula} → **{check.total}** vs DC **{check.dc}**",
        _format_outcome(check.outcome),
    ]

    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        color=color,
    )
    embed.set_footer(text=f"Nat {natural_roll} — marge {check.margin:+d}")
    return embed


def build_generic_check_embed(
    check: D20CheckResult,
    label: str,
    actor_name: str,
) -> discord.Embed:
    """Render a d20 check without a specific ability label.

    Convenience wrapper around :func:`build_save_check_embed` for places
    where the ability context is implicit or absent.
    """
    return build_save_check_embed(check, label, actor_name, ability="-")


# ---------------------------------------------------------------------------
# Skill check embed (IMPROVISE → skill check)
# ---------------------------------------------------------------------------

# French label per Skill, used in the embed title. Falls back to the SRD
# (English) name when missing — keeps the embed legible even if a skill is
# added before this table is updated.
_SKILL_FR_LABEL: dict[Skill, str] = {
    Skill.ATHLETICS: "Athlétisme",
    Skill.ACROBATICS: "Acrobatie",
    Skill.SLEIGHT_OF_HAND: "Escamotage",
    Skill.STEALTH: "Discrétion",
    Skill.ARCANA: "Arcanes",
    Skill.HISTORY: "Histoire",
    Skill.INVESTIGATION: "Investigation",
    Skill.NATURE: "Nature",
    Skill.RELIGION: "Religion",
    Skill.ANIMAL_HANDLING: "Dressage",
    Skill.INSIGHT: "Perspicacité",
    Skill.MEDICINE: "Médecine",
    Skill.PERCEPTION: "Perception",
    Skill.SURVIVAL: "Survie",
    Skill.DECEPTION: "Tromperie",
    Skill.INTIMIDATION: "Intimidation",
    Skill.PERFORMANCE: "Représentation",
    Skill.PERSUASION: "Persuasion",
}


def build_skill_check_embed(
    check: D20CheckResult,
    skill: Skill,
    actor_name: str,
) -> discord.Embed:
    """Render a skill-check d20 result (IMPROVISE → skill check).

    Title shows the French skill name and the underlying ability code,
    e.g. ``"🎲 Héros — Test de Escamotage (DEX)"``. The body reuses the
    same formula / outcome / margin layout as
    :func:`build_save_check_embed` for visual consistency.
    """
    label = f"Test de {_SKILL_FR_LABEL.get(skill, skill.value)}"
    ability = SKILL_ABILITY[skill].value
    return build_save_check_embed(check, label, actor_name, ability)


# ---------------------------------------------------------------------------
# Tuple → embed dispatcher (used by both action_handler and combat turn manager)
# ---------------------------------------------------------------------------


def embed_for_dice_entry(entry: Any, *, fallback_actor: str) -> discord.Embed | None:
    """Convert a ``pending_dice_embeds`` tuple into a :class:`discord.Embed`.

    Tuples carry a ``kind`` tag at index 0 plus payload. Recognised kinds:

    - ``("attack_roll", AttackResult, attacker_name)``
    - ``("flee_check", D20CheckResult, actor_name)``
    - ``("truce_check", D20CheckResult, actor_name)``
    - ``("skill_check", D20CheckResult, actor_name, Skill)``
    - any other tag → generic d20 check embed

    Returns ``None`` for malformed entries so the caller can drop them
    silently rather than crash.
    """
    if not isinstance(entry, tuple) or len(entry) < 2:
        return None
    kind = entry[0]
    payload = entry[1]
    name = entry[2] if len(entry) >= 3 and isinstance(entry[2], str) else fallback_actor

    if kind == "attack_roll" and isinstance(payload, AttackResult):
        return build_attack_roll_embed(payload, name)
    if not isinstance(payload, D20CheckResult):
        return None
    if kind == "flee_check":
        return build_save_check_embed(
            payload, label="Tentative de fuite", actor_name=name, ability="DEX",
        )
    if kind == "truce_check":
        return build_save_check_embed(
            payload, label="Tentative de trêve", actor_name=name, ability="CHA",
        )
    if kind == "skill_check":
        skill = entry[3] if len(entry) >= 4 else None
        if isinstance(skill, Skill):
            return build_skill_check_embed(payload, skill, name)
        return build_generic_check_embed(payload, label="Test de compétence", actor_name=name)
    return build_generic_check_embed(
        payload, label=str(kind).replace("_", " ").title(), actor_name=name,
    )


# ---------------------------------------------------------------------------
# Standalone damage roll embed
# ---------------------------------------------------------------------------


def build_damage_roll_embed(
    dice_expression: str,
    result: DiceResult,
    damage_type: DamageType,
    source_name: str = "",
) -> discord.Embed:
    """Render a standalone damage roll (e.g. signature area-of-effect).

    Used when the attack and damage rolls are detached — a boss signature
    that hits everyone in a zone, a condition tick, a trap. Shows the
    individual dice rolls alongside the total and damage type.
    """
    rolls_text = ", ".join(str(r) for r in result.rolls)
    modifier = result.modifier
    sign = "+" if modifier >= 0 else ""
    if modifier:
        breakdown = f"`{dice_expression}` → ({rolls_text}) {sign}{modifier}"
    else:
        breakdown = f"`{dice_expression}` → ({rolls_text})"

    dtype_fr = _format_damage_type(damage_type)
    title = (
        f"💥 {source_name} — {result.total} dégâts ({dtype_fr})"
        if source_name
        else f"💥 {result.total} dégâts ({dtype_fr})"
    )

    embed = discord.Embed(
        title=title,
        description=breakdown,
        color=_COLOR_HIT,
    )
    return embed
