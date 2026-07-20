"""Confirmation Oui/Reformuler quand l'interpreter doute (confiance basse).

Affichée quand le pipeline retourne un ``LowConfidenceResult`` : le joueur
valide l'interprétation avant qu'elle s'exécute — un tour n'est jamais
consommé sur une lecture douteuse (leçon H11). Timeout et Reformuler laissent
``confirmed`` à False : le cog annule sans toucher à l'état du jeu.
"""

from __future__ import annotations

import discord
from discord import ui

from ai.models import InterpretedAction
from engine.validators import ActionType

_CONFIRM_COLOR = 0xF5A623

_FR_TEMPLATES: dict[ActionType, str] = {
    ActionType.ATTACK: "Attaque sur {target}",
    ActionType.MOVE: "Déplacement vers {target}",
    ActionType.TALK: "Parler à {target}",
    ActionType.PICKUP: "Ramasser {target}",
    ActionType.USE_ITEM: "Utiliser {target}",
    ActionType.SEARCH: "Fouiller {target}",
    ActionType.INTERACT: "Interagir avec {target}",
}

_EN_TEMPLATES: dict[ActionType, str] = {
    ActionType.ATTACK: "Attack {target}",
    ActionType.MOVE: "Move to {target}",
    ActionType.TALK: "Talk to {target}",
    ActionType.PICKUP: "Pick up {target}",
    ActionType.USE_ITEM: "Use {target}",
    ActionType.SEARCH: "Search {target}",
    ActionType.INTERACT: "Interact with {target}",
}


def describe_action(action: InterpretedAction, language: str = "fr") -> str:
    """Résumé humain d'une InterpretedAction pour l'embed de confirmation."""
    if action.action_type is ActionType.IMPROVISE:
        detail = action.improvise_description or action.raw_input
        prefix = "Improvisation : " if language == "fr" else "Improvise: "
        return f"{prefix}{detail}"

    templates = _FR_TEMPLATES if language == "fr" else _EN_TEMPLATES
    target = action.target_name or action.item_name
    template = templates.get(action.action_type)
    if template is not None and target:
        return template.format(target=target)
    if target:
        return f"{action.action_type.value} → {target}"
    return action.action_type.value


def build_confirm_embed(
    action: InterpretedAction, language: str = "fr",
) -> discord.Embed:
    """Embed « J'ai compris : X. C'est bien ça ? » de la vue de confirmation."""
    summary = describe_action(action, language)
    if language == "fr":
        title = "Confirme ton action"
        description = f"J'ai compris : **{summary}**\n\nC'est bien ça ?"
    else:
        title = "Confirm your action"
        description = f"I understood: **{summary}**\n\nIs that right?"
    return discord.Embed(
        title=title, description=description, color=_CONFIRM_COLOR,
    )


class ConfirmActionView(ui.View):
    """Boutons Oui / Reformuler. Seul l'auteur de l'action peut cliquer."""

    timeout: float = 120.0  # 2 minutes, aligné sur ClarificationView

    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=self.timeout)
        self.author_id = author_id
        self.confirmed: bool = False

        yes_button: ui.Button["ConfirmActionView"] = ui.Button(
            label="Oui",
            style=discord.ButtonStyle.success,
            emoji="✅",
            custom_id="confirm_yes",
        )
        yes_button.callback = self._on_yes
        self.add_item(yes_button)

        redo_button: ui.Button["ConfirmActionView"] = ui.Button(
            label="Reformuler",
            style=discord.ButtonStyle.secondary,
            emoji="✏️",
            custom_id="confirm_redo",
        )
        redo_button.callback = self._on_redo
        self.add_item(redo_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul le joueur qui a lancé l'action peut confirmer.",
                ephemeral=True,
            )
            return False
        return True

    async def _on_yes(self, interaction: discord.Interaction) -> None:
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    async def _on_redo(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
