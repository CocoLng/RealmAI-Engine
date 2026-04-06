"""Campaign launcher — orchestrates onboarding before gameplay starts.

Manages the phase between /start_campaign and the first narrative:
character creation, starter gear selection, and background story arc
generation. Once all players are ready, creates the GameSession and
sends the opening narrative.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import discord

from bot.embeds.narrative_embed import build_narrative_embed
from bot.game_session import GameSession, create_ai_services
from bot.views.character_create_view import CharacterCreateView
from bot.views.start_onboarding_view import StartOnboardingView
from bot.views.starter_gear_view import StarterGearView
from engine.character import (
    Character,
    apply_racial_bonuses,
    create_character,
    roll_ability_scores,
)
from engine.inventory import Inventory, create_inventory
from engine.spells import SpellcasterState, create_spellcaster_state
from engine.starter_gear import StarterKit, apply_starter_kit, get_starter_kits
from world.campaign import Campaign
from world.location import Location
from world.story_arc import StoryArc

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class PlayerProgress(StrEnum):
    """Tracks each player's onboarding state."""

    PENDING = "pending"
    CHARACTER_DONE = "character_done"
    GEAR_DONE = "gear_done"


@dataclass
class CampaignLauncher:
    """Temporary orchestrator for the onboarding phase.

    Created by /start_campaign, lives in bot.launchers[channel_id].
    Once all players finish onboarding and the story arc is generated,
    creates a GameSession and removes itself.
    """

    bot: RealmBot
    campaign: Campaign
    channel: discord.TextChannel
    player_ids: list[int]
    language: str = "fr"
    player_progress: dict[int, PlayerProgress] = field(default_factory=dict)
    characters: dict[int, Character] = field(default_factory=dict)
    inventories: dict[int, Inventory] = field(default_factory=dict)
    spellcasters: dict[int, SpellcasterState | None] = field(default_factory=dict)
    story_arc: StoryArc | None = None
    current_location: Location | None = None
    _arc_task: asyncio.Task[StoryArc | None] | None = field(
        default=None, repr=False,
    )
    _location_task: asyncio.Task[Location | None] | None = field(
        default=None, repr=False,
    )

    def __post_init__(self) -> None:
        for uid in self.player_ids:
            self.player_progress.setdefault(uid, PlayerProgress.PENDING)

    # ------------------------------------------------------------------
    # Public API — called by session cog
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Send the welcome embed and start background AI tasks."""
        mentions = " ".join(f"<@{uid}>" for uid in self.player_ids)
        embed = discord.Embed(
            title=f"Campagne : {self.campaign.name}",
            description=(
                f"Bienvenue, aventuriers ! Avant de commencer votre quete, "
                f"chaque joueur doit creer son personnage et choisir son "
                f"equipement de depart.\n\n"
                f"**Joueurs attendus** : {mentions}\n\n"
                f"Cliquez sur le bouton ci-dessous pour commencer !"
            ),
            color=0xDAA520,
        )

        view = StartOnboardingView(on_click=self._on_create_character_clicked)
        await self.channel.send(embed=embed, view=view)

    def start_background_tasks(self) -> None:
        """Launch story arc and location generation as background tasks."""
        from ai.client import OllamaClient, OllamaUnavailableError

        try:
            client = OllamaClient()
        except (OllamaUnavailableError, Exception):
            logger.warning("Ollama unavailable — skipping arc/location generation")
            return

        from ai.arc_generator import ArcGenerator
        from ai.world_generator import WorldGenerator

        arc_gen = ArcGenerator(client)
        world_gen = WorldGenerator(client)

        self._arc_task = asyncio.create_task(
            asyncio.to_thread(
                arc_gen.generate,
                self.campaign.name,
                len(self.player_ids),
            ),
            name=f"arc-gen-{self.campaign.id}",
        )
        self._arc_task.add_done_callback(self._on_arc_done)

        self._location_task = asyncio.create_task(
            asyncio.to_thread(
                world_gen.generate,
                campaign_context=f"New campaign: {self.campaign.name}",
                location_type="starting_area",
                language=self.language,
            ),
            name=f"loc-gen-{self.campaign.id}",
        )
        self._location_task.add_done_callback(self._on_location_done)

    # ------------------------------------------------------------------
    # Callbacks from views
    # ------------------------------------------------------------------

    async def _on_create_character_clicked(
        self, interaction: discord.Interaction,
    ) -> None:
        """Handle the 'Create Character' button click."""
        user_id = interaction.user.id

        if user_id not in self.player_progress:
            await interaction.response.send_message(
                "Tu ne fais pas partie de cette campagne.", ephemeral=True,
            )
            return

        if self.player_progress[user_id] != PlayerProgress.PENDING:
            await interaction.response.send_message(
                "Tu as deja cree ton personnage !", ephemeral=True,
            )
            return

        view = CharacterCreateView(on_complete=self._on_character_created)
        await interaction.response.send_message(
            "Choisis ta race :", view=view, ephemeral=True,
        )

    async def _on_character_created(
        self,
        interaction: discord.Interaction,
        view: CharacterCreateView,
    ) -> None:
        """Called when a player finishes the character creation flow."""
        user_id = interaction.user.id

        assert view.race is not None
        assert view.char_class is not None
        assert view.alignment is not None
        assert view.character_name is not None

        scores = roll_ability_scores()
        scores = apply_racial_bonuses(scores, view.race)
        character = create_character(
            name=view.character_name,
            race=view.race,
            char_class=view.char_class,
            ability_scores=scores,
            alignment=view.alignment,
        )

        inventory = create_inventory()
        spellcaster = create_spellcaster_state(view.char_class, level=1)

        self.characters[user_id] = character
        self.inventories[user_id] = inventory
        self.spellcasters[user_id] = spellcaster
        self.player_progress[user_id] = PlayerProgress.CHARACTER_DONE

        # Persist character to DB
        db_session = self.bot.db_factory()
        try:
            from db.repositories import PlayerCharacterRepository

            pc_repo = PlayerCharacterRepository(db_session)
            pc_repo.save(
                user_id, self.campaign.id, character, inventory, spellcaster,
            )
            db_session.commit()
        finally:
            db_session.close()

        # Send starter gear selection
        kits = get_starter_kits(view.char_class)
        gear_view = StarterGearView(
            kits=kits, on_selected=self._on_gear_selected,
        )
        items_desc = "\n".join(
            f"**{kit.name}** — {kit.description}" for kit in kits
        )
        await interaction.response.send_message(
            f"Personnage **{character.name}** cree !\n\n"
            f"Choisis ton equipement de depart :\n{items_desc}",
            view=gear_view,
            ephemeral=True,
        )

    async def _on_gear_selected(
        self,
        interaction: discord.Interaction,
        kit: StarterKit,
    ) -> None:
        """Called when a player selects a starter gear kit."""
        user_id = interaction.user.id
        inventory = self.inventories.get(user_id)
        character = self.characters.get(user_id)

        if inventory is None or character is None:
            await interaction.response.send_message(
                "Erreur interne.", ephemeral=True,
            )
            return

        inventory = apply_starter_kit(kit, inventory)
        self.inventories[user_id] = inventory
        self.player_progress[user_id] = PlayerProgress.GEAR_DONE

        # Update inventory in DB
        db_session = self.bot.db_factory()
        try:
            from db.repositories import PlayerCharacterRepository

            pc_repo = PlayerCharacterRepository(db_session)
            spellcaster = self.spellcasters.get(user_id)
            pc_repo.update(
                user_id, self.campaign.id, character, inventory, spellcaster,
            )
            db_session.commit()
        finally:
            db_session.close()

        # Confirm ephemerally
        await interaction.response.send_message(
            f"Kit **{kit.name}** equipe ! Tu es pret(e).", ephemeral=True,
        )

        # Announce publicly
        race = character.race.value
        cls = character.char_class.value
        await self.channel.send(
            f"**{character.name}** ({race} {cls}) est pret(e) ! [{kit.name}]",
        )

        await self._check_ready()

    # ------------------------------------------------------------------
    # Background task callbacks
    # ------------------------------------------------------------------

    def _on_arc_done(self, task: asyncio.Task[StoryArc | None]) -> None:
        """Callback when arc generation finishes."""
        if task.cancelled():
            logger.warning("Arc generation cancelled for campaign %s", self.campaign.id)
            return
        try:
            self.story_arc = task.result()
            if self.story_arc:
                self.story_arc = self.story_arc.model_copy(
                    update={"campaign_id": self.campaign.id},
                )
                logger.info(
                    "Story arc generated for campaign %s (%d beats)",
                    self.campaign.id,
                    len(self.story_arc.beats),
                )
        except Exception:
            logger.warning(
                "Story arc generation failed for campaign %s",
                self.campaign.id,
                exc_info=True,
            )

        asyncio.create_task(self._check_ready())

    def _on_location_done(self, task: asyncio.Task[Location | None]) -> None:
        """Callback when location generation finishes."""
        if task.cancelled():
            logger.warning("Location generation cancelled for campaign %s", self.campaign.id)
            return
        try:
            self.current_location = task.result()
            if self.current_location:
                self.campaign.current_location = self.current_location.name
                logger.info(
                    "Starting location generated: %s",
                    self.current_location.name,
                )
        except Exception:
            logger.warning(
                "Location generation failed for campaign %s",
                self.campaign.id,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Launch check
    # ------------------------------------------------------------------

    async def _check_ready(self) -> None:
        """Check if all players are GEAR_DONE and arc is done. If so, launch."""
        all_ready = all(
            progress == PlayerProgress.GEAR_DONE
            for progress in self.player_progress.values()
        )
        arc_done = (
            self._arc_task is None or self._arc_task.done()
        )

        if not all_ready or not arc_done:
            return

        await self._launch_campaign()

    async def _launch_campaign(self) -> None:
        """Create the GameSession, send opening narrative, clean up."""
        session = GameSession(
            campaign=self.campaign,
            characters=self.characters,
            inventories=self.inventories,
            spellcasters=self.spellcasters,
            current_location=self.current_location,
            story_arc=self.story_arc,
            language=self.language,
        )
        create_ai_services(session)

        # Persist story arc and location
        db_session = self.bot.db_factory()
        try:
            if self.story_arc:
                from db.repositories import StoryArcRepository

                arc_repo = StoryArcRepository(db_session)
                arc_repo.save(self.story_arc)

            if self.current_location:
                from db.repositories import LocationRepository, CampaignRepository

                LocationRepository(db_session).save(
                    self.current_location, self.campaign.id,
                )
                CampaignRepository(db_session).update(self.campaign)

            db_session.commit()
        finally:
            db_session.close()

        # Move from launchers to sessions
        self.bot.sessions[self.channel.id] = session
        self.bot.launchers.pop(self.channel.id, None)

        # Build opening narrative
        desc = "Votre aventure commence..."
        if self.current_location:
            desc = self.current_location.description or desc
        if self.story_arc and self.story_arc.beats:
            first_beat = self.story_arc.beats[0]
            desc = f"{desc}\n\n*{first_beat.description}*"

        embed = build_narrative_embed(desc, f"Campagne : {self.campaign.name}", "dramatic")
        await self.channel.send(embed=embed)

        player_count = len(self.characters)
        logger.info(
            "LAUNCH campaign=%s players=%d arc_beats=%d location=%s",
            self.campaign.id,
            player_count,
            len(self.story_arc.beats) if self.story_arc else 0,
            self.current_location.name if self.current_location else "none",
        )
