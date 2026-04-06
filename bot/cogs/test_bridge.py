"""TestBridge cog — translates !test commands into cog calls for automated testing.

Only active when TEST_MODE=true in environment. Only accepts commands from
the authorized tester bot (TESTER_BOT_ID).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands

from engine.character import (
    CharacterClass,
    Race,
    apply_racial_bonuses,
    create_character,
    roll_ability_scores,
)
from engine.inventory import create_inventory
from engine.spells import create_spellcaster_state

if TYPE_CHECKING:
    from bot.bot import RealmBot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ChannelTestInteraction — fake Interaction that posts to the real channel
# ---------------------------------------------------------------------------


@dataclass
class _ChannelResponse:
    """Mimics interaction.response — posts to channel instead."""

    channel: discord.TextChannel
    _responded: bool = False

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> discord.Message | None:
        """Post the response to the channel."""
        send_kwargs: dict[str, Any] = {}
        if content:
            send_kwargs["content"] = content
        if embed:
            send_kwargs["embed"] = embed
        if view:
            send_kwargs["view"] = view
        self._responded = True
        if send_kwargs:
            return await self.channel.send(**send_kwargs)
        return None

    async def defer(self, *, ephemeral: bool = False, **kwargs: Any) -> None:
        """No-op defer."""
        self._responded = True

    def is_done(self) -> bool:
        """Whether the response has been sent."""
        return self._responded


@dataclass
class _ChannelFollowup:
    """Mimics interaction.followup — posts to channel."""

    channel: discord.TextChannel

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> discord.Message | None:
        """Post the followup to the channel."""
        send_kwargs: dict[str, Any] = {}
        if content:
            send_kwargs["content"] = content
        if embed:
            send_kwargs["embed"] = embed
        if view:
            send_kwargs["view"] = view
        if send_kwargs:
            return await self.channel.send(**send_kwargs)
        return None


@dataclass
class _VirtualMember:
    """Lightweight mock of a Discord Member for virtual players."""

    id: int
    name: str
    display_name: str
    mention: str = ""

    def __post_init__(self) -> None:
        if not self.mention:
            self.mention = f"<@{self.id}>"

    def __str__(self) -> str:
        return self.name


class ChannelTestInteraction:
    """Fake discord.Interaction that posts responses to a real channel."""

    def __init__(
        self,
        bot: RealmBot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        user: _VirtualMember,
    ) -> None:
        self.client = bot
        self.guild = guild
        self.guild_id = guild.id
        self.channel = channel
        self.channel_id = channel.id
        self.user = user  # type: ignore[assignment]
        self.response = _ChannelResponse(channel)
        self.followup = _ChannelFollowup(channel)


# ---------------------------------------------------------------------------
# TestBridge Cog
# ---------------------------------------------------------------------------


class TestBridge(commands.Cog):
    """Test bridge — translates !test commands from the tester bot into cog calls.

    Only active when TEST_MODE=true. Only accepts commands from TESTER_BOT_ID.
    """

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot
        self.tester_bot_id = int(os.environ.get("TESTER_BOT_ID", "0"))
        self.virtual_players: dict[int, _VirtualMember] = {}
        self.active_views: dict[int, discord.ui.View] = {}

    def _get_virtual_player(self, idx: int) -> _VirtualMember:
        """Get or create a virtual player by index."""
        if idx not in self.virtual_players:
            self.virtual_players[idx] = _VirtualMember(
                id=200_000_000 + idx,
                name=f"TestPlayer{idx}",
                display_name=f"Test Player {idx}",
            )
        return self.virtual_players[idx]

    @staticmethod
    def _parse(content: str) -> tuple[str, dict[str, str], int]:
        """Parse '!test [player=N] command key=value ...' into (command, args, player_idx).

        Returns:
            command: The command name
            args: Key-value argument dict
            player_idx: Virtual player index (default 1)
        """
        # Remove "!test " prefix
        rest = content[6:].strip()
        parts = shlex.split(rest)

        player_idx = 1
        args: dict[str, str] = {}
        command = ""

        i = 0
        while i < len(parts):
            part = parts[i]
            if "=" in part:
                key, value = part.split("=", 1)
                if key == "player":
                    player_idx = int(value)
                else:
                    args[key] = value
            elif not command:
                command = part
            i += 1

        return command, args, player_idx

    def _make_interaction(
        self, guild: discord.Guild, channel: discord.TextChannel, player_idx: int,
    ) -> ChannelTestInteraction:
        """Create a ChannelTestInteraction for the virtual player."""
        user = self._get_virtual_player(player_idx)
        return ChannelTestInteraction(self.bot, guild, channel, user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for !test commands from the authorized tester bot."""
        # Only accept from the tester bot
        if message.author.id != self.tester_bot_id:
            return
        if not message.content.startswith("!test "):
            return
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return

        command, args, player_idx = self._parse(message.content)
        logger.info("TESTBRIDGE command=%s args=%s player=%d", command, args, player_idx)

        try:
            await self._dispatch(command, args, player_idx, message.guild, message.channel)
        except Exception:
            logger.exception("TESTBRIDGE error command=%s", command)
            await message.channel.send(f"TestBridge error: {command}")

    async def _dispatch(
        self,
        command: str,
        args: dict[str, str],
        player_idx: int,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        """Route command to the appropriate cog handler."""
        inter = self._make_interaction(guild, channel, player_idx)

        if command == "start_campaign":
            await self._handle_start_campaign(inter, args)
        elif command == "create_character":
            await self._handle_create_character(inter, args)
        elif command == "save":
            cog = self.bot.get_cog("SessionCog")
            if cog:
                await cog.save.callback(cog, inter)  # type: ignore[union-attr, arg-type]
        elif command == "resume":
            cog = self.bot.get_cog("SessionCog")
            if cog:
                await cog.resume.callback(cog, inter)  # type: ignore[union-attr, arg-type]
        elif command == "look":
            cog = self.bot.get_cog("ExplorationCog")
            if cog:
                await cog.look.callback(cog, inter)  # type: ignore[union-attr, arg-type]
        elif command == "move":
            cog = self.bot.get_cog("ExplorationCog")
            if cog:
                direction = args.get("direction", "")
                await cog.move.callback(cog, inter, direction)  # type: ignore[union-attr, arg-type]
        elif command == "search":
            cog = self.bot.get_cog("ExplorationCog")
            if cog:
                target = args.get("target", "")
                await cog.search.callback(cog, inter, target)  # type: ignore[union-attr, arg-type]
        elif command == "talk":
            cog = self.bot.get_cog("ExplorationCog")
            if cog:
                npc = args.get("npc", "")
                await cog.talk.callback(cog, inter, npc)  # type: ignore[union-attr, arg-type]
        elif command == "roll":
            cog = self.bot.get_cog("RollsCog")
            if cog:
                expr = args.get("expression", "1d20")
                await cog.roll_dice.callback(cog, inter, expr)  # type: ignore[union-attr, arg-type]
        elif command == "inventory":
            cog = self.bot.get_cog("InventoryCog")
            if cog:
                await cog.inventory.callback(cog, inter, public=True)  # type: ignore[union-attr, arg-type]
        elif command == "equip":
            cog = self.bot.get_cog("InventoryCog")
            if cog:
                item = args.get("item", "")
                slot = args.get("slot", "")
                await cog.equip.callback(cog, inter, item, slot)  # type: ignore[union-attr, arg-type]
        elif command == "unequip":
            cog = self.bot.get_cog("InventoryCog")
            if cog:
                slot = args.get("slot", "")
                await cog.unequip.callback(cog, inter, slot)  # type: ignore[union-attr, arg-type]
        elif command == "use_item":
            cog = self.bot.get_cog("InventoryCog")
            if cog:
                item = args.get("item", "")
                await cog.use_item.callback(cog, inter, item)  # type: ignore[union-attr, arg-type]
        elif command == "character":
            cog = self.bot.get_cog("CharacterCog")
            if cog:
                await cog.character.callback(cog, inter, public=True)  # type: ignore[union-attr, arg-type]
        elif command == "game_state":
            await self._handle_game_state(channel)
        else:
            await channel.send(f"TestBridge: commande inconnue '{command}'")

    async def _handle_start_campaign(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Handle start_campaign — bypasses channel creation.

        Unlike the real /start_campaign which creates a dedicated channel,
        the TestBridge registers the session on the current test channel.
        """
        import uuid
        from datetime import datetime, timezone

        from bot.game_session import GameSession, create_ai_services
        from db.repositories import CampaignChannelRepository, CampaignRepository
        from world.campaign import Campaign

        theme = args.get("theme", "Test Campaign")
        players = int(args.get("players", "1"))

        # Build campaign
        player_ids = [str(self._get_virtual_player(i).id) for i in range(1, players + 1)]
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name=theme,
            created_at=datetime.now(timezone.utc),
            player_names=player_ids,
        )

        # Persist campaign + channel mapping
        db_session = self.bot.db_factory()
        try:
            CampaignRepository(db_session).save(campaign)
            db_session.flush()
            CampaignChannelRepository(db_session).save(
                inter.channel_id, campaign.id, inter.guild_id,
            )
            db_session.commit()
        finally:
            db_session.close()

        # Create in-memory session on the TEST channel
        session = GameSession(campaign=campaign)
        create_ai_services(session)
        self.bot.sessions[inter.channel_id] = session

        logger.info(
            "TESTBRIDGE start_campaign=%s theme=%r players=%d channel=%s",
            campaign.id, theme, players, inter.channel_id,
        )

        from bot.embeds.narrative_embed import build_narrative_embed

        desc = "Bienvenue, aventuriers !"
        if session.current_location:
            desc = session.current_location.description or desc
        embed = build_narrative_embed(desc, f"Campagne: {theme}", "dramatic")
        await inter.channel.send(embed=embed)
        await inter.channel.send(f"Campagne **{theme}** lancee dans ce canal (test mode).")

    async def _handle_create_character(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Create a character directly (bypasses multi-step view flow)."""
        session = self.bot.get_session(inter.channel_id)
        if session is None:
            await inter.channel.send("Aucune session active.")
            return

        name = args.get("name", "TestCharacter")
        race = Race(args.get("race", "Human"))
        char_class = CharacterClass(args.get("class_", "Fighter"))

        scores = roll_ability_scores()
        scores = apply_racial_bonuses(scores, race)
        character = create_character(name=name, race=race, char_class=char_class, ability_scores=scores)
        inventory = create_inventory()
        spellcaster = create_spellcaster_state(char_class, 1)

        user_id = inter.user.id
        session.characters[user_id] = character
        session.inventories[user_id] = inventory
        session.spellcasters[user_id] = spellcaster

        # Persist to DB
        db_session = self.bot.db_factory()
        try:
            from db.repositories import PlayerCharacterRepository

            pc_repo = PlayerCharacterRepository(db_session)
            pc_repo.save(user_id, session.campaign.id, character, inventory, spellcaster)
            db_session.commit()
        finally:
            db_session.close()

        from bot.embeds.character_embed import build_character_embed

        embed = build_character_embed(character)
        await inter.channel.send(content=f"**{name}** cree !", embed=embed)

    async def _handle_game_state(self, channel: discord.TextChannel) -> None:
        """Serialize and post the active game session state."""
        session = self.bot.get_session(channel.id)
        if session is None:
            await channel.send("```json\n{\"error\": \"No active session\"}\n```")
            return

        state: dict[str, Any] = {
            "campaign": {
                "name": session.campaign.name,
                "id": session.campaign.id,
            },
            "characters": [
                {
                    "user_id": uid,
                    "name": char.name,
                    "race": char.race.value,
                    "class": char.char_class.value,
                    "hp": char.hp,
                    "max_hp": char.max_hp,
                    "ac": char.ac,
                    "xp": char.xp,
                    "level": char.level,
                }
                for uid, char in session.characters.items()
            ],
            "combat_active": session.combat_state is not None,
            "combat_state": None,
            "location": None,
            "npcs": [
                {"name": npc.name, "disposition": npc.disposition.value}
                for npc in session.npcs.values()
            ],
            "quests": [
                {"title": q.title, "status": q.status.value}
                for q in session.quests
            ],
        }

        if session.combat_state is not None:
            state["combat_state"] = {
                "round": session.combat_state.round_number,
                "turn_index": session.combat_state.current_turn_index,
                "combatants": [
                    {
                        "name": c.name,
                        "side": c.side.value,
                        "hp": c.character.hp,
                        "max_hp": c.character.max_hp,
                        "is_alive": c.is_alive,
                    }
                    for c in session.combat_state.combatants
                ],
            }

        if session.current_location is not None:
            state["location"] = {
                "name": session.current_location.name,
                "description": session.current_location.description,
            }

        await channel.send(f"```json\n{json.dumps(state, indent=2)}\n```")


async def setup(bot: commands.Bot) -> None:
    """Register the TestBridge cog (only if TEST_MODE is enabled)."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        await bot.add_cog(TestBridge(bot))  # type: ignore[arg-type]
        logger.info("TestBridge cog loaded (TEST_MODE=true)")
    else:
        logger.debug("TestBridge cog skipped (TEST_MODE is not true)")
