"""Character embed builder — displays character sheet in a Discord embed."""

import discord

from engine.character import (
    SKILL_ABILITY,
    Ability,
    Character,
    CharacterClass,
    compute_modifier,
    compute_skill_modifier,
)

# Class-based embed colors
_CLASS_COLORS: dict[CharacterClass, int] = {
    CharacterClass.FIGHTER: 0xCC0000,
    CharacterClass.WIZARD: 0x3366CC,
    CharacterClass.ROGUE: 0x666666,
    CharacterClass.CLERIC: 0xFFCC00,
    CharacterClass.RANGER: 0x339933,
    CharacterClass.BARBARIAN: 0x993300,
}

_DEFAULT_COLOR = 0xDAA520


def build_character_embed(character: Character) -> discord.Embed:
    """Build a Discord embed for a character sheet.

    Args:
        character: The character to display.

    Returns:
        A discord.Embed with ability scores, HP/AC, and class info.
    """
    color = _CLASS_COLORS.get(character.char_class, _DEFAULT_COLOR)

    embed = discord.Embed(
        title=f"{character.name} — {character.race} {character.char_class} (Niv. {character.level})",
        color=color,
    )

    # Ability scores — one inline field per ability
    for ability in Ability:
        score = character.ability_scores.get(ability)
        mod = compute_modifier(score)
        sign = "+" if mod >= 0 else ""
        embed.add_field(
            name=ability.value,
            value=f"{score} ({sign}{mod})",
            inline=True,
        )

    # Combat stats
    embed.add_field(
        name="HP",
        value=f"{character.hp}/{character.max_hp}",
        inline=True,
    )
    embed.add_field(
        name="AC",
        value=str(character.ac),
        inline=True,
    )
    embed.add_field(
        name="Proficiency",
        value=f"+{character.proficiency_bonus}",
        inline=True,
    )

    # Saving throw proficiencies
    saves = ", ".join(s.value for s in character.saving_throw_proficiencies)
    embed.add_field(
        name="Saving Throws",
        value=saves,
        inline=False,
    )

    # Features (racial + class traits)
    if character.features:
        features_text = "\n".join(f"• {f.name}" for f in character.features)
        embed.add_field(name="Traits & Features", value=features_text, inline=False)

    # Skill proficiencies with computed modifiers
    if character.skill_proficiencies:
        skills_text = ", ".join(
            f"{s.value} ({SKILL_ABILITY[s].value}) +{compute_skill_modifier(character, s)}"
            for s in character.skill_proficiencies
        )
        embed.add_field(name="Competences", value=skills_text, inline=False)

    # Footer with XP and hit die
    embed.set_footer(
        text=f"XP: {character.xp} — {character.char_class} Hit Die: {character.hit_die}",
    )

    return embed
