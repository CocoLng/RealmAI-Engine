"""Session cog -- campaign lifecycle management."""

from __future__ import annotations

import asyncio
import logging
import re
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
        players="Joueurs invités (mentions @user1 @user2). Optionnel — sinon lobby ouvert.",
    )
    async def start_campaign(
        self,
        interaction: discord.Interaction,
        theme: str,
        name: str | None = None,
        players: str | None = None,
    ) -> None:
        """Create a new campaign lobby — players join via the lobby view.

        ``players`` is an optional space-separated list of user mentions.
        When provided, the channel is created **private**: only the host,
        the tagged players, and the bot can see it. Tagged players still
        click the Rejoindre button inside the lobby to actually create
        their character — the parameter only gates channel visibility.
        """
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

        # Resolve invited players from mention string. We accept both
        # `<@123>` and `<@!123>` formats (Discord renders the latter for
        # users with a guild nickname).
        invited_members: list[discord.Member] = []
        if players:
            mention_re = re.compile(r"<@!?(\d+)>")
            seen_ids: set[int] = set()
            for match in mention_re.finditer(players):
                uid = int(match.group(1))
                if uid in seen_ids or uid == creator_id:
                    continue
                seen_ids.add(uid)
                member = guild.get_member(uid)
                if member is not None and not member.bot:
                    invited_members.append(member)

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

            # Channel privacy: host + invited players + bot only.
            # @everyone is denied (set inside create_session_channel).
            host_member = guild.get_member(creator_id) or interaction.user
            initial_members: list[discord.Member] = list(invited_members)
            if isinstance(host_member, discord.Member):
                initial_members.insert(0, host_member)
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
        lobby = LobbyState(
            creator_id=creator_id,
            language=language,
            campaign_name=campaign_name,
            theme=theme,
        )
        # Serialise concurrent edits of the public lobby message — multiple
        # players may finish setup or leave at the same instant.
        lobby_refresh_lock = asyncio.Lock()

        # ------------------------------------------------------------
        # Lobby callbacks
        # ------------------------------------------------------------

        async def refresh_lobby_message(
            lobby_view: LobbyView,
            *,
            via_interaction: discord.Interaction | None = None,
        ) -> None:
            """Re-render the lobby embed in place (host post or fresh edit)."""
            async with lobby_refresh_lock:
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

        async def on_leave(
            inter: discord.Interaction, lobby_view: LobbyView,
        ) -> None:
            """Remove the player from the lobby and refresh the public roster."""
            lobby.remove_player(inter.user.id)
            # The interaction is on the lobby message — use it to refresh
            # the embed atomically (single API call, no race vs background edits).
            await refresh_lobby_message(lobby_view, via_interaction=inter)

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
            """Transition the lobby into a GameSession and post the opening.

            Host-only is enforced upstream by ``LobbyView.launch`` — defensive
            check kept here so direct callers (test bridge, etc.) can't bypass.
            """
            from bot.lobby_state import GenerationPhase

            if inter.user.id != creator_id:
                # Defensive — should already be blocked by LobbyView.launch
                if not inter.response.is_done():
                    await inter.response.send_message(
                        "Seul le host peut démarrer la campagne.", ephemeral=True,
                    )
                return

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

            # Lock the lobby so nothing else mutates while we launch
            lobby_view.join.disabled = True
            lobby_view.leave.disabled = True
            lobby_view.launch.disabled = True
            await refresh_lobby_message(lobby_view)

            # If pregen is still running, post a public status message and
            # wait for it. The launch then auto-continues.
            status_msg: discord.Message | None = None
            if (
                lobby.pregen_task is not None
                and not lobby.pregen_task.done()
            ):
                phase_label = {
                    GenerationPhase.PENDING:  "initialisation",
                    GenerationPhase.ARC:      "écriture de l'arc narratif",
                    GenerationPhase.LOCATION: "création du lieu de départ",
                }.get(lobby.pregen_phase, lobby.pregen_phase.name.lower())
                status_msg = await channel.send(
                    f"🪄 **Préparation de l'aventure en cours...**\n"
                    f"_Phase : {phase_label}_\n"
                    f"_Le récit démarre automatiquement dès que c'est prêt._",
                )
                try:
                    await lobby.pregen_task
                except Exception:  # pragma: no cover — already trapped inside pregen
                    logger.exception("on_launch: pregen task raised")

            if status_msg is not None:
                try:
                    await status_msg.edit(
                        content="✨ **Aventure prête !** Le récit commence...",
                    )
                except discord.HTTPException:
                    pass

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
            on_leave_clicked=on_leave,
        )
        host_member_for_label = guild.get_member(creator_id)
        host_name = (
            host_member_for_label.display_name
            if host_member_for_label is not None
            else interaction.user.display_name
        )
        embed = build_lobby_embed(
            campaign_name=campaign_name,
            theme=theme,
            host_name=host_name,
            roster=[],
            language=language,
        )
        # Ping invited players in the lobby message so they get a notification.
        # AllowedMentions whitelists the invited users specifically — no
        # accidental @everyone / @role pings.
        if invited_members:
            lobby_content = " ".join(m.mention for m in invited_members)
            allowed = discord.AllowedMentions(
                everyone=False, roles=False, users=invited_members,
            )
            lobby_msg = await channel.send(
                content=lobby_content, embed=embed, view=lobby_view,
                allowed_mentions=allowed,
            )
        else:
            lobby_msg = await channel.send(embed=embed, view=lobby_view)
        # Stash the message so callbacks can re-edit when no interaction is in scope
        lobby_view._lobby_message = lobby_msg  # type: ignore[attr-defined]
        lobby.lobby_message = lobby_msg

        self.bot.lobbies[channel.id] = lobby

        # Kick off background arc + location generation so it's ready by the
        # time the host clicks Démarrer. Player count uses 1 as a default;
        # the arc generator only uses it as a difficulty hint in the prompt
        # so a 1→6 mismatch costs nothing structural.
        lobby.pregen_task = asyncio.create_task(
            self._pregenerate_campaign_world(lobby, campaign, language),
            name=f"pregen-{campaign.id}",
        )

        logger.info(
            "SESSION lobby_open campaign=%s theme=%r host=%s guild=%s channel=%s invited=%d pregen=started",
            campaign.id, theme, creator_id, guild.name, channel.name,
            len(invited_members),
        )

        if invited_members:
            invited_list = ", ".join(m.display_name for m in invited_members)
            await interaction.followup.send(
                f"Salon privé créé dans {channel.mention} avec **{invited_list}**.",
            )
        else:
            await interaction.followup.send(
                f"Lobby ouvert dans {channel.mention} — les joueurs peuvent rejoindre.",
            )

    # ------------------------------------------------------------------
    # /add_member — host-only, host adds a player or viewer to the channel
    # ------------------------------------------------------------------

    async def _refresh_lobby_embed(
        self, lobby: LobbyState, guild: discord.Guild,
    ) -> None:
        """Rebuild the lobby roster embed in place. Safe no-op if no message."""
        if lobby.lobby_message is None:
            return
        roster = []
        for p in lobby.players.values():
            m = guild.get_member(p.user_id)
            disp = m.display_name if m else f"User {p.user_id}"
            roster.append((p, disp))
        host_member = guild.get_member(lobby.creator_id)
        host_name = (
            host_member.display_name if host_member
            else f"User {lobby.creator_id}"
        )
        new_embed = build_lobby_embed(
            campaign_name=lobby.campaign_name,
            theme=lobby.theme,
            host_name=host_name,
            roster=roster,
            language=lobby.language,
        )
        try:
            await lobby.lobby_message.edit(embed=new_embed)
        except discord.HTTPException:
            logger.warning("_refresh_lobby_embed: edit failed", exc_info=True)

    @app_commands.command(
        name="add_member",
        description="Ajoute un joueur (lobby) ou un spectateur (campagne lancée) au salon",
    )
    @app_commands.describe(user="Utilisateur à ajouter au salon de campagne")
    async def add_member(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Add a member to the campaign channel.

        Behaviour depends on campaign state:
        - **Lobby phase** (channel.id in bot.lobbies): the new member is
          slotted as JOINED and can click Rejoindre to create a character
          normally — useful when the host forgot to mention someone in
          /start_campaign.
        - **Active session** (channel.id in bot.sessions): the new member
          becomes a *viewer* — they can read and chat in the channel, but
          ActionHandlerCog ignores them when they ping the bot (only
          users in ``session.characters`` can drive actions).

        Host-only; refuses outside campaign channels.
        """
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Cette commande doit être utilisée dans un salon de campagne.",
                ephemeral=True,
            )
            return

        lobby = self.bot.lobbies.get(channel.id)
        session = self.bot.sessions.get(channel.id)
        if lobby is None and session is None:
            await interaction.response.send_message(
                "Ce salon n'a aucune campagne active. "
                "Utilise cette commande dans un salon créé par `/start_campaign`.",
                ephemeral=True,
            )
            return

        host_id = (
            lobby.creator_id if lobby is not None
            else session.creator_id  # type: ignore[union-attr]
        )
        if interaction.user.id != host_id:
            await interaction.response.send_message(
                "Seul le host de la campagne peut ajouter des membres.",
                ephemeral=True,
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "Tu ne peux pas ajouter un bot.", ephemeral=True,
            )
            return
        if user.id == host_id:
            await interaction.response.send_message(
                "Tu es déjà dans le salon (host).", ephemeral=True,
            )
            return

        # Grant channel access (idempotent).
        try:
            await channel.set_permissions(
                user, read_messages=True, send_messages=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Permissions insuffisantes pour modifier le salon.",
                ephemeral=True,
            )
            return

        ping_only = discord.AllowedMentions(
            everyone=False, roles=False, users=[user],
        )
        if lobby is not None:
            try:
                lobby.add_player(user.id)
            except ValueError as exc:
                await interaction.response.send_message(
                    f"Impossible : {exc}", ephemeral=True,
                )
                return
            await self._refresh_lobby_embed(lobby, channel.guild)
            await channel.send(
                f"🎭 {user.mention} a été ajouté au lobby — clique "
                f"**Rejoindre** pour créer ton personnage.",
                allowed_mentions=ping_only,
            )
            await interaction.response.send_message(
                f"✅ {user.mention} ajouté au lobby.", ephemeral=True,
            )
        else:
            # Post-launch: viewer. ActionHandlerCog ignores non-players.
            await channel.send(
                f"👁️ {user.mention} rejoint la table en **spectateur** — "
                f"tu peux suivre l'aventure et discuter, mais le bot ne "
                f"traitera pas tes pings.",
                allowed_mentions=ping_only,
            )
            await interaction.response.send_message(
                f"✅ {user.mention} ajouté en spectateur.", ephemeral=True,
            )

        logger.info(
            "ADD_MEMBER channel=%s host=%s added=%s mode=%s",
            channel.id, host_id, user.id,
            "lobby" if lobby is not None else "viewer",
        )

    # ------------------------------------------------------------------
    # Background pre-generation (arc + starting location)
    # ------------------------------------------------------------------

    async def _pregenerate_campaign_world(
        self, lobby: LobbyState, campaign: Campaign, language: str,
    ) -> None:
        """Generate StoryArc + starting Location while players are creating chars.

        Results land on ``lobby.story_arc`` / ``lobby.current_location``;
        ``lobby.pregen_phase`` advances PENDING → ARC → LOCATION → READY.
        On error, sets FAILED + ``pregen_error``; the launch path will
        surface this to the host.
        """
        from ai.arc_generator import ArcGenerator
        from ai.client import OllamaClient, OllamaUnavailableError
        from ai.world_generator import WorldGenerator
        from bot.lobby_state import GenerationPhase
        from engine.arc_recipes import generate_recipe

        try:
            client = OllamaClient()
        except (OllamaUnavailableError, Exception) as exc:
            lobby.pregen_phase = GenerationPhase.FAILED
            lobby.pregen_error = f"Ollama indisponible: {exc}"
            logger.warning(
                "PREGEN ollama_unavailable campaign=%s err=%s", campaign.id, exc,
            )
            return

        try:
            recipe = generate_recipe(theme=campaign.name)
            arc_gen = ArcGenerator(client)
            world_gen = WorldGenerator(client)

            # ---- Arc ----
            lobby.pregen_phase = GenerationPhase.ARC
            arc_start = time.monotonic()
            arc = await asyncio.to_thread(
                arc_gen.generate, campaign.name, 1, language, recipe,
            )
            lobby.story_arc = arc.model_copy(update={"campaign_id": campaign.id})
            logger.info(
                "PREGEN arc_done campaign=%s elapsed=%.1fs beats=%d",
                campaign.id, time.monotonic() - arc_start, len(lobby.story_arc.beats),
            )

            # ---- Location ----
            lobby.pregen_phase = GenerationPhase.LOCATION
            arc_context = (
                f"Campaign: {campaign.name}. "
                f"Villain: {lobby.story_arc.villain_name}. "
                f"First beat: "
                f"{lobby.story_arc.beats[0].description if lobby.story_arc.beats else 'unknown'}."
            )
            arc_location_hints = [
                beat.location_hint for beat in lobby.story_arc.beats if beat.location_hint
            ]
            loc_start = time.monotonic()
            lobby.current_location = await asyncio.to_thread(
                lambda: world_gen.generate(
                    campaign_context=arc_context,
                    location_type="starting_area",
                    language=language,
                    location_hints=arc_location_hints,
                ),
            )
            logger.info(
                "PREGEN loc_done campaign=%s elapsed=%.1fs location=%r",
                campaign.id, time.monotonic() - loc_start, lobby.current_location.name,
            )

            lobby.pregen_phase = GenerationPhase.READY
        except OllamaUnavailableError as exc:
            lobby.pregen_phase = GenerationPhase.FAILED
            lobby.pregen_error = str(exc)
            logger.warning(
                "PREGEN ollama_lost campaign=%s err=%s", campaign.id, exc,
            )
        except Exception as exc:
            lobby.pregen_phase = GenerationPhase.FAILED
            lobby.pregen_error = f"{type(exc).__name__}: {exc}"
            logger.exception("PREGEN failed campaign=%s", campaign.id)

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
        """Build GameSession from pre-generated arc/location, post opening narrative."""
        from bot.embeds.character_embed import build_party_card_embed
        from bot.embeds.narrative_embed import (
            build_countdown_embed, build_opening_crawl_embed,
        )
        from bot.embeds.scene_embed import build_scene_embed

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

        # ---- Use pre-generated arc + starting location (background task) ----
        # The pregen task started at /start_campaign. If still running, await
        # it now — we've already saved minutes by overlapping with character
        # creation. If it failed, surface the error and abort the launch.
        from bot.lobby_state import GenerationPhase

        if lobby.pregen_task is not None and not lobby.pregen_task.done():
            logger.info(
                "LAUNCH waiting_pregen campaign=%s phase=%s",
                campaign.id, lobby.pregen_phase.name,
            )
            try:
                await lobby.pregen_task
            except Exception:  # pragma: no cover — already trapped inside pregen
                logger.exception("LAUNCH pregen_task raised campaign=%s", campaign.id)

        if lobby.pregen_phase == GenerationPhase.FAILED:
            await channel.send(
                f"❌ La génération de l'aventure a échoué : {lobby.pregen_error}\n"
                "Relance `/start_campaign` une fois le souci résolu.",
            )
            self.bot.lobbies.pop(channel.id, None)
            lobby_view.stop()
            return

        story_arc = lobby.story_arc
        current_location = lobby.current_location
        if current_location is not None:
            campaign.current_location = current_location.name
        logger.info(
            "LAUNCH using_pregen campaign=%s arc=%s location=%s",
            campaign.id,
            "ok" if story_arc is not None else "missing",
            "ok" if current_location is not None else "missing",
        )

        # ---- Build GameSession ----
        session = GameSession(
            campaign=campaign,
            creator_id=lobby.creator_id,
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

        # Rebuild in-memory session. ``creator_id`` is set from the resumer
        # — we don't persist it on Campaign yet, so /resume effectively
        # transfers host rights to whoever brings the campaign back online.
        session = GameSession(
            campaign=campaign,
            creator_id=interaction.user.id,
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

        # H8 (suite): the resumed location may still have stub neighbors.
        from bot.location_prefetch import schedule_location_prefetch

        schedule_location_prefetch(session, db_factory=self.bot.db_factory)

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

        # Host-only gate. creator_id == 0 means a legacy/test session with no
        # recorded host (older saves predate the field), so we allow those.
        if session.creator_id != 0 and interaction.user.id != session.creator_id:
            await interaction.response.send_message(
                "Seul l'hôte de la campagne peut la terminer.",
                ephemeral=True,
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
