"""Components V2 récap of the character sheet for the REVIEW step.

Falls back to a classic embed if Components V2 is unavailable at runtime.
"""

from __future__ import annotations

import discord

from engine.character import Character


def build_setup_recap_embed(
    character: Character,
    kit_name: str,
    motivation_key: str,
    concept: str,
    language: str = "fr",
) -> discord.Embed:
    """Build the recap embed (classic embed — works on all discord.py versions).

    ``kit_name`` and ``motivation_key`` are the canonical English keys the
    engine stores; they are translated for display only, so step 6/6 reads
    in the same language as the steps that produced it.
    """
    embed = discord.Embed(
        title=f"📜 {character.name}",
        description=concept or "_Aucun concept renseigné._",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Identité",
        value=f"**{character.race.value} {character.char_class.value}** — Niveau {character.level}",
        inline=False,
    )
    s = character.ability_scores
    embed.add_field(
        name="Caractéristiques",
        value=(
            f"```STR {s.STR:2d}  DEX {s.DEX:2d}  CON {s.CON:2d}\n"
            f"INT {s.INT:2d}  WIS {s.WIS:2d}  CHA {s.CHA:2d}```"
        ),
        inline=False,
    )
    embed.add_field(
        name="Vie & Défense",
        value=f"❤️ HP {character.hp}/{character.max_hp}  •  🛡️ AC {character.ac}",
        inline=True,
    )
    embed.add_field(
        name="Bonus de maîtrise",
        value=f"+{character.proficiency_bonus}",
        inline=True,
    )
    embed.add_field(
        name="Sauvegardes maîtrisées",
        value=", ".join(a.name for a in character.saving_throw_proficiencies),
        inline=False,
    )
    if character.skill_proficiencies:
        embed.add_field(
            name="Compétences",
            value=", ".join(s.value for s in character.skill_proficiencies),
            inline=False,
        )
    from bot.i18n import get_kit_label, get_motivation_label

    embed.add_field(
        name="Kit de départ",
        value=get_kit_label(language, kit_name, "name") if kit_name else "",
        inline=True,
    )
    embed.add_field(
        name="Motivation",
        value=get_motivation_label(language, motivation_key),
        inline=True,
    )
    return embed
