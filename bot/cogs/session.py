"""Session cog -- campaign lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import discord
from discord import app_commands
from discord.ext import commands

from bot.campaign_launcher import CampaignLauncher
from bot.config import GuildConfig
from bot.game_session import GameSession, create_ai_services
from bot.persistence import persist_session
from bot.utils.channel_manager import archive_channel, create_session_channel
from db.repositories import (
    CampaignChannelRepository,
    CampaignRepository,
    GuildConfigRepository,
    LocationRepository,
    NPCRepository,
    PlayerCharacterRepository,
    QuestRepository,
    StoryArcRepository,
)
from world.campaign import Campaign

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class _CampaignChannelArcStore:
    """Adapts CampaignChannelRepository to the ArcTrackerStore Protocol.

    Holds a db_factory callable rather than a session so each call opens
    and commits its own short-lived session — consistent with how other
    repo calls in this cog work.
    """

    def __init__(self, db_factory: Callable[[], Any]) -> None:
        self._db_factory = db_factory

    def get_message_id(self, channel_id: int) -> int | None:
        """Return the stored Arc Tracker message ID, or None."""
        from db.repositories.campaign_channel_repo import CampaignChannelRepository as _Repo
        db_session = self._db_factory()
        try:
            return _Repo(db_session).get_arc_tracker_message_id(channel_id)
        finally:
            db_session.close()

    def set_message_id(self, channel_id: int, message_id: int | None) -> None:
        """Persist the Arc Tracker message ID (or clear it with None)."""
        from db.repositories.campaign_channel_repo import CampaignChannelRepository as _Repo
        db_session = self._db_factory()
        try:
            _Repo(db_session).update_arc_tracker_message_id(channel_id, message_id)
            db_session.commit()
        finally:
            db_session.close()


class SessionCog(commands.Cog):
    """Campaign lifecycle: start, resume, save, end, settings."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    @staticmethod
    def _parse_mentions(players_str: str) -> list[int]:
        """Extract user IDs from a mention string like '<@123> <@456>'."""
        return [int(uid) for uid in re.findall(r"<@!?(\d+)>", players_str)]

    # ------------------------------------------------------------------
    # /start_campaign
    # ------------------------------------------------------------------

    @app_commands.command(name="start_campaign", description="Lance une nouvelle campagne")
    @app_commands.describe(
        theme="Theme de la campagne (ex: 'Foret sombre', 'Donjon ancien')",
        players="Joueurs a inviter (mentionnez-les: @Alice @Bob)",
    )
    async def start_campaign(
        self,
        interaction: discord.Interaction,
        theme: str,
        players: str,
    ) -> None:
        """Create a new campaign with a dedicated channel."""
        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("start_campaign: interaction expired before defer()")
            return

        # Parse mentioned players
        player_ids = self._parse_mentions(players)
        if not player_ids:
            await interaction.followup.send(
                "Mentionne au moins un joueur (@pseudo).", ephemeral=True,
            )
            return

        # Include invoker if not already mentioned
        if interaction.user.id not in player_ids:
            player_ids.insert(0, interaction.user.id)

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "Cette commande doit etre utilisee dans un serveur.", ephemeral=True,
            )
            return

        # Build campaign model
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name=theme,
            created_at=datetime.now(timezone.utc),
            player_names=[str(uid) for uid in player_ids],
        )

        # Resolve Member objects for channel permissions
        player_members: list[discord.Member] = []
        for uid in player_ids:
            member = guild.get_member(uid)
            if member:
                player_members.append(member)

        # Atomic: persist campaign + create channel + persist mapping
        db_session = self.bot.db_factory()
        channel: discord.TextChannel | None = None
        try:
            guild_config_repo = GuildConfigRepository(db_session)
            config = guild_config_repo.get(guild.id)
            category_name = config.category_name if config else "RealmAI Sessions"
            language = config.language if config else "fr"

            campaign_repo = CampaignRepository(db_session)
            campaign_repo.save(campaign)
            db_session.flush()  # allocate DB resources, don't commit yet

            channel = await create_session_channel(
                guild, theme, player_members, guild.me, category_name,
            )

            channel_repo = CampaignChannelRepository(db_session)
            channel_repo.save(channel.id, campaign.id, guild.id)
            db_session.commit()
        except Exception:
            db_session.rollback()
            if channel is not None:
                try:
                    await channel.delete(reason="Rollback: start_campaign failed")
                except Exception:
                    logger.error("Failed to cleanup orphan channel %s", channel.id)
            raise
        finally:
            db_session.close()

        # Create launcher (orchestrates onboarding before gameplay)
        launcher = CampaignLauncher(
            bot=self.bot,
            campaign=campaign,
            channel=channel,
            player_ids=player_ids,
            language=language,
            creator_id=interaction.user.id,
        )
        self.bot.launchers[channel.id] = launcher

        # Start background AI tasks (arc + location generation)
        launcher.start_background_tasks()

        player_mentions = ", ".join(str(uid) for uid in player_ids)
        logger.info(
            "SESSION start campaign=%s theme=%r players=[%s] guild=%s channel=%s",
            campaign.id, theme, player_mentions,
            guild.name, channel.name,
        )

        # Send welcome embed + onboarding view in the new channel
        await launcher.start()

        # Arc Tracker pin — best effort, never blocks campaign creation
        try:
            from bot.utils.arc_tracker import ArcTrackerData, ArcTrackerManager
            store = _CampaignChannelArcStore(self.bot.db_factory)
            manager = ArcTrackerManager(store=store)
            await manager.ensure_pinned(
                channel=channel,
                campaign_id=campaign.id,
                channel_id=channel.id,
                data=ArcTrackerData(
                    chapter_title="Chapitre 1 — Début de la campagne",
                    current_objective="Découvrez le monde et le pourquoi de votre quête.",
                    recent_beats=[],
                    active_quests=[],
                    last_updated_relative="à l'instant",
                ),
            )
        except Exception:
            logger.warning("Failed to pin Arc Tracker on /start_campaign", exc_info=True)

        await interaction.followup.send(f"Campagne lancee dans {channel.mention} !")

    # ------------------------------------------------------------------
    # /resume
    # ------------------------------------------------------------------

    @app_commands.command(name="resume", description="Reprend la derniere session sauvegardee")
    async def resume(self, interaction: discord.Interaction) -> None:
        """Reload a saved campaign into memory for the current channel."""
        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("resume: interaction expired before defer()")
            return

        channel_id = interaction.channel_id
        if channel_id is None:
            await interaction.followup.send(
                "Impossible de determiner le canal.", ephemeral=True,
            )
            return

        # Already active?
        if self.bot.get_session(channel_id):
            await interaction.followup.send(
                "Une session est deja active dans ce canal.", ephemeral=True,
            )
            return

        # Load campaign from DB via channel mapping
        db_session = self.bot.db_factory()
        try:
            channel_repo = CampaignChannelRepository(db_session)
            mapping = channel_repo.get_by_channel(channel_id)
            if mapping is None:
                await interaction.followup.send(
                    "Aucune campagne associee a ce canal. Utilise `/start_campaign`.",
                    ephemeral=True,
                )
                return
            campaign_id, guild_id = mapping

            # Read language from guild config
            guild_config_repo = GuildConfigRepository(db_session)
            guild_config = guild_config_repo.get(guild_id)
            language = guild_config.language if guild_config else "fr"

            campaign_repo = CampaignRepository(db_session)
            campaign = campaign_repo.get_by_id(campaign_id)
            if campaign is None:
                await interaction.followup.send(
                    "Campagne introuvable.", ephemeral=True,
                )
                return

            # Load player characters
            pc_repo = PlayerCharacterRepository(db_session)
            pc_rows = pc_repo.get_all_for_campaign(campaign_id)

            # Load location
            location = None
            if campaign.current_location:
                loc_repo = LocationRepository(db_session)
                location = loc_repo.get_by_name(campaign.current_location, campaign_id)

            # Load NPCs
            npc_repo = NPCRepository(db_session)
            npcs = npc_repo.list_by_campaign(campaign_id)

            # Load quests
            quest_repo = QuestRepository(db_session)
            quests = quest_repo.list_by_campaign(campaign_id)

            # Load story arc
            arc_repo = StoryArcRepository(db_session)
            story_arc = arc_repo.get_by_campaign(campaign_id)
        finally:
            db_session.close()

        # Restore combat state from JSON
        combat_state = None
        if campaign.combat_state_json:
            from engine.combat import CombatState
            combat_state = CombatState.model_validate_json(campaign.combat_state_json)

        # Rebuild in-memory session
        session = GameSession(
            campaign=campaign,
            current_location=location,
            combat_state=combat_state,
            npcs={npc.name: npc for npc in npcs},
            quests=quests,
            story_arc=story_arc,
            language=language,
        )
        for user_id, char, inv, spell in pc_rows:
            session.characters[user_id] = char
            session.inventories[user_id] = inv
            session.spellcasters[user_id] = spell

        create_ai_services(session)
        self.bot.sessions[channel_id] = session

        player_count = len(session.characters)
        combat_msg = " (combat en cours !)" if combat_state else ""
        npc_count = len(session.npcs)
        quest_count = len(session.quests)
        logger.info(
            "SESSION resume campaign=%s channel=%s characters=%d npcs=%d quests=%d combat=%s",
            campaign.id, channel_id, player_count, npc_count, quest_count,
            combat_state is not None,
        )

        await interaction.followup.send(
            f"Session reprise ! Campagne **{campaign.name}** "
            f"-- {player_count} personnage(s), {npc_count} PNJ(s), {quest_count} quete(s){combat_msg}.",
        )

        # Surface AI initialization warnings to the campaign channel.
        if session.ai_warnings and interaction.channel is not None:
            for warning in session.ai_warnings:
                try:
                    await interaction.channel.send(warning)
                except Exception:
                    logger.warning("Failed to send AI warning to channel %s", channel_id)

    # ------------------------------------------------------------------
    # /save
    # ------------------------------------------------------------------

    @app_commands.command(name="save", description="Sauvegarde la partie en cours")
    async def save(self, interaction: discord.Interaction) -> None:
        """Persist the current session state to the database."""
        channel_id = interaction.channel_id
        session = self.bot.get_session(channel_id) if channel_id else None
        if session is None:
            await interaction.response.send_message(
                "Aucune session active. Utilise `/start_campaign` ou `/resume`.",
                ephemeral=True,
            )
            return

        self._persist_session(session)
        logger.info("SESSION save campaign=%s", session.campaign.id)

        await interaction.response.send_message("Partie sauvegardee !", ephemeral=True)

    # ------------------------------------------------------------------
    # /end_campaign
    # ------------------------------------------------------------------

    @app_commands.command(name="end_campaign", description="Termine et archive la campagne")
    async def end_campaign(self, interaction: discord.Interaction) -> None:
        """Save, archive the channel, and clean up the session."""
        channel_id = interaction.channel_id
        session = self.bot.get_session(channel_id) if channel_id else None
        if session is None:
            await interaction.response.send_message(
                "Aucune session active dans ce canal.", ephemeral=True,
            )
            return

        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("end_campaign: interaction expired before defer()")
            return

        # Persist before archiving
        self._persist_session(session)

        # Clean up ChromaDB collection for this campaign (L5).
        if session.semantic_memory is not None:
            try:
                session.semantic_memory.delete_campaign(session.campaign.id)
            except Exception:
                logger.warning(
                    "Failed to delete ChromaDB collection for campaign %s",
                    session.campaign.id,
                    exc_info=True,
                )

        logger.info(
            "SESSION end campaign=%s channel=%s",
            session.campaign.id, channel_id,
        )

        await interaction.followup.send(
            f"Campagne **{session.campaign.name}** terminee. Le canal sera archive.",
        )

        # Arc Tracker removal — best effort, never blocks campaign end
        channel = interaction.channel
        if channel is not None and channel_id is not None:
            try:
                from bot.utils.arc_tracker import ArcTrackerManager
                store = _CampaignChannelArcStore(self.bot.db_factory)
                manager = ArcTrackerManager(store=store)
                await manager.remove(channel=channel, channel_id=channel_id)
            except Exception:
                logger.warning("Failed to remove Arc Tracker on /end_campaign", exc_info=True)

        # Archive the channel
        guild = interaction.guild
        if channel and guild:
            await archive_channel(channel, guild)  # type: ignore[arg-type]

        # Remove from in-memory sessions
        if channel_id is not None and channel_id in self.bot.sessions:
            del self.bot.sessions[channel_id]

    # ------------------------------------------------------------------
    # /settings
    # ------------------------------------------------------------------

    @app_commands.command(name="settings", description="Configure le bot pour ce serveur")
    @app_commands.describe(
        category="Nom de la categorie Discord pour les sessions",
        language="Langue du narrateur (fr, en, es, de, pt)",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def settings(
        self,
        interaction: discord.Interaction,
        category: str | None = None,
        language: str | None = None,
    ) -> None:
        """Update the guild's bot configuration."""
        if interaction.guild is None:
            await interaction.response.send_message("Serveur requis.", ephemeral=True)
            return

        db_session = self.bot.db_factory()
        try:
            repo = GuildConfigRepository(db_session)
            existing = repo.get(interaction.guild.id)
            config = existing or GuildConfig(guild_id=interaction.guild.id)

            if category is not None:
                config = config.model_copy(update={"category_name": category})
            if language is not None:
                config = config.model_copy(update={"language": language})

            repo.upsert(config)
            db_session.commit()
        finally:
            db_session.close()

        logger.info(
            "SESSION settings guild=%s category=%r language=%s",
            interaction.guild.id, config.category_name, config.language,
        )

        if category is None and language is None:
            await interaction.response.send_message(
                f"Config actuelle: categorie=**{config.category_name}**, langue=**{config.language}**",
                ephemeral=True,
            )
        else:
            parts = []
            if category is not None:
                parts.append(f"categorie: **{category}**")
            if language is not None:
                parts.append(f"langue: **{language}**")
            await interaction.response.send_message(
                f"Mis a jour: {', '.join(parts)}", ephemeral=True,
            )

    # ------------------------------------------------------------------
    # /story_catch_up
    # ------------------------------------------------------------------

    @app_commands.command(
        name="story_catch_up",
        description="Le MJ recadre la scene — recap de l'objectif actuel et des prochaines pistes.",
    )
    async def story_catch_up(self, interaction: discord.Interaction) -> None:
        """Run the Story Director immediately and post a recap embed."""
        await interaction.response.defer(thinking=True)

        channel_id = interaction.channel_id
        session = self.bot.get_session(channel_id) if channel_id else None
        if session is None:
            await interaction.followup.send(
                "Aucune campagne active dans ce canal. Lance `/start_campaign` ou `/resume`.",
                ephemeral=True,
            )
            return

        if session.semantic_memory is None or session.story_director is None:
            await interaction.followup.send(
                "Le MJ n'a pas de memoire active pour cette campagne.",
                ephemeral=True,
            )
            return

        try:
            note = await asyncio.to_thread(
                session.story_director.check_coherence,
                session.campaign.id,
                "(catch-up request)",
            )
        except Exception:
            logger.exception(
                "story_catch_up: director failed campaign=%s", session.campaign.id,
            )
            await interaction.followup.send(
                "Le MJ n'a pas pu rassembler ses idees. Reessaie dans un instant.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Le MJ recadre la scene",
            description=note.current_objective or "Aucun objectif clair pour l'instant.",
            color=discord.Color.gold(),
        )
        if note.suggested_hooks:
            embed.add_field(
                name="Pistes possibles",
                value="\n".join(f"• {h}" for h in note.suggested_hooks[:3]),
                inline=False,
            )
        if note.next_beat_hint:
            embed.add_field(
                name="Prochaine direction",
                value=note.next_beat_hint,
                inline=False,
            )

        session.force_next_director_run = True

        logger.info(
            "story_catch_up: recap posted campaign=%s objective=%r hooks=%d",
            session.campaign.id, note.current_objective, len(note.suggested_hooks),
        )
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_session(self, session: GameSession) -> None:
        """Save campaign, characters, combat state, NPCs, and quests to DB."""
        persist_session(self.bot.db_factory, session)


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(SessionCog(bot))  # type: ignore[arg-type]
