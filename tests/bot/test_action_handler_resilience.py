"""Tests for bot/cogs/action_handler.py network resilience.

A transient DNS or connection failure from aiohttp at the moment the bot
tries to send the initial progress embed must NOT crash on_message. The
handler must log the failure, release the session lock, and leave no
half-written state behind.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from ai.models import InterpretedAction
from bot.action_pipeline import ActionPipelineResult
from bot.cogs.action_handler import ActionHandlerCog
from engine.character import (
    AbilityScores,
    CharacterClass,
    Race,
    create_character,
)
from engine.validators import ActionType
from world.campaign import Campaign
from world.location import Location


# ---------------------------------------------------------------------------
# Minimal fakes (mirror test_action_handler_cog.py)
# ---------------------------------------------------------------------------


@dataclass
class FakeMessage:
    content: str
    author: Any
    channel: Any
    mentions: list[Any] = field(default_factory=list)
    reply: AsyncMock = field(default_factory=AsyncMock)


@dataclass
class FakeAuthor:
    id: int
    bot: bool = False
    display_name: str = "Player"


@dataclass
class FakeChannel:
    id: int
    send: AsyncMock = field(default_factory=AsyncMock)


def _make_session(player_id: int = 1) -> Any:
    scores = AbilityScores(STR=12, DEX=10, CON=10, INT=10, WIS=10, CHA=10)
    char = create_character("Aldric", Race.HUMAN, CharacterClass.FIGHTER, scores)
    location = Location(
        name="Place", description="d", connections=[],
        npcs_present=[], items_available=[],
    )
    session = MagicMock()
    session.campaign = Campaign(id="camp-1", name="Test", player_names=[str(player_id)])
    session.characters = {player_id: char}
    session.npcs = {}
    session.current_location = location
    session.combat_state = None
    session.inventories = {}
    session.language = "fr"
    session.interpreter = MagicMock()
    session.narrator = MagicMock()
    session.action_lock = asyncio.Lock()
    return session


def _make_bot(sessions: dict[int, Any] | None = None, bot_user_id: int = 9999) -> Any:
    bot = MagicMock()
    bot.user = MagicMock()
    bot.user.id = bot_user_id
    bot.sessions = sessions or {}
    return bot


class _NeverCalledFactory:
    """Pipeline factory that records any attempt and fails the test."""

    def __init__(self) -> None:
        self.constructed = 0

    def __call__(self, **kwargs: Any) -> Any:
        self.constructed += 1
        raise AssertionError(
            "pipeline should NOT be constructed when the initial send fails",
        )


def _make_dns_error() -> aiohttp.ClientConnectionError:
    """Construct a ClientConnectionError that mirrors the live traceback.

    The real crash was ``ClientConnectorDNSError`` but we use the common base
    ``ClientConnectionError`` — our handler catches the base, so the same fix
    applies to DNS errors, resets, server-disconnect, etc.
    """
    return aiohttp.ClientConnectionError("Cannot connect to host discord.com:443")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInitialSendResilience:
    """When the initial progress embed send fails with a network error,
    on_message must return gracefully — no exception propagation, no pipeline
    run, no lock held, a single warning logged."""

    @pytest.mark.asyncio
    async def test_aiohttp_connection_error_is_swallowed(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = ActionHandlerCog(bot)
        factory = _NeverCalledFactory()
        cog._pipeline_factory = factory  # type: ignore[assignment]

        channel = FakeChannel(id=1)
        channel.send.side_effect = _make_dns_error()
        msg = FakeMessage(
            content="<@9999> je regarde autour",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        with caplog.at_level(logging.WARNING, logger="bot.cogs.action_handler"):
            # Must NOT raise — transient network failure is a no-op.
            await cog.on_message(msg)  # type: ignore[arg-type]

        # Pipeline must not have been built or run.
        assert factory.constructed == 0
        # Lock must be released so the next action can proceed.
        assert not session.action_lock.locked()
        # We logged a warning explaining the drop.
        assert any(
            "progress send failed" in rec.message.lower()
            or "dropping" in rec.message.lower()
            for rec in caplog.records
        ), f"expected a 'progress send failed' warning, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_discord_http_exception_is_swallowed(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = ActionHandlerCog(bot)
        cog._pipeline_factory = _NeverCalledFactory()  # type: ignore[assignment]

        channel = FakeChannel(id=1)
        # Simulate a Discord 500 / 503 — same resilience path as aiohttp.
        response = MagicMock()
        response.status = 503
        response.reason = "Service Unavailable"
        channel.send.side_effect = discord.HTTPException(response, "busy")
        msg = FakeMessage(
            content="<@9999> je fouille",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        # Must NOT raise.
        await cog.on_message(msg)  # type: ignore[arg-type]
        assert not session.action_lock.locked()


class TestNoPartialDbState:
    """When the initial send fails we drop the action cleanly — the pipeline
    never runs, so any DB write inside the pipeline never happens. This test
    is the behavioural counterpart to the resilience fix: the pipeline factory
    is wired in a way that WOULD be observable if called, and we assert it
    stayed untouched."""

    @pytest.mark.asyncio
    async def test_pipeline_never_constructed_on_send_failure(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = ActionHandlerCog(bot)

        # If the factory were called the test would fail because the
        # MagicMock's .kwargs doesn't match anything downstream, but more
        # importantly — we just want to assert that the factory was not
        # invoked. Replace with a raising fake.
        factory = _NeverCalledFactory()
        cog._pipeline_factory = factory  # type: ignore[assignment]

        channel = FakeChannel(id=1)
        channel.send.side_effect = _make_dns_error()
        msg = FakeMessage(
            content="<@9999> je m'approche de l'autel",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        assert factory.constructed == 0


class TestHappyPathRegression:
    """Sanity check that a successful send still flows through — the
    resilience wrapper must not swallow normal returns."""

    @pytest.mark.asyncio
    async def test_successful_send_still_runs_pipeline(self) -> None:
        session = _make_session(player_id=1)
        bot = _make_bot(sessions={1: session})
        cog = ActionHandlerCog(bot)

        pipeline_run = False

        def factory(**kwargs: Any) -> Any:
            nonlocal pipeline_run
            pipeline = MagicMock()
            pipeline._pending_combat_start_embed = None

            async def process(player_text: str, progress_callback: Any = None) -> Any:
                nonlocal pipeline_run
                pipeline_run = True
                return ActionPipelineResult(
                    narrative="x",
                    tone="dramatic",
                    mechanics_text="x",
                    interpreted_action=InterpretedAction(
                        action_type=ActionType.LOOK,
                        actor_name="Aldric",
                        raw_input="x",
                    ),
                )

            pipeline.process = process
            return pipeline

        cog._pipeline_factory = factory  # type: ignore[assignment]

        channel = FakeChannel(id=1)  # send succeeds (AsyncMock default)
        progress_msg = MagicMock()
        progress_msg.edit = AsyncMock()
        channel.send.return_value = progress_msg

        msg = FakeMessage(
            content="<@9999> je regarde autour de moi attentivement",
            author=FakeAuthor(id=1),
            channel=channel,
            mentions=[bot.user],
        )

        await cog.on_message(msg)  # type: ignore[arg-type]

        assert pipeline_run, "pipeline must run when the initial send succeeds"
        assert not session.action_lock.locked()
