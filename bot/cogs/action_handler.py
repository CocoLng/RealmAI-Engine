"""ActionHandlerCog — listens for @bot mentions in campaign channels.

Filters messages, then runs them through :class:`bot.action_pipeline.ActionPipeline`
and renders the progress / result / clarification UI.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from bot.action_pipeline import (
    ActionPipeline,
    ActionPipelineResult,
    AmbiguityResult,
    PipelinePhase,
    UnknownEntityResult,
)
from bot.embeds.action_progress_embed import build_action_progress_embed
from bot.embeds.beat_embed import build_beat_advance_embed
from bot.embeds.narrative_embed import build_narrative_embed
from bot.embeds.scene_embed import build_scene_embed
from bot.views.clarification_view import (
    ClarificationView,
    build_clarification_embed,
)
from engine.validators import ActionType

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)

_MENTION_RE = re.compile(r"<@!?(\d+)>")

# Short OOC reactions that should not trigger the LLM pipeline.
_OOC_NOISE: frozenset[str] = frozenset({
    "ok", "okay", "merci", "thanks", "thank you", "gg", "lol", "haha",
    "nice", "cool", "hi", "hello", "salut", "yo", "wtf", "wow",
})


def looks_like_action(text: str) -> bool:
    """Heuristic: does ``text`` look like a player action worth interpreting?

    Returns ``False`` for empty text, very short messages, or known OOC
    interjections. The goal is to avoid spending an LLM call on messages
    that are obviously not in-character actions.
    """
    stripped = text.strip()
    if len(stripped) < 4:
        return False
    if stripped.lower() in _OOC_NOISE:
        return False
    return True


def _strip_bot_mention(content: str, bot_user_id: int) -> str:
    """Remove the leading @bot mention(s) from the message content."""
    cleaned = _MENTION_RE.sub(
        lambda m: "" if int(m.group(1)) == bot_user_id else m.group(0),
        content,
    )
    return cleaned.strip()


class ActionHandlerCog(commands.Cog):
    """Routes free-text @mention messages to the action pipeline."""

    def __init__(self, bot: "RealmBot") -> None:
        self.bot = bot
        # Allow tests to inject a fake pipeline factory.
        self._pipeline_factory: Any = ActionPipeline

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # 1. Skip the bot's own messages.
        if message.author.bot:
            return

        # 2. Channel must host an active campaign.
        session = self.bot.sessions.get(message.channel.id)
        if session is None:
            return

        # 3. The bot must be mentioned.
        if self.bot.user is None or self.bot.user not in message.mentions:
            return

        # 4. Author must be a registered player in the session.
        if message.author.id not in session.characters:
            await message.reply(
                "Tu n'as pas de personnage dans cette campagne. "
                "Utilise `/create_character` pour rejoindre la partie.",
            )
            return

        # 5. AI must be available.
        if session.interpreter is None or session.narrator is None:
            await message.reply(
                "⚠️ Le Game Master est indisponible (Ollama injoignable). "
                "Réessaie dans un instant ou utilise les commandes `/look`, "
                "`/search`, `/talk`, `/move` en attendant.",
            )
            return

        raw_text = _strip_bot_mention(message.content, self.bot.user.id)

        # 6. Heuristic OOC filter — no LLM for short / noisy messages.
        if not looks_like_action(raw_text):
            await message.reply(
                "Je suis le Game Master de cette campagne. Mentionne-moi avec "
                "une action que tu veux entreprendre (ex : *fouiller l'autel*, "
                "*parler au prêtre*, *entrer dans la cathédrale*).",
            )
            return

        # 7. Serialize actions per session — refuse if one is in progress.
        if session.action_lock.locked():
            await message.reply(
                "⏳ Une action est déjà en cours de résolution. "
                "Attends que la scène soit terminée puis renvoie ton message.",
            )
            return

        async with session.action_lock:
            await self._run_pipeline(message, session, raw_text)

    # ------------------------------------------------------------------
    # Pipeline orchestration
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        message: discord.Message,
        session: Any,
        raw_text: str,
    ) -> None:
        actor = session.characters[message.author.id]
        actor_name = actor.name
        start = time.monotonic()

        # 1. Post the initial progress embed.
        progress_embed = build_action_progress_embed(
            actor_name=actor_name,
            raw_text=raw_text,
            current_phase=PipelinePhase.INTERPRETING,
            elapsed_seconds=0.0,
        )
        progress_msg = await message.channel.send(embed=progress_embed)

        async def update_progress(phase: PipelinePhase) -> None:
            try:
                await progress_msg.edit(
                    embed=build_action_progress_embed(
                        actor_name=actor_name,
                        raw_text=raw_text,
                        current_phase=phase,
                        elapsed_seconds=time.monotonic() - start,
                    ),
                )
            except discord.HTTPException:
                logger.warning(
                    "ACTION progress edit failed campaign=%s phase=%s",
                    session.campaign.id, phase.name,
                )

        # 2. Build the pipeline for this action.
        pipeline = self._pipeline_factory(
            interpreter=session.interpreter,
            narrator=session.narrator,
            location=session.current_location,
            npcs=session.npcs,
            actor_name=actor_name,
            language=session.language,
            campaign_id=session.campaign.id,
            combat_state=session.combat_state,
            inventory=session.inventories.get(message.author.id),
            session=session,
            db_factory=self.bot.db_factory,
        )

        logger.info(
            "ACTION received user=%s campaign=%s text=%r",
            message.author, session.campaign.id, raw_text[:100],
        )

        # 3. Run.
        try:
            result = await pipeline.process(
                player_text=raw_text,
                progress_callback=update_progress,
            )
        except Exception as exc:
            logger.exception(
                "ACTION failed campaign=%s reason=%s",
                session.campaign.id, exc,
            )
            await progress_msg.edit(
                embed=build_action_progress_embed(
                    actor_name=actor_name,
                    raw_text=raw_text,
                    current_phase=PipelinePhase.FAILED,
                    elapsed_seconds=time.monotonic() - start,
                ),
            )
            await message.channel.send(
                "❌ Le Game Master n'a pas pu répondre. Réessaie dans un instant.",
            )
            return

        # 4. Dispatch on result type.
        if isinstance(result, ActionPipelineResult):
            await self._render_success(progress_msg, result)
            # Lot A — scene awareness: re-display the scene after a MOVE so
            # players keep their bearings. NOTE: under Lot A alone,
            # session.current_location does not yet change on MOVE — Lot D
            # will activate the actual location swap. Until then this
            # re-posts the same scene, which is harmless and still useful as
            # a refresher.
            try:
                if (
                    result.interpreted_action.action_type == ActionType.MOVE
                    and session.current_location is not None
                ):
                    await message.channel.send(
                        embed=build_scene_embed(
                            location=session.current_location,
                            language=session.language,
                        ),
                    )
                    logger.info(
                        "SCENE posted-after-move campaign=%s location=%s",
                        session.campaign.id,
                        session.current_location.name,
                    )
            except AttributeError:
                logger.debug(
                    "SCENE post-after-move skipped: missing action attributes",
                )
            # Lot D — celebrate beat progression with a "Nouveau chapitre" embed.
            if result.new_beat is not None and session.story_arc is not None:
                try:
                    await message.channel.send(
                        embed=build_beat_advance_embed(
                            beat=result.new_beat,
                            total_beats=len(session.story_arc.beats),
                            language=session.language,
                        ),
                    )
                    logger.info(
                        "BEAT embed posted campaign=%s beat=%d",
                        session.campaign.id,
                        result.new_beat.beat_number,
                    )
                except Exception:
                    logger.exception(
                        "BEAT embed post failed campaign=%s",
                        session.campaign.id,
                    )
        elif isinstance(result, AmbiguityResult):
            await self._render_ambiguity(
                progress_msg, result, message.author.id, pipeline,
                actor_name=actor_name, raw_text=raw_text, start=start,
                session=session,
            )
        elif isinstance(result, UnknownEntityResult):
            await self._render_unknown(progress_msg, result)

        logger.info(
            "ACTION done campaign=%s elapsed=%.1fs",
            session.campaign.id, time.monotonic() - start,
        )

    async def _render_success(
        self,
        progress_msg: discord.Message,
        result: ActionPipelineResult,
    ) -> None:
        embed = build_narrative_embed(
            narrative=result.narrative,
            mechanics=result.mechanics_text,
            tone=result.tone,
        )
        await progress_msg.edit(embed=embed, view=None)

    async def _render_unknown(
        self,
        progress_msg: discord.Message,
        result: UnknownEntityResult,
    ) -> None:
        embed = build_narrative_embed(
            narrative=result.refusal_narrative,
            mechanics=f"⚠️ {result.field_name}: '{result.raw_value}' introuvable.",
            tone=result.tone,
        )
        await progress_msg.edit(embed=embed, view=None)

    async def _render_ambiguity(
        self,
        progress_msg: discord.Message,
        ambiguity: AmbiguityResult,
        author_id: int,
        pipeline: Any,
        *,
        actor_name: str,
        raw_text: str,
        start: float,
        session: Any,
    ) -> None:
        embed = build_clarification_embed(ambiguity)
        view = ClarificationView(ambiguity, author_id=author_id)
        await progress_msg.edit(embed=embed, view=view)

        # Wait for the user to click a button (or timeout).
        await view.wait()

        if view.cancelled:
            await progress_msg.edit(
                embed=build_action_progress_embed(
                    actor_name=actor_name,
                    raw_text=raw_text,
                    current_phase=PipelinePhase.FAILED,
                    elapsed_seconds=time.monotonic() - start,
                ),
                view=None,
            )
            return

        if view.chosen_entity_id is None:
            # Timeout
            await progress_msg.edit(
                embed=build_action_progress_embed(
                    actor_name=actor_name,
                    raw_text=raw_text,
                    current_phase=PipelinePhase.FAILED,
                    elapsed_seconds=time.monotonic() - start,
                ),
                view=None,
            )
            return

        async def update_progress(phase: PipelinePhase) -> None:
            try:
                await progress_msg.edit(
                    embed=build_action_progress_embed(
                        actor_name=actor_name,
                        raw_text=raw_text,
                        current_phase=phase,
                        elapsed_seconds=time.monotonic() - start,
                    ),
                    view=None,
                )
            except discord.HTTPException:
                logger.warning(
                    "ACTION progress edit failed (resume) campaign=%s phase=%s",
                    session.campaign.id, phase.name,
                )

        try:
            result = await pipeline.resume_with_resolution(
                ambiguity, view.chosen_entity_id,
                progress_callback=update_progress,
            )
        except Exception as exc:
            logger.exception(
                "ACTION resume failed campaign=%s reason=%s",
                session.campaign.id, exc,
            )
            await progress_msg.edit(
                embed=build_action_progress_embed(
                    actor_name=actor_name,
                    raw_text=raw_text,
                    current_phase=PipelinePhase.FAILED,
                    elapsed_seconds=time.monotonic() - start,
                ),
                view=None,
            )
            return

        if isinstance(result, ActionPipelineResult):
            await self._render_success(progress_msg, result)
        elif isinstance(result, UnknownEntityResult):
            await self._render_unknown(progress_msg, result)
        # An ambiguity here would be unusual — fall through to leave the
        # current embed in place rather than crash.


async def setup(bot: "RealmBot") -> None:
    """discord.py extension entry point."""
    await bot.add_cog(ActionHandlerCog(bot))
