"""Inventory embed builder — displays inventory contents in a Discord embed."""

import discord

from engine.character import Character
from engine.inventory import (
    EquipmentSlot,
    Inventory,
    compute_carrying_capacity,
    compute_total_weight,
    is_encumbered,
)


_COLOR = 0xDAA520


def build_inventory_embed(inventory: Inventory, character: Character) -> discord.Embed:
    """Build a Discord embed for a character's inventory.

    Args:
        inventory: The inventory to display.
        character: The owning character (needed for name, STR, size).

    Returns:
        A discord.Embed with gold, weight, equipped items, attunement, and backpack.
    """
    embed = discord.Embed(
        title=f"Inventaire de {character.name}",
        color=_COLOR,
    )

    # Gold
    embed.add_field(name="Or", value=f"{inventory.gold} po", inline=True)

    # Weight
    current = compute_total_weight(inventory)
    capacity = compute_carrying_capacity(character.ability_scores.STR, character.size)
    weight_text = f"{current:.1f}/{capacity:.1f} lb"
    if is_encumbered(inventory, character.ability_scores.STR, character.size):
        weight_text += " **[Encombre]**"
    embed.add_field(name="Poids", value=weight_text, inline=True)

    # Equipped items
    equipped_lines: list[str] = []
    for slot in EquipmentSlot:
        item = inventory.equipped.get(slot)
        if item is not None:
            equipped_lines.append(f"**{slot.value}**: {item.name}")
    if equipped_lines:
        embed.add_field(
            name="Equipe",
            value="\n".join(equipped_lines),
            inline=False,
        )

    # Attuned items (only if any)
    if inventory.attuned:
        attuned_names = ", ".join(item.name for item in inventory.attuned)
        embed.add_field(
            name=f"Harmonise ({len(inventory.attuned)}/3)",
            value=attuned_names,
            inline=False,
        )

    # Backpack — unequipped items
    backpack_lines: list[str] = []
    for item in inventory.items:
        if item.stackable and item.quantity > 1:
            backpack_lines.append(f"{item.name} x{item.quantity}")
        else:
            backpack_lines.append(item.name)
    if backpack_lines:
        embed.add_field(
            name="Sac a dos",
            value="\n".join(backpack_lines),
            inline=False,
        )

    return embed
