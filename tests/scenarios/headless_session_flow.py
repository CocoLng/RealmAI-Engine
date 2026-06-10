"""Headless driver for :class:`bot.cogs.session.SessionCog.start_campaign`.

Plugs together every Discord-facing seam of the lobby → launch flow so
scenario tests can exercise the *real* cog code without spawning a
Discord client or talking to Ollama. This is the Lead 5 deliverable of
the *Simulator hardening* chantier (``tasks/todo.md``); it consumes
Lead 4's :class:`HeadlessCharacterSetupFlow`.

Patches installed by :meth:`HeadlessSessionFlow.start`:

* ``bot.cogs.session.create_session_channel`` → returns the scenario
  runner's mock ``TextChannel`` so the cog never tries to talk to
  Discord.
* ``bot.cogs.session.SessionCog._pregenerate_campaign_world`` → fills
  ``lobby.story_arc`` / ``lobby.current_location`` from the values the
  test passed in, then advances the phase to ``READY``. No LLM call.
* ``bot.cogs.session.asyncio.sleep`` → no-op so the launch countdown
  doesn't waste 4.5 wall-seconds per test.
* ``bot.story_bible_logger.StoryBibleLogger.write_header`` → no-op so
  the launch doesn't leave a Markdown audit file under ``logs/``.

Discord-message side-effects (channel.send / msg.edit / channel.purge)
land in the scenario runner's existing :class:`ChannelCapture` —
``opening crawl`` and ``scene`` embeds end up in
``scenario.channel_capture.messages`` and tests assert against them.

Usage::

    driver = HeadlessSessionFlow(scenario_runner=scenario)
    async with driver:
        await driver.start(
            host_idx=0,
            theme="Test campaign",
            pregen_arc=my_arc,
            pregen_location=my_location,
        )
        await driver.add_player(player_idx=0, name="Aria", ...)
        session = await driver.launch()
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from bot.cogs.session import SessionCog
from bot.lobby_state import GenerationPhase
from bot.views.character_setup_flow import CharacterSetupFlow, IdentityModal
from bot.views.lobby_view import LobbyView
from engine.character import (
    Character,
    CharacterClass,
    Race,
    Skill,
)
from tests.scenarios.headless_character_flow import HeadlessCharacterSetupFlow
from tests.scenarios.scenario_runner import EmbedCapture

if TYPE_CHECKING:
    from bot.game_session import GameSession
    from tests.scenarios.scenario_runner import ScenarioRunner
    from world.location import Location
    from world.story_arc import StoryArc


class HeadlessSessionFlow:
    """Drive ``SessionCog.start_campaign`` end-to-end without Discord.

    The driver is an async context manager — patches install on entry
    (well, on ``start``) and are torn down on exit. Use ``async with``
    to keep tests leak-free.
    """

    def __init__(self, *, scenario_runner: "ScenarioRunner") -> None:
        self.scenario = scenario_runner
        self.session_cog = SessionCog(scenario_runner.bot)
        self.lobby_view: LobbyView | None = None
        self.host_id: int = 0
        self._exit_stack: AsyncExitStack = AsyncExitStack()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "HeadlessSessionFlow":
        await self._exit_stack.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._exit_stack.__aexit__(*exc)

    # ------------------------------------------------------------------
    # Patches
    # ------------------------------------------------------------------

    def _install_patches(
        self,
        *,
        pregen_arc: "StoryArc | None",
        pregen_location: "Location | None",
    ) -> None:
        """Install all patches needed for headless cog execution."""
        # 1. create_session_channel returns our mock channel.
        cs_patch = patch(
            "bot.cogs.session.create_session_channel",
            new_callable=AsyncMock,
            return_value=self.scenario.channel,
        )
        self._exit_stack.enter_context(cs_patch)

        # 2. _pregenerate_campaign_world seeds arc/location synchronously
        #    so /start_campaign returns instantly and the launch path
        #    doesn't need a live Ollama.
        async def fake_pregen(
            self_cog: SessionCog,
            lobby: Any,
            campaign: Any,
            language: str,
        ) -> None:
            if pregen_arc is not None:
                lobby.story_arc = pregen_arc.model_copy(
                    update={"campaign_id": campaign.id},
                )
            if pregen_location is not None:
                lobby.current_location = pregen_location
            lobby.pregen_phase = GenerationPhase.READY

        pg_patch = patch.object(
            SessionCog, "_pregenerate_campaign_world", new=fake_pregen,
        )
        self._exit_stack.enter_context(pg_patch)

        # 3. asyncio.sleep — the launch countdown burns ~4.5 s otherwise.
        sleep_patch = patch(
            "bot.cogs.session.asyncio.sleep",
            new_callable=AsyncMock,
        )
        self._exit_stack.enter_context(sleep_patch)

        # 3b. Lobby TTL watcher — its 2h sleep uses the same (now no-op)
        #     asyncio.sleep, which would expire the lobby instantly in
        #     headless runs. Wall-clock expiry is irrelevant here.
        ttl_patch = patch.object(
            SessionCog, "_expire_lobby_after", new_callable=AsyncMock,
        )
        self._exit_stack.enter_context(ttl_patch)

        # 4. StoryBibleLogger.write_header — no Markdown artifacts.
        sb_patch = patch(
            "bot.story_bible_logger.StoryBibleLogger.write_header",
        )
        self._exit_stack.enter_context(sb_patch)

    # ------------------------------------------------------------------
    # Channel surface upgrades
    # ------------------------------------------------------------------

    def _wire_channel(self) -> None:
        """Augment the scenario channel with the async surface the cog needs.

        ScenarioRunner.channel is a plain ``MagicMock`` whose default
        attributes are non-awaitable mocks. The cog awaits
        ``channel.purge``, ``msg.edit``, ``msg.delete``, etc., so we
        replace each with an ``AsyncMock``. The send-capture wrapper is
        also re-installed so the messages it returns expose async
        ``edit``/``delete``.
        """
        chan = self.scenario.channel
        chan.guild = self.scenario.guild
        chan.purge = AsyncMock(return_value=[])
        chan.edit = AsyncMock()
        chan.delete = AsyncMock()
        chan.mention = f"<#{chan.id}>"

        # Replace channel.send with a smarter wrapper: the messages it
        # returns have async edit/delete so refresh_lobby_message and
        # the countdown work without exploding.
        capture = self.scenario.channel_capture

        async def send(
            content: str | None = None,
            *,
            embed: discord.Embed | None = None,
            view: discord.ui.View | None = None,
            **kwargs: Any,
        ) -> MagicMock:
            cap = EmbedCapture(content=content, embed=embed, view=view)
            capture.messages.append(cap)
            msg = MagicMock()
            msg.id = len(capture.messages)
            msg.edit = AsyncMock()
            msg.delete = AsyncMock()
            msg.pin = AsyncMock()
            return msg

        chan.send = send

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        *,
        host_idx: int = 0,
        theme: str = "Test",
        pregen_arc: "StoryArc | None" = None,
        pregen_location: "Location | None" = None,
    ) -> None:
        """Invoke ``SessionCog.start_campaign.callback`` to open the lobby.

        After this returns, the lobby is posted and ``bot.lobbies`` is
        populated. Use :meth:`add_player` to drive each player through
        character creation, then :meth:`launch` to start the campaign.
        """
        host = self.scenario._make_player(host_idx)
        self.host_id = host.id

        self._install_patches(
            pregen_arc=pregen_arc, pregen_location=pregen_location,
        )
        self._wire_channel()

        inter = self.scenario._make_interaction(host_idx)
        inter.followup.send = AsyncMock()  # type: ignore[method-assign]

        # mypy sees ``callback(interaction, theme, name, players)`` after the
        # @app_commands decorator unwraps the cog ``self``. We pass both so
        # the decorator's bound-method call works at runtime — matches the
        # pattern used throughout ScenarioRunner.
        # The @app_commands.command decorator hides the cog ``self`` from
        # mypy's view of ``callback``, so calling the unwrapped bound method
        # trips arg-type / call-arg / misc all at once. Same pattern as
        # ScenarioRunner.save/resume — see tests/scenarios/scenario_runner.py.
        # One-liner so the type-ignore covers every argument.
        await self.session_cog.start_campaign.callback(self.session_cog, inter, theme, None, None)  # type: ignore[call-arg,arg-type,misc]

        # Retrieve the LobbyView by walking the captured messages — the
        # cog posts exactly one with a LobbyView attached.
        for m in self.scenario.channel_capture.messages:
            if isinstance(m.view, LobbyView):
                self.lobby_view = m.view
                break
        if self.lobby_view is None:
            msg = "start_campaign did not post a LobbyView"
            raise RuntimeError(msg)

        # Drain the pregen task so subsequent operations see lobby ready.
        lobby = self.scenario.bot.lobbies[self.scenario.channel.id]
        if lobby.pregen_task is not None:
            await lobby.pregen_task

    async def add_player(
        self,
        *,
        player_idx: int,
        name: str,
        race: Race,
        char_class: CharacterClass,
        skills: list[Skill],
        kit_name: str,
        motivation_key: str,
        concept: str = "",
        stats_method: str = "preset",
    ) -> Character:
        """Drive one player through the lobby Rejoindre + setup flow.

        Internally this clicks the Rejoindre button, intercepts the
        modal the cog sends, then drives the same modal + flow through
        :class:`HeadlessCharacterSetupFlow`.
        """
        if self.lobby_view is None:
            msg = "start() must be awaited first"
            raise RuntimeError(msg)

        self.scenario._make_player(player_idx)
        inter = self.scenario._make_interaction(player_idx)
        inter.response.is_done = lambda: False  # type: ignore[method-assign]

        # The on_join closure awaits ``interaction.response.send_modal(modal)``.
        # Capture the modal so we can drive its parent flow.
        captured: list[IdentityModal] = []

        async def fake_send_modal(modal: IdentityModal) -> None:
            captured.append(modal)

        inter.response.send_modal = fake_send_modal  # type: ignore[attr-defined]

        await self.lobby_view._on_join(inter, self.lobby_view)  # type: ignore[arg-type]

        if not captured:
            msg = "on_join did not send a modal"
            raise RuntimeError(msg)
        modal = captured[0]
        flow: CharacterSetupFlow = modal.parent_view

        # Wrap the existing flow so on_setup_complete (DB persistence +
        # lobby roster update) keeps firing through the cog's closure.
        driver = HeadlessCharacterSetupFlow.from_flow(flow)
        character = await driver.run_full_flow(
            name=name,
            concept=concept,
            race=race,
            char_class=char_class,
            skills=skills,
            kit_name=kit_name,
            motivation_key=motivation_key,
            stats_method=stats_method,  # type: ignore[arg-type]
        )
        return character

    async def launch(self, *, host_idx: int = 0) -> "GameSession":
        """Host clicks Démarrer — drives ``_launch_campaign_from_lobby``.

        Returns the resulting :class:`GameSession`, also accessible via
        ``scenario.bot.sessions[scenario.channel.id]``.
        """
        if self.lobby_view is None:
            msg = "start() must be awaited first"
            raise RuntimeError(msg)

        inter = self.scenario._make_interaction(host_idx)
        inter.response.is_done = lambda: False  # type: ignore[method-assign]

        await self.lobby_view._on_launch(inter, self.lobby_view)  # type: ignore[arg-type]

        session = self.scenario.bot.sessions.get(self.scenario.channel.id)
        if session is None:
            msg = "Launch did not produce a GameSession"
            raise RuntimeError(msg)
        return session
