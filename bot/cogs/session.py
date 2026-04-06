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

from bot.config import GuildConfig
from bot.embeds.narrative_embed import build_narrative_embed
from bot.game_session import GameSession, create_ai_services
from bot.utils.channel_manager import archive_channel, create_session_channel
from db.repositories import (
    CampaignChannelRepository,
    CampaignRepository,
    GuildConfigRepository,
    LocationRepository,
    PlayerCharacterRepository,
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

        # Fetch guild config and persist campaign
        db_session = self.bot.db_factory()
        try:
            guild_config_repo = GuildConfigRepository(db_session)
            config = guild_config_repo.get(guild.id)
            category_name = config.category_name if config else "RealmAI Sessions"

            campaign_repo = CampaignRepository(db_session)
            campaign_repo.save(campaign)
            db_session.commit()
        finally:
            db_session.close()

        # Resolve Member objects for channel permissions
        player_members: list[discord.Member] = []
        for uid in player_ids:
            member = guild.get_member(uid)
            if member:
                player_members.append(member)

        # Create private session channel
        channel = await create_session_channel(
            guild, theme, player_members, guild.me, category_name,
        )

        # Persist channel-campaign mapping
        db_session = self.bot.db_factory()
        try:
            channel_repo = CampaignChannelRepository(db_session)
            channel_repo.save(channel.id, campaign.id, guild.id)
            db_session.commit()
        finally:
            db_session.close()

        # Create in-memory game session
        session = GameSession(campaign=campaign)
        create_ai_services(session)

        # Generate initial location via AI (best-effort)
        if session.ollama_client:
            try:
                from ai.world_generator import WorldGenerator

                gen = WorldGenerator(session.ollama_client)
                location = gen.generate(
                    campaign_context=f"New campaign: {theme}",
                    location_type="starting_area",
                )
                session.current_location = location
                campaign.current_location = location.name

                db_session = self.bot.db_factory()
                try:
                    LocationRepository(db_session).save(location, campaign.id)
                    CampaignRepository(db_session).update(campaign)
                    db_session.commit()
                finally:
                    db_session.close()
            except Exception:
                logger.warning("Failed to generate initial location", exc_info=True)

        self.bot.sessions[channel.id] = session

        player_mentions = ", ".join(str(uid) for uid in player_ids)
        logger.info(
            "SESSION start campaign=%s theme=%r players=[%s] guild=%s channel=%s",
            campaign.id, theme, player_mentions,
            guild.name, channel.name,
        )

        # Welcome message in the new channel
        desc = "Bienvenue, aventuriers !"
        if session.current_location:
            desc = session.current_location.description or desc
        embed = build_narrative_embed(desc, f"Campagne: {theme}", "dramatic")
        await channel.send(embed=embed)

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
            campaign_id, _ = mapping

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
        finally:
            db_session.close()

        # Rebuild in-memory session
        session = GameSession(campaign=campaign, current_location=location)
        for user_id, char, inv, spell in pc_rows:
            session.characters[user_id] = char
            session.inventories[user_id] = inv
            session.spellcasters[user_id] = spell

        create_ai_services(session)
        self.bot.sessions[channel_id] = session

        player_count = len(session.characters)
        logger.info(
            "SESSION resume campaign=%s channel=%s characters=%d",
            campaign.id, channel_id, player_count,
        )

        await interaction.followup.send(
            f"Session reprise ! Campagne **{campaign.name}** "
            f"-- {player_count} personnage(s) charge(s).",
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
    @app_commands.describe(category="Nom de la categorie Discord pour les sessions")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def settings(self, interaction: discord.Interaction, category: str) -> None:
        """Update the guild's session category name."""
        if interaction.guild is None:
            await interaction.response.send_message("Serveur requis.", ephemeral=True)
            return

        config = GuildConfig(guild_id=interaction.guild.id, category_name=category)
        db_session = self.bot.db_factory()
        try:
            repo = GuildConfigRepository(db_session)
            repo.upsert(config)
            db_session.commit()
        finally:
            db_session.close()

        logger.info(
            "SESSION settings guild=%s category=%r",
            interaction.guild.id, category,
        )
        await interaction.response.send_message(
            f"Categorie mise a jour: **{category}**", ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_session(self, session: GameSession) -> None:
        """Save campaign and all player characters to the database."""
        db_session = self.bot.db_factory()
        try:
            camp_repo = CampaignRepository(db_session)
            camp_repo.update(session.campaign)

            pc_repo = PlayerCharacterRepository(db_session)
            for user_id, char in session.characters.items():
                inv = session.inventories.get(user_id)
                spell = session.spellcasters.get(user_id)
                if inv is not None:
                    try:
                        pc_repo.update(user_id, session.campaign.id, char, inv, spell)
                    except ValueError:
                        pc_repo.save(user_id, session.campaign.id, char, inv, spell)

            db_session.commit()
        finally:
            db_session.close()


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(SessionCog(bot))  # type: ignore[arg-type]
