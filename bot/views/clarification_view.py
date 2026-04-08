"""Disambiguation buttons shown when entity resolution returns multiple matches.

Only the player who triggered the action can press the buttons. After a
selection (or timeout / cancel), the view stops and stores the chosen entity
id on ``self.chosen_entity_id`` so the cog can resume the pipeline.
"""

from __future__ import annotations

import discord
from discord import ui

from bot.action_pipeline import AmbiguityResult

_AMBIGUOUS_COLOR = 0xF5A623


class ClarificationView(ui.View):
    """Up to 4 candidate buttons + cancel. Only the original author can click."""

    timeout: float = 120.0  # 2 minutes

    def __init__(
        self,
        ambiguity: AmbiguityResult,
        author_id: int,
    ) -> None:
        super().__init__(timeout=self.timeout)
        self.author_id = author_id
        self.chosen_entity_id: str | None = None
        self.cancelled: bool = False

        for candidate in ambiguity.candidates[:4]:
            self.add_item(_CandidateButton(candidate.id, candidate.label))

        cancel_button: ui.Button["ClarificationView"] = ui.Button(
            label="Annuler",
            style=discord.ButtonStyle.secondary,
            emoji="\u274c",
            custom_id="clarify_cancel",
        )
        cancel_button.callback = self._on_cancel  # type: ignore[method-assign]
        self.add_item(cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seul le joueur qui a lancé l'action peut faire ce choix.",
                ephemeral=True,
            )
            return False
        return True

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.cancelled = True
        await interaction.response.defer()
        self.stop()


class _CandidateButton(ui.Button["ClarificationView"]):
    def __init__(self, entity_id: str, label: str) -> None:
        super().__init__(
            label=label[:80],
            style=discord.ButtonStyle.primary,
            custom_id=f"clarify_{entity_id}",
        )
        self.entity_id = entity_id

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert view is not None
        view.chosen_entity_id = self.entity_id
        await interaction.response.defer()
        view.stop()


def build_clarification_embed(ambiguity: AmbiguityResult) -> discord.Embed:
    """Build the embed shown alongside the ClarificationView."""
    embed = discord.Embed(
        title="Précise ta cible",
        description=(
            f'Plusieurs cibles correspondent à "{ambiguity.raw_value}". '
            "Choisis laquelle :"
        ),
        color=_AMBIGUOUS_COLOR,
    )
    for candidate in ambiguity.candidates[:4]:
        value = candidate.description or "\u200b"
        embed.add_field(
            name=candidate.label,
            value=value,
            inline=False,
        )
    return embed
