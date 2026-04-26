"""Session cog -- campaign lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import GuildConfig
from bot.embeds.lobby_embed import build_lobby_embed
from bot.game_session import GameSession, create_ai_services
from bot.lobby_state import LobbyPlayerStatus, LobbyState
from bot.persistence import persist_session
from bot.utils.channel_manager import archive_channel, create_session_channel
from bot.views.character_setup_flow import CharacterSetupFlow, IdentityModal
from bot.views.lobby_view import LobbyView
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
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state
from engine.starter_gear import apply_starter_kit, get_starter_kits
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

    # ------------------------------------------------------------------
    # /start_campaign
    # ------------------------------------------------------------------

    @app_commands.command(name="start_campaign", description="Lance une nouvelle campagne")
    @app_commands.describe(
        theme="Thème de la campagne (ex: Dark Fantasy, Cyberpunk noir)",
        name="Nom optionnel — par défaut le thème est utilisé",
    )
    async def start_campaign(
        self,
        interaction: discord.Interaction,
        theme: str,
        name: str | None = None,
    ) -> None:
        """Create a new campaign lobby — players join via the lobby view."""
        try:
            await interaction.response.defer()
        except discord.NotFound:
            logger.warning("start_campaign: interaction expired before defer()")
            return

        guild = interaction.guild
        if guild is None:
            await interaction.followup.send(
                "Cette commande doit être utilisée dans un serveur.", ephemeral=True,
            )
            return

        campaign_name = name or theme
        creator_id = interaction.user.id

        # Build campaign model — player_names will be filled at launch time
        # from the lobby roster (READY players only).
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name=campaign_name,
            created_at=datetime.now(timezone.utc),
            player_names=[],
        )

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

            # Channel is created with the host as initial member; other
            # players gain access when they click Rejoindre (handled at
            # launch via permission overwrites if needed).
            host_member = guild.get_member(creator_id) or interaction.user
            initial_members: list[discord.Member] = []
            if isinstance(host_member, discord.Member):
                initial_members.append(host_member)
            channel = await create_session_channel(
                guild, campaign_name, initial_members, guild.me, category_name,
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

        # Build lobby state
        lobby = LobbyState(creator_id=creator_id, language=language)

        # ------------------------------------------------------------
        # Lobby callbacks
        # ------------------------------------------------------------

        async def refresh_lobby_message(
            lobby_view: LobbyView,
            *,
            via_interaction: discord.Interaction | None = None,
        ) -> None:
            """Re-render the lobby embed in place (host post or fresh edit)."""
            roster = []
            for p in lobby.players.values():
                member = guild.get_member(p.user_id)
                disp = member.display_name if member else f"User {p.user_id}"
                roster.append((p, disp))
            host_member = guild.get_member(creator_id)
            host_name = host_member.display_name if host_member else f"User {creator_id}"
            new_embed = build_lobby_embed(
                campaign_name=campaign_name,
                theme=theme,
                host_name=host_name,
                roster=roster,
                language=language,
            )
            if via_interaction is not None and not via_interaction.response.is_done():
                await via_interaction.response.edit_message(embed=new_embed, view=lobby_view)
            else:
                # Edit the original lobby message via stored reference
                msg = getattr(lobby_view, "_lobby_message", None)
                if msg is not None:
                    try:
                        await msg.edit(embed=new_embed, view=lobby_view)
                    except discord.HTTPException:
                        logger.warning("refresh_lobby_message: edit failed")

        async def on_join(
            inter: discord.Interaction, lobby_view: LobbyView,
        ) -> None:
            user_id = inter.user.id
            try:
                lobby.add_player(user_id)
            except ValueError:
                await inter.response.send_message(
                    "Le lobby est plein.", ephemeral=True,
                )
                return

            # Send the IdentityModal first — it fires the rest of the flow.
            async def on_setup_complete(
                character: Any, kit_name: str, motivation_key: str,
            ) -> None:
                """Called by CharacterSetupFlow when the player confirms."""
                # Build inventory + apply starter kit
                inventory = create_inventory()
                kits = get_starter_kits(character.char_class)
                kit = next((k for k in kits if k.name == kit_name), None)
                if kit is not None:
                    inventory = apply_starter_kit(kit, inventory)
                spellcaster = create_spellcaster_state(character.char_class, level=1)

                # Persist character to DB right now so it survives a bot restart
                db_sess = self.bot.db_factory()
                try:
                    pc_repo = PlayerCharacterRepository(db_sess)
                    pc_repo.save(
                        user_id, campaign.id, character, inventory, spellcaster,
                    )
                    db_sess.commit()
                finally:
                    db_sess.close()

                # Update lobby player record
                player = lobby.players.get(user_id)
                if player is not None:
                    player.character = character
                    player.inventory = inventory
                    player.spellcaster = spellcaster
                    player.kit_name = kit_name
                    player.motivation_key = motivation_key
                    player.status = LobbyPlayerStatus.READY

                logger.info(
                    "LOBBY ready user=%s name=%s class=%s campaign=%s",
                    user_id, character.name, character.char_class.value, campaign.id,
                )
                # Refresh the public lobby embed
                await refresh_lobby_message(lobby_view)

            # Mark CREATING and refresh roster
            lobby.set_status(user_id, LobbyPlayerStatus.CREATING)
            await refresh_lobby_message(lobby_view)

            flow = CharacterSetupFlow(
                user_id=user_id,
                language=language,
                on_complete=on_setup_complete,
            )
            modal = IdentityModal(parent_view=flow)
            await inter.response.send_modal(modal)

        async def on_launch(
            inter: discord.Interaction, lobby_view: LobbyView,
        ) -> None:
            """Transition the lobby into a GameSession and post the opening."""
            try:
                await inter.response.defer()
            except discord.NotFound:
                logger.warning("on_launch: interaction expired before defer()")

            ready = [
                p for p in lobby.players.values()
                if p.status == LobbyPlayerStatus.READY
                and p.character is not None
                and p.inventory is not None
            ]
            if not ready:
                # Should be blocked by has_any_ready, but be defensive
                logger.warning("on_launch called with no ready players")
                return

            assert channel is not None
            await self._launch_campaign_from_lobby(
                channel=channel,
                campaign=campaign,
                lobby=lobby,
                ready_players=ready,
                language=language,
                lobby_view=lobby_view,
            )

        # Build view + post lobby
        lobby_view = LobbyView(
            lobby_state=lobby,
            host_id=creator_id,
            language=language,
            on_join_clicked=on_join,
            on_launch_clicked=on_launch,
        )
        host_member = guild.get_member(creator_id)
        host_name = host_member.display_name if host_member else interaction.user.display_name
        embed = build_lobby_embed(
            campaign_name=campaign_name,
            theme=theme,
            host_name=host_name,
            roster=[],
            language=language,
        )
        lobby_msg = await channel.send(embed=embed, view=lobby_view)
        # Stash the message so callbacks can re-edit when no interaction is in scope
        lobby_view._lobby_message = lobby_msg  # type: ignore[attr-defined]

        self.bot.lobbies[channel.id] = lobby

        logger.info(
            "SESSION lobby_open campaign=%s theme=%r host=%s guild=%s channel=%s",
            campaign.id, theme, creator_id, guild.name, channel.name,
        )

        await interaction.followup.send(
            f"Lobby ouvert dans {channel.mention} — les joueurs peuvent rejoindre.",
        )

    # ------------------------------------------------------------------
    # Lobby → GameSession transition (C3)
    # ------------------------------------------------------------------

    async def _launch_campaign_from_lobby(
        self,
        *,
        channel: discord.TextChannel,
        campaign: Campaign,
        lobby: LobbyState,
        ready_players: list,
        language: str,
        lobby_view: LobbyView,
    ) -> None:
        """Generate arc/location, build GameSession, post opening narrative."""
        from ai.client import OllamaClient, OllamaUnavailableError
        from bot.embeds.character_embed import build_party_card_embed
        from bot.embeds.narrative_embed import (
            build_countdown_embed, build_opening_crawl_embed,
        )
        from bot.embeds.scene_embed import build_scene_embed

        # Generation phase — let the player know we're cooking the story
        try:
            client = OllamaClient()
        except (OllamaUnavailableError, Exception):
            await channel.send(
                "Ollama est indisponible — impossible de générer la campagne. "
                "Vérifie que le serveur tourne, puis relance `/start_campaign`.",
            )
            self.bot.lobbies.pop(channel.id, None)
            lobby_view.stop()
            return

        # Build characters/inventories/spellcasters dicts
        characters = {p.user_id: p.character for p in ready_players}
        inventories = {p.user_id: p.inventory for p in ready_players}
        spellcasters: dict[int, Any] = {}
        for p in ready_players:
            if p.spellcaster is not None:
                spellcasters[p.user_id] = p.spellcaster
        kits = {p.user_id: p.kit_name or "" for p in ready_players}
        motivations = {p.user_id: p.motivation_key or "" for p in ready_players}

        # Update Campaign player_names
        campaign.player_names = [str(p.user_id) for p in ready_players]

        # ---- Generate arc + starting location ----
        story_arc = None
        current_location = None
        try:
            from ai.arc_generator import ArcGenerator
            from ai.world_generator import WorldGenerator
            from engine.arc_recipes import generate_recipe

            arc_gen = ArcGenerator(client)
            world_gen = WorldGenerator(client)

            recipe = generate_recipe(theme=campaign.name)
            logger.info(
                "GENERATION recipe campaign=%s archetype=%s tone=%s beats=%d",
                campaign.id, recipe.archetype, recipe.tone, recipe.num_beats,
            )

            arc_start = time.monotonic()
            arc = await asyncio.to_thread(
                arc_gen.generate,
                campaign.name, len(ready_players), language, recipe,
            )
            story_arc = arc.model_copy(update={"campaign_id": campaign.id})
            logger.info(
                "GENERATION arc_done campaign=%s elapsed=%.1fs beats=%d",
                campaign.id, time.monotonic() - arc_start, len(story_arc.beats),
            )

            arc_context = (
                f"Campaign: {campaign.name}. "
                f"Villain: {story_arc.villain_name}. "
                f"First beat: "
                f"{story_arc.beats[0].description if story_arc.beats else 'unknown'}."
            )
            arc_location_hints = [
                beat.location_hint for beat in story_arc.beats if beat.location_hint
            ]
            loc_start = time.monotonic()
            current_location = await asyncio.to_thread(
                world_gen.generate,
                arc_context, "starting_area", language, arc_location_hints,
            )
            logger.info(
                "GENERATION loc_done campaign=%s elapsed=%.1fs location=%r",
                campaign.id, time.monotonic() - loc_start, current_location.name,
            )
            campaign.current_location = current_location.name
        except OllamaUnavailableError:
            await channel.send(
                "Ollama est devenu indisponible pendant la génération. "
                "Relance `/start_campaign` quand le serveur sera de retour.",
            )
            self.bot.lobbies.pop(channel.id, None)
            lobby_view.stop()
            return
        except Exception:
            logger.exception(
                "GENERATION failed campaign=%s — launching with fallback",
                campaign.id,
            )

        # ---- Build GameSession ----
        session = GameSession(
            campaign=campaign,
            characters=characters,
            inventories=inventories,
            spellcasters=spellcasters,
            current_location=current_location,
            story_arc=story_arc,
            character_kits=dict(kits),
            character_motivations=dict(motivations),
            language=language,
        )
        create_ai_services(session)

        # Persist arc + location
        db_session = self.bot.db_factory()
        try:
            if story_arc is not None:
                arc_repo = StoryArcRepository(db_session)
                arc_repo.save(story_arc)
            if current_location is not None:
                loc_repo = LocationRepository(db_session)
                loc_repo.save(current_location, campaign.id)
                from bot.world_navigation import create_exit_stubs

                create_exit_stubs(
                    loc_repo,
                    current_location.connections,
                    parent_name=current_location.name,
                    campaign_id=campaign.id,
                )
                CampaignRepository(db_session).update(campaign)
            db_session.commit()
        finally:
            db_session.close()

        # Story bible header
        if session.story_bible is not None:
            try:
                session.story_bible.write_header(
                    campaign=campaign,
                    story_arc=story_arc,
                    location=current_location,
                    characters=characters,
                    character_kits=kits,
                    character_motivations=motivations,
                )
            except Exception:
                logger.warning(
                    "story_bible write_header failed for campaign=%s",
                    campaign.id, exc_info=True,
                )

        # Move the lobby state to active sessions
        self.bot.sessions[channel.id] = session
        self.bot.lobbies.pop(channel.id, None)
        lobby_view.stop()

        # Purge onboarding noise so the campaign opens on a clean canvas
        try:
            await channel.purge(limit=200)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning("LAUNCH purge failed campaign=%s", campaign.id, exc_info=True)

        # Surface AI init warnings
        for warning in session.ai_warnings:
            try:
                await channel.send(warning)
            except Exception:
                logger.warning("Failed to send AI warning campaign=%s", campaign.id)

        # Animated countdown
        try:
            countdown_msg = await channel.send(
                embed=build_countdown_embed(3, campaign.name, language),
            )
            for step in (2, 1):
                await asyncio.sleep(1.5)
                await countdown_msg.edit(
                    embed=build_countdown_embed(step, campaign.name, language),
                )
            await asyncio.sleep(1.5)
            await countdown_msg.delete()
        except Exception:
            logger.warning(
                "LAUNCH countdown failed campaign=%s", campaign.id, exc_info=True,
            )

        # Party cards
        try:
            for user_id, character in characters.items():
                member = channel.guild.get_member(user_id)
                member_name = member.display_name if member else "???"
                card_embed = build_party_card_embed(character, member_name, language)
                await channel.send(embed=card_embed)
                await asyncio.sleep(0.3)
        except Exception:
            logger.warning(
                "LAUNCH party cards failed campaign=%s", campaign.id, exc_info=True,
            )

        # Separator
        try:
            await channel.send("━━━━━━━━━━ ✦ ━━━━━━━━━━")
        except Exception:
            logger.warning("LAUNCH separator failed campaign=%s", campaign.id, exc_info=True)

        # Opening crawl
        crawl_embed = build_opening_crawl_embed(
            campaign_name=campaign.name,
            story_arc=story_arc,
            location=current_location,
            language=language,
        )
        await channel.send(embed=crawl_embed)

        # Scene hydration + scene embed
        if current_location is not None:
            from bot.scene_hydration import hydrate_scene
            hydrate_scene(session, db_factory=self.bot.db_factory)
            scene_embed = build_scene_embed(
                location=current_location,
                language=language,
                arrival_hook=current_location.arrival_hook,
            )
            await channel.send(embed=scene_embed)
            logger.info(
                "SCENE posted campaign=%s location=%s npcs=%d",
                campaign.id, current_location.name,
                len(current_location.npcs_present),
            )

        logger.info(
            "LAUNCH campaign=%s players=%d arc_beats=%d location=%s",
            campaign.id, len(characters),
            len(story_arc.beats) if story_arc else 0,
            current_location.name if current_location else "none",
        )

        # Arc Tracker pin — best effort
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
            logger.warning("Failed to pin Arc Tracker on launch", exc_info=True)

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
        if note.current_beat_atmosphere:
            embed.add_field(
                name="Atmosphère actuelle",
                value=note.current_beat_atmosphere,
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
