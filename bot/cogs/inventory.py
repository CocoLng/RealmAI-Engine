"""Inventory cog — item management commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.inventory_embed import build_inventory_embed
from engine.character import compute_modifier
from engine.inventory import EquipmentSlot, compute_ac_from_equipment, equip_item, remove_item, unequip_item

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class InventoryCog(commands.Cog):
    """Inventory management: view, equip, unequip, use items."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    @app_commands.command(name="inventory", description="Affiche ton inventaire")
    @app_commands.describe(public="Afficher publiquement")
    async def inventory(self, interaction: discord.Interaction, public: bool = False) -> None:
        """Display the calling user's inventory."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("Aucune session active.", ephemeral=True)
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        inv = session.inventories.get(user_id)
        if char is None or inv is None:
            await interaction.response.send_message(
                "Tu n'as pas de personnage. Utilise `/create_character`.", ephemeral=True,
            )
            return

        embed = build_inventory_embed(inv, char)
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @app_commands.command(name="equip", description="Equipe un objet")
    @app_commands.describe(item="Nom de l'objet", slot="Emplacement (Main Hand, Off Hand, Armor, etc.)")
    async def equip(self, interaction: discord.Interaction, item: str, slot: str) -> None:
        """Equip an item from the backpack into an equipment slot."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("Aucune session active.", ephemeral=True)
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        inv = session.inventories.get(user_id)
        if char is None or inv is None:
            await interaction.response.send_message("Tu n'as pas de personnage.", ephemeral=True)
            return

        # Validate slot
        try:
            eq_slot = EquipmentSlot(slot)
        except ValueError:
            valid_slots = ", ".join(s.value for s in EquipmentSlot)
            await interaction.response.send_message(
                f"Emplacement invalide: `{slot}`. Valides: {valid_slots}", ephemeral=True,
            )
            return

        try:
            inv = equip_item(inv, item, eq_slot)
        except (ValueError, KeyError) as e:
            await interaction.response.send_message(f"Erreur: {e}", ephemeral=True)
            return

        # Recompute AC
        dex_mod = compute_modifier(char.ability_scores.DEX)
        char.ac = compute_ac_from_equipment(inv.equipped, dex_mod)

        session.inventories[user_id] = inv
        await interaction.response.send_message(
            f"**{item}** equipe en **{eq_slot.value}**.", ephemeral=True,
        )

    @app_commands.command(name="unequip", description="Desequipe un emplacement")
    @app_commands.describe(slot="Emplacement a liberer (Main Hand, Off Hand, Armor, etc.)")
    async def unequip(self, interaction: discord.Interaction, slot: str) -> None:
        """Unequip an item from an equipment slot back to the backpack."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("Aucune session active.", ephemeral=True)
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        inv = session.inventories.get(user_id)
        if char is None or inv is None:
            await interaction.response.send_message("Tu n'as pas de personnage.", ephemeral=True)
            return

        try:
            eq_slot = EquipmentSlot(slot)
        except ValueError:
            valid_slots = ", ".join(s.value for s in EquipmentSlot)
            await interaction.response.send_message(
                f"Emplacement invalide: `{slot}`. Valides: {valid_slots}", ephemeral=True,
            )
            return

        try:
            inv = unequip_item(inv, eq_slot)
        except (ValueError, KeyError) as e:
            await interaction.response.send_message(f"Erreur: {e}", ephemeral=True)
            return

        dex_mod = compute_modifier(char.ability_scores.DEX)
        char.ac = compute_ac_from_equipment(inv.equipped, dex_mod)

        session.inventories[user_id] = inv
        await interaction.response.send_message(
            f"Emplacement **{eq_slot.value}** libere.", ephemeral=True,
        )

    @app_commands.command(name="use_item", description="Utilise un objet consommable")
    @app_commands.describe(item="Nom de l'objet a utiliser")
    async def use_item(self, interaction: discord.Interaction, item: str) -> None:
        """Use (consume) an item, removing it from inventory."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message("Aucune session active.", ephemeral=True)
            return

        user_id = interaction.user.id
        char = session.characters.get(user_id)
        inv = session.inventories.get(user_id)
        if char is None or inv is None:
            await interaction.response.send_message("Tu n'as pas de personnage.", ephemeral=True)
            return

        try:
            inv, used_item = remove_item(inv, item)
        except (ValueError, KeyError) as e:
            await interaction.response.send_message(f"Erreur: {e}", ephemeral=True)
            return

        session.inventories[user_id] = inv
        await interaction.response.send_message(
            f"**{used_item.name}** utilise.", ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(InventoryCog(bot))  # type: ignore[arg-type]
