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
from typing import TYPE_CHECKING, Any, cast

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


# Commands that used to live on the deleted ExplorationCog. They are kept
# in the bridge vocabulary — the MCP tools and existing scenarios speak it —
# but resolve through the free-text action pipeline.
_EXPLORATION_COMMANDS = frozenset({"look", "move", "search", "talk"})


def _exploration_text(command: str, args: dict[str, str]) -> str:
    """Render a legacy exploration command as French free-text.

    The interpreter classifies these back into the right ActionType, so the
    bridge keeps a stable surface without a parallel command path.
    """
    if command == "move":
        return f"je vais vers {args.get('direction', '')}".strip()
    if command == "search":
        target = args.get("target", "")
        return f"je fouille {target}".strip() if target else "je fouille les lieux"
    if command == "talk":
        npc = args.get("npc", "")
        return f"je parle à {npc}".strip() if npc else "je parle aux gens ici"
    return "j'observe attentivement les alentours"


# ---------------------------------------------------------------------------
# ChannelTestInteraction — fake Interaction that posts to the real channel
# ---------------------------------------------------------------------------


class _ChannelResponse:
    """Mimics interaction.response — posts to channel instead.

    When ``bridge`` is provided, any view attached to a response is registered
    in ``bridge.active_views`` so subsequent ``click_button`` / ``select_option``
    / ``submit_modal`` commands can drive it. When ``message`` is provided,
    ``edit_message`` targets that message (matching discord.py semantics where
    the interaction's response edits the message hosting the clicked component).
    """

    def __init__(
        self,
        channel: discord.TextChannel,
        bridge: TestBridge | None = None,
        message: discord.Message | None = None,
        player_idx: int = 1,
    ) -> None:
        self.channel = channel
        self.bridge = bridge
        self.message = message
        self.player_idx = player_idx
        self._responded = False

    async def send_message(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> discord.Message | None:
        """Post the response to the channel and register the view if present."""
        send_kwargs: dict[str, Any] = {}
        if content:
            send_kwargs["content"] = content
        if embed:
            send_kwargs["embed"] = embed
        if view:
            send_kwargs["view"] = view
        self._responded = True
        msg: discord.Message | None = None
        if send_kwargs:
            msg = await self.channel.send(**send_kwargs)
        if msg is not None and view is not None and self.bridge is not None:
            self.bridge.active_views[msg.id] = view
        return msg

    async def edit_message(
        self,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        **kwargs: Any,
    ) -> None:
        """Edit the message hosting this interaction.

        When a new view is attached, it replaces the view registered under the
        message id in ``bridge.active_views``.
        """
        self._responded = True
        if self.message is None:
            logger.warning("edit_message called but no message is attached to the interaction")
            return
        edit_kwargs: dict[str, Any] = {}
        if content is not None:
            edit_kwargs["content"] = content
        if embed is not None:
            edit_kwargs["embed"] = embed
        if view is not None:
            edit_kwargs["view"] = view
        await self.message.edit(**edit_kwargs)
        if view is not None and self.bridge is not None:
            self.bridge.active_views[self.message.id] = view

    async def send_modal(self, modal: discord.ui.Modal) -> None:
        """Record the modal as pending for this player so ``submit_modal`` can find it."""
        self._responded = True
        if self.bridge is not None:
            self.bridge.pending_modals[self.player_idx] = modal

    async def defer(self, *, ephemeral: bool = False, **kwargs: Any) -> None:
        """No-op defer."""
        self._responded = True

    def is_done(self) -> bool:
        """Whether the response has been sent."""
        return self._responded


class _ChannelFollowup:
    """Mimics interaction.followup — posts to channel."""

    def __init__(
        self,
        channel: discord.TextChannel,
        bridge: TestBridge | None = None,
    ) -> None:
        self.channel = channel
        self.bridge = bridge

    async def send(
        self,
        content: str | None = None,
        *,
        embed: discord.Embed | None = None,
        view: discord.ui.View | None = None,
        ephemeral: bool = False,
        **kwargs: Any,
    ) -> discord.Message | None:
        """Post the followup and register any attached view."""
        send_kwargs: dict[str, Any] = {}
        if content:
            send_kwargs["content"] = content
        if embed:
            send_kwargs["embed"] = embed
        if view:
            send_kwargs["view"] = view
        msg: discord.Message | None = None
        if send_kwargs:
            msg = await self.channel.send(**send_kwargs)
        if msg is not None and view is not None and self.bridge is not None:
            self.bridge.active_views[msg.id] = view
        return msg


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


@dataclass
class _FakeNarrateMessage:
    """Minimal discord.Message stand-in for ActionHandlerCog._run_pipeline.

    `_run_pipeline` only touches `message.author.id`, `message.channel.send`,
    and (on error path) `message.channel.send` again. No reply needed.
    """

    channel: discord.TextChannel
    author: _VirtualMember


class ChannelTestInteraction:
    """Fake discord.Interaction that posts responses to a real channel.

    Parameters
    ----------
    bridge:
        Optional back-reference so ``response.send_message``/``edit_message``
        can register the attached view in ``bridge.active_views`` and so
        ``send_modal`` can park the modal for later ``submit_modal`` commands.
    message:
        When the interaction corresponds to a component click (button/select),
        this is the message hosting the component. ``response.edit_message``
        targets it.
    player_idx:
        Virtual player index, used to key pending modals per player.
    """

    def __init__(
        self,
        bot: RealmBot,
        guild: discord.Guild,
        channel: discord.TextChannel,
        user: _VirtualMember,
        *,
        bridge: TestBridge | None = None,
        message: discord.Message | None = None,
        player_idx: int = 1,
    ) -> None:
        self.client = bot
        self.guild = guild
        self.guild_id = guild.id
        self.channel = channel
        self.channel_id = channel.id
        self.user = user
        self.message = message
        self.response = _ChannelResponse(
            channel, bridge=bridge, message=message, player_idx=player_idx,
        )
        self.followup = _ChannelFollowup(channel, bridge=bridge)


# ---------------------------------------------------------------------------
# TestBridge Cog
# ---------------------------------------------------------------------------


class TestBridge(commands.Cog):
    """Test bridge — translates !test commands from the tester bot into cog calls.

    Only active when TEST_MODE=true. Only accepts commands from TESTER_BOT_ID.
    """

    __test__ = False  # pytest: not a test class

    def __init__(self, bot: RealmBot) -> None:
        self.bot = bot
        self.tester_bot_id = int(os.environ.get("TESTER_BOT_ID", "0"))
        self.virtual_players: dict[int, _VirtualMember] = {}
        self.active_views: dict[int, discord.ui.View] = {}
        # Modals are ephemeral dialogs with no persistent msg id. Key by player
        # so the same tester can drive multiple parallel flows concurrently.
        self.pending_modals: dict[int, discord.ui.Modal] = {}

    def _cog(self, name: str) -> Any:
        """Look up a cog for dynamic app-command dispatch.

        Returns ``Any`` on purpose: the bridge invokes slash-command
        callbacks that exist only on the concrete cog subclasses, and
        ``Bot.get_cog`` is typed as returning the ``Cog`` base. Annotating
        it honestly here beats an ``attr-defined`` ignore on every call
        site — and keeps the ``None`` check meaningful.
        """
        return self.bot.get_cog(name)

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
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        player_idx: int,
        *,
        message: discord.Message | None = None,
    ) -> ChannelTestInteraction:
        """Create a ChannelTestInteraction for the virtual player."""
        user = self._get_virtual_player(player_idx)
        return ChannelTestInteraction(
            self.bot, guild, channel, user,
            bridge=self, message=message, player_idx=player_idx,
        )

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
        elif command == "lobby":
            await self._handle_lobby(inter, args)
        elif command == "create_character":
            await self._handle_create_character(inter, args)
        elif command == "save":
            cog = self._cog("SessionCog")
            if cog:
                await cog.save.callback(cog, inter)
        elif command == "resume":
            cog = self._cog("SessionCog")
            if cog:
                await cog.resume.callback(cog, inter)
        elif command in _EXPLORATION_COMMANDS:
            # ExplorationCog and its /look /move /search /talk slash commands
            # were removed in 5681a6b; free text through the action pipeline
            # replaced them. Route these to the same pipeline instead of
            # looking up a cog that can never resolve — that lookup failed
            # silently, so an MCP-driven test got no reply and no error.
            await self._handle_narrate(
                inter, {"text": _exploration_text(command, args)},
            )
        elif command == "roll":
            cog = self._cog("RollsCog")
            if cog:
                expr = args.get("expression", "1d20")
                await cog.roll_dice.callback(cog, inter, expr)
        elif command == "inventory":
            cog = self._cog("InventoryCog")
            if cog:
                await cog.inventory.callback(cog, inter, public=True)
        elif command == "equip":
            cog = self._cog("InventoryCog")
            if cog:
                item = args.get("item", "")
                slot = args.get("slot", "")
                await cog.equip.callback(cog, inter, item, slot)
        elif command == "unequip":
            cog = self._cog("InventoryCog")
            if cog:
                slot = args.get("slot", "")
                await cog.unequip.callback(cog, inter, slot)
        elif command == "use_item":
            cog = self._cog("InventoryCog")
            if cog:
                item = args.get("item", "")
                await cog.use_item.callback(cog, inter, item)
        elif command == "character":
            cog = self._cog("CharacterCog")
            if cog:
                await cog.character.callback(cog, inter, public=True)
        elif command == "game_state":
            await self._handle_game_state(channel)
        elif command == "inject_scene":
            await self._handle_inject_scene(inter, args)
        elif command == "narrate":
            await self._handle_narrate(inter, args)
        elif command == "click_button":
            await self._handle_click_button(args, player_idx, guild, channel)
        elif command == "select_option":
            await self._handle_select_option(args, player_idx, guild, channel)
        elif command == "submit_modal":
            await self._handle_submit_modal(args, player_idx, guild, channel)
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
        embed = build_narrative_embed(desc, tone="dramatic", footer_override=f"Campagne: {theme}")
        await inter.channel.send(embed=embed)
        await inter.channel.send(f"Campagne **{theme}** lancee dans ce canal (test mode).")

    async def _handle_lobby(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Open the REAL campaign lobby on the test channel.

        Runs the actual ``SessionCog.start_campaign`` callback with a single
        seam redirected: ``create_session_channel`` returns the test channel
        instead of creating a dedicated one (the same seam the headless
        scenario driver patches). Everything else — lobby embed, LobbyView,
        CharacterSetupFlow, background pregen, launch countdown — is
        production code, driveable via ``click_button`` / ``submit_modal`` /
        ``select_option``. This is what the C8 live smoke exercises.
        """
        from unittest.mock import patch

        cog = self._cog("SessionCog")
        if cog is None:
            await inter.channel.send("TestBridge: SessionCog introuvable.")
            return
        theme = args.get("theme", "Test Campaign")
        name = args.get("name")

        async def _use_test_channel(
            guild: Any, ch_name: Any, members: Any, me: Any, category: Any,
        ) -> Any:
            return inter.channel

        with patch("bot.cogs.session.create_session_channel", _use_test_channel):
            await cog.start_campaign.callback(cog, inter, theme, name, None)

        lobby = self.bot.lobbies.get(inter.channel_id)
        if lobby is None or lobby.lobby_message is None or lobby.lobby_view is None:
            await inter.channel.send(
                "TestBridge: lobby non ouvert (voir logs du bot).",
            )
            return
        self.active_views[lobby.lobby_message.id] = lobby.lobby_view
        await inter.channel.send(
            f"TestBridge: lobby ouvert, message={lobby.lobby_message.id}",
        )

    async def _handle_create_character(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Create a character via the test bridge.

        Always uses the quick path now: the lobby-driven setup flow is too
        elaborate for the bridge to drive component-by-component. Tests that
        need full-fidelity character setup go through ``lobby`` and drive
        the real views component by component.
        """
        session = self.bot.get_session(inter.channel_id)
        if session is None:
            await inter.channel.send("Aucune session active.")
            return

        await self._handle_create_character_quick(inter, args)

    async def _handle_create_character_quick(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Fast-path character creation (original shortcut behaviour)."""
        session = self.bot.get_session(inter.channel_id)
        assert session is not None  # checked by caller

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
        await inter.channel.send(content=f"**{name}** cree (quick) !", embed=embed)

    # NOTE: _on_character_create_complete (legacy multi-step flow callback) was
    # removed with the deletion of CharacterCreateView. Tests that need a
    # character go through ``_handle_create_character_quick`` or the lobby
    # helpers (lobby_join / lobby_set_ready).

    # ------------------------------------------------------------------
    # Component-driving handlers
    # ------------------------------------------------------------------

    async def _handle_click_button(
        self,
        args: dict[str, str],
        player_idx: int,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        """Invoke a Button callback on the view registered for the given message.

        Args:
            msg: Discord message id of the view-bearing message
            button: Label of the button (case-sensitive match)
        """
        msg_id = int(args.get("msg", "0"))
        label = args.get("button", "")
        if not msg_id or not label:
            await channel.send("TestBridge click_button: requiert msg=<id> et button=<label>")
            return

        view = self.active_views.get(msg_id)
        if view is None:
            await channel.send(f"TestBridge click_button: aucune view active pour msg={msg_id}")
            return

        button: discord.ui.Button[Any] | None = None
        for child in view.children:
            if isinstance(child, discord.ui.Button) and child.label == label:
                button = child
                break
        if button is None:
            labels = [c.label for c in view.children if isinstance(c, discord.ui.Button)]
            await channel.send(
                f"TestBridge click_button: bouton '{label}' introuvable. "
                f"Disponibles: {labels}",
            )
            return

        message = await self._safe_fetch_message(channel, msg_id)
        inter = self._make_interaction(guild, channel, player_idx, message=message)
        await button.callback(inter)  # type: ignore[arg-type]

    async def _handle_select_option(
        self,
        args: dict[str, str],
        player_idx: int,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        """Invoke a Select callback on the view registered for the given message.

        Args:
            msg: Discord message id of the view-bearing message
            value: The option value to select (may be comma-separated for multi-select)
        """
        msg_id = int(args.get("msg", "0"))
        raw_value = args.get("value", "")
        if not msg_id or raw_value == "":
            await channel.send("TestBridge select_option: requiert msg=<id> et value=<value>")
            return
        values = [v for v in raw_value.split(",") if v]

        view = self.active_views.get(msg_id)
        if view is None:
            await channel.send(f"TestBridge select_option: aucune view active pour msg={msg_id}")
            return

        select: discord.ui.Select[Any] | None = None
        for child in view.children:
            if not isinstance(child, discord.ui.Select):
                continue
            option_values = [opt.value for opt in child.options]
            if all(v in option_values for v in values):
                select = child
                break
        if select is None:
            await channel.send(
                f"TestBridge select_option: aucun select ne propose les valeurs {values}",
            )
            return

        # Populate .values on the select so the callback sees the selection
        # exactly as discord.py would after a user picked options. The
        # attribute is typed for every select flavour (user/role/channel
        # pickers included); a string select only ever holds str values.
        select._values = cast("list[Any]", values)

        message = await self._safe_fetch_message(channel, msg_id)
        inter = self._make_interaction(guild, channel, player_idx, message=message)
        await select.callback(inter)  # type: ignore[arg-type]

    async def _handle_submit_modal(
        self,
        args: dict[str, str],
        player_idx: int,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        """Submit the modal currently pending for this player.

        Args:
            field_<label>=<value>  for each TextInput field in the modal
            (labels are matched case-sensitively, spaces encoded as ~)
        """
        modal = self.pending_modals.pop(player_idx, None)
        if modal is None:
            await channel.send(
                f"TestBridge submit_modal: aucun modal pending pour player={player_idx}",
            )
            return

        field_values: dict[str, str] = {
            key[len("field_"):].replace("~", " "): value.replace("~", " ")
            for key, value in args.items()
            if key.startswith("field_")
        }

        # Fill each TextInput in the modal whose label matches a provided field.
        for child in modal.children:
            if isinstance(child, discord.ui.TextInput):
                label = child.label
                if label in field_values:
                    child._value = field_values[label]

        inter = self._make_interaction(guild, channel, player_idx)
        await modal.on_submit(inter)  # type: ignore[arg-type]

    @staticmethod
    async def _safe_fetch_message(
        channel: discord.TextChannel, msg_id: int,
    ) -> discord.Message | None:
        """Fetch a message by id, returning None on any failure (test-friendly)."""
        try:
            return await channel.fetch_message(msg_id)
        except (discord.NotFound, discord.Forbidden, AttributeError, Exception) as exc:
            logger.debug("TESTBRIDGE fetch_message(%s) failed: %s", msg_id, exc)
            return None

    async def _handle_inject_scene(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Plant a Location with optional item descriptions on the active session.

        Used by live tests that need a populated scene without going through
        the LLM world generator. Args:
            name=...                  Location name
            description=...           Location description
            items=name1|name2         Pipe-separated item names
            desc_<itemname>=...       Canon description for that item
            npcs=name1|name2          Pipe-separated NPC names
        """
        from world.location import Location

        session = self.bot.get_session(inter.channel_id)
        if session is None:
            await inter.channel.send("Aucune session active.")
            return

        # Values come from `!test` parsing which is shlex-split on whitespace,
        # so callers MUST use `~` as a literal space marker in any value.
        def _unescape(value: str) -> str:
            return value.replace("~", " ")

        name = _unescape(args.get("name", "Lieu de test"))
        description = _unescape(args.get("description", "Un lieu de test."))
        items_raw = _unescape(args.get("items", ""))
        npcs_raw = _unescape(args.get("npcs", ""))
        items = [i for i in items_raw.split("|") if i] if items_raw else []
        npcs = [n for n in npcs_raw.split("|") if n] if npcs_raw else []
        descriptions = {
            _unescape(key[len("desc_"):]): _unescape(value)
            for key, value in args.items()
            if key.startswith("desc_") and _unescape(key[len("desc_"):]) in items
        }

        location = Location(
            name=name,
            description=description,
            items_available=items,
            item_descriptions=descriptions,
            npcs_present=npcs,
        )
        session.current_location = location

        # Hydrate NPCs so the entity resolver can find them.
        if npcs:
            from bot.scene_hydration import hydrate_scene
            hydrate_scene(session, db_factory=self.bot.db_factory)

        logger.info(
            "TESTBRIDGE inject_scene channel=%s name=%r items=%d descriptions=%d",
            inter.channel_id, name, len(items), len(descriptions),
        )
        await inter.channel.send(
            f"Scene injected: {name} (items={len(items)}, "
            f"descriptions={len(descriptions)}, npcs={len(npcs)})",
        )

    async def _handle_narrate(
        self, inter: ChannelTestInteraction, args: dict[str, str],
    ) -> None:
        """Run free-text through the full action pipeline as the virtual player.

        Equivalent to a player @-mentioning the bot in a real channel, but
        bypasses the on_message author/mention checks since the tester bot
        cannot impersonate a real player. Args:
            text=...                  Free-text action to send
        """
        text = args.get("text", "").replace("~", " ")
        if not text:
            await inter.channel.send("TestBridge narrate: missing text=")
            return
        session = self.bot.get_session(inter.channel_id)
        if session is None:
            await inter.channel.send("Aucune session active.")
            return
        actor_id = inter.user.id
        if actor_id not in session.characters:
            await inter.channel.send(
                f"TestBridge narrate: virtual player {actor_id} has no character.",
            )
            return

        cog = self._cog("ActionHandlerCog")
        if cog is None:
            await inter.channel.send("ActionHandlerCog not loaded.")
            return

        if session.action_lock.locked():
            await inter.channel.send("⏳ action en cours, réessaie.")
            return

        # Build a fake message-like object the cog can consume.
        fake_message = _FakeNarrateMessage(
            channel=inter.channel,
            author=inter.user,
        )

        # _run_pipeline owns action_lock itself (and releases it before the
        # combat turn handoff) — holding it here would deadlock the task.
        await cog._run_pipeline(fake_message, session, text)

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
