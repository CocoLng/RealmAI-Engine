"""Session cog -- campaign lifecycle management."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.campaign_launcher import CampaignLauncher
from bot.config import GuildConfig
from bot.game_session import GameSession, create_ai_services
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
        await interaction.response.defer()

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

        await interaction.followup.send(f"Campagne lancee dans {channel.mention} !")

    # ------------------------------------------------------------------
    # /resume
    # ------------------------------------------------------------------

    @app_commands.command(name="resume", description="Reprend la derniere session sauvegardee")
    async def resume(self, interaction: discord.Interaction) -> None:
        """Reload a saved campaign into memory for the current channel."""
        await interaction.response.defer()

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

        await interaction.response.defer()

        # Persist before archiving
        self._persist_session(session)
        logger.info(
            "SESSION end campaign=%s channel=%s",
            session.campaign.id, channel_id,
        )

        await interaction.followup.send(
            f"Campagne **{session.campaign.name}** terminee. Le canal sera archive.",
        )

        # Archive the channel
        channel = interaction.channel
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
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_session(self, session: GameSession) -> None:
        """Save campaign, characters, combat state, NPCs, and quests to DB."""
        db_session = self.bot.db_factory()
        try:
            # Campaign + combat state
            session.campaign.combat_state_json = (
                session.combat_state.model_dump_json()
                if session.combat_state is not None
                else None
            )
            camp_repo = CampaignRepository(db_session)
            camp_repo.update(session.campaign)

            # Player characters
            pc_repo = PlayerCharacterRepository(db_session)
            for user_id, char in session.characters.items():
                inv = session.inventories.get(user_id)
                spell = session.spellcasters.get(user_id)
                if inv is not None:
                    try:
                        pc_repo.update(user_id, session.campaign.id, char, inv, spell)
                    except ValueError:
                        pc_repo.save(user_id, session.campaign.id, char, inv, spell)

            # NPCs
            npc_repo = NPCRepository(db_session)
            for npc in session.npcs.values():
                try:
                    npc_repo.update(npc, session.campaign.id)
                except ValueError:
                    npc_repo.save(npc, session.campaign.id)

            # Quests
            quest_repo = QuestRepository(db_session)
            for quest in session.quests:
                try:
                    quest_repo.update(quest, session.campaign.id)
                except ValueError:
                    quest_repo.save(quest, session.campaign.id)

            # Story arc
            if session.story_arc:
                arc_repo = StoryArcRepository(db_session)
                try:
                    arc_repo.update(session.story_arc)
                except ValueError:
                    arc_repo.save(session.story_arc)

            db_session.commit()
        finally:
            db_session.close()


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(SessionCog(bot))  # type: ignore[arg-type]
