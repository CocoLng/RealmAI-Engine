"""Combat action buttons — attack, cast spell, defend, flee.

Only the active player (whose turn it is) can interact with the view.
After a button press, ``self.action`` holds the chosen action string
so the combat cog can read it.
"""

from __future__ import annotations

import discord
from discord import ui

from bot.game_session import GameSession


class CombatView(ui.View):
    """Four combat action buttons. Only the active player can interact."""

    timeout = 300.0  # 5 minutes

    def __init__(self, session: GameSession, active_user_id: int) -> None:
        super().__init__(timeout=self.timeout)
        self.session = session
        self.active_user_id = active_user_id
        self.action: str | None = None

        # Disable cast_spell if player is not a spellcaster
        spellcaster = session.spellcasters.get(active_user_id)
        if spellcaster is None:
            self.cast_spell.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the active player can press buttons."""
        if interaction.user.id != self.active_user_id:
            await interaction.response.send_message(
                "Ce n'est pas ton tour !", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Attaquer", style=discord.ButtonStyle.danger, emoji="\u2694\ufe0f")
    async def attack(
        self, interaction: discord.Interaction, button: ui.Button["CombatView"]
    ) -> None:
        """Choose the attack action."""
        self.action = "attack"
        await interaction.response.defer()
        self.stop()

    @ui.button(
        label="Lancer sort", style=discord.ButtonStyle.primary, emoji="\u2728"
    )
    async def cast_spell(
        self, interaction: discord.Interaction, button: ui.Button["CombatView"]
    ) -> None:
        """Choose the cast-spell action."""
        self.action = "cast_spell"
        await interaction.response.defer()
        self.stop()

    @ui.button(
        label="Defendre", style=discord.ButtonStyle.secondary, emoji="\U0001f6e1\ufe0f"
    )
    async def defend(
        self, interaction: discord.Interaction, button: ui.Button["CombatView"]
    ) -> None:
        """Choose the defend action."""
        self.action = "defend"
        await interaction.response.defer()
        self.stop()

    @ui.button(
        label="Fuir", style=discord.ButtonStyle.secondary, emoji="\U0001f3c3"
    )
    async def flee(
        self, interaction: discord.Interaction, button: ui.Button["CombatView"]
    ) -> None:
        """Choose the flee action."""
        self.action = "flee"
        await interaction.response.defer()
        self.stop()
