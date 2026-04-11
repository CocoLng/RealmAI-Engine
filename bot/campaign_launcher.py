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

from bot.embeds.character_embed import build_party_card_embed
from bot.embeds.narrative_embed import build_countdown_embed, build_opening_crawl_embed
from bot.embeds.scene_embed import build_scene_embed
from bot.game_session import GameSession, create_ai_services
from bot.i18n import CLASS_LABELS, RACE_LABELS, get_kit_label, get_label
from bot.llm_retry import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, retry_llm_call
from bot.views.character_create_view import CharacterCreateView
from bot.views.character_edit_flow import CharacterEditFlow
from bot.views.character_edit_view import CharacterEditView
from bot.views.force_launch_view import ForceLaunchView
from bot.views.start_onboarding_view import StartOnboardingView
from bot.views.starter_gear_view import StarterGearView
from engine.character import (
    Ability,
    Character,
    assign_standard_array,
    create_character,
)
from engine.inventory import Inventory, create_inventory
from engine.spells import SpellcasterState, create_spellcaster_state
from engine.starter_gear import StarterKit, apply_starter_kit, get_starter_kits
from world.campaign import Campaign
from world.location import Location
from world.story_arc import StoryArc

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any, TypeVar

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
    creator_id: int = 0
    player_progress: dict[int, PlayerProgress] = field(default_factory=dict)
    characters: dict[int, Character] = field(default_factory=dict)
    inventories: dict[int, Inventory] = field(default_factory=dict)
    spellcasters: dict[int, SpellcasterState | None] = field(default_factory=dict)
    raw_assignments: dict[int, dict[Ability, int]] = field(default_factory=dict)
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
    _force_launch_offered: bool = field(default=False, repr=False)
    _gen_start: float = field(default=0.0, repr=False)
    _ephemeral_interactions: dict[int, list[discord.Interaction]] = field(
        default_factory=dict, repr=False,
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

        if self._launched:
            await interaction.response.send_message(
                "La partie a deja commence !", ephemeral=True,
            )
            return

        if self.player_progress[user_id] != PlayerProgress.PENDING:
            # Player already has a character — show edit menu instead of full wizard
            character = self.characters.get(user_id)
            if character is None:
                await interaction.response.send_message(
                    "Erreur interne.", ephemeral=True,
                )
                return

            edit_view = CharacterEditView(
                character=character,
                language=self.language,
                on_modify=self._make_on_modify(user_id),
            )
            await interaction.response.send_message(
                edit_view.get_summary_text(), view=edit_view, ephemeral=True,
            )
            return

        view = CharacterCreateView(language=self.language, on_complete=self._on_character_created)
        await interaction.response.send_message(
            "Choisis ta race :", view=view, ephemeral=True,
        )
        self._ephemeral_interactions.setdefault(user_id, []).append(interaction)

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
        assert view.ability_assignments is not None
        assert view.skill_proficiencies is not None

        scores = assign_standard_array(view.ability_assignments, view.race)
        character = create_character(
            name=view.character_name,
            race=view.race,
            char_class=view.char_class,
            ability_scores=scores,
            alignment=view.alignment,
            skill_proficiencies=view.skill_proficiencies,
        )

        inventory = create_inventory()
        spellcaster = create_spellcaster_state(view.char_class, level=1)

        self.characters[user_id] = character
        self.inventories[user_id] = inventory
        self.spellcasters[user_id] = spellcaster
        self.raw_assignments[user_id] = view.ability_assignments
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
        self._ephemeral_interactions.setdefault(user_id, []).append(interaction)

    async def _on_gear_selected(
        self,
        interaction: discord.Interaction,
        kit: StarterKit,
    ) -> None:
        """Called when a player selects a starter gear kit."""
        user_id = interaction.user.id

        if self.player_progress.get(user_id) == PlayerProgress.PENDING:
            logger.warning("ONBOARD stale gear callback user=%s campaign=%s", interaction.user, self.campaign.id)
            return

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
        self._ephemeral_interactions.setdefault(user_id, []).append(interaction)

        # Announce publicly
        await self.channel.send(
            f"**{character.name}** ({race_display} {cls_display}) est pret(e) ! [{kit_display}]",
        )

        # Clean up ephemeral onboarding messages for this player
        await self._cleanup_ephemeral(user_id)

        await self._check_ready()

    # ------------------------------------------------------------------
    # Character edit callbacks
    # ------------------------------------------------------------------

    def _make_on_modify(
        self, user_id: int,
    ) -> "Callable[[discord.Interaction, list[str]], Coroutine[Any, Any, None]]":
        """Create an on_modify callback bound to a specific user_id."""
        async def _on_modify(
            interaction: discord.Interaction,
            selected_fields: list[str],
        ) -> None:
            character = self.characters.get(user_id)
            if character is None:
                await interaction.response.send_message(
                    "Erreur interne.", ephemeral=True,
                )
                return

            raw = self.raw_assignments.get(user_id, {})
            flow = CharacterEditFlow(
                character=character,
                raw_assignments=raw,
                language=self.language,
                on_complete=self._on_character_edited,
            )
            await flow.start(interaction, selected_fields)

        return _on_modify

    async def _on_character_edited(
        self,
        interaction: discord.Interaction,
        flow: CharacterEditFlow,
    ) -> None:
        """Called when a player finishes editing their character."""
        user_id = interaction.user.id

        scores = assign_standard_array(flow.ability_assignments, flow.race)
        character = create_character(
            name=flow.character_name,
            race=flow.race,
            char_class=flow.char_class,
            ability_scores=scores,
            alignment=flow.alignment,
            skill_proficiencies=flow.skill_proficiencies,
        )

        spellcaster = create_spellcaster_state(flow.char_class, level=1)

        self.characters[user_id] = character
        self.spellcasters[user_id] = spellcaster
        self.raw_assignments[user_id] = flow.ability_assignments

        logger.info(
            "ONBOARD edit user=%s name=%s race=%s class=%s class_changed=%s campaign=%s",
            interaction.user, character.name, flow.race.value,
            flow.char_class.value, flow.class_changed, self.campaign.id,
        )

        # Persist updated character to DB
        db_session = self.bot.db_factory()
        try:
            from db.repositories import PlayerCharacterRepository

            pc_repo = PlayerCharacterRepository(db_session)
            inventory = self.inventories.get(user_id, create_inventory())
            pc_repo.update(
                user_id, self.campaign.id, character,
                inventory, spellcaster,
            )
            db_session.commit()
        finally:
            db_session.close()

        if flow.class_changed:
            # Class changed — reset inventory and show gear selection again
            self.inventories[user_id] = create_inventory()
            self.player_progress[user_id] = PlayerProgress.CHARACTER_DONE
            self._notified_players_ready = False

            kits = get_starter_kits(flow.char_class)
            gear_view = StarterGearView(
                kits=kits, on_selected=self._on_gear_selected, language=self.language,
            )
            items_desc = "\n".join(
                f"**{get_kit_label(self.language, kit.name, 'name')}** — "
                f"{get_kit_label(self.language, kit.name, 'description') or kit.description}"
                for kit in kits
            )
            await interaction.response.send_message(
                f"Personnage **{character.name}** modifie !\n\n"
                f"Ta classe a change — choisis ton equipement de depart :\n{items_desc}",
                view=gear_view,
                ephemeral=True,
            )
            await self.channel.send(
                f"**{character.name}** a modifie son personnage (nouvelle classe).",
            )
        else:
            # Class unchanged — keep gear, stay at current progress
            await interaction.response.send_message(
                f"Personnage **{character.name}** modifie !", ephemeral=True,
            )
            await self.channel.send(
                f"**{character.name}** a modifie son personnage.",
            )

    # ------------------------------------------------------------------
    # Background generation (sequential: arc → location)
    # ------------------------------------------------------------------

    async def _run_generation(self, client: "OllamaClient") -> None:
        """Run arc then location generation sequentially with retry.

        Uses the Arc Recipe Engine for structural variety:
        1. Generate a code-driven recipe (no LLM call)
        2. Pass recipe to ArcGenerator (single LLM call)
        3. Pass enriched context to WorldGenerator (single LLM call)
        """
        import random

        from ai.arc_generator import ArcGenerator
        from ai.client import OllamaUnavailableError
        from ai.world_generator import WorldGenerator
        from engine.arc_recipes import generate_recipe

        arc_gen = ArcGenerator(client)
        world_gen = WorldGenerator(client)

        # --- Generate recipe (pure Python, instant) ---
        recipe = generate_recipe(theme=self.campaign.name)
        logger.info(
            "GENERATION recipe campaign=%s archetype=%s tone=%s beats=%d",
            self.campaign.id, recipe.archetype, recipe.tone, recipe.num_beats,
        )

        # --- Arc generation (mandatory, single LLM call) ---
        self._generation_phase = GenerationPhase.ARC
        arc_start = time.monotonic()
        logger.info("GENERATION arc_start campaign=%s", self.campaign.id)
        try:
            arc = await self._retry_llm_call(
                lambda: arc_gen.generate(
                    self.campaign.name,
                    len(self.player_ids),
                    self.language,
                    recipe=recipe,
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

        # --- Location generation (mandatory, single LLM call) ---
        self._generation_phase = GenerationPhase.LOCATION
        loc_start = time.monotonic()
        logger.info("GENERATION loc_start campaign=%s", self.campaign.id)
        arc_context = (
            f"Campaign: {self.campaign.name}. "
            f"Villain: {self.story_arc.villain_name}. "
            f"First beat: {self.story_arc.beats[0].description if self.story_arc.beats else 'unknown'}."
        )
        # Extract canonical location names from the arc so the world
        # generator reuses them (prevents name mismatch — see H6).
        arc_location_hints = [
            beat.location_hint
            for beat in self.story_arc.beats
            if beat.location_hint
        ]

        # Enriched context for variety: atmosphere + beat context + NPC hint
        atmospheres = [
            "oppressante", "féerique", "délabrée", "vivante", "silencieuse",
            "chaotique", "sacrée", "industrielle", "souterraine", "maritime",
            "aérienne", "volcanique",
        ]
        atmosphere = random.choice(atmospheres)  # noqa: S311
        first_beat = self.story_arc.beats[0] if self.story_arc.beats else None
        beat_context = first_beat.description if first_beat else None
        npc_hint = 2 if first_beat and first_beat.encounter_type == "social" else None

        try:
            location = await self._retry_llm_call(
                lambda: world_gen.generate(
                    campaign_context=arc_context,
                    location_type="starting_area",
                    language=self.language,
                    location_hints=arc_location_hints,
                    atmosphere=atmosphere,
                    beat_context=beat_context,
                    npc_count_hint=npc_hint,
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

        at_least_one_ready = any(
            p == PlayerProgress.GEAR_DONE for p in self.player_progress.values()
        )
        if (
            generation_done
            and not all_ready
            and at_least_one_ready
            and not self._force_launch_offered
        ):
            self._force_launch_offered = True
            not_ready = [
                uid for uid, p in self.player_progress.items()
                if p != PlayerProgress.GEAR_DONE
            ]
            mentions = " ".join(f"<@{uid}>" for uid in not_ready)
            view = ForceLaunchView(
                creator_id=self.creator_id,
                on_click=self._on_force_launch,
            )
            await self.channel.send(
                f"Joueurs en attente : {mentions}\n"
                f"Le createur peut lancer la partie sans eux.",
                view=view,
            )

        if not all_ready or not generation_done:
            return

        await self._launch_campaign()

    async def _on_force_launch(self, interaction: discord.Interaction) -> None:
        """Force-launch the campaign, excluding non-ready players."""
        if self._launched:
            await interaction.response.send_message(
                "La partie a deja ete lancee.", ephemeral=True,
            )
            return

        not_ready = [
            uid for uid, p in self.player_progress.items()
            if p != PlayerProgress.GEAR_DONE
        ]
        for uid in not_ready:
            self.player_ids.remove(uid)
            del self.player_progress[uid]
            self.characters.pop(uid, None)
            self.inventories.pop(uid, None)
            self.spellcasters.pop(uid, None)

        mentions = " ".join(f"<@{uid}>" for uid in not_ready)
        await interaction.response.send_message(
            f"Lancement force ! Joueurs exclus : {mentions}",
        )
        logger.info(
            "LAUNCH force creator=%s excluded=%s campaign=%s",
            interaction.user, not_ready, self.campaign.id,
        )
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

                loc_repo = LocationRepository(db_session)
                loc_repo.save(self.current_location, self.campaign.id)
                # Pre-instantiate stubs for every connection of the starting
                # location so the LLM "knows" them (they exist in the DB and
                # show up in /query prompts). Stubs are hydrated lazily on
                # first visit via ``bot.world_navigation.change_location``.
                from bot.world_navigation import create_exit_stubs

                create_exit_stubs(
                    loc_repo,
                    self.current_location.connections,
                    parent_name=self.current_location.name,
                    campaign_id=self.campaign.id,
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

        # --- Purge onboarding messages for immersion ---
        try:
            await self.channel.purge(limit=200)
        except (discord.Forbidden, discord.HTTPException):
            logger.warning(
                "LAUNCH purge failed campaign=%s", self.campaign.id, exc_info=True,
            )

        # --- Delete any remaining ephemeral onboarding messages ---
        for uid in list(self._ephemeral_interactions):
            await self._cleanup_ephemeral(uid)

        # Surface AI initialization warnings (after purge so they survive).
        for warning in session.ai_warnings:
            try:
                await self.channel.send(warning)
            except Exception:
                logger.warning(
                    "Failed to send AI warning campaign=%s", self.campaign.id,
                )

        # --- Animated countdown embed (3 → 2 → 1, then deleted) ---
        try:
            countdown_msg = await self.channel.send(
                embed=build_countdown_embed(3, self.campaign.name, self.language),
            )
            for step in (2, 1):
                await asyncio.sleep(1.5)
                await countdown_msg.edit(
                    embed=build_countdown_embed(step, self.campaign.name, self.language),
                )
            await asyncio.sleep(1.5)
            await countdown_msg.delete()
        except Exception:
            logger.warning(
                "LAUNCH countdown failed campaign=%s", self.campaign.id, exc_info=True,
            )

        # --- Party character cards (permanent, one per player) ---
        try:
            for user_id, character in self.characters.items():
                member = self.channel.guild.get_member(user_id)
                member_name = member.display_name if member else "???"
                card_embed = build_party_card_embed(
                    character, member_name, self.language,
                )
                await self.channel.send(embed=card_embed)
                await asyncio.sleep(0.3)
        except Exception:
            logger.warning(
                "LAUNCH party cards failed campaign=%s",
                self.campaign.id,
                exc_info=True,
            )

        # --- Separator ---
        try:
            await self.channel.send("\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 \u2726 \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501")
        except Exception:
            logger.warning(
                "LAUNCH separator failed campaign=%s", self.campaign.id, exc_info=True,
            )

        # Opening crawl embed
        crawl_embed = build_opening_crawl_embed(
            campaign_name=self.campaign.name,
            story_arc=self.story_arc,
            location=self.current_location,
            language=self.language,
        )
        await self.channel.send(embed=crawl_embed)

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

    async def _cleanup_ephemeral(self, user_id: int) -> None:
        """Delete all stored ephemeral interactions for a player."""
        interactions = self._ephemeral_interactions.pop(user_id, [])
        for itn in interactions:
            try:
                await itn.delete_original_response()
            except (discord.NotFound, discord.HTTPException):
                logger.debug(
                    "ephemeral delete failed user=%s campaign=%s",
                    user_id, self.campaign.id,
                )

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
