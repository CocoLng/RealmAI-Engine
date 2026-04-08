"""Campaign launcher — orchestrates onboarding before gameplay starts.

Manages the phase between /start_campaign and the first narrative:
character creation, starter gear selection, and background story arc
generation. Once all players are ready, creates the GameSession and
sends the opening narrative.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING

import discord

from bot.embeds.narrative_embed import build_narrative_embed
from bot.embeds.scene_embed import build_scene_embed
from bot.game_session import GameSession, create_ai_services
from bot.i18n import CLASS_LABELS, RACE_LABELS, get_kit_label, get_label
from bot.llm_retry import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, retry_llm_call
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
    from collections.abc import Callable
    from typing import TypeVar

    from ai.client import OllamaClient
    from bot.bot import RealmBot

    T = TypeVar("T")

logger = logging.getLogger(__name__)

# Retry configuration for LLM calls (re-exported for tests / external callers)
MAX_RETRIES = DEFAULT_MAX_RETRIES
RETRY_DELAYS = list(DEFAULT_RETRY_DELAYS)


class PlayerProgress(StrEnum):
    """Tracks each player's onboarding state."""

    PENDING = "pending"
    CHARACTER_DONE = "character_done"
    GEAR_DONE = "gear_done"


class GenerationPhase(IntEnum):
    """Tracks background LLM generation state for observability."""

    PENDING  = 0
    ARC      = 1
    LOCATION = 2
    READY    = 3
    FAILED   = 4


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
    _generation_task: asyncio.Task[None] | None = field(
        default=None, repr=False,
    )
    _generation_failed: bool = field(default=False, repr=False)
    _launched: bool = field(default=False, repr=False)
    _generation_phase: GenerationPhase = field(
        default=GenerationPhase.PENDING, repr=False,
    )
    _notified_ollama_waiting: bool = field(default=False, repr=False)
    _notified_generation_ready: bool = field(default=False, repr=False)
    _notified_players_ready: bool = field(default=False, repr=False)
    _gen_start: float = field(default=0.0, repr=False)

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
        """Launch sequential arc → location generation as a single background task."""
        from ai.client import OllamaClient, OllamaUnavailableError

        try:
            client = OllamaClient()
        except (OllamaUnavailableError, Exception):
            logger.warning(
                "Ollama unavailable — cannot generate arc/location for campaign %s",
                self.campaign.id,
            )
            self._generation_failed = True
            self._generation_phase = GenerationPhase.FAILED
            # Notify players instead of failing silently
            asyncio.create_task(self._notify_generation_failed())
            return

        self._gen_start = time.monotonic()
        self._generation_task = self._safe_create_task(
            self._run_generation(client),
        )

    # ------------------------------------------------------------------
    # Callbacks from views
    # ------------------------------------------------------------------

    async def _on_create_character_clicked(
        self, interaction: discord.Interaction,
    ) -> None:
        """Handle the 'Create Character' button click."""
        user_id = interaction.user.id
        logger.info(
            "ONBOARD click user=%s campaign=%s",
            interaction.user, self.campaign.id,
        )

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

        view = CharacterCreateView(language=self.language, on_complete=self._on_character_created)
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

        logger.info(
            "ONBOARD character user=%s name=%s race=%s class=%s campaign=%s",
            interaction.user, character.name, view.race.value,
            view.char_class.value, self.campaign.id,
        )

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
            kits=kits, on_selected=self._on_gear_selected, language=self.language,
        )
        items_desc = "\n".join(
            f"**{get_kit_label(self.language, kit.name, 'name')}** — "
            f"{get_kit_label(self.language, kit.name, 'description') or kit.description}"
            for kit in kits
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

        logger.info(
            "ONBOARD gear user=%s kit=%s campaign=%s",
            interaction.user, kit.name, self.campaign.id,
        )

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

        kit_display = get_kit_label(self.language, kit.name, "name")
        race_display = get_label(RACE_LABELS, self.language, character.race.value)
        cls_display = get_label(CLASS_LABELS, self.language, character.char_class.value)

        # Confirm ephemerally
        await interaction.response.send_message(
            f"Kit **{kit_display}** equipe ! Tu es pret(e).", ephemeral=True,
        )

        # Announce publicly
        await self.channel.send(
            f"**{character.name}** ({race_display} {cls_display}) est pret(e) ! [{kit_display}]",
        )

        await self._check_ready()

    # ------------------------------------------------------------------
    # Background generation (sequential: arc → location)
    # ------------------------------------------------------------------

    async def _run_generation(self, client: "OllamaClient") -> None:
        """Run arc then location generation sequentially with retry."""
        from ai.arc_generator import ArcGenerator
        from ai.client import OllamaUnavailableError
        from ai.world_generator import WorldGenerator

        arc_gen = ArcGenerator(client)
        world_gen = WorldGenerator(client)

        # --- Arc generation (mandatory) ---
        self._generation_phase = GenerationPhase.ARC
        arc_start = time.monotonic()
        logger.info("GENERATION arc_start campaign=%s", self.campaign.id)
        try:
            arc = await self._retry_llm_call(
                lambda: arc_gen.generate(
                    self.campaign.name,
                    len(self.player_ids),
                    self.language,
                ),
            )
        except OllamaUnavailableError:
            self._generation_phase = GenerationPhase.FAILED
            logger.error(
                "GENERATION failed campaign=%s phase=ARC reason=OllamaUnavailableError",
                self.campaign.id,
            )
            await self._notify_generation_failed()
            return

        self.story_arc = arc.model_copy(
            update={"campaign_id": self.campaign.id},
        )
        logger.info(
            "GENERATION arc_done campaign=%s elapsed=%.1fs beats=%d",
            self.campaign.id,
            time.monotonic() - arc_start,
            len(self.story_arc.beats),
        )

        # --- Location generation (mandatory, uses arc context) ---
        self._generation_phase = GenerationPhase.LOCATION
        loc_start = time.monotonic()
        logger.info("GENERATION loc_start campaign=%s", self.campaign.id)
        arc_context = (
            f"Campaign: {self.campaign.name}. "
            f"Villain: {self.story_arc.villain_name}. "
            f"First beat: {self.story_arc.beats[0].description if self.story_arc.beats else 'unknown'}."
        )
        try:
            location = await self._retry_llm_call(
                lambda: world_gen.generate(
                    campaign_context=arc_context,
                    location_type="starting_area",
                    language=self.language,
                ),
            )
        except OllamaUnavailableError:
            self._generation_phase = GenerationPhase.FAILED
            logger.error(
                "GENERATION failed campaign=%s phase=LOCATION reason=OllamaUnavailableError",
                self.campaign.id,
            )
            await self._notify_generation_failed()
            return

        self.current_location = location
        self.campaign.current_location = location.name
        self._generation_phase = GenerationPhase.READY
        logger.info(
            "GENERATION loc_done campaign=%s elapsed=%.1fs location=%r",
            self.campaign.id,
            time.monotonic() - loc_start,
            location.name,
        )
        logger.info(
            "GENERATION total campaign=%s elapsed=%.1fs",
            self.campaign.id,
            time.monotonic() - self._gen_start,
        )

        # Both succeeded — check if players are ready
        await self._check_ready()

    async def _retry_llm_call(self, fn: "Callable[[], T]") -> "T":
        """Retry a blocking LLM call with backoff and player-facing notice.

        Delegates to the shared :func:`bot.llm_retry.retry_llm_call` helper,
        adding a one-shot Discord notification on the first retry.
        """
        async def _on_retry(_attempt: int) -> None:
            if not self._notified_ollama_waiting:
                self._notified_ollama_waiting = True
                await self.channel.send(
                    "⚠️ Génération en attente — Game Master est occupé. "
                    "Nouvelle tentative en cours, la campagne démarrera automatiquement.",
                )

        return await retry_llm_call(
            fn,
            max_retries=MAX_RETRIES,
            delays=tuple(RETRY_DELAYS),
            on_retry=_on_retry,
            log_label=f"GENERATION campaign={self.campaign.id}",
        )

    async def _notify_generation_failed(self) -> None:
        """Mark generation as failed and notify the channel."""
        self._generation_failed = True
        self._generation_phase = GenerationPhase.FAILED
        await self.channel.send(
            "Ollama est indisponible. Impossible de demarrer la campagne. "
            "Verifiez que le serveur Ollama est en cours d'execution, "
            "puis relancez avec `/start_campaign`.",
        )
        # Clean up launcher
        self.bot.launchers.pop(self.channel.id, None)

    # ------------------------------------------------------------------
    # Launch check
    # ------------------------------------------------------------------

    async def _check_ready(self) -> None:
        """Check if all players are GEAR_DONE and generation succeeded. If so, launch."""
        if self._launched or self._generation_failed:
            return

        all_ready = all(
            progress == PlayerProgress.GEAR_DONE
            for progress in self.player_progress.values()
        )
        generation_done = (
            self.story_arc is not None and self.current_location is not None
        )

        logger.info(
            "ONBOARD check all_ready=%s arc_done=%s campaign=%s",
            all_ready, generation_done, self.campaign.id,
        )

        if all_ready and not generation_done and not self._notified_players_ready:
            self._notified_players_ready = True
            await self.channel.send(
                "✅ Tous les joueurs sont prêts ! Génération de l'univers en cours...",
            )

        if generation_done and not all_ready and not self._notified_generation_ready:
            self._notified_generation_ready = True
            await self.channel.send(
                "✅ Univers généré ! En attente des joueurs...",
            )

        if not all_ready or not generation_done:
            return

        await self._launch_campaign()

    async def _launch_campaign(self) -> None:
        """Create the GameSession, send opening narrative, clean up."""
        if self._launched:
            return
        self._launched = True

        logger.info("LAUNCH starting campaign=%s", self.campaign.id)

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

        # Write the static story-bible header now that every plan element is
        # frozen. Failure is logged but non-fatal — gameplay must not block
        # on an audit artefact.
        if session.story_bible is not None:
            try:
                session.story_bible.write_header(
                    campaign=self.campaign,
                    story_arc=self.story_arc,
                    location=self.current_location,
                    characters=self.characters,
                )
            except Exception:
                logger.warning(
                    "story_bible write_header failed for campaign=%s",
                    self.campaign.id, exc_info=True,
                )

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

        embed = build_narrative_embed(desc, tone="dramatic", footer_override=f"Campagne : {self.campaign.name}")
        await self.channel.send(embed=embed)

        # Lot G — hydrate Location.npcs_present into real NPC rows so the
        # entity resolver can match TALK targets. Must run before the scene
        # embed is built so the embed reflects the canonical state.
        if self.current_location is not None:
            from bot.scene_hydration import hydrate_scene

            hydrate_scene(session, db_factory=self.bot.db_factory)

        # Lot A — scene awareness: post a structured scene embed so players
        # know who/what is in front of them. Without this, players type
        # generic phrases like "le villageois" against names like
        # "Jeanne, la Villageoise Terrifiée" and the resolver fails.
        if self.current_location is not None:
            scene_embed = build_scene_embed(
                location=self.current_location,
                language=self.language,
            )
            await self.channel.send(embed=scene_embed)
            logger.info(
                "SCENE posted campaign=%s location=%s npcs=%d",
                self.campaign.id,
                self.current_location.name,
                len(self.current_location.npcs_present),
            )
        else:
            logger.debug(
                "SCENE skipped campaign=%s reason=no_current_location",
                self.campaign.id,
            )

        player_count = len(self.characters)
        logger.info(
            "LAUNCH campaign=%s players=%d arc_beats=%d location=%s",
            self.campaign.id,
            player_count,
            len(self.story_arc.beats) if self.story_arc else 0,
            self.current_location.name if self.current_location else "none",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_create_task(self, coro: object) -> asyncio.Task[None]:
        """Create an asyncio task with error logging on failure."""
        task: asyncio.Task[None] = asyncio.create_task(coro)  # type: ignore[arg-type]

        def _on_done(t: asyncio.Task[None]) -> None:
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "Background task failed for campaign %s: %s",
                    self.campaign.id, exc,
                    exc_info=exc,
                )

        task.add_done_callback(_on_done)
        return task
