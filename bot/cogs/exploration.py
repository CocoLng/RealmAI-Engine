"""Exploration cog — /look, /search, /talk, /move."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds.narrative_embed import build_narrative_embed
from bot.story_bible_logger import record_turn_and_maybe_check
from db.repositories import NPCRepository

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


class ExplorationCog(commands.Cog):
    """Exploration commands for navigating the world."""

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="look",
        description="[Deprecie] Mentionne le bot avec ton action (ex: @bot j'observe autour)",
    )
    async def look(self, interaction: discord.Interaction) -> None:
        """Describe the current location."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        if session.combat_state is not None:
            await interaction.response.send_message(
                "Impossible pendant un combat !", ephemeral=True,
            )
            return

        location = session.current_location
        if location is None:
            await interaction.response.send_message(
                "Aucun lieu actuel. La campagne vient de commencer.",
                ephemeral=True,
            )
            return

        logger.info("EXPLORE look user=%s location=%s", interaction.user, location.name)

        # Build description
        description = location.description or location.name
        mechanics = ""
        if location.connections:
            mechanics += f"Sorties: {', '.join(location.connections)}"
        if location.npcs_present:
            mechanics += f"\nPNJ: {', '.join(location.npcs_present)}"
        if location.items_available:
            mechanics += f"\nObjets: {', '.join(location.items_available)}"

        # Narrate if AI available
        narrative = description
        tone = "dramatic"
        if session.narrator:
            try:
                result = await asyncio.to_thread(
                    session.narrator.narrate,
                    f"The party looks around {location.name}: {description}",
                    "",
                    session.language,
                )
                narrative = result.narrative
                tone = result.tone
            except Exception:
                pass

        embed = build_narrative_embed(narrative, mechanics or "Rien de notable.", tone)
        await interaction.response.send_message(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=str(interaction.user),
            command="/look",
            args="",
            mechanics=mechanics or "Rien de notable.",
            narrative=narrative,
        )

    @app_commands.command(
        name="search",
        description="[Deprecie] Mentionne le bot avec ton action (ex: @bot je fouille l'autel)",
    )
    @app_commands.describe(target="Ce que tu cherches")
    async def search(self, interaction: discord.Interaction, target: str) -> None:
        """Search for something in the current location."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        if session.combat_state is not None:
            await interaction.response.send_message(
                "Impossible pendant un combat !", ephemeral=True,
            )
            return

        location = session.current_location
        if location is None:
            await interaction.response.send_message(
                "Aucun lieu a fouiller.", ephemeral=True,
            )
            return

        # Check if target matches items or NPCs
        found = target.lower() in [i.lower() for i in location.items_available]
        logger.info(
            "EXPLORE search user=%s target=%r location=%s found=%s",
            interaction.user, target, location.name, found,
        )
        mechanics = f"Recherche de '{target}' dans {location.name}: "
        mechanics += "Trouve !" if found else "Rien trouve."

        narrative = mechanics
        tone = "dramatic"
        if session.narrator:
            try:
                result = session.narrator.narrate(
                    f"Searching for {target} in {location.name}. {'Found!' if found else 'Nothing found.'}",
                    "",
                )
                narrative = result.narrative
                tone = result.tone
            except Exception:
                pass

        embed = build_narrative_embed(narrative, mechanics, tone)
        await interaction.response.send_message(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=str(interaction.user),
            command="/search",
            args=f'target="{target}"',
            mechanics=mechanics,
            narrative=narrative,
        )

    @app_commands.command(
        name="talk",
        description="[Deprecie] Mentionne le bot avec ton action (ex: @bot je parle au pretre)",
    )
    @app_commands.describe(npc="Nom du PNJ")
    async def talk(self, interaction: discord.Interaction, npc: str) -> None:
        """Initiate conversation with an NPC."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        if session.combat_state is not None:
            await interaction.response.send_message(
                "Impossible pendant un combat !", ephemeral=True,
            )
            return

        location = session.current_location
        if location is None:
            await interaction.response.send_message(
                "Aucun lieu actuel.", ephemeral=True,
            )
            return

        # Check NPC is present
        npc_present = npc.lower() in [n.lower() for n in location.npcs_present]
        if not npc_present:
            await interaction.response.send_message(
                f"Aucun PNJ nomme '{npc}' ici.", ephemeral=True,
            )
            return

        await interaction.response.defer()

        # Try to load NPC from DB for personality
        npc_data = None
        db_session = self.bot.db_factory()
        try:
            npc_repo = NPCRepository(db_session)
            npc_data = npc_repo.get_by_name(npc, session.campaign.id)
        finally:
            db_session.close()

        # Use NPC agent if available
        mechanics = f"Conversation avec {npc}"
        narrative = f"{npc} vous regarde."
        tone = "dramatic"

        if session.npc_agent and npc_data:
            try:
                response = await asyncio.to_thread(
                    session.npc_agent.respond,
                    npc_data,
                    player_input="initiates conversation",
                    context_prompt="",
                    language=session.language,
                )
                narrative = response.dialogue
                logger.info(
                    "EXPLORE talk user=%s npc=%s disposition_change=%+d revealed=%d",
                    interaction.user, npc,
                    response.disposition_change, len(response.revealed_info),
                )
                # Apply disposition change
                if response.disposition_change != 0:
                    mechanics += f" (disposition: {response.disposition_change:+d})"
                    # Persist NPC disposition update
                    db_session = self.bot.db_factory()
                    try:
                        npc_repo = NPCRepository(db_session)
                        npc_repo.update(npc_data, session.campaign.id)
                        db_session.commit()
                    finally:
                        db_session.close()
            except Exception:
                logger.warning("NPC agent failed for %s", npc)
        elif session.narrator:
            try:
                result = session.narrator.narrate(
                    f"The party talks to {npc}.", "",
                )
                narrative = result.narrative
                tone = result.tone
            except Exception:
                pass

        embed = build_narrative_embed(narrative, mechanics, tone)
        await interaction.followup.send(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=str(interaction.user),
            command="/talk",
            args=f'npc="{npc}"',
            mechanics=mechanics,
            narrative=narrative,
        )

    @app_commands.command(
        name="move",
        description="[Deprecie] Mentionne le bot avec ton action (ex: @bot j'entre dans la cathedrale)",
    )
    @app_commands.describe(direction="Nom du lieu de destination")
    async def move(self, interaction: discord.Interaction, direction: str) -> None:
        """Move to a connected location."""
        session = self.bot.get_session(interaction.channel_id)
        if session is None:
            await interaction.response.send_message(
                "Aucune session active.", ephemeral=True,
            )
            return

        if session.combat_state is not None:
            await interaction.response.send_message(
                "Impossible pendant un combat !", ephemeral=True,
            )
            return

        location = session.current_location
        if location is None:
            await interaction.response.send_message(
                "Aucun lieu actuel.", ephemeral=True,
            )
            return

        # Check if direction matches a connection
        match = None
        for conn in location.connections:
            if conn.lower() == direction.lower():
                match = conn
                break

        if match is None:
            available = ", ".join(location.connections) if location.connections else "aucune"
            await interaction.response.send_message(
                f"Pas de chemin vers '{direction}'. Sorties: {available}",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        # Lot D — `/move` is now a thin shim around the shared helper.
        logger.warning(
            "MOVE deprecated_command_used user=%s campaign=%s direction=%r",
            interaction.user.id, session.campaign.id, direction,
        )
        try:
            await interaction.followup.send(
                ":warning: `/move` est déprécié. Tape simplement `@bot {action}` "
                "pour te déplacer (ex: `@bot j'entre dans le donjon`).",
                ephemeral=True,
            )
        except Exception:
            pass

        from bot.world_navigation import LocationChangeError, change_location
        try:
            dest = await change_location(
                session, match, db_factory=self.bot.db_factory,
            )
        except LocationChangeError as exc:
            from world.location import Location
            dest = Location(name=match, description=f"Vous arrivez a {match}.")
            session.current_location = dest
            session.campaign.current_location = dest.name
            logger.warning(
                "EXPLORE /move fallback user=%s reason=%s",
                interaction.user, exc.reason,
            )

        logger.info(
            "EXPLORE move user=%s from=%s to=%s",
            interaction.user, location.name, dest.name,
        )

        # Narrate arrival
        mechanics = f"Deplacement: {location.name} → {dest.name}"
        narrative = dest.description or f"Vous arrivez a {dest.name}."
        tone = "dramatic"
        if session.narrator:
            try:
                result = await asyncio.to_thread(
                    session.narrator.narrate,
                    f"The party arrives at {dest.name}: {dest.description}",
                    "",
                    session.language,
                )
                narrative = result.narrative
                tone = result.tone
            except Exception:
                pass

        embed = build_narrative_embed(narrative, mechanics, tone)
        await interaction.followup.send(embed=embed)
        await record_turn_and_maybe_check(
            session,
            user_name=str(interaction.user),
            command="/move",
            args=f'direction="{direction}"',
            mechanics=mechanics,
            narrative=narrative,
        )


async def setup(bot: commands.Bot) -> None:
    """Register the cog with the bot."""
    await bot.add_cog(ExplorationCog(bot))  # type: ignore[arg-type]
