"""/hint slash command — three progressive hint levels.

Level 1: deterministic, free, unlimited (vague hint).
Level 2: deterministic, 1 use per beat (objective list).
Level 3: BeatJudge LLM verbose, 5-turn cooldown after use (concrete actions).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from db.repositories.hint_usage_repo import HintUsageRepository


class HintCog(commands.Cog):
    """Slash command /hint with progressive guidance levels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ----- helpers (overridable in tests) -----

    def _get_session(self, channel_id: int) -> "GameSession | None":
        """Lookup the active GameSession for a channel."""
        sessions = getattr(self.bot, "sessions", {})
        return sessions.get(channel_id)

    def _get_repo(self) -> "HintUsageRepository":
        """Build a HintUsageRepository on a fresh DB session."""
        from db.repositories.hint_usage_repo import HintUsageRepository
        db_factory = getattr(self.bot, "db_factory", None)
        if db_factory is None:
            raise RuntimeError("Bot is missing db_factory")
        db_session = db_factory()
        return HintUsageRepository(db_session)

    def _build_judge(self, session: "GameSession"):  # type: ignore[return]
        """Build a BeatJudge bound to this session's Ollama client.

        Overridable in tests.
        """
        from ai.beat_judge import BeatJudge
        if session.ollama_client is None:
            raise RuntimeError("BeatJudge requires an OllamaClient")
        return BeatJudge(session.ollama_client)

    # ----- commands -----

    @app_commands.command(name="hint", description="Demande un indice pour avancer")
    @app_commands.describe(
        public="Afficher l'indice à tout le groupe (par défaut: éphémère)",
    )
    async def hint(
        self,
        interaction: discord.Interaction,
        public: bool = False,
    ) -> None:
        """Three-level progressive hint, escalates on repeat use within a beat."""
        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.response.send_message(
                "Cette commande n'est pas disponible dans ce contexte.",
                ephemeral=True,
            )
            return
        session = self._get_session(channel_id)
        if session is None or session.story_arc is None:
            await interaction.response.send_message(
                "Aucune campagne active dans ce salon.", ephemeral=True,
            )
            return

        arc = session.story_arc
        if arc.current_beat_index >= len(arc.beats):
            await interaction.response.send_message(
                "L'arc est terminé — plus rien à découvrir.", ephemeral=True,
            )
            return
        beat = arc.beats[arc.current_beat_index]

        repo = self._get_repo()
        try:
            await self._dispatch_levels(interaction, session, beat, repo, public)
        finally:
            # One DB session is opened per invocation (H2) — close it.
            self._close_repo(repo)

    async def _dispatch_levels(  # type: ignore[no-untyped-def]
        self, interaction, session, beat, repo, public,
    ) -> None:
        """Pick the hint level from usage state and send the response."""
        row = repo.get_or_create(
            campaign_id=session.campaign.id, beat_number=beat.beat_number,
        )

        # Level decision:
        # - never used L2 yet AND no L1 use → L1
        # - used L1 (≥1 time) but never L2 → L2
        # - used L2 already → L3 (subject to cooldown)
        if not row.level2_used and row.level1_uses == 0:
            text = self._build_level1(beat)
            repo.increment_level1(
                campaign_id=session.campaign.id, beat_number=beat.beat_number,
            )
            await interaction.response.send_message(
                text + "\n\n💡 Niveau 1", ephemeral=not public,
            )
            return

        if not row.level2_used:
            text = self._build_level2(beat)
            repo.set_level2_used(
                campaign_id=session.campaign.id, beat_number=beat.beat_number,
            )
            await interaction.response.send_message(
                text + "\n\n💡 Niveau 2", ephemeral=not public,
            )
            return

        # Level 3: cooldown check first. The turn counter lives on the
        # Campaign model, NOT on GameSession.
        current_turn = getattr(session.campaign, "interaction_count", 0) or 0
        cooldown = 5
        if (
            row.level3_last_used_turn is not None
            and current_turn - row.level3_last_used_turn < cooldown
        ):
            remaining = cooldown - (current_turn - row.level3_last_used_turn)
            await interaction.response.send_message(
                f"💡 Niveau 3 indisponible — réessaie dans {remaining} tour(s).",
                ephemeral=True,
            )
            return

        # Level 3: defer (LLM call may take 1-3s)
        await interaction.response.defer(ephemeral=not public)
        text = await self._build_level3(beat, session)
        repo.set_level3_last_used_turn(
            campaign_id=session.campaign.id,
            beat_number=beat.beat_number,
            turn=current_turn,
        )
        await interaction.followup.send(text + "\n\n💡 Niveau 3", ephemeral=not public)

    @staticmethod
    def _close_repo(repo: "HintUsageRepository") -> None:
        """Close the DB session backing ``repo``.

        The repository does not expose its session publicly; reaching for
        ``_session`` here keeps db/* untouched (owned by another chantier).
        """
        db_session = getattr(repo, "_session", None)
        if db_session is not None:
            db_session.close()

    # ----- level builders -----

    def _build_level1(self, beat) -> str:  # type: ignore[no-untyped-def]
        """Vague, in-character hint. Falls back to first sentence of description."""
        if beat.player_visible_hint:
            return beat.player_visible_hint
        first_sentence = beat.description.split(".", 1)[0].strip()
        if first_sentence:
            return first_sentence + "."
        return "Tu sens que quelque chose t'attend par ici."

    def _build_level2(self, beat) -> str:  # type: ignore[no-untyped-def]
        """List of pending/partial objective descriptions."""
        if not beat.objectives:
            return "Aucun objectif identifié pour ce beat."
        lines = ["Voici ce qu'il te reste à faire :"]
        for obj in beat.objectives:
            lines.append(f"◯ {obj.description}")
        return "\n".join(lines)

    async def _build_level3(self, beat, session) -> str:  # type: ignore[no-untyped-def]
        """Run the BeatJudge in verbose mode and format its reasoning."""
        from engine.beat_progression import JudgeRequest, ObjectivePartialMatch
        from ai.models import InterpretedAction
        from engine.validators import ActionType

        partial = [
            ObjectivePartialMatch(
                id=obj.id, kind=obj.kind, target=obj.target,
                description=obj.description, match_score=0.0,
                gate_failed=False, gate_kind=None,
            )
            for obj in beat.objectives
        ]
        synthetic_action = InterpretedAction(
            action_type=ActionType.QUESTION,
            actor_name="hero",
            raw_input="(joueur demande un indice via /hint niveau 3)",
        )
        req = JudgeRequest(
            beat_title=beat.title,
            beat_description=beat.description,
            beat_judge_rubric=beat.judge_rubric,
            objectives=partial,
            player_action_text=synthetic_action.raw_input,
            interpreted_action=synthetic_action,
            outcome_summary="",
            location_name=session.current_location.name if session.current_location else None,
            npcs_present=[],
        )
        judge = self._build_judge(session)
        judge.begin_turn(
            turn_id=f"hint-{getattr(session.campaign, 'interaction_count', 0)}",
        )
        # evaluate() wraps a blocking httpx POST — keep it off the event
        # loop (H2).
        resp = await asyncio.to_thread(judge.evaluate, req)
        if resp.suggested_next_action:
            return (
                f"Pour avancer :\n"
                f"• {resp.suggested_next_action}\n\n"
                f"_{resp.reasoning}_"
            )
        return f"_{resp.reasoning or 'Mes pensées s embrouillent — réessaie autre chose.'}_"


async def setup(bot: commands.Bot) -> None:
    """Register HintCog with the bot."""
    await bot.add_cog(HintCog(bot))
