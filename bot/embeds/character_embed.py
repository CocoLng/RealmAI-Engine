"""Character embed builder — displays character sheet in a Discord embed."""

import discord

from bot.i18n import (
    ABILITY_LABELS,
    CLASS_LABELS,
    PARTY_CARD_LABELS,
    RACE_LABELS,
    get_label,
)
from engine.character import (
    SKILL_ABILITY,
    Ability,
    Character,
    CharacterClass,
    compute_modifier,
    compute_skill_modifier,
)

# Class-based embed colors
CLASS_COLORS: dict[CharacterClass, int] = {
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
    color = CLASS_COLORS.get(character.char_class, _DEFAULT_COLOR)

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


def build_party_card_embed(
    character: Character,
    member_name: str,
    language: str = "fr",
) -> discord.Embed:
    """Build a condensed character card for party discovery at launch.

    Args:
        character: The character to display.
        member_name: Discord display name of the player.
        language: Language code for translated labels.

    Returns:
        A compact discord.Embed with key stats and ability scores.
    """
    color = CLASS_COLORS.get(character.char_class, _DEFAULT_COLOR)

    race_label = get_label(RACE_LABELS, language, character.race.value)
    class_label = get_label(CLASS_LABELS, language, character.char_class.value)
    card_labels = PARTY_CARD_LABELS.get(language, PARTY_CARD_LABELS.get("en", {}))
    ability_labels = ABILITY_LABELS.get(language, {})

    lvl = card_labels.get("level", "Level")
    hp = card_labels.get("hp", "HP")
    ac = card_labels.get("ac", "AC")

    embed = discord.Embed(
        title=f"{character.name} — {race_label} {class_label}",
        description=f"{lvl} {character.level} · {character.hp} {hp} · {ac} {character.ac}",
        color=color,
    )

    # Compact ability scores: 2 lines of 3
    lines: list[str] = []
    row: list[str] = []
    for i, ability in enumerate(Ability):
        score = character.ability_scores.get(ability)
        mod = compute_modifier(score)
        sign = "+" if mod >= 0 else ""
        label = ability_labels.get(ability.value, ability.value)
        row.append(f"{label} {score:>2}({sign}{mod})")
        if len(row) == 3:
            lines.append("  ".join(row))
            row = []
    if row:
        lines.append("  ".join(row))

    embed.add_field(name="\u200b", value="```\n" + "\n".join(lines) + "\n```", inline=False)
    embed.set_footer(text=member_name)

    return embed
